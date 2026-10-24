"""
Reads BioShock Remastered map files (.bsm) and lists what the randomizer could use beyond map_stock.py's Gardens:

- transitions: every scripted level change (ActionChangeLevel): the map it loads and the PlayerStart it puts the
  player at, with the script that runs it;
- starts: the PlayerStarts a level change can arrive at (their Label is what StartLocationLabel names);
- bathysphere: the BathysphereSwitch actors (by Label) and the bathysphere destinations the map's scripts unlock;
- quests: each quest the map's scripts start or complete, with what starts the script;
- diaries: the audio diaries lying in the map (LogPickup actors), by item class;
- given: what the map's scripts hand the player (ActionGiveItemsToPlayer): radio messages, story items and so on;
- world: plasmid and tonic pickups, weapon pickups and safes, one by one with their Label and position: what
  world-item and safe checks (design section 5) would be made of;
- pickups: every other actor with loot (LootSlot -> LootItemSpecification), by item class, with how many of each;
- enemies: which research subjects the map puts in it (an actor of the subject's class, or a spawner or spawn action
  naming one), by the game's ResearchName, with how many actors, spawners and spawn actions name each. This is what
  data.RESEARCH_SUBJECTS's levels should follow (a subject is researched where it is surely met). Matched by class
  and object names, so an enemy only ever made by something the names do not show is missed [inferred].

Only reads; Python 3.8+, nothing to install. Needs map_stock.py next to it.

    python map_survey.py "E:\\SteamLibrary\\steamapps\\common\\BioShock Remastered" > map_survey.json

Read from 2-Fisheries.bsm on 2026-10-14: its one ActionChangeLevel loads 2-SubBay at FromFisheriesPS, its PlayerStarts
are PlayerStart and FromSubBayPS, its BathysphereSwitch is labelled FromMedicalSwitch, and it unlocks 2-Fisheries and
1-Medical as bathysphere destinations (the destination is a name in the action's resolveInfoList, given when it runs).
"""
import json
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from map_stock import SIZES, Package, compact  # noqa: E402

# The research subjects (ShockPlayer's ResearchName) and how their classes and spawners are named, as ShockAI's
# class list has them (reference/bioshock_runtime_classes.txt): Aggressor, AggressorSpawner, Assassin, Bouncer, ...
ENEMY_NAMES = {
    "MeleeThug": ("MeleeThug", "Thuggish"),
    "RangedAggressor": ("RangedAggressor", "Aggressor", "Leadhead"),
    "Grenadier": ("Grenadier", "Nitro"),
    "CeilingCrawler": ("CeilingCrawler", "Crawler", "Spider"),
    "Assassin": ("Assassin", "Houdini"),
    "Bouncer": ("Bouncer",),
    "Rosie": ("Rosie",),
    "SecurityBot": ("SecurityBot",),
    "Gatherer": ("SpawnedGatherer", "Gatherer"),
    "Turret": ("Turret",),
    "SecurityCamera": ("SecurityCamera",),
}
# Names that look like an enemy's but are not one: its parts, effects and sounds.
NOT_ENEMIES = ("Weapon", "Ammo", "Projectile", "Attack", "Action", "FX", "Sound", "Hit", "Decal", "Backpack",
               "Cone", "Gun", "Explo", "Spotlight", "Reward", "Booty", "Panic", "Vent", "Label")
QUEST_ACTIONS = {"ActionInitiateQuest": "start", "ActionCompleteQuest": "complete",
                 "ActionCompleteQuestObjective": "complete objective"}
SKIP_MAPS = ("_deu", "_esn", "_fra", "_ita", "_jpn", "_pol", "_rus", "_cze", "_hun")  # localized copies, empty


class Ref(int):
    """An object property's value: the package's object index (exports from 1, imports from -1)."""


class Survey(Package):
    def __init__(self, data):
        super().__init__(data)
        self.children = {}
        for i, e in enumerate(self.exports, 1):
            self.children.setdefault(e["outer"], []).append(i)
        self._props = {}

    def properties(self, b, p):
        """As map_stock's, but objects stay object indexes (Ref) and strings are decoded."""
        out = {}
        while p < len(b):
            n, p = compact(b, p)
            p += 4
            name = self.names[n]
            if name == "None":
                return out, p
            info = b[p]
            p += 1
            kind, size_code, flag = info & 0xf, (info >> 4) & 7, info >> 7
            if kind == 10:
                _, p = compact(b, p)
                p += 4
            if size_code in SIZES:
                size = SIZES[size_code]
            elif size_code == 5:
                size, p = b[p], p + 1
            elif size_code == 6:
                size, p = struct.unpack_from("<H", b, p)[0], p + 2
            else:
                size, p = struct.unpack_from("<i", b, p)[0], p + 4
            if flag and kind != 3:
                p += 1
            value = b[p:p + size]
            p += size
            if kind == 3:
                value = bool(flag)
            elif kind == 2 and size == 4:
                value = struct.unpack("<i", value)[0]
            elif kind == 4 and size == 4:
                value = round(struct.unpack("<f", value)[0], 4)
            elif kind == 1 and size == 1:
                value = value[0]
            elif kind in (5, 8):
                value = Ref(compact(value, 0)[0])
            elif kind == 6:
                value = self.names[compact(value, 0)[0]]
            elif kind == 13:
                value = text(value)
            out.setdefault(name, value)
        raise ValueError("properties run past the object")

    def props(self, i):
        """The properties of export i (from 1), wherever they start: the try that finds the most wins."""
        if i not in self._props:
            b = self.body(self.exports[i - 1])
            best = {}
            for start in range(0, min(len(b), 96)):
                try:
                    found, _ = self.properties(b, start)
                except (ValueError, IndexError, struct.error, UnicodeDecodeError):
                    continue
                if score(found) > score(best):
                    best = found
            self._props[i] = best
        return self._props[i]

    def resolved(self, i):
        """The values a script action is given when it runs (its resolveInfoList), by property name."""
        b = self.props(i).get("resolveInfoList")
        if not isinstance(b, bytes):
            return {}
        try:
            return {row.get("PropertyName"): row.get("Variable") for row in self.structs(b)}
        except (ValueError, IndexError, struct.error):
            return {}

    def trigger_of(self, script):
        """What starts a Script: the message class it waits for and what sends it, as far as the map says."""
        found = self.props(script) if script > 0 else {}
        return {"message": self.name_of(found.get("scriptMessageClass")),
                "by": self.name_of(found.get("TriggeredBy")) if isinstance(found.get("TriggeredBy"), Ref) else None}

    def valid(self, ref):
        return isinstance(ref, int) and -len(self.imports) <= ref <= len(self.exports)

    def name_of(self, ref):
        return self.object_name(int(ref)) if self.valid(ref) else None

    def loot(self, i):
        """(item class, smallest stack, largest stack) for each loot specification under export i's LootSlot(s)."""
        rows = []
        slots = [r for r in (self.props(i).get("LootSlot"),) if isinstance(r, Ref) and r > 0 and self.valid(r)]
        slots += [c for c in self.children.get(i, []) if self.class_of(self.exports[c - 1]) == "LootSlot"]
        for slot in dict.fromkeys(slots):
            specs = [r for r in (self.props(slot).get("LootSpec"),) if isinstance(r, Ref) and r > 0 and self.valid(r)]
            specs += [c for c in self.children.get(slot, []) + self.children.get(i, [])
                      if self.class_of(self.exports[c - 1]) == "LootItemSpecification"]
            for spec in dict.fromkeys(specs):
                found = self.props(spec)
                if "ItemClass" in found:
                    rows.append((self.name_of(found["ItemClass"]), found.get("MinStackSize"),
                                 found.get("MaxStackSize")))
        return rows


# Properties that only turn up where a parse started in the right place; a parse that finds them wins over a longer one
# made of whatever the bytes happened to spell.
KNOWN = frozenset({"parentScript", "resolveInfoList", "Level", "Tag", "Label", "ItemClass", "LootSpec", "LootSlot",
                   "MapName", "scriptMessageClass", "Actions", "CheckpointTypePadding"})


def score(found):
    return (len(KNOWN & found.keys()), len(found))


def text(value):
    n, p = compact(value, 0)
    raw = value[p:p - n] if n < 0 else value[p:p + 2 * n]
    return (raw.decode("latin1") if n < 0 else raw.decode("utf-16-le")).rstrip("\0")


# Actors that could become checks of their own (design section 5): plasmid and tonic pickups, weapons (a weapon's
# pickup class names it with its first ammo, "ShotgunAnd00BuckPickup", or alone, "PistolPickup"), the Research Camera,
# and safes. A safe set into a wall is a SecurityCrate_WallSafe (Medical Pavilion, by the Telekinesis plasmid; found
# with the probe 2026-10-15); any other SecurityCrate kind is listed too, so the survey shows it.
SAFES = ("SecurityCrate_Safe", "SecurityCrate_WallSafe", "Lockbox")
# The wrench is left out: a script hands it over (WrenchPickupItem), and section 5 keeps no ID for it.
WEAPONS = ("Pistol", "MachineGun", "Shotgun", "GrenadeLauncher", "ChemicalThrower", "Crossbow", "ResearchCamera")
PLAIN_WEAPON_PICKUPS = tuple(f"{weapon}Pickup" for weapon in WEAPONS)


def enemy_of(name):
    """The research subject a class or object name stands for, or None. Longest pattern first, so a RangedAggressor
    is not counted as a plain Aggressor twice and a SpawnedGatherer is a Gatherer."""
    if not name or any(part in name for part in NOT_ENEMIES):
        return None
    for pattern, subject in sorted(((p, s) for s, ps in ENEMY_NAMES.items() for p in ps), key=lambda x: -len(x[0])):
        if pattern.lower() in name.lower():
            return subject
    return None


def is_weapon_pickup(cls):
    return ("And" in cls and cls.endswith("Pickup")) or cls in PLAIN_WEAPON_PICKUPS


def weapon_of_pickup(cls):
    """The weapon a weapon pickup class names: "ShotgunAnd00BuckPickup" and "ShotgunPickup" are both Shotgun."""
    return cls.split("And", 1)[0] if "And" in cls else cls[:-len("Pickup")]


def is_world_item(cls):
    return cls.endswith("PlasmidPickup") or is_weapon_pickup(cls) or cls in SAFES or cls.startswith("SecurityCrate")


def position(value):
    if isinstance(value, bytes) and len(value) == 12:
        return [round(v) for v in struct.unpack("<3f", value)]
    return None


def survey(path):
    pkg = Survey(path.read_bytes())
    result = {"map": path.name, "transitions": [], "starts": [], "bathysphere": {"switches": [], "unlocks": [],
              "menus": 0}, "quests": [], "world": [], "diaries": [], "given": [], "pickups": {}, "enemies": {}}
    pickups = Counter()
    enemies: dict[str, Counter] = {}
    for i, e in enumerate(pkg.exports, 1):
        cls = pkg.class_of(e)
        note_enemy(pkg, i, e, cls, enemies)
        if cls == "ActionChangeLevel":
            found = {**pkg.props(i), **pkg.resolved(i)}
            script = e["outer"]
            result["transitions"].append({"map": found.get("MapName"), "start": found.get("StartLocationLabel"),
                                          "script": pkg.name_of(script), **pkg.trigger_of(script)})
        elif cls == "PlayerStart":
            result["starts"].append(pkg.props(i).get("Label"))
        elif cls == "BathysphereSwitch":
            result["bathysphere"]["switches"].append(pkg.props(i).get("Label"))
        elif cls == "ActionUnlockBathysphereDestination":
            result["bathysphere"]["unlocks"].append({"map": pkg.resolved(i).get("MapName"),
                                                     "script": pkg.name_of(e["outer"]), **pkg.trigger_of(e["outer"])})
        elif cls == "ActionShowBathysphereUI":
            result["bathysphere"]["menus"] += 1
        elif cls in QUEST_ACTIONS:
            quest = pkg.resolved(i).get("QuestName")
            if quest:
                result["quests"].append({"action": QUEST_ACTIONS[cls], "quest": quest, "script": pkg.name_of(e["outer"]),
                                         **pkg.trigger_of(e["outer"])})
        elif cls == "ActionGiveItemsToPlayer":
            given = pkg.name_of(pkg.props(i).get("ItemClass"))
            if given:
                result["given"].append(given)
        elif cls == "LogPickup":
            result["diaries"] += [item for item, _, _ in pkg.loot(i)]
        elif e["outer"] == 0 and cls not in ("LootSlot", "LootItemSpecification"):
            loot = pkg.loot(i)
            if is_world_item(cls):
                found = pkg.props(i)
                result["world"].append({"class": cls, "actor": e["name"], "label": found.get("Label"),
                                        "at": position(found.get("Location")),
                                        "items": sorted({item for item, _, _ in loot if item})})
            for item, low, high in loot:
                pickups[item] += 1
    result["given"].sort()
    result["diaries"].sort()
    result["pickups"] = dict(sorted(pickups.items()))
    result["enemies"] = {subject: dict(sorted(how.items())) for subject, how in sorted(enemies.items())}
    return result


def note_enemy(pkg, i, e, cls, enemies):
    """Counts export i if it is an enemy, a spawner of one, or a spawn action naming one."""
    if e["outer"] == 0 and enemy_of(cls) and "Spawn" not in cls:
        enemies.setdefault(enemy_of(cls), Counter())["actors"] += 1
    elif "Spawn" in cls and not cls.startswith("ActionSpawnActor"):
        named = enemy_of(cls[len("Action"):] if cls.startswith("Action") else cls)  # AggressorSpawner, ActionSpawnTurret
        if named is None:  # a general spawner or ActionSpawnAI: the class it makes is among its settings
            values = [*pkg.props(i).values(), *pkg.resolved(i).values()]
            named = next((enemy_of(pkg.name_of(v)) for v in values if isinstance(v, Ref) and enemy_of(pkg.name_of(v))),
                         None) or next((enemy_of(v) for v in values if isinstance(v, str) and enemy_of(v)), None)
        if named is not None:
            enemies.setdefault(named, Counter())["spawn actions" if cls.startswith("Action") else "spawners"] += 1


def main():
    paths = []
    for arg in sys.argv[1:] or ["."]:
        path = Path(arg)
        paths += sorted(path.rglob("*.bsm"), key=lambda p: p.name.lower()) if path.is_dir() else [path]
    results = []
    for path in paths:
        if path.stem.lower().endswith(SKIP_MAPS):
            continue
        try:
            results.append(survey(path))
        except Exception as error:  # one odd file should not stop the rest
            results.append({"map": path.name, "error": f"{type(error).__name__}: {error}"})
        print(f"read {path.name}", file=sys.stderr)
    json.dump(results, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()

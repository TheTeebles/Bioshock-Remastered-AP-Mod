"""
Turns map_survey.py's JSON into the candidate location tables for world-item and safe checks (design section 5), with
the IDs section 5 keeps for them:

- plasmids lying in the world (ActivePlasmidPickup): 6001-6010, in story order;
- gene tonics lying in the world (Physical and Engineering PlasmidPickup): 6011-6050, in story order;
- weapons: 6051-6057, one per weapon, at the level where it first lies in the world. The maps place many copies of
  each weapon (Neptune's Bounty has three shotguns and three machine guns; a copy you already hold gives ammo), so
  the check is "the weapon is held for the first time", which the agent already reads (weapons.owned);
- safes and lockboxes: 6101-6199, numbered per level in the order the map lists them.

Item names come from the client's tables (game_data.GIVE_CLASS and WEAPON_CLASS, read backwards); a class those
tables don't know keeps its class name and is listed under "unknown" so it can be added.

Run it from an Archipelago checkout with the BioShock world in worlds/bioshock, like survey_report.py:

    python path/to/world_items.py survey.json > world_items.json
    python path/to/world_items.py survey.json --markdown > world_items.md
"""
import json
import sys

from worlds.bioshock.client.game_data import GIVE_CLASS, MAP_LEVEL, WEAPON_CLASS, normalize_map
from worlds.bioshock.data import LEVEL_NAMES

from map_survey import is_weapon_pickup, weapon_of_pickup

SAFES = ("SecurityCrate_Safe", "Lockbox")
FIRST_ID = {"plasmid": 6001, "tonic": 6011, "weapon": 6051, "safe": 6101}
LAST_ID = {"plasmid": 6010, "tonic": 6050, "weapon": 6057, "safe": 6199}

ITEM_OF_CLASS = {cls.rsplit(".", 1)[-1]: name for name, cls in GIVE_CLASS.items()}
WEAPON_OF_CLASS = {cls.rsplit(".", 1)[-1]: name for name, cls in WEAPON_CLASS.items()}


def level_of(map_file):
    name = normalize_map(map_file.rsplit(".", 1)[0])
    return MAP_LEVEL.get(name, name)


def story_order(entry):
    level = level_of(entry["map"])
    return (LEVEL_NAMES.index(level) if level in LEVEL_NAMES else len(LEVEL_NAMES), entry["map"].lower())


def kind_of(cls):
    if cls in SAFES:
        return "safe"
    if cls == "ActivePlasmidPickup":
        return "plasmid"
    if cls.endswith("PlasmidPickup"):
        return "tonic"
    if is_weapon_pickup(cls):
        return "weapon"
    return None


def candidates(maps):
    """The candidate locations, in ID order, and the classes no table could name."""
    rows, unknown = [], set()
    next_id = dict(FIRST_ID)
    weapons_seen = set()
    safes_per_level = {}
    for entry in sorted(maps, key=story_order):
        level = level_of(entry["map"])
        if level not in LEVEL_NAMES:
            continue  # not one of Rapture's levels (Autoplay.bsm, the Lighthouse)
        for spot in entry["world"]:
            kind = kind_of(spot["class"])
            if kind is None:
                continue
            if kind == "weapon":
                cls = weapon_of_pickup(spot["class"])
                if cls in weapons_seen:
                    continue
                weapons_seen.add(cls)
                vanilla = WEAPON_OF_CLASS.get(cls)
                if vanilla is None:
                    unknown.add(cls)
                    vanilla = cls
                name = f"{level} - {vanilla}"
            elif kind == "safe":
                safes_per_level[level] = safes_per_level.get(level, 0) + 1
                vanilla = None
                name = f"{level} Safe {safes_per_level[level]}"
            else:
                held = [item for item in spot["items"] if item]
                if len(held) != 1:
                    unknown.add(f"{spot['actor']} holds {held}")
                cls = held[0] if held else spot["class"]
                vanilla = ITEM_OF_CLASS.get(cls)
                if vanilla is None:
                    unknown.add(cls)
                    vanilla = cls
                name = f"{level} - {vanilla}"
            rows.append({"id": next_id[kind], "name": name, "kind": kind, "level": level, "map": entry["map"],
                         "actor": spot["actor"], "label": spot["label"], "at": spot["at"], "vanilla": vanilla,
                         "contents": spot["items"] if kind == "safe" else None})
            next_id[kind] += 1
    over = [kind for kind in FIRST_ID if next_id[kind] - 1 > LAST_ID[kind]]
    if over:
        raise ValueError(f"more {', '.join(over)} spots than section 5 keeps IDs for")
    rows.sort(key=lambda row: row["id"])
    names = [row["name"] for row in rows]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    return rows, sorted(unknown), duplicates


def markdown(rows, unknown, duplicates):
    out = ["# World-item and safe check candidates\n", "| ID | Location | Map | Actor (Label) | Vanilla / contents |",
           "|---|---|---|---|---|"]
    for row in rows:
        what = row["vanilla"] or ", ".join(row["contents"]) or "empty"
        out.append(f"| {row['id']} | {row['name']} | {row['map']} | {row['actor']} ({row['label']}) | {what} |")
    counts = {}
    for row in rows:
        counts[row["kind"]] = counts.get(row["kind"], 0) + 1
    out.append("\nTotals: " + ", ".join(f"{kind} {n}" for kind, n in counts.items()))
    if duplicates:
        out.append("\nNames used twice (the same item lies twice in one level): " + ", ".join(duplicates))
    if unknown:
        out.append("\nClasses the client's tables don't name: " + ", ".join(unknown))
    return "\n".join(out)


def main():
    maps = [entry for entry in json.load(open(sys.argv[1], encoding="utf-8")) if "error" not in entry]
    rows, unknown, duplicates = candidates(maps)
    if "--markdown" in sys.argv[2:]:
        print(markdown(rows, unknown, duplicates))
    else:
        json.dump({"locations": rows, "unknown": unknown, "duplicate_names": duplicates}, sys.stdout, indent=1)
        print()


if __name__ == "__main__":
    main()

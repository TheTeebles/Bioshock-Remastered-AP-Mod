"""
What the client knows about turning Archipelago items into game commands, and game events into location IDs.

Nothing in here talks to Archipelago or to the game, so all of it can be unit-tested.

Where the names of the game's classes and commands come from:

  * The running game. On 2026-10-07 it was asked for its classes (the console command `obj list`, on the Steam
    build). test/runtime_classes.txt is the part of what it printed that holds items: every class of
    ShockDesignerClasses and the bigger two thirds of ShockGame. A test holds every class named below against it.
  * The class list of the game's script packages, exported with UE Explorer from the Steam build (654 classes in
    ShockGame): test/shockgame_classes.txt. It says what the script package on disk declares, which is not quite
    what the running game has: six second and third levels that the export lists under ShockGame are, in the
    running game, in ShockDesignerClasses like every other higher level.
  * Console command guides for BioShock Remastered. They say what each class is called in the game, that the
    second and third level of a plasmid or tonic is a class of the same name with "Two" or "Three" added, in
    ShockDesignerClasses, and that weapons and their upgrades have commands of their own.

That a class exists does not mean giving it does what is hoped; the design document keeps the list of what has
been tried in the game and how it went. A class the game does not have is no longer silent, though: while the
command runs the game raises "Failed to find object 'Class ...'", the agent passes that on, and the client reports
the item as not delivered (frida_agent.missing_class).
"""
from __future__ import annotations

from dataclasses import dataclass

from ..data import (
    ARCADIA, FARMERS_MARKET, FORT_FROLIC, ITEM_TABLE, LEVEL_COMPLETION_LEVELS, LEVEL_NAMES, LOCATION_TABLE, PLASMIDS,
    PROVING_GROUNDS, STORY_MILESTONES, TONICS, ItemKind, LocationCategory, Source, level_completion_location_name,
    story_location_name,
)

# --------------------------------------------------------------------------------------------------------------------
# Items -> console commands
# --------------------------------------------------------------------------------------------------------------------

SCRIPT_PACKAGE = "ShockGame"  # the game's own script classes
DESIGNER_PACKAGE = "ShockDesignerClasses"  # classes made in the editor: higher levels, and what the shops sell

ADAM_CLASS = "ShockGame.ADAM"
DOLLARS_CLASS = "ShockGame.Credits"

# In-game name of a plasmid -> the class of its first level.
PLASMID_CLASS: dict[str, str] = {
    "Electro Bolt": "ElectricBolt",
    "Incinerate!": "Incineration",
    "Telekinesis": "Telekinesis",
    "Winter Blast": "IcicleAssault",
    "Insect Swarm": "InsectSwarmPlasmid",
    "Cyclone Trap": "SpringboardTrap",
    "Enrage!": "BerserkRage",
    "Security Bullseye": "SecurityBeacon",
    "Target Dummy": "DecoyHuman",
    "Sonic Boom": "AirBlast",
    "Hypnotize Big Daddy": "SummonProtector",
}

# In-game name of a gene tonic -> the class of its first level.
TONIC_CLASS: dict[str, str] = {
    # Combat
    "Armored Shell": "ArmoredBody",
    "Damage Research": "DeepResearcher",
    "Electric Flesh": "ElectricBody",
    "Frozen Field": "FreezingNimbus",
    "Human Inferno": "SuperHeated",
    "Machine Buster": "MachineBully",
    "Photographer's Eye": "EyeForDetail",
    "Static Discharge": "ChargedBursts",
    "Wrench Jockey": "MeleeMaster",
    "Wrench Lurker": "SneakAttack",
    # Engineering
    "Alarm Expert": "AlarmExpert",
    "Clever Inventor": "EfficientCrafter",
    "Focused Hacker": "ElectronicsExpert",
    "Hacking Expert": "HackingExpert",
    "Prolific Inventor": "ProlificCrafter",
    "Safecracker": "StationExpert",
    "Security Expert": "SecuritySystemsExpert",
    "Shorten Alarms": "ShorterAlarm",
    "Speedy Hacker": "SlowFlow",
    "Vending Expert": "VendingExpert",
    # Physical
    "Bloodlust": "BloodLust",
    "Booze Hound": "BoozeHound",
    "EVE Link": "MedHypoOmnisynthesis",
    "EVE Saver": "BioAmmoEfficiency",
    "Extra Nutrition": "HealthyConsumer",
    "Hacker's Delight": "GeneticHacker",
    "Medical Expert": "MedicineFriendly",
    "Natural Camouflage": "ChameleonBlood",
    "Scrounger": "ThoroughScavenger",
    "Security Evasion": "NearSightedCameras",
    "SportBoost": "FastTwitch",
}

_LEVEL_WORD = {2: "Two", 3: "Three"}


def _split_level(name: str) -> tuple[str, int]:
    """("Armored Shell", 2) for "Armored Shell 2"; level 1 for a name without a number."""
    family, _, number = name.rpartition(" ")
    if family and number.isdigit() and int(number) in _LEVEL_WORD:
        return family, int(number)
    return name, 1


def _leveled_classes(families: dict[str, str], names: list[str]) -> dict[str, str]:
    """The class to give for each in-game name: the family's own class, or its "Two"/"Three" class."""
    classes: dict[str, str] = {}
    for name in names:
        family, level = _split_level(name)
        if family in families:
            class_name = families[family] + _LEVEL_WORD.get(level, "")
            # Every higher level is in the designer package. The script export also lists six of them under
            # ShockGame (what the Research Camera rewards), but the running game has no such classes there:
            # `GiveItem 1 ShockGame.FastTwitchTwo` gave nothing and raised "Failed to find object" (2026-10-07).
            package = SCRIPT_PACKAGE if level == 1 else DESIGNER_PACKAGE
            classes[name] = f"{package}.{class_name}"
    return classes


# In-game item name -> class for `GiveItem <count> <class>`.
GIVE_CLASS: dict[str, str] = {
    **_leveled_classes(PLASMID_CLASS, [name for plasmid in PLASMIDS for name, _, _ in plasmid.levels]),
    **_leveled_classes(TONIC_CLASS, [tonic.name for tonic in TONICS]),
    "Health Upgrade": "ShockGame.HealthUpgrade",
    "EVE Upgrade": "ShockGame.BioAmmoUpgrade",
    "Plasmid Slot": "ShockGame.ActiveGeneticSlotUpgrade",
    "Physical Tonic Slot": "ShockGame.PhysicalGeneticSlotUpgrade",
    "Engineering Tonic Slot": "ShockGame.EngineeringGeneticSlotUpgrade",
    "Combat Tonic Slot": "ShockGame.WeaponsGeneticSlotUpgrade",  # the game calls the combat track "weapons"
    # The two hypos are given as the classes the shops sell; ShockGame has classes of the same names as well.
    "First Aid Kit": "ShockDesignerClasses.MedHypo",
    "EVE Hypo": "ShockDesignerClasses.BioAmmoHypo",
    "Auto-Hack Tool": "ShockGame.AutoHack",
}

# Weapons have a command of their own: `GiveWeapon <class>`.
WEAPON_CLASS: dict[str, str] = {
    "Pistol": "ShockGame.Pistol",
    "Machine Gun": "ShockGame.MachineGun",
    "Shotgun": "ShockGame.Shotgun",
    "Grenade Launcher": "ShockGame.GrenadeLauncher",
    "Research Camera": "ShockGame.ResearchCamera",
    "Chemical Thrower": "ShockGame.ChemicalThrower",
    "Crossbow": "ShockGame.Crossbow",
}

# So do the Power to the People upgrades: `AddWeaponStatUpgrade <weapon> <stat>`.
WEAPON_UPGRADE: dict[str, tuple[str, str]] = {
    "Pistol Clip Size": ("Pistol", "MagazineSize"),
    "Pistol Damage Increase": ("Pistol", "Damage"),
    "Machine Gun Damage Increase": ("MachineGun", "Damage"),
    "Machine Gun Kickback Reduction": ("MachineGun", "Kickback"),
    "Shotgun Rate of Fire": ("Shotgun", "RateOfFire"),
    "Shotgun Damage Increase": ("Shotgun", "Damage"),
    "Grenade Launcher Damage Increase": ("GrenadeLauncher", "Damage"),
    "Grenade Launcher Damage Immunity": ("GrenadeLauncher", "Immunity"),
    "Chemical Thrower Consumption Rate": ("ChemicalThrower", "ConsumptionRate"),
    "Chemical Thrower Range": ("ChemicalThrower", "Range"),
    "Crossbow Breakage Chance": ("Crossbow", "BreakageChance"),
    "Crossbow Damage Increase": ("Crossbow", "Damage"),
}

# Filler that is a fixed amount of a currency.
CURRENCY_FILLER: dict[str, tuple[int, str]] = {
    "10 ADAM": (10, ADAM_CLASS),
    "25 ADAM": (25, ADAM_CLASS),
    "$25": (25, DOLLARS_CLASS),
    "$50": (50, DOLLARS_CLASS),
    "$100": (100, DOLLARS_CLASS),
}

# Filler that is a number of things: (count, class) for each. The ammunition is the ordinary kind for the three guns
# the player has had since Medical Pavilion.
BUNDLES: dict[str, tuple[tuple[int, str], ...]] = {
    "Ammo Bundle": (
        (12, "ShockGame.Pistol_Bullet"),
        (40, "ShockGame.MachineGun_Bullet"),
        (8, "ShockGame.Shotgun_00Buck"),
    ),
    "Film": ((5, "ShockGame.Film"),),
    # One of each of the twelve things the U-Invent machines take. The classes are from the running game's list.
    "Invention Components": tuple((1, f"ShockDesignerClasses.{name}Component") for name in (
        "Alcohol", "Battery", "BrassTube", "ChlorophyllSolution", "DistilledWater", "EmptyHypo", "EnzymeSample",
        "Glue", "Kerosene", "RubberHose", "ShellCasing", "SteelScrew",
    )),
}

# Traps the game has console commands for.
# `StartSecurityAlarm` alone rings the alarm and starts its timer, but the bots come from the level's own spawners,
# and none came when it was given from the console (2026-10-07). `summon` puts a bot right in front of the player,
# and one summoned while the alarm rings is hostile; summoned after the alarm has stopped it arrives already hacked
# and friendly, so the alarm goes first. Of the bot classes tried in Fisheries, only the Deck2 one was found
# (2026-10-07); the classes of ShockAIClasses are not all loaded in every level.
SECURITY_BOT = "ShockAIClasses.SpawnedDeck2MediumSecurityBot"
TRAP_COMMANDS: dict[str, tuple[str, ...]] = {
    "Security Alarm Trap": ("StartSecurityAlarm", f"summon {SECURITY_BOT}"),
}
# How many of a trap's last commands may fail without the trap failing: an alarm with no bot is still a trap, and
# setting it aside would ring the alarm a second time on /retry.
TRAP_OPTIONAL: dict[str, int] = {
    "Security Alarm Trap": 1,
}

# The other traps are not console commands. The agent implements them by name.
TRAP_ACTIONS: dict[str, str] = {
    "EVE Drain Trap": "eve_drain",
    "Pickpocket Trap": "pickpocket",
}

ITEM_NAME: dict[int, str] = {item.id: item.name for item in ITEM_TABLE}
ITEM_KIND: dict[str, str] = {item.name: item.kind for item in ITEM_TABLE}
_PLASMID_LEVELS: dict[str, tuple[tuple[str, str, str], ...]] = {
    plasmid.item_name: plasmid.levels for plasmid in PLASMIDS
}


@dataclass(frozen=True)
class Delivery:
    """How to hand one received item to the game."""
    description: str  # what the player is getting, e.g. "Winter Blast 2"
    commands: tuple[str, ...] = ()  # console commands, run in order
    optional: int = 0  # how many of the last commands may fail without the item failing
    action: str | None = None  # named agent action instead of commands (traps)

    @property
    def is_instant(self) -> bool:
        """True when there is nothing to send to the game (level access items, surplus copies)."""
        return not self.commands and self.action is None


def give_command(count: int, class_name: str) -> str:
    return f"GiveItem {count} {class_name}"


def plasmid_levels(item_name: str, world_items_shuffled: bool = False) -> tuple[str, ...]:
    """The in-game plasmid each successive copy of a plasmid item stands for.

    While world pickups are vanilla, the levels you find lying around are not in the pool, so the first copy of
    "Progressive Electro Bolt" is Electro Bolt 2. Plasmids that only exist as a world pickup keep their one level, so
    a copy sent with a server command still does something.
    """
    levels = _PLASMID_LEVELS[item_name]
    if not world_items_shuffled:
        pooled = tuple(name for name, source, _ in levels if source != Source.WORLD)
        if pooled:
            return pooled
    return tuple(name for name, _, _ in levels)


def plan_delivery(item_name: str, copy_number: int, world_items_shuffled: bool = False) -> Delivery | None:
    """Work out how to deliver the `copy_number`-th (1-based) received copy of an item.

    Returns None when the item can't be delivered yet because its game class isn't known.
    """
    kind = ITEM_KIND[item_name]

    if kind == ItemKind.LEVEL_ACCESS:
        return Delivery(item_name)

    if kind == ItemKind.PLASMID:
        levels = plasmid_levels(item_name, world_items_shuffled)
        if copy_number > len(levels):
            return Delivery(f"{item_name} (extra copy, already at the highest level)")
        target = levels[copy_number - 1]
        class_name = GIVE_CLASS.get(target)
        return Delivery(target, (give_command(1, class_name),)) if class_name else None

    if kind == ItemKind.TRAP:
        if item_name in TRAP_COMMANDS:
            return Delivery(item_name, TRAP_COMMANDS[item_name], optional=TRAP_OPTIONAL.get(item_name, 0))
        return Delivery(item_name, action=TRAP_ACTIONS[item_name])

    if kind == ItemKind.WEAPON:
        class_name = WEAPON_CLASS.get(item_name)
        return Delivery(item_name, (f"GiveWeapon {class_name}",)) if class_name else None

    if kind == ItemKind.WEAPON_UPGRADE:
        upgrade = WEAPON_UPGRADE.get(item_name)
        return Delivery(item_name, (f"AddWeaponStatUpgrade {upgrade[0]} {upgrade[1]}",)) if upgrade else None

    if item_name in CURRENCY_FILLER:
        count, class_name = CURRENCY_FILLER[item_name]
        return Delivery(item_name, (give_command(count, class_name),))

    if item_name in BUNDLES:
        return Delivery(item_name, tuple(give_command(count, class_name) for count, class_name in BUNDLES[item_name]))

    class_name = GIVE_CLASS.get(item_name)
    return Delivery(item_name, (give_command(1, class_name),)) if class_name else None


def classes_given() -> dict[str, list[str]]:
    """Every game class the tables above name -> the items it is for. For the tests and the to-do list."""
    used: dict[str, list[str]] = {}
    for name, class_name in GIVE_CLASS.items():
        used.setdefault(class_name, []).append(name)
    for name, class_name in WEAPON_CLASS.items():
        used.setdefault(class_name, []).append(name)
    for name, (_, class_name) in CURRENCY_FILLER.items():
        used.setdefault(class_name, []).append(name)
    for name, parts in BUNDLES.items():
        for _, class_name in parts:
            used.setdefault(class_name, []).append(name)
    return used


def undeliverable_items() -> list[str]:
    """Item names (per plasmid level) that still need a game class. For status output and the to-do list."""
    missing: list[str] = []
    for item in ITEM_TABLE:
        if item.kind == ItemKind.PLASMID:
            missing += [f"{item.name}: {level}" for level, _, _ in _PLASMID_LEVELS[item.name]
                        if level not in GIVE_CLASS]
        elif plan_delivery(item.name, 1) is None:
            missing.append(item.name)
    return missing


# --------------------------------------------------------------------------------------------------------------------
# Game events -> location IDs
# --------------------------------------------------------------------------------------------------------------------

LOCATION_LEVEL: dict[int, str] = {location.id: location.level for location in LOCATION_TABLE}
LOCATION_NAME: dict[int, str] = {location.id: location.name for location in LOCATION_TABLE}
_LOCATION_ID: dict[str, int] = {location.name: location.id for location in LOCATION_TABLE}

DIARY_COUNT = sum(1 for location in LOCATION_TABLE if location.category == LocationCategory.AUDIO_DIARY)


def _numbered_locations(category: str) -> dict[tuple[str, int], int]:
    """(level, k) -> location ID for categories counted per level, k starting at 1."""
    result: dict[tuple[str, int], int] = {}
    seen: dict[str, int] = {}
    for location in LOCATION_TABLE:
        if location.category == category:
            seen[location.level] = seen.get(location.level, 0) + 1
            result[location.level, seen[location.level]] = location.id
    return result


LITTLE_SISTER_LOCATION: dict[tuple[str, int], int] = _numbered_locations(LocationCategory.LITTLE_SISTER)
STATION_LOCATION: dict[tuple[str, int], int] = _numbered_locations(LocationCategory.POWER_TO_THE_PEOPLE)
REEL_LOCATION: dict[str, int] = {
    location.level: location.id for location in LOCATION_TABLE
    if location.category == LocationCategory.DIRECTORS_COMMENTARY
}
LEVEL_COMPLETION_LOCATION: dict[str, int] = {
    level: _LOCATION_ID[level_completion_location_name(level)] for level in LEVEL_COMPLETION_LEVELS
}
MILESTONE_LOCATION: dict[str, int] = {
    milestone.key: _LOCATION_ID[story_location_name(milestone)] for milestone in STORY_MILESTONES
}


# Which of the game's quests (its objectives, by object name) completes which story milestone. Read from the running
# game on 2026-10-07 with each quest's on-screen text. Some objectives exist twice, an original and an "Update" that
# replaces it, and either one being completed counts. Milestones with no quest of their own are left out for now:
# electro_bolt, ryans_ambush, ice_wall, peach_wilkins and self_destruct.
QUEST_MILESTONES: dict[str, str] = {
    "DestroySteinmanDebris": "surgery_wreckage",  # "Destroy the debris."
    "QuarantineKey": "steinman",  # "Get the key from Steinman."
    "ResearchSplicers": "spider_photos",  # "Photograph 3 spider splicers."
    "OpenSubDoors": "submarine_bay",  # "Open the hatch."
    "BringRoseToLangford": "rosa_gallica",  # "Bring the rose specimen to Langford."
    "BringRoseToLangfordUpdateA": "rosa_gallica",  # "Put the rose in the Pneumo."
    "FindMPRFormula": "langfords_safe",  # "Search Langford's office."
    "GatherChloro": "chlorophyll",  # "Obtain 7 Chlorophyll Solution."
    "GatherChloroUpdateA": "chlorophyll",
    "ReleaseMPR": "lazarus_vector",  # "Deploy the Lazarus Vector."
    "ReleaseMPRUpdateC": "lazarus_vector",
    "DefendMPRAmbush": "defend_lab",  # "Hold off Ryan's forces."
    "GatherWater": "distilled_water",  # "Obtain 7 Distilled Water."
    "GatherWaterUpdateA": "distilled_water",
    "GatherEnzymes": "enzyme_samples",  # "Obtain 7 Enzyme Samples."
    "GatherEnzymesUpdateA": "enzyme_samples",
    "TakeFirstPhoto": "fitzpatrick",  # "Photograph the dead pianist."
    "KillFinneganUpdateA": "finnegan",  # "Photograph Finnegan's corpse."
    "KillCobbUpdateA": "cobb",  # "Photograph Cobb's corpse."
    "KillRodriguezUpdateA": "rodriguez",  # "Photograph Rodriguez's corpse."
    "ReplaceThreeMorePhotos": "masterpiece",  # "Finish Cohen's Masterpiece."
    "FindNitroglycerin": "nitroglycerin",  # "Find one Nitroglycerin Charge."
    "AssembleBomb": "emp_bomb",  # "Finish Kyburz's EMP Bomb"
    "OverloadGenerator": "core_overload",  # "Overload the Core."
    "KillRyan": "andrew_ryan",  # "Kill Andrew Ryan."
    "Get1stDose": "lot_192_first",  # "Get the Lot 192 remedy."
    "Get1stDoseUpdated": "lot_192_first",
    "Get2ndDoseAtTen": "lot_192_second",  # "Get a second dose of Lot 192."
    "Get2ndDoseAtTenUpdated": "lot_192_second",
    "Get2ndDoseAtLab": "lot_192_second",
    "UseVoiceboxMachine": "voice_box",  # "Sound: Use the Larynx Modification Machine."
    "FindPheremoneSamples": "pheromones",  # "Smell: Find 3 Big Daddy Pheromone Samples."
    "GetBodysuit": "bodysuit",  # "Look: Find a Big Daddy Bodysuit."
    "GetHelmet": "helmet",  # "Look: Find a Big Daddy Helmet."
    "GetBoots": "boots",  # "Look: Find Big Daddy Boots."
    "EscortGathererToEnd": "escort",  # "Escort the Little Sister"
}
assert set(QUEST_MILESTONES.values()) <= set(MILESTONE_LOCATION), "every quest leads to a real milestone"


def milestones_of(completed_quests: object) -> frozenset[str] | None:
    """The story milestones reached, from the names of the quests the agent saw completed. None while it cannot
    tell (it has not looked the quests up yet, or it has no way to)."""
    if not isinstance(completed_quests, (list, tuple)):
        return None
    return frozenset(QUEST_MILESTONES[name] for name in completed_quests if name in QUEST_MILESTONES)


# Which class of the game's is which audio diary (the number in AUDIO_DIARIES). Each diary is an item class of its
# own; the game's title for each (read from the running game on 2026-10-08) matched exactly one diary here. The game
# has eight more logs that are not checks, and files diary 60 under Neptune's Bounty in its log screen.
DIARY_CLASSES: dict[str, int] = {
    "Med_Cf_WishUWereHere": 1,  # New Year's Eve Alone
    "Med_Sb_BathroomWall": 2,  # Hole in Bathroom Wall
    "Med_Cf_ReleasedToday": 3,  # Released Today
    "Med_St_RantA": 4,  # ADAM's Changes
    "Med_St_RantB": 5,  # Higher Standards
    "Med_Ry_ProwlStreets": 6,  # Parasite Expectations
    "Med_Tn_Properly": 7,  # Love for Science
    "Med_St_RantC": 8,  # Limits of Imagination
    "Med_Ry_Vandalism": 9,  # Vandalism
    "Med_St_RantD": 10,  # Surgery's Picasso
    "Med_Md_FrozenPipes": 11,  # Freezing Pipes
    "Med_Sg_ClinicalTrail": 12,  # Enrage Trial
    "Med_Tn_Wunderkind": 13,  # Useless Experiments
    "Med_Sg_TelekinesisProgress": 14,  # Testing Telekinesis
    "Med_Sg_PityFreak": 15,  # Plasmids Are the Paint
    "Med_St_Symmetry": 16,  # Symmetry
    "Med_St_Aphrodite": 17,  # Aphrodite Walking
    "Med_St_NotWhatSheWanted": 18,  # Not What She Wanted
    "Med_St_AdamFactories": 19,  # Gatherer Vulnerability
    "Fis_Ry_FontaineMustGo": 20,  # Fontaine Must Go
    "Fis_Pd_BathyLockdown": 21,  # Bathysphere Keys
    "Fis_Tn_TenSawSmugglers": 22,  # Finding the Sea Slug
    "Fis_Pd_SuvsInteroLog": 23,  # Picked Up Timmy H.
    "Fis_Ml_ComeTo": 24,  # Masha Come Home
    "Fis_Ry_MenaceFontaine": 25,  # Watch Fontaine
    "Fis_Pd_HaveMyBadge": 26,  # Have My Badge
    "Fis_Tn_TenCrazySlug": 27,  # ADAM Discovery
    "Fis_Md_LeakyEden": 28,  # Eden Leaking
    "Fis_Tn_TenPigMen": 29,  # Fontaine's Smugglers
    "Fis_Ry_CityInUproar": 30,  # Death Penalty in Rapture
    "Fis_Pd_SmugglingRing": 31,  # Smuggling Ring
    "Fis_Ry_WorkingLate": 32,  # Working Late Again
    "Fis_Md_FontaineFuturisticsLog": 33,  # Arresting Fontaine
    "Fis_Ml_BarelyRecognized": 34,  # Saw Masha Today
    "Fis_Md_WoodWolves": 35,  # Rapture Changing
    "Fis_Md_RyanPlumbing": 36,  # Meeting Ryan
    "Fis_Pd_RyanDoesWorse": 37,  # Timmy H. Interrogation
    "Fis_Pc_PuttingTheScrewsOn": 38,  # Putting the Screws On
    "Fis_Pc_RyanLikeUs": 39,  # Meeting with Fontaine
    "Fis_Ft_FonMadScience": 40,  # Kraut Scientist
    "Fis_Pc_OfferedADeal": 41,  # Offered a Deal
    "Hyd_Md_SeeingGhosts": 42,  # Seeing Ghosts
    "Hyd_Gm_Tryst": 43,  # Big Night Out
    "Hyd_Tn_TenStoryA": 44,  # Mass Producing ADAM
    "Hyd_Pf_LanBioA": 45,  # Arcadia Closed
    "Hyd_Ml_FlavorF": 46,  # Shouldn't Have Come
    "Hyd_Pf_Cultists": 47,  # The Saturnine
    "Hyd_Ry_FlavorB": 48,  # The Market Is Patient
    "Hyd_Cf_FlavorA": 49,  # Heroes and Criminals
    "Hyd_Pf_Roses": 50,  # Early Tests Promising
    "Hyd_Ry_FlavorE": 51,  # Offer a Better Product
    "Hyd_Pf_RecipeHintA": 52,  # What Won't They Steal?
    "Hyd_Pf_LanBioC": 53,  # Teaching an Old Hound
    "Hyd_Pf_LVIntroA": 54,  # The Lazarus Vector
    "Hyd_Pf_Recipe": 55,  # Lazarus Vector Formula
    "Hyd_Pf_LanBioB": 56,  # Arcadia and Oxygen
    "Hyd_Tn_TenStoryB": 57,  # Maternal Instinct
    "Hyd_Td_RecipeHintB": 58,  # Bee Enzyme
    "Hyd_Ry_FlavorD": 59,  # Pulling Together
    "Fis_Ry_BouncerAndGatherer": 60,  # First Encounter
    "Hyd_Tn_TenStoryC": 61,  # Hatred
    "Hyd_Ry_MindControlBadGood": 62,  # Desperate Times
    "Hyd_Pb_RecipeHintC": 63,  # Water in Wine
    "Hyd_Tn_TenStoryD": 64,  # Functional Children
    "Hyd_Tn_AdamExplanation": 65,  # ADAM Explained
    "Hyd_Ry_FlavorC": 66,  # The Great Chain
    "Rec_Cf_StoodUp": 67,  # Stood Up Again
    "Rec_Co_MusicalInsult": 68,  # Musical Insult
    "Rec_Pr_GoRecordStore": 69,  # Come to the Record Store
    "Rec_Co_TakeTheEarsOff": 70,  # The Wild Bunny
    "Rec_Pd_MeatballBeat": 71,  # Artists' Feud
    "Rec_Al_FancyCiggies": 72,  # Fancy Cigarettes
    "Rec_Co_GoingToHell": 73,  # The Doubters
    "Rec_Po_Hallucination": 74,  # The Iceman Cometh
    "Rec_Md_DoneOver": 75,  # Fontaine's Army
    "Rec_Md_GunsBlazing": 76,  # Guns Blazing
    "Rec_Pq_Art": 77,  # It's All Grift
    "Rec_Jj_Pregnancy": 78,  # Pregnancy
    "Rec_Ac_StableBoy": 79,  # Ryan's Stableboy
    "Rec_Pd_BumpCulpepper": 80,  # Bump Culpepper?
    "Rec_Co_CohenToastBroadway": 81,  # Requiem for Andrew Ryan
    "Eng_Md_FlavourA": 82,  # Ryan Takes F Futuristics
    "Eng_Pv_ScopeGate": 83,  # Scoping the Gate
    "Eng_Md_FlavourD": 84,  # Stopping Ryan
    "Eng_Pt_GoToHeatLoss": 85,  # Going to Heat Loss
    "Eng_Ry_Difference": 86,  # A Man or a Parasite
    "Eng_Md_FlavourB": 87,  # Fontaine's Legacy
    "Eng_Pu_RecipeHint": 88,  # Running Short on R-34s
    "Eng_Pt_Assassin": 89,  # Assassin
    "Eng_Ry_WhereElse": 90,  # Impossible Anywhere Else
    "Eng_Pu_KyburzCodeHint": 91,  # Kyburz Door Code
    "Eng_Md_FlavourC": 92,  # Genetic Arms Race
    "Eng_Pu_BombLocation": 93,  # Getting a Break
    "Eng_Pv_AlmostFinished": 94,  # Device Almost Finished
    "Eng_Pu_MarketCode": 95,  # Market Maintenance Code
    "Eng_Ry_OnRaptureA": 96,  # Great Chain Moves Slowly
    "Eng_Pv_Dream": 97,  # The Dream
    "Eng_Pv_BombRecipe": 98,  # Assembling the Bomb
    "Eng_Sg_VitaChamber": 99,  # The Vita-Chamber
    "Eng_Sg_PuppyScene": 100,  # Mind Control Test
    "Eng_Sg_FlavourBaby": 101,  # Baby Status
    "Res_Sg_AboutTenenbaum": 102,  # Mozart of Genetics
    "Res_Pd_ArtistWoman": 103,  # Artist Woman
    "Res_Sg_AboutChild": 104,  # Fontaine's Human Jukebox
    "Res_Sg_FirstQuestTarget": 105,  # Mind Control Antidote
    "Res_Zi_FontCode": 106,  # Fontaine's Breakup
    "Res_Ft_SadSaps": 107,  # Sad Saps
    "Res_Cf_SoAngry": 108,  # What's Happening Here?
    "Res_Cf_Lives": 109,  # Atlas Lives
    "Res_Cf_TheyTrustMe": 110,  # Meeting Atlas
    "Res_Ft_LongCon": 111,  # The Longest Con
    "Res_Cf_Raid": 112,  # Today's Raid
    "Res_Sg_ProtectionBond": 113,  # Protection Bond
    "Sci_Sg_GoodForSuchong": 114,  # Changing Employers
    "Sci_Tn_WhyGirls": 115,  # Why Just Girls?
    "Sci_Sg_OneUseSuits": 116,  # Cheap Son of a Bitch
    "Sci_Sg_Stink": 117,  # Protector Smell
    "Sci_Ry_Mistakes": 118,  # Mistakes
    "Sci_Sg_Opium": 119,  # Protecting Little Ones
    "Sci_Sg_BootsMissingLog": 120,  # Missing Boots
    "Sci_Ry_LittleGirls": 121,  # Marketing Gold
    "Sci_Sg_ExtraMunitions": 122,  # Extra Munitions
}
assert sorted(DIARY_CLASSES.values()) == list(range(1, DIARY_COUNT + 1)), "every diary has exactly one class"


def diaries_of(received: object) -> frozenset[int] | None:
    """The diaries received, from the classes of the logs the agent saw. None while it cannot tell."""
    if not isinstance(received, (list, tuple)):
        return None
    return frozenset(DIARY_CLASSES[name] for name in received if name in DIARY_CLASSES)


def diary_location(number: int) -> int | None:
    return 1000 + number if 1 <= number <= DIARY_COUNT else None


# --------------------------------------------------------------------------------------------------------------------
# Story progress
# --------------------------------------------------------------------------------------------------------------------

STORY_ORDER: tuple[str, ...] = LEVEL_NAMES
STORY_INDEX: dict[str, int] = {level: index for index, level in enumerate(STORY_ORDER)}


def _completion_trigger(level: str) -> int:
    """Story index the player has to reach for `level` to count as finished."""
    if level in (ARCADIA, FARMERS_MARKET):
        # You go Arcadia -> Farmer's Market -> back to Arcadia; both are done once you reach Fort Frolic.
        return STORY_INDEX[FORT_FROLIC]
    return STORY_INDEX[level] + 1


COMPLETION_TRIGGER: dict[str, int] = {level: _completion_trigger(level) for level in LEVEL_COMPLETION_LEVELS}


def completed_levels(furthest_index: int) -> list[str]:
    """Levels whose story is finished once the player has reached `furthest_index` in STORY_ORDER."""
    return [level for level, trigger in COMPLETION_TRIGGER.items() if furthest_index >= trigger]


# Two ways to tell where the player is.
#
# The map's name is the reliable one. The game reports it for the level the player is in, and it is the same however
# the player got there. The names are the game's own map files (Steam build, 2026-10-06), spelled the way
# normalize_map leaves them. "1-medical" has been read out of the running game; the others are matched to their
# levels by name. The level number further down is only the fallback for a state without a map name.
MAP_LEVEL: dict[str, str] = {
    "1-welcome": "Welcome to Rapture",
    "1-medical": "Medical Pavilion",
    "2-fisheries": "Neptune's Bounty",
    "2-subbay": "Smuggler's Hideout",
    "3-arcadia": "Arcadia",
    "3-market": "Farmer's Market",
    "4-recreation": "Fort Frolic",
    "5-hephaestus": "Hephaestus",
    "5-ryan": "Rapture Central Control",
    "6-resi": "Olympus Heights",
    "6-slums": "Apollo Square",
    "7-science": "Point Prometheus",
    "7-gauntlet": "Proving Grounds",
}
CRASH_SITE_MAPS: frozenset[str] = frozenset({"0-lighthouse"})  # the plane crash and lighthouse: a new game starts here
FONTAINE_ARENA_MAPS: frozenset[str] = frozenset({"7-bossfight"})  # the final fight has a map of its own
# Maps that are not part of the story: the engine's own small level, the attract mode, the Remastered museum and the
# Challenge Rooms.
OTHER_MAPS: frozenset[str] = frozenset({
    "entry", "autoplay", "museum", "challengeroomcombat", "challengeroomdecoy", "challengeroomelectric",
})
# Items are only handed over inside Rapture's own levels: not in a Challenge Room, and not before the bathysphere
# ride at the very start.
NO_DELIVERY_MAPS: frozenset[str] = OTHER_MAPS | CRASH_SITE_MAPS
_LANGUAGE_SUFFIXES = ("_chn", "_deu", "_esp", "_fra", "_int", "_ita", "_jpn")  # every map has these variants too

assert set(MAP_LEVEL.values()) == set(LEVEL_NAMES), "every level has exactly one map"
assert len(MAP_LEVEL) == len(LEVEL_NAMES)


def normalize_map(name: object) -> str | None:
    """A map name as the tables above spell it: lower case, without folder, options, ".bsm" or language ending."""
    if not isinstance(name, str):
        return None
    name = name.strip().split("?", 1)[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    if name.endswith(".bsm"):
        name = name[:-4]
    for suffix in _LANGUAGE_SUFFIXES:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    return name or None


# The LiveSplit autosplitter's "level" value is not an ID. Its values behave like the room in one of the engine's
# lists: a fixed number when a level is entered by playing, and more each time the list fills up and the engine
# enlarges it. The enlargement follows the engine's usual rule, which reproduces every second value the autosplitter
# lists. The numbers only hold in a game played straight through: after loading a save the value is something else
# entirely (1030 in Medical Pavilion, seen live). That is why the map's name comes first.
_ARRIVAL_VALUE: dict[str, int] = {
    "Welcome to Rapture": 5538,
    "Medical Pavilion": 7039,
    "Neptune's Bounty": 8543,
    "Smuggler's Hideout": 3205,
    "Arcadia": 8187,
    "Farmer's Market": 5448,
    "Fort Frolic": 9317,
    "Hephaestus": 6932,
    "Rapture Central Control": 2116,
    "Olympus Heights": 8291,
    "Apollo Square": 5804,
    "Point Prometheus": 8672,
    "Proving Grounds": 4101,
}
_CRASH_SITE_ARRIVAL = 1555  # the plane crash and lighthouse, before Welcome to Rapture proper
_FONTAINE_ARENA_ARRIVAL = 928  # the final fight is its own map
GROWTH_STEPS = 4  # how many enlargements of each map's list to recognise; four already means 3.5 times the actors


def grown(capacity: int) -> int:
    """Room in an engine list after it was full at `capacity` and one more element was added."""
    count = capacity + 1
    return count + 3 * count // 8 + 32


def level_values(arrival: int, steps: int = GROWTH_STEPS) -> tuple[int, ...]:
    """Every value a map can show: the one it loads with, then one per enlargement."""
    values = [arrival]
    for _ in range(steps):
        values.append(grown(values[-1]))
    return tuple(values)


CRASH_SITE_VALUES: frozenset[int] = frozenset(level_values(_CRASH_SITE_ARRIVAL))
FONTAINE_ARENA_VALUES: frozenset[int] = frozenset(level_values(_FONTAINE_ARENA_ARRIVAL))

LEVEL_FROM_VALUE: dict[int, str] = {
    value: level for level, arrival in _ARRIVAL_VALUE.items() for value in level_values(arrival)
}
LEVEL_FROM_VALUE.update({value: "Welcome to Rapture" for value in CRASH_SITE_VALUES})
LEVEL_FROM_VALUE.update({value: PROVING_GROUNDS for value in FONTAINE_ARENA_VALUES})

assert set(_ARRIVAL_VALUE) == set(LEVEL_NAMES)
assert len(LEVEL_FROM_VALUE) == (len(LEVEL_NAMES) + 2) * (GROWTH_STEPS + 1), "no two maps may share a value"

FONTAINE_FIGHT_PHASE = 3  # the last phase of the fight ...
FONTAINE_DEFEATED_PHASE = 4  # ... and the value it changes to when he is beaten


def level_of(map_name: str | None, level_value: int | None) -> str | None:
    """The level the player is in, or None if that cannot be told. A map the client knows by name decides."""
    if map_name in MAP_LEVEL:
        return MAP_LEVEL[map_name]
    if map_name in CRASH_SITE_MAPS:
        return "Welcome to Rapture"
    if map_name in FONTAINE_ARENA_MAPS:
        return PROVING_GROUNDS
    if map_name in OTHER_MAPS:
        return None
    return LEVEL_FROM_VALUE.get(level_value)  # type: ignore[arg-type]


def known_place(map_name: str | None) -> bool:
    """Whether the map's name alone says where the player is."""
    return map_name in MAP_LEVEL or map_name in CRASH_SITE_MAPS or map_name in FONTAINE_ARENA_MAPS \
        or map_name in OTHER_MAPS


def delivers_in(map_name: str | None) -> bool:
    """Whether items may be handed over in this map. A map the client has no name for gets the benefit of the
    doubt, as before there were map names."""
    return map_name not in NO_DELIVERY_MAPS


def at_crash_site(map_name: str | None, level_value: int | None) -> bool:
    """The very start of a new game."""
    if known_place(map_name):
        return map_name in CRASH_SITE_MAPS
    return level_value in CRASH_SITE_VALUES


def in_fontaine_arena(map_name: str | None, level_value: int | None) -> bool | None:
    """True in the final fight's own map, False when the player is known to be elsewhere (the level number is 0
    while a level loads), None when neither the map's name nor the level number says."""
    if known_place(map_name):
        return map_name in FONTAINE_ARENA_MAPS
    if level_value in FONTAINE_ARENA_VALUES:
        return True
    if level_value == 0 or level_value in LEVEL_FROM_VALUE:
        return False
    return None

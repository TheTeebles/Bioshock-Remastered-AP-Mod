"""
Static game data for BioShock (BioShock Remastered, PC).

Everything the APWorld knows about the game lives here, so the item/location/region code stays generic
and the game client can import the exact same tables to map in-game events to Archipelago IDs.

ID scheme (stable once released, never reuse an ID for something else):
    Locations                            Items
    1001-1122  Audio Diaries             100-107  Level Access
    2001-2021  Little Sisters            200-210  Plasmids
    3001-3010  Director's Commentary     300-357  Gene Tonics
    4001-4012  Power to the People       400-406  Weapons            (reserved, not shuffled yet)
    5001-5012  Level Completion          450-461  Weapon Upgrades    (reserved, not shuffled yet)
    6001-6999  World items, containers   500-505  Character Upgrades
               (reserved, not used yet)  900-910  Filler
    7001-7033  Story                     950-952  Traps

Data sources are listed in docs/en_BioShock.md. Values marked "verify" in the design doc should be
confirmed in-game while building the client.
"""
from __future__ import annotations

from typing import NamedTuple

GAME_NAME = "BioShock"


# --------------------------------------------------------------------------------------------------------------------
# Levels
# --------------------------------------------------------------------------------------------------------------------

class Level(NamedTuple):
    name: str
    access_item: str | None  # item that unlocks this level's checks when level_access is "access_items"
    revisitable: bool  # can the player come back via the bathysphere network?


WELCOME_TO_RAPTURE = "Welcome to Rapture"
MEDICAL_PAVILION = "Medical Pavilion"
NEPTUNES_BOUNTY = "Neptune's Bounty"
SMUGGLERS_HIDEOUT = "Smuggler's Hideout"
ARCADIA = "Arcadia"
FARMERS_MARKET = "Farmer's Market"
FORT_FROLIC = "Fort Frolic"
HEPHAESTUS = "Hephaestus"
RAPTURE_CENTRAL_CONTROL = "Rapture Central Control"
OLYMPUS_HEIGHTS = "Olympus Heights"
APOLLO_SQUARE = "Apollo Square"
POINT_PROMETHEUS = "Point Prometheus"
PROVING_GROUNDS = "Proving Grounds"

NEPTUNES_BOUNTY_ACCESS = "Neptune's Bounty Access"
ARCADIA_ACCESS = "Arcadia Access"
FARMERS_MARKET_ACCESS = "Farmer's Market Access"
FORT_FROLIC_ACCESS = "Fort Frolic Access"
HEPHAESTUS_ACCESS = "Hephaestus Access"
OLYMPUS_HEIGHTS_ACCESS = "Olympus Heights Access"
APOLLO_SQUARE_ACCESS = "Apollo Square Access"
POINT_PROMETHEUS_ACCESS = "Point Prometheus Access"

# Story order. Small levels share the access item of the level they hang off.
LEVELS: tuple[Level, ...] = (
    Level(WELCOME_TO_RAPTURE, None, False),
    Level(MEDICAL_PAVILION, None, True),
    Level(NEPTUNES_BOUNTY, NEPTUNES_BOUNTY_ACCESS, True),
    Level(SMUGGLERS_HIDEOUT, NEPTUNES_BOUNTY_ACCESS, True),
    Level(ARCADIA, ARCADIA_ACCESS, True),
    Level(FARMERS_MARKET, FARMERS_MARKET_ACCESS, True),
    Level(FORT_FROLIC, FORT_FROLIC_ACCESS, True),
    Level(HEPHAESTUS, HEPHAESTUS_ACCESS, True),
    Level(RAPTURE_CENTRAL_CONTROL, HEPHAESTUS_ACCESS, True),
    Level(OLYMPUS_HEIGHTS, OLYMPUS_HEIGHTS_ACCESS, True),
    Level(APOLLO_SQUARE, APOLLO_SQUARE_ACCESS, True),
    Level(POINT_PROMETHEUS, POINT_PROMETHEUS_ACCESS, True),
    Level(PROVING_GROUNDS, POINT_PROMETHEUS_ACCESS, True),
)
LEVEL_BY_NAME: dict[str, Level] = {level.name: level for level in LEVELS}
LEVEL_NAMES: tuple[str, ...] = tuple(level.name for level in LEVELS)

ACCESS_ITEMS: tuple[str, ...] = (
    NEPTUNES_BOUNTY_ACCESS,
    ARCADIA_ACCESS,
    FARMERS_MARKET_ACCESS,
    FORT_FROLIC_ACCESS,
    HEPHAESTUS_ACCESS,
    OLYMPUS_HEIGHTS_ACCESS,
    APOLLO_SQUARE_ACCESS,
    POINT_PROMETHEUS_ACCESS,
)

# The goal: the last phase of the Frank Fontaine fight at the end of Proving Grounds.
GOAL_LOCATION = "Defeat Frank Fontaine"
GOAL_LEVEL = PROVING_GROUNDS
VICTORY_ITEM = "Victory"


# --------------------------------------------------------------------------------------------------------------------
# Locations
# --------------------------------------------------------------------------------------------------------------------

class AudioDiary(NamedTuple):
    number: int  # 1-122, story order; location ID = 1000 + number
    level: str
    title: str


AUDIO_DIARIES: tuple[AudioDiary, ...] = (
    # Welcome to Rapture (2) -- the only level that can never be revisited
    AudioDiary(1, WELCOME_TO_RAPTURE, "New Year's Eve Alone"),
    AudioDiary(2, WELCOME_TO_RAPTURE, "Hole in Bathroom Wall"),
    # Medical Pavilion (17)
    AudioDiary(3, MEDICAL_PAVILION, "Released Today"),
    AudioDiary(4, MEDICAL_PAVILION, "ADAM's Changes"),
    AudioDiary(5, MEDICAL_PAVILION, "Higher Standards"),
    AudioDiary(6, MEDICAL_PAVILION, "Parasite Expectations"),
    AudioDiary(7, MEDICAL_PAVILION, "Love for Science"),
    AudioDiary(8, MEDICAL_PAVILION, "Limits of Imagination"),
    AudioDiary(9, MEDICAL_PAVILION, "Vandalism"),
    AudioDiary(10, MEDICAL_PAVILION, "Surgery's Picasso"),
    AudioDiary(11, MEDICAL_PAVILION, "Freezing Pipes"),
    AudioDiary(12, MEDICAL_PAVILION, "Enrage Trial"),
    AudioDiary(13, MEDICAL_PAVILION, "Useless Experiments"),
    AudioDiary(14, MEDICAL_PAVILION, "Testing Telekinesis"),
    AudioDiary(15, MEDICAL_PAVILION, "Plasmids Are the Paint"),
    AudioDiary(16, MEDICAL_PAVILION, "Symmetry"),
    AudioDiary(17, MEDICAL_PAVILION, "Aphrodite Walking"),
    AudioDiary(18, MEDICAL_PAVILION, "Not What She Wanted"),
    AudioDiary(19, MEDICAL_PAVILION, "Gatherer Vulnerability"),
    # Neptune's Bounty (19)
    AudioDiary(20, NEPTUNES_BOUNTY, "Fontaine Must Go"),
    AudioDiary(21, NEPTUNES_BOUNTY, "Bathysphere Keys"),
    AudioDiary(22, NEPTUNES_BOUNTY, "Finding the Sea Slug"),
    AudioDiary(23, NEPTUNES_BOUNTY, "Picked Up Timmy H."),
    AudioDiary(24, NEPTUNES_BOUNTY, "Masha Come Home"),
    AudioDiary(25, NEPTUNES_BOUNTY, "Watch Fontaine"),
    AudioDiary(26, NEPTUNES_BOUNTY, "Have My Badge"),
    AudioDiary(27, NEPTUNES_BOUNTY, "ADAM Discovery"),
    AudioDiary(28, NEPTUNES_BOUNTY, "Eden Leaking"),
    AudioDiary(29, NEPTUNES_BOUNTY, "Fontaine's Smugglers"),
    AudioDiary(30, NEPTUNES_BOUNTY, "Death Penalty in Rapture"),
    AudioDiary(31, NEPTUNES_BOUNTY, "Smuggling Ring"),
    AudioDiary(32, NEPTUNES_BOUNTY, "Working Late Again"),
    AudioDiary(33, NEPTUNES_BOUNTY, "Arresting Fontaine"),
    AudioDiary(34, NEPTUNES_BOUNTY, "Saw Masha Today"),
    AudioDiary(35, NEPTUNES_BOUNTY, "Rapture Changing"),
    AudioDiary(36, NEPTUNES_BOUNTY, "Meeting Ryan"),
    AudioDiary(37, NEPTUNES_BOUNTY, "Timmy H. Interrogation"),
    AudioDiary(38, NEPTUNES_BOUNTY, "Putting the Screws On"),
    # Smuggler's Hideout (3)
    AudioDiary(39, SMUGGLERS_HIDEOUT, "Meeting with Fontaine"),
    AudioDiary(40, SMUGGLERS_HIDEOUT, "Kraut Scientist"),
    AudioDiary(41, SMUGGLERS_HIDEOUT, "Offered a Deal"),
    # Arcadia (17, including "The Great Chain", found on the return trip)
    AudioDiary(42, ARCADIA, "Seeing Ghosts"),
    AudioDiary(43, ARCADIA, "Big Night Out"),
    AudioDiary(44, ARCADIA, "Mass Producing ADAM"),
    AudioDiary(45, ARCADIA, "Arcadia Closed"),
    AudioDiary(46, ARCADIA, "Shouldn't Have Come"),
    AudioDiary(47, ARCADIA, "The Saturnine"),
    AudioDiary(48, ARCADIA, "The Market Is Patient"),
    AudioDiary(49, ARCADIA, "Heroes and Criminals"),
    AudioDiary(50, ARCADIA, "Early Tests Promising"),
    AudioDiary(51, ARCADIA, "Offer a Better Product"),
    AudioDiary(52, ARCADIA, "What Won't They Steal?"),
    AudioDiary(53, ARCADIA, "Teaching an Old Hound"),
    AudioDiary(54, ARCADIA, "The Lazarus Vector"),
    AudioDiary(55, ARCADIA, "Lazarus Vector Formula"),
    AudioDiary(56, ARCADIA, "Arcadia and Oxygen"),
    AudioDiary(57, ARCADIA, "Maternal Instinct"),
    # Farmer's Market (8)
    AudioDiary(58, FARMERS_MARKET, "Bee Enzyme"),
    AudioDiary(59, FARMERS_MARKET, "Pulling Together"),
    AudioDiary(60, FARMERS_MARKET, "First Encounter"),
    AudioDiary(61, FARMERS_MARKET, "Hatred"),
    AudioDiary(62, FARMERS_MARKET, "Desperate Times"),
    AudioDiary(63, FARMERS_MARKET, "Water in Wine"),
    AudioDiary(64, FARMERS_MARKET, "Functional Children"),
    AudioDiary(65, FARMERS_MARKET, "ADAM Explained"),
    # Back in Arcadia
    AudioDiary(66, ARCADIA, "The Great Chain"),
    # Fort Frolic (15)
    AudioDiary(67, FORT_FROLIC, "Stood Up Again"),
    AudioDiary(68, FORT_FROLIC, "Musical Insult"),
    AudioDiary(69, FORT_FROLIC, "Come to the Record Store"),
    AudioDiary(70, FORT_FROLIC, "The Wild Bunny"),
    AudioDiary(71, FORT_FROLIC, "Artists' Feud"),
    AudioDiary(72, FORT_FROLIC, "Fancy Cigarettes"),
    AudioDiary(73, FORT_FROLIC, "The Doubters"),
    AudioDiary(74, FORT_FROLIC, "The Iceman Cometh"),
    AudioDiary(75, FORT_FROLIC, "Fontaine's Army"),
    AudioDiary(76, FORT_FROLIC, "Guns Blazing"),
    AudioDiary(77, FORT_FROLIC, "It's All Grift"),
    AudioDiary(78, FORT_FROLIC, "Pregnancy"),
    AudioDiary(79, FORT_FROLIC, "Ryan's Stableboy"),
    AudioDiary(80, FORT_FROLIC, "Bump Culpepper?"),
    AudioDiary(81, FORT_FROLIC, "Requiem for Andrew Ryan"),
    # Hephaestus (17)
    AudioDiary(82, HEPHAESTUS, "Ryan Takes F Futuristics"),
    AudioDiary(83, HEPHAESTUS, "Scoping the Gate"),
    AudioDiary(84, HEPHAESTUS, "Stopping Ryan"),
    AudioDiary(85, HEPHAESTUS, "Going to Heat Loss"),
    AudioDiary(86, HEPHAESTUS, "A Man or a Parasite"),
    AudioDiary(87, HEPHAESTUS, "Fontaine's Legacy"),
    AudioDiary(88, HEPHAESTUS, "Running Short on R-34s"),
    AudioDiary(89, HEPHAESTUS, "Assassin"),
    AudioDiary(90, HEPHAESTUS, "Impossible Anywhere Else"),
    AudioDiary(91, HEPHAESTUS, "Kyburz Door Code"),
    AudioDiary(92, HEPHAESTUS, "Genetic Arms Race"),
    AudioDiary(93, HEPHAESTUS, "Getting a Break"),
    AudioDiary(94, HEPHAESTUS, "Device Almost Finished"),
    AudioDiary(95, HEPHAESTUS, "Market Maintenance Code"),
    AudioDiary(96, HEPHAESTUS, "Great Chain Moves Slowly"),
    AudioDiary(97, HEPHAESTUS, "The Dream"),
    AudioDiary(98, HEPHAESTUS, "Assembling the Bomb"),
    # Rapture Central Control (3)
    AudioDiary(99, RAPTURE_CENTRAL_CONTROL, "The Vita-Chamber"),
    AudioDiary(100, RAPTURE_CENTRAL_CONTROL, "Mind Control Test"),
    AudioDiary(101, RAPTURE_CENTRAL_CONTROL, "Baby Status"),
    # Olympus Heights (6)
    AudioDiary(102, OLYMPUS_HEIGHTS, "Mozart of Genetics"),
    AudioDiary(103, OLYMPUS_HEIGHTS, "Artist Woman"),
    AudioDiary(104, OLYMPUS_HEIGHTS, "Fontaine's Human Jukebox"),
    AudioDiary(105, OLYMPUS_HEIGHTS, "Mind Control Antidote"),
    AudioDiary(106, OLYMPUS_HEIGHTS, "Fontaine's Breakup"),
    AudioDiary(107, OLYMPUS_HEIGHTS, "Sad Saps"),
    # Apollo Square (6)
    AudioDiary(108, APOLLO_SQUARE, "What's Happening Here?"),
    AudioDiary(109, APOLLO_SQUARE, "Atlas Lives"),
    AudioDiary(110, APOLLO_SQUARE, "Meeting Atlas"),
    AudioDiary(111, APOLLO_SQUARE, "The Longest Con"),
    AudioDiary(112, APOLLO_SQUARE, "Today's Raid"),
    AudioDiary(113, APOLLO_SQUARE, "Protection Bond"),
    # Point Prometheus (9)
    AudioDiary(114, POINT_PROMETHEUS, "Changing Employers"),
    AudioDiary(115, POINT_PROMETHEUS, "Why Just Girls?"),
    AudioDiary(116, POINT_PROMETHEUS, "Cheap Son of a Bitch"),
    AudioDiary(117, POINT_PROMETHEUS, "Protector Smell"),
    AudioDiary(118, POINT_PROMETHEUS, "Mistakes"),
    AudioDiary(119, POINT_PROMETHEUS, "Protecting Little Ones"),
    AudioDiary(120, POINT_PROMETHEUS, "Missing Boots"),
    AudioDiary(121, POINT_PROMETHEUS, "Marketing Gold"),
    AudioDiary(122, POINT_PROMETHEUS, "Extra Munitions"),
)

# Little Sisters (rescue OR harvest counts). 21 total. Location k in a level = the k-th sister you deal with there.
LITTLE_SISTERS_PER_LEVEL: dict[str, int] = {
    MEDICAL_PAVILION: 2,
    NEPTUNES_BOUNTY: 3,
    ARCADIA: 2,
    FARMERS_MARKET: 1,
    FORT_FROLIC: 3,
    HEPHAESTUS: 3,
    OLYMPUS_HEIGHTS: 2,
    APOLLO_SQUARE: 2,
    POINT_PROMETHEUS: 3,
}

# Remastered-only "Director's Commentary" golden film reels. One per listed level, 10 total.
DIRECTORS_COMMENTARY_LEVELS: tuple[str, ...] = (
    WELCOME_TO_RAPTURE,  # Footlight Theater stage
    MEDICAL_PAVILION,  # next to the Gatherer's Garden after the first Little Sister
    NEPTUNES_BOUNTY,  # Fontaine Fisheries freezer shelf
    FARMERS_MARKET,  # Worley Winery cellar shack
    FORT_FROLIC,  # Fleet Hall projection room
    HEPHAESTUS,  # shelf under the EMP bomb casing
    RAPTURE_CENTRAL_CONTROL,  # Andrew Ryan's desk
    APOLLO_SQUARE,  # couch near the bathysphere
    POINT_PROMETHEUS,  # Little Wonders Educational Facility, Little Sister Room 1
    PROVING_GROUNDS,  # next to the final U-Invent machine
)

# Power to the People weapon upgrade stations. 12 total, one upgrade each.
# Location k in a level = the k-th station you use there.
POWER_TO_THE_PEOPLE_PER_LEVEL: dict[str, int] = {
    NEPTUNES_BOUNTY: 1,  # Fontaine Fisheries freezer, bottom floor
    ARCADIA: 1,  # Tree Farm
    FARMERS_MARKET: 1,  # Worley Winery cellar, bottom floor
    FORT_FROLIC: 2,  # Le Marquis d'Epoque; Sinclair Spirits
    HEPHAESTUS: 2,  # Hephaestus Core; Kyburz's office
    OLYMPUS_HEIGHTS: 2,  # Mercury Suites (two stations)
    APOLLO_SQUARE: 1,  # Hestia Chambers, 4th floor
    POINT_PROMETHEUS: 2,  # Atrium; Optimized Eugenics
}

# Levels whose first story completion is a check (arriving in the next level for the first time).
# Proving Grounds is excluded because finishing it is the goal.
LEVEL_COMPLETION_LEVELS: tuple[str, ...] = tuple(name for name in LEVEL_NAMES if name != PROVING_GROUNDS)


class StoryMilestone(NamedTuple):
    number: int  # location ID is 7000 + number. Never renumber: add new ones with the next free number
    key: str  # short name the game agent and the client use for it
    level: str
    title: str


# Bosses and main objectives. Every one of them has to happen to finish the game, so none can be missed, not even
# in Welcome to Rapture. They are grouped by level, and in the order they happen within a level (the story leaves
# Arcadia for Farmer's Market and comes back to release the Lazarus Vector).
# Picking up weapons, plasmids and tonics is left out on purpose: those become checks of their own when world items
# are shuffled. Electro Bolt is the exception. It is a scripted story moment and stays vanilla.
STORY_MILESTONES: tuple[StoryMilestone, ...] = (
    StoryMilestone(1, "electro_bolt", WELCOME_TO_RAPTURE, "Inject Electro Bolt"),
    StoryMilestone(2, "ryans_ambush", WELCOME_TO_RAPTURE, "Survive Ryan's Ambush"),  # at the Neptune's Bounty gate
    StoryMilestone(3, "ice_wall", MEDICAL_PAVILION, "Melt the Ice to Dental Services"),  # not the Twilight Fields door
    StoryMilestone(4, "surgery_wreckage", MEDICAL_PAVILION, "Clear the Way to Surgery"),  # bomb thrown with Telekinesis
    StoryMilestone(5, "steinman", MEDICAL_PAVILION, "Defeat Dr. Steinman"),
    StoryMilestone(6, "spider_photos", NEPTUNES_BOUNTY, "Photograph the Spider Splicers"),  # all three
    StoryMilestone(7, "peach_wilkins", NEPTUNES_BOUNTY, "Defeat Peach Wilkins"),  # the way on opens when he dies
    StoryMilestone(8, "submarine_bay", SMUGGLERS_HIDEOUT, "Open the Submarine Bay"),
    StoryMilestone(9, "rosa_gallica", ARCADIA, "Bring Langford the Rosa Gallica"),
    StoryMilestone(10, "langfords_safe", ARCADIA, "Open Langford's Safe"),  # the Lazarus Vector formula
    StoryMilestone(11, "chlorophyll", ARCADIA, "Collect the Chlorophyll Solution"),  # all seven, around Arcadia
    StoryMilestone(12, "lazarus_vector", ARCADIA, "Release the Lazarus Vector"),
    StoryMilestone(13, "defend_lab", ARCADIA, "Defend Langford's Lab"),
    StoryMilestone(14, "distilled_water", FARMERS_MARKET, "Collect the Distilled Water"),  # all seven, Worley Winery
    StoryMilestone(15, "enzyme_samples", FARMERS_MARKET, "Collect the Enzyme Samples"),  # all seven, Silverwing Apiary
    StoryMilestone(16, "fitzpatrick", FORT_FROLIC, "Photograph Kyle Fitzpatrick"),
    StoryMilestone(17, "finnegan", FORT_FROLIC, "Photograph Martin Finnegan"),
    StoryMilestone(18, "cobb", FORT_FROLIC, "Photograph Silas Cobb"),
    StoryMilestone(19, "rodriguez", FORT_FROLIC, "Photograph Hector Rodriguez"),
    StoryMilestone(20, "masterpiece", FORT_FROLIC, "Complete Cohen's Masterpiece"),
    StoryMilestone(21, "nitroglycerin", HEPHAESTUS, "Find the Nitroglycerin"),  # Kyburz's office
    StoryMilestone(22, "emp_bomb", HEPHAESTUS, "Assemble the EMP Bomb"),
    StoryMilestone(23, "core_overload", HEPHAESTUS, "Overload the Core"),
    StoryMilestone(24, "andrew_ryan", RAPTURE_CENTRAL_CONTROL, "Confront Andrew Ryan"),
    StoryMilestone(25, "self_destruct", RAPTURE_CENTRAL_CONTROL, "Stop the Self-Destruct"),
    StoryMilestone(26, "lot_192_first", OLYMPUS_HEIGHTS, "Take the First Dose of Lot 192"),  # Fontaine's home
    StoryMilestone(27, "lot_192_second", APOLLO_SQUARE, "Take the Second Dose of Lot 192"),  # Suchong's Free Clinic
    StoryMilestone(28, "voice_box", POINT_PROMETHEUS, "Get the Big Daddy Voice Box"),  # Live Subject Testing
    StoryMilestone(29, "pheromones", POINT_PROMETHEUS, "Collect the Big Daddy Pheromones"),  # all three, Little Wonders
    StoryMilestone(30, "bodysuit", POINT_PROMETHEUS, "Find the Big Daddy Bodysuit"),  # Failsafe Armored Escorts
    StoryMilestone(31, "helmet", POINT_PROMETHEUS, "Find the Big Daddy Helmet"),  # Candidate Induction
    StoryMilestone(32, "boots", POINT_PROMETHEUS, "Find the Big Daddy Boots"),  # Mendel Memorial Research Library
    StoryMilestone(33, "escort", PROVING_GROUNDS, "Escort the Little Sister"),
)


def audio_diary_location_name(diary: AudioDiary) -> str:
    return f"{diary.level} - Audio Diary: {diary.title}"


def little_sister_location_name(level: str, index: int) -> str:
    return f"{level} - Little Sister {index}"


def directors_commentary_location_name(level: str) -> str:
    return f"{level} - Director's Commentary Reel"


def power_to_the_people_location_name(level: str, index: int) -> str:
    return f"{level} - Power to the People {index}"


def level_completion_location_name(level: str) -> str:
    return f"{level} - Level Complete"


def story_location_name(milestone: StoryMilestone) -> str:
    return f"{milestone.level} - Story: {milestone.title}"


class LocationData(NamedTuple):
    name: str
    id: int
    level: str
    category: str  # one of the LocationCategory constants below
    missable: bool  # True when the check can be permanently missed (no backtracking)


class LocationCategory:
    AUDIO_DIARY = "Audio Diaries"
    LITTLE_SISTER = "Little Sisters"
    DIRECTORS_COMMENTARY = "Director's Commentary"
    POWER_TO_THE_PEOPLE = "Power to the People"
    LEVEL_COMPLETION = "Level Completion"
    STORY = "Story"

    ALL = (AUDIO_DIARY, LITTLE_SISTER, DIRECTORS_COMMENTARY, POWER_TO_THE_PEOPLE, LEVEL_COMPLETION, STORY)


def _build_location_table() -> tuple[LocationData, ...]:
    table: list[LocationData] = []

    for diary in AUDIO_DIARIES:
        missable = not LEVEL_BY_NAME[diary.level].revisitable
        table.append(LocationData(audio_diary_location_name(diary), 1000 + diary.number, diary.level,
                                  LocationCategory.AUDIO_DIARY, missable))

    sister_id = 2000
    for level in LEVEL_NAMES:
        for index in range(1, LITTLE_SISTERS_PER_LEVEL.get(level, 0) + 1):
            sister_id += 1
            table.append(LocationData(little_sister_location_name(level, index), sister_id, level,
                                      LocationCategory.LITTLE_SISTER, False))

    for offset, level in enumerate(DIRECTORS_COMMENTARY_LEVELS, start=1):
        missable = not LEVEL_BY_NAME[level].revisitable
        table.append(LocationData(directors_commentary_location_name(level), 3000 + offset, level,
                                  LocationCategory.DIRECTORS_COMMENTARY, missable))

    station_id = 4000
    for level in LEVEL_NAMES:
        for index in range(1, POWER_TO_THE_PEOPLE_PER_LEVEL.get(level, 0) + 1):
            station_id += 1
            table.append(LocationData(power_to_the_people_location_name(level, index), station_id, level,
                                      LocationCategory.POWER_TO_THE_PEOPLE, False))

    for level in LEVEL_COMPLETION_LEVELS:
        table.append(LocationData(level_completion_location_name(level), 5001 + LEVEL_NAMES.index(level), level,
                                  LocationCategory.LEVEL_COMPLETION, False))

    # 6000-6999 is kept for world items and containers.
    for milestone in STORY_MILESTONES:
        table.append(LocationData(story_location_name(milestone), 7000 + milestone.number, milestone.level,
                                  LocationCategory.STORY, False))

    return tuple(table)


LOCATION_TABLE: tuple[LocationData, ...] = _build_location_table()


# --------------------------------------------------------------------------------------------------------------------
# Items
# --------------------------------------------------------------------------------------------------------------------

class ItemKind:
    LEVEL_ACCESS = "Level Access"
    PLASMID = "Plasmids"
    TONIC = "Gene Tonics"
    WEAPON = "Weapons"
    WEAPON_UPGRADE = "Weapon Upgrades"
    CHARACTER_UPGRADE = "Character Upgrades"
    FILLER = "Filler"
    TRAP = "Traps"


class Source:
    """How the game hands an item out in vanilla. Only WORLD items conflict with shuffling (the game still gives
    you the pickup), so they stay out of the pool until the client can suppress vanilla pickups."""
    START = "start"  # you already have it
    WORLD = "world"  # free pickup / quest reward at a fixed spot
    GATHERERS_GARDEN = "gatherer's garden"  # bought with ADAM
    GIFT = "tenenbaum gift"  # teddy bear gift for rescuing Little Sisters
    RESEARCH = "research"  # Research Camera reward
    U_INVENT = "u-invent"  # crafted
    AP_ONLY = "archipelago"  # only exists as an Archipelago item


class PlasmidData(NamedTuple):
    item_name: str  # progressive when it has more than one level
    levels: tuple[tuple[str, str, str], ...]  # (in-game name, source, level where first available)


PLASMIDS: tuple[PlasmidData, ...] = (
    PlasmidData("Progressive Electro Bolt", (
        ("Electro Bolt", Source.WORLD, WELCOME_TO_RAPTURE),
        ("Electro Bolt 2", Source.GATHERERS_GARDEN, ARCADIA),
        ("Electro Bolt 3", Source.GATHERERS_GARDEN, OLYMPUS_HEIGHTS),
    )),
    PlasmidData("Progressive Incinerate!", (
        ("Incinerate!", Source.WORLD, MEDICAL_PAVILION),
        ("Incinerate! 2", Source.GATHERERS_GARDEN, FORT_FROLIC),
        ("Incinerate! 3", Source.GATHERERS_GARDEN, POINT_PROMETHEUS),
    )),
    PlasmidData("Telekinesis", (
        ("Telekinesis", Source.WORLD, MEDICAL_PAVILION),
    )),
    PlasmidData("Progressive Winter Blast", (
        ("Winter Blast", Source.GATHERERS_GARDEN, NEPTUNES_BOUNTY),
        ("Winter Blast 2", Source.GATHERERS_GARDEN, FORT_FROLIC),
        ("Winter Blast 3", Source.GATHERERS_GARDEN, OLYMPUS_HEIGHTS),
    )),
    PlasmidData("Progressive Insect Swarm", (
        ("Insect Swarm", Source.GATHERERS_GARDEN, FARMERS_MARKET),
        ("Insect Swarm 2", Source.GATHERERS_GARDEN, HEPHAESTUS),
        ("Insect Swarm 3", Source.GATHERERS_GARDEN, OLYMPUS_HEIGHTS),
    )),
    PlasmidData("Progressive Cyclone Trap", (
        ("Cyclone Trap", Source.GATHERERS_GARDEN, ARCADIA),
        ("Cyclone Trap 2", Source.GATHERERS_GARDEN, ARCADIA),
    )),
    PlasmidData("Enrage!", (
        ("Enrage!", Source.GATHERERS_GARDEN, MEDICAL_PAVILION),
    )),
    PlasmidData("Security Bullseye", (
        ("Security Bullseye", Source.WORLD, NEPTUNES_BOUNTY),
    )),
    PlasmidData("Target Dummy", (
        ("Target Dummy", Source.GATHERERS_GARDEN, NEPTUNES_BOUNTY),
    )),
    PlasmidData("Progressive Sonic Boom", (
        ("Sonic Boom", Source.GATHERERS_GARDEN, NEPTUNES_BOUNTY),
        ("Sonic Boom 2", Source.GATHERERS_GARDEN, FORT_FROLIC),
    )),
    PlasmidData("Progressive Hypnotize Big Daddy", (
        ("Hypnotize Big Daddy", Source.GIFT, NEPTUNES_BOUNTY),
        ("Hypnotize Big Daddy 2", Source.GIFT, FORT_FROLIC),
    )),
)


class TonicData(NamedTuple):
    name: str
    track: str  # "Physical", "Engineering" or "Combat"
    source: str
    level: str | None  # where it is first available (None for research rewards, which work anywhere)


PHYSICAL = "Physical"
ENGINEERING = "Engineering"
COMBAT = "Combat"

TONICS: tuple[TonicData, ...] = (
    # Combat (20)
    TonicData("Armored Shell", COMBAT, Source.GATHERERS_GARDEN, MEDICAL_PAVILION),
    TonicData("Armored Shell 2", COMBAT, Source.GIFT, OLYMPUS_HEIGHTS),
    TonicData("Damage Research", COMBAT, Source.WORLD, HEPHAESTUS),
    TonicData("Damage Research 2", COMBAT, Source.WORLD, POINT_PROMETHEUS),
    TonicData("Electric Flesh", COMBAT, Source.WORLD, FORT_FROLIC),
    TonicData("Electric Flesh 2", COMBAT, Source.WORLD, OLYMPUS_HEIGHTS),
    TonicData("Frozen Field", COMBAT, Source.WORLD, FORT_FROLIC),
    TonicData("Frozen Field 2", COMBAT, Source.WORLD, HEPHAESTUS),
    TonicData("Human Inferno", COMBAT, Source.GATHERERS_GARDEN, ARCADIA),
    TonicData("Human Inferno 2", COMBAT, Source.GATHERERS_GARDEN, POINT_PROMETHEUS),
    TonicData("Machine Buster", COMBAT, Source.GATHERERS_GARDEN, NEPTUNES_BOUNTY),
    TonicData("Machine Buster 2", COMBAT, Source.GATHERERS_GARDEN, OLYMPUS_HEIGHTS),
    TonicData("Photographer's Eye", COMBAT, Source.WORLD, FARMERS_MARKET),
    TonicData("Photographer's Eye 2", COMBAT, Source.RESEARCH, None),
    TonicData("Static Discharge", COMBAT, Source.WORLD, MEDICAL_PAVILION),
    TonicData("Static Discharge 2", COMBAT, Source.RESEARCH, None),
    TonicData("Wrench Jockey", COMBAT, Source.WORLD, MEDICAL_PAVILION),
    TonicData("Wrench Jockey 2", COMBAT, Source.RESEARCH, None),
    TonicData("Wrench Lurker", COMBAT, Source.WORLD, NEPTUNES_BOUNTY),
    TonicData("Wrench Lurker 2", COMBAT, Source.GATHERERS_GARDEN, OLYMPUS_HEIGHTS),
    # Engineering (18)
    TonicData("Alarm Expert", ENGINEERING, Source.WORLD, FORT_FROLIC),
    TonicData("Alarm Expert 2", ENGINEERING, Source.WORLD, POINT_PROMETHEUS),
    TonicData("Clever Inventor", ENGINEERING, Source.WORLD, OLYMPUS_HEIGHTS),
    TonicData("Focused Hacker", ENGINEERING, Source.WORLD, NEPTUNES_BOUNTY),
    TonicData("Focused Hacker 2", ENGINEERING, Source.WORLD, APOLLO_SQUARE),
    TonicData("Hacking Expert", ENGINEERING, Source.WORLD, ARCADIA),
    TonicData("Hacking Expert 2", ENGINEERING, Source.GATHERERS_GARDEN, FORT_FROLIC),
    TonicData("Prolific Inventor", ENGINEERING, Source.GIFT, APOLLO_SQUARE),
    TonicData("Safecracker", ENGINEERING, Source.GIFT, ARCADIA),
    TonicData("Safecracker 2", ENGINEERING, Source.WORLD, POINT_PROMETHEUS),
    TonicData("Security Expert", ENGINEERING, Source.WORLD, MEDICAL_PAVILION),
    TonicData("Security Expert 2", ENGINEERING, Source.RESEARCH, None),
    TonicData("Shorten Alarms", ENGINEERING, Source.WORLD, NEPTUNES_BOUNTY),
    TonicData("Shorten Alarms 2", ENGINEERING, Source.WORLD, HEPHAESTUS),
    TonicData("Speedy Hacker", ENGINEERING, Source.WORLD, MEDICAL_PAVILION),
    TonicData("Speedy Hacker 2", ENGINEERING, Source.GATHERERS_GARDEN, OLYMPUS_HEIGHTS),
    TonicData("Vending Expert", ENGINEERING, Source.GATHERERS_GARDEN, ARCADIA),
    TonicData("Vending Expert 2", ENGINEERING, Source.GATHERERS_GARDEN, HEPHAESTUS),
    # Physical (20)
    TonicData("Bloodlust", PHYSICAL, Source.U_INVENT, FORT_FROLIC),
    TonicData("Booze Hound", PHYSICAL, Source.U_INVENT, FORT_FROLIC),
    TonicData("EVE Link", PHYSICAL, Source.GATHERERS_GARDEN, MEDICAL_PAVILION),
    TonicData("EVE Link 2", PHYSICAL, Source.WORLD, FARMERS_MARKET),
    TonicData("EVE Saver", PHYSICAL, Source.GATHERERS_GARDEN, ARCADIA),
    TonicData("Extra Nutrition", PHYSICAL, Source.GATHERERS_GARDEN, NEPTUNES_BOUNTY),
    TonicData("Extra Nutrition 2", PHYSICAL, Source.WORLD, FORT_FROLIC),
    TonicData("Extra Nutrition 3", PHYSICAL, Source.RESEARCH, None),
    TonicData("Hacker's Delight", PHYSICAL, Source.WORLD, MEDICAL_PAVILION),
    TonicData("Hacker's Delight 2", PHYSICAL, Source.U_INVENT, FORT_FROLIC),
    TonicData("Hacker's Delight 3", PHYSICAL, Source.WORLD, POINT_PROMETHEUS),
    TonicData("Medical Expert", PHYSICAL, Source.WORLD, NEPTUNES_BOUNTY),
    TonicData("Medical Expert 2", PHYSICAL, Source.WORLD, FORT_FROLIC),
    TonicData("Medical Expert 3", PHYSICAL, Source.WORLD, APOLLO_SQUARE),
    TonicData("Natural Camouflage", PHYSICAL, Source.RESEARCH, None),
    TonicData("Scrounger", PHYSICAL, Source.RESEARCH, None),
    TonicData("Security Evasion", PHYSICAL, Source.WORLD, ARCADIA),
    TonicData("Security Evasion 2", PHYSICAL, Source.WORLD, HEPHAESTUS),
    TonicData("SportBoost", PHYSICAL, Source.RESEARCH, None),
    TonicData("SportBoost 2", PHYSICAL, Source.RESEARCH, None),
)

# Weapons and their Power to the People upgrades. Reserved for a later phase (world pickup shuffle).
WEAPONS: tuple[tuple[str, str], ...] = (
    ("Pistol", WELCOME_TO_RAPTURE),
    ("Machine Gun", MEDICAL_PAVILION),
    ("Shotgun", MEDICAL_PAVILION),
    ("Grenade Launcher", NEPTUNES_BOUNTY),
    ("Research Camera", NEPTUNES_BOUNTY),
    ("Chemical Thrower", ARCADIA),
    ("Crossbow", FORT_FROLIC),
)
WEAPON_UPGRADES: tuple[str, ...] = (
    "Pistol Clip Size",
    "Pistol Damage Increase",
    "Machine Gun Damage Increase",
    "Machine Gun Kickback Reduction",
    "Shotgun Rate of Fire",
    "Shotgun Damage Increase",
    "Grenade Launcher Damage Increase",
    "Grenade Launcher Damage Immunity",
    "Chemical Thrower Consumption Rate",
    "Chemical Thrower Range",
    "Crossbow Breakage Chance",
    "Crossbow Damage Increase",
)

# (name, copies in the pool). Vanilla counts are bought at Gatherer's Gardens.
CHARACTER_UPGRADES: tuple[tuple[str, int], ...] = (
    ("Health Upgrade", 8),
    ("EVE Upgrade", 8),
    ("Plasmid Slot", 4),
    ("Physical Tonic Slot", 4),
    ("Engineering Tonic Slot", 4),
    ("Combat Tonic Slot", 4),
)

# (name, relative weight when rolling filler)
FILLER_ITEMS: tuple[tuple[str, int], ...] = (
    ("10 ADAM", 8),
    ("25 ADAM", 4),
    ("$25", 8),
    ("$50", 6),
    ("$100", 2),
    ("First Aid Kit", 10),
    ("EVE Hypo", 10),
    ("Auto-Hack Tool", 5),
    ("Ammo Bundle", 10),
    ("Film", 2),
    ("Invention Components", 4),
)

TRAP_ITEMS: tuple[tuple[str, int], ...] = (
    ("EVE Drain Trap", 4),  # EVE drops to zero
    ("Pickpocket Trap", 3),  # lose half your dollars
    ("Security Alarm Trap", 3),  # sets off a security alarm (needs the DLL client phase)
)


class ItemData(NamedTuple):
    name: str
    id: int
    kind: str
    track: str | None = None  # tonic track, for tonics only


def _build_item_table() -> tuple[ItemData, ...]:
    table: list[ItemData] = []
    table += [ItemData(name, 100 + i, ItemKind.LEVEL_ACCESS) for i, name in enumerate(ACCESS_ITEMS)]
    table += [ItemData(plasmid.item_name, 200 + i, ItemKind.PLASMID) for i, plasmid in enumerate(PLASMIDS)]
    table += [ItemData(tonic.name, 300 + i, ItemKind.TONIC, tonic.track) for i, tonic in enumerate(TONICS)]
    table += [ItemData(name, 400 + i, ItemKind.WEAPON) for i, (name, _) in enumerate(WEAPONS)]
    table += [ItemData(name, 450 + i, ItemKind.WEAPON_UPGRADE) for i, name in enumerate(WEAPON_UPGRADES)]
    table += [ItemData(name, 500 + i, ItemKind.CHARACTER_UPGRADE) for i, (name, _) in enumerate(CHARACTER_UPGRADES)]
    table += [ItemData(name, 900 + i, ItemKind.FILLER) for i, (name, _) in enumerate(FILLER_ITEMS)]
    table += [ItemData(name, 950 + i, ItemKind.TRAP) for i, (name, _) in enumerate(TRAP_ITEMS)]
    return tuple(table)


ITEM_TABLE: tuple[ItemData, ...] = _build_item_table()

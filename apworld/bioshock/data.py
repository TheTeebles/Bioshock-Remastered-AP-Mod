"""
Static game data for BioShock (BioShock Remastered, PC).

Everything the APWorld knows about the game lives here, so the item/location/region code stays generic
and the game client can import the exact same tables to map in-game events to Archipelago IDs.

ID scheme (stable once released, never reuse an ID for something else):
    Locations                            Items
    1001-1122  Audio Diaries             100-107  Level Access
    2001-2021  Little Sisters            200-210  Plasmids
    3001-3010  (retired: film reels)     300-357  Gene Tonics
    4001-4012  Power to the People       400-406  Weapons            (reserved, not shuffled yet)
    5001-5012  Level Completion          450-461  Weapon Upgrades    (reserved, not shuffled yet)
    6001-6999  World items, containers   500-505  Character Upgrades
               (reserved, not used yet)  900-910  Filler
    7001-7033  Story                     950-952  Traps
    8001-8999  Gatherer's Garden: 8000 + 50 * level index + line

Data sources are listed in docs/en_BioShock.md. Values marked "verify" in the design doc should be
confirmed in-game while building the client.
"""
from __future__ import annotations

from typing import NamedTuple

GAME_NAME = "BioShock"
WORLD_VERSION = "0.1.52"  # the apworld's version, also in archipelago.json; the client says which it is


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

# The game's seven chapters (the number its map names start with). Open Rapture gates travel by chapter: the
# levels of a chapter depend on each other's story, so they open together. Chapter 1 always comes first; 2 to 4 can
# be played in any order; 5 (Ryan) needs 2 to 4 behind it, and 6 and 7 follow it in order.
class Chapter(NamedTuple):
    number: int
    levels: tuple[str, ...]
    access_item: str | None  # the item that opens the chapter in Open Rapture


CHAPTERS: tuple[Chapter, ...] = (
    Chapter(1, (WELCOME_TO_RAPTURE, MEDICAL_PAVILION), None),
    Chapter(2, (NEPTUNES_BOUNTY, SMUGGLERS_HIDEOUT), NEPTUNES_BOUNTY_ACCESS),
    Chapter(3, (ARCADIA, FARMERS_MARKET), ARCADIA_ACCESS),
    Chapter(4, (FORT_FROLIC,), FORT_FROLIC_ACCESS),
    Chapter(5, (HEPHAESTUS, RAPTURE_CENTRAL_CONTROL), HEPHAESTUS_ACCESS),
    Chapter(6, (OLYMPUS_HEIGHTS, APOLLO_SQUARE), OLYMPUS_HEIGHTS_ACCESS),
    Chapter(7, (POINT_PROMETHEUS, PROVING_GROUNDS), POINT_PROMETHEUS_ACCESS),
)
CHAPTER_OF_LEVEL: dict[str, Chapter] = {level: chapter for chapter in CHAPTERS for level in chapter.levels}
FREE_CHAPTERS: tuple[int, ...] = (2, 3, 4)  # the chapters before Ryan, in any order
CHAPTER_ACCESS_ITEMS: tuple[str, ...] = tuple(chapter.access_item for chapter in CHAPTERS if chapter.access_item)
HUB = "Bathysphere"  # the region the bathysphere network is, once Medical Pavilion is behind the player

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

# The Remastered "Director's Commentary" film reels are not checks: a reel unlocks its film for the whole PC, not
# the save, and the game seems to leave out the reels a player has found before (0.1.7). Their IDs stay unused.

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
# are shuffled.
# Numbers 1, 2, 3, 7 and 25 (Inject Electro Bolt, Survive Ryan's Ambush, Melt the Ice to Dental Services, Defeat Peach
# Wilkins, Stop the Self-Destruct) were taken out in 0.1.7: the game keeps no quest for them that the client can
# read. Their IDs stay unused.
STORY_MILESTONES: tuple[StoryMilestone, ...] = (
    StoryMilestone(4, "surgery_wreckage", MEDICAL_PAVILION, "Clear the Way to Surgery"),  # bomb thrown with Telekinesis
    StoryMilestone(5, "steinman", MEDICAL_PAVILION, "Defeat Dr. Steinman"),
    StoryMilestone(6, "spider_photos", NEPTUNES_BOUNTY, "Photograph the Spider Splicers"),  # all three
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


# What each level's Gatherer's Garden sells, in the order of its stock list (VendingLootSpec). Every line can be
# bought once (the Plasmid Slot sometimes twice), and each line is one check. Each level has one Garden. Read from
# the map files with client-poc/map_reader/map_stock.py on 2026-10-14 (notes/logs/map_stock_2026-10-14.json); the
# Neptune's Bounty list matches the probe's read of the running game (2026-10-13).
GATHERERS_GARDEN_STOCK: dict[str, tuple[str, ...]] = {
    MEDICAL_PAVILION: (  # 5 lines
        "Enrage!", "Armored Shell", "EVE Link", "Health Upgrade", "EVE Upgrade",
    ),
    NEPTUNES_BOUNTY: (  # 16 lines
        "Plasmid Slot", "Health Upgrade", "EVE Upgrade", "Enrage!", "Sonic Boom", "Target Dummy", "Winter Blast",
        "Armored Shell", "EVE Link", "Extra Nutrition", "Machine Buster", "Hacker's Delight", "Security Expert",
        "Speedy Hacker", "Wrench Jockey", "Static Discharge",
    ),
    ARCADIA: (  # 29 lines
        "Plasmid Slot", "Physical Tonic Slot", "Engineering Tonic Slot", "Combat Tonic Slot", "Health Upgrade",
        "EVE Upgrade", "Enrage!", "Sonic Boom", "Target Dummy", "Winter Blast", "Electro Bolt 2", "Cyclone Trap",
        "Security Bullseye", "Human Inferno", "EVE Saver", "Vending Expert", "Medical Expert", "Focused Hacker",
        "Shorten Alarms", "Wrench Lurker", "Armored Shell", "EVE Link", "Extra Nutrition", "Machine Buster",
        "Hacker's Delight", "Security Expert", "Speedy Hacker", "Wrench Jockey", "Static Discharge",
    ),
    FARMERS_MARKET: (  # 22 lines
        "Plasmid Slot", "Physical Tonic Slot", "Engineering Tonic Slot", "Combat Tonic Slot", "Insect Swarm",
        "Sonic Boom", "Human Inferno", "EVE Saver", "Vending Expert", "Medical Expert", "Focused Hacker",
        "Shorten Alarms", "Wrench Lurker", "Armored Shell", "EVE Link", "Extra Nutrition", "Machine Buster",
        "Hacker's Delight", "Security Expert", "Speedy Hacker", "Wrench Jockey", "Static Discharge",
    ),
    FORT_FROLIC: (  # 34 lines
        "Plasmid Slot", "Physical Tonic Slot", "Engineering Tonic Slot", "Combat Tonic Slot", "Health Upgrade",
        "EVE Upgrade", "Enrage!", "Insect Swarm 2", "Sonic Boom 2", "Target Dummy", "Winter Blast 2",
        "Electro Bolt 2", "Cyclone Trap", "Security Bullseye", "Incinerate! 2", "Hacking Expert 2",
        "Security Evasion", "Photographer's Eye", "Human Inferno", "EVE Saver", "Vending Expert", "Medical Expert",
        "Focused Hacker", "Shorten Alarms", "Wrench Lurker", "Armored Shell", "EVE Link 2", "Extra Nutrition",
        "Machine Buster", "Hacker's Delight", "Security Expert", "Speedy Hacker", "Wrench Jockey", "Static Discharge",
    ),
    HEPHAESTUS: (  # 39 lines
        "Plasmid Slot", "Physical Tonic Slot", "Engineering Tonic Slot", "Combat Tonic Slot", "Health Upgrade",
        "EVE Upgrade", "Enrage!", "Target Dummy", "Winter Blast 2", "Insect Swarm 2", "Cyclone Trap 2",
        "Security Bullseye", "Sonic Boom 2", "Electro Bolt 2", "Electro Bolt 3", "Incinerate! 2", "Alarm Expert",
        "Frozen Field", "Electric Flesh", "Hacking Expert 2", "Security Evasion", "Photographer's Eye",
        "Human Inferno", "EVE Saver", "Vending Expert", "Vending Expert 2", "Medical Expert 2", "Focused Hacker",
        "Shorten Alarms", "Wrench Lurker 2", "Armored Shell", "EVE Link 2", "Extra Nutrition 2", "Machine Buster",
        "Hacker's Delight", "Security Expert", "Speedy Hacker", "Wrench Jockey", "Static Discharge",
    ),
    OLYMPUS_HEIGHTS: (  # 41 lines
        "Plasmid Slot", "Physical Tonic Slot", "Engineering Tonic Slot", "Combat Tonic Slot", "Health Upgrade",
        "EVE Upgrade", "Enrage!", "Target Dummy", "Winter Blast 3", "Sonic Boom 2", "Insect Swarm 3",
        "Electro Bolt 3", "Incinerate! 2", "Incinerate! 3", "Cyclone Trap 2", "Security Bullseye", "Damage Research",
        "Alarm Expert", "Frozen Field 2", "Electric Flesh", "Hacking Expert 2", "Security Evasion 2",
        "Photographer's Eye", "Human Inferno", "EVE Saver", "Vending Expert", "Vending Expert 2", "Medical Expert 2",
        "Focused Hacker", "Shorten Alarms 2", "Wrench Lurker 2", "Armored Shell", "EVE Link 2", "Extra Nutrition 2",
        "Machine Buster", "Machine Buster 2", "Hacker's Delight", "Security Expert", "Speedy Hacker 2",
        "Wrench Jockey", "Static Discharge",
    ),
    APOLLO_SQUARE: (  # 39 lines
        "Plasmid Slot", "Physical Tonic Slot", "Engineering Tonic Slot", "Combat Tonic Slot", "Enrage!",
        "Target Dummy", "Winter Blast 3", "Sonic Boom 2", "Insect Swarm 3", "Electro Bolt 3", "Incinerate! 2",
        "Incinerate! 3", "Cyclone Trap 2", "Security Bullseye", "Damage Research", "Alarm Expert", "Frozen Field 2",
        "Electric Flesh", "Hacking Expert 2", "Security Evasion 2", "Photographer's Eye", "Human Inferno",
        "EVE Saver", "Vending Expert", "Vending Expert 2", "Medical Expert 2", "Focused Hacker", "Shorten Alarms 2",
        "Wrench Lurker 2", "Armored Shell", "EVE Link 2", "Extra Nutrition 2", "Machine Buster", "Machine Buster 2",
        "Hacker's Delight", "Security Expert", "Speedy Hacker 2", "Wrench Jockey", "Static Discharge",
    ),
    POINT_PROMETHEUS: (  # 42 lines
        "Plasmid Slot", "Physical Tonic Slot", "Engineering Tonic Slot", "Combat Tonic Slot", "Health Upgrade",
        "EVE Upgrade", "Enrage!", "Target Dummy", "Winter Blast 3", "Sonic Boom 2", "Insect Swarm", "Insect Swarm 3",
        "Electro Bolt 3", "Cyclone Trap 2", "Security Bullseye", "Incinerate! 3", "Clever Inventor",
        "Damage Research", "Alarm Expert", "Frozen Field 2", "Electric Flesh 2", "Hacking Expert 2",
        "Security Evasion 2", "Photographer's Eye", "Human Inferno 2", "EVE Saver", "Vending Expert",
        "Vending Expert 2", "Medical Expert 3", "Focused Hacker 2", "Shorten Alarms 2", "Wrench Lurker 2",
        "Armored Shell", "EVE Link 2", "Extra Nutrition 2", "Machine Buster", "Machine Buster 2", "Hacker's Delight",
        "Security Expert", "Speedy Hacker 2", "Wrench Jockey", "Static Discharge",
    ),
    PROVING_GROUNDS: (  # 6 lines
        "Plasmid Slot", "Physical Tonic Slot", "Engineering Tonic Slot", "Combat Tonic Slot", "Health Upgrade",
        "EVE Upgrade",
    ),
}
GATHERERS_GARDEN_LINES_PER_LEVEL = 50  # room in the location IDs for each level's lines


def gatherers_garden_location_name(level: str, vanilla_item: str) -> str:
    return f"{level} - Gatherer's Garden: {vanilla_item}"


def power_to_the_people_location_name(level: str, index: int) -> str:
    return f"{level} - Power to the People {index}"


def level_completion_location_name(level: str) -> str:
    return f"{level} - Level Complete"


def story_location_name(milestone: StoryMilestone) -> str:
    return f"{milestone.level} - Story: {milestone.title}"


# --------------------------------------------------------------------------------------------------------------------
# Things lying in the levels: plasmid and tonic pickups, weapons and safes
# --------------------------------------------------------------------------------------------------------------------

class SpotKind:
    PLASMID = "plasmid"  # an ActivePlasmidPickup: the check is taking it
    TONIC = "tonic"  # a Physical, Engineering or Combat (Weapons) PlasmidPickup: the same
    WEAPON = "weapon"  # the check is holding the weapon for the first time, wherever it came from
    SAFE = "safe"  # a SecurityCrate_Safe or _WallSafe: the check is opening it (its contents stay as they are)


class WorldSpot(NamedTuple):
    id: int
    kind: str  # a SpotKind
    level: str
    map: str  # the map file it lies in, the way the client's normalize_map spells it
    actor: str | None  # its actor's name in that map (None for weapons)
    vanilla: str | None  # the plasmid, tonic or weapon it gives (None for safes)


# Read from the map files with client-poc/map_reader/world_items.py on 2026-10-14
# (notes/logs/map_survey_2026-10-14.json).
# Weapons are listed once, at the level where the maps first place one. The Pistol and the Shotgun are given by
# scripts earlier (Welcome to Rapture and Medical Pavilion), which only means their check may come in early and wait
# for its level's access item, like any check found before its level is unlocked.
WORLD_SPOTS: tuple[WorldSpot, ...] = (
    WorldSpot(6001, SpotKind.PLASMID, "Welcome to Rapture", "1-welcome", "ActivePlasmidPickup1", "Electro Bolt"),
    WorldSpot(6002, SpotKind.PLASMID, "Medical Pavilion", "1-medical", "ActivePlasmidPickup0", "Telekinesis"),
    WorldSpot(6003, SpotKind.PLASMID, "Medical Pavilion", "1-medical", "ActivePlasmidPickup2", "Incinerate!"),
    WorldSpot(6004, SpotKind.PLASMID, "Neptune's Bounty", "2-fisheries", "ActivePlasmidPickup0", "Security Bullseye"),
    WorldSpot(6011, SpotKind.TONIC, "Medical Pavilion", "1-medical", "EngineeringPlasmidPickup0", "Security Expert"),
    WorldSpot(6012, SpotKind.TONIC, "Medical Pavilion", "1-medical", "PhysicalPlasmidPickup0", "Hacker's Delight"),
    WorldSpot(6013, SpotKind.TONIC, "Medical Pavilion", "1-medical", "WeaponsPlasmidPickup1", "Wrench Jockey"),
    WorldSpot(6014, SpotKind.TONIC, "Medical Pavilion", "1-medical", "WeaponsPlasmidPickup5", "Static Discharge"),
    WorldSpot(6015, SpotKind.TONIC, "Medical Pavilion", "1-medical", "EngineeringPlasmidPickup2", "Speedy Hacker"),
    WorldSpot(6016, SpotKind.TONIC, "Neptune's Bounty", "2-fisheries", "EngineeringPlasmidPickup1", "Focused Hacker"),
    WorldSpot(6017, SpotKind.TONIC, "Neptune's Bounty", "2-fisheries", "PhysicalPlasmidPickup0", "Medical Expert"),
    WorldSpot(6018, SpotKind.TONIC, "Neptune's Bounty", "2-fisheries", "EngineeringPlasmidPickup0", "Shorten Alarms"),
    WorldSpot(6019, SpotKind.TONIC, "Arcadia", "3-arcadia", "EngineeringPlasmidPickup1", "Hacking Expert"),
    WorldSpot(6020, SpotKind.TONIC, "Farmer's Market", "3-market", "PhysicalPlasmidPickup0", "EVE Link 2"),
    WorldSpot(6021, SpotKind.TONIC, "Farmer's Market", "3-market", "WeaponsPlasmidPickup4", "Photographer's Eye"),
    WorldSpot(6022, SpotKind.TONIC, "Fort Frolic", "4-recreation", "WeaponsPlasmidPickup2", "Electric Flesh"),
    WorldSpot(6023, SpotKind.TONIC, "Fort Frolic", "4-recreation", "PhysicalPlasmidPickup2", "Extra Nutrition 2"),
    WorldSpot(6024, SpotKind.TONIC, "Fort Frolic", "4-recreation", "PhysicalPlasmidPickup1", "Medical Expert 2"),
    WorldSpot(6025, SpotKind.TONIC, "Fort Frolic", "4-recreation", "EngineeringPlasmidPickup0", "Alarm Expert"),
    WorldSpot(6026, SpotKind.TONIC, "Hephaestus", "5-hephaestus", "WeaponsPlasmidPickup3", "Frozen Field 2"),
    WorldSpot(6027, SpotKind.TONIC, "Hephaestus", "5-hephaestus", "EngineeringPlasmidPickup0", "Shorten Alarms 2"),
    WorldSpot(6028, SpotKind.TONIC, "Hephaestus", "5-hephaestus", "PhysicalPlasmidPickup0", "Security Evasion 2"),
    WorldSpot(6029, SpotKind.TONIC, "Hephaestus", "5-hephaestus", "WeaponsPlasmidPickup0", "Damage Research"),
    WorldSpot(6030, SpotKind.TONIC, "Olympus Heights", "6-resi", "EngineeringPlasmidPickup0", "Clever Inventor"),
    WorldSpot(6031, SpotKind.TONIC, "Olympus Heights", "6-resi", "WeaponsPlasmidPickup1", "Electric Flesh 2"),
    WorldSpot(6032, SpotKind.TONIC, "Apollo Square", "6-slums", "EngineeringPlasmidPickup0", "Focused Hacker 2"),
    WorldSpot(6033, SpotKind.TONIC, "Apollo Square", "6-slums", "PhysicalPlasmidPickup0", "Medical Expert 3"),
    WorldSpot(6034, SpotKind.TONIC, "Point Prometheus", "7-science", "EngineeringPlasmidPickup3", "Safecracker 2"),
    WorldSpot(6035, SpotKind.TONIC, "Point Prometheus", "7-science", "WeaponsPlasmidPickup0", "Damage Research 2"),
    WorldSpot(6036, SpotKind.TONIC, "Point Prometheus", "7-science", "PhysicalPlasmidPickup0", "Hacker's Delight 3"),
    WorldSpot(6037, SpotKind.TONIC, "Point Prometheus", "7-science", "EngineeringPlasmidPickup2", "Alarm Expert 2"),
    WorldSpot(6051, SpotKind.WEAPON, "Medical Pavilion", "1-medical", None, "Machine Gun"),
    WorldSpot(6052, SpotKind.WEAPON, "Neptune's Bounty", "2-fisheries", None, "Shotgun"),
    WorldSpot(6053, SpotKind.WEAPON, "Neptune's Bounty", "2-fisheries", None, "Research Camera"),
    WorldSpot(6054, SpotKind.WEAPON, "Neptune's Bounty", "2-fisheries", None, "Pistol"),
    WorldSpot(6055, SpotKind.WEAPON, "Neptune's Bounty", "2-fisheries", None, "Grenade Launcher"),
    WorldSpot(6056, SpotKind.WEAPON, "Arcadia", "3-arcadia", None, "Chemical Thrower"),
    WorldSpot(6057, SpotKind.WEAPON, "Hephaestus", "5-hephaestus", None, "Crossbow"),
    WorldSpot(6101, SpotKind.SAFE, "Medical Pavilion", "1-medical", "SecurityCrate_Safe0", None),
    WorldSpot(6102, SpotKind.SAFE, "Medical Pavilion", "1-medical", "SecurityCrate_Safe3", None),
    WorldSpot(6103, SpotKind.SAFE, "Neptune's Bounty", "2-fisheries", "SecurityCrate_Safe2", None),
    WorldSpot(6105, SpotKind.SAFE, "Neptune's Bounty", "2-fisheries", "SecurityCrate_Safe1", None),
    WorldSpot(6106, SpotKind.SAFE, "Neptune's Bounty", "2-fisheries", "SecurityCrate_Safe3", None),
    WorldSpot(6107, SpotKind.SAFE, "Neptune's Bounty", "2-fisheries", "SecurityCrate_Safe0", None),
    WorldSpot(6108, SpotKind.SAFE, "Arcadia", "3-arcadia", "SecurityCrate_Safe3", None),
    WorldSpot(6111, SpotKind.SAFE, "Arcadia", "3-arcadia", "SecurityCrate_Safe2", None),
    WorldSpot(6117, SpotKind.SAFE, "Arcadia", "3-arcadia", "SecurityCrate_Safe0", None),
    WorldSpot(6118, SpotKind.SAFE, "Farmer's Market", "3-market", "SecurityCrate_Safe3", None),
    WorldSpot(6123, SpotKind.SAFE, "Farmer's Market", "3-market", "SecurityCrate_Safe1", None),
    WorldSpot(6129, SpotKind.SAFE, "Farmer's Market", "3-market", "SecurityCrate_Safe2", None),
    WorldSpot(6131, SpotKind.SAFE, "Farmer's Market", "3-market", "SecurityCrate_Safe0", None),
    WorldSpot(6132, SpotKind.SAFE, "Fort Frolic", "4-recreation", "SecurityCrate_Safe1", None),
    WorldSpot(6133, SpotKind.SAFE, "Fort Frolic", "4-recreation", "SecurityCrate_Safe2", None),
    WorldSpot(6134, SpotKind.SAFE, "Fort Frolic", "4-recreation", "SecurityCrate_Safe0", None),
    WorldSpot(6135, SpotKind.SAFE, "Fort Frolic", "4-recreation", "SecurityCrate_Safe6", None),
    WorldSpot(6136, SpotKind.SAFE, "Fort Frolic", "4-recreation", "SecurityCrate_Safe3", None),
    WorldSpot(6137, SpotKind.SAFE, "Hephaestus", "5-hephaestus", "SecurityCrate_Safe3", None),
    WorldSpot(6138, SpotKind.SAFE, "Hephaestus", "5-hephaestus", "SecurityCrate_Safe1", None),
    WorldSpot(6139, SpotKind.SAFE, "Hephaestus", "5-hephaestus", "SecurityCrate_Safe0", None),
    WorldSpot(6140, SpotKind.SAFE, "Hephaestus", "5-hephaestus", "SecurityCrate_Safe2", None),
    WorldSpot(6148, SpotKind.SAFE, "Olympus Heights", "6-resi", "SecurityCrate_Safe3", None),
    WorldSpot(6149, SpotKind.SAFE, "Olympus Heights", "6-resi", "SecurityCrate_Safe0", None),
    WorldSpot(6150, SpotKind.SAFE, "Olympus Heights", "6-resi", "SecurityCrate_Safe4", None),
    WorldSpot(6151, SpotKind.SAFE, "Olympus Heights", "6-resi", "SecurityCrate_Safe1", None),
    WorldSpot(6154, SpotKind.SAFE, "Apollo Square", "6-slums", "SecurityCrate_Safe1", None),
    WorldSpot(6155, SpotKind.SAFE, "Apollo Square", "6-slums", "SecurityCrate_Safe0", None),
    WorldSpot(6157, SpotKind.SAFE, "Point Prometheus", "7-science", "SecurityCrate_Safe2", None),
    WorldSpot(6158, SpotKind.SAFE, "Point Prometheus", "7-science", "SecurityCrate_Safe1", None),
    WorldSpot(6159, SpotKind.SAFE, "Point Prometheus", "7-science", "SecurityCrate_Safe0", None),
    WorldSpot(6164, SpotKind.SAFE, "Proving Grounds", "7-gauntlet", "SecurityCrate_Safe2", None),
    # Safes set into walls (SecurityCrate_WallSafe), found with the probe on 2026-10-15 by the Telekinesis plasmid and
    # read from the map files the same day (notes/logs/map_survey_2026-10-15.json). After the others, so their IDs
    # and names (each level's safes counted on from its first ones) stay as they were.
    WorldSpot(6165, SpotKind.SAFE, "Medical Pavilion", "1-medical", "SecurityCrate_WallSafe1", None),
    WorldSpot(6166, SpotKind.SAFE, "Medical Pavilion", "1-medical", "SecurityCrate_WallSafe3", None),
    WorldSpot(6167, SpotKind.SAFE, "Medical Pavilion", "1-medical", "SecurityCrate_WallSafe0", None),
    WorldSpot(6168, SpotKind.SAFE, "Medical Pavilion", "1-medical", "SecurityCrate_WallSafe2", None),
    WorldSpot(6169, SpotKind.SAFE, "Fort Frolic", "4-recreation", "SecurityCrate_WallSafe5", None),
    WorldSpot(6170, SpotKind.SAFE, "Fort Frolic", "4-recreation", "SecurityCrate_WallSafe4", None),
    WorldSpot(6171, SpotKind.SAFE, "Fort Frolic", "4-recreation", "SecurityCrate_WallSafe3", None),
    WorldSpot(6172, SpotKind.SAFE, "Fort Frolic", "4-recreation", "SecurityCrate_WallSafe1", None),
    WorldSpot(6173, SpotKind.SAFE, "Fort Frolic", "4-recreation", "SecurityCrate_WallSafe0", None),
)

# Plasmids the story needs (Electro Bolt opens the way out of Welcome to Rapture, Incinerate! melts the ice in the
# Medical Pavilion). Their pickups stay checks, but always give their own plasmid as well.
KEPT_WORLD_PICKUPS: frozenset[str] = frozenset({"Electro Bolt", "Incinerate!"})

# Telekinesis throws the grenades that clear the debris in front of Surgery, where Steinman has the key to the
# bathysphere. With the pickups shuffled (Teebs, 2026-10-15) it is a progression item: everything past the debris,
# and every level after Medical Pavilion, needs it.
TELEKINESIS = "Telekinesis"
SURGERY = "Medical Pavilion: Surgery"  # the region past the debris

# The Medical Pavilion checks that logic puts past the debris, and so behind Telekinesis. The map files give the world
# items' positions (wall safe 5 lies east of the Telekinesis pickup, toward Surgery; a save of Teebs's, 2026-10-18,
# held the Machine Gun and had opened safe 6 before taking Telekinesis, so those two are before it); diaries, Little
# Sisters and the level's end have none, so they are all put past it to be safe. A check put here that is really
# before the debris only makes logic ask for Telekinesis a little early; the other way round could lock a seed.
PAST_THE_DEBRIS: frozenset[str] = frozenset({
    *(f"{MEDICAL_PAVILION} - Audio Diary: {diary.title}" for diary in AUDIO_DIARIES if diary.level == MEDICAL_PAVILION),
    f"{MEDICAL_PAVILION} - Little Sister 1", f"{MEDICAL_PAVILION} - Little Sister 2",
    f"{MEDICAL_PAVILION} - Level Complete",
    f"{MEDICAL_PAVILION} - Story: Clear the Way to Surgery", f"{MEDICAL_PAVILION} - Story: Defeat Dr. Steinman",
    f"{MEDICAL_PAVILION} - Safe 5",
    # Not past the debris, but out of reach without Telekinesis all the same: the wall safe in Chompers Dental
    # (SecurityCrate_WallSafe3, Teebs, 2026-10-19). Logic treats it like the checks past the debris.
    f"{MEDICAL_PAVILION} - Safe 4",
})


def world_spot_location_name(spot: WorldSpot, number: int = 0) -> str:
    """number: the spot's place among the level's safes, from 1."""
    if spot.kind == SpotKind.WEAPON:
        return f"{spot.level} - Weapon: {spot.vanilla}"
    if spot.kind == SpotKind.SAFE:
        return f"{spot.level} - {spot.kind.capitalize()} {number}"
    return f"{spot.level} - Pickup: {spot.vanilla}"


def shuffled_world_pickups() -> list[WorldSpot]:
    """The plasmid and tonic pickups whose item goes into the multiworld when World Item Checks is 'shuffled'."""
    return [spot for spot in WORLD_SPOTS
            if spot.kind in (SpotKind.PLASMID, SpotKind.TONIC) and spot.vanilla not in KEPT_WORLD_PICKUPS]


# --------------------------------------------------------------------------------------------------------------------
# Research: photographing enemies with the Research Camera
# --------------------------------------------------------------------------------------------------------------------

class ResearchSubject(NamedTuple):
    key: str  # the game's ResearchName
    name: str  # as the game shows it
    level: str  # a level where it is always found, to photograph it there
    awards: tuple[tuple[int, str], ...]  # (research level, the tonic it gives) in vanilla


RESEARCH_LEVELS = 5  # NUM_RESEARCH_LEVELS
# ShockPlayer's ResearchTrackData and ResearchLevels, from the exported ShockPlayer.uc (uploaded 2026-10-15). The
# levels are where each subject is surely found: Neptune's Bounty, where the camera lies and Atlas has you photograph
# Spider Splicers; Nitro and Houdini Splicers only from Hephaestus, to be safe.
RESEARCH_SUBJECTS: tuple[ResearchSubject, ...] = (
    ResearchSubject("MeleeThug", "Thuggish Splicer", NEPTUNES_BOUNTY, ((2, "SportBoost"), (4, "SportBoost 2"))),
    ResearchSubject("RangedAggressor", "Leadhead Splicer", NEPTUNES_BOUNTY,
                    ((2, "Scrounger"), (4, "Static Discharge 2"))),
    ResearchSubject("Grenadier", "Nitro Splicer", HEPHAESTUS, ()),
    ResearchSubject("CeilingCrawler", "Spider Splicer", NEPTUNES_BOUNTY, ((4, "Extra Nutrition 3"),)),
    ResearchSubject("Assassin", "Houdini Splicer", HEPHAESTUS, ((2, "Natural Camouflage"),)),
    ResearchSubject("Bouncer", "Bouncer", NEPTUNES_BOUNTY, ((2, "Wrench Jockey 2"),)),
    ResearchSubject("Rosie", "Rosie", NEPTUNES_BOUNTY, ((2, "Photographer's Eye 2"),)),
    ResearchSubject("SecurityBot", "Security Bot", NEPTUNES_BOUNTY, ((2, "Security Expert 2"),)),
    ResearchSubject("Gatherer", "Little Sister", NEPTUNES_BOUNTY, ()),
    ResearchSubject("Turret", "Turret", NEPTUNES_BOUNTY, ()),
    ResearchSubject("SecurityCamera", "Security Camera", NEPTUNES_BOUNTY, ()),
)
RESEARCH_AWARDS: tuple[str, ...] = tuple(tonic for subject in RESEARCH_SUBJECTS for _, tonic in subject.awards)


def research_location_id(subject_index: int, level: int) -> int:
    return 9000 + 10 * subject_index + level


def research_location_name(subject: ResearchSubject, level: int) -> str:
    return f"Research: {subject.name} {level}"


class LocationData(NamedTuple):
    name: str
    id: int
    level: str
    category: str  # one of the LocationCategory constants below
    missable: bool  # True when the check can be permanently missed (no backtracking)


class LocationCategory:
    AUDIO_DIARY = "Audio Diaries"
    LITTLE_SISTER = "Little Sisters"
    POWER_TO_THE_PEOPLE = "Power to the People"
    LEVEL_COMPLETION = "Level Completion"
    STORY = "Story"
    GATHERERS_GARDEN = "Gatherer's Garden"
    WORLD_ITEM = "World Items"
    SAFE = "Safes"
    RESEARCH = "Research"

    ALL = (AUDIO_DIARY, LITTLE_SISTER, POWER_TO_THE_PEOPLE, LEVEL_COMPLETION, STORY, GATHERERS_GARDEN, WORLD_ITEM,
           SAFE, RESEARCH)


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

    station_id = 4000
    for level in LEVEL_NAMES:
        for index in range(1, POWER_TO_THE_PEOPLE_PER_LEVEL.get(level, 0) + 1):
            station_id += 1
            table.append(LocationData(power_to_the_people_location_name(level, index), station_id, level,
                                      LocationCategory.POWER_TO_THE_PEOPLE, False))

    for level in LEVEL_COMPLETION_LEVELS:
        table.append(LocationData(level_completion_location_name(level), 5001 + LEVEL_NAMES.index(level), level,
                                  LocationCategory.LEVEL_COMPLETION, False))

    numbers: dict[tuple[str, str], int] = {}
    for spot in WORLD_SPOTS:
        number = numbers[spot.level, spot.kind] = numbers.get((spot.level, spot.kind), 0) + 1
        category = LocationCategory.SAFE if spot.kind == SpotKind.SAFE else \
            LocationCategory.WORLD_ITEM
        table.append(LocationData(world_spot_location_name(spot, number), spot.id, spot.level, category,
                                  not LEVEL_BY_NAME[spot.level].revisitable))

    for milestone in STORY_MILESTONES:
        table.append(LocationData(story_location_name(milestone), 7000 + milestone.number, milestone.level,
                                  LocationCategory.STORY, False))

    for level, stock in GATHERERS_GARDEN_STOCK.items():
        assert len(stock) < GATHERERS_GARDEN_LINES_PER_LEVEL and len(set(stock)) == len(stock), level
        for line, vanilla_item in enumerate(stock, start=1):
            table.append(LocationData(gatherers_garden_location_name(level, vanilla_item),
                                      8000 + GATHERERS_GARDEN_LINES_PER_LEVEL * LEVEL_NAMES.index(level) + line,
                                      level, LocationCategory.GATHERERS_GARDEN, False))

    for index, subject in enumerate(RESEARCH_SUBJECTS):
        for level in range(1, RESEARCH_LEVELS + 1):
            table.append(LocationData(research_location_name(subject, level), research_location_id(index, level),
                                      subject.level, LocationCategory.RESEARCH, False))

    return tuple(table)


LOCATION_TABLE: tuple[LocationData, ...] = _build_location_table()


def _first_garden_appearances() -> frozenset[int]:
    """The Garden lines that sell something no earlier level's Garden sells, in story order: 61 of the 273."""
    seen: set[str] = set()
    first = set()
    for level in LEVEL_NAMES:
        for line, vanilla_item in enumerate(GATHERERS_GARDEN_STOCK.get(level, ()), start=1):
            if vanilla_item not in seen:
                seen.add(vanilla_item)
                first.add(8000 + GATHERERS_GARDEN_LINES_PER_LEVEL * LEVEL_NAMES.index(level) + line)
    return frozenset(first)


# With Shop Checks set to first appearances, only these Garden lines are checks.
GATHERERS_GARDEN_FIRST_APPEARANCES: frozenset[int] = _first_garden_appearances()


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

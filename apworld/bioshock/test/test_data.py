import json
import pkgutil
import unittest
from collections import Counter

from .. import world
from ..client import game_data
from ..data import (
    ACCESS_ITEMS, AUDIO_DIARIES, DIRECTORS_COMMENTARY_LEVELS, ITEM_TABLE, LEVEL_NAMES, LITTLE_SISTERS_PER_LEVEL,
    LOCATION_TABLE, PLASMIDS, POWER_TO_THE_PEOPLE_PER_LEVEL, STORY_MILESTONES, TONICS, LocationCategory,
)


class TestGameData(unittest.TestCase):
    def test_audio_diaries(self) -> None:
        self.assertEqual([diary.number for diary in AUDIO_DIARIES], list(range(1, 123)))
        titles = [diary.title for diary in AUDIO_DIARIES]
        self.assertEqual(len(titles), len(set(titles)), "audio diary titles must be unique")
        per_level = Counter(diary.level for diary in AUDIO_DIARIES)
        self.assertEqual(per_level, Counter({
            "Welcome to Rapture": 2, "Medical Pavilion": 17, "Neptune's Bounty": 19, "Smuggler's Hideout": 3,
            "Arcadia": 17, "Farmer's Market": 8, "Fort Frolic": 15, "Hephaestus": 17,
            "Rapture Central Control": 3, "Olympus Heights": 6, "Apollo Square": 6, "Point Prometheus": 9,
        }))

    def test_collectible_totals(self) -> None:
        self.assertEqual(sum(LITTLE_SISTERS_PER_LEVEL.values()), 21)
        self.assertEqual(len(DIRECTORS_COMMENTARY_LEVELS), 10)
        self.assertEqual(sum(POWER_TO_THE_PEOPLE_PER_LEVEL.values()), 12)
        for level in (*LITTLE_SISTERS_PER_LEVEL, *DIRECTORS_COMMENTARY_LEVELS, *POWER_TO_THE_PEOPLE_PER_LEVEL):
            self.assertIn(level, LEVEL_NAMES)

    def test_location_table(self) -> None:
        self.assertEqual(Counter(location.category for location in LOCATION_TABLE), Counter({
            LocationCategory.AUDIO_DIARY: 122,
            LocationCategory.LITTLE_SISTER: 21,
            LocationCategory.DIRECTORS_COMMENTARY: 10,
            LocationCategory.POWER_TO_THE_PEOPLE: 12,
            LocationCategory.LEVEL_COMPLETION: 12,
            LocationCategory.STORY: 33,
        }))
        names = [location.name for location in LOCATION_TABLE]
        ids = [location.id for location in LOCATION_TABLE]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(ids), len(set(ids)))
        diary_ids = {location.id for location in LOCATION_TABLE if location.category == LocationCategory.AUDIO_DIARY}
        self.assertEqual(diary_ids, set(range(1001, 1123)))
        self.assertEqual({location.id for location in LOCATION_TABLE if location.category == LocationCategory.STORY},
                         set(range(7001, 7034)))

    def test_story_milestones(self) -> None:
        keys = [milestone.key for milestone in STORY_MILESTONES]
        self.assertEqual(len(keys), len(set(keys)))
        levels = [LEVEL_NAMES.index(milestone.level) for milestone in STORY_MILESTONES]
        self.assertEqual(levels, sorted(levels), "grouped by level, levels in story order")
        self.assertEqual(set(LEVEL_NAMES), {milestone.level for milestone in STORY_MILESTONES},
                         "every level has at least one")
        story = [location for location in LOCATION_TABLE if location.category == LocationCategory.STORY]
        self.assertFalse([location.name for location in story if location.missable], "a story step cannot be missed")

    def test_story_location_ids_never_change(self) -> None:
        """Location IDs are what a generated seed and the server know a check by. A new milestone gets the next free
        number in data.py and a new line here; nothing below may ever be edited."""
        names = {location.id: location.name for location in LOCATION_TABLE
                 if location.category == LocationCategory.STORY}
        self.assertEqual(names, {
            7001: "Welcome to Rapture - Story: Inject Electro Bolt",
            7002: "Welcome to Rapture - Story: Survive Ryan's Ambush",
            7003: "Medical Pavilion - Story: Melt the Ice to Dental Services",
            7004: "Medical Pavilion - Story: Clear the Way to Surgery",
            7005: "Medical Pavilion - Story: Defeat Dr. Steinman",
            7006: "Neptune's Bounty - Story: Photograph the Spider Splicers",
            7007: "Neptune's Bounty - Story: Defeat Peach Wilkins",
            7008: "Smuggler's Hideout - Story: Open the Submarine Bay",
            7009: "Arcadia - Story: Bring Langford the Rosa Gallica",
            7010: "Arcadia - Story: Open Langford's Safe",
            7011: "Arcadia - Story: Collect the Chlorophyll Solution",
            7012: "Arcadia - Story: Release the Lazarus Vector",
            7013: "Arcadia - Story: Defend Langford's Lab",
            7014: "Farmer's Market - Story: Collect the Distilled Water",
            7015: "Farmer's Market - Story: Collect the Enzyme Samples",
            7016: "Fort Frolic - Story: Photograph Kyle Fitzpatrick",
            7017: "Fort Frolic - Story: Photograph Martin Finnegan",
            7018: "Fort Frolic - Story: Photograph Silas Cobb",
            7019: "Fort Frolic - Story: Photograph Hector Rodriguez",
            7020: "Fort Frolic - Story: Complete Cohen's Masterpiece",
            7021: "Hephaestus - Story: Find the Nitroglycerin",
            7022: "Hephaestus - Story: Assemble the EMP Bomb",
            7023: "Hephaestus - Story: Overload the Core",
            7024: "Rapture Central Control - Story: Confront Andrew Ryan",
            7025: "Rapture Central Control - Story: Stop the Self-Destruct",
            7026: "Olympus Heights - Story: Take the First Dose of Lot 192",
            7027: "Apollo Square - Story: Take the Second Dose of Lot 192",
            7028: "Point Prometheus - Story: Get the Big Daddy Voice Box",
            7029: "Point Prometheus - Story: Collect the Big Daddy Pheromones",
            7030: "Point Prometheus - Story: Find the Big Daddy Bodysuit",
            7031: "Point Prometheus - Story: Find the Big Daddy Helmet",
            7032: "Point Prometheus - Story: Find the Big Daddy Boots",
            7033: "Proving Grounds - Story: Escort the Little Sister",
        })
        self.assertEqual(game_data.MILESTONE_LOCATION["peach_wilkins"], 7007, "the agent's key for it")
        self.assertEqual(len(game_data.MILESTONE_LOCATION), 33)

    def test_only_welcome_to_rapture_is_missable(self) -> None:
        missable = {location.name for location in LOCATION_TABLE if location.missable}
        self.assertEqual(missable, {
            "Welcome to Rapture - Audio Diary: New Year's Eve Alone",
            "Welcome to Rapture - Audio Diary: Hole in Bathroom Wall",
            "Welcome to Rapture - Director's Commentary Reel",
        })

    def test_item_table(self) -> None:
        names = [item.name for item in ITEM_TABLE]
        ids = [item.id for item in ITEM_TABLE]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ACCESS_ITEMS), 8)
        self.assertEqual(sum(len(plasmid.levels) for plasmid in PLASMIDS), 22)
        self.assertEqual(Counter(tonic.track for tonic in TONICS),
                         Counter({"Combat": 20, "Engineering": 18, "Physical": 20}))

    def test_manifest_matches_the_world(self) -> None:
        manifest = json.loads(pkgutil.get_data(world.__name__, "archipelago.json"))
        self.assertEqual(manifest["game"], world.BioShockWorld.game)
        self.assertEqual(manifest["world_version"], world.WORLD_VERSION, "bump both when releasing")

"""World Item Checks and Safe Checks: the plasmids, tonics and weapons lying in the levels, and the safes."""
from __future__ import annotations

import unittest
from collections import Counter
from typing import Any

from Options import OptionError

from ..client import game_data
from ..client.frida_agent import state_from_agent
from ..data import (
    PAST_THE_DEBRIS,
    KEPT_WORLD_PICKUPS, LOCATION_TABLE, WORLD_SPOTS, LocationCategory, SpotKind, shuffled_world_pickups,
)
from .. import locations
from .bases import BioShockTestBase
from .client_harness import Harness, loc

WORLD_ITEM_LOCATIONS = {location.name for location in LOCATION_TABLE if location.category == LocationCategory.WORLD_ITEM}
SAFE_LOCATIONS = {location.name for location in LOCATION_TABLE if location.category == LocationCategory.SAFE}
CAMERA = "Neptune's Bounty - Weapon: Research Camera"


class TestTable(unittest.TestCase):
    def test_spots(self) -> None:
        self.assertEqual(Counter(spot.kind for spot in WORLD_SPOTS), Counter(
            {SpotKind.PLASMID: 4, SpotKind.TONIC: 27, SpotKind.WEAPON: 7, SpotKind.SAFE: 41}))
        self.assertEqual(len({(spot.map, spot.actor) for spot in WORLD_SPOTS if spot.actor}), 72, "one actor, one spot")
        for spot in WORLD_SPOTS:
            self.assertEqual(game_data.level_of(spot.map, None), spot.level, spot)
            self.assertEqual(game_data.normalize_map(spot.map), spot.map, spot)
        self.assertEqual(sorted(game_data.WEAPON_LOCATION), sorted(
            game_class.rsplit(".", 1)[-1] for game_class in game_data.WEAPON_CLASS.values()))

    def test_names(self) -> None:
        self.assertIn("Medical Pavilion - Pickup: Telekinesis", WORLD_ITEM_LOCATIONS)
        self.assertIn("Arcadia - Weapon: Chemical Thrower", WORLD_ITEM_LOCATIONS)
        self.assertIn("Neptune's Bounty - Safe 1", SAFE_LOCATIONS)
        self.assertFalse(any("Lockbox" in name for name in SAFE_LOCATIONS), "lockboxes are out of scope (Teebs, 2026-10-15)")

    def test_kept_pickups_are_the_story_plasmids(self) -> None:
        shuffled = {spot.vanilla for spot in shuffled_world_pickups()}
        self.assertEqual(len(shuffled), 29)
        self.assertIn("Telekinesis", shuffled)
        self.assertFalse(shuffled & KEPT_WORLD_PICKUPS)
        self.assertEqual(sum(len(actors) for actors in game_data.SHUFFLED_PICKUPS.values()), 29)


class TestOff(BioShockTestBase):
    def test_nothing_by_default(self) -> None:
        names = {location.name for location in self.multiworld.get_locations(self.player)}
        self.assertFalse(names & (WORLD_ITEM_LOCATIONS | SAFE_LOCATIONS))
        self.assertEqual(self.world.fill_slot_data()["world_item_checks"], 0)
        self.assertEqual(self.world.fill_slot_data()["safe_checks"], 0)


class TestKeepItems(BioShockTestBase):
    options = {"world_item_checks": "keep_items", "safe_checks": True}

    def test_locations(self) -> None:
        names = {location.name for location in self.multiworld.get_locations(self.player)}
        self.assertEqual(names & WORLD_ITEM_LOCATIONS, WORLD_ITEM_LOCATIONS - {CAMERA},
                         "the camera is no check for a player who starts with it")
        self.assertEqual(names & SAFE_LOCATIONS, SAFE_LOCATIONS)
        slot_data = self.world.fill_slot_data()
        self.assertEqual((slot_data["world_item_checks"], slot_data["safe_checks"]), (1, 1))

    def test_pickups_stay_out_of_the_pool(self) -> None:
        pool = Counter(item.name for item in self.multiworld.itempool)
        self.assertEqual(pool["Telekinesis"], 0)
        self.assertEqual(pool["Wrench Jockey"], 0)

    def test_welcome_to_rapture_pickup_holds_filler(self) -> None:
        location = self.multiworld.get_location("Welcome to Rapture - Pickup: Electro Bolt", self.player)
        self.assertEqual(location.progress_type.name, "EXCLUDED")


class TestCameraFound(BioShockTestBase):
    options = {"world_item_checks": "keep_items", "start_with_research_camera": False}

    def test_camera_is_a_check(self) -> None:
        self.assertTrue(self.can_reach_location("Medical Pavilion - Weapon: Machine Gun"))
        names = {location.name for location in self.multiworld.get_locations(self.player)}
        self.assertIn(CAMERA, names)


class TestShuffled(BioShockTestBase):
    options = {"world_item_checks": "shuffled"}

    def test_shuffled_pickups_are_in_the_pool(self) -> None:
        pool = Counter(item.name for item in self.multiworld.itempool)
        self.assertEqual(pool["Telekinesis"], 1)
        self.assertTrue(self.get_item_by_name("Telekinesis").advancement, "the debris before Surgery needs it")
        self.assertEqual(pool["Security Bullseye"], 1)
        self.assertEqual(pool["Wrench Jockey"], 1)
        self.assertEqual(pool["Hacker's Delight 3"], 1)
        self.assertEqual(pool["Progressive Electro Bolt"], 2, "its pickup still gives the first level")
        self.assertEqual(pool["Progressive Incinerate!"], 2)
        self.assertEqual(self.world.fill_slot_data()["world_item_checks"], 2)

    def test_telekinesis_opens_surgery_and_the_way_out(self) -> None:
        self.collect_by_name("Neptune's Bounty Access")
        self.assertTrue(self.can_reach_location("Medical Pavilion - Pickup: Telekinesis"))
        self.assertTrue(self.can_reach_location("Medical Pavilion - Pickup: Wrench Jockey"))
        self.assertTrue(self.can_reach_location("Medical Pavilion - Weapon: Machine Gun"))
        for name in ("Medical Pavilion - Story: Defeat Dr. Steinman", "Neptune's Bounty - Pickup: Security Bullseye"):
            self.assertFalse(self.can_reach_location(name), name)
        self.collect_by_name("Telekinesis")
        for name in ("Medical Pavilion - Story: Defeat Dr. Steinman", "Neptune's Bounty - Pickup: Security Bullseye"):
            self.assertTrue(self.can_reach_location(name), name)

    def test_past_the_debris_names_real_checks(self) -> None:
        self.assertLessEqual(PAST_THE_DEBRIS, {location.name for location in LOCATION_TABLE})
        self.assertEqual(len(PAST_THE_DEBRIS), 24)


class TestShuffledOpenRapture(BioShockTestBase):
    options = {"world_item_checks": "shuffled", "level_access": "open_rapture"}

    def test_the_hub_needs_telekinesis(self) -> None:
        self.collect_by_name(["Neptune's Bounty Access"])
        self.assertFalse(self.can_reach_location("Neptune's Bounty - Pickup: Security Bullseye"))
        self.collect_by_name("Telekinesis")
        self.assertTrue(self.can_reach_location("Neptune's Bounty - Pickup: Security Bullseye"))


class TestKeptTelekinesisNeedsNothing(BioShockTestBase):
    options = {"world_item_checks": "keep_items"}

    def test_surgery_is_open(self) -> None:
        self.assertTrue(self.can_reach_location("Medical Pavilion - Story: Defeat Dr. Steinman"))


class TestRoom(BioShockTestBase):
    def generate(self, **options: Any) -> None:
        self.options = options
        self.world_setup()

    def test_shuffled_pickups_need_room(self) -> None:
        only_pickups = dict(level_access="vanilla", world_item_checks="shuffled", story_checks=False,
                            little_sister_checks=False, power_to_the_people_checks=False,
                            level_completion_checks=False, audio_diary_checks="off")
        self.generate(**only_pickups)  # 36 open checks for 28 items and Telekinesis
        pickups = sorted(name for name in WORLD_ITEM_LOCATIONS if "Pickup:" in name and "Welcome" not in name)
        with self.assertRaises(OptionError) as raised:
            self.generate(**only_pickups, exclude_locations=pickups[:9])
        self.assertIn("28 items come only from the multiworld", str(raised.exception))
        self.assertIn("leave room for 26", str(raised.exception))


class TestUniversalTracker(BioShockTestBase):
    def test_options_come_back_from_slot_data(self) -> None:
        slot_data = dict(self.world.fill_slot_data(), world_item_checks=2, safe_checks=1, research_checks=1)
        self.multiworld.re_gen_passthrough = {"BioShock": slot_data}
        self.world.generate_early()
        counts = Counter(location.category for location in locations.enabled_locations(self.world))
        self.assertEqual(counts[LocationCategory.WORLD_ITEM], 37)  # the camera is started with
        self.assertEqual(counts[LocationCategory.SAFE], 41)
        self.assertEqual(counts[LocationCategory.RESEARCH], 11)


class TestClient(unittest.TestCase):
    def harness(self, world_item_checks: int = 1) -> Harness:
        h = Harness(access_items=False)
        h.slot_data["world_item_checks"] = world_item_checks
        h.slot_data["safe_checks"] = 1
        return h.connect().attach()

    def test_pickups_safes_and_weapons_send_checks(self) -> None:
        h = self.harness()
        h.game.reports_weapons = True
        h.step(4)
        for name in ("Medical Pavilion - Pickup: Telekinesis", "Neptune's Bounty - Safe 1",
                     "Medical Pavilion - Weapon: Machine Gun"):
            self.assertNotIn(loc(name), h.sent_checks)
            h.game.take_from_world(name)
            h.step(4)
            self.assertIn(loc(name), h.sent_checks, name)

    def test_shuffled_pickups_are_emptied(self) -> None:
        h = self.harness(world_item_checks=2)
        h.step(4)
        self.assertEqual(h.game.empty_pickups, {"placeholder": "chips",
                                                "maps": {name: list(actors)
                                                         for name, actors in game_data.SHUFFLED_PICKUPS.items()}})
        self.assertEqual(h.game.take_from_world("Medical Pavilion - Pickup: Wrench Jockey"), "nothing")
        self.assertEqual(h.game.take_from_world("Medical Pavilion - Pickup: Incinerate!"), "Incinerate!")
        self.assertEqual(h.game.take_from_world("Medical Pavilion - Pickup: Telekinesis"), "nothing")
        h.step(4)
        self.assertIn(loc("Medical Pavilion - Pickup: Wrench Jockey"), h.sent_checks)

    def test_kept_pickups_are_not_emptied(self) -> None:
        h = self.harness(world_item_checks=1)
        h.step(4)
        self.assertIsNone(h.game.empty_pickups)
        self.assertEqual(h.game.take_from_world("Medical Pavilion - Pickup: Telekinesis"), "Telekinesis")

    def test_a_game_that_cannot_empty_them_says_so(self) -> None:
        h = Harness(access_items=False)
        h.slot_data["world_item_checks"] = 2
        h.game.unsupported_actions.add("empty_pickups")
        h.connect().attach()
        h.step(4)
        self.assertTrue(any("still give themselves" in text for text in h.warnings()), h.warnings())

    def test_sim_take_a_weapon_sends_its_check(self) -> None:
        h = self.harness()
        h.step(4)
        h.game.take_from_world("Medical Pavilion - Weapon: Machine Gun")
        h.step(4)
        self.assertIn(loc("Medical Pavilion - Weapon: Machine Gun"), h.sent_checks)

    def test_sim_take(self) -> None:
        h = self.harness()
        h.game.travel("Neptune's Bounty")
        self.assertEqual(h.game.take_here("safe 1"), "the safe's contents (Neptune's Bounty - Safe 1)")
        self.assertIn("Security Bullseye", h.game.take_here("bullseye"))
        with self.assertRaises(ValueError):
            h.game.take_here("safe")  # several
        h.game.travel("Welcome to Rapture")
        self.assertIn("Electro Bolt", h.game.take_here("electro bolt"))

    def test_agent_state(self) -> None:
        state = state_from_agent({
            "map": "1-Medical", "pickupsTaken": {"1-Medical": ["ActivePlasmidPickup0"]},
            "pickupsHere": {"map": "1-Medical", "names": ["ActivePlasmidPickup2"]},
            "containersOpened": {"2-Fisheries_int": ["SecurityCrate_Safe2", 7]},
        })
        self.assertEqual(state.pickups_taken, {"1-medical": frozenset({"ActivePlasmidPickup0"})})
        self.assertEqual(state.pickups_here, ("1-medical", frozenset({"ActivePlasmidPickup2"})))
        self.assertEqual(state.containers_opened, {"2-fisheries": frozenset({"SecurityCrate_Safe2"})})
        self.assertIsNone(state_from_agent({"map": "1-Medical"}).pickups_here)
        self.assertEqual(state_from_agent({"researchLevels": {"ThuggishSplicer": 2, "Bouncer": "x", "Rosie": 1065353216,
                                                              "Turret": -1}}).research, {"ThuggishSplicer": 2},
                         "a level the game cannot have is a misread")

    def test_missing_pickups_count_as_taken(self) -> None:
        medical = {actor for (map_name, actor) in game_data.PICKUP_LOCATION if map_name == "1-medical"}
        self.assertEqual(len(medical), 7)
        lying = frozenset(sorted(medical)[1:])
        self.assertEqual(game_data.pickups_missing(("1-medical", lying)),
                         {game_data.PICKUP_LOCATION["1-medical", sorted(medical)[0]]})
        # A map whose pickups the agent sees none of by their names says nothing: it may not read them right.
        self.assertEqual(game_data.pickups_missing(("1-medical", frozenset({"SomethingElse0"}))), set())
        self.assertEqual(game_data.pickups_missing(("1-medical", frozenset())), set())
        self.assertEqual(game_data.pickups_missing(None), set())


if __name__ == "__main__":
    unittest.main()


class TestWallSafes(unittest.TestCase):
    def test_wall_safes_come_after_the_first_safes_and_keep_their_names(self) -> None:
        medical = [spot for spot in WORLD_SPOTS if spot.kind == SpotKind.SAFE and spot.map == "1-medical"]
        self.assertEqual([spot.actor for spot in medical], [
            "SecurityCrate_Safe0", "SecurityCrate_Safe3", "SecurityCrate_WallSafe1", "SecurityCrate_WallSafe3",
            "SecurityCrate_WallSafe0", "SecurityCrate_WallSafe2"])
        self.assertEqual([spot.id for spot in medical], [6101, 6102, 6165, 6166, 6167, 6168])

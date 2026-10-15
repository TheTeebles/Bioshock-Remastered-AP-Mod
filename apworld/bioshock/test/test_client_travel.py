"""Open Rapture on the client side: where the bathysphere may go, the crossings sent back to the hub, and Level
Complete for levels played in any order. Run against the simulated game."""
import unittest

from ..client import game_data, travel
from ..client.core import OPEN_RAPTURE
from ..data import (
    ARCADIA_ACCESS, CHAPTER_OF_LEVEL, FORT_FROLIC_ACCESS, HEPHAESTUS_ACCESS, LEVELS, NEPTUNES_BOUNTY_ACCESS,
    OLYMPUS_HEIGHTS_ACCESS, POINT_PROMETHEUS_ACCESS,
)
from .client_harness import Harness

EVERY_CHAPTER = (NEPTUNES_BOUNTY_ACCESS, ARCADIA_ACCESS, FORT_FROLIC_ACCESS, HEPHAESTUS_ACCESS,
                 OLYMPUS_HEIGHTS_ACCESS, POINT_PROMETHEUS_ACCESS)


def open_rapture() -> Harness:
    h = Harness()
    h.slot_data["level_access"] = OPEN_RAPTURE
    h.slot_data["level_access_items"] = {level.name: CHAPTER_OF_LEVEL[level.name].access_item for level in LEVELS}
    return h


def stops(rules: dict) -> list[str]:
    return [game_data.MAP_LEVEL[stop.get("to", stop["map"])] for stop in rules["stops"]]


class TestTravelRules(unittest.TestCase):
    def test_with_nothing_held_the_bathysphere_only_goes_back_to_medical(self) -> None:
        rules = travel.travel_rules(set(), {"Welcome to Rapture", "Medical Pavilion"})
        self.assertEqual(stops(rules), ["Medical Pavilion"])
        self.assertEqual([(r["from"], r["to"]) for r in rules["redirects"]],
                         [("2-subbay", "3-arcadia"), ("5-ryan", "6-resi")])
        self.assertTrue(all((r["map"], r["arrival"]) == ("1-medical", "Backtrack_MedicalStart")
                            for r in rules["redirects"]))

    def test_chapters_two_to_four_open_with_their_own_item(self) -> None:
        rules = travel.travel_rules({NEPTUNES_BOUNTY_ACCESS, FORT_FROLIC_ACCESS}, set())
        self.assertEqual(stops(rules), ["Medical Pavilion", "Neptune's Bounty", "Fort Frolic"])

    def test_hephaestus_needs_the_three_chapters_before_it(self) -> None:
        held = {HEPHAESTUS_ACCESS, NEPTUNES_BOUNTY_ACCESS, ARCADIA_ACCESS}
        self.assertNotIn("Hephaestus", stops(travel.travel_rules(held, set())))
        self.assertIn("Hephaestus", stops(travel.travel_rules(held | {FORT_FROLIC_ACCESS}, set())))

    def test_a_first_ride_to_arcadia_docks_where_the_submarine_does(self) -> None:
        def arrival(visited: set[str]) -> str:
            rules = travel.travel_rules({ARCADIA_ACCESS}, visited)
            return next(stop["arrival"] for stop in rules["stops"] if stop["map"] == "3-arcadia")

        self.assertEqual(arrival(set()), "FromFisheriesPS")
        self.assertEqual(arrival({"Arcadia"}), "ArcBathyEndPS")

    def test_the_arcadia_crossing_opens_with_arcadia_access(self) -> None:
        rules = travel.travel_rules({ARCADIA_ACCESS}, set())
        self.assertEqual([r["to"] for r in rules["redirects"]], ["6-resi"])

    def test_chapters_six_and_seven_open_with_their_items_once_the_chapters_before_are_open(self) -> None:
        held = set(EVERY_CHAPTER)
        self.assertEqual(stops(travel.travel_rules(held - {HEPHAESTUS_ACCESS}, set()))[-2:], ["Arcadia", "Fort Frolic"],
                         "Ryan's chapter, and so the two after it, need Hephaestus Access")
        self.assertEqual(stops(travel.travel_rules(held - {OLYMPUS_HEIGHTS_ACCESS}, set()))[-1], "Hephaestus")
        self.assertEqual(stops(travel.travel_rules(held, set()))[-2:], ["Olympus Heights", "Point Prometheus"])

    def test_olympus_heights_is_offered_on_apollo_squares_line_until_reached(self) -> None:
        held = set(EVERY_CHAPTER)
        line = next(s for s in travel.travel_rules(held, set())["stops"] if s["map"] == "6-slums")
        self.assertEqual(line, {"map": "6-slums", "arrival": "PlayerStart_First", "to": "6-resi",
                                "title": "Olympus Heights"})
        line = next(s for s in travel.travel_rules(held, {"Olympus Heights"})["stops"] if s["map"] == "6-slums")
        self.assertEqual(line, {"map": "6-slums", "arrival": "PlayerStart_FromDeck7Bathy"})

    def test_every_stop_and_crossing_names_a_map_the_client_knows(self) -> None:
        for stop in travel.STOPS:
            self.assertEqual(game_data.MAP_LEVEL[stop.map], stop.level)
        for crossing in travel.CROSSINGS:
            self.assertIn(crossing.from_map, game_data.MAP_LEVEL)
            self.assertIn(crossing.to_map, game_data.MAP_LEVEL)


class TestOpenRaptureCompletion(unittest.TestCase):
    def test_levels_in_a_chapter_complete_as_the_next_one_is_reached(self) -> None:
        self.assertEqual(travel.completed_levels({"Neptune's Bounty"}, ()), ["Medical Pavilion"])
        self.assertIn("Neptune's Bounty", travel.completed_levels({"Neptune's Bounty", "Smuggler's Hideout"}, ()))
        self.assertIn("Welcome to Rapture", travel.completed_levels({"Medical Pavilion"}, ()))

    def test_the_last_level_of_a_chapter_completes_with_its_closing_quest(self) -> None:
        self.assertNotIn("Fort Frolic", travel.completed_levels({"Fort Frolic", "Hephaestus"}, ()))
        self.assertIn("Fort Frolic", travel.completed_levels({"Fort Frolic"}, {"ReplaceThreeMorePhotos"}))
        self.assertIn("Medical Pavilion", travel.completed_levels({"Medical Pavilion"}, {"QuarantineKey"}),
                      "Medical completes before any ride out, which its own check may be what opens")
        done = travel.completed_levels({"Arcadia", "Farmer's Market"}, {"DefendMPRAmbush"})
        self.assertIn("Arcadia", done)
        self.assertIn("Farmer's Market", done)
        self.assertNotIn("Arcadia", travel.completed_levels({"Arcadia", "Farmer's Market"}, ()),
                         "the story goes from Arcadia to Farmer's Market and back")


class TestOpenRaptureClient(unittest.TestCase):
    def test_the_agent_is_told_the_rules_and_told_again_when_they_change(self) -> None:
        h = open_rapture().connect().attach()
        h.game.travel("Medical Pavilion")
        h.step(3)
        self.assertEqual(h.game.bathysphere_stops(), ["Medical Pavilion"])
        h.receive(FORT_FROLIC_ACCESS)
        h.step(2)
        self.assertEqual(h.game.bathysphere_stops(), ["Medical Pavilion", "Fort Frolic"])
        h.game.ride("Fort Frolic")
        h.step(3)
        self.assertEqual(h.core.level, "Fort Frolic")

    def test_rules_are_sent_once_per_change(self) -> None:
        h = open_rapture().connect().attach()
        sent = []
        original = h.game.run_action
        h.game.run_action = lambda command_id, action: (sent.append(action), original(command_id, action))[1]
        h.step(5)
        self.assertEqual(len([a for a in sent if a.startswith("travel_rules ")]), 1)
        h.detach()
        h.attach()
        h.step(2)
        self.assertEqual(len([a for a in sent if a.startswith("travel_rules ")]), 2, "a new agent is told again")

    def test_no_rules_outside_open_rapture(self) -> None:
        h = Harness().connect().attach()
        h.step(5)
        self.assertIsNone(h.game.travel_rules)

    def test_a_crossing_into_a_locked_chapter_goes_to_the_hub_and_says_why(self) -> None:
        h = open_rapture().connect().attach()
        h.receive(NEPTUNES_BOUNTY_ACCESS)
        h.game.travel("Smuggler's Hideout")
        h.step(3)
        self.assertEqual(h.game.cross("Arcadia"), "Medical Pavilion")
        h.step(2)
        self.assertTrue(any("Arcadia is locked until you receive Arcadia Access" in w for w in h.warnings()))
        h.receive(ARCADIA_ACCESS)
        h.step(2)
        h.game.travel("Smuggler's Hideout")
        self.assertEqual(h.game.cross("Arcadia"), "Arcadia")

    def test_level_complete_follows_the_order_played(self) -> None:
        h = open_rapture().connect().attach()
        h.receive(*EVERY_CHAPTER)
        for level in ("Medical Pavilion", "Fort Frolic", "Neptune's Bounty"):
            h.game.travel(level)
            h.step(3)
        completed = {game_data.LOCATION_NAME[location] for location in h.sent_checks
                     if "Level Complete" in game_data.LOCATION_NAME[location]}
        self.assertEqual(completed, {"Welcome to Rapture - Level Complete", "Medical Pavilion - Level Complete"},
                         "riding past chapters finishes none of them")
        h.game.quests.add("ReplaceThreeMorePhotos")
        h.step(2)
        self.assertIn(game_data.LEVEL_COMPLETION_LOCATION["Fort Frolic"], h.sent_checks)

    def test_visited_levels_survive_a_restart(self) -> None:
        h = open_rapture().connect().attach()
        h.receive(ARCADIA_ACCESS)
        h.game.travel("Arcadia")
        h.step(3)
        self.assertEqual(h.storage["visited"], ["Arcadia"])
        h.restart_client()
        self.assertEqual(h.core.visited, {"Arcadia"})
        h.step(2)
        rules = h.game.travel_rules or {}
        self.assertEqual(next(s["arrival"] for s in rules["stops"] if s["map"] == "3-arcadia"), "ArcBathyEndPS")


class TestLockedWayOn(unittest.TestCase):
    def test_entering_a_level_whose_way_on_is_locked_says_so_once(self) -> None:
        h = open_rapture().connect().attach()
        h.receive(NEPTUNES_BOUNTY_ACCESS)
        h.game.travel("Smuggler's Hideout")
        h.step(5)
        said = [n.text for n in h.notices if "The way on to Arcadia is locked" in n.text]
        self.assertEqual(len(said), 1)

    def test_a_bounce_back_says_where_you_are(self) -> None:
        text = travel.blocked_notice("3-arcadia", "2-subbay", set())
        self.assertIn("you stay in Smuggler's Hideout", text)
        self.assertIn("took you back to Medical Pavilion", travel.blocked_notice("3-arcadia", "1-medical", set()))
        self.assertIsNone(travel.blocked_notice("3-arcadia", "1-medical", {ARCADIA_ACCESS}))

    def test_every_redirect_has_a_fallback_no_longer_than_the_crossing(self) -> None:
        arrivals = {"3-arcadia": "FromFisheriesPS", "6-resi": "PlayerStart_First"}
        for redirect in travel.travel_rules(set(), set())["redirects"]:
            fallback = redirect["fallback"]
            self.assertLessEqual(len(fallback["map"]), len(redirect["to"]))
            self.assertLessEqual(len(fallback["arrival"]), len(arrivals[redirect["to"]]))

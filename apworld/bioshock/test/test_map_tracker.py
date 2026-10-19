"""Tests for Universal Tracker's map tab: the files in tracker/, the functions it calls, and the client telling it where
the player is."""
import pkgutil
import unittest

from .. import map_tracker
from ..client.core import MAP_UPDATE_DISTANCE, MAP_UPDATE_SECONDS
from ..client.frida_agent import position_of
from ..client.protocol import GameState, Position, ShowOnMap
from ..data import LOCATION_TABLE, NEPTUNES_BOUNTY
from ..world import BioShockWorld
from .client_harness import Harness

FLOOR_1 = "Fisheries01Marker"  # Neptune's Bounty's first floor, where UpperHub is drawn
FLOOR_3 = "Fisheries03Marker"


class TestTrackerFiles(unittest.TestCase):
    def setUp(self) -> None:
        self.pages = map_tracker.pages()
        self.names = {page["name"] for page in self.pages}
        self.locations = map_tracker._read("locations/locations.json")

    def test_the_world_offers_the_map_tab(self) -> None:
        tab = BioShockWorld.tracker_world
        self.assertEqual(tab["map_page_folder"], "tracker")
        self.assertEqual(tab["map_page_setting_key"], "bioshock_map_{team}_{player}")
        self.assertIn(NEPTUNES_BOUNTY, [level for level, _ in tab["map_page_groups"]])

    def test_every_section_is_a_location_of_the_world(self) -> None:
        names = {location.name for location in LOCATION_TABLE}
        for pin in self.locations:
            for section in pin["sections"]:
                self.assertIn(section["name"], names, pin["name"])

    def test_every_pin_is_on_a_page_and_inside_its_picture(self) -> None:
        sizes = {page["name"]: picture_size(page["img"]) for page in self.pages}
        for pin in self.locations:
            self.assertTrue(pin["map_locations"], pin["name"])
            for spot in pin["map_locations"]:
                self.assertIn(spot["map"], self.names)
                width, height = sizes[spot["map"]]
                self.assertTrue(0 <= spot["x"] < width and 0 <= spot["y"] < height, (pin["name"], spot))

    def test_no_two_pins_share_a_spot(self) -> None:
        # Universal Tracker keeps one pin per position on a page, so a second one there would vanish.
        spots = [(spot["map"], spot["x"], spot["y"]) for pin in self.locations for spot in pin["map_locations"]]
        self.assertEqual(len(spots), len(set(spots)))

    def test_groups_name_existing_pages(self) -> None:
        for level, names in map_tracker._read("groups.json"):
            self.assertEqual(names[0], level, "the overview comes first")
            for name in names:
                self.assertIn(name, self.names)

    def test_every_floor_has_its_own_page(self) -> None:
        for floors in map_tracker.regions().values():
            for floor in set(floors.values()):
                self.assertGreaterEqual(map_tracker.page_of_floor(floor), 0, floor)


class TestPlayerOnTheMap(unittest.TestCase):
    def test_the_tab_turns_to_the_players_floor(self) -> None:
        page = map_tracker.pages()[map_tracker.page_of_floor(FLOOR_3)]
        self.assertEqual(page["name"], f"{NEPTUNES_BOUNTY}: Floor 3")
        for nothing in ("", None, "NoSuchMarker", 3, {"layer": FLOOR_3}):
            self.assertEqual(map_tracker.page_of_floor(nothing), -1)

    def test_the_dot_shows_on_the_floor_and_on_the_overview(self) -> None:
        position = {"layer": FLOOR_1, "x": -299, "y": -79}  # the Security Bullseye pickup
        pin = next(pin for pin in map_tracker._read("locations/locations.json")
                   if pin["sections"][0]["name"] == f"{NEPTUNES_BOUNTY} - Pickup: Security Bullseye")
        for spot in pin["map_locations"]:
            index = next(i for i, page in enumerate(map_tracker.pages()) if page["name"] == spot["map"])
            x, y, icon = map_tracker.player_on_page(index, position)
            self.assertLessEqual(abs(x - spot["x"]), 1)
            self.assertLessEqual(abs(y - spot["y"]), 1)
            self.assertEqual(icon, "images/player.png")
            self.assertIsNotNone(pkgutil.get_data(map_tracker.__package__, f"tracker/{icon}"))

    def test_no_dot_where_the_page_does_not_show_the_floor(self) -> None:
        index = map_tracker.page_of_floor(FLOOR_3)
        self.assertIsNone(map_tracker.player_on_page(index, {"layer": FLOOR_1, "x": 0, "y": 0}))
        for nonsense in (None, "", {"layer": FLOOR_3}, {"layer": FLOOR_3, "x": "1", "y": 2}):
            self.assertIsNone(map_tracker.player_on_page(index, nonsense))
        self.assertIsNone(map_tracker.player_on_page(-1, {"layer": FLOOR_3, "x": 0, "y": 0}))
        self.assertIsNone(map_tracker.player_on_page(10_000, {"layer": FLOOR_3, "x": 0, "y": 0}))


class TestClientShowsThePlayer(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness().connect()
        self.now = 100.0

    def stand(self, region: str | None, x: int = 0, y: int = 0, map_name: str = "2-fisheries",
              seconds: float = MAP_UPDATE_SECONDS) -> list[ShowOnMap]:
        self.now += seconds
        self.h.core.on_state(GameState(map=map_name, position=Position(region, x, y, 0)))
        self.h.core.tick(self.now)
        return [action for action in self.h.core.drain() if isinstance(action, ShowOnMap)]

    def test_floor_and_position_are_told_at_once(self) -> None:
        self.assertEqual(self.stand("UpperHub", 10, 20), [ShowOnMap(FLOOR_1, 10, 20, True)])

    def test_moving_on_one_floor_is_told_now_and_then(self) -> None:
        self.stand("UpperHub")
        self.assertEqual(self.stand("UpperHub", 500, 0, seconds=0.1), [], "too soon")
        self.assertEqual(self.stand("UpperHub", MAP_UPDATE_DISTANCE - 1, 0), [], "too small a step")
        self.assertEqual(self.stand("UpperHub", 500, 0), [ShowOnMap(FLOOR_1, 500, 0, False)])

    def test_a_new_floor_is_told_straight_away(self) -> None:
        self.stand("UpperHub")
        self.assertEqual(self.stand("FontaineFreezerHall1", seconds=0.1), [ShowOnMap(FLOOR_3, 0, 0, True)])

    def test_a_zone_the_map_does_not_draw_keeps_the_floor(self) -> None:
        self.assertEqual(self.stand(None), [], "no floor known yet")
        self.stand("UpperHub")
        self.assertEqual(self.stand(None, 900, 0), [ShowOnMap(FLOOR_1, 900, 0, False)])
        self.assertEqual(self.stand("NotOnTheMapScreen", 900, 900), [ShowOnMap(FLOOR_1, 900, 900, False)])
        self.assertEqual(self.stand(None, 0, 0, map_name="2-subbay"), [], "not the floor of another map")

    def test_maps_without_pages_and_unknown_places_say_nothing(self) -> None:
        self.assertEqual(self.stand("Arena", map_name="challengeroomcombat"), [])
        self.h.core.on_state(GameState(map="2-fisheries"))
        self.h.core.tick(self.now + 5)
        self.assertEqual([a for a in self.h.core.drain() if isinstance(a, ShowOnMap)], [])

    def test_a_new_connection_hears_the_floor_again(self) -> None:
        self.stand("UpperHub")
        self.h.disconnect()
        self.assertEqual(self.stand("UpperHub"), [], "nobody to tell")
        self.h.connect()
        self.assertEqual(self.stand("UpperHub"), [ShowOnMap(FLOOR_1, 0, 0, True)])


class TestAgentPosition(unittest.TestCase):
    def test_read_from_the_agent(self) -> None:
        self.assertEqual(position_of({"region": "UpperHub", "x": 1.4, "y": -2, "z": 3}), Position("UpperHub", 1, -2, 3))
        self.assertEqual(position_of({"region": None, "x": 1, "y": 2, "z": 3}), Position(None, 1, 2, 3))
        for nonsense in (None, [], {"x": 1, "y": 2}, {"x": True, "y": 2, "z": 3}, {"x": float("nan"), "y": 0, "z": 0}):
            self.assertIsNone(position_of(nonsense))


def picture_size(path: str) -> tuple[int, int]:
    data = pkgutil.get_data(map_tracker.__package__, f"tracker/{path}")
    assert data is not None and data[:8] == b"\x89PNG\r\n\x1a\n", path
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")

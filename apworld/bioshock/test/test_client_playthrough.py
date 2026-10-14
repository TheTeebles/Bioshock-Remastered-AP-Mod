"""
Whole seeds from start to finish: a generated world, the client core and the simulated game.

The "server" here is the seed's own placements: checking a location hands back the item that was placed there.
Archipelago's logic is the referee. A check or a goal that goes out before the world's rules allow it fails the test,
and so does one that is still held back once they do.
"""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from typing import Any, ClassVar

from BaseClasses import CollectionState
from Fill import distribute_items_restrictive

from ..client import game_data, travel
from ..client.protocol import Action, SendGoal
from ..data import (
    ARCADIA, CHAPTERS, FARMERS_MARKET, FORT_FROLIC, GOAL_LOCATION, LEVEL_NAMES, LOCATION_TABLE, WELCOME_TO_RAPTURE, ItemKind,
    LocationCategory,
)
from ..items import ITEM_KIND
from .bases import BioShockTestBase
from .client_harness import Harness, expected_deliveries, play_level

LOCATION_LEVEL = {location.id: location.level for location in LOCATION_TABLE}
LEVEL_COMPLETIONS = {location.id for location in LOCATION_TABLE
                     if location.category == LocationCategory.LEVEL_COMPLETION}


def finished_levels(reached: Sequence[str]) -> set[str]:
    """Levels whose story is over once the player has been to `reached`. The rule, stated without the client's code:
    a level is finished when you arrive in a later one, except that the story returns to Arcadia after the
    Farmer's Market, so those two only finish on arriving in Fort Frolic."""
    furthest = max(LEVEL_NAMES.index(level) for level in reached)
    finished = set(LEVEL_NAMES[:furthest])
    if furthest < LEVEL_NAMES.index(FORT_FROLIC):
        finished -= {ARCADIA, FARMERS_MARKET}
    return finished


class SeedHarness(Harness):
    """The client harness with a generated seed behind it."""

    def __init__(self, slot_data: dict[str, Any], placements: dict[int, int],
                 check_in_logic: Callable[[int], bool], goal_in_logic: Callable[[], bool],
                 collect: Callable[[int], None]) -> None:
        super().__init__()
        self.slot_data = slot_data
        self.valid = set(placements)
        self.placements = placements
        self.check_in_logic = check_in_logic
        self.goal_in_logic = goal_in_logic
        self.collect = collect
        self.out_of_logic: list[str] = []
        self.most_held = 0

    def perform(self, actions: Iterable[Action]) -> None:
        actions = list(actions)
        if any(isinstance(action, SendGoal) for action in actions) and not self.goal_in_logic():
            self.out_of_logic.append("the goal")
        super().perform(actions)

    def server_receives(self, locations: Sequence[int]) -> None:
        new = [location for location in locations if location not in self.server_checked]
        self.out_of_logic += [str(location) for location in new if not self.check_in_logic(location)]
        super().server_receives(locations)
        for location in new:
            self.collect(location)
            self.server_items.append(self.placements[location])
        self.core.on_items(self.server_items)


class PlaythroughBase(BioShockTestBase):
    auto_construct = False  # every test builds its own seeds
    seeds: ClassVar[tuple[int, ...]] = (1, 2, 3)

    def new_seed(self, seed: int) -> SeedHarness:
        self.world_setup(seed)
        distribute_items_restrictive(self.multiworld)
        locations = {location.address: location for location in self.multiworld.get_locations(self.player)
                     if location.address is not None}
        goal = self.multiworld.get_location(GOAL_LOCATION, self.player)
        state = CollectionState(self.multiworld)  # what the player has been sent so far, as the generator sees it

        def collect(location_id: int) -> None:
            state.collect(locations[location_id].item, True, locations[location_id])

        slot_data = json.loads(json.dumps(self.world.fill_slot_data()))  # the client gets it as JSON
        placements = {location_id: location.item.code for location_id, location in locations.items()}
        return SeedHarness(slot_data, placements, lambda location_id: locations[location_id].can_reach(state),
                           lambda: goal.can_reach(state), collect)

    def check(self, harness: SeedHarness, played: Sequence[str], reached: Sequence[str] | None = None) -> None:
        """What must hold whenever the client has come to rest. `played` are the levels the player has cleaned out,
        `reached` the levels they have been to (the same ones unless told otherwise)."""
        self.assertEqual(harness.out_of_logic, [], "sent before the world's rules allow it")

        found = harness.core.collected & harness.valid
        expected = {location for location in harness.valid
                    if location not in LEVEL_COMPLETIONS and LOCATION_LEVEL[location] in played}
        self.assertEqual(found - LEVEL_COMPLETIONS, expected, "the checks of the levels played so far")
        finished = finished_levels(played if reached is None else reached)
        self.assertEqual(found & LEVEL_COMPLETIONS,
                         {location for location in harness.valid & LEVEL_COMPLETIONS
                          if LOCATION_LEVEL[location] in finished}, "the levels finished so far")

        in_logic = {location for location in found if harness.check_in_logic(location)}
        self.assertEqual(harness.server_checked, in_logic, "everything logic allows is sent, and nothing else")
        self.assertEqual(set(harness.core.held_locations()), found - in_logic)
        harness.most_held = max(harness.most_held, len(found - in_logic))

        self.assertEqual(harness.goals, int(harness.core.goal_reached and harness.goal_in_logic()))
        self.assertEqual(harness.core.delivered, len(harness.server_items), "every received item was dealt with")
        self.assertEqual(harness.storage, harness.core.snapshot(), "the server has the latest saved state")

    def check_finished(self, harness: SeedHarness, expected_warnings: tuple[str, ...] = ()) -> None:
        self.assertEqual(harness.server_checked, harness.valid)
        self.assertEqual(harness.goals, 1)
        self.assertEqual(harness.core.held_locations(), [])
        self.assertLessEqual(set(harness.sent_checks), harness.valid, "only locations that exist in this seed")
        self.assertEqual(len(harness.sent_checks), len(set(harness.sent_checks)), "no check is sent twice")
        self.assertEqual(len(harness.server_items), len(harness.server_checked))

        commands, actions = expected_deliveries(harness.server_items)
        self.assertEqual(harness.game.commands, commands, "every item is given once, in the order it was received")
        self.assertEqual(harness.game.actions, actions)
        self.assertEqual([text for text in harness.warnings()
                          if not text.startswith(("Can't deliver ",) + expected_warnings)], [])

    def play_story(self, harness: SeedHarness,
                   interrupt: Callable[[SeedHarness, int], None] | None = None) -> None:
        """Play every level in story order, doing everything in each, then beat Fontaine."""
        harness.connect().attach()
        for index, level in enumerate(LEVEL_NAMES):
            play_level(harness.game, level)
            if interrupt is not None:
                interrupt(harness, index)
            harness.settle()
            self.check(harness, LEVEL_NAMES[:index + 1])
        harness.game.defeat_fontaine()
        harness.settle()
        self.check(harness, LEVEL_NAMES)


class TestStoryPlaythrough(PlaythroughBase):
    options = {"level_access": "access_items"}

    def test_story_order(self) -> None:
        held = 0
        for seed in self.seeds:
            with self.subTest(seed=seed):
                harness = self.new_seed(seed)
                self.play_story(harness)
                self.check_finished(harness)
                held += harness.most_held
        self.assertGreater(held, 0, "none of these seeds ever held a check back, so they do not test level access")

    def test_rushing_to_the_end_and_coming_back(self) -> None:
        for seed in self.seeds:
            with self.subTest(seed=seed):
                harness = self.new_seed(seed)
                harness.connect().attach()
                play_level(harness.game, WELCOME_TO_RAPTURE)  # the one level that cannot be revisited
                for index, level in enumerate(LEVEL_NAMES[1:], start=1):
                    harness.game.travel(level)
                    harness.settle()
                    self.check(harness, [WELCOME_TO_RAPTURE], reached=LEVEL_NAMES[:index + 1])
                harness.game.defeat_fontaine()
                harness.settle()
                self.check(harness, [WELCOME_TO_RAPTURE], reached=LEVEL_NAMES)
                self.assertTrue(harness.core.goal_reached)

                played = [WELCOME_TO_RAPTURE]
                for level in reversed(LEVEL_NAMES[1:]):
                    play_level(harness.game, level)
                    harness.settle()
                    played.append(level)
                    self.check(harness, played, reached=LEVEL_NAMES)
                self.check_finished(harness)

    def test_interruptions_change_nothing(self) -> None:
        def interrupt(harness: SeedHarness, index: int) -> None:
            kind = index % 4
            if kind == 0:  # the client is closed and started again before it has looked at the game
                harness.restart_client()
            elif kind == 1:  # the connection to Archipelago drops while items are on their way to the game
                harness.step(2)
                harness.disconnect()
                harness.step(8)
                harness.connect()
            elif kind == 2:  # the client is restarted once everything is done
                harness.settle()
                harness.restart_client()
            else:  # the game is closed and opened again
                harness.settle()
                harness.detach()
                harness.step(3)
                harness.attach()

        for seed in self.seeds:
            with self.subTest(seed=seed):
                plain = self.new_seed(seed)
                self.play_story(plain)
                self.check_finished(plain)

                harness = self.new_seed(seed)
                self.play_story(harness, interrupt)
                self.check_finished(harness, expected_warnings=("Game disconnected",))
                self.assertEqual(Counter(harness.game.commands), Counter(plain.game.commands))
                self.assertEqual(harness.game.inventory, plain.game.inventory)


class TestVanillaAccessPlaythrough(PlaythroughBase):
    options = {"level_access": "vanilla"}

    def test_nothing_is_ever_held(self) -> None:
        for seed in self.seeds:
            with self.subTest(seed=seed):
                harness = self.new_seed(seed)
                self.play_story(harness)
                self.check_finished(harness)
                self.assertEqual(harness.most_held, 0)
                self.assertFalse([notice.text for notice in harness.notices if "will be sent once" in notice.text])


class TestTrapPlaythrough(PlaythroughBase):
    options = {"trap_chance": 100}
    seeds = (1,)

    def test_traps_reach_the_game(self) -> None:
        harness = self.new_seed(self.seeds[0])
        self.play_story(harness)
        self.check_finished(harness)
        self.assertGreater(len(harness.game.actions), 50)
        self.assertEqual(set(harness.game.actions), {"eve_drain", "pickpocket"})
        self.assertGreater(harness.game.alarms, 20, "the alarm trap is a console command of the game's own")
        self.assertEqual(harness.game.commands.count("StartSecurityAlarm"), harness.game.alarms)
        self.assertEqual(len(harness.game.summoned), harness.game.alarms, "a security bot with every alarm")


class TestAudioDiariesOnlyPlaythrough(PlaythroughBase):
    options = {
        "audio_diary_checks": "all",
        "story_checks": False,
        "little_sister_checks": False,
        "power_to_the_people_checks": False,
        "level_completion_checks": False,
    }
    seeds = (1,)

    def test_other_pickups_are_not_sent(self) -> None:
        harness = self.new_seed(self.seeds[0])
        self.assertEqual(len(harness.valid), 122)
        self.play_story(harness)
        self.check_finished(harness)
        self.assertGreater(len(harness.core.collected), 122, "the player still did everything else")


class TestNoCollectiblesPlaythrough(PlaythroughBase):
    options = {"audio_diary_checks": "off"}
    seeds = (1, 2)

    def test_the_game_is_finished_without_a_single_diary(self) -> None:
        for seed in self.seeds:
            with self.subTest(seed=seed):
                harness = self.new_seed(seed)
                self.assertEqual(len(harness.valid), 73)
                self.assertFalse([location for location in harness.valid if 1000 < location < 2000])
                self.play_story(harness)
                self.check_finished(harness)


class TestSkippingCollectiblesPlaythrough(PlaythroughBase):
    """The default settings, played by someone who never picks up a diary."""
    seeds = (1, 2, 3)

    def test_everything_that_matters_arrives_anyway(self) -> None:
        for seed in self.seeds:
            with self.subTest(seed=seed):
                harness = self.new_seed(seed)
                collectibles = {location.id for location in LOCATION_TABLE
                                if location.category == LocationCategory.AUDIO_DIARY}
                self.assertEqual(len(harness.valid & collectibles), 122)
                harness.connect().attach()
                for level in LEVEL_NAMES:
                    play_level(harness.game, level)
                    harness.game.diaries.clear()  # walked past every one of them
                    harness.settle()
                harness.game.defeat_fontaine()
                harness.settle()

                self.assertEqual(harness.out_of_logic, [])
                self.assertEqual(harness.goals, 1, "the goal never depends on a collectible")
                self.assertEqual(harness.server_checked, harness.valid - collectibles)
                self.assertEqual(harness.core.held_locations(), [])
                # Everything left behind on the collectibles is filler: every access item and every useful item
                # of the seed has been received.
                left_behind = Counter(ITEM_KIND[game_data.ITEM_NAME[harness.placements[location]]]
                                      for location in harness.valid - harness.server_checked)
                self.assertEqual(set(left_behind), {ItemKind.FILLER})
                self.assertEqual(sum(left_behind.values()), 122)


class TestOpenRapturePlaythrough(PlaythroughBase):
    """Open Rapture: the player goes wherever the bathysphere and the story's crossings let them, a chapter at a time,
    and plays it out. Archipelago's logic is the referee, as for the story order."""
    options = {"level_access": "open_rapture"}
    seeds = (1, 2, 3, 4, 5)

    def play_open_rapture(self, harness: SeedHarness) -> list[int]:
        game = harness.game
        harness.connect().attach()
        for level in (WELCOME_TO_RAPTURE, "Medical Pavilion"):
            play_level(game, level)
            game.quests |= travel.CLOSING_QUESTS.get(level, frozenset())
            harness.settle()
        done = {1}
        order = []
        while len(done) < len(CHAPTERS):
            for chapter in CHAPTERS:
                if chapter.number in done or not self.enter(harness, chapter, done):
                    continue
                for level in chapter.levels:
                    play_level(game, level)
                    game.quests |= travel.CLOSING_QUESTS.get(level, frozenset())
                    harness.settle()
                done.add(chapter.number)
                order.append(chapter.number)
                break
            else:
                self.fail(f"stuck with chapters {sorted(done)} done; the bathysphere offers {game.bathysphere_stops()}")
        game.defeat_fontaine()
        harness.settle()
        return order

    @staticmethod
    def enter(harness: SeedHarness, chapter, done: set[int]) -> bool:
        """Go to the start of a chapter if the game lets the player, the way the game would."""
        game = harness.game
        first = chapter.levels[0]
        if chapter.number == 6:  # the story's way, out of Ryan's office
            if 5 not in done:
                return False
            game.travel("Rapture Central Control")
            harness.settle()
            if game.cross(first) != first:
                harness.settle()
                return False
            return True
        if chapter.number == 7 and 6 not in done:
            return False
        stop = "Apollo Square" if chapter.number == 6 else first
        if stop not in game.bathysphere_stops():
            return False
        game.ride(stop)
        return True

    def test_any_order_the_items_allow(self) -> None:
        orders = set()
        for seed in self.seeds:
            with self.subTest(seed=seed):
                harness = self.new_seed(seed)
                orders.add(tuple(self.play_open_rapture(harness)))
                self.assertEqual(harness.out_of_logic, [], "sent before the world's rules allow it")
                self.check_finished(harness, expected_warnings=("Arcadia is locked", "Olympus Heights is locked"))
        self.assertGreater(len(orders), 1, "these seeds all opened the chapters in the same order")

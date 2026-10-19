"""Open Rapture (level_access: open_rapture): travel opens chapter by chapter. The world side only; the client side
is in test_client_travel.py."""
import itertools

from BaseClasses import CollectionState
from Options import OptionError

from ..client import travel
from ..data import CHAPTER_ACCESS_ITEMS, CHAPTERS, GOAL_LOCATION, LOCATION_TABLE
from .bases import BioShockTestBase
from .test_logic import PoolTestBase

ACCESS = {chapter.number: chapter.access_item for chapter in CHAPTERS}


def locations_in_chapter(number: int) -> list[str]:
    levels = next(chapter.levels for chapter in CHAPTERS if chapter.number == number)
    return [location.name for location in LOCATION_TABLE if location.level in levels]


class TestOpenRapture(BioShockTestBase):
    options = {"level_access": "open_rapture"}

    def test_one_access_item_per_chapter(self) -> None:
        progression = [item.name for item in self.multiworld.itempool if item.advancement]
        self.assertCountEqual(progression, CHAPTER_ACCESS_ITEMS)
        self.assertEqual(self.get_items_by_name("Farmer's Market Access"), [])
        self.assertEqual(self.get_items_by_name("Apollo Square Access"), [])

    def test_chapter_one_is_open(self) -> None:
        for name in self.enabled(locations_in_chapter(1)):
            self.assertTrue(self.can_reach_location(name), name)

    def test_the_chapters_before_ryan_need_only_their_own_item(self) -> None:
        for number in (2, 3, 4):
            with self.subTest(chapter=number):
                self.assertAccessDependency(self.enabled(locations_in_chapter(number)), [[ACCESS[number]]],
                                            only_check_listed=True)

    def test_ryan_needs_the_three_chapters_before_him(self) -> None:
        names = self.enabled(locations_in_chapter(5))
        self.collect_by_name([ACCESS[5], ACCESS[2], ACCESS[3]])
        self.assertFalse(any(self.can_reach_location(name) for name in names))
        self.collect_by_name(ACCESS[4])
        self.assertTrue(all(self.can_reach_location(name) for name in names))

    def test_the_end_follows_in_story_order(self) -> None:
        self.collect_by_name([ACCESS[6], ACCESS[7]])
        self.assertBeatable(False)
        self.assertFalse(self.can_reach_location(self.enabled(locations_in_chapter(6))[0]))
        self.collect_by_name([ACCESS[2], ACCESS[3], ACCESS[4], ACCESS[5]])
        self.assertTrue(self.can_reach_location(self.enabled(locations_in_chapter(6))[0]))
        self.assertBeatable(True)
        self.assertTrue(self.can_reach_location(GOAL_LOCATION))

    def test_the_client_opens_exactly_the_chapters_the_logic_reaches(self) -> None:
        # The bathysphere offers what travel.open_chapters says; the generator places items by the regions. They
        # must agree for every set of access items, or a seed could need a chapter the menu does not offer.
        items = [ACCESS[number] for number in sorted(ACCESS) if ACCESS[number]]
        for size in range(len(items) + 1):
            for held in itertools.combinations(items, size):
                state = CollectionState(self.multiworld)
                for name in held:
                    state.collect(self.world.create_item(name), prevent_sweep=True)
                reached = {chapter.number for chapter in CHAPTERS
                           if self.multiworld.get_region(chapter.levels[0], self.player).can_reach(state)}
                with self.subTest(held=held):
                    self.assertEqual(reached, set(travel.open_chapters(set(held))))

    def test_slot_data_names_each_levels_chapter_item(self) -> None:
        access = self.world.fill_slot_data()["level_access_items"]
        self.assertEqual(access["Farmer's Market"], "Arcadia Access")
        self.assertEqual(access["Apollo Square"], "Olympus Heights Access")
        self.assertEqual(access["Rapture Central Control"], "Hephaestus Access")
        self.assertIsNone(access["Medical Pavilion"])

    def enabled(self, names: list[str]) -> list[str]:
        present = {location.name for location in self.multiworld.get_locations(self.player)}
        return [name for name in names if name in present]


class TestOpenRaptureWithoutStartingCamera(BioShockTestBase):
    options = {"level_access": "open_rapture", "start_with_research_camera": False}

    def test_fort_frolics_photographs_need_the_camera_from_neptunes_bounty(self) -> None:
        photos = ["Fort Frolic - Story: Photograph Kyle Fitzpatrick", "Fort Frolic - Story: Complete Cohen's Masterpiece",
                  "Fort Frolic - Level Complete"]
        self.collect_by_name(ACCESS[4])
        self.assertFalse(any(self.can_reach_location(name) for name in photos))
        self.collect_by_name(ACCESS[2])
        self.assertTrue(all(self.can_reach_location(name) for name in photos))

    def test_the_whole_of_fort_frolic_needs_the_camera(self) -> None:
        # Cohen opens the rest of Fort Frolic only once Fitzpatrick's photograph is placed.
        present = {location.name for location in self.multiworld.get_locations(self.player)}
        fort_frolic = [name for name in locations_in_chapter(4) if name in present]
        self.assertGreater(len(fort_frolic), 10)
        self.assertAccessDependency(fort_frolic, [[ACCESS[2]]], only_check_listed=True)

    def test_universal_tracker_knows_the_camera_was_not_given(self) -> None:
        slot_data = dict(self.world.fill_slot_data())
        self.world.options.start_with_research_camera.value = True
        self.world.options.level_access.value = 0
        self.multiworld.re_gen_passthrough = {"BioShock": slot_data}
        self.world.generate_early()
        self.assertFalse(self.world.options.start_with_research_camera)
        self.assertEqual(self.world.options.level_access.value, 2)


class TestOpenRaptureGenerates(PoolTestBase):
    auto_construct = False

    def test_every_seed_generates(self) -> None:
        for seed in range(6):
            with self.subTest(seed=seed):
                self.generate_for_real({"level_access": "open_rapture"}, seed=seed)

    def test_next_to_other_players(self) -> None:
        for others in itertools.product(({"level_access": "access_items"}, {"level_access": "vanilla"}), repeat=1):
            self.generate_for_real({"level_access": "open_rapture"}, *others)

    def test_somewhere_to_start_is_still_needed(self) -> None:
        # Stations only: every one is past chapter 1, so the first chapter item would have nowhere to go.
        stations_only = {"level_access": "open_rapture", "story_checks": False, "little_sister_checks": False,
                         "level_completion_checks": False, "audio_diary_checks": "off"}
        with self.assertRaises(OptionError) as raised:
            self.generate_for_real(stations_only)
        self.assertIn("open from the start", str(raised.exception))

    def test_a_later_chapter_item_alone_is_no_start(self) -> None:
        # Stations only and Hephaestus Access to start with: Hephaestus still needs chapters 2 to 4 first.
        stations_only = {"level_access": "open_rapture", "story_checks": False, "little_sister_checks": False,
                         "level_completion_checks": False, "audio_diary_checks": "off",
                         "start_inventory": {"Hephaestus Access": 1}}
        with self.assertRaises(OptionError) as raised:
            self.generate_for_real(stations_only)
        self.assertIn("open from the start", str(raised.exception))


class TestOpenRaptureOptionSweep(PoolTestBase):
    """Many seeds over random mixes of the other options: each one either generates and is beatable, or is refused
    with an OptionError that says why. A fill that fails is a bug."""
    auto_construct = False
    SEEDS = 40

    def random_options(self, rng) -> dict:
        return {
            "level_access": "open_rapture",
            "story_checks": rng.random() < 0.8,
            "little_sister_checks": rng.random() < 0.7,
            "power_to_the_people_checks": rng.random() < 0.6,
            "level_completion_checks": rng.random() < 0.7,
            "audio_diary_checks": rng.choice(["off", "filler_only", "all"]),
            "shop_checks": rng.choice(["off", "first_appearances", "all"]),
            "start_with_research_camera": rng.random() < 0.5,
            "trap_chance": rng.choice([0, 10, 50]),
        }

    def test_random_option_mixes(self) -> None:
        import random
        rng = random.Random(2026_10_14)
        made = refused = 0
        for seed in range(self.SEEDS):
            players = [self.random_options(rng) for _ in range(rng.choice([1, 1, 2]))]
            with self.subTest(seed=seed, players=players):
                try:
                    multiworld = self.generate_for_real(*players, seed=seed)
                except OptionError:
                    refused += 1
                    continue
                self.assertTrue(multiworld.can_beat_game(multiworld.state), "every player can reach the goal")
                made += 1
        self.assertGreater(made, self.SEEDS // 2, f"most mixes should generate ({refused} refused)")

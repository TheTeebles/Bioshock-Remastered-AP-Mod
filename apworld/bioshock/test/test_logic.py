import itertools
import tempfile
from collections import Counter
from collections.abc import Iterable
from typing import Any

from BaseClasses import Item, ItemClassification, LocationProgressType, MultiWorld
from Fill import distribute_items_restrictive
from Options import OptionError
from worlds.generic.Rules import exclusion_rules

from ..client.core import SUPPORTED_SLOT_DATA_VERSION
from ..data import (
    ACCESS_ITEMS, GOAL_LOCATION, LEVELS, LOCATION_TABLE, PLASMIDS, TONICS, WEAPON_UPGRADES, ItemKind, LocationCategory,
    Source,
)
from ..items import ITEM_KIND
from ..options import AudioDiaryChecks, option_presets
from .. import locations
from ..world import SLOT_DATA_VERSION, BioShockWorld
from .bases import BioShockTestBase, generate_multiworld

CATEGORY = {location.name: location.category for location in LOCATION_TABLE}
COLLECTIBLES = (LocationCategory.AUDIO_DIARY,)
IMPORTANT = ItemClassification.progression | ItemClassification.useful


def locations_in(level: str) -> list[str]:
    return [location.name for location in LOCATION_TABLE if location.level == level]


def count_items(items: Iterable[Item]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for item in items:
        counts["progression" if item.advancement else "useful" if item.useful else "filler"] += 1
    return counts


class PoolTestBase(BioShockTestBase):
    def real_locations(self) -> list[Any]:
        return [location for location in self.multiworld.get_locations(self.player) if location.address is not None]

    def pool(self) -> Counter[str]:
        """How many progression, useful and filler items this world put in the pool."""
        return count_items(self.multiworld.itempool)

    def generate_for_real(self, *players: dict[str, Any], seed: int = 7) -> MultiWorld:
        """Archipelago's own generator from start to finish: start inventory, name groups, exclusions, fill, checks
        and output. It raises when a seed cannot be made. Players are called Player1, Player2, ..."""
        with tempfile.TemporaryDirectory(prefix="bioshock_ap_test_") as directory:
            named = {f"Player{number}": options for number, options in enumerate(players, 1)}
            return generate_multiworld(directory, named, seed)[0]

    def fill(self) -> None:
        exclusion_rules(self.multiworld, self.player, self.world.options.exclude_locations.value)  # as Main does
        distribute_items_restrictive(self.multiworld)
        self.assertEqual(self.multiworld.get_unfilled_locations(self.player), [])


class TestAccessItems(BioShockTestBase):
    options = {"level_access": "access_items", "shop_checks": True}  # every kind of check, so all are tested

    def test_default_pool(self) -> None:
        self.assertEqual(len(self.multiworld.get_unfilled_locations(self.player)) + 1,  # +1 for the goal event
                         len(list(self.multiworld.get_locations(self.player))))
        progression = [item.name for item in self.multiworld.itempool if item.advancement]
        self.assertCountEqual(progression, ACCESS_ITEMS)

    def test_starting_levels_are_open(self) -> None:
        for level in ("Welcome to Rapture", "Medical Pavilion"):
            for name in locations_in(level):
                self.assertTrue(self.can_reach_location(name), name)

    def test_each_level_needs_its_access_item(self) -> None:
        for level in LEVELS:
            if level.access_item is None:
                continue
            with self.subTest(level=level.name):
                self.assertAccessDependency(locations_in(level.name), [[level.access_item]], only_check_listed=True)

    def test_goal_needs_point_prometheus_access(self) -> None:
        self.assertAccessDependency([GOAL_LOCATION], [["Point Prometheus Access"]], only_check_listed=True)
        self.assertBeatable(False)
        self.collect_by_name("Point Prometheus Access")
        self.assertBeatable(True)


class TestVanillaAccess(BioShockTestBase):
    options = {"level_access": "vanilla"}

    def test_no_access_items(self) -> None:
        for name in ACCESS_ITEMS:
            self.assertEqual(self.get_items_by_name(name), [])

    def test_everything_open(self) -> None:
        for location in self.multiworld.get_locations(self.player):
            self.assertTrue(location.can_reach(self.multiworld.state), location.name)
        self.assertBeatable(True)


class TestDefaults(PoolTestBase):
    """Out of the box: story, Little Sisters, stations and level completion carry everything that matters, and the
    hidden collectibles only ever hold filler."""

    def test_sizes(self) -> None:
        self.assertEqual(Counter(CATEGORY[location.name] for location in self.real_locations()), Counter({
            LocationCategory.AUDIO_DIARY: 122,
            LocationCategory.STORY: 28,
            LocationCategory.LITTLE_SISTER: 21,
            LocationCategory.POWER_TO_THE_PEOPLE: 12,
            LocationCategory.LEVEL_COMPLETION: 12,
        }))
        # 73 checks may hold real items: 8 access items and 65 of the 78 useful ones. The rest of the pool is filler.
        self.assertEqual(self.pool(), Counter(progression=8, useful=65, filler=122))

    def test_collectibles_are_marked_filler_only(self) -> None:
        for location in self.real_locations():
            expected = LocationProgressType.EXCLUDED if CATEGORY[location.name] in COLLECTIBLES \
                else LocationProgressType.DEFAULT
            self.assertEqual(location.progress_type, expected, location.name)

    def test_nothing_that_matters_ends_up_on_a_collectible(self) -> None:
        self.fill()
        for location in self.real_locations():
            if CATEGORY[location.name] in COLLECTIBLES:
                self.assertFalse(location.item.classification & IMPORTANT, f"{location.item.name} on {location.name}")
        on_real_checks = [location.item for location in self.real_locations()
                          if CATEGORY[location.name] not in COLLECTIBLES]
        # Alone, the pool is an exact fit: every other check holds something that matters.
        self.assertEqual(count_items(on_real_checks), Counter(progression=8, useful=65))
        self.assertLessEqual(set(ACCESS_ITEMS), {item.name for item in on_real_checks})

    def test_no_vanilla_world_pickups_in_pool(self) -> None:
        world_pickups = {tonic.name for tonic in TONICS if tonic.source == Source.WORLD}
        world_pickups |= {"Telekinesis", "Security Bullseye"}
        pool_names = {item.name for item in self.multiworld.itempool}
        self.assertFalse(world_pickups & pool_names)

    def test_plasmids_and_upgrades_are_kept_before_tonics(self) -> None:
        expected = {
            **{name: 1 for name in WEAPON_UPGRADES},
            "Progressive Electro Bolt": 2, "Progressive Incinerate!": 2, "Progressive Winter Blast": 3,
            "Progressive Insect Swarm": 3, "Progressive Cyclone Trap": 2, "Progressive Sonic Boom": 2,
            "Progressive Hypnotize Big Daddy": 2, "Enrage!": 1, "Target Dummy": 1,
            "Health Upgrade": 8, "EVE Upgrade": 8, "Plasmid Slot": 4, "Physical Tonic Slot": 4,
            "Engineering Tonic Slot": 4, "Combat Tonic Slot": 4,
        }
        for name, count in expected.items():
            self.assertEqual(len(self.get_items_by_name(name)), count, name)
        self.assertEqual({plasmid.item_name for plasmid in PLASMIDS} - set(expected) - set(WEAPON_UPGRADES),
                         {"Telekinesis", "Security Bullseye"})
        tonics = [item.name for item in self.multiworld.itempool if ITEM_KIND[item.name] == ItemKind.TONIC]
        self.assertEqual(len(tonics), 65 - 12 - 18 - 32, "what room is left goes to tonics")
        self.assertEqual(len(tonics), len(set(tonics)), "and never the same tonic twice")

    def test_which_tonics_are_left_out_depends_on_the_seed(self) -> None:
        candidates = [tonic.name for tonic in TONICS if tonic.source != Source.WORLD]
        kept = []
        for seed in (1, 2, 3, 1):
            self.world_setup(seed)
            kept.append([item.name for item in self.multiworld.itempool if ITEM_KIND[item.name] == ItemKind.TONIC])
            self.assertEqual(len(kept[-1]), 3)
        self.assertEqual(sorted(kept[0]), sorted(kept[3]), "the same seed makes the same choice")
        self.assertEqual(len({frozenset(names) for names in kept}), 3, "different seeds choose differently")
        self.assertTrue(any(set(names) != set(candidates[:20]) for names in kept), "not simply the first twenty")

    def test_no_traps_by_default(self) -> None:
        self.assertFalse([item for item in self.multiworld.itempool if item.trap])


class TestCollectiblesOff(PoolTestBase):
    options = {"audio_diary_checks": "off"}

    def test_only_the_real_checks_exist(self) -> None:
        self.assertEqual(len(self.real_locations()), 73)
        self.assertFalse([location.name for location in self.real_locations()
                          if CATEGORY[location.name] in COLLECTIBLES])
        self.assertEqual(self.pool(), Counter(progression=8, useful=65))
        self.fill()


class TestCollectiblesHoldAnything(PoolTestBase):
    options = {"audio_diary_checks": "all"}

    def test_every_useful_item_fits(self) -> None:
        self.assertEqual(len(self.real_locations()), 195)
        self.assertEqual(self.pool(), Counter(progression=8, useful=90, filler=97))

    def test_welcome_to_rapture_collectibles_still_only_get_filler(self) -> None:
        """Welcome to Rapture cannot be revisited, so what can be missed there never holds anything important.
        Its story checks cannot be missed, so those are ordinary checks."""
        self.fill()
        missable = 0
        for location in self.world.get_region("Welcome to Rapture").locations:
            if CATEGORY[location.name] in COLLECTIBLES:
                missable += 1
                self.assertEqual(location.progress_type, LocationProgressType.EXCLUDED, location.name)
                self.assertFalse(location.item.classification & IMPORTANT, location.name)
            else:
                self.assertEqual(location.progress_type, LocationProgressType.DEFAULT, location.name)
        self.assertEqual(missable, 2)
        excluded = [location for location in self.real_locations()
                    if location.progress_type == LocationProgressType.EXCLUDED]
        self.assertEqual(len(excluded), 2, "nothing else is filler-only in this mode")


class TestFewRealChecks(PoolTestBase):
    """Only the 21 Little Sisters can hold real items: the 8 access items, then 13 plasmid items and nothing else."""
    options = {"story_checks": False, "power_to_the_people_checks": False, "level_completion_checks": False}

    def test_pool_shrinks_to_fit(self) -> None:
        self.assertEqual(self.pool(), Counter(progression=8, useful=13, filler=122))
        useful = [item.name for item in self.multiworld.itempool if item.useful]
        self.assertTrue(all(ITEM_KIND[name] == ItemKind.PLASMID for name in useful), useful)
        self.fill()


class TestPlayerExcludesLocations(PoolTestBase):
    excluded = ["Medical Pavilion - Story: Defeat Dr. Steinman", "Medical Pavilion - Little Sister 1",
                "Fort Frolic - Power to the People 2", "Arcadia - Level Complete",
                "Arcadia - Audio Diary: Seeing Ghosts"]  # the last one is filler-only already
    options = {"exclude_locations": excluded}

    def test_pool_leaves_room_for_the_exclusions(self) -> None:
        self.assertEqual(self.pool(), Counter(progression=8, useful=61, filler=126))
        self.fill()
        for name in self.excluded:
            item = self.multiworld.get_location(name, self.player).item
            self.assertFalse(item.classification & IMPORTANT, f"{item.name} on {name}")

    def test_group_and_level_names_count_too(self) -> None:
        """Archipelago turns the names of location groups into location names before the world looks at them."""
        multiworld = self.generate_for_real({"exclude_locations": ["Little Sisters", "Smuggler's Hideout"]})
        placed = [location.item for location in multiworld.get_locations(1) if location.address is not None]
        # 21 Little Sisters, plus the story check and the level completion in Smuggler's Hideout: 50 checks are left.
        self.assertEqual(count_items(placed), Counter(progression=8, useful=42, filler=145))


class TestAudioDiariesOnly(PoolTestBase):
    options = option_presets["Audio Diaries Only"]

    def test_only_audio_diaries(self) -> None:
        self.assertEqual(len(self.real_locations()), 122)
        self.assertTrue(all("Audio Diary" in location.name for location in self.real_locations()))
        self.assertEqual(self.pool(), Counter(progression=8, useful=78, filler=36))


class TestAllTraps(BioShockTestBase):
    options = {"trap_chance": 100}

    def test_filler_is_all_traps(self) -> None:
        filler = [item for item in self.multiworld.itempool if not item.advancement and not item.useful]
        self.assertTrue(filler)
        self.assertTrue(all(item.trap for item in filler))


class TestOptionChecks(PoolTestBase):
    auto_construct = False

    def generate(self, **options: Any) -> None:
        self.options = options
        self.world_setup()

    def test_no_checks_at_all_is_refused(self) -> None:
        nothing = dict(audio_diary_checks="off", story_checks=False,
                       little_sister_checks=False, power_to_the_people_checks=False, level_completion_checks=False)
        for level_access in ("access_items", "vanilla"):  # vanilla adds no items, so only this check can stop it
            with self.subTest(level_access=level_access), self.assertRaises(OptionError) as raised:
                self.generate(level_access=level_access, **nothing)
            self.assertIn("every kind of BioShock check is switched off", str(raised.exception))

    def test_access_items_need_checks_that_can_hold_them(self) -> None:
        with self.assertRaises(OptionError) as raised:
            self.generate(story_checks=False, little_sister_checks=False, power_to_the_people_checks=False,
                          level_completion_checks=False)  # only filler-only collectibles are left
        self.assertIn("needs 8 checks", str(raised.exception))
        self.assertIn("leave only 0", str(raised.exception))

    # Twelve level completion checks and nothing else, minus what the player excludes.
    completion_only = dict(story_checks=False, little_sister_checks=False, power_to_the_people_checks=False,
                           audio_diary_checks="off")
    later_levels = ("Arcadia", "Fort Frolic", "Hephaestus", "Apollo Square", "Olympus Heights")

    def test_exactly_enough_checks_is_enough(self) -> None:
        self.generate(**self.completion_only,
                      exclude_locations=[f"{level} - Level Complete" for level in self.later_levels[:4]])
        self.assertEqual(self.pool(), Counter(progression=8, filler=4))
        self.fill()
        self.assertBeatable(True)

    def test_one_check_too_few_is_refused(self) -> None:
        seven_left = dict(self.completion_only,
                          exclude_locations=[f"{level} - Level Complete" for level in self.later_levels])
        with self.assertRaises(OptionError) as raised:
            self.generate(**seven_left)
        self.assertIn("leave only 7", str(raised.exception))
        self.assertIn("exclude_locations", str(raised.exception))

        # An access item the player starts with leaves the pool again, so seven checks are then enough.
        multiworld = self.generate_for_real({**seven_left, "start_inventory_from_pool": {"Apollo Square Access": 1}})
        placed = [location.item for location in multiworld.get_locations(1) if location.address is not None]
        self.assertEqual(count_items(placed), Counter(progression=7, filler=5))
        with self.assertRaises(OptionError):  # one that is merely added to the inventory does not
            self.generate_for_real({**seven_left, "start_inventory": {"Apollo Square Access": 1}})

    def test_shuffled_upgrades_need_room_in_the_pool(self) -> None:
        stations_only = dict(story_checks=False, little_sister_checks=False, level_completion_checks=False,
                             audio_diary_checks="off")
        self.generate(**stations_only, level_access="vanilla")  # 12 stations, 12 upgrades
        with self.assertRaises(OptionError) as raised:
            self.generate(**stations_only, level_access="vanilla",
                          exclude_locations=["Arcadia - Power to the People 1"])
        self.assertIn("leave room for 11", str(raised.exception))

    def test_access_items_from_the_pool_still_take_room(self) -> None:
        # The pool is made with every access item in it, and those started with are swapped for filler afterwards,
        # so they leave no more room for the upgrades.
        options = dict(story_checks=False, little_sister_checks=False, audio_diary_checks="off",
                       start_inventory_from_pool={name: 1 for name in ACCESS_ITEMS},
                       exclude_locations=[f"{level} - Level Complete" for level in self.later_levels])
        with self.assertRaises(OptionError) as raised:
            self.generate(**options)
        self.assertIn("leave room for", str(raised.exception))

    def test_filler_only_collectibles_alone_work_without_access_items(self) -> None:
        self.generate(level_access="vanilla", story_checks=False, little_sister_checks=False,
                      power_to_the_people_checks=False, level_completion_checks=False)
        self.assertEqual(self.pool(), Counter(filler=122))
        self.fill()

    def test_every_preset_generates(self) -> None:
        for name, preset in option_presets.items():
            with self.subTest(preset=name):
                self.generate(**preset)
                self.fill()
                self.assertBeatable(True)

    def test_old_on_off_values_still_work(self) -> None:
        self.assertEqual(AudioDiaryChecks.from_any(True).value, AudioDiaryChecks.option_all)
        self.assertEqual(AudioDiaryChecks.from_any(False).value, AudioDiaryChecks.option_off)
        for text, value in (("true", 2), ("on", 2), ("yes", 2), ("false", 0), ("off", 0), ("no", 0)):
            self.assertEqual(AudioDiaryChecks.from_any(text).value, value, text)
        self.assertEqual(AudioDiaryChecks.from_any("filler_only").value, AudioDiaryChecks.option_filler_only)
        self.assertEqual(AudioDiaryChecks.default, AudioDiaryChecks.option_filler_only)
        self.assertEqual(AudioDiaryChecks(AudioDiaryChecks.default).current_option_name, "Filler Only")


class TestShopChecks(PoolTestBase):
    """Gatherer's Garden lines are checks when asked for, and only ever hold useful items or filler: a player may
    never have the ADAM to buy one."""
    options = {"shop_checks": True}

    def test_sizes(self) -> None:
        garden = [location for location in self.real_locations()
                  if CATEGORY[location.name] == LocationCategory.GATHERERS_GARDEN]
        self.assertEqual(len(garden), 273)
        self.assertEqual(len({location.parent_region.name for location in garden}), 10, "one Garden per level")
        # The checks make room for every useful item; the rest is filler.
        self.assertEqual(self.pool(), Counter(progression=8, useful=90, filler=370))

    def test_first_appearances(self) -> None:
        self.options = {"shop_checks": "first_appearances"}
        self.world_setup()
        garden = {location.name for location in self.real_locations()
                  if CATEGORY[location.name] == LocationCategory.GATHERERS_GARDEN}
        self.assertEqual(len(garden), 61)
        self.assertIn("Medical Pavilion - Gatherer's Garden: Enrage!", garden)
        self.assertIn("Neptune's Bounty - Gatherer's Garden: Sonic Boom", garden)
        self.assertNotIn("Neptune's Bounty - Gatherer's Garden: Enrage!", garden, "sold first in Medical Pavilion")
        self.assertIn("Fort Frolic - Gatherer's Garden: Sonic Boom 2", garden)
        self.assertNotIn("Fort Frolic - Gatherer's Garden: Sonic Boom", garden)

    def test_no_progression_in_a_shop(self) -> None:
        self.fill()
        for location in self.real_locations():
            if CATEGORY[location.name] == LocationCategory.GATHERERS_GARDEN:
                self.assertFalse(location.item.advancement, f"{location.item.name} on {location.name}")
        self.assertBeatable(True)

    def test_off_by_default(self) -> None:
        self.assertFalse(self.generate_for_real({"game": "BioShock", "BioShock": {}}).worlds[1].options.shop_checks)

    def test_shops_alone_cannot_hold_access_items(self) -> None:
        only_shops = {"shop_checks": True, "story_checks": False, "little_sister_checks": False,
                      "power_to_the_people_checks": False, "level_completion_checks": False,
                      "audio_diary_checks": "off"}
        with self.assertRaises(OptionError):
            self.options = only_shops
            self.world_setup()
        self.options = {**only_shops, "level_access": "vanilla"}
        self.world_setup()
        self.fill()
        self.assertBeatable(True)

    def test_slot_data(self) -> None:
        self.assertEqual(self.world.fill_slot_data()["shop_checks"], 2)


class TestEveryCombination(PoolTestBase):
    """All 96 ways to set the check options, each as a one-player game: it either fills and can be finished with
    nothing important on a filler-only check, or it is turned down before generation starts."""
    auto_construct = False
    switches = ("story_checks", "little_sister_checks", "power_to_the_people_checks", "level_completion_checks")
    collectibles = ("audio_diary_checks",)

    @staticmethod
    def should_be_refused(options: dict[str, Any]) -> bool:
        anything_on = any(options[name] for name in TestEveryCombination.switches) \
            or any(options[name] != "off" for name in TestEveryCombination.collectibles)
        if not anything_on:
            return True
        if options["level_access"] == "vanilla":
            return False
        # The first access item needs a check in Welcome to Rapture or Medical Pavilion that may hold it. Power to
        # the People stations only start in Neptune's Bounty.
        return not (options["story_checks"] or options["little_sister_checks"] or options["level_completion_checks"]
                    or options["audio_diary_checks"] == "all")

    def test_every_combination(self) -> None:
        refused = 0
        for access in ("access_items", "vanilla"):
            for on in itertools.product((True, False), repeat=len(self.switches)):
                for modes in itertools.product(("off", "filler_only", "all"), repeat=len(self.collectibles)):
                    options = {"level_access": access, **dict(zip(self.switches, on)),
                               **dict(zip(self.collectibles, modes))}
                    with self.subTest(**options):
                        if self.should_be_refused(options):
                            refused += 1
                            with self.assertRaises(OptionError):
                                self.options = options
                                self.world_setup()
                            continue
                        self.options = options
                        self.world_setup()
                        self.fill()
                        self.assertBeatable(True)
                        for location in self.real_locations():
                            if location.progress_type == LocationProgressType.EXCLUDED:
                                self.assertFalse(location.item.classification & IMPORTANT,
                                                 f"{location.item.name} on {location.name}")
        self.assertEqual(refused, 5)


class TestSomewhereToStart(PoolTestBase):
    """With Level Access on, the first access item has to be somewhere the player can get to. Every Power to the
    People station is in a level that needs an access item, so "stations only" has nowhere to start from. Left
    alone, Archipelago's fill would give up on such a seed whenever no other player has room for the item either."""
    auto_construct = False
    stations_only = {"story_checks": False, "little_sister_checks": False, "level_completion_checks": False}

    def test_nowhere_to_start_is_refused(self) -> None:
        self.options = self.stations_only
        with self.assertRaises(OptionError) as raised:
            self.world_setup()
        self.assertIn("open from the start", str(raised.exception))
        with self.assertRaises(OptionError):
            self.generate_for_real(self.stations_only)

    def test_it_is_refused_next_to_other_players_too(self) -> None:
        only_filler_checks = {"level_access": "vanilla", "story_checks": False, "little_sister_checks": False,
                              "power_to_the_people_checks": False, "level_completion_checks": False}
        for others in ([{}], [self.stations_only], [only_filler_checks], [{}, {}, self.stations_only]):
            with self.subTest(others=others), self.assertRaises(OptionError) as raised:
                self.generate_for_real(self.stations_only, *others)
            self.assertIn("Player1", str(raised.exception))

    def test_excluding_every_starting_check_is_refused(self) -> None:
        with self.assertRaises(OptionError) as raised:
            self.generate_for_real({"exclude_locations": ["Welcome to Rapture", "Medical Pavilion"]}, {})
        self.assertIn("exclude_locations", str(raised.exception))
        # One check left in the first two levels is enough to start from.
        first_two = [name for name in locations_in("Welcome to Rapture") + locations_in("Medical Pavilion")
                     if name != "Medical Pavilion - Level Complete"]
        self.generate_for_real({"exclude_locations": first_two})

    def test_ways_out(self) -> None:
        # Later levels' completion checks leave room for the twelve upgrades without being anywhere to start.
        room = {"level_completion_checks": True,
                "exclude_locations": ["Welcome to Rapture - Level Complete", "Medical Pavilion - Level Complete"]}
        for way_out in ({"level_access": "vanilla"},
                        {"start_inventory": {"Fort Frolic Access": 1}, **room},
                        {"start_inventory_from_pool": {"Arcadia Access": 1}, **room},
                        {"little_sister_checks": True},
                        {"audio_diary_checks": "all"}):  # a Medical Pavilion diary can then hold one
            with self.subTest(way_out=way_out):
                self.generate_for_real({**self.stations_only, **way_out})
                self.generate_for_real({**self.stations_only, **way_out}, {**self.stations_only, **way_out})

    def test_a_starting_item_that_opens_nothing_does_not_count(self) -> None:
        with self.assertRaises(OptionError):
            self.generate_for_real({**self.stations_only, "start_inventory": {"Health Upgrade": 1}})
        with self.assertRaises(OptionError):  # Arcadia's only station is excluded, so its access item opens nothing
            self.generate_for_real({**self.stations_only, "start_inventory": {"Arcadia Access": 1},
                                    "exclude_locations": ["Arcadia - Power to the People 1"]})


class TestSlotData(BioShockTestBase):
    options = {"death_link": True, "level_access": "access_items"}

    def test_slot_data(self) -> None:
        slot_data = self.world.fill_slot_data()
        self.assertEqual(slot_data["slot_data_version"], 4)
        self.assertEqual(SLOT_DATA_VERSION, SUPPORTED_SLOT_DATA_VERSION, "the client that ships with the world")
        self.assertEqual(slot_data["death_link"], 1)
        self.assertEqual(slot_data["story_checks"], 1)
        self.assertEqual(slot_data["audio_diary_checks"], AudioDiaryChecks.option_filler_only)
        self.assertNotIn("directors_commentary_checks", slot_data)
        self.assertEqual(slot_data["level_access_items"]["Arcadia"], "Arcadia Access")
        self.assertIsNone(slot_data["level_access_items"]["Medical Pavilion"])

    def test_universal_tracker_takes_the_options_from_the_slot_data(self) -> None:
        slot_data = dict(self.world.fill_slot_data(), shop_checks=2, audio_diary_checks=AudioDiaryChecks.option_all,
                         level_access=0, death_link=0)
        self.assertEqual(BioShockWorld.interpret_slot_data(slot_data), slot_data)
        self.multiworld.re_gen_passthrough = {"BioShock": slot_data}
        self.world.generate_early()
        for name in BioShockWorld.TRACKED_OPTIONS:
            self.assertEqual(getattr(self.world.options, name).value, slot_data[name], name)
        self.assertEqual(len([location for location in locations.enabled_locations(self.world)
                              if location.category == LocationCategory.GATHERERS_GARDEN]), 273)


    def test_universal_tracker_rebuilds_a_seed_that_needed_its_start_inventory(self) -> None:
        # Made with every access item from the pool, so no check had to hold one. The slot data doesn't carry the
        # start inventory, and the rebuild must not refuse the seed for it.
        slot_data = dict(self.world.fill_slot_data(), story_checks=0, little_sister_checks=0,
                         power_to_the_people_checks=0, level_completion_checks=0,
                         audio_diary_checks=AudioDiaryChecks.option_off, shop_checks=2)
        self.multiworld.re_gen_passthrough = {"BioShock": slot_data}
        self.world.generate_early()
        self.assertEqual(self.world.options.level_access.value, slot_data["level_access"])

class TestWeaponStations(PoolTestBase):
    """Station checks, on by default: a station is a check you use once, and the weapon upgrades come from the
    multiworld."""

    def test_weapon_upgrades_in_the_pool(self) -> None:
        names = Counter(item.name for item in self.multiworld.itempool)
        for upgrade in WEAPON_UPGRADES:
            self.assertEqual(names[upgrade], 1, upgrade)

    def test_stations_may_hold_progression(self) -> None:
        stations = [location for location in self.real_locations()
                    if CATEGORY[location.name] == LocationCategory.POWER_TO_THE_PEOPLE]
        self.assertEqual(len(stations), 12)
        self.assertTrue(all(location.item_rule(self.world.create_item("Arcadia Access")) for location in stations))


class TestWeaponStationsOff(PoolTestBase):
    """With no station checks the stations stay vanilla, and so do their upgrades."""
    options = {"power_to_the_people_checks": False}

    def test_no_weapon_upgrades_in_the_pool(self) -> None:
        names = {item.name for item in self.multiworld.itempool}
        self.assertFalse(names & set(WEAPON_UPGRADES))


class TestResearchCamera(BioShockTestBase):
    def precollected(self) -> list[str]:
        return [item.name for item in self.multiworld.precollected_items[self.player]]

    def test_on_by_default(self) -> None:
        self.assertEqual(self.precollected().count("Research Camera"), 1)
        self.assertNotIn("Research Camera", [item.name for item in self.multiworld.itempool])


class TestNoResearchCamera(BioShockTestBase):
    options = {"start_with_research_camera": False}

    def test_not_given(self) -> None:
        self.assertNotIn("Research Camera", [item.name for item in self.multiworld.precollected_items[self.player]])


class TestResearchCameraAlreadyInStartInventory(PoolTestBase):
    def test_given_once(self) -> None:
        multiworld = self.generate_for_real({"start_inventory": {"Research Camera": 1}})
        names = [item.name for item in multiworld.precollected_items[1]]
        self.assertEqual(names.count("Research Camera"), 1)

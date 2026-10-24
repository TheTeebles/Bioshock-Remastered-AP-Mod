import itertools
from collections.abc import Mapping
from typing import Any, TextIO

from Options import OptionError
from worlds.AutoWorld import World

from . import items, locations, map_tracker, regions, rules
from . import options as bioshock_options
from .data import (
    CHAPTER_OF_LEVEL, CHAPTERS, FREE_CHAPTERS, GAME_NAME, LEVELS, MEDICAL_PAVILION, PAST_THE_DEBRIS, TELEKINESIS,
    WELCOME_TO_RAPTURE, WORLD_VERSION, LocationData,
)
from .web_world import BioShockWebWorld

RESEARCH_CAMERA = "Research Camera"
SLOT_DATA_VERSION = 6  # 2: story checks; audio_diary_checks is 0/1/2. 3: no directors_commentary_checks.
# 4: station_touch, and shop_checks is 0/1/2. 5: world_item_checks (0/1/2), safe_checks, research_checks (0/1/2).
# 6: chapter_order (0/1) and chapter_sequence


class BioShockWorld(World):
    """
    BioShock is a 2007 first-person shooter set in Rapture, a failed underwater utopia. Armed with plasmids,
    gene tonics and whatever you can scavenge, you fight your way from the Medical Pavilion to Frank Fontaine.
    This world targets BioShock Remastered on PC.
    """

    game = GAME_NAME
    web = BioShockWebWorld()

    options_dataclass = bioshock_options.BioShockOptions
    options: bioshock_options.BioShockOptions
    chapter_sequence: tuple[int, ...] = ()  # Chapter Order shuffled: the order chapters 2 to 4 open in

    location_name_to_id = locations.LOCATION_NAME_TO_ID
    item_name_to_id = items.ITEM_NAME_TO_ID
    location_name_groups = locations.LOCATION_NAME_GROUPS
    item_name_groups = items.ITEM_NAME_GROUPS

    # Universal Tracker builds the player's world again from the slot data, without their YAML.
    ut_can_gen_without_yaml = True
    # ... and shows the game's map with a pin on every check that has a place (map_tracker.py).
    if map_tracker.tracker_world():
        tracker_world = map_tracker.tracker_world()
    TRACKED_OPTIONS = ("level_access", "chapter_order", "story_checks", "little_sister_checks",
                       "power_to_the_people_checks", "level_completion_checks", "audio_diary_checks", "shop_checks",
                       "trap_chance", "death_link", "start_with_research_camera", "world_item_checks", "safe_checks",
                       "research_checks")

    @staticmethod
    def interpret_slot_data(slot_data: dict[str, Any]) -> dict[str, Any]:
        return slot_data

    def generate_early(self) -> None:
        tracked = getattr(self.multiworld, "re_gen_passthrough", {}).get(self.game)
        if tracked is not None:  # Universal Tracker: the options the seed was made with
            for name in self.TRACKED_OPTIONS:
                if name in tracked:
                    getattr(self.options, name).value = tracked[name]
            self.chapter_sequence = tuple(int(n) for n in tracked.get("chapter_sequence") or ())
        else:  # a seed being rebuilt was checked when it was made, with start inventories the slot data doesn't carry
            self._check_options()
            self.chapter_sequence = self._pick_chapter_sequence()
        if self.options.start_with_research_camera and RESEARCH_CAMERA not in self.options.start_inventory.value:
            self.multiworld.push_precollected(self.create_item(RESEARCH_CAMERA))

    def _pick_chapter_sequence(self) -> tuple[int, ...]:
        """Chapter Order shuffled: the seed's order of the chapters before Ryan. Empty when they open in any order."""
        if self.options.chapter_order != bioshock_options.ChapterOrder.option_shuffled:
            return ()
        orders = list(itertools.permutations(FREE_CHAPTERS))
        if not self.options.start_with_research_camera:
            # Fort Frolic's story needs the camera, which lies in Neptune's Bounty (rules.py).
            orders = [order for order in orders if order.index(2) < order.index(4)]
        return self.random.choice(orders)

    def _check_options(self) -> None:
        if (self.options.chapter_order == bioshock_options.ChapterOrder.option_shuffled
                and self.options.level_access != bioshock_options.LevelAccess.option_open_rapture):
            raise OptionError(f"{self.player_name}: Chapter Order 'shuffled' only works with Level Access set to "
                              "'open_rapture'. Set Chapter Order to 'free' or Level Access to 'open_rapture'.")
        if not locations.enabled_locations(self):
            raise OptionError(f"{self.player_name}: every kind of BioShock check is switched off. "
                              "Turn at least one of them on.")
        # Access items that start in the player's inventory are taken out of the pool again, so they need no check.
        from_pool = self.options.start_inventory_from_pool.value
        needed = sum(1 for name in items.progression_item_names(self) if not from_pool.get(name))
        open_checks = locations.progression_locations(self)
        does_not_count = ("Filler-only collectibles, shop checks and locations under exclude_locations do not "
                          "count.")
        if needed > len(open_checks):
            raise OptionError(
                f"{self.player_name}: Level Access and Telekinesis need {needed} checks that may hold progression, "
                "but these "
                f"options leave only {len(open_checks)}. {does_not_count} Turn on story, Little Sister, Power to "
                "the People or level completion checks, exclude fewer locations, set audio diaries "
                "to 'all', or set Level Access to 'vanilla'.")
        if needed and not self._open_from_the_start(open_checks):
            # Without this the fill fails whenever no other player has room for the first access item either.
            raise OptionError(
                f"{self.player_name}: with Level Access on or the pickups shuffled, at least one check that may hold "
                "progression has to be open from the start, and here every one of them needs an access item or "
                f"Telekinesis first. {does_not_count} Turn on story, Little Sister or level completion checks, exclude fewer "
                "locations in Welcome to Rapture and Medical Pavilion, set Level Access to 'vanilla', or add an "
                "access item to start_inventory.")
        # As create_all_items counts it: access items taken back out of the pool still took their place in it.
        room = len(locations.open_locations(self)) - len(items.progression_item_names(self))
        required = len(items.required_useful_items(self))
        if required and room < required:
            # The game gives these no more, so one left out of the pool could never be had.
            raise OptionError(
                f"{self.player_name}: {required} items come only from the multiworld (the 12 weapon upgrades with "
                "Power to the People checks on, the shuffled pickups with World Item Checks set to 'shuffled', the "
                "9 research tonics with Research Checks on) and need as many checks that may hold useful items, but "
                f"these options leave room for {max(room, 0)}. "
                "Filler-only collectibles and locations under exclude_locations do not count. Turn on more kinds of "
                "checks, set audio diaries to 'all' or exclude fewer locations.")

    def _open_from_the_start(self, open_checks: list[LocationData]) -> list[LocationData]:
        """The checks among these that need no access item the player does not start with."""
        start_items = set(self.options.start_inventory.value) | set(self.options.start_inventory_from_pool.value)
        if items.world_pickups_shuffled(self) and TELEKINESIS not in start_items:  # the debris before Surgery
            open_checks = [location for location in open_checks if location.name not in PAST_THE_DEBRIS
                           and location.level in (WELCOME_TO_RAPTURE, MEDICAL_PAVILION)]
        if self.options.level_access == bioshock_options.LevelAccess.option_open_rapture:
            # A chapter's own item is not enough: chapter 5 needs 2 to 4 first, and so on (rules.py).
            return [location for location in open_checks if rules.items_to_reach_chapter(
                self, CHAPTER_OF_LEVEL[location.level].number) <= start_items]
        return [location for location in open_checks
                if items.access_item_of(self, location.level) in (None, *start_items)]

    def create_regions(self) -> None:
        regions.create_regions(self)
        locations.create_all_locations(self)

    def set_rules(self) -> None:
        rules.set_all_rules(self)

    def create_items(self) -> None:
        items.create_all_items(self, len(locations.open_locations(self)))

    def create_item(self, name: str) -> items.BioShockItem:
        return items.create_item(self, name)

    def get_filler_item_name(self) -> str:
        return items.get_random_filler_item_name(self)

    def fill_slot_data(self) -> Mapping[str, Any]:
        access_items = self.options.level_access != bioshock_options.LevelAccess.option_vanilla
        return {
            "slot_data_version": SLOT_DATA_VERSION,
            "world_version": WORLD_VERSION,
            **self.options.as_dict(*self.TRACKED_OPTIONS),
            # Stations open no menu: using one sends its check and closes it, and the upgrades come from the pool.
            "station_touch": items.stations_shuffled(self),
            # Which item unlocks each level's checks (None = always unlocked). Empty in vanilla access mode.
            "level_access_items": {level.name: items.access_item_of(self, level.name) for level in LEVELS}
            if access_items else {},
            # Chapter Order shuffled: the order chapters 2 to 4 open in. Empty when they open in any order.
            "chapter_sequence": list(self.chapter_sequence),
        }

    def write_spoiler_header(self, spoiler_handle: TextIO) -> None:
        if self.chapter_sequence:
            names = [CHAPTERS[number - 1].levels[0] for number in self.chapter_sequence]
            spoiler_handle.write(f"Chapter Order ({self.player_name}): {' -> '.join(names)}\n")

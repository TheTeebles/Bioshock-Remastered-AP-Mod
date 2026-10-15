from collections.abc import Mapping
from typing import Any

from Options import OptionError
from worlds.AutoWorld import World

from . import items, locations, regions, rules
from . import options as bioshock_options
from .data import GAME_NAME, LEVELS, LocationData
from .web_world import BioShockWebWorld

WORLD_VERSION = "0.1.36"
RESEARCH_CAMERA = "Research Camera"
SLOT_DATA_VERSION = 5  # 2: story checks; audio_diary_checks is 0/1/2. 3: no directors_commentary_checks.
# 4: station_touch, and shop_checks is 0/1/2. 5: world_item_checks (0/1/2), safe_checks, research_checks (0/1/2)


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

    location_name_to_id = locations.LOCATION_NAME_TO_ID
    item_name_to_id = items.ITEM_NAME_TO_ID
    location_name_groups = locations.LOCATION_NAME_GROUPS
    item_name_groups = items.ITEM_NAME_GROUPS

    # Universal Tracker builds the player's world again from the slot data, without their YAML.
    ut_can_gen_without_yaml = True
    TRACKED_OPTIONS = ("level_access", "story_checks", "little_sister_checks", "power_to_the_people_checks",
                       "level_completion_checks", "audio_diary_checks", "shop_checks", "trap_chance", "death_link",
                       "start_with_research_camera", "world_item_checks", "safe_checks",
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
        else:  # a seed being rebuilt was checked when it was made, with start inventories the slot data doesn't carry
            self._check_options()
        if self.options.start_with_research_camera and RESEARCH_CAMERA not in self.options.start_inventory.value:
            self.multiworld.push_precollected(self.create_item(RESEARCH_CAMERA))

    def _check_options(self) -> None:
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
                f"{self.player_name}: Level Access needs {needed} checks that may hold an access item, but these "
                f"options leave only {len(open_checks)}. {does_not_count} Turn on story, Little Sister, Power to "
                "the People or level completion checks, exclude fewer locations, set audio diaries "
                "to 'all', or set Level Access to 'vanilla'.")
        if needed and not self._open_from_the_start(open_checks):
            # Without this the fill fails whenever no other player has room for the first access item either.
            raise OptionError(
                f"{self.player_name}: with Level Access on, at least one check that may hold an access item has to "
                "be open from the start, and here every one of them is in a level that needs an access item "
                f"first. {does_not_count} Turn on story, Little Sister or level completion checks, exclude fewer "
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
        }

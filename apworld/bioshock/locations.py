from __future__ import annotations

from typing import TYPE_CHECKING

from BaseClasses import Location, LocationProgressType

from .data import GAME_NAME, GOAL_LEVEL, GOAL_LOCATION, LOCATION_TABLE, VICTORY_ITEM, LocationCategory, LocationData
from .items import BioShockItem
from .options import CollectibleChecks

if TYPE_CHECKING:
    from .world import BioShockWorld


class BioShockLocation(Location):
    game = GAME_NAME


LOCATION_NAME_TO_ID: dict[str, int] = {location.name: location.id for location in LOCATION_TABLE}


def _build_location_groups() -> dict[str, set[str]]:
    groups: dict[str, set[str]] = {}
    for location in LOCATION_TABLE:
        groups.setdefault(location.level, set()).add(location.name)
        groups.setdefault(location.category, set()).add(location.name)
    return groups


LOCATION_NAME_GROUPS: dict[str, set[str]] = _build_location_groups()


def is_enabled(world: BioShockWorld, location: LocationData) -> bool:
    options = world.options
    return {
        LocationCategory.AUDIO_DIARY: options.audio_diary_checks != CollectibleChecks.option_off,
        LocationCategory.LITTLE_SISTER: bool(options.little_sister_checks),
        LocationCategory.DIRECTORS_COMMENTARY: options.directors_commentary_checks != CollectibleChecks.option_off,
        LocationCategory.POWER_TO_THE_PEOPLE: bool(options.power_to_the_people_checks),
        LocationCategory.LEVEL_COMPLETION: bool(options.level_completion_checks),
        LocationCategory.STORY: bool(options.story_checks),
    }[location.category]


def is_filler_only(world: BioShockWorld, location: LocationData) -> bool:
    """True for checks that must never hold anything a player needs or would miss."""
    if location.missable:
        return True  # Welcome to Rapture can never be revisited
    options = world.options
    return {
        LocationCategory.AUDIO_DIARY: options.audio_diary_checks == CollectibleChecks.option_filler_only,
        LocationCategory.DIRECTORS_COMMENTARY:
            options.directors_commentary_checks == CollectibleChecks.option_filler_only,
    }.get(location.category, False)


def enabled_locations(world: BioShockWorld) -> list[LocationData]:
    return [location for location in LOCATION_TABLE if is_enabled(world, location)]


def open_locations(world: BioShockWorld) -> list[LocationData]:
    """This slot's checks that can hold progression and useful items.

    Locations the player excluded in their YAML do not count either. Archipelago only applies those after the item
    pool has been made, which is why they are looked up here.
    """
    excluded_by_player = world.options.exclude_locations.value
    return [location for location in enabled_locations(world)
            if not is_filler_only(world, location) and location.name not in excluded_by_player]


def create_all_locations(world: BioShockWorld) -> None:
    for data in enabled_locations(world):
        region = world.get_region(data.level)
        location = BioShockLocation(world.player, data.name, data.id, region)
        if is_filler_only(world, data):
            location.progress_type = LocationProgressType.EXCLUDED
        region.locations.append(location)

    world.get_region(GOAL_LEVEL).add_event(
        GOAL_LOCATION, VICTORY_ITEM, location_type=BioShockLocation, item_type=BioShockItem
    )

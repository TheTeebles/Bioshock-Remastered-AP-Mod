from __future__ import annotations

from typing import TYPE_CHECKING

from BaseClasses import Location, LocationProgressType

from .data import (
    GAME_NAME, GATHERERS_GARDEN_FIRST_APPEARANCES, GOAL_LEVEL, GOAL_LOCATION, LOCATION_TABLE, VICTORY_ITEM,
    WORLD_SPOTS, LocationCategory, LocationData,
)
from .items import BioShockItem
from .options import CollectibleChecks, ResearchChecks, ShopChecks, WorldItemChecks

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

# Holding the Research Camera is no check for a player who starts with it.
RESEARCH_CAMERA_LOCATION: int = next(spot.id for spot in WORLD_SPOTS if spot.vanilla == "Research Camera")


def is_enabled(world: BioShockWorld, location: LocationData) -> bool:
    options = world.options
    return {
        LocationCategory.AUDIO_DIARY: options.audio_diary_checks != CollectibleChecks.option_off,
        LocationCategory.LITTLE_SISTER: bool(options.little_sister_checks),
        LocationCategory.POWER_TO_THE_PEOPLE: bool(options.power_to_the_people_checks),
        LocationCategory.LEVEL_COMPLETION: bool(options.level_completion_checks),
        LocationCategory.STORY: bool(options.story_checks),
        LocationCategory.GATHERERS_GARDEN: options.shop_checks == ShopChecks.option_all or (
            options.shop_checks == ShopChecks.option_first_appearances
            and location.id in GATHERERS_GARDEN_FIRST_APPEARANCES),
        LocationCategory.WORLD_ITEM: options.world_item_checks != WorldItemChecks.option_off and not (
            options.start_with_research_camera and location.id == RESEARCH_CAMERA_LOCATION),
        LocationCategory.SAFE: bool(options.safe_checks),
        LocationCategory.RESEARCH: options.research_checks == ResearchChecks.option_all or (
            options.research_checks == ResearchChecks.option_first_level and location.id % 10 == 1),
    }[location.category]


def may_hold_progression(location: LocationData) -> bool:
    """False for checks that can hold useful items but nothing needed to progress. Shop purchases cost ADAM, and
    how much a player has depends on how they play, so a shop check might never be bought. Research past the first
    level takes many photos, which is a grind nobody should need."""
    if location.category == LocationCategory.RESEARCH:
        return location.id % 10 == 1
    return location.category != LocationCategory.GATHERERS_GARDEN


def is_filler_only(world: BioShockWorld, location: LocationData) -> bool:
    """True for checks that must never hold anything a player needs or would miss."""
    if location.missable:
        return True  # Welcome to Rapture can never be revisited
    options = world.options
    return {
        LocationCategory.AUDIO_DIARY: options.audio_diary_checks == CollectibleChecks.option_filler_only,
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


def progression_locations(world: BioShockWorld) -> list[LocationData]:
    """This slot's checks that can hold progression items."""
    return [location for location in open_locations(world) if may_hold_progression(location)]


def create_all_locations(world: BioShockWorld) -> None:
    for data in enabled_locations(world):
        region = world.get_region(data.level)
        location = BioShockLocation(world.player, data.name, data.id, region)
        if is_filler_only(world, data):
            location.progress_type = LocationProgressType.EXCLUDED
        elif not may_hold_progression(data):
            location.item_rule = lambda item: not item.advancement
        region.locations.append(location)

    world.get_region(GOAL_LEVEL).add_event(
        GOAL_LOCATION, VICTORY_ITEM, location_type=BioShockLocation, item_type=BioShockItem
    )

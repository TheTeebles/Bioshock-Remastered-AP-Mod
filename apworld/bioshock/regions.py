from __future__ import annotations

from typing import TYPE_CHECKING

from BaseClasses import Region

from .data import LEVELS

if TYPE_CHECKING:
    from .world import BioShockWorld


def travel_entrance_name(level_name: str) -> str:
    return f"Travel to {level_name}"


def create_and_connect_regions(world: BioShockWorld) -> None:
    """
    One region per level, all hanging off the origin region.

    The story is linear, but the client only gates *sending checks* (a level's checks go out once you hold its
    access item, retroactively), so the only thing logic needs to know per level is its own access item. The rule
    for each travel entrance is set in rules.py.
    """
    origin = Region(world.origin_region_name, world.player, world.multiworld)
    world.multiworld.regions.append(origin)

    for level in LEVELS:
        region = Region(level.name, world.player, world.multiworld)
        world.multiworld.regions.append(region)
        origin.connect(region, travel_entrance_name(level.name))

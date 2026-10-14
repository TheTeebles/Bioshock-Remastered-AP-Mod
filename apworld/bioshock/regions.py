from __future__ import annotations

from typing import TYPE_CHECKING

from BaseClasses import Region

from .data import CHAPTERS, FREE_CHAPTERS, HUB, LEVELS
from .options import LevelAccess

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


def chapter_entrance_name(number: int) -> str:
    return f"Bathysphere to Chapter {number}"


def create_open_rapture_regions(world: BioShockWorld) -> None:
    """
    Open Rapture: the story's own way through chapter 1, then the bathysphere hub. Chapters 2 to 4 and 5 hang off
    the hub (each behind its access item, set in rules.py); inside a chapter the levels follow in story order; 6 comes
    after 5 and 7 after 6, as the story needs Ryan dead before them.
    """
    multiworld, player = world.multiworld, world.player
    origin = Region(world.origin_region_name, player, multiworld)
    hub = Region(HUB, player, multiworld)
    multiworld.regions += [origin, hub]
    regions = {}
    for level in LEVELS:
        regions[level.name] = Region(level.name, player, multiworld)
        multiworld.regions.append(regions[level.name])
    for chapter in CHAPTERS:  # a chapter's levels one after another
        for before, after in zip(chapter.levels, chapter.levels[1:]):
            regions[before].connect(regions[after])
    first = {chapter.number: regions[chapter.levels[0]] for chapter in CHAPTERS}
    last = {chapter.number: regions[chapter.levels[-1]] for chapter in CHAPTERS}
    origin.connect(first[1])
    last[1].connect(hub)
    for number in (*FREE_CHAPTERS, 5):
        hub.connect(first[number], chapter_entrance_name(number))
    last[5].connect(first[6], chapter_entrance_name(6))
    last[6].connect(first[7], chapter_entrance_name(7))


def create_regions(world: BioShockWorld) -> None:
    if world.options.level_access == LevelAccess.option_open_rapture:
        create_open_rapture_regions(world)
    else:
        create_and_connect_regions(world)

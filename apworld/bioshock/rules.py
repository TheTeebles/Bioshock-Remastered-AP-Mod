from __future__ import annotations

from typing import TYPE_CHECKING

from rule_builder.rules import Has, HasAll

from .data import (
    CHAPTERS, FORT_FROLIC, FREE_CHAPTERS, LEVELS, NEPTUNES_BOUNTY_ACCESS, STORY_MILESTONES, VICTORY_ITEM,
    level_completion_location_name, story_location_name,
)
from .options import LevelAccess
from .regions import chapter_entrance_name, travel_entrance_name

if TYPE_CHECKING:
    from .world import BioShockWorld


def set_all_rules(world: BioShockWorld) -> None:
    if world.options.level_access == LevelAccess.option_access_items:
        for level in LEVELS:
            if level.access_item:
                world.set_rule(world.get_entrance(travel_entrance_name(level.name)), Has(level.access_item))

    elif world.options.level_access == LevelAccess.option_open_rapture:
        access = {chapter.number: chapter.access_item for chapter in CHAPTERS}
        for number in FREE_CHAPTERS:
            world.set_rule(world.get_entrance(chapter_entrance_name(number)), Has(access[number]))
        # Ryan's chapter turns the story over, so the three before it have to be open first.
        world.set_rule(world.get_entrance(chapter_entrance_name(5)),
                       HasAll(access[5], *(access[number] for number in FREE_CHAPTERS)))
        for number in (6, 7):
            world.set_rule(world.get_entrance(chapter_entrance_name(number)), Has(access[number]))
        if not world.options.start_with_research_camera:
            # Cohen's masterpiece is made of photographs, and the camera lies in Neptune's Bounty.
            needs_camera = [story_location_name(m) for m in STORY_MILESTONES if m.level == FORT_FROLIC]
            needs_camera.append(level_completion_location_name(FORT_FROLIC))
            present = {location.name for location in world.multiworld.get_locations(world.player)}
            for name in needs_camera:
                if name in present:
                    world.set_rule(world.get_location(name), Has(NEPTUNES_BOUNTY_ACCESS))

    world.set_completion_rule(Has(VICTORY_ITEM))

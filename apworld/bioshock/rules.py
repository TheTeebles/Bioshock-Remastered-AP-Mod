from __future__ import annotations

from typing import TYPE_CHECKING

from rule_builder.rules import Has, HasAll

from .data import (
    CHAPTERS, FREE_CHAPTERS, LEVELS, NEPTUNES_BOUNTY_ACCESS, TELEKINESIS, VICTORY_ITEM,
)
from .items import world_pickups_shuffled
from .options import LevelAccess
from .regions import DEBRIS_ENTRANCE, chapter_entrance_name, travel_entrance_name

if TYPE_CHECKING:
    from .world import BioShockWorld


def items_to_reach_chapter(world: BioShockWorld, number: int) -> frozenset[str]:
    """Open Rapture: every item the way into a chapter needs, the chapters before it on the way included (the rules
    below; chapter 1 needs none)."""
    access = {chapter.number: chapter.access_item for chapter in CHAPTERS}
    if number in FREE_CHAPTERS:
        camera_chapter = 4 if not world.options.start_with_research_camera else None
        return frozenset({access[number], NEPTUNES_BOUNTY_ACCESS} if number == camera_chapter else {access[number]})
    if number == 5:
        return frozenset({access[5], *(access[free] for free in FREE_CHAPTERS)})
    if number in (6, 7):
        return items_to_reach_chapter(world, number - 1) | {access[number]}
    return frozenset()


def set_all_rules(world: BioShockWorld) -> None:
    if world_pickups_shuffled(world):  # Telekinesis comes from the multiworld, and the debris needs it
        world.set_rule(world.get_entrance(DEBRIS_ENTRANCE), Has(TELEKINESIS))

    if world.options.level_access == LevelAccess.option_access_items:
        for level in LEVELS:
            if level.access_item:
                world.set_rule(world.get_entrance(travel_entrance_name(level.name)), Has(level.access_item))

    elif world.options.level_access == LevelAccess.option_open_rapture:
        access = {chapter.number: chapter.access_item for chapter in CHAPTERS}
        camera_chapter = 4 if not world.options.start_with_research_camera else None
        for number in FREE_CHAPTERS:
            if number == camera_chapter:
                # Cohen opens Fort Frolic past Fleet Hall only for photographs, and the camera lies in Neptune's
                # Bounty. Most of the chapter's checks are past it, so the whole chapter needs Neptune's Bounty.
                world.set_rule(world.get_entrance(chapter_entrance_name(number)),
                               HasAll(access[number], NEPTUNES_BOUNTY_ACCESS))
            else:
                world.set_rule(world.get_entrance(chapter_entrance_name(number)), Has(access[number]))
        # Ryan's chapter turns the story over, so the three before it have to be open first.
        world.set_rule(world.get_entrance(chapter_entrance_name(5)),
                       HasAll(access[5], *(access[number] for number in FREE_CHAPTERS)))
        for number in (6, 7):
            world.set_rule(world.get_entrance(chapter_entrance_name(number)), Has(access[number]))

    world.set_completion_rule(Has(VICTORY_ITEM))

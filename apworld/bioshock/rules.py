from __future__ import annotations

from typing import TYPE_CHECKING

from rule_builder.rules import Has

from .data import LEVELS, VICTORY_ITEM
from .options import LevelAccess
from .regions import travel_entrance_name

if TYPE_CHECKING:
    from .world import BioShockWorld


def set_all_rules(world: BioShockWorld) -> None:
    if world.options.level_access == LevelAccess.option_access_items:
        for level in LEVELS:
            if level.access_item:
                world.set_rule(world.get_entrance(travel_entrance_name(level.name)), Has(level.access_item))

    world.set_completion_rule(Has(VICTORY_ITEM))

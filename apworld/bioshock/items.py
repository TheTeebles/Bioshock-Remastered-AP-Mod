from __future__ import annotations

from typing import TYPE_CHECKING

from BaseClasses import Item, ItemClassification

from .data import (
    ACCESS_ITEMS, CHARACTER_UPGRADES, FILLER_ITEMS, GAME_NAME, ITEM_TABLE, PLASMIDS, TONICS, TRAP_ITEMS, ItemKind,
    Source,
)
from .options import LevelAccess

if TYPE_CHECKING:
    from .world import BioShockWorld


class BioShockItem(Item):
    game = GAME_NAME


ITEM_NAME_TO_ID: dict[str, int] = {item.name: item.id for item in ITEM_TABLE}
ITEM_KIND: dict[str, str] = {item.name: item.kind for item in ITEM_TABLE}

_DEFAULT_CLASSIFICATION: dict[str, ItemClassification] = {
    ItemKind.LEVEL_ACCESS: ItemClassification.progression,
    ItemKind.PLASMID: ItemClassification.useful,
    ItemKind.TONIC: ItemClassification.useful,
    ItemKind.WEAPON: ItemClassification.useful,
    ItemKind.WEAPON_UPGRADE: ItemClassification.useful,
    ItemKind.CHARACTER_UPGRADE: ItemClassification.useful,
    ItemKind.FILLER: ItemClassification.filler,
    ItemKind.TRAP: ItemClassification.trap,
}


def _build_item_groups() -> dict[str, set[str]]:
    groups: dict[str, set[str]] = {}
    for item in ITEM_TABLE:
        groups.setdefault(item.kind, set()).add(item.name)
        if item.track:
            groups.setdefault(f"{item.track} Tonics", set()).add(item.name)
    groups["Access"] = set(ACCESS_ITEMS)
    groups["ADAM"] = {name for name, _ in FILLER_ITEMS if name.endswith("ADAM")}
    groups["Money"] = {name for name, _ in FILLER_ITEMS if name.startswith("$")}
    return groups


ITEM_NAME_GROUPS: dict[str, set[str]] = _build_item_groups()


def create_item(world: BioShockWorld, name: str) -> BioShockItem:
    classification = _DEFAULT_CLASSIFICATION[ITEM_KIND[name]]
    return BioShockItem(name, classification, ITEM_NAME_TO_ID[name], world.player)


def get_random_filler_item_name(world: BioShockWorld) -> str:
    if world.random.randrange(100) < world.options.trap_chance:
        names, weights = zip(*TRAP_ITEMS)
    else:
        names, weights = zip(*FILLER_ITEMS)
    return world.random.choices(names, weights=weights)[0]


def progression_item_names(world: BioShockWorld) -> list[str]:
    """The items the logic depends on."""
    return list(ACCESS_ITEMS) if world.options.level_access == LevelAccess.option_access_items else []


def useful_item_groups() -> tuple[list[str], ...]:
    """Every useful item this world can add to the pool, most wanted group first.

    Pickups the game hands out at a fixed spot (Source.WORLD) stay vanilla for now: the client cannot stop the game
    from giving them, so shuffling them would only create duplicates. Everything bought, crafted, researched or
    gifted is a candidate.
    """
    plasmids = [plasmid.item_name for plasmid in PLASMIDS for _, source, _ in plasmid.levels if source != Source.WORLD]
    upgrades = [name for name, count in CHARACTER_UPGRADES for _ in range(count)]
    tonics = [tonic.name for tonic in TONICS if tonic.source != Source.WORLD]
    return plasmids, upgrades, tonics


def pick_useful_items(world: BioShockWorld, room: int) -> list[str]:
    """As many useful items as there is room for. When they do not all fit, the last group is thinned out first,
    at random. Nothing is lost for the player: whatever is left out can still be bought, crafted or researched in
    the game as usual."""
    kept: list[str] = []
    for group in useful_item_groups():
        space = room - len(kept)
        if space <= 0:
            break
        kept += group if len(group) <= space else world.random.sample(group, space)
    return kept


def create_all_items(world: BioShockWorld, open_locations: int) -> None:
    """Fill the pool. `open_locations` is how many of this slot's checks may hold progression and useful items.

    Archipelago never puts those on filler-only checks, so this slot must not make more of them than it has room
    for. Otherwise a game with few players (or one) could not be filled.
    """
    progression = progression_item_names(world)
    names = progression + pick_useful_items(world, open_locations - len(progression))
    itempool = [world.create_item(name) for name in names]

    unfilled = len(world.multiworld.get_unfilled_locations(world.player))
    assert len(itempool) <= unfilled, f"{world.player_name}: {len(itempool)} items for {unfilled} locations"
    itempool += [world.create_filler() for _ in range(unfilled - len(itempool))]
    world.multiworld.itempool += itempool

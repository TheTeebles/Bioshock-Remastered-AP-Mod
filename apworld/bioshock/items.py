from __future__ import annotations

from typing import TYPE_CHECKING

from BaseClasses import Item, ItemClassification

from .data import (
    ACCESS_ITEMS, CHAPTER_ACCESS_ITEMS, CHAPTER_OF_LEVEL, CHARACTER_UPGRADES, LEVEL_BY_NAME, FILLER_ITEMS, GAME_NAME,
    ITEM_TABLE, PLASMIDS, RESEARCH_AWARDS, TELEKINESIS, TONICS, TRAP_ITEMS, WEAPON_UPGRADES, ItemKind, Source,
    shuffled_world_pickups,
)
from .options import LevelAccess, ResearchChecks, WorldItemChecks

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
    if name == TELEKINESIS and world_pickups_shuffled(world):
        classification = ItemClassification.progression  # the debris in front of Surgery needs it
    return BioShockItem(name, classification, ITEM_NAME_TO_ID[name], world.player)


def get_random_filler_item_name(world: BioShockWorld) -> str:
    if world.random.randrange(100) < world.options.trap_chance:
        names, weights = zip(*TRAP_ITEMS)
    else:
        names, weights = zip(*FILLER_ITEMS)
    return world.random.choices(names, weights=weights)[0]


def progression_item_names(world: BioShockWorld) -> list[str]:
    """The items the logic depends on."""
    telekinesis = [TELEKINESIS] if world_pickups_shuffled(world) else []
    if world.options.level_access == LevelAccess.option_open_rapture:
        return list(CHAPTER_ACCESS_ITEMS) + telekinesis
    if world.options.level_access == LevelAccess.option_access_items:
        return list(ACCESS_ITEMS) + telekinesis
    return telekinesis


def access_item_of(world: BioShockWorld, level: str) -> str | None:
    """The item a level's checks wait for: per level with Access Items, per chapter with Open Rapture."""
    if world.options.level_access == LevelAccess.option_open_rapture:
        return CHAPTER_OF_LEVEL[level].access_item
    if world.options.level_access == LevelAccess.option_access_items:
        return LEVEL_BY_NAME[level].access_item
    return None


def stations_shuffled(world: BioShockWorld) -> bool:
    """Whether the weapon upgrades come from the multiworld: then the stations are checks with no upgrade menu."""
    return bool(world.options.power_to_the_people_checks)


_PLASMID_ITEM: dict[str, str] = {name: plasmid.item_name for plasmid in PLASMIDS for name, _, _ in plasmid.levels}


def world_pickups_shuffled(world: BioShockWorld) -> bool:
    return world.options.world_item_checks == WorldItemChecks.option_shuffled


def world_pickup_items(world: BioShockWorld | None = None) -> list[str]:
    """The items of the pickups that give a bag of chips instead, which only the multiworld hands out then.
    Telekinesis is one of them, but a progression item (progression_item_names)."""
    if world is None or not world_pickups_shuffled(world):
        return []
    return [_PLASMID_ITEM.get(spot.vanilla, spot.vanilla) for spot in shuffled_world_pickups()
            if spot.vanilla != TELEKINESIS]


def research_awards_shuffled(world: BioShockWorld) -> bool:
    """With research checks, research gives chips and its tonics come from the multiworld."""
    return world.options.research_checks != ResearchChecks.option_off


def required_useful_items(world: BioShockWorld) -> list[str]:
    """Useful items the game no longer gives by itself, so the pool must hold every one of them."""
    return ((list(WEAPON_UPGRADES) if stations_shuffled(world) else []) + world_pickup_items(world)
            + (list(RESEARCH_AWARDS) if research_awards_shuffled(world) else []))


def useful_item_groups(world: BioShockWorld | None = None) -> tuple[list[str], ...]:
    """Every useful item this world can add to the pool, most wanted group first.

    Pickups the game hands out at a fixed spot (Source.WORLD) stay vanilla unless World Item Checks shuffles them:
    otherwise the game still gives them, and shuffling them would only create duplicates. Everything bought,
    crafted, researched or gifted is a candidate. The items the game no longer gives (weapon upgrades when the
    stations are checks, shuffled pickups) come first, since nothing else in the game does.
    """
    plasmids = [plasmid.item_name for plasmid in PLASMIDS for _, source, _ in plasmid.levels if source != Source.WORLD]
    upgrades = [name for name, count in CHARACTER_UPGRADES for _ in range(count)]
    required = required_useful_items(world) if world is not None else []
    tonics = [tonic.name for tonic in TONICS if tonic.source != Source.WORLD and tonic.name not in required]
    return required, plasmids, upgrades, tonics


def pick_useful_items(world: BioShockWorld, room: int) -> list[str]:
    """As many useful items as there is room for. When they do not all fit, the last group is thinned out first,
    at random. Nothing is lost for the player: whatever is left out can still be bought, crafted or researched in
    the game as usual."""
    kept: list[str] = []
    for group in useful_item_groups(world):
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

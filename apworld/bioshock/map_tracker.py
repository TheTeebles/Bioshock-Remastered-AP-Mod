"""
Universal Tracker's map tab: the game's own map screen, one page per floor and one overview per level, with a pin on
every check that has a place.

The pages and pins in tracker/ are made by client-poc/tracker/build_tracker.py from the game's map files and its map
screen (Maps.swf). Each pin's sections are named exactly like the AP locations, so Universal Tracker colours them with
this world's own logic and only shows the ones in the seed.

The BioShock Client tells the tracker where the player is through data storage: MAP_KEY holds the floor the player is
on (the map screen's LayerName, "Fisheries03Marker"), which turns the tab to that floor, and POSITION_KEY holds
{"layer", "x", "y"} in world units, which places a dot on whichever page shows that floor.
"""
from __future__ import annotations

import json
import pkgutil
from functools import cache
from typing import Any

FOLDER = "tracker"
MAP_KEY = "bioshock_map_{team}_{player}"
POSITION_KEY = "bioshock_position_{team}_{player}"
PLAYER_ICON = "images/player.png"


@cache
def _read(path: str) -> Any:
    raw = pkgutil.get_data(__package__, f"{FOLDER}/{path}")
    return json.loads(raw) if raw else None


def pages() -> list[dict[str, Any]]:
    return _read("maps/maps.json") or []


def regions() -> dict[str, dict[str, str]]:
    """Map (as game_data.normalize_map spells it) -> map screen region (lower case) -> the floor it is drawn on."""
    return _read("regions.json") or {}


def page_of_floor(layer: Any) -> int:
    """The page that shows this floor on its own; -1 (stay on the page shown) for anything else."""
    if isinstance(layer, str) and layer:
        for index, page in enumerate(pages()):
            if list(page.get("bioshock_layers", {})) == [layer]:
                return index
    return -1


def player_on_page(index: int, position: Any) -> tuple[int, int, str] | None:
    """Where the player's dot goes on this page, or None when the page does not show the player's floor."""
    # Universal Tracker asks before any page is picked, with no index (None) then.
    if not isinstance(position, dict) or not isinstance(index, int) or not 0 <= index < len(pages()):
        return None
    transform = pages()[index].get("bioshock_layers", {}).get(position.get("layer"))
    x, y = position.get("x"), position.get("y")
    if transform is None or not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    a, kx, b, ky = transform
    return round(a + x * kx), round(b + y * ky), PLAYER_ICON


def tracker_world() -> dict[str, Any]:
    """The tracker_world Universal Tracker reads off the world class; empty when the tracker files are missing."""
    groups = _read("groups.json")
    if not pages() or not groups:
        return {}
    return {
        "map_page_folder": FOLDER,
        "map_page_maps": ["maps/maps.json"],
        "map_page_locations": ["locations/locations.json"],
        "map_page_groups": [(level, names) for level, names in groups],
        "map_page_setting_key": MAP_KEY,
        "map_page_index": page_of_floor,
        "location_setting_key": POSITION_KEY,
        "location_icon_coords": player_on_page,
    }

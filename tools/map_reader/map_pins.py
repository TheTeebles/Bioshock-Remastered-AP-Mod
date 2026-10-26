"""
Reads BioShock Remastered map files (.bsm) and lists what the map tracker needs to pin each check on the game's own
map screen:

- floors: each MapUILayerMarker (one floor of the in-game map): its LayerName, world position and the MapUIRegions
  drawn on it;
- scales: the MapUILayerScaleMarkers' world positions (with the floor's marker, they calibrate world to map picture);
- actors: every actor a check can sit on (audio diaries, plasmid/tonic/weapon pickups, safes, Gardens, Power to the
  People stations, Little Sister vents and Big Daddy spawners, bathysphere switches, player starts), with its
  position and the MapUIRegion of the zone it stands in (Region.Zone -> ZoneInfo.MapUIRegion), which names its floor.
  Audio diaries carry the class of the log they give.

Only reads; Python 3.8+, nothing to install. Needs map_stock.py and map_survey.py next to it.

    python map_pins.py "E:\\SteamLibrary\\steamapps\\common\\BioShock Remastered" > map_pins.json

Read from 2-Fisheries.bsm on 2026-10-19: 5 floors (Fisheries01Marker ... Fisheries05Marker), 5 scale markers.
"""
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from map_stock import SIZES, compact  # noqa: E402
from map_survey import KNOWN, SKIP_MAPS, Ref, Survey, is_world_item, position  # noqa: E402

PINNED = ("LogPickup", "PlaceableGrowthStation", "PlaceableWeaponUpgradeStation", "PlacedGathererVent",
          "ProtectorSpawner", "BathysphereSwitch", "PlayerStart")


def names_array(pkg, b):
    """An array of names (compact count, then compact name index + int32 number each), as the game spells them."""
    count, p = compact(b, 0)
    out = []
    for _ in range(count):
        index, p = compact(b, p)
        number = struct.unpack_from("<i", b, p)[0]
        p += 4
        out.append(numbered(pkg.names[index], number))
    return out


def numbered(name, number):
    """A name with its number the way Maps.swf spells region sprites: FontaineFreezerHall + 2 = FontaineFreezerHall1."""
    return name + (str(number - 1) if number else "")


class Pins(Survey):
    """As Survey, but name properties keep their number (map_survey drops it): two zones can be FontaineFreezerHall
    1 and 2."""

    def props(self, i):
        """As Survey's, but the map screen's properties count as known ones. In 3-Arcadia.bsm a floor marker's bytes
        also parse from an earlier start into junk plus every property but RegionNames, one more property than the
        right parse, which Survey's scoring would pick. (Ranking them above the other known properties instead lost
        the audio diaries' LootSlot in 2-SubBay.bsm.)"""
        if i not in self._props:
            b = self.body(self.exports[i - 1])
            best, best_score = {}, None
            for start in range(0, min(len(b), 96)):
                try:
                    found, _ = self.properties(b, start)
                except (ValueError, IndexError, struct.error, UnicodeDecodeError):
                    continue
                score = (len((MAP_PROPERTIES | KNOWN) & found.keys()), len(found))
                if best_score is None or score > best_score:
                    best, best_score = found, score
            self._props[i] = best
        return self._props[i]

    def properties(self, b, p):
        out, end = super().properties(b, p)
        for name, value in list(out.items()):
            if name in NAME_PROPERTIES and isinstance(value, str):
                out[name] = self._numbered(b, p, name) or value
        return out, end

    def _numbered(self, b, p, wanted):
        while p < len(b):
            n, p = compact(b, p)
            p += 4
            name = self.names[n]
            if name == "None":
                return None
            info = b[p]
            p += 1
            kind, size_code, flag = info & 0xf, (info >> 4) & 7, info >> 7
            if kind == 10:
                _, p = compact(b, p)
                p += 4
            if size_code in SIZES:
                size = SIZES[size_code]
            elif size_code == 5:
                size, p = b[p], p + 1
            elif size_code == 6:
                size, p = struct.unpack_from("<H", b, p)[0], p + 2
            else:
                size, p = struct.unpack_from("<i", b, p)[0], p + 4
            if flag and kind != 3:
                p += 1
            if name == wanted and kind == 6:
                index, q = compact(b, p)
                return numbered(self.names[index], struct.unpack_from("<i", b, q)[0] if q + 4 <= p + size else 0)
            p += size
        return None


NAME_PROPERTIES = frozenset({"MapUIRegion", "LayerName", "ScaleMarkerName"})
MAP_PROPERTIES = NAME_PROPERTIES | {"RegionNames", "Location", "Region"}


def zone_of(pkg, found):
    region = found.get("Region")
    if not isinstance(region, bytes):
        return None
    try:
        zone = pkg.properties(region, 0)[0].get("Zone")
    except (ValueError, IndexError, struct.error):
        return None
    return int(zone) if isinstance(zone, Ref) and zone > 0 else None


def pins(path):
    pkg = Pins(path.read_bytes())
    result = {"map": path.name, "floors": [], "scales": [], "actors": []}
    zone_region = {}
    for i, e in enumerate(pkg.exports, 1):
        if pkg.class_of(e) == "ZoneInfo":
            region = pkg.props(i).get("MapUIRegion")
            if isinstance(region, str) and region != "None":
                zone_region[i] = region
    for i, e in enumerate(pkg.exports, 1):
        if e["outer"] != 0:
            continue
        cls = pkg.class_of(e)
        if cls == "MapUILayerMarker":
            found = pkg.props(i)
            regions = found.get("RegionNames")
            result["floors"].append({"layer": found.get("LayerName"), "actor": e["name"],
                                     "at": position(found.get("Location")),
                                     "scale": found.get("ScaleMarkerName"),
                                     "regions": names_array(pkg, regions) if isinstance(regions, bytes) else []})
        elif cls == "MapUILayerScaleMarker":
            found = pkg.props(i)
            result["scales"].append({"actor": e["name"], "at": position(found.get("Location"))})
        elif cls in PINNED or is_world_item(cls):
            found = pkg.props(i)
            row = {"class": cls, "actor": e["name"], "at": position(found.get("Location")),
                   "region": zone_region.get(zone_of(pkg, found))}
            if found.get("Label"):
                row["label"] = found["Label"]
            if cls == "LogPickup":
                row["items"] = sorted({item for item, _, _ in pkg.loot(i) if item})
            result["actors"].append(row)
    result["floors"].sort(key=lambda floor: str(floor["layer"]))
    return result


def main():
    paths = []
    for arg in sys.argv[1:] or ["."]:
        path = Path(arg)
        paths += sorted(path.rglob("*.bsm"), key=lambda p: p.name.lower()) if path.is_dir() else [path]
    results = []
    for path in paths:
        if path.stem.lower().endswith(SKIP_MAPS) or "_" in path.stem:  # the localized copies hold no actors
            continue
        try:
            results.append(pins(path))
        except Exception as error:  # one odd file should not stop the rest
            results.append({"map": path.name, "error": f"{type(error).__name__}: {error}"})
        print(f"read {path.name}", file=sys.stderr)
    json.dump(results, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()

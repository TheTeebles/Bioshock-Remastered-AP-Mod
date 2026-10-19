"""
Builds the map tracker that ships in the apworld (apworld/bioshock/tracker/): Universal Tracker's map tab, in
PopTracker's format.

    python build_tracker.py map_pins.json mapsPC.swf ../../apworld/bioshock/tracker

- map_pins.json: what client-poc/map_reader/map_pins.py read from the game's map files (floors, calibration markers
  and the actors checks sit on).
- mapsPC.swf: the game's map screen (ContentBaked/PC/FlashMovies). Every MapUIRegion is a sprite drawn in its floor's
  coordinates, and every floor is a sprite named like the map file's LayerName holding a PositionalMarker (the floor
  marker's world position) and a ScaleMarker (its scale marker's). Picture = PositionalMarker + (world - floor
  marker) * k on each axis.

Writes images/ (one picture per floor and one overview per level, floors side by side), maps/maps.json,
locations/locations.json, and prints which checks have no pin. Each pin's sections are named exactly like the AP
locations, which is how Universal Tracker colours them with the world's own logic. Needs Pillow, and Chromium to turn
the vector art into pictures (CHROMIUM, else Playwright's).
"""
from __future__ import annotations

import importlib
import json
import math
import os
import subprocess
import sys
import tempfile
import types
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from shape import parse, svg  # noqa: E402
from swfscan import index, sprite_children  # noqa: E402


def _load_world_tables():
    """bioshock.data and bioshock.client.game_data, without the package's __init__ (which needs Archipelago)."""
    apworld = HERE.parent.parent / "apworld"
    for name, folder in (("bioshock", apworld / "bioshock"), ("bioshock.client", apworld / "bioshock" / "client")):
        if name not in sys.modules:
            package = types.ModuleType(name)
            package.__path__ = [str(folder)]
            sys.modules[name] = package
    return importlib.import_module("bioshock.data"), importlib.import_module("bioshock.client.game_data")


data, game_data = _load_world_tables()

SCALE = 3.0  # picture pixels per unit of the map screen's stage
VIEW = (-600, -600, 2000, 1800)  # the stage area rendered before cropping: x, y, width, height
MARGIN = 24  # pixels kept around a floor's art
BACKGROUND = (17, 17, 17)
OVERVIEW_WIDTH = 2400  # an overview is shrunk to fit this
LABEL_HEIGHT = 40
FLOOR_PIN = 30  # location_size on a floor's page, in picture pixels
OVERVIEW_PIN = 16
PIN_BORDER = 3

WEAPON_NAMES = {"MachineGun": "Machine Gun", "Shotgun": "Shotgun", "Pistol": "Pistol",
                "GrenadeLauncher": "Grenade Launcher", "ChemicalThrower": "Chemical Thrower", "Crossbow": "Crossbow",
                "ResearchCamera": "Research Camera"}
LEVEL_PIN_ANCHORS = ("BathysphereSwitch", "PlayerStart")  # where a level's checks without a place go, best first


def tf(m):
    return "matrix(%g %g %g %g %g %g)" % tuple(m)


class MapArt:
    def __init__(self, path):
        self.b, self.defs, self.ex = index(str(path))
        self.lower = {name.lower(): cid for name, cid in self.ex.items()}
        self.cache = {}

    def floor_markers(self, layer):
        kids = {c.get("name"): c["m"] for c in sprite_children(self.b, self.defs, self.ex[layer]) if "m" in c}
        return kids["PositionalMarker"][4:], kids["ScaleMarker"][4:]

    def render(self, cid, depth=0):
        if cid not in self.defs or depth > 6:
            return []
        code, q, _ = self.defs[cid]
        if code in (2, 22, 32, 83):
            if cid not in self.cache:
                try:
                    paths = svg(parse(self.b, q, code))
                except Exception as error:  # an odd shape loses itself, not the floor
                    paths = [f"<!-- shape {cid}: {error} -->"]
                # Each room gets a thin outline, so neighbouring rooms show their walls.
                self.cache[cid] = [p.replace('fill-rule="evenodd"/>', 'fill-rule="evenodd" stroke="#6b5636" '
                                             'stroke-width="0.6"/>') for p in paths]
            return self.cache[cid]
        if code != 39:
            return []
        out = []
        for child in sprite_children(self.b, self.defs, cid):
            name = child.get("name", "")
            if "char" not in child or name in ("PositionalMarker", "ScaleMarker") or name.startswith("TextBox"):
                continue
            inner = self.render(child["char"], depth + 1)
            if inner:
                out.append('<g transform="%s">%s</g>' % (tf(child.get("m", (1, 0, 0, 1, 0, 0))), "".join(inner)))
        return out

    def floor_svg(self, regions):
        body, missing = [], []
        for region in regions:
            cid = self.lower.get(region.lower())
            if cid is None:
                missing.append(region)
            else:
                body += self.render(cid)
        x, y, w, h = VIEW
        doc = ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="%d %d %d %d" '
               'style="background:#111">%s</svg>' % (w * SCALE, h * SCALE, x, y, w, h, "".join(body)))
        return doc, missing


def chromium():
    found = os.environ.get("CHROMIUM")
    if found:
        return found
    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))
    for candidate in sorted(root.glob("chromium-*/chrome-linux/chrome"), reverse=True):
        return str(candidate)
    raise SystemExit("No Chromium found: set CHROMIUM to its executable.")


def rasterize(svg_text, work):
    source = work / "floor.svg"
    target = work / "floor.png"
    source.write_text(svg_text)
    _, _, w, h = VIEW
    subprocess.run([chromium(), "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                    f"--window-size={int(w * SCALE)},{int(h * SCALE)}", f"--screenshot={target}",
                    source.as_uri()], check=True, capture_output=True, timeout=120)
    return Image.open(target).convert("RGB")


def crop_box(image):
    mask = Image.eval(image.convert("L"), lambda v: 255 if v > 40 else 0)
    box = mask.getbbox()
    if box is None:
        return None
    left, top, right, bottom = box
    return (max(left - MARGIN, 0), max(top - MARGIN, 0), min(right + MARGIN, image.width),
            min(bottom + MARGIN, image.height))


def save_png(image, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    image.quantize(colors=48, method=Image.Quantize.MEDIANCUT).save(path, optimize=True)


def calibrate(floor, scales, art):
    """The scale its own markers give a floor, (kx, ky), or None; and the picture position of the floor marker."""
    (px, py), (sx, sy) = art.floor_markers(floor["layer"])
    lx, ly = floor["at"][:2]
    scale = scales.get(floor.get("scale"))
    if scale is None or scale[0] == lx or scale[1] == ly:
        return None, (px, py)
    return ((sx - px) / (scale[0] - lx), (sy - py) / (scale[1] - ly)), (px, py)


def level_scale(calibrations):
    """One scale for all of a level's floors: the median of what their scale markers say. The art of a level is drawn
    at one scale, and the markers were placed by hand: most agree to 1%, some are 5 to 30% off on one axis (Hephaestus
    floor 4, Apollo Square floor 4), one has both axes negative (Olympus Heights floor 7)."""
    values = sorted(abs(v) for k, _ in calibrations.values() if k is not None for v in k)
    return values[len(values) // 2] if values else None


def level_of_map(name):
    return game_data.MAP_LEVEL.get(game_data.normalize_map(name))


def floor_number(layer):
    digits = "".join(ch for ch in layer if ch.isdigit())
    return int(digits) if digits else 0


# ----------------------------------------------------------------------------------------------------------------
# Which checks sit on which actor
# ----------------------------------------------------------------------------------------------------------------

def location_names():
    return {location.id: location.name for location in data.LOCATION_TABLE}


def pins_for_level(level, map_name, actors):
    """[(pin title, [AP location names], actor rows)] for one level's map."""
    names = location_names()
    by_level = defaultdict(list)
    for location in data.LOCATION_TABLE:
        by_level[location.level, location.category].append(location.name)
    out = []
    diary_title = {d.number: d for d in data.AUDIO_DIARIES}
    for actor in actors:
        if actor["class"] == "LogPickup":
            for item in actor.get("items", []):
                number = game_data.DIARY_CLASSES.get(item)
                if number is not None and diary_title[number].level == level:
                    diary = diary_title[number]
                    out.append((f"Audio Diary: {diary.title}", [data.audio_diary_location_name(diary)], [actor]))
    spots = {(spot.map, spot.actor): spot for spot in data.WORLD_SPOTS if spot.actor}
    for actor in actors:
        spot = spots.get((game_data.normalize_map(map_name), actor["actor"]))
        if spot is not None:
            name = names[spot.id]
            out.append((name.split(" - ", 1)[1], [name], [actor]))
    for spot in data.WORLD_SPOTS:
        if spot.kind == data.SpotKind.WEAPON and spot.level == level:
            wanted = [a for a in actors if is_weapon_pickup(a["class"]) and
                      WEAPON_NAMES.get(weapon_of(a["class"])) == spot.vanilla]
            if wanted:
                out.append((f"Weapon: {spot.vanilla}", [names[spot.id]], wanted))
    gardens = [a for a in actors if a["class"] == "PlaceableGrowthStation"]
    garden_checks = by_level[level, data.LocationCategory.GATHERERS_GARDEN]
    if gardens and garden_checks:
        out.append(("Gatherer's Garden", garden_checks, gardens))
    stations = [a for a in actors if a["class"] == "PlaceableWeaponUpgradeStation"]
    station_checks = by_level[level, data.LocationCategory.POWER_TO_THE_PEOPLE]
    if stations and station_checks:
        # Station k is the k-th one used, wherever it stands, so each station carries them all.
        out.append(("Power to the People", station_checks, stations))
    anywhere = (by_level[level, data.LocationCategory.STORY] + by_level[level, data.LocationCategory.LITTLE_SISTER]
                + by_level[level, data.LocationCategory.LEVEL_COMPLETION])
    anchor = next((a for kind in LEVEL_PIN_ANCHORS for a in actors if a["class"] == kind and a.get("floor")), None)
    if anywhere and anchor is not None:
        out.append((f"{level}: story, Little Sisters, level complete", anywhere, [anchor]))
    return out


def is_weapon_pickup(cls):
    return ("And" in cls and cls.endswith("Pickup")) or cls in {f"{w}Pickup" for w in WEAPON_NAMES}


def weapon_of(cls):
    return cls.split("And", 1)[0] if "And" in cls else cls[:-len("Pickup")]


def assign_floors(level_map, pages):
    """Each actor's floor, from its zone's map region. An actor in no region (a few stand in zones the map screen does
    not draw) goes on a floor whose art lies under it, the one with the nearest placed actor; else on the floor of the
    nearest placed actor."""
    floor_of_region = {region.lower(): floor["layer"] for floor in level_map["floors"] for region in floor["regions"]}
    placed = []
    for actor in level_map["actors"]:
        actor["floor"] = floor_of_region.get((actor.get("region") or "").lower())
        if actor["floor"] in pages and actor["at"]:
            placed.append(actor)
    for actor in level_map["actors"]:
        if actor["floor"] in pages or not actor["at"] or not placed:
            continue
        under = {layer for layer, page in pages.items() if on_art(page[0], page[1](actor["at"]))}
        nearby = [other for other in placed if other["floor"] in under] or placed
        actor["floor"] = min(nearby, key=lambda other: math.dist(other["at"], actor["at"]))["floor"]


def on_art(picture, at, reach=6):
    x, y = round(at[0]), round(at[1])
    for dx in range(-reach, reach + 1, 3):
        for dy in range(-reach, reach + 1, 3):
            if 0 <= x + dx < picture.width and 0 <= y + dy < picture.height and \
                    sum(picture.getpixel((x + dx, y + dy))) > 3 * 60:
                return True
    return False


# ----------------------------------------------------------------------------------------------------------------
# Pages
# ----------------------------------------------------------------------------------------------------------------

def build(pins_path, swf_path, out):
    art = MapArt(swf_path)
    maps_json, locations_json, groups = [], [], []
    regions = {}  # map -> MapUIRegion (lower case) -> floor, for the client to say which floor the player is on
    pinned = set()
    report = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        levels = [m for m in json.loads(Path(pins_path).read_text()) if level_of_map(m["map"]) and "error" not in m]
        levels.sort(key=lambda m: data.LEVEL_NAMES.index(level_of_map(m["map"])))
        for level_map in levels:
            level = level_of_map(level_map["map"])
            stem = game_data.normalize_map(level_map["map"])
            scales = {s["actor"]: s["at"] for s in level_map["scales"]}
            floors = [f for f in level_map["floors"] if f["layer"] in art.ex and f["at"]]
            calibrations = {f["layer"]: calibrate(f, scales, art) for f in floors}
            shared = level_scale(calibrations)
            if shared is None:
                report.append(f"{level}: no floor could be calibrated, skipped")
                continue
            pages = {}  # layer -> (picture, transform: world (x, y) -> picture (x, y))
            for floor in sorted(floors, key=lambda f: floor_number(f["layer"])):
                own, (px, py) = calibrations[floor["layer"]]
                if own is None or max(abs(abs(v) - shared) / shared for v in own) > 0.03:
                    report.append(f"{level}: {floor['layer']}'s scale markers disagree with the level's ({own})")
                k = (shared, shared)
                svg_text, missing = art.floor_svg(floor["regions"])
                if missing:
                    report.append(f"{level}: {floor['layer']} has no art for {', '.join(missing)}")
                picture = rasterize(svg_text, work)
                box = crop_box(picture)
                if box is None:
                    report.append(f"{level}: {floor['layer']} drew nothing ({', '.join(floor['regions'])})")
                    continue
                picture = picture.crop(box)
                lx, ly = floor["at"][:2]
                vx, vy = VIEW[:2]

                def to_picture(world, k=k, px=px, py=py, lx=lx, ly=ly, box=box):
                    stage_x, stage_y = px + (world[0] - lx) * k[0], py + (world[1] - ly) * k[1]
                    return (stage_x - vx) * SCALE - box[0], (stage_y - vy) * SCALE - box[1]
                pages[floor["layer"]] = (picture, to_picture, k, (px, py, lx, ly, box))

            if not pages:
                report.append(f"{level}: no floor drew anything, skipped")
                continue
            regions[stem] = {region.lower(): floor["layer"] for floor in floors for region in floor["regions"]
                             if floor["layer"] in pages}
            assign_floors(level_map, pages)
            level_pins = pins_for_level(level, level_map["map"], level_map["actors"])
            group = [level]
            overview, placement = compose_overview(level, pages)
            overview_name = level
            save_png(overview, out / "images" / f"{stem}.png")
            maps_json.append(page_entry(overview_name, f"images/{stem}.png", OVERVIEW_PIN, {
                layer: transform_entry(pages[layer], placement[layer]) for layer in pages}))
            for layer, (picture, *_rest) in pages.items():
                name = f"{level}: Floor {floor_number(layer)}"
                image = f"images/{stem}_{floor_number(layer)}.png"
                save_png(picture, out / image)
                maps_json.append(page_entry(name, image, FLOOR_PIN, {layer: transform_entry(pages[layer],
                                                                                              (0, 0, 1.0))}))
                group.append(name)
            groups.append((level, group))

            used = defaultdict(set)  # page -> picture positions taken, so no two pins sit on one spot
            for title, sections, actors in level_pins:
                map_locations = []
                for actor in actors:
                    if actor.get("floor") not in pages or not actor["at"]:
                        continue
                    picture = pages[actor["floor"]][0]
                    x, y = pages[actor["floor"]][1](actor["at"])
                    if not (0 <= x < picture.width and 0 <= y < picture.height):
                        report.append(f"{level}: {title} ({actor['actor']}) falls outside {actor['floor']}, no pin")
                        continue
                    map_locations.append(spot(used, f"{level}: Floor {floor_number(actor['floor'])}", x, y))
                    ox, oy, factor = placement[actor["floor"]]
                    map_locations.append(spot(used, overview_name, ox + x * factor, oy + y * factor))
                if map_locations:
                    locations_json.append({"name": title, "map_locations": map_locations,
                                           "sections": [{"name": section} for section in sections]})
                    pinned.update(sections)
    out.mkdir(parents=True, exist_ok=True)
    (out / "maps").mkdir(exist_ok=True)
    (out / "locations").mkdir(exist_ok=True)
    (out / "maps" / "maps.json").write_text(json.dumps(maps_json, indent=1) + "\n")
    (out / "locations" / "locations.json").write_text(json.dumps(locations_json, indent=1) + "\n")
    (out / "groups.json").write_text(json.dumps(groups, indent=1) + "\n")
    (out / "regions.json").write_text(json.dumps(regions, indent=1, sort_keys=True) + "\n")
    player_icon(out / "images" / "player.png")
    unpinned = [loc for loc in data.LOCATION_TABLE
                if loc.name not in pinned and loc.category != data.LocationCategory.RESEARCH]
    by_level = defaultdict(list)
    for loc in unpinned:
        by_level[loc.level].append(loc.name.split(" - ", 1)[1])
    for line in report:
        print(line)
    print(f"{len(pinned)} checks pinned; {len(unpinned)} without a pin (research left out):")
    for level, names in by_level.items():
        print(f"  {level} ({len(names)}): {'; '.join(names)}")


def player_icon(path):
    """Where the player is: a red dot with a white ring, drawn large and shrunk so its edge is smooth."""
    size = 160
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon)
    draw.ellipse((8, 8, size - 8, size - 8), fill=(255, 255, 255, 255))
    draw.ellipse((30, 30, size - 30, size - 30), fill=(220, 30, 30, 255))
    icon.resize((40, 40), Image.LANCZOS).save(path, optimize=True)


def spot(used, page, x, y):
    x, y = round(x), round(y)
    while (x, y) in used[page]:  # Universal Tracker keeps one pin per position
        x += 3
    used[page].add((x, y))
    return {"map": page, "x": x, "y": y}


def compose_overview(level, pages):
    """One picture of the level's floors side by side, each labelled, shrunk to fit OVERVIEW_WIDTH."""
    layers = list(pages)
    columns = max(1, math.ceil(math.sqrt(len(layers) * 1.5)))
    rows = [layers[i:i + columns] for i in range(0, len(layers), columns)]
    width = max(sum(pages[layer][0].width for layer in row) for row in rows)
    factor = min(1.0, OVERVIEW_WIDTH / width)
    placement = {}
    y = 0
    cells = []
    for row in rows:
        x = 0
        height = max(round(pages[layer][0].height * factor) for layer in row)
        for layer in row:
            picture = pages[layer][0]
            size = (round(picture.width * factor), round(picture.height * factor))
            cells.append((layer, picture.resize(size, Image.LANCZOS), x, y + LABEL_HEIGHT))
            placement[layer] = (x, y + LABEL_HEIGHT, factor)
            x += size[0]
        y += height + LABEL_HEIGHT
    canvas = Image.new("RGB", (max(c[2] + c[1].width for c in cells), y), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    font = label_font()
    for layer, picture, x, top in cells:
        canvas.paste(picture, (x, top))
        draw.text((x + 12, top - LABEL_HEIGHT + 6), f"Floor {floor_number(layer)}", fill=(230, 210, 170), font=font)
    return canvas, placement


def label_font():
    for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"):
        try:
            return ImageFont.truetype(name, 26)
        except OSError:
            continue
    return ImageFont.load_default()


def transform_entry(page, placement):
    """World (x, y) -> this page's picture: picture = (a + x * kx, b + y * ky), as [a, kx, b, ky]."""
    _, _, k, (px, py, lx, ly, box), = page
    ox, oy, factor = placement
    vx, vy = VIEW[:2]
    a = ox + factor * ((px - lx * k[0] - vx) * SCALE - box[0])
    b = oy + factor * ((py - ly * k[1] - vy) * SCALE - box[1])
    return [round(a, 2), round(factor * k[0] * SCALE, 6), round(b, 2), round(factor * k[1] * SCALE, 6)]


def page_entry(name, image, pin, layers):
    # "bioshock_layers" is ours (Universal Tracker and PopTracker skip keys they do not know): where the client's
    # position of the player lands on this page, by floor.
    return {"name": name, "img": image, "location_size": pin, "location_border_thickness": PIN_BORDER,
            "bioshock_layers": layers}


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    build(sys.argv[1], sys.argv[2], Path(sys.argv[3]))

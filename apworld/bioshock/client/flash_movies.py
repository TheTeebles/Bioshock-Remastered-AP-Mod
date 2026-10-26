"""
The Gatherer's Garden menu with the Archipelago icon, built without changing the game's folder.

The Garden's menu (craftingStationPC.swf, in ContentBaked/pc/FlashMovies) is a plain Flash 6 movie. It imports each
line's icon from sharedlibrary.swf by the name of the line's item class. APIcons.swf, shipped with the client, exports
one more icon under ICON_SYMBOL. The client writes a copy of the player's menu that also imports that icon into a
folder of its own, next to APIcons.swf, and the agent hands the game those two files when it opens them (it opens the
menus again on every load). A line whose class is named ICON_SYMBOL then shows the AP icon. Tried in the game on
2026-10-14.

tools/ap_icon/make_ap_icon.py builds APIcons.swf from the Archipelago logo.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path

MENU = "craftingStationPC.swf"
LIBRARY = "APIcons.swf"
FILES = (MENU, LIBRARY)
ICON_SYMBOL = "Item"  # the name the icon is exported under; also the class name the agent gives a line for it
# Where the menu imports the library from. The game opens it next to where it looked for the menu, and is handed
# APIcons.swf from the client's folder instead.
LIBRARY_PATH = f"..\\FlashMovies\\{LIBRARY}"
PATCH_VERSION = 1  # bump when the copy of the menu changes, so that copies already made are made again
MOVIES = ("ContentBaked", "pc", "FlashMovies")
STAMP = "source.txt"  # what the copy was made from

IMPORT_ASSETS = 57
IMPORT_ASSETS2 = 71
TEXTURE_TAG = 512  # BioShock's own: a texture (id, width, height, format, 0, texels)
# Tags whose body starts with the id of the character they define.
DEFINING = frozenset({2, 6, 7, 10, 11, 14, 20, 21, 22, 32, 33, 34, 35, 36, 37, 39, 46, 48, 60, 75, 83, 84, 87, 90,
                      91, TEXTURE_TAG})


class MoviesUnavailable(Exception):
    """The menu with the AP icon cannot be made. The message says why, in the player's words."""


def find_menu(exe: str) -> Path | None:
    """The game's own craftingStationPC.swf, found from where BioshockHD.exe is (Build/Final in the Steam build)."""
    folder = Path(exe).parent
    for candidate in [folder, *folder.parents][:5]:
        menu = candidate.joinpath(*MOVIES, MENU)
        if menu.is_file():
            return menu
    return None


def prepare(exe: str, cache: Path, library: bytes) -> Path:
    """Make sure `cache` holds APIcons.swf and a copy of the game's menu that imports from it. Returns `cache`.

    The copy is only made again when the game's menu changes (an update) or PATCH_VERSION does."""
    source = find_menu(exe)
    if source is None:
        raise MoviesUnavailable(f"{MENU} is not where it should be, near {exe}")
    try:
        cache.mkdir(parents=True, exist_ok=True)
        info = source.stat()
        stamp = f"{PATCH_VERSION} {info.st_size} {info.st_mtime_ns} {source}"
        stamp_file = cache / STAMP
        if not (cache / MENU).is_file() or _read_text(stamp_file) != stamp:
            _write(cache / MENU, import_into(source.read_bytes(), LIBRARY_PATH, ICON_SYMBOL))
            stamp_file.write_text(stamp, encoding="utf-8")
        if not (cache / LIBRARY).is_file() or (cache / LIBRARY).read_bytes() != library:
            _write(cache / LIBRARY, library)
    except OSError as error:
        raise MoviesUnavailable(f"could not write the Garden's menu to {cache}: {error}") from error
    return cache


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _write(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


# --------------------------------------------------------------------------------------------------------------------
# SWF
# --------------------------------------------------------------------------------------------------------------------

def tag(code: int, body: bytes) -> bytes:
    if len(body) < 0x3f and code != TEXTURE_TAG:
        return struct.pack("<H", (code << 6) | len(body)) + body
    return struct.pack("<HI", (code << 6) | 0x3f, len(body)) + body


def read_tags(data: bytes, pos: int, end: int):
    """(code, body, offset of its header) for each tag, up to and with the End tag."""
    while pos + 2 <= end:
        header = struct.unpack_from("<H", data, pos)[0]
        start = pos
        pos += 2
        code, length = header >> 6, header & 0x3f
        if length == 0x3f:
            length = struct.unpack_from("<I", data, pos)[0]
            pos += 4
        if pos + length > end:
            raise MoviesUnavailable(f"{MENU} ends in the middle of a tag")
        yield code, data[pos:pos + length], start
        pos += length
        if code == 0:
            return


def first_tag(data: bytes) -> int:
    """Where the tags start: after the signature, the length, the frame rectangle, the rate and the frame count."""
    bits = data[8] >> 3
    return 8 + (5 + 4 * bits + 7) // 8 + 4


def import_into(movie: bytes, library: str, name: str) -> bytes:
    """The movie, also importing `name` from `library` with its other imports, under an id of its own."""
    if movie[:3] != b"FWS" or len(movie) < 21:
        raise MoviesUnavailable(f"{MENU} is not a Flash movie the client knows how to change")
    tags = list(read_tags(movie, first_tag(movie), len(movie)))
    used = set()
    imports = []
    for code, body, start in tags:
        if code in DEFINING and len(body) >= 2:
            used.add(struct.unpack_from("<H", body)[0])
        if code in (IMPORT_ASSETS, IMPORT_ASSETS2):  # imported characters take ids too
            imports.append(start + _header_size(movie, start) + len(body))
            p = body.index(b"\0") + 1 + (2 if code == IMPORT_ASSETS2 else 0)
            count, p = struct.unpack_from("<H", body, p)[0], p + 2
            for _ in range(count):
                used.add(struct.unpack_from("<H", body, p)[0])
                p = body.index(b"\0", p + 2) + 1
    if not imports or not used:
        raise MoviesUnavailable(f"{MENU} does not import its icons as the client expects")
    imported = tag(IMPORT_ASSETS, library.encode("latin1") + b"\0" + struct.pack("<HH", 1, max(used) + 1) +
                   name.encode("latin1") + b"\0")
    last = max(imports)
    out = bytearray(movie[:last] + imported + movie[last:])
    struct.pack_into("<I", out, 4, len(out))
    return bytes(out)


def _header_size(data: bytes, start: int) -> int:
    return 6 if struct.unpack_from("<H", data, start)[0] & 0x3f == 0x3f else 2

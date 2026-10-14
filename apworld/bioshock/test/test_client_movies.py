"""Tests for the copy of the Garden's menu that can show the AP icon. No game needed: a tiny movie stands in for it."""
import os
import pkgutil
import struct
import tempfile
import unittest
from pathlib import Path

from ..client import flash_movies
from ..client.flash_movies import MENU, MoviesUnavailable, import_into, prepare, read_tags, tag


def movie(*tags: bytes) -> bytes:
    body = bytes([0]) + struct.pack("<HH", 30 << 8, 1) + b"".join(tags) + tag(1, b"") + tag(0, b"")
    return b"FWS" + bytes([6]) + struct.pack("<I", 8 + len(body)) + body


def imports(library: str, *assets: tuple[int, str]) -> bytes:
    body = library.encode() + b"\0" + struct.pack("<H", len(assets))
    for character, name in assets:
        body += struct.pack("<H", character) + name.encode() + b"\0"
    return tag(57, body)


def tags_of(data: bytes) -> list[tuple[int, bytes]]:
    return [(code, body) for code, body, _ in read_tags(data, flash_movies.first_tag(data), len(data))]


class TestMenuCopy(unittest.TestCase):
    def test_the_icon_is_imported_with_the_others_under_a_new_id(self) -> None:
        shape = tag(2, struct.pack("<H", 7) + b"shape")
        original = movie(imports("lib.swf", (3, "Chips"), (9, "AirBlast")), shape)
        patched = import_into(original, "..\\FlashMovies\\APIcons.swf", "Item")
        self.assertEqual(struct.unpack_from("<I", patched, 4)[0], len(patched))
        codes = tags_of(patched)
        self.assertEqual([code for code, _ in codes], [57, 57, 2, 1, 0])
        self.assertEqual(codes[1][1], b"..\\FlashMovies\\APIcons.swf\0" + struct.pack("<HH", 1, 10) + b"Item\0")
        self.assertEqual(codes[2][1], struct.pack("<H", 7) + b"shape", "the rest is unchanged")

    def test_a_movie_it_does_not_know_is_refused(self) -> None:
        with self.assertRaises(MoviesUnavailable):
            import_into(b"CWS" + bytes(30), "lib.swf", "Item")  # compressed
        with self.assertRaises(MoviesUnavailable):
            import_into(movie(tag(2, struct.pack("<H", 1))), "lib.swf", "Item")  # imports nothing

    def test_the_shipped_icon_library_exports_the_icon(self) -> None:
        library = pkgutil.get_data(flash_movies.__name__, flash_movies.LIBRARY)
        self.assertIsNotNone(library)
        exports = [body for code, body in tags_of(library) if code == 56]
        self.assertEqual(exports, [struct.pack("<HH", 1, 3) + flash_movies.ICON_SYMBOL.encode() + b"\0"])


class TestPrepare(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.addCleanup(self.folder.cleanup)
        self.exe = self.root / "BioShock Remastered" / "Build" / "Final" / "BioshockHD.exe"
        self.exe.parent.mkdir(parents=True)
        self.movies = self.root / "BioShock Remastered" / "ContentBaked" / "pc" / "FlashMovies"
        self.movies.mkdir(parents=True)
        self.original = movie(imports("lib.swf", (3, "Chips")))
        (self.movies / MENU).write_bytes(self.original)
        self.cache = self.root / "cache"

    def test_the_copy_and_the_library_are_written_outside_the_game(self) -> None:
        self.assertEqual(prepare(str(self.exe), self.cache, b"library"), self.cache)
        self.assertEqual((self.cache / "APIcons.swf").read_bytes(), b"library")
        self.assertEqual(len(tags_of((self.cache / MENU).read_bytes())), 4)
        self.assertEqual((self.movies / MENU).read_bytes(), self.original, "the game's own file is only read")
        self.assertEqual(sorted(os.listdir(self.movies)), [MENU])

    def test_the_copy_is_only_made_again_when_the_game_changes(self) -> None:
        prepare(str(self.exe), self.cache, b"library")
        (self.cache / MENU).write_bytes(b"marker")
        prepare(str(self.exe), self.cache, b"library")
        self.assertEqual((self.cache / MENU).read_bytes(), b"marker")
        (self.movies / MENU).write_bytes(movie(imports("lib.swf", (3, "Chips"), (4, "Health"))))
        prepare(str(self.exe), self.cache, b"new library")
        self.assertEqual(len(tags_of((self.cache / MENU).read_bytes())), 4)
        self.assertEqual((self.cache / "APIcons.swf").read_bytes(), b"new library")

    def test_a_game_without_the_menu_says_so(self) -> None:
        with self.assertRaisesRegex(MoviesUnavailable, "craftingStationPC.swf is not where it should be"):
            prepare(str(self.root / "elsewhere" / "BioshockHD.exe"), self.cache, b"library")

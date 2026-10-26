"""
Builds the Archipelago icon for Gatherer's Garden lines that hold multiworld items.

BioShock Remastered's Garden menu (craftingStationPC.swf, in ContentBaked/pc/FlashMovies) attaches each line's icon
by the item class's name, from symbols it imports from sharedlibrary.swf. Each icon is a sprite holding one round
64 x 64 shape filled from a texture; textures are tag 512, BioShock's own: id, width, height, format (0 DXT1, 4 DXT5),
0, then the texels. sharedlibrary.swf is only read when the game starts, but the menu movies are read again on every
load, so the icon goes in a small library of our own (APIcons.swf) that a copy of the menu movie imports.

    python make_ap_icon.py craftingStationPC.swf icon.png out/ [--name Item]

The game's file is only read. Nothing is written into the game's folder. The client ships the APIcons.swf this makes
(apworld/bioshock/client/APIcons.swf) and patches the player's own menu itself, with flash_movies.import_into.
"""
import argparse
import math
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'apworld' / 'bioshock' / 'client'))
import dxt  # noqa: E402
from flash_movies import import_into  # noqa: E402  # what the client does to the player's menu

ICON_TEXELS = 128  # the game's icons are 128 x 128 texels, drawn 64 x 64 pixels (scale 10 in twips)
TEXTURE_TAG = 512
DXT5 = 4
LIBRARY = 'APIcons.swf'


# ---- the picture -------------------------------------------------------------------------------------------------

def medallion(logo: Image.Image, size: int = ICON_TEXELS) -> Image.Image:
    """The logo on a round tan medallion like the game's own icons."""
    scale = 4
    big = size * scale
    image = Image.new('RGBA', (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    centre = big / 2
    radius = big / 2 - scale
    for step in range(int(radius), 0, -1):  # light in the middle, darker at the rim
        t = step / radius
        colour = tuple(int(a + (b - a) * t) for a, b in zip((214, 178, 122), (166, 122, 70)))
        draw.ellipse([centre - step, centre - step, centre + step, centre + step], fill=colour + (255,))
    ring = 3 * scale
    draw.ellipse([centre - radius, centre - radius, centre + radius, centre + radius],
                 outline=(70, 48, 28, 255), width=ring)
    inner = int(radius * 1.45)
    art = logo.convert('RGBA').resize((inner, inner), Image.LANCZOS)
    image.alpha_composite(art, (int(centre - inner / 2), int(centre - inner / 2)))
    return image.resize((size, size), Image.LANCZOS)


# ---- SWF reading and writing ---------------------------------------------------------------------------------------

class BitWriter:
    def __init__(self):
        self.bits = []

    def u(self, value: int, n: int):
        self.bits += [(value >> (n - 1 - i)) & 1 for i in range(n)]

    def s(self, value: int, n: int):
        self.u(value & ((1 << n) - 1), n)

    def bytes(self) -> bytes:
        bits = self.bits + [0] * (-len(self.bits) % 8)
        return bytes(int(''.join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8))


def signed_bits(*values: int) -> int:
    return max(max(abs(v) for v in values).bit_length() + 1, 1)


def rect_bytes(xmin, xmax, ymin, ymax) -> bytes:
    n = signed_bits(xmin, xmax, ymin, ymax)
    w = BitWriter()
    w.u(n, 5)
    for v in (xmin, xmax, ymin, ymax):
        w.s(v, n)
    return w.bytes()


def matrix_bytes(scale: float, tx: int, ty: int) -> bytes:
    fixed = int(round(scale * 65536))
    w = BitWriter()
    w.u(1, 1)
    n = signed_bits(fixed)
    w.u(n, 5)
    w.s(fixed, n)
    w.s(fixed, n)
    w.u(0, 1)  # no rotation
    n = signed_bits(tx, ty)
    w.u(n, 5)
    w.s(tx, n)
    w.s(ty, n)
    return w.bytes()


def tag(code: int, body: bytes) -> bytes:
    if len(body) < 0x3f and code not in (TEXTURE_TAG,):
        return struct.pack('<H', (code << 6) | len(body)) + body
    return struct.pack('<HI', (code << 6) | 0x3f, len(body)) + body


def circle_records(radius: int = 640) -> bytes:
    """Line styles, bit counts and records of a round shape like the game's icons: one fill (fill style 1 on the right
    of the path), eight curves going clockwise on screen from the right-most point."""
    w = BitWriter()
    w.u(0, 8)  # no line styles
    w.u(1, 4)  # fill bits
    w.u(0, 4)  # line bits
    points = [(round(radius * math.cos(a)), round(radius * math.sin(a)))
              for a in (i * math.pi / 4 for i in range(9))]
    control = radius / math.cos(math.pi / 8)
    w.u(0, 1)
    w.u(0b00101, 5)  # fill style 1 and a move
    n = signed_bits(*points[0])
    w.u(n, 5)
    w.s(points[0][0], n)
    w.s(points[0][1], n)
    w.u(1, 1)
    x, y = points[0]
    for i in range(8):
        a = (i + 0.5) * math.pi / 4
        cx, cy = round(control * math.cos(a)), round(control * math.sin(a))
        ex, ey = points[i + 1]
        deltas = (cx - x, cy - y, ex - cx, ey - cy)
        n = max(signed_bits(*deltas), 2)
        w.u(1, 1)
        w.u(0, 1)  # a curve
        w.u(n - 2, 4)
        for d in deltas:
            w.s(d, n)
        x, y = ex, ey
    w.u(0, 6)  # end of the shape
    return w.bytes()


def icon_library(icon: Image.Image, name: str) -> bytes:
    """A small SWF holding one icon like the game's, exported under `name`."""
    texture_id, shape_id, sprite_id = 1, 2, 3
    texels = np.asarray(icon.convert('RGBA'))
    texture = struct.pack('<5H', texture_id, ICON_TEXELS, ICON_TEXELS, DXT5, 0) + dxt.encode_dxt5(texels)
    shape = (struct.pack('<H', shape_id) + rect_bytes(-640, 640, -640, 640) + bytes([1, 0x41]) +
             struct.pack('<H', texture_id) + matrix_bytes(10.0, -640, -640) + circle_records())
    place = tag(26, bytes([0x06]) + struct.pack('<HH', 1, shape_id) + b'\0')  # character + identity matrix
    sprite = struct.pack('<HH', sprite_id, 1) + place + tag(1, b'') + tag(0, b'')
    export = struct.pack('<HH', 1, sprite_id) + name.encode('latin1') + b'\0'
    body = (rect_bytes(0, 1280, 0, 1280) + struct.pack('<HH', 30 << 8, 1) +
            tag(9, b'\0\0\0') + tag(TEXTURE_TAG, texture) + tag(2, shape) + tag(39, sprite) + tag(56, export) +
            tag(1, b'') + tag(0, b''))
    return b'FWS' + bytes([6]) + struct.pack('<I', 8 + len(body)) + body


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('movie', help="the game's craftingStationPC.swf (only read)")
    parser.add_argument('logo', help='the picture for the icon (the Archipelago logo)')
    parser.add_argument('out', help='folder for the patched movie and APIcons.swf')
    parser.add_argument('--name', default='Item', help='the name the icon is exported and imported under')
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    icon = medallion(Image.open(args.logo))
    icon.resize((256, 256), Image.LANCZOS).save(out / 'ap_icon_preview.png')
    (out / LIBRARY).write_bytes(icon_library(icon, args.name))
    movie = Path(args.movie)
    (out / movie.name).write_bytes(import_into(movie.read_bytes(), f'..\\FlashMovies\\{LIBRARY}', args.name))
    print(f'{out / LIBRARY} exports {args.name}; {out / movie.name} imports it')


if __name__ == '__main__':
    main()

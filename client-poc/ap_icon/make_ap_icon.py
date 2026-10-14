"""
Builds the Archipelago icon for Gatherer's Garden lines that hold multiworld items, and a copy of the game's
sharedlibrary.swf with that icon added as one more exported symbol.

BioShock Remastered draws an item's icon from the symbol of sharedlibrary.swf (ContentBaked/pc/FlashMovies) exported
under the item class's name. Each such symbol is a sprite holding one 64 x 64 shape filled from a texture sheet; the
sheets are tag 512, BioShock's own texture tag: id, width, height, format (0 DXT1, 4 DXT5), 0, then the texels.

    python make_ap_icon.py sharedlibrary.swf ap_icon.png out/sharedlibrary.swf [--name APItem] [--preview icon.png]

The original file is only read. Nothing is written into the game's folder.
"""
import argparse
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dxt  # noqa: E402

ICON_TEXELS = 128  # the game's icons are 128 x 128 texels, drawn 64 x 64 pixels (scale 10 in twips)
TEXTURE_TAG = 512
DXT5 = 4
TEMPLATE_ICON = 'Chips'  # an icon whose shape has one fill: its outline and records are reused


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

class Bits:
    def __init__(self, data: bytes, byte: int = 0):
        self.data, self.pos = data, byte * 8

    def u(self, n: int) -> int:
        value = 0
        for _ in range(n):
            value = (value << 1) | ((self.data[self.pos >> 3] >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return value

    def align(self) -> int:
        self.pos = (self.pos + 7) & ~7
        return self.pos >> 3


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


def skip_matrix(bits: Bits):
    bits.align()
    if bits.u(1):
        n = bits.u(5)
        bits.u(2 * n)
    if bits.u(1):
        n = bits.u(5)
        bits.u(2 * n)
    n = bits.u(5)
    bits.u(2 * n)


def tag(code: int, body: bytes) -> bytes:
    if len(body) < 0x3f and code not in (TEXTURE_TAG,):
        return struct.pack('<H', (code << 6) | len(body)) + body
    return struct.pack('<HI', (code << 6) | 0x3f, len(body)) + body


def read_tags(data: bytes, pos: int, end: int):
    while pos < end:
        header = struct.unpack('<H', data[pos:pos + 2])[0]
        start = pos
        pos += 2
        code, length = header >> 6, header & 0x3f
        if length == 0x3f:
            length = struct.unpack('<I', data[pos:pos + 4])[0]
            pos += 4
        yield code, data[pos:pos + length], start
        pos += length
        if code == 0:
            return


def first_tag(data: bytes) -> int:
    n = data[8] >> 3
    return 8 + (5 + 4 * n + 7) // 8 + 4


DEFINING = {2, 6, 7, 10, 11, 14, 20, 21, 22, 32, 33, 34, 35, 36, 37, 39, 46, 48, 60, 75, 83, 84, 87, 90, 91,
            TEXTURE_TAG}


def patch(library: bytes, icon: Image.Image, name: str) -> bytes:
    if library[:3] != b'FWS':
        raise SystemExit('expected an uncompressed SWF (FWS)')
    tags = list(read_tags(library, first_tag(library), len(library)))
    defs, exports = {}, {}
    for code, body, _ in tags:
        if code in DEFINING and len(body) >= 2:
            defs[struct.unpack('<H', body[:2])[0]] = (code, body)
        if code == 56:
            count, p = struct.unpack('<H', body[:2])[0], 2
            for _ in range(count):
                cid = struct.unpack('<H', body[p:p + 2])[0]
                end = body.index(b'\0', p + 2)
                exports[body[p + 2:end].decode('latin1')] = cid
                p = end + 1
    if name in exports:
        raise SystemExit(f'{name} is already exported')
    # the template icon: its sprite places one shape; that shape's outline and records are reused as they are
    sprite = defs[exports[TEMPLATE_ICON]][1]
    shape_id = next(struct.unpack('<H', b[3:5])[0] for c, b, _ in read_tags(sprite, 4, len(sprite)) if c == 26)
    shape_code, shape = defs[shape_id]
    bits = Bits(shape, 2)
    n = bits.u(5)
    bits.u(4 * n)
    p = bits.align()
    if shape[p] != 1 or shape[p + 1] not in (0x40, 0x41, 0x42, 0x43):
        raise SystemExit(f'{TEMPLATE_ICON} is not one bitmap fill')
    bits = Bits(shape, p + 4)  # count, type, bitmap id
    skip_matrix(bits)
    rest = shape[bits.align():]  # line styles, fill and line bit counts, shape records

    first_free = max(defs) + 1
    texture_id, new_shape_id, sprite_id = first_free, first_free + 1, first_free + 2
    texels = np.asarray(icon.convert('RGBA'))
    texture = struct.pack('<5H', texture_id, ICON_TEXELS, ICON_TEXELS, DXT5, 0) + dxt.encode_dxt5(texels)
    # the shape: the template's 64 x 64 square, filled (clipped) from texel 0 of our texture
    new_shape = (struct.pack('<H', new_shape_id) + rect_bytes(-640, 640, -640, 640) + bytes([1, 0x41]) +
                 struct.pack('<H', texture_id) + matrix_bytes(10.0, -640, -640) + rest)
    place = tag(26, bytes([0x06]) + struct.pack('<HH', 1, new_shape_id) + b'\0')  # character + identity matrix
    new_sprite = struct.pack('<HH', sprite_id, 1) + place + tag(1, b'') + tag(0, b'')
    export = struct.pack('<HH', 1, sprite_id) + name.encode('latin1') + b'\0'
    added = (tag(TEXTURE_TAG, texture) + tag(shape_code, new_shape) + tag(39, new_sprite) + tag(56, export))

    # everything goes in before the first ShowFrame, with the library's other definitions
    show = next(start for code, _, start in tags if code == 1)
    out = bytearray(library[:show] + added + library[show:])
    struct.pack_into('<I', out, 4, len(out))
    return bytes(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('library')
    parser.add_argument('logo')
    parser.add_argument('out')
    parser.add_argument('--name', default='APItem')
    parser.add_argument('--preview')
    args = parser.parse_args()
    icon = medallion(Image.open(args.logo))
    if args.preview:
        icon.resize((256, 256), Image.LANCZOS).save(args.preview)
    out = patch(Path(args.library).read_bytes(), icon, args.name)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_bytes(out)
    print(f'{args.out}: {len(out)} bytes, {args.name} exported')


if __name__ == '__main__':
    main()

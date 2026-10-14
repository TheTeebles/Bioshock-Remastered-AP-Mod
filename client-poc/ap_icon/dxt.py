"""DXT1 and DXT5 (BC1/BC3) for the textures BioShock keeps in its Flash libraries (tag 512: format 0 is DXT1,
format 4 is DXT5). A plain encoder: good enough for one small icon."""
import numpy as np


def _565(c):
    c = np.asarray(c, dtype=np.int32)
    return ((c[..., 0] >> 3) << 11) | ((c[..., 1] >> 2) << 5) | (c[..., 2] >> 3)


def _rgb(v):
    v = np.asarray(v, dtype=np.int32)
    r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
    return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], axis=-1)


def _colors(c0, c1, four):
    a, b = _rgb(c0).astype(np.float64), _rgb(c1).astype(np.float64)
    if four:
        return np.stack([a, b, (2 * a + b) / 3, (a + 2 * b) / 3]).round().astype(np.int32)
    return np.stack([a, b, (a + b) / 2, np.zeros(3)]).round().astype(np.int32)


def decode(data: bytes, width: int, height: int, fmt: int) -> np.ndarray:
    """RGBA pixels of a DXT1 (fmt 0) or DXT5 (fmt 4) texture."""
    out = np.zeros((height, width, 4), dtype=np.uint8)
    size = 8 if fmt == 0 else 16
    pos = 0
    for by in range(0, height, 4):
        for bx in range(0, width, 4):
            block = data[pos:pos + size]
            pos += size
            alpha = np.full(16, 255, dtype=np.int32)
            if fmt == 4:
                a0, a1 = block[0], block[1]
                bits = int.from_bytes(block[2:8], 'little')
                table = [a0, a1] + ([((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)] if a0 > a1 else
                                    [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255])
                alpha = np.array([table[(bits >> (3 * i)) & 7] for i in range(16)])
                block = block[8:]
            c0, c1 = int.from_bytes(block[0:2], 'little'), int.from_bytes(block[2:4], 'little')
            palette = _colors(c0, c1, c0 > c1 or fmt == 4)
            bits = int.from_bytes(block[4:8], 'little')
            for i in range(16):
                y, x = by + i // 4, bx + i % 4
                if y < height and x < width:
                    index = (bits >> (2 * i)) & 3
                    out[y, x, :3] = palette[index]
                    out[y, x, 3] = alpha[i] if fmt == 4 else (0 if (c0 <= c1 and index == 3) else 255)
    return out


def encode_dxt5(pixels: np.ndarray) -> bytes:
    """DXT5 of RGBA pixels whose size is a multiple of 4."""
    height, width = pixels.shape[:2]
    out = bytearray()
    for by in range(0, height, 4):
        for bx in range(0, width, 4):
            block = pixels[by:by + 4, bx:bx + 4].reshape(16, 4).astype(np.int32)
            # alpha: eight steps between the block's highest and lowest alpha
            a0, a1 = int(block[:, 3].max()), int(block[:, 3].min())
            if a0 == a1:
                a1 = max(0, a0 - 1) if a0 > 0 else 0
                a0 = a1 + 1 if a0 == a1 else a0
            table = np.array([a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)])
            indices = np.abs(block[:, 3:4] - table[None, :]).argmin(axis=1)
            bits = 0
            for i, index in enumerate(indices):
                bits |= int(index) << (3 * i)
            out += bytes([a0, a1]) + bits.to_bytes(6, 'little')
            # colour: the two visible pixels furthest apart are the ends of the line the others are put on
            visible = block[block[:, 3] > 16][:, :3]
            if len(visible) == 0:
                visible = block[:, :3]
            distance = ((visible[:, None, :] - visible[None, :, :]) ** 2).sum(axis=2)
            i, j = np.unravel_index(distance.argmax(), distance.shape)
            hi, lo = visible[i], visible[j]
            c0, c1 = int(_565(hi)), int(_565(lo))
            if c0 < c1:
                c0, c1 = c1, c0
            if c0 == c1:
                c1 = max(0, c0 - 1)
                if c0 == c1:
                    c0 = 1
            palette = _colors(c0, c1, True)
            indices = ((block[:, None, :3] - palette[None, :, :]) ** 2).sum(axis=2).argmin(axis=1)
            bits = 0
            for i, index in enumerate(indices):
                bits |= int(index) << (2 * i)
            out += c0.to_bytes(2, 'little') + c1.to_bytes(2, 'little') + bits.to_bytes(4, 'little')
    return bytes(out)

"""Minimal DefineShape(1-4) -> SVG path converter (solid fills and lines; gradients approximated by first color)."""
import struct
from swfscan import Bits, matrix, rect

def rgb(b, p, alpha):
    if alpha: return '#%02x%02x%02x' % tuple(b[p:p+3]), b[p+3]/255, p+4
    return '#%02x%02x%02x' % tuple(b[p:p+3]), 1.0, p+3

def fillstyles(b, p, ver):
    n = b[p]; p += 1
    if n == 0xff and ver >= 2: n = struct.unpack_from('<H', b, p)[0]; p += 2
    out = []
    for _ in range(n):
        t = b[p]; p += 1
        if t == 0:
            c, a, p = rgb(b, p, ver >= 3); out.append((c, a))
        elif t in (0x10, 0x12, 0x13):
            _, p = matrix(b, p)
            spread = b[p]; cnt = spread & 15; p += 1; cols = []
            for i in range(cnt):
                p += 1; c, a, p = rgb(b, p, ver >= 3); cols.append((c, a))
            if t == 0x13: p += 2
            out.append(cols[len(cols)//2] if cols else ('#888888', 1))
        elif t in (0x40, 0x41, 0x42, 0x43):
            p += 2; _, p = matrix(b, p); out.append(('#808080', 0.5))
        else:
            raise ValueError('fill type %x' % t)
    return out, p

def linestyles(b, p, ver):
    n = b[p]; p += 1
    if n == 0xff: n = struct.unpack_from('<H', b, p)[0]; p += 2
    out = []
    for _ in range(n):
        w = struct.unpack_from('<H', b, p)[0] / 20; p += 2
        if ver == 4:
            f1 = b[p]; f2 = b[p+1]; p += 2
            join = (f1 >> 4) & 3; hasfill = (f1 >> 3) & 1
            if join == 2: p += 2
            if hasfill:
                fs, p = fillstyles(b, bytes([1]) and p, ver) if False else (None, p)
                # read a single FILLSTYLE
                t = b[p]
                tmp = bytes([1]) + b[p:p+400]
                fs, used = fillstyles(tmp, 0, ver); p += used - 1
                out.append((w, fs[0][0], fs[0][1]))
            else:
                c, a, p = rgb(b, p, True); out.append((w, c, a))
        else:
            c, a, p = rgb(b, p, ver >= 3); out.append((w, c, a))
    return out, p

def parse(b, q, code):
    ver = {2: 1, 22: 2, 32: 3, 83: 4}[code]
    cid = struct.unpack_from('<H', b, q)[0]
    bounds, p = rect(b, q + 2)
    if ver == 4:
        _, p = rect(b, p); p += 1
    fills, p = fillstyles(b, p, ver)
    lines, p = linestyles(b, p, ver)
    bt = Bits(b, p); nf = bt.u(4); nl = bt.u(4)
    x = y = 0.0; f0 = f1 = ln = 0; fbase = 0; lbase = 0
    allfills = [fills]; alllines = [lines]
    edges = []  # (fillidx0, fillidx1, line, seg) seg=(x0,y0,cx,cy,x1,y1) cx None = straight
    while True:
        if bt.u(1) == 0:
            flags = bt.u(5)
            if flags == 0: break
            if flags & 1:
                n = bt.u(5); x = bt.sg(n) / 20; y = bt.sg(n) / 20
            if flags & 2: v = bt.u(nf); f0 = (len(allfills)-1, v)
            if flags & 4: v = bt.u(nf); f1 = (len(allfills)-1, v)
            if flags & 8: v = bt.u(nl); ln = (len(alllines)-1, v)
            if flags & 16:
                bt.align(); pp = bt.byte()
                fills, pp = fillstyles(b, pp, ver); lines, pp = linestyles(b, pp, ver)
                allfills.append(fills); alllines.append(lines)
                bt = Bits(b, pp); nf = bt.u(4); nl = bt.u(4)
                f0 = f1 = ln = 0
        else:
            if bt.u(1):
                n = bt.u(4) + 2
                if bt.u(1): dx = bt.sg(n) / 20; dy = bt.sg(n) / 20
                elif bt.u(1): dx = 0; dy = bt.sg(n) / 20
                else: dx = bt.sg(n) / 20; dy = 0
                seg = (x, y, None, None, x + dx, y + dy)
            else:
                n = bt.u(4) + 2
                cx = x + bt.sg(n) / 20; cy = y + bt.sg(n) / 20
                ax = cx + bt.sg(n) / 20; ay = cy + bt.sg(n) / 20
                seg = (x, y, cx, cy, ax, ay)
            edges.append((f0, f1, ln, seg)); x, y = seg[4], seg[5]
    return cid, bounds, allfills, alllines, edges

def svg(shape):
    cid, bounds, allfills, alllines, edges = shape
    out = []
    # fills: collect directed edges per fill, join into paths
    byfill = {}
    for f0, f1, ln, s in edges:
        if f1 and f1[1]: byfill.setdefault(f1, []).append(s)
        if f0 and f0[1]:
            x0, y0, cx, cy, x1, y1 = s; byfill.setdefault(f0, []).append((x1, y1, cx, cy, x0, y0))
    for (g, i), segs in byfill.items():
        c, a = allfills[g][i-1]
        d = []
        rem = segs[:]
        while rem:
            s = rem.pop(0); d.append('M%.2f %.2f' % (s[0], s[1])); cur = s
            while True:
                d.append(('L%.2f %.2f' % (cur[4], cur[5])) if cur[2] is None else ('Q%.2f %.2f %.2f %.2f' % cur[2:]))
                nxt = next((k for k, t in enumerate(rem) if abs(t[0]-cur[4]) < .01 and abs(t[1]-cur[5]) < .01), None)
                if nxt is None: break
                cur = rem.pop(nxt)
        out.append('<path d="%s" fill="%s" fill-opacity="%.2f" fill-rule="evenodd"/>' % (' '.join(d), c, a))
    for f0, f1, ln, s in edges:
        if ln and ln[1]:
            w, c, a = alllines[ln[0]][ln[1]-1]
            d = 'M%.2f %.2f ' % s[:2] + (('L%.2f %.2f' % s[4:]) if s[2] is None else ('Q%.2f %.2f %.2f %.2f' % s[2:]))
            out.append('<path d="%s" fill="none" stroke="%s" stroke-opacity="%.2f" stroke-width="%.2f" stroke-linecap="round"/>' % (d, c, a, max(w, .5)))
    return out

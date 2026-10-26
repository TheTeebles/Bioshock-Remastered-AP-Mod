"""
Reads BioShock Remastered map files (.bsm) and lists what the Archipelago checks need from each: the Power to the
People stations and every Gatherer's Garden with the lines it sells. Only reads; Python 3.8+, nothing to install.

    python map_stock.py "E:\\SteamLibrary\\steamapps\\common\\BioShock Remastered" > map_stock.json

A folder is searched for .bsm files (with its subfolders); files can also be named one by one.

The maps are Unreal packages (version 142, licensee 56) whose name, import and export tables sit at the end of the
file. Export entries: the class (compact index), the super class (compact index, 0 but for classes), the outer (int32), an int32, the object's name
(compact name index + int32 number), flags (8 bytes), the serial size and offset (compact indexes; no offset when
the size is 0), then an int32. Objects hold UE2 tagged properties. A Garden is an actor of class
PlaceableGrowthStation whose VendingTable property names a VendingLootTable, whose VendingLootSpec array holds the
lines: ItemClass, CostAdjustment, SupplySize and so on. Read from 2-Fisheries.bsm on 2026-10-14 and matched against
what the game shows at Neptune's Bounty.
"""
import json
import struct
import sys
from pathlib import Path

GARDEN = "PlaceableGrowthStation"
STATION = "PlaceableWeaponUpgradeStation"
SIZES = {0: 1, 1: 2, 2: 4, 3: 12, 4: 16}


def compact(b, p):
    x = b[p]
    p += 1
    negative, value = x & 0x80, x & 0x3f
    if x & 0x40:
        shift = 6
        while True:
            x = b[p]
            p += 1
            value |= (x & 0x7f) << shift
            shift += 7
            if not x & 0x80:
                break
    return (-value if negative else value), p


class Package:
    def __init__(self, data):
        self.d = d = data
        tag, version, licensee = struct.unpack_from("<IHH", d, 0)
        if tag != 0x9E2A83C1:
            raise ValueError("not an Unreal package")
        self.version = (version, licensee)
        _, ncount, noff, ecount, eoff, icount, ioff = struct.unpack_from("<7I", d, 8)
        self.names = []
        p = noff
        for _ in range(ncount):
            n, p = compact(d, p)
            if n < 0:
                text = d[p:p - n].decode("latin1")
                p -= n
            else:
                text = d[p:p + 2 * n].decode("utf-16-le")
                p += 2 * n
            self.names.append(text.rstrip("\0"))
            p += 8
        self.imports = []
        p = ioff
        for _ in range(icount):
            _, p = self.fname(p)
            _, p = self.fname(p)
            outer = struct.unpack_from("<i", d, p)[0]
            p += 4
            name, p = self.fname(p)
            self.imports.append((name, outer))
        self.exports = []
        p = eoff
        for _ in range(ecount):
            cls, p = compact(d, p)
            _, p = compact(d, p)  # super, for a class
            outer = struct.unpack_from("<i", d, p)[0]
            p += 8
            name, p = self.fname(p)
            p += 8
            size, p = compact(d, p)
            offset = 0
            if size > 0:
                offset, p = compact(d, p)
            p += 4
            self.exports.append({"class": cls, "outer": outer, "name": name, "size": size, "offset": offset})

    def fname(self, p):
        n, p = compact(self.d, p)
        number = struct.unpack_from("<i", self.d, p)[0]
        return self.names[n] + (str(number - 1) if number else ""), p + 4

    def object_name(self, i):
        if i < 0:
            return self.imports[-i - 1][0]
        if i > 0:
            return self.exports[i - 1]["name"]
        return None

    def class_of(self, export):
        return self.object_name(export["class"]) or "Class"

    def body(self, export):
        return self.d[export["offset"]:export["offset"] + export["size"]]

    def properties(self, b, p):
        out = {}
        while p < len(b):
            n, p = compact(b, p)
            p += 4
            name = self.names[n]
            if name == "None":
                return out, p
            info = b[p]
            p += 1
            kind, size_code, flag = info & 0xf, (info >> 4) & 7, info >> 7
            if kind == 10:  # struct: its name first
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
                p += 1  # array index
            value = b[p:p + size]
            p += size
            if kind == 3:
                value = bool(flag)
            elif kind == 2 and size == 4:
                value = struct.unpack("<i", value)[0]
            elif kind == 4 and size == 4:
                value = round(struct.unpack("<f", value)[0], 4)
            elif kind == 1 and size == 1:
                value = value[0]
            elif kind == 5:
                value = self.object_name(compact(value, 0)[0])
            elif kind == 6:
                value = self.names[compact(value, 0)[0]]
            out.setdefault(name, value)
        raise ValueError("properties run past the object")

    def find_properties(self, export, wanted):
        """The object's properties. Where they start depends on the kind of object, so a few places are tried."""
        b = self.body(export)
        for start in range(0, min(len(b), 96)):
            try:
                found, end = self.properties(b, start)
            except (ValueError, IndexError, struct.error, UnicodeDecodeError):
                continue
            if wanted in found:
                return found
        return None

    def structs(self, b):
        count, p = compact(b, 0)
        rows = []
        for _ in range(count):
            row, p = self.properties(b, p)
            rows.append(row)
        return rows


def read_map(path):
    pkg = Package(path.read_bytes())
    tables = {i + 1: e for i, e in enumerate(pkg.exports) if pkg.class_of(e) == "VendingLootTable"}
    by_name = {e["name"]: e for e in tables.values()}
    result = {"map": path.name, "stations": [], "gardens": []}
    for e in pkg.exports:
        cls = pkg.class_of(e)
        if cls == STATION:
            result["stations"].append(e["name"])
        elif cls == GARDEN:
            garden = {"actor": e["name"], "table": None, "lines": []}
            found = pkg.find_properties(e, "VendingTable")
            table = by_name.get(found["VendingTable"]) if found else None
            if table is not None:
                garden["table"] = table["name"]
                spec = pkg.find_properties(table, "VendingLootSpec")
                for row in pkg.structs(spec["VendingLootSpec"]) if spec else []:
                    garden["lines"].append({key: row.get(key) for key in (
                        "ItemClass", "CostAdjustment", "SupplySize", "DisplayWhenUnHacked", "DisplayWhenHacked")})
            result["gardens"].append(garden)
    return result


def main():
    paths = []
    for arg in sys.argv[1:] or ["."]:
        path = Path(arg)
        paths += sorted(path.rglob("*.bsm"), key=lambda p: p.name.lower()) if path.is_dir() else [path]
    results = []
    for path in paths:
        try:
            results.append(read_map(path))
        except Exception as error:  # one odd file should not stop the rest
            results.append({"map": path.name, "error": f"{type(error).__name__}: {error}"})
        print(f"read {path.name}", file=sys.stderr)
    json.dump(results, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()

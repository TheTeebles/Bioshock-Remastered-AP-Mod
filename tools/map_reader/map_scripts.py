"""
Prints the scripts of a BioShock Remastered map (.bsm) as an outline: what starts each script, then its actions in
order, with ActionIf's tests and branches nested under it and each action's settings (including the values handed to
it when it runs). For working out what a level does on arrival, which facts and quests it tests, and so on.
Only reads; Python 3.9+, nothing to install. Needs map_stock.py and map_survey.py next to it.

    python map_scripts.py 4-Recreation.bsm > scripts.txt
    python map_scripts.py "E:\\SteamLibrary\\steamapps\\common\\BioShock Remastered" 4-Recreation 3-Arcadia > scripts.txt

A folder is searched (with its subfolders) for the maps named after it; with no names, every map in it.

Read from 2-Fisheries.bsm on 2026-10-14.
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from map_stock import compact  # noqa: E402
from map_survey import Ref, Survey  # noqa: E402

# Settings every action or actor has that say nothing about what it does.
NOISE = frozenset({"parentScript", "CheckpointTypePadding", "Level", "Region", "PhysicsVolume", "OwnerGroups", "Tag",
                   "Location", "Rotation", "DrawScale", "bLightChanged", "resolveInfoList", "Actions", "trueActions",
                   "elseActions", "testsOr", "testsAnd", "tests", "loopActions", "Base", "ColLocation"})
# Array properties holding further actions, in the order they are printed.
NESTED = ("testsAnd", "testsOr", "tests", "trueActions", "elseActions", "loopActions", "Actions")


def refs(pkg, value):
    """An array of object references: a compact count, then one compact index each."""
    if not isinstance(value, bytes) or not value:
        return []
    try:
        count, p = compact(value, 0)
        out = []
        for _ in range(count):
            ref, p = compact(value, p)
            out.append(ref)
        return out if p == len(value) and all(pkg.valid(r) and r > 0 for r in out) else []
    except IndexError:
        return []


def shown(pkg, value):
    if isinstance(value, Ref):
        return pkg.name_of(value) or f"#{int(value)}"
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, str):
        return repr(value)
    return str(value)


def action_lines(pkg, i, depth, seen):
    e = pkg.exports[i - 1]
    found = pkg.props(i)
    settings = [f"{k}={shown(pkg, v)}" for k, v in found.items() if k not in NOISE]
    settings += [f"{k}<-{shown(pkg, v)}" for k, v in pkg.resolved(i).items() if k]
    pad = "  " * depth
    lines = [f"{pad}{pkg.class_of(e)} {e['name']}" + (f": {', '.join(settings)}" if settings else "")]
    if i in seen:
        return lines
    seen.add(i)
    for key in NESTED:
        children = refs(pkg, found.get(key))
        if children:
            lines.append(f"{pad}  [{key}]")
            for child in children:
                lines += action_lines(pkg, child, depth + 2, seen)
    return lines


def outline(pkg):
    scripts = []
    for i, e in enumerate(pkg.exports, 1):
        if pkg.class_of(e) != "Script":
            continue
        found = pkg.props(i)
        trigger = pkg.trigger_of(i)
        head = f"{e['name']} \"{found.get('Label', '')}\": on {trigger['message']}" + \
            (f" from {trigger['by']}" if trigger["by"] else "")
        seen = set()
        lines = [head]
        for action in refs(pkg, found.get("Actions")):
            lines += action_lines(pkg, action, 1, seen)
        # Actions of the script that no list reached (the map's own order).
        rest = [c for c in pkg.children.get(i, []) if c not in seen and pkg.class_of(pkg.exports[c - 1]).startswith(
            "Action")]
        if rest and not seen:
            for action in rest:
                lines += action_lines(pkg, action, 1, seen)
        scripts.append(lines)
    return scripts


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    sys.stdout.reconfigure(encoding="utf-8")
    path = Path(sys.argv[1])
    if path.is_dir():
        wanted = {name.lower().removesuffix(".bsm") for name in sys.argv[2:]}
        paths = sorted((p for p in path.rglob("*.bsm") if not wanted or p.stem.lower() in wanted),
                       key=lambda p: p.name.lower())
    else:
        paths = [Path(arg) for arg in sys.argv[1:]]
    for map_path in paths:
        print(f"===== {map_path.name}")
        print(f"read {map_path.name}", file=sys.stderr)
        try:
            for lines in outline(Survey(map_path.read_bytes())):
                print("\n".join(lines))
                print()
        except Exception as error:  # one odd file should not stop the rest
            print(f"could not read it: {type(error).__name__}: {error}")


if __name__ == "__main__":
    main()

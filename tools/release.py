"""
Sets the version numbers a release needs, all together, and can build the .apworld.

The world's version lives in two places (data.py's WORLD_VERSION and archipelago.json's world_version) and the game
agent's in two more (agent.js and its hand-run copy, client-poc/bioshock_ap_agent.js). A release with any of them out
of step tells players the wrong thing, so they are only ever set here.

    python tools/release.py                       say what each version is now, and whether they agree
    python tools/release.py --world 0.2.1         set the world's version (major.minor.build)
    python tools/release.py --agent 2026-10-25.1  set the agent's version
    python tools/release.py --build ../Archipelago
                                                  copy the world into that Archipelago checkout and package it with its
                                                  "Build APWorlds" component; the .apworld lands in its build/apworlds

Exits with 1 when the versions disagree, so CI can run it with no arguments.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORLD = ROOT / "apworld" / "bioshock"
DATA = WORLD / "data.py"
MANIFEST = WORLD / "archipelago.json"
AGENTS = (WORLD / "client" / "agent.js", ROOT / "client-poc" / "bioshock_ap_agent.js")

WORLD_LINE = re.compile(r'^(WORLD_VERSION = ")([^"]*)(")', re.M)
AGENT_LINE = re.compile(r"^(const AGENT_VERSION = ')([^']*)(';)", re.M)
WORLD_FORMAT = re.compile(r"\d+\.\d+\.\d+")  # the apworld specification's "major.minor.build"
AGENT_FORMAT = re.compile(r"\d{4}-\d{2}-\d{2}\.\d+")  # the day, then a count for that day


def read_versions() -> dict[str, str | None]:
    found: dict[str, str | None] = {}
    match = WORLD_LINE.search(DATA.read_text(encoding="utf-8"))
    found["data.py"] = match.group(2) if match else None
    found["archipelago.json"] = json.loads(MANIFEST.read_text(encoding="utf-8")).get("world_version")
    for agent in AGENTS:
        match = AGENT_LINE.search(agent.read_text(encoding="utf-8"))
        found[str(agent.relative_to(ROOT))] = match.group(2) if match else None
    return found


def problems(found: dict[str, str | None]) -> list[str]:
    out = []
    world = {found["data.py"], found["archipelago.json"]}
    agents = {found[str(agent.relative_to(ROOT))] for agent in AGENTS}
    if len(world) != 1:
        out.append(f"the world's versions disagree: data.py {found['data.py']}, "
                   f"archipelago.json {found['archipelago.json']}")
    elif not WORLD_FORMAT.fullmatch(next(iter(world)) or ""):
        out.append(f"the world's version {next(iter(world))} is not major.minor.build")
    if len(agents) != 1:
        out.append("the agent's versions disagree: " + ", ".join(
            f"{agent.relative_to(ROOT)} {found[str(agent.relative_to(ROOT))]}" for agent in AGENTS))
    elif not AGENT_FORMAT.fullmatch(next(iter(agents)) or ""):
        out.append(f"the agent's version {next(iter(agents))} is not day.count (2026-10-25.1)")
    return out


def replace_in(path: Path, pattern: re.Pattern[str], version: str) -> None:
    text = path.read_text(encoding="utf-8")
    new, count = pattern.subn(lambda m: m.group(1) + version + m.group(3), text)
    if count != 1:
        sys.exit(f"{path.relative_to(ROOT)}: expected one version line, found {count}")
    path.write_text(new, encoding="utf-8")


def set_world(version: str) -> None:
    if not WORLD_FORMAT.fullmatch(version):
        sys.exit(f"{version} is not major.minor.build")
    replace_in(DATA, WORLD_LINE, version)
    # Edited as text, so the file keeps its own layout (tabs, key order).
    text = MANIFEST.read_text(encoding="utf-8")
    new, count = re.subn(r'("world_version":\s*")[^"]*(")', lambda m: m.group(1) + version + m.group(2), text)
    if count != 1:
        sys.exit(f"archipelago.json: expected one world_version, found {count}")
    MANIFEST.write_text(new, encoding="utf-8")


def set_agent(version: str) -> None:
    if not AGENT_FORMAT.fullmatch(version):
        sys.exit(f"{version} is not day.count (2026-10-25.1)")
    for agent in AGENTS:
        replace_in(agent, AGENT_LINE, version)


def build(archipelago: Path) -> None:
    if not (archipelago / "Launcher.py").is_file() or not (archipelago / "worlds").is_dir():
        sys.exit(f"{archipelago} is not an Archipelago source checkout")
    target = archipelago / "worlds" / "bioshock"
    if target.is_symlink():
        print(f"{target} links to {target.resolve()}: building from there")
    else:
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(WORLD, target, ignore=shutil.ignore_patterns("__pycache__"))
    subprocess.run([sys.executable, "Launcher.py", "Build APWorlds", "--", "BioShock"], cwd=archipelago, check=True)
    print(f"built {archipelago / 'build' / 'apworlds' / 'bioshock.apworld'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--world", help="set the world's version, major.minor.build")
    parser.add_argument("--agent", help="set the game agent's version, day.count")
    parser.add_argument("--build", type=Path, metavar="ARCHIPELAGO", help="package the world in this checkout")
    args = parser.parse_args()
    if args.world:
        set_world(args.world)
    if args.agent:
        set_agent(args.agent)
    found = read_versions()
    for where, version in found.items():
        print(f"{where}: {version}")
    trouble = problems(found)
    for line in trouble:
        print(f"error: {line}", file=sys.stderr)
    if trouble:
        return 1
    if args.build:
        build(args.build.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())

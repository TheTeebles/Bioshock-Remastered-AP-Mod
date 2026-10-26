"""
Reads the logs of an in-game test batch back, step by step (the Open Rapture test batch of 2026-10-14
and the ones after it).

    python read_batch_logs.py BioShockClient_<date>.txt probe_log.txt > batch_summary.md

Give it any number of logs: the BioShock Client's (logs/BioShockClient*.txt in the Archipelago folder, or the window's
text pasted into a file) and the probe's (frida -o probe_log.txt). It tells them apart by their lines. Standard library
only. It prints a Markdown summary: for each part of the batch, what the logs show, the lines that matter, and any
trouble. Nothing here decides a test passed; it collects the evidence so it can be read in a minute.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

TROUBLE = re.compile(r"could not|cannot|did not|problem|raised during|Traceback|Error|access violation|"
                     r"no such|not known|not found|still give|still sell|is locked until", re.IGNORECASE)
PROBE_STEP = re.compile(r"\[probe\] === step (\w+)\((.*)\) ===")
SENT = re.compile(r"Sent (\d+) check\(s\): (.*)")

CHECK_KINDS = (  # (kind, how its location names look)
    ("Pickups", re.compile(r" - Pickup: ")),
    ("Weapons", re.compile(r" - Weapon: ")),
    ("Safes and lockboxes", re.compile(r" - (Safe|Lockbox) \d+$")),
    ("Research", re.compile(r"^Research: ")),
    ("Gatherer's Gardens", re.compile(r" - Gatherer's Garden: ")),
)

CLIENT_PARTS = (  # (part of the batch, the agent and client lines that belong to it)
    ("Game connection", re.compile(r"Game connected|Game disconnected|agent \d{4}-\d\d-\d\d|commands disabled")),
    ("Open Rapture: bathysphere and crossings", re.compile(r"Open Rapture|is locked until you receive|bathysphere")),
    ("Gatherer's Gardens", re.compile(r"Garden")),
    ("World items and safes", re.compile(r" taken in | open in | now gives |world items:|weapons held:")),
    ("Research", re.compile(r"research")),
)


def is_probe_log(lines: list[str]) -> bool:
    return sum("[probe]" in line for line in lines[:400]) > 3


def clean(line: str) -> str:
    return line.rstrip("\n").strip()


def show(lines: list[str], limit: int = 12) -> list[str]:
    out = [f"    {line[:200]}" for line in lines[:limit]]
    if len(lines) > limit:
        out.append(f"    ... and {len(lines) - limit} more")
    return out


def read_client(name: str, lines: list[str]) -> list[str]:
    out = [f"## Client log: {name}", ""]
    sent: list[str] = []
    unnamed = 0  # the client names at most eight checks per line ("... and N more")
    for line in lines:
        found = SENT.search(line)
        if found:
            named = [s for s in found.group(2).split("; ") if not s.startswith("and ")]
            sent += named
            unnamed += max(int(found.group(1)) - len(named), 0)
    for part, pattern in CLIENT_PARTS:
        hits = [clean(line) for line in lines if pattern.search(line)]
        trouble = [line for line in hits if TROUBLE.search(line)]
        state = "nothing in the log" if not hits else ("trouble" if trouble else "seen")
        out.append(f"### {part}: {state} ({len(hits)} lines)")
        if trouble:
            out += ["  Trouble:", *show(trouble)]
        if hits:
            out += ["  Lines:", *show([line for line in hits if line not in trouble], 15)]
        out.append("")
    out.append("### Checks sent, by kind")
    kinds: dict[str, list[str]] = defaultdict(list)
    for location in sent:
        kind = next((k for k, pattern in CHECK_KINDS if pattern.search(location)), "Other")
        kinds[kind].append(location)
    if not sent:
        out.append("  No 'Sent N check(s)' lines (a client older than apworld 0.1.34, or nothing was sent).")
    if unnamed:
        out.append(f"  {unnamed} more were sent without their names in the log, so the counts below may be "
                   "short by up to that many.")
    for kind in [k for k, _ in CHECK_KINDS] + ["Other"]:
        if kinds[kind]:
            out.append(f"  {kind}: {len(kinds[kind])}")
            out += show(kinds[kind], 8)
    gardens = defaultdict(int)
    for location in kinds["Gatherer's Gardens"]:
        gardens[location.split(" - ")[0]] += 1
    if gardens:
        out.append("  Garden checks by level: " + ", ".join(f"{level} {n}" for level, n in sorted(gardens.items())))
    errors = [clean(line) for line in lines if re.search(r"Traceback|Exception|crash|problem inside", line)]
    out += ["", f"### Errors and crashes: {len(errors)} lines", *show(errors), ""]
    return out


def read_probe(name: str, lines: list[str]) -> list[str]:
    out = [f"## Probe log: {name}", ""]
    version = next((clean(line) for line in lines if "[probe] probe " in line), "no version line")
    out += [f"Probe: {version}", ""]
    steps: list[tuple[str, str, list[str]]] = []
    for line in lines:
        marker = PROBE_STEP.search(line)
        if marker:
            steps.append((marker.group(1), marker.group(2), []))
        elif steps:
            steps[-1][2].append(clean(line))
    if not steps:
        out.append("No step markers (a probe older than 2026-10-15.5): the whole log follows in short.")
        out += show([clean(line) for line in lines], 40)
        return out
    wanted = ["globals", "scriptApi", "research", "researchDiff", "researchTable", "pickups", "pickupItem"]
    ran = {step for step, _, _ in steps}
    missing = [step for step in wanted if step not in ran]
    if missing:
        out += [f"Steps not run: {', '.join(missing)}", ""]
    for step, args, body in steps:
        body = [line for line in body if line]
        trouble = [line for line in body if TROUBLE.search(line)]
        out.append(f"### {step}({args}): {len(body)} lines" + (f", {len(trouble)} with trouble" if trouble else ""))
        out += show(body, 10)
        if trouble:
            out += ["  Trouble:", *show(trouble, 6)]
        out.append("")
    return out


def main(paths: list[str]) -> int:
    if not paths:
        print(__doc__)
        return 2
    report = ["# Batch logs, read back", ""]
    for path in paths:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        reader = read_probe if is_probe_log(lines) else read_client
        report += reader(Path(path).name, lines)
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

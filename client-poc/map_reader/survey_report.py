"""
Turns map_survey.py's JSON into a Markdown report, and holds it against what the world and client already assume.

Run it from an Archipelago checkout with the BioShock world in worlds/bioshock:

    python path/to/survey_report.py survey.json > survey_report.md

It reports:
- every level change in the story (from map, to map, arrival point) and every bathysphere destination unlocked;
- each audio diary the client knows (game_data.DIARY_CLASSES) that lies in a different level's map than the one
  data.AUDIO_DIARIES puts it in, or in no map at all (given by a script, or carried by someone);
- the quests each map's scripts start and complete, for Level Complete detection without the story order;
- the plasmids, tonics, weapons and safes lying in each map, for world-item and safe checks.
"""
import json
import sys
from collections import defaultdict

from worlds.bioshock.client.game_data import DIARY_CLASSES, MAP_LEVEL, normalize_map
from worlds.bioshock.data import AUDIO_DIARIES


def level_of(map_file):
    name = normalize_map(map_file.rsplit(".", 1)[0])
    return MAP_LEVEL.get(name, name)


def main():
    maps = [entry for entry in json.load(open(sys.argv[1], encoding="utf-8")) if "error" not in entry]
    out = []
    say = out.append

    say("# Map survey report\n")
    say("## Level changes\n")
    say("| From | To | Arrives at | Started by |")
    say("|---|---|---|---|")
    for entry in maps:
        for move in entry["transitions"]:
            say(f"| {level_of(entry['map'])} | {level_of(str(move['map']))} | {move['start']} | {move['message']} |")
    say("\n## Bathysphere\n")
    say("| Map | Switches | Destinations unlocked | Menus |")
    say("|---|---|---|---|")
    for entry in maps:
        bathy = entry["bathysphere"]
        if bathy["switches"] or bathy["unlocks"] or bathy["menus"]:
            unlocked = ", ".join(sorted({str(u["map"]) for u in bathy["unlocks"]}))
            say(f"| {level_of(entry['map'])} | {', '.join(map(str, bathy['switches']))} | {unlocked} | "
                f"{bathy['menus']} |")

    say("\n## Audio diaries against data.py\n")
    lying = {}
    for entry in maps:
        for diary in entry["diaries"]:
            lying.setdefault(diary, level_of(entry["map"]))
    given = {item for entry in maps for item in entry["given"]}
    number_level = {diary.number: diary.level for diary in AUDIO_DIARIES}
    problems = 0
    for cls, number in sorted(DIARY_CLASSES.items(), key=lambda kv: kv[1]):
        expected = number_level[number]
        found = lying.get(cls)
        if found == expected:
            continue
        problems += 1
        where = found or ("given by a script" if cls in given else "in no map's LogPickup")
        say(f"- Diary {number} (`{cls}`): data.py says {expected}, the maps say {where}")
    if not problems:
        say("Every diary lies in the level data.py puts it in.")
    unknown = sorted(set(lying) - set(DIARY_CLASSES))
    if unknown:
        say(f"\nLogs lying in the maps that are not checks: {', '.join(unknown)}")

    say("\n## Quests by map\n")
    for entry in maps:
        started = sorted({q["quest"] for q in entry["quests"] if q["action"] == "start"})
        completed = sorted({q["quest"] for q in entry["quests"] if q["action"] == "complete"})
        if started or completed:
            say(f"- **{level_of(entry['map'])}**: starts {', '.join(started) or 'none'}; "
                f"completes {', '.join(completed) or 'none'}")

    say("\n## World items and safes\n")
    say("| Level | Kind | Label | Items |")
    say("|---|---|---|---|")
    counts = defaultdict(int)
    for entry in maps:
        for item in entry["world"]:
            counts[item["class"]] += 1
            say(f"| {level_of(entry['map'])} | {item['class']} | {item['label']} | {', '.join(item['items'])} |")
    say("\nTotals: " + ", ".join(f"{cls} {n}" for cls, n in sorted(counts.items())))
    print("\n".join(out))


if __name__ == "__main__":
    main()

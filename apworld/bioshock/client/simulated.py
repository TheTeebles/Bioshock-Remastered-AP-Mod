"""
A stand-in for BioShock that lives entirely in memory.

It answers the same questions the real agent does, so the client can be exercised end to end (against a real
Archipelago server too) before the game side exists. The unit tests drive it directly; in the client, `/simulate`
starts it and `/sim` plays it.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from ..data import (
    AUDIO_DIARIES, LEVEL_NAMES, LITTLE_SISTERS_PER_LEVEL, POWER_TO_THE_PEOPLE_PER_LEVEL,
    STORY_MILESTONES, WELCOME_TO_RAPTURE,
)
from .game_data import MAP_LEVEL
from .protocol import ActionResult, AgentEvent, CommandResult, GameState, PlayerDied, TravelRedirected

_GIVE_ITEM = re.compile(r"^GiveItem (\d+) ([A-Za-z_][\w.]*)$")
_GIVE_WEAPON = re.compile(r"^GiveWeapon ([A-Za-z_][\w.]*)$")
_WEAPON_UPGRADE = re.compile(r"^AddWeaponStatUpgrade ([A-Za-z]+) ([A-Za-z]+)$")
_SUMMON = re.compile(r"^summon ([A-Za-z_][\w.]*)$")


def resolve_level(text: str) -> str:
    """Level name from what a player typed: any capitalisation, or a unique part of the name."""
    wanted = text.strip().lower()
    if not wanted:
        raise ValueError("Which level? For example: Arcadia")
    exact = [level for level in LEVEL_NAMES if level.lower() == wanted]
    matches = exact or [level for level in LEVEL_NAMES if wanted in level.lower()]
    if len(matches) != 1:
        raise ValueError(f"'{text.strip()}' is not one level. Levels: {', '.join(LEVEL_NAMES)}")
    return matches[0]


class SimulatedGame:
    description = "simulated game"

    def __init__(self) -> None:
        self.level = WELCOME_TO_RAPTURE
        self.loading = False
        self.diaries: set[int] = set()
        self.little_sisters: Counter[str] = Counter()
        self.stations: Counter[str] = Counter()
        self.milestones: set[str] = set()
        self.fontaine_defeated = False
        self.inventory: Counter[str] = Counter()
        self.alarms = 0
        self.summoned: list[str] = []  # classes put into the level with `summon`
        self.deaths = 0
        self.closed = False
        self.station_touch = False  # stations open no menu, as the client asks when they are touch checks
        self.held_weapons: set[str] = set()  # weapon classes the player holds ("Crossbow")
        self.reports_weapons = False  # whether the state says which weapons the player holds, as the agent does
        self.weapons_pending = False  # whether the state says the agent has not looked at the weapons yet
        self.garden_lines: dict[str, list[dict[str, Any]]] = {}  # level -> what its Gardens sell, as the client said
        self.movies: dict[str, Any] | None = None  # the folder and files the client said to hand the game instead
        self.garden_bought: dict[str, set[int]] = {}  # level -> Garden lines bought (from 1)
        self.quests: set[str] = set()  # the game's quests completed, by name
        self.travel_rules: dict[str, Any] | None = None  # Open Rapture: where the bathysphere goes, as the client said

        # For tests: what arrived, and ways to misbehave.
        self.commands: list[str] = []
        self.actions: list[str] = []
        self.refuse: set[str] = set()  # commands containing any of these are refused
        self.unsupported_actions: set[str] = set()

        self._paused = False
        self._waiting: list[tuple[int, str, bool]] = []  # (id, command or action name, is an action)
        self._events: list[AgentEvent] = []

    @property
    def paused(self) -> bool:
        """A paused game still looks ready, but it runs nothing: what arrives waits until the game continues.
        The real agent does the same whenever the game stops running its world."""
        return self._paused

    @paused.setter
    def paused(self, value: bool) -> None:
        self._paused = value
        self._run_waiting()

    # ---------------------------------------------------------------------------------------------------------------
    # GameAgent
    # ---------------------------------------------------------------------------------------------------------------

    def read_state(self) -> GameState:
        return GameState(
            ready=not self.loading,
            level=self.level,
            goal=self.fontaine_defeated,
            diaries=frozenset(self.diaries),
            little_sisters=dict(self.little_sisters),
            stations=dict(self.stations),
            milestones=frozenset(self.milestones),
            weapons=frozenset(self.held_weapons) if self.reports_weapons else None,
            weapons_pending=self.weapons_pending,
            garden_bought={level: frozenset(lines) for level, lines in self.garden_bought.items()},
            quests=frozenset(self.quests),
        )

    def poll_events(self) -> list[AgentEvent]:
        events, self._events = self._events, []
        return events

    def run_command(self, command_id: int, command: str) -> None:
        self.commands.append(command)
        self._waiting.append((command_id, command, False))
        self._run_waiting()

    def run_action(self, command_id: int, action: str) -> None:
        if action == "touch_stations":  # a setting, not something that happens in the game: not in `actions`
            self.station_touch = True
            self._events.append(ActionResult(command_id, True))
            return
        if action.startswith("garden_lines "):  # also a setting: what the Gardens of a level sell
            offer = json.loads(action.partition(" ")[2])
            self.garden_lines[offer["level"]] = offer["lines"]
            self._events.append(ActionResult(command_id, True))
            return
        if action.startswith("travel_rules "):  # a setting too: where the bathysphere and level changes may go
            self.travel_rules = json.loads(action.partition(" ")[2])
            self._events.append(ActionResult(command_id, True))
            return
        if action.startswith("redirect_movies "):  # and so is where the Garden's menu with the AP icon is
            self.movies = json.loads(action.partition(" ")[2])
            self._events.append(ActionResult(command_id, True))
            return
        self.actions.append(action)
        self._waiting.append((command_id, action, True))
        self._run_waiting()

    def _run_waiting(self) -> None:
        if self._paused:
            return
        waiting, self._waiting = self._waiting, []
        for command_id, text, is_action in waiting:
            if is_action:
                if text in self.unsupported_actions:
                    self._events.append(ActionResult(command_id, False, "the simulated game does not support it"))
                else:
                    self._events.append(ActionResult(command_id, True))
                continue
            handled = not any(part in text for part in self.refuse) and self._run_console_command(text)
            self._events.append(CommandResult(command_id, handled))

    def _run_console_command(self, text: str) -> bool:
        """The console commands the client uses. Anything else is not handled, as in the game."""
        if match := _GIVE_ITEM.match(text):
            self.inventory[match[2]] += int(match[1])
        elif match := _GIVE_WEAPON.match(text):
            self.inventory[match[1]] += 1
            self.held_weapons.add(match[1].rsplit(".", 1)[-1])
        elif match := _WEAPON_UPGRADE.match(text):
            self.inventory[f"{match[1]} {match[2]} upgrade"] += 1
        elif text == "StartSecurityAlarm":
            self.alarms += 1
        elif match := _SUMMON.match(text):
            self.summoned.append(match[1])
        else:
            return False
        return True

    def kill_player(self) -> None:
        self.die()

    def close(self) -> None:
        self.closed = True

    # ---------------------------------------------------------------------------------------------------------------
    # The "player"
    # ---------------------------------------------------------------------------------------------------------------

    def travel(self, level: str) -> None:
        if level not in LEVEL_NAMES:
            raise ValueError(f"Unknown level: {level}")
        self.level = level

    def bathysphere_stops(self) -> list[str]:
        """The levels the bathysphere offers: all of them, unless the client has said otherwise."""
        if self.travel_rules is None:
            return list(LEVEL_NAMES)
        return [MAP_LEVEL[stop["map"]] for stop in self.travel_rules["stops"]]

    def ride(self, level: str) -> None:
        """Take the bathysphere to a level, as its menu allows."""
        if level not in self.bathysphere_stops():
            raise ValueError(f"The bathysphere does not go to {level}.")
        self.travel(level)

    def cross(self, level: str) -> str:
        """Take the story's own way into a level, which the client may have sent elsewhere. Returns where it led."""
        wanted = next(map_name for map_name, name in MAP_LEVEL.items() if name == level)
        for redirect in (self.travel_rules or {}).get("redirects", ()):
            if redirect["to"] == wanted and MAP_LEVEL.get(redirect["from"]) == self.level:
                self.travel(MAP_LEVEL[redirect["map"]])
                self._events.append(TravelRedirected(wanted))
                return self.level
        self.travel(level)
        return level

    def collect_diary(self, number: int) -> str:
        if not 1 <= number <= len(AUDIO_DIARIES):
            raise ValueError(f"Audio diaries are numbered 1 to {len(AUDIO_DIARIES)}.")
        self.diaries.add(number)
        diary = AUDIO_DIARIES[number - 1]
        return f"{diary.level}: {diary.title}"

    def deal_with_little_sister(self, level: str | None = None) -> None:
        level = level or self.level
        limit = LITTLE_SISTERS_PER_LEVEL.get(level, 0)
        if self.little_sisters[level] >= limit:
            raise ValueError(f"{level} has no more Little Sisters ({limit} in total).")
        self.little_sisters[level] += 1

    def use_station(self, level: str | None = None) -> None:
        level = level or self.level
        limit = POWER_TO_THE_PEOPLE_PER_LEVEL.get(level, 0)
        if self.stations[level] >= limit:
            raise ValueError(f"{level} has no more Power to the People stations ({limit} in total).")
        self.stations[level] += 1

    def buy_from_garden(self, line: int) -> str:
        """Buy line `line` (from 1) of the Gatherer's Garden here. Returns what the line was selling."""
        offer = {entry["line"]: entry for entry in self.garden_lines.get(self.level, ())}
        if line not in offer:
            raise ValueError(f"The Gardens in {self.level} have no line {line} that is a check"
                             + (f" (lines: {', '.join(str(n) for n in sorted(offer))})." if offer else "."))
        if line in self.garden_bought.get(self.level, set()) or offer[line]["bought"]:
            raise ValueError(f"Line {line} has been bought already.")
        self.garden_bought.setdefault(self.level, set()).add(line)
        return f"{offer[line]['name']} for {offer[line]['price']} ADAM"

    def reach_milestone(self, level: str | None = None) -> str:
        """Do the next story milestone of a level. Returns what it was."""
        level = level or self.level
        in_level = [milestone for milestone in STORY_MILESTONES if milestone.level == level]
        for milestone in in_level:
            if milestone.key not in self.milestones:
                self.milestones.add(milestone.key)
                return milestone.title
        raise ValueError(f"{level} has no more story milestones ({len(in_level)} in total).")

    def defeat_fontaine(self) -> None:
        self.fontaine_defeated = True

    def die(self) -> None:
        self.deaths += 1
        self._events.append(PlayerDied())

"""
A stand-in for BioShock that lives entirely in memory.

It answers the same questions the real agent does, so the client can be exercised end to end (against a real
Archipelago server too) before the game side exists. The unit tests drive it directly; in the client, `/simulate`
starts it and `/sim` plays it.
"""
from __future__ import annotations

import re
from collections import Counter

from ..data import (
    AUDIO_DIARIES, DIRECTORS_COMMENTARY_LEVELS, LEVEL_NAMES, LITTLE_SISTERS_PER_LEVEL, POWER_TO_THE_PEOPLE_PER_LEVEL,
    STORY_MILESTONES, WELCOME_TO_RAPTURE,
)
from .protocol import ActionResult, AgentEvent, CommandResult, GameState, PlayerDied

_GIVE_ITEM = re.compile(r"^GiveItem (\d+) ([A-Za-z_][\w.]*)$")
_GIVE_WEAPON = re.compile(r"^GiveWeapon ([A-Za-z_][\w.]*)$")
_WEAPON_UPGRADE = re.compile(r"^AddWeaponStatUpgrade ([A-Za-z]+) ([A-Za-z]+)$")


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
        self.reels: set[str] = set()
        self.stations: Counter[str] = Counter()
        self.milestones: set[str] = set()
        self.fontaine_defeated = False
        self.inventory: Counter[str] = Counter()
        self.alarms = 0
        self.deaths = 0
        self.closed = False

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
            reels=frozenset(self.reels),
            stations=dict(self.stations),
            milestones=frozenset(self.milestones),
        )

    def poll_events(self) -> list[AgentEvent]:
        events, self._events = self._events, []
        return events

    def run_command(self, command_id: int, command: str) -> None:
        self.commands.append(command)
        self._waiting.append((command_id, command, False))
        self._run_waiting()

    def run_action(self, command_id: int, action: str) -> None:
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
        elif match := _WEAPON_UPGRADE.match(text):
            self.inventory[f"{match[1]} {match[2]} upgrade"] += 1
        elif text == "StartSecurityAlarm":
            self.alarms += 1
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

    def collect_reel(self, level: str | None = None) -> None:
        level = level or self.level
        if level not in DIRECTORS_COMMENTARY_LEVELS:
            raise ValueError(f"{level} has no Director's Commentary reel.")
        self.reels.add(level)

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

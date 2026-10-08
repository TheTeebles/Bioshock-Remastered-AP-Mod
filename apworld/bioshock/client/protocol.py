"""
The types that cross the two seams of the client:

    Archipelago server  <-- actions --  BridgeCore  -- actions -->  game agent
                        -- packets -->              <-- state/events --

Everything here is plain data, so the core can be tested without Archipelago, Frida or the game.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


# --------------------------------------------------------------------------------------------------------------------
# Game agent -> client
# --------------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class GameState:
    """One snapshot of the game. A field stays None while the agent has no way to detect it yet."""
    ready: bool = False  # in a level and able to run commands right now (not loading, not in a menu)
    level: str | None = None  # level name, when the agent can tell directly
    map: str | None = None  # otherwise the name of the map the player is in, as game_data.normalize_map leaves it
    level_value: int | None = None  # the autosplitter's level value, from an agent that cannot name the map
    in_level: bool | None = None  # False: certainly in no level right now (a loading screen, or no game loaded)
    fontaine_phase: int | None = None  # phase of the final fight
    goal: bool | None = None  # True once the agent itself has seen Fontaine defeated
    diaries: frozenset[int] | None = None  # audio diary numbers (1-122) collected in this save
    little_sisters: Mapping[str, int] | None = None  # level -> sisters rescued or harvested there
    reels: frozenset[str] | None = None  # levels whose Director's Commentary reel is collected
    stations: Mapping[str, int] | None = None  # level -> Power to the People stations used there
    milestones: frozenset[str] | None = None  # story milestones reached in this save (keys of STORY_MILESTONES)


@dataclass(frozen=True)
class CommandStarted:
    """The command has gone into the game. Until its result follows, the game is busy with that command."""
    command_id: int


@dataclass(frozen=True)
class CommandResult:
    command_id: int
    handled: bool  # False: the engine did not recognise or accept the command


@dataclass(frozen=True)
class ActionResult:
    command_id: int
    ok: bool
    reason: str = ""


@dataclass(frozen=True)
class PlayerDied:
    pass


@dataclass(frozen=True)
class AgentMessage:
    text: str


@dataclass(frozen=True)
class LittleSisterResolved:
    """The agent saw a Little Sister rescued or harvested, in this map (None if it could not tell)."""
    map: str | None


AgentEvent = CommandStarted | CommandResult | ActionResult | PlayerDied | AgentMessage | LittleSisterResolved


class AgentError(Exception):
    """The game went away, or the agent stopped answering."""


class GameAgent(Protocol):
    """Whatever sits inside (or pretends to be) the game. Calls must return quickly and never block on the game."""
    description: str

    def read_state(self) -> GameState: ...

    def poll_events(self) -> list[AgentEvent]: ...

    def run_command(self, command_id: int, command: str) -> None: ...

    def run_action(self, command_id: int, action: str) -> None: ...

    def kill_player(self) -> None: ...

    def close(self) -> None: ...


# --------------------------------------------------------------------------------------------------------------------
# Core -> the outside world
# --------------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SendChecks:
    locations: tuple[int, ...]


@dataclass(frozen=True)
class SendGoal:
    pass


@dataclass(frozen=True)
class SendDeath:
    cause: str


@dataclass(frozen=True)
class RunCommand:
    command_id: int
    command: str


@dataclass(frozen=True)
class RunAction:
    command_id: int
    action: str


@dataclass(frozen=True)
class KillPlayer:
    pass


@dataclass(frozen=True)
class SaveState:
    data: dict[str, Any]


@dataclass(frozen=True)
class Notice:
    text: str
    warning: bool = False


Action = SendChecks | SendGoal | SendDeath | RunCommand | RunAction | KillPlayer | SaveState | Notice

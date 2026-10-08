"""
The real game, reached through Frida.

Frida injects agent.js into BioshockHD.exe. Frida itself lives in a Python of the player's own, because the
Archipelago people install is a packaged program whose built-in Python cannot hold it. So this module starts
frida_helper.py in the player's Python and talks to it through pipes (frida_helper.py describes the conversation),
and turns what comes back into the plain types the core understands.

Nothing in here waits for the game or for the helper. Starting, attaching and every call return at once, and the
answers are picked up the next time the client looks.
"""
from __future__ import annotations

import collections
import dataclasses
import json
import os
import pkgutil
import queue
import re
import subprocess
import sys
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .game_data import delivers_in, diaries_of, milestones_of, normalize_map
from .protocol import (
    ActionResult, AgentError, AgentEvent, AgentMessage, CommandResult, CommandStarted, GameState,
    LittleSisterResolved,
)

PROCESS_NAME = "BioshockHD.exe"
PYTHON_VARIABLE = "BIOSHOCK_AP_PYTHON"  # environment variable naming the Python to use
SCRIPT_ERROR = "Agent script error: "

HELLO_SECONDS = 20.0  # for a Python to start and load Frida; slow the first time after the PC starts
ATTACH_SECONDS = 30.0  # for Frida to get into the game and the agent to answer
STALE_SECONDS = 3.0  # with no news from the game for this long, it does not count as ready
EXIT_SECONDS = 6.0  # what a helper gets to take the agent out of the game, before it is stopped the hard way
QUIET_SECONDS = 1.0  # a helper that has been gone this long has nothing more to say
ANSWER_LINES = 3  # how much of what the game prints in answer to a command is shown to the player
RAISED_LINES = 3  # ... and of what it raises while the command runs
# How the game says that it has no class of the name it was given. Seen in the game on 2026-10-07, in English.
MISSING_CLASS = re.compile(r"Failed to find object 'Class ([^']+)'")

NO_FRIDA = ("The client reaches the game through Frida, and Frida has to be installed in a Python of your own: "
            "the one built into Archipelago cannot hold it. Install Python from python.org, run "
            "`pip install frida` in a terminal, then type /attach. If your Python is somewhere unusual, name it "
            "with /python <path to python.exe>. /simulate tries the client without the game, on a test seed.")

# Frida's words for why its hold on the game ended, in the player's.
DETACH_REASONS = {
    "process-terminated": "the game closed",
    "process-replaced": "the game was restarted",
    "connection-terminated": "the connection to the game was lost",
    "device-lost": "the connection to the game was lost",
    "application-requested": "detached",
}

# Starts the helper from the text the client sends as its first line, so that nothing has to be unpacked from the
# apworld. The empty entry of sys.path is the current folder, and a stray json.py there must not be picked up.
BOOTSTRAP = ("import sys;sys.path[:]=[p for p in sys.path if p];import json;"
             "exec(compile(json.loads(sys.stdin.buffer.readline()),'frida_helper.py','exec'))")

Command = tuple[str, ...]  # what starts a Python, e.g. ("C:\\Windows\\py.exe", "-3")


class AgentUnusable(AgentError):
    """Attaching cannot work until something changes, so trying again every few seconds is pointless."""


class FridaUnavailable(AgentUnusable):
    """No Python with the frida package in it was found."""


class AgentScriptFailed(AgentUnusable):
    """The agent script did not start inside the game."""


class GameNotRunning(AgentError):
    """BioshockHD.exe was not found. Worth retrying later."""


def load_agent_source() -> str:
    return _packaged("agent.js")


def load_helper_source() -> str:
    return _packaged("frida_helper.py")


def _packaged(name: str) -> str:
    try:
        data = pkgutil.get_data(__name__, name)
    except OSError:  # how a file that is not there shows, in a folder and in a zip alike
        data = None
    if data is None:
        raise AgentScriptFailed(f"{name} is missing from the apworld. Install the apworld again")
    return data.decode("utf-8")


def state_from_agent(raw: dict[str, Any]) -> GameState:
    """Translate the agent's `state()` answer. Kept apart from Frida so it can be tested."""
    loading = raw.get("loading")
    in_game = raw.get("inGame")
    # None means the agent cannot tell on this build, which is not a reason to wait forever. False means no.
    # playerReady: the player has a controller and a body in a level that is running. Items are player commands,
    # and the engine alone would answer "not handled" for them, which the client would take for a refusal.
    map_name = normalize_map(raw.get("map"))
    ready = bool(raw.get("execAvailable")) and bool(raw.get("hookInstalled")) \
        and raw.get("engineReady") is not False and loading is not True and in_game is not False \
        and raw.get("playerReady") is not False and delivers_in(map_name)
    # The level number is only passed on from an agent that cannot name the map. An agent that can names it
    # whenever the player is in a level, and when it does not (a menu, a pause, a loading screen) the number
    # is not to be trusted either: after loading a save it can be anything, another level's value included.
    names_maps = "map" in raw
    return GameState(
        ready=ready,
        map=map_name,
        level_value=None if names_maps else (raw.get("levelValue") or None),  # 0 while a level is loading
        in_level=False if loading is True or in_game is False else None,
        fontaine_phase=raw.get("fontainePhase"),
        milestones=milestones_of(raw.get("completedQuests")),
        diaries=diaries_of(raw.get("logsReceived")),
    )


def event_from_message(message: dict[str, Any]) -> AgentEvent | None:
    """Translate one message the agent sent. Returns None for messages the client has no use for."""
    if message.get("type") == "error":
        where = f" (line {message['lineNumber']})" if message.get("lineNumber") else ""
        return AgentMessage(f"{SCRIPT_ERROR}{message.get('description', message)}{where}")
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return None
    kind = payload.get("type")
    if kind == "exec_started":
        if payload.get("id") is None:
            return None
        return CommandStarted(int(payload["id"]))
    if kind == "exec_result":
        if payload.get("id") is None:
            return None  # typed at the Frida prompt, not ours
        if payload.get("fatal"):  # the agent failed on this command: trying it again would do the same
            return ActionResult(int(payload["id"]), False, str(payload.get("reason", "the game agent failed")))
        return CommandResult(int(payload["id"]), bool(payload.get("handled")))
    if kind == "action_result":
        if not payload.get("id"):  # id 0: asked for outside item delivery (DeathLink)
            if payload.get("ok"):
                return None
            return AgentMessage(f"The game agent could not do '{payload.get('name')}': "
                                f"{payload.get('reason', 'unknown reason')}")
        return ActionResult(int(payload["id"]), bool(payload.get("ok")), str(payload.get("reason", "")))
    if kind == "little_sister":
        return LittleSisterResolved(normalize_map(payload.get("map")))
    if kind == "exec_disabled":
        return AgentMessage(f"The game agent turned commands off: {payload.get('reason', 'unknown reason')}")
    return None


def answer_from_message(message: dict[str, Any]) -> AgentMessage | None:
    """What the game printed in answer to one of the client's commands, if it printed anything.

    Most commands print nothing. When one does, it is worth showing: it may be the game saying why an item did not
    arrive, which the command's own result does not tell.
    """
    payload = message.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "exec_result" or payload.get("id") is None:
        return None
    received = list(payload.get("output") or ())
    lines = [str(line).strip() for line in received if str(line).strip()]
    if not lines:
        return None
    shown = lines[:ANSWER_LINES]
    total = payload.get("outputLines")
    beyond = max(0, total - len(received)) if isinstance(total, int) else 0  # printed, but not sent along
    more = len(lines) - len(shown) + beyond
    return AgentMessage(f"The game answered \"{payload.get('command', '?')}\" with: {' | '.join(shown)}"
                        + (f" (and {more} more line{'s' if more > 1 else ''})" if more > 0 else ""))


def raised_from_message(message: dict[str, Any]) -> tuple[int, str, str] | None:
    """(command id, command, description) when the agent says the game raised an exception during a command."""
    payload = message.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "exec_raised" or payload.get("id") is None:
        return None
    what = str(payload.get("what", "")).strip()
    if not what:
        return None
    return int(payload["id"]), str(payload.get("command", "?")), what


def missing_class(command: str, described: Sequence[str]) -> str | None:
    """The class a command named and the game says it does not have, or None.

    The game says so by raising an exception while the command runs, and then carries on as if nothing had been
    asked of it: the command comes back as handled, and nothing arrives. Only a class the command itself names
    counts; a game that fails to find something else on the way may still have given the item.
    """
    words = command.lower().split()
    for what in described:
        found = MISSING_CLASS.search(what)
        if found and found.group(1).lower() in words:
            return found.group(1)
    return None


def routine_lookup(command: str, what: str) -> bool:
    """True for the one thing the game raises with every item of the designer package and thinks nothing of.

    Given `ShockDesignerClasses.ArmoredBodyTwo`, the game also looks for a class of that name in ShockGame, does
    not find one, and carries on to give the item with its window (seen in the game on 2026-10-07). Telling the
    player so every time would only make a working item look broken.
    """
    found = MISSING_CLASS.search(what)
    if found is None:
        return False
    package, _, name = found.group(1).lower().rpartition(".")
    if package != "shockgame":
        return False
    return any(word.endswith(f".{name}") and not word.startswith("shockgame.") for word in command.lower().split())


@dataclasses.dataclass
class _Raised:
    """What the game has raised so far while one command runs."""
    command: str
    described: list[str] = dataclasses.field(default_factory=list)
    routine: int = 0  # how many more were the game's routine look into ShockGame, which is not passed on


def raised_summary(command: str, described: Sequence[str], total: int | None = None,
                   gone: bool = False) -> AgentMessage:
    """One line for the player about what the game raised while a command ran.

    A game of this kind raises exceptions as a matter of routine and deals with them itself, so this is not a
    fault. But it is the game's own word on a command, and often the only one: a class it does not know, say.
    `total` is how many it raised in all, where the agent said. `gone`: the game went away before it answered.
    """
    shown = list(described[:RAISED_LINES])
    more = max(len(described), total or 0) - len(shown)
    start = (f"Before the game went away it raised, while running \"{command}\": " if gone
             else f"While running \"{command}\" the game raised: ")
    return AgentMessage(start + " | ".join(shown) + (f" (and {more} more)" if more > 0 else ""))


# --------------------------------------------------------------------------------------------------------------------
# Finding a Python
# --------------------------------------------------------------------------------------------------------------------

def python_candidates(preferred: Iterable[Sequence[str] | str | None] = ()) -> list[Command]:
    """Commands that may start a Python with Frida in it, most likely first.

    Whether one really has Frida is found out by starting it, so a wrong guess costs a second and nothing else.
    """
    found: list[Command] = []
    seen: set[tuple[str, ...]] = set()

    def add(command: Sequence[str] | str | None) -> None:
        parts = (command,) if isinstance(command, str) else tuple(command or ())
        if not parts or not all(parts):
            return
        # Links are deliberately not followed: a virtual environment's python is a link to the one it was made
        # from, and the two do not have the same packages.
        key = (os.path.normcase(os.path.abspath(parts[0])) if os.path.dirname(parts[0]) else parts[0], *parts[1:])
        if key not in seen:
            seen.add(key)
            found.append(parts)

    for choice in preferred:
        add(choice)
    add(os.environ.get(PYTHON_VARIABLE))
    if not getattr(sys, "frozen", False):  # running from source: Archipelago's Python is an ordinary one
        add(sys.executable)
    frida_tool = _program("frida")
    if frida_tool:
        # `pip install frida-tools` puts the frida command in Scripts\ under the Python on Windows, and next to
        # it elsewhere.
        folder = os.path.dirname(os.path.abspath(frida_tool))
        for place in (os.path.dirname(folder), folder):
            for name in ("python.exe", "python3", "python"):
                if os.path.isfile(os.path.join(place, name)):
                    add(os.path.join(place, name))
    launcher = _program("py")
    if launcher:
        add((launcher, "-3"))
    for name in ("python", "python3"):
        add(_program(name))
    return found


def _program(name: str) -> str | None:
    """Where a program of this name is on the PATH.

    Not `shutil.which`: on Windows that looks in the current folder first, and it accepts .bat and .cmd wrappers,
    whose quoting rules would mangle what the helper is started with.
    """
    file_name = f"{name}.exe" if os.name == "nt" else name
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        folder = folder.strip('"')
        if not folder or folder == os.curdir:
            continue
        path = os.path.join(folder, file_name)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


# --------------------------------------------------------------------------------------------------------------------
# The helper process
# --------------------------------------------------------------------------------------------------------------------

class _HelperProcess:
    """One helper and its three pipes. It moves lines and knows nothing about what they mean."""

    def __init__(self, command: Command, env: Mapping[str, str] | None, helper_source: str) -> None:
        self.command = command
        self._process = subprocess.Popen(
            [*command, "-c", BOOTSTRAP], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=None if env is None else dict(env),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # no console window flashing up
        self._lock = threading.Lock()
        self._received: list[dict[str, Any] | None] = []  # None once its output has closed
        self._outbox: queue.Queue[bytes | None] = queue.Queue()
        self._gone_since: float | None = None
        self._ended = False  # the client has been told that the helper will say no more
        self._stopping = False
        self._errors: collections.deque[str] = collections.deque(maxlen=20)
        self._outbox.put(json.dumps(helper_source).encode("ascii") + b"\n")
        self._error_reader = threading.Thread(target=self._read_errors, name="BioShock helper errors", daemon=True)
        self._error_reader.start()
        threading.Thread(target=self._read, name="BioShock helper output", daemon=True).start()
        threading.Thread(target=self._write, name="BioShock helper input", daemon=True).start()

    # -- the client's thread ------------------------------------------------------------------------------------------

    def send(self, message: dict[str, Any]) -> None:
        self._outbox.put(json.dumps(message).encode("ascii") + b"\n")

    def take(self, now: float) -> list[dict[str, Any] | None]:
        """Everything the helper has said since the last call, oldest first. None means it will say no more."""
        with self._lock:
            received, self._received = self._received, []
        if self._ended:
            return []
        if not received and self._process.poll() is not None:
            # Gone, and yet its output has not closed: something it started still holds the pipe. Do not wait
            # for that forever, but do give the last lines a moment to arrive.
            if self._gone_since is None:
                self._gone_since = now
            elif now - self._gone_since > QUIET_SECONDS:
                received.append(None)
        if None in received:
            self._ended = True
            del received[received.index(None) + 1:]
        return received

    def last_words(self) -> str:
        """The last thing the helper wrote that was not part of the conversation: its error, if it had one."""
        self._error_reader.join(0.2)  # what a helper says as it dies arrives a moment after its output closes
        return self._errors[-1] if self._errors else ""

    def stop(self) -> None:
        """Have the helper take the agent out of the game and leave, and see to it that it does leave."""
        if self._stopping:
            return
        self._stopping = True
        self._outbox.put(None)  # closes its input, which is its sign to go
        # Not left to the thread that writes to it: that one is stuck for as long as a program that does not
        # read its input has not taken what was already sent.
        threading.Thread(target=self._see_out, name="BioShock helper exit", daemon=True).start()

    def _see_out(self) -> None:
        try:
            self._process.wait(EXIT_SECONDS)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()

    # -- its own threads ----------------------------------------------------------------------------------------------

    def _read(self) -> None:
        stream = self._process.stdout
        assert stream is not None
        try:
            for raw in stream:
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue  # not part of the conversation
                if not isinstance(message, dict):
                    continue
                with self._lock:
                    newest = self._received[-1] if self._received else None
                    if message.get("event") == "state" and newest is not None and newest.get("event") == "state":
                        self._received[-1] = message  # only the latest state matters
                    else:
                        self._received.append(message)
        except (OSError, ValueError):
            pass
        with self._lock:
            self._received.append(None)
        stream.close()

    def _read_errors(self) -> None:
        stream = self._process.stderr
        assert stream is not None
        try:
            for raw in stream:
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    self._errors.append(line[:300])
        except (OSError, ValueError):
            pass
        stream.close()

    def _write(self) -> None:
        stream = self._process.stdin
        assert stream is not None
        try:
            while True:
                data = self._outbox.get()
                if data is None:
                    break
                stream.write(data)
                stream.flush()
        except (OSError, ValueError):
            pass  # the helper has gone; the reader notices
        # Closing its input is what tells the helper to take the agent out of the game and leave.
        try:
            stream.close()
        except OSError:
            pass


# --------------------------------------------------------------------------------------------------------------------
# The agent as the client sees it
# --------------------------------------------------------------------------------------------------------------------

_STARTING = "starting"  # waiting for a Python to say that it has Frida
_ATTACHING = "attaching"  # waiting for Frida to get into the game
_WAITING = "waiting"  # that did not work this time; try_again() has another go
_ATTACHED = "attached"
_OVER = "over"


class FridaAgent:
    """The game, by way of a helper process.

    `start()` it, ask `poll_attach()` until that says yes, and from then on it is the GameAgent the client loop
    expects.
    """

    def __init__(self, candidates: Iterable[Sequence[str]], env: Mapping[str, str] | None = None) -> None:
        self.description = "BioShock Remastered"
        self.command: Command = ()  # the Python that turned out to have Frida
        self.python = ""
        self.frida_version = ""
        self._candidates = [tuple(candidate) for candidate in candidates]
        self._env = env
        self._tried: list[str] = []
        self._helper: _HelperProcess | None = None
        self._phase = _STARTING
        self._deadline = 0.0
        self._failure: AgentError | None = None
        self._events: collections.deque[AgentEvent] = collections.deque()
        self._raised: dict[int, _Raised] = {}  # command id -> what the game raised while that command ran
        self._script_errors: list[str] = []
        self._state = GameState()
        self._state_at = 0.0
        self._gone: str | None = None
        self._closed = False
        try:
            self._agent_source = load_agent_source()
            self._helper_source = load_helper_source()
        except AgentUnusable as error:
            self._give_up(error)
        else:
            self._start_next()

    @classmethod
    def start(cls, candidates: Iterable[Sequence[str]], env: Mapping[str, str] | None = None) -> "FridaAgent":
        """Begin attaching to the running game, trying these Pythons in order. Returns at once."""
        return cls(candidates, env)

    # -- attaching ----------------------------------------------------------------------------------------------------

    @property
    def alive(self) -> bool:
        """False once this object is of no further use and a new one has to be started."""
        return self._helper is not None and self._gone is None

    @property
    def busy(self) -> bool:
        """True while an attempt to get into the game is under way and has no outcome yet."""
        return self._phase in (_STARTING, _ATTACHING)

    def poll_attach(self) -> bool:
        """How attaching is going: True once the agent is in the game, False while that is still under way.

        Raises AgentUnusable when it cannot work until something changes. Raises another AgentError when it did
        not work this time, typically because the game is not running yet; `try_again()` then has another go.
        """
        self._pump()
        now = time.monotonic()
        if self._phase == _STARTING and now > self._deadline:
            self._tried.append(f"{self._name()} did not answer within {HELLO_SECONDS:.0f} seconds")
            self._start_next()
        elif self._phase == _ATTACHING and now > self._deadline:
            self._give_up(AgentUnusable(
                f"The game did not let the client in within {ATTACH_SECONDS:.0f} seconds. /attach tries again."))
        if self._failure is not None:
            failure, self._failure = self._failure, None
            raise failure
        return self._phase == _ATTACHED

    def try_again(self) -> None:
        """After poll_attach() reported a problem worth another try. Does nothing while a try is under way."""
        self._pump()  # a helper that died while it waited must not be taken for one that dies while attaching
        if self._phase == _WAITING and self._helper is not None:
            self._ask()

    def _start_next(self) -> None:
        """Start a helper in the next Python on the list."""
        self._retire()
        while self._candidates:
            command = self._candidates.pop(0)
            try:
                self._helper = _HelperProcess(command, self._env, self._helper_source)
            except (OSError, ValueError) as error:
                self._tried.append(f"{' '.join(command)} could not be started ({error})")
                continue
            self._phase = _STARTING
            self._deadline = time.monotonic() + HELLO_SECONDS
            return
        tried = f"Tried: {'; '.join(self._tried)}." if self._tried else "No Python was found on this PC."
        self._give_up(FridaUnavailable(f"{NO_FRIDA} {tried}"))

    def _ask(self) -> None:
        assert self._helper is not None
        self._events.clear()
        self._raised.clear()
        self._script_errors = []
        self._helper.send({"op": "attach"})
        self._phase = _ATTACHING
        self._deadline = time.monotonic() + ATTACH_SECONDS

    def _give_up(self, error: AgentError) -> None:
        self._retire()
        self._phase = _OVER
        self._failure = error

    def _retire(self) -> None:
        helper, self._helper = self._helper, None
        if helper is not None:
            helper.stop()

    def _name(self) -> str:
        return " ".join(self._helper.command) if self._helper is not None else "the helper"

    # -- what the helper says -----------------------------------------------------------------------------------------

    def _pump(self) -> None:
        """Take in what the helper has said since the last time. Runs on the client's thread, like all the rest."""
        helper = self._helper
        if helper is None:
            return
        for message in helper.take(time.monotonic()):
            if message is None:
                self._on_helper_ended(helper)
            else:
                self._on_message(message)
            if self._helper is not helper:
                return  # done with that helper; whatever else it said no longer matters

    def _on_helper_ended(self, helper: _HelperProcess) -> None:
        words = helper.last_words()
        said = f": {words}" if words else ""
        if self._phase == _STARTING:
            self._tried.append(f"{self._name()} stopped right away{said}")
            self._start_next()
        elif self._phase == _ATTACHING:
            # Not worth a new helper every few seconds: whatever stopped this one would stop the next.
            self._give_up(AgentUnusable(f"The helper that reaches the game stopped while it was getting in{said}. "
                                        "/attach tries again."))
        elif self._phase == _WAITING:
            self._retire()
            self._phase = _OVER
            self._failure = AgentError(f"The helper that reaches the game stopped{said}")
        elif self._gone is None:
            self._tell_raised(gone=True)
            self._gone = f"the helper that reaches the game stopped{said}"

    def _on_message(self, message: dict[str, Any]) -> None:
        assert self._helper is not None
        event = message.get("event")
        text = str(message.get("text", ""))
        if event == "hello":
            if self._phase == _STARTING:
                self.command = self._helper.command
                self.python = str(message.get("python") or self._name())
                self.frida_version = str(message.get("frida") or "?")
                self._helper.send({"op": "setup", "process": PROCESS_NAME, "source": self._agent_source})
                self._ask()
        elif event == "failed":
            self._on_failed(str(message.get("kind")), text)
        elif event == "attached":
            if self._phase == _ATTACHING:
                raw = message.get("state")
                state = raw if isinstance(raw, dict) else {}
                self._take_state(state)
                self.description = f"BioShock Remastered ({state.get('build', 'unknown build')})"
                self._phase = _ATTACHED
                self._events.appendleft(AgentMessage(f"Using Frida {self.frida_version} from {self.python}."))
        elif event == "state":
            raw = message.get("state")
            if self._phase == _ATTACHED and isinstance(raw, dict):
                self._take_state(raw)
        elif event == "message":
            raw = message.get("message")
            raised = raised_from_message(raw) if isinstance(raw, dict) else None
            if raised is not None:
                # Kept until the command's result follows, and told in one line then. If the game goes away
                # instead, it is told at that moment: it is then the best account of why there is.
                during = self._raised.setdefault(raised[0], _Raised(raised[1]))
                if routine_lookup(during.command, raised[2]):
                    during.routine += 1
                else:
                    during.described.append(raised[2])
            translated = event_from_message(raw) if isinstance(raw, dict) else None
            if isinstance(translated, (CommandResult, ActionResult)) and translated.command_id in self._raised:
                during = self._raised[translated.command_id]
                missing = missing_class(during.command, during.described)
                if missing is not None and isinstance(translated, CommandResult) and translated.handled:
                    # The command ran, and the game said in passing that it has no such class. Nothing arrived,
                    # and asking again would get the same answer.
                    del self._raised[translated.command_id]
                    translated = ActionResult(translated.command_id, False, f"the game has no class {missing}")
                else:
                    # First what the game raised on the way, then how the command ended.
                    total = raw["payload"].get("raised") if isinstance(raw, dict) else None
                    self._tell_raised(translated.command_id, total if isinstance(total, int) else None)
            if translated is not None:
                self._events.append(translated)
                if isinstance(translated, AgentMessage) and translated.text.startswith(SCRIPT_ERROR):
                    self._script_errors.append(translated.text)
            answer = answer_from_message(raw) if isinstance(raw, dict) else None
            if answer is not None:
                self._events.append(answer)
        elif event == "log":
            self._events.append(AgentMessage(text))  # what the agent prints is shown to the player
        elif event == "detached":
            if self._phase == _ATTACHED and self._gone is None:
                self._tell_raised(gone=True)
                reason = str(message.get("reason") or "the game closed")
                self._gone = DETACH_REASONS.get(reason, reason)

    def _on_failed(self, kind: str, text: str) -> None:
        if kind == "frida_missing":
            if self._phase == _STARTING:
                self._tried.append(f"{self._name()} has no Frida ({text})")
                self._start_next()
            return
        if self._phase != _ATTACHING:
            return
        if kind == "script":
            # A script that throws while loading leaves the helper with "no such method". What the script itself
            # reported is the cause, and injecting it again would fail the same way.
            details = "; ".join(self._script_errors) or text
            self._give_up(AgentScriptFailed(f"The game agent could not start: {details}. /attach tries again."))
            return
        self._phase = _WAITING
        if kind == "not_running":
            self._failure = GameNotRunning("BioShock Remastered is not running")
        elif kind == "ambiguous":
            self._failure = AgentError(f"More than one {PROCESS_NAME} is running; close the extra one ({text})")
        elif kind == "denied":
            # Not tried again by itself: Frida may ask Windows for administrator rights every time it is refused.
            self._give_up(AgentUnusable(f"Windows did not let the client into the game ({text}). If the game runs "
                                        "as administrator, Archipelago has to as well. /attach tries again."))
        elif kind == "closed":
            self._failure = AgentError("The game closed while the client was connecting to it")
        else:
            self._failure = AgentError(f"Could not attach to the game: {text}")

    def _tell_raised(self, command_id: int | None = None, total: int | None = None, gone: bool = False) -> None:
        """Pass on what the game raised during one command, or during every command still open."""
        for key in ([command_id] if command_id is not None else sorted(self._raised)):
            during = self._raised.pop(key, None)
            if during is not None and during.described:
                counted = None if total is None else total - during.routine
                self._events.append(raised_summary(during.command, during.described, counted, gone))

    def _take_state(self, raw: dict[str, Any]) -> None:
        self._state = state_from_agent(raw)
        self._state_at = time.monotonic()

    # -- GameAgent ----------------------------------------------------------------------------------------------------

    def read_state(self) -> GameState:
        self._pump()
        if self._gone is not None:
            raise AgentError(self._gone)
        if time.monotonic() - self._state_at > STALE_SECONDS:
            # The game has stopped answering (frozen, most likely). Where it was is still the best guess at where
            # it is. That it is ready for anything is what no longer holds.
            return dataclasses.replace(self._state, ready=False)
        return self._state

    def poll_events(self) -> list[AgentEvent]:
        self._pump()
        events = list(self._events)
        self._events.clear()
        return events

    def run_command(self, command_id: int, command: str) -> None:
        self._send({"op": "exec", "id": command_id, "command": command})

    def run_action(self, command_id: int, action: str) -> None:
        self._send({"op": "action", "id": command_id, "name": action})

    def kill_player(self) -> None:
        self._send({"op": "action", "id": 0, "name": "kill"})

    def _send(self, message: dict[str, Any]) -> None:
        if self._gone is not None or self._helper is None:
            raise AgentError(self._gone or "not attached")
        self._helper.send(message)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._gone = self._gone or "detached"
        self._phase = _OVER
        self._retire()  # the helper takes the agent, and its hook, out of the game as it leaves

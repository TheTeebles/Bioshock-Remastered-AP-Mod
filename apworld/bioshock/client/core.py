"""
The client's brain. It decides what to send to Archipelago and what to send to the game, and nothing else.

It never touches a socket or the game. The Archipelago wrapper and the game agent feed it facts
(`on_connected`, `on_items`, `on_state`, ...) and then carry out whatever `drain()` returns.

Rules it implements:
  * Items are given one at a time, in the order the server sent them, and only counted as given once the game
    confirms. A command is never sent twice while its first copy may still be waiting inside the game.
  * An item that cannot be given is set aside, not lost: it is tried again once the client knows how to give it,
    or when the player asks. So is the item the game was busy with when it closed: it may be what closed it.
  * A location is sent only when the level it belongs to is unlocked ("soft" level access). Anything collected
    earlier is kept and sent the moment the access item arrives. The goal works the same way.
  * Level completion comes from how far into the story the player has been. With Open Rapture it comes from the
    levels visited and the quests that close a chapter, and the agent is told where the bathysphere may go.
  * All of that survives restarts through Archipelago data storage.
"""
from __future__ import annotations

import json

from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..data import GOAL_LEVEL, ItemKind
from . import flash_movies, game_data, travel
from .game_data import Delivery
from .protocol import (
    Action, ActionResult, AgentEvent, AgentMessage, CommandResult, CommandStarted, GameState, KillPlayer,
    LittleSisterResolved, Notice, PlayerDied, RunAction, RunCommand, SaveState, ScoutedItem, ScoutLocations,
    SendChecks, SendDeath, SendGoal, TravelRedirected,
)

SAVE_VERSION = 1
SUPPORTED_SLOT_DATA_VERSION = 4  # keep in step with SLOT_DATA_VERSION in world.py
OPEN_RAPTURE = 2  # level_access in the slot data: LevelAccess.option_open_rapture

COMMAND_TIMEOUT = 5.0  # seconds without an answer before the player is told the game has not confirmed yet
RETRY_DELAY = 1.0  # pause before retrying a command the game refused
MAX_ATTEMPTS = 3  # tries per command before the item is set aside
DEATHLINK_GRACE = 10.0  # after killing the player for a DeathLink, ignore their death for this long
DEATHLINK_WINDOW = 30.0  # a received death that cannot be carried out within this time is dropped
LEVEL_CONFIRMATIONS = 2  # a level has to be seen this many polls in a row before it counts
QUIET_ANNOUNCEMENTS = 3  # more held checks than this at once are announced in one line

# Why an item was set aside instead of given.
UNKNOWN = "unknown"  # the client has no way to give it yet (no game class known, or an item ID it has never heard of)
REFUSED = "refused"  # the game refused it, or could not do it
NEEDS_WEAPON = "needs weapon"  # a weapon upgrade, kept until the player holds the weapon


@dataclass
class _InFlight:
    index: int  # position in the received-items list
    delivery: Delivery
    step: int = 0
    command_id: int = 0
    sent_at: float = 0.0
    attempts: int = 0
    retry_at: float | None = None
    warned: bool = False  # the player has been told that the game is taking its time
    started: bool = False  # the game has taken the command up, and has not answered it yet


class BridgeCore:
    def __init__(self) -> None:
        self._out: list[Action] = []

        # Archipelago side
        self.connected = False
        self.identity: tuple[Any, ...] | None = None  # (seed, team, slot) the saved state belongs to
        self.access_items: dict[str, str | None] = {}
        self.world_items_shuffled = False
        self.station_touch = False  # stations open no menu: the agent is asked to skip it
        self.shop_checks = False  # Gatherer's Garden lines are checks: they sell stand-ins for multiworld items
        self.open_rapture = False  # access items open travel itself: the agent is told where the bathysphere goes
        self.scouted: dict[int, ScoutedItem] = {}  # location -> what sits there, for the Garden lines
        self._scouts_asked = False  # asked the server this connection
        self.death_link = False
        self.valid_locations: frozenset[int] = frozenset()
        self.checked: set[int] = set()  # confirmed by the server
        self.sent: set[int] = set()  # sent this connection, not confirmed yet
        self.items: list[int] = []
        self._received_names: frozenset[str] = frozenset()
        self.goal_sent = False

        # Saved to Archipelago data storage
        self.delivered = 0  # how many of `items` have been dealt with: given, or set aside in `skipped`
        self.skipped: dict[int, str] = {}  # index in `items` -> UNKNOWN or REFUSED, for items that were not given
        self.furthest = 0  # furthest story index the player has reached
        self.visited: set[str] = set()  # levels the player has been in
        self.goal_reached = False
        self.collected: set[int] = set()  # every location the game has reported, sent or not
        self._dirty = False

        # Game side
        self.agent_attached = False
        self.agent_description = ""
        self.state = GameState()
        self.level: str | None = None
        self._level_candidate: str | None = None
        self._level_streak = 0
        self._fontaine_armed = False
        self._warned_new_game = False
        self._in_flight: _InFlight | None = None
        self._stopping = False
        self._next_command_id = 1
        self._second_tries: list[int] = []  # set-aside items that are worth trying now
        self._look_for_second_tries = False
        self._pending_resync = 0  # how many items to give again once nothing is on its way
        self._local_reset = False  # /resync lowered the count and the server has not been told yet
        self._pending_kill = False
        self._kill_deadline: float | None = None
        self._ignore_death_until = 0.0
        self._touch_command_id: int | None = None  # the agent has been asked to skip station menus, under this id
        self._garden_command_id: int | None = None  # the last Garden lines sent to the agent went under this id
        self._garden_sent: str | None = None  # ... and were these, so the same lines are not sent again
        self._travel_command_id: int | None = None  # the last travel rules sent to the agent went under this id
        self._travel_sent: str | None = None  # ... and were these
        self._travel_trouble_shown = False
        self._entered: str | None = None  # the level the player was last told about where its way on leads
        # Makes the Garden's menu with the AP icon and returns the folder it is in (None: not for this agent, such as
        # the simulated game). Raises an exception whose message says why it cannot.
        self.prepare_movies: Callable[[], str | None] = lambda: None
        self._movies_asked = False  # this agent has been given the menu with the AP icon, or it could not be made
        self._movies_command_id: int | None = None
        self._announced_locked: set[int] = set()
        self._announced_goal_locked = False

    # ---------------------------------------------------------------------------------------------------------------
    # Output
    # ---------------------------------------------------------------------------------------------------------------

    def drain(self) -> list[Action]:
        actions, self._out = self._out, []
        return actions

    def _notice(self, text: str, warning: bool = False) -> None:
        self._out.append(Notice(text, warning))

    @property
    def busy(self) -> bool:
        """True while items are on their way to the game or about to be, so the caller can poll faster."""
        return self._in_flight is not None or (
            not self._stopping and self.connected and self.agent_attached and self.state.ready
            and (self.delivered < len(self.items) or bool(self._second_tries)))

    @property
    def awaiting_answer(self) -> bool:
        """True while a command has gone to the game and its answer has not come back."""
        return self._in_flight is not None and self._in_flight.retry_at is None

    def stop_deliveries(self) -> None:
        """The client is closing: start no more items, but still take the answer for the one on its way."""
        self._stopping = True

    # ---------------------------------------------------------------------------------------------------------------
    # Archipelago side
    # ---------------------------------------------------------------------------------------------------------------

    def on_connected(self, identity: tuple[Any, ...], slot_data: Mapping[str, Any], valid_locations: Collection[int],
                     checked_locations: Collection[int], saved: Mapping[str, Any] | None,
                     item_ids: Sequence[int] = ()) -> None:
        """Call once the server has accepted the connection and the saved state has been fetched.

        `item_ids` is whatever the server has already sent on this connection; later updates go to `on_items`.
        """
        if identity != self.identity:
            self._forget_slot()
            self.identity = identity
            # Only for a slot we were not on before: /deathlink then holds for the rest of the session.
            self.death_link = bool(slot_data.get("death_link", 0))
        self.connected = True
        self.sent = set()
        self.goal_sent = False
        self.valid_locations = frozenset(valid_locations)
        self.checked = set(checked_locations)
        self.on_items(item_ids)

        version = slot_data.get("slot_data_version", 0)
        if version > SUPPORTED_SLOT_DATA_VERSION:
            self._notice(f"This seed was generated by a newer BioShock apworld (slot data v{version}). "
                         "Update the apworld so the client matches.", warning=True)
        self.access_items = dict(slot_data.get("level_access_items") or {})
        self.world_items_shuffled = bool(slot_data.get("world_item_checks", 0))
        self.station_touch = bool(slot_data.get("station_touch", False))
        self.shop_checks = bool(slot_data.get("shop_checks", 0))
        self.open_rapture = slot_data.get("level_access") == OPEN_RAPTURE
        self._travel_sent = None
        self._scouts_asked = False
        self._garden_sent = None

        self._merge_saved(saved)
        self._look_for_second_tries = True

    def on_disconnected(self) -> None:
        self.connected = False
        self.sent = set()
        self.goal_sent = False

    def on_items(self, item_ids: Sequence[int]) -> None:
        """The full, ordered list of items the server has sent this slot so far."""
        self.items = list(item_ids)
        self._received_names = frozenset(
            game_data.ITEM_NAME[item_id] for item_id in self.items if item_id in game_data.ITEM_NAME)

    def on_checked(self, checked_locations: Iterable[int]) -> None:
        self.checked.update(checked_locations)

    def on_scouted(self, items: Mapping[int, ScoutedItem]) -> None:
        """The server's answer to ScoutLocations: what sits at each location."""
        self.scouted.update(items)

    def on_deathlink(self) -> None:
        # With no game running there is nobody to kill, and a death hours later would make no sense.
        if self.death_link and self.agent_attached:
            self._pending_kill = True
            self._kill_deadline = None

    def has_access(self, level: str) -> bool:
        access_item = self.access_items.get(level)
        return access_item is None or access_item in self._received_names

    # ---------------------------------------------------------------------------------------------------------------
    # Game side
    # ---------------------------------------------------------------------------------------------------------------

    def on_agent_attached(self, description: str) -> None:
        self.agent_attached = True
        self.agent_description = description
        self._reset_game_view()
        self._look_for_second_tries = True
        self._notice(f"Game connected: {description}.")

    def on_agent_detached(self, reason: str = "") -> None:
        if self.agent_attached:
            self._notice(f"Game disconnected{': ' + reason if reason else '.'}", warning=bool(reason))
        flight = self._in_flight
        if flight is not None and flight.started:
            # The game went away in the middle of this very command. Giving the item again the moment the game is
            # back would, if the command is what closed it, close it again, and so on for ever.
            self._notice(f"The game went away while it was being given {flight.delivery.description}. In case "
                         "that is what closed it, the item is set aside; /retry tries it again.", warning=True)
            self._finish(flight, given=False)
        self.agent_attached = False
        self._reset_game_view()

    def _reset_game_view(self) -> None:
        self.state = GameState()
        self.level = None
        self._level_candidate = None
        self._level_streak = 0
        self._fontaine_armed = False
        self._warned_new_game = False
        self._in_flight = None  # an item that was on its way starts again from its first command
        self._pending_kill = False
        self._touch_command_id = None  # a new agent is asked again
        self._garden_command_id = None
        self._garden_sent = None
        self._travel_command_id = None
        self._travel_sent = None
        self._movies_asked = False
        self._movies_command_id = None
        # ... and what was about to be sent to the agent that has gone must not reach the next one as well.
        self._out = [action for action in self._out if not isinstance(action, (RunCommand, RunAction, KillPlayer))]

    def on_state(self, state: GameState) -> None:
        held_before = self.state.weapons or frozenset()
        was_pending = self.state.weapons_pending
        self.state = state
        if state.weapons and state.weapons - held_before:
            self._look_for_weapon_upgrades(state.weapons)
        elif was_pending and not state.weapons_pending and state.weapons is None:
            self._look_for_weapon_upgrades(None)  # the agent looked and can't tell after all: they go in as before
        self._track_level(state)
        self._track_goal(state)

        found = self._locations_in(state)
        found.update(game_data.LEVEL_COMPLETION_LOCATION[level] for level in self._completed_levels(state)
                     if level in game_data.LEVEL_COMPLETION_LOCATION)
        new = found - self.collected
        if new:
            self.collected |= new
            self._dirty = True

    def on_event(self, event: AgentEvent, now: float) -> None:
        if isinstance(event, CommandStarted):
            flight = self._in_flight
            if flight is not None and flight.command_id == event.command_id:
                flight.started = True
        elif isinstance(event, CommandResult):
            self._on_result(event.command_id, event.handled, now, "the game refused the command", retry=True)
        elif isinstance(event, ActionResult) and event.command_id == self._touch_command_id:
            if not event.ok:
                self._notice(f"Power to the People stations will still open their menu, which also gives an "
                             f"upgrade: {event.reason}.", warning=True)
        elif isinstance(event, ActionResult) and event.command_id == self._garden_command_id:
            if not event.ok:
                self._notice(f"The Gatherer's Gardens here still sell their own genes: {event.reason}.",
                             warning=True)
        elif isinstance(event, ActionResult) and event.command_id == self._travel_command_id:
            if not event.ok and not self._travel_trouble_shown:
                self._travel_trouble_shown = True
                self._notice(f"The bathysphere and the level changes are not locked to your access items: "
                             f"{event.reason}.", warning=True)
        elif isinstance(event, TravelRedirected):
            text = travel.blocked_notice(event.to or "", event.landed, self._received_names)
            if text is not None:
                self._notice(text, warning=True)
        elif isinstance(event, ActionResult) and event.command_id == self._movies_command_id:
            if event.ok:
                self._notice("Gatherer's Garden lines that sell other players' items show the Archipelago icon from "
                             "the next time you load a save or a level.")
            else:
                self._notice(f"Gatherer's Garden lines that sell other players' items show a bag of chips: "
                             f"{event.reason}.", warning=True)
        elif isinstance(event, ActionResult):
            # The agent either supports an action or it doesn't, so asking again would not help.
            self._on_result(event.command_id, event.ok, now, event.reason or "the game could not do it",
                            retry=False)
        elif isinstance(event, PlayerDied):
            self._on_player_died(now)
        elif isinstance(event, AgentMessage):
            self._notice(event.text)
        elif isinstance(event, LittleSisterResolved):
            self._on_little_sister(event.map)

    def tick(self, now: float) -> None:
        """Advance everything that depends on time or on a combination of both sides. Call a few times a second."""
        self._send_ready_checks()
        self._send_goal_if_ready()
        self._ask_for_touch()
        self._scout_gardens()
        self._send_garden_lines()
        self._send_travel_rules()
        self._redirect_movies()
        self._pump_delivery(now)
        self._pump_deathlink(now)
        if self._dirty and self.connected:
            self._dirty = False
            self._local_reset = False
            self._out.append(SaveState(self.snapshot()))

    # ---------------------------------------------------------------------------------------------------------------
    # Player commands
    # ---------------------------------------------------------------------------------------------------------------

    def resync(self, count: int | None = None) -> int:
        """Give the last `count` items again (all of them when None). Returns how many that is.

        It takes effect once nothing is on its way to the game, so that item does not arrive an extra time.
        """
        again = self.delivered if count is None else max(0, min(count, self.delivered))
        self._pending_resync = max(self._pending_resync, again)
        return again

    def retry_set_aside(self) -> int:
        """Try again to give every set-aside item the client has a way to give. Returns how many that is."""
        self._second_tries = [index for index in sorted(self.skipped) if self._plan(index) is not None]
        self._look_for_second_tries = False
        return len(self._second_tries)

    def status_lines(self) -> list[str]:
        waiting = sum(1 for reason in self.skipped.values() if reason == UNKNOWN)
        for_weapons = sum(1 for reason in self.skipped.values() if reason == NEEDS_WEAPON)
        refused = len(self.skipped) - waiting - for_weapons
        lines = [
            f"Archipelago: {'connected' if self.connected else 'not connected'}",
            f"Game: {self.agent_description if self.agent_attached else 'not connected'}"
            + (f", in {self.level}" if self.level else
               "" if not self.state.map else
               f", outside Rapture's levels ({self.state.map})" if game_data.known_place(self.state.map) else
               f", in a map the client has no level for yet ({self.state.map})"),
            f"Items: {self.delivered - len(self.skipped)} of {len(self.items)} given"
            + (f", {waiting} the client cannot give yet" if waiting else "")
            + (f", {for_weapons} waiting for their weapon" if for_weapons else "")
            + (f", {refused} the game refused (/retry tries them again)" if refused else "")
            + (", the rest when the game is ready for them"
               if self.delivered < len(self.items) and self.agent_attached and not self.state.ready else ""),
        ]
        held = self.held_locations()
        waiting_for = sorted({str(self.access_items.get(game_data.LOCATION_LEVEL[location])) for location in held})
        lines.append(f"Checks: {len(self.checked | self.sent)} sent"
                     + (f", {len(held)} held until you receive {', '.join(waiting_for)}" if held else ""))
        if self.goal_reached:
            lines.append("Goal: Fontaine defeated" + ("" if self.has_access(GOAL_LEVEL) else
                                                    f", held until you receive {self.access_items.get(GOAL_LEVEL)}"))
        return lines

    def held_locations(self) -> list[int]:
        """Collected locations that are waiting for their level's access item."""
        return sorted(location for location in self.collected
                      if location in self.valid_locations and location not in self.checked
                      and not self.has_access(game_data.LOCATION_LEVEL[location]))

    # ---------------------------------------------------------------------------------------------------------------
    # Saved state
    # ---------------------------------------------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Everything worth keeping between sessions. Stored on the Archipelago server."""
        return {
            "version": SAVE_VERSION,
            "delivered": self.delivered,
            "skipped": [[index, reason] for index, reason in sorted(self.skipped.items())],
            "furthest": self.furthest,
            "visited": sorted(self.visited),
            "goal": self.goal_reached,
            "collected": sorted(self.collected),
        }

    def _forget_slot(self) -> None:
        self.items = []
        self._received_names = frozenset()
        self.delivered = 0
        self.skipped = {}
        self.furthest = 0
        self.visited = set()
        self.goal_reached = False
        self.collected = set()
        self.scouted = {}
        self._second_tries = []
        self._pending_resync = 0
        self._local_reset = False
        self._announced_locked = set()
        self._announced_goal_locked = False
        self._in_flight = None
        self._pending_kill = False
        self._dirty = False

    def _merge_saved(self, saved: Mapping[str, Any] | None) -> None:
        """Combine what the server stored with what this session already knows; neither side ever goes backwards."""
        if not isinstance(saved, Mapping) or saved.get("version") != SAVE_VERSION:
            self._dirty = True  # nothing usable stored yet: write our view
            return
        saved_delivered = int(saved.get("delivered", 0))
        if saved_delivered > self.delivered and not self._local_reset:
            # The server is ahead: an earlier session got further. Take its item bookkeeping whole.
            self.delivered = saved_delivered
            self.skipped = self._read_skipped(saved.get("skipped"), saved_delivered)
        self.furthest = max(self.furthest, int(saved.get("furthest", 0)))
        self.visited |= {level for level in saved.get("visited", ()) if level in game_data.STORY_INDEX}
        self.goal_reached = self.goal_reached or bool(saved.get("goal", False))
        self.collected |= {int(location) for location in saved.get("collected", ())}
        if self.snapshot() != dict(saved):
            self._dirty = True  # this session knows more than the server does

    @staticmethod
    def _read_skipped(raw: Any, delivered: int) -> dict[int, str]:
        skipped: dict[int, str] = {}
        for entry in raw if isinstance(raw, (list, tuple)) else ():
            if (isinstance(entry, (list, tuple)) and len(entry) == 2 and isinstance(entry[0], int)
                    and 0 <= entry[0] < delivered and entry[1] in (UNKNOWN, REFUSED, NEEDS_WEAPON)):
                skipped[entry[0]] = entry[1]
        return skipped

    # ---------------------------------------------------------------------------------------------------------------
    # Checks and goal
    # ---------------------------------------------------------------------------------------------------------------

    def _track_level(self, state: GameState) -> None:
        if (game_data.at_crash_site(state.map, state.level_value) and self.connected and self.delivered > 0
                and not self._warned_new_game):
            # Given items live in the save file. A fresh save has none of them, and the client cannot tell
            # by itself whether the player wants them again.
            self._warned_new_game = True
            self._notice(f"This looks like a new game, but {self.delivered} item(s) were already given to an "
                         "earlier save. Type /resync to receive them again.", warning=True)

        level = state.level or game_data.level_of(state.map, state.level_value)
        if level is None:
            self._level_candidate = None
            self._level_streak = 0
            if state.map is not None:
                self.level = None  # the game names a map, and it is not one of the levels
            return
        if level == self._level_candidate:
            self._level_streak += 1
        else:
            self._level_candidate = level
            self._level_streak = 1
        if self._level_streak < LEVEL_CONFIRMATIONS:
            return
        self.level = level
        index = game_data.STORY_INDEX[level]
        if index > self.furthest:
            self.furthest = index
            self._dirty = True
        if level not in self.visited:
            self.visited.add(level)
            self._dirty = True
        if self.open_rapture and level != self._entered:
            self._entered = level
            text = travel.locked_way_on(level, self._received_names)
            if text is not None:
                self._notice(text)

    def _completed_levels(self, state: GameState) -> list[str]:
        if self.open_rapture:  # levels come in any order: what counts is where the player has been, and the story
            return travel.completed_levels(self.visited, state.quests or ())
        return game_data.completed_levels(self.furthest)

    def _track_goal(self, state: GameState) -> None:
        in_arena = game_data.in_fontaine_arena(state.map, state.level_value)
        if state.in_level is False:
            in_arena = False  # a loading screen, or back at the menu: the fight was left, whatever else is read
        if in_arena:
            if state.fontaine_phase == game_data.FONTAINE_FIGHT_PHASE:
                self._fontaine_armed = True
        elif in_arena is False:
            self._fontaine_armed = False  # a loading screen or another level: the fight was left
        # When the client cannot tell where the player is, things stay as they were: a pause, a hitch in which
        # the game names no map for a moment, an arena that shows a level number this client did not expect.
        # Losing track of the fight then would cost the player their goal.
        defeated = self._fontaine_armed and state.fontaine_phase == game_data.FONTAINE_DEFEATED_PHASE
        if (state.goal or defeated) and not self.goal_reached:
            self.goal_reached = True
            self._dirty = True

    def _on_little_sister(self, map_name: str | None) -> None:
        """One more sister dealt with: she becomes the first sister of her level that has not been collected yet.
        Which sister of the level she was cannot be told, so the order is the order they are dealt with."""
        level = game_data.level_of(map_name, None) if map_name is not None else self.level
        if level is None:
            self._notice(f"A Little Sister was rescued or harvested in {map_name or 'a map the client does not know'}, "
                         "which is not one of the levels, so no check goes with it.", warning=True)
            return
        index = 1
        while (level, index) in game_data.LITTLE_SISTER_LOCATION:
            location = game_data.LITTLE_SISTER_LOCATION[level, index]
            if location not in self.collected:
                self.collected.add(location)
                self._dirty = True
                return
            index += 1
        # Every sister of this level is collected already: a save loaded from before an earlier rescue, say.

    @staticmethod
    def _locations_in(state: GameState) -> set[int]:
        found: set[int] = set()
        for number in state.diaries or ():
            location = game_data.diary_location(number)
            if location is not None:
                found.add(location)
        for level, count in (state.little_sisters or {}).items():
            found.update(game_data.LITTLE_SISTER_LOCATION[level, k] for k in range(1, count + 1)
                         if (level, k) in game_data.LITTLE_SISTER_LOCATION)
        for level, count in (state.stations or {}).items():
            found.update(game_data.STATION_LOCATION[level, k] for k in range(1, count + 1)
                         if (level, k) in game_data.STATION_LOCATION)
        for key in state.milestones or ():
            if key in game_data.MILESTONE_LOCATION:
                found.add(game_data.MILESTONE_LOCATION[key])
        for level, lines in (state.garden_bought or {}).items():
            found.update(game_data.GARDEN_LOCATION[level, line] for line in lines
                         if (level, line) in game_data.GARDEN_LOCATION)
        return found

    def _send_ready_checks(self) -> None:
        if not self.connected:
            return
        ready: list[int] = []
        newly_held: list[int] = []
        for location in sorted(self.collected):
            if location not in self.valid_locations or location in self.checked or location in self.sent:
                continue
            if self.has_access(game_data.LOCATION_LEVEL[location]):
                ready.append(location)
            elif location not in self._announced_locked:
                self._announced_locked.add(location)
                newly_held.append(location)
        if newly_held:
            self._announce_held(newly_held)
        if ready:
            self.sent.update(ready)
            self._out.append(SendChecks(tuple(ready)))

    def _announce_held(self, locations: list[int]) -> None:
        def access_item(location: int) -> str:
            return str(self.access_items[game_data.LOCATION_LEVEL[location]])

        if len(locations) <= QUIET_ANNOUNCEMENTS:
            for location in locations:
                self._notice(f"Found {game_data.LOCATION_NAME[location]}. "
                             f"It will be sent once you receive {access_item(location)}.")
        else:  # a whole level at once, or everything again after a restart: one line, not hundreds
            needed = ", ".join(sorted({access_item(location) for location in locations}))
            self._notice(f"Found {len(locations)} checks in levels that are still locked. "
                         f"They will be sent once you receive: {needed}.")

    def _send_goal_if_ready(self) -> None:
        if not self.connected or not self.goal_reached or self.goal_sent:
            return
        if self.has_access(GOAL_LEVEL):
            self.goal_sent = True
            self._out.append(SendGoal())
        elif not self._announced_goal_locked:
            self._announced_goal_locked = True
            self._notice(f"Fontaine is defeated. Your goal will be sent once you receive "
                         f"{self.access_items[GOAL_LEVEL]}.")

    # ---------------------------------------------------------------------------------------------------------------
    # Item delivery
    # ---------------------------------------------------------------------------------------------------------------

    def _plan(self, index: int) -> Delivery | None:
        """How to give the item at `index` of the received list, or None if the client has no way yet."""
        if not 0 <= index < len(self.items):
            return None
        item_id = self.items[index]
        name = game_data.ITEM_NAME.get(item_id)
        if name is None:
            return None
        return game_data.plan_delivery(name, self.items[:index + 1].count(item_id), self.world_items_shuffled)

    def _pump_delivery(self, now: float) -> None:
        if self._stopping:
            return
        flight = self._in_flight
        if flight is not None:
            if flight.retry_at is not None:
                if now >= flight.retry_at and self.state.ready:
                    self._send_step(flight, now)
            elif not flight.warned and now - flight.sent_at > COMMAND_TIMEOUT:
                # Never send it again: the first copy may still be waiting inside the game (a paused game runs
                # no commands), and then the item would arrive twice.
                flight.warned = True
                self._notice(f"Still waiting for the game to confirm {flight.delivery.description}. "
                             "It goes through as soon as the game is running again.", warning=True)
            return

        if self._pending_resync:
            self._apply_resync()
        if not self.connected:
            return
        if self._look_for_second_tries:
            # Once per connection and per game start: set-aside items that this client now knows how to give.
            self._look_for_second_tries = False
            self._second_tries = [index for index in sorted(self.skipped)
                                  if self.skipped[index] == UNKNOWN and self._plan(index) is not None]
            if self.state.weapons:
                self._look_for_weapon_upgrades(self.state.weapons)
        if not (self.agent_attached and self.state.ready):
            return

        while self._second_tries:
            index = self._second_tries.pop(0)
            delivery = self._plan(index)
            if index not in self.skipped or delivery is None:
                continue
            if self._waits_for_weapon(delivery):
                self.skipped[index] = NEEDS_WEAPON
                continue
            if delivery.is_instant:
                del self.skipped[index]
                self._dirty = True
                continue
            self._start(index, delivery, now)
            return

        while self.delivered < len(self.items):
            index = self.delivered
            name = game_data.ITEM_NAME.get(self.items[index])
            delivery = self._plan(index)
            if delivery is None:
                if name is None:
                    self._notice(f"Received an item this client does not know (ID {self.items[index]}). "
                                 "It is kept for after you update the apworld.", warning=True)
                else:
                    self._notice(f"Can't deliver {name} yet: the client does not know its game class. "
                                 "It is kept for when it does.", warning=True)
                self.skipped[index] = UNKNOWN
                self._advance()
                continue
            if self._waits_for_weapon(delivery):
                self._notice(f"{delivery.description} waits until you have the {delivery.needs_weapon}."
                             if not self.state.weapons_pending else
                             f"{delivery.description} waits until the game has looked at which weapons you hold.")
                self.skipped[index] = NEEDS_WEAPON
                self._advance()
                continue
            if delivery.is_instant:
                if name is not None and game_data.ITEM_KIND[name] == ItemKind.LEVEL_ACCESS:
                    self._notice(f"{name} received.")
                self._advance()
                continue
            self._start(index, delivery, now)
            return

    def _waits_for_weapon(self, delivery: Delivery) -> bool:
        """True for an upgrade whose weapon the player doesn't hold, or while the agent hasn't looked at the weapons
        yet. An agent that can't tell gets it as before."""
        if delivery.needs_weapon is None:
            return False
        held = self.state.weapons
        if held is None:
            return self.state.weapons_pending
        return delivery.needs_weapon not in held

    def _look_for_weapon_upgrades(self, held: frozenset[str] | None) -> None:
        """The player has picked up a weapon: the upgrades kept for it go in next. None: all the upgrades kept."""
        ready = [index for index, reason in sorted(self.skipped.items()) if reason == NEEDS_WEAPON
                 and (delivery := self._plan(index)) is not None and (held is None or delivery.needs_weapon in held)]
        self._second_tries += [index for index in ready if index not in self._second_tries]

    def _ask_for_touch(self) -> None:
        """With touch stations, ask each agent once to skip the station menu."""
        if not (self.station_touch and self.connected and self.agent_attached) or self._touch_command_id is not None:
            return
        self._touch_command_id = self._next_command_id
        self._next_command_id += 1
        self._out.append(RunAction(self._touch_command_id, "touch_stations"))

    def _garden_locations(self) -> list[int]:
        return [location for location in game_data.GARDEN_LOCATION.values() if location in self.valid_locations]

    def _scout_gardens(self) -> None:
        """With shop checks, ask the server once per connection what every Garden line holds."""
        if not (self.shop_checks and self.connected) or self._scouts_asked:
            return
        self._scouts_asked = True
        wanted = tuple(location for location in self._garden_locations() if location not in self.scouted)
        if wanted:
            self._out.append(ScoutLocations(wanted))

    def _send_garden_lines(self) -> None:
        """Tell the agent what the Gardens of the level the player is in should sell, once all of it is known."""
        if not (self.shop_checks and self.connected and self.agent_attached) or self.level is None:
            return
        lines = []
        for (level, line), location in sorted(game_data.GARDEN_LOCATION.items()):
            if level != self.level:
                continue
            if location not in self.valid_locations:
                # Not a check in this seed (Shop Checks set to first appearances): sold out, so that the Garden
                # never sells a gene from outside the multiworld.
                lines.append({"line": line, "name": "", "description": "", "price": 0, "bought": True})
                continue
            item = self.scouted.get(location)
            if item is None:
                return  # wait for the server's answer
            name, description = game_data.garden_text(item.item, item.player, item.flags, item.own)
            entry = {
                "line": line, "name": name, "description": description,
                "price": game_data.garden_price(item.item, item.flags, item.own),
                "bought": location in self.checked or location in self.sent or location in self.collected,
            }
            # The agent prices one of our own BioShock items as the game would, from its class, when it can.
            game_class = game_data.garden_class(item.item, item.own)
            if game_class is not None:
                entry["class"] = game_class
                entry["known"] = game_data.garden_price_known(item.item, item.own)
            icon = game_data.garden_icon(item.item, item.own)
            if icon is not None:
                entry["icon"] = icon  # only its icon: the line keeps the client's price
            lines.append(entry)
        if not lines:
            return
        offer = {"level": self.level, "map": self.state.map, "lines": lines}
        action = "garden_lines " + json.dumps(offer, separators=(",", ":"))
        if action == self._garden_sent:
            return
        self._garden_sent = action
        self._garden_command_id = self._next_command_id
        self._next_command_id += 1
        self._out.append(RunAction(self._garden_command_id, action))

    def _send_travel_rules(self) -> None:
        """With Open Rapture, tell the agent where the bathysphere may go, whenever that changes."""
        if not (self.open_rapture and self.connected and self.agent_attached):
            return
        rules = travel.travel_rules(self._received_names, self.visited)
        action = "travel_rules " + json.dumps(rules, separators=(",", ":"))
        if action == self._travel_sent:
            return
        self._travel_sent = action
        self._travel_command_id = self._next_command_id
        self._next_command_id += 1
        self._out.append(RunAction(self._travel_command_id, action))

    def _redirect_movies(self) -> None:
        """With shop checks, give each agent once the Garden's menu that can show the AP icon."""
        if not (self.shop_checks and self.connected and self.agent_attached) or self._movies_asked:
            return
        self._movies_asked = True
        try:
            folder = self.prepare_movies()
        except Exception as error:  # the icon is a nicety: the lines work without it
            self._notice(f"Gatherer's Garden lines that sell other players' items show a bag of chips: {error}.",
                         warning=True)
            return
        if folder is None:
            return
        self._movies_command_id = self._next_command_id
        self._next_command_id += 1
        offer = {"folder": folder, "files": list(flash_movies.FILES)}
        self._out.append(RunAction(self._movies_command_id,
                                   "redirect_movies " + json.dumps(offer, separators=(",", ":"))))

    def _start(self, index: int, delivery: Delivery, now: float) -> None:
        self._in_flight = _InFlight(index, delivery)
        self._send_step(self._in_flight, now)

    def _send_step(self, flight: _InFlight, now: float) -> None:
        flight.command_id = self._next_command_id
        self._next_command_id += 1
        flight.sent_at = now
        flight.retry_at = None
        flight.warned = False
        if flight.delivery.action is not None:
            self._out.append(RunAction(flight.command_id, flight.delivery.action))
        else:
            self._out.append(RunCommand(flight.command_id, flight.delivery.commands[flight.step]))

    def _on_result(self, command_id: int, ok: bool, now: float, failure: str, retry: bool) -> None:
        flight = self._in_flight
        if flight is None or flight.command_id != command_id:
            return  # an answer to something that has since been restarted
        flight.started = False  # answered: whatever happens to the game now, this command is not what did it
        if not ok:
            flight.attempts += 1
            if retry and flight.attempts < MAX_ATTEMPTS:
                flight.retry_at = now + RETRY_DELAY
                return
            commands = flight.delivery.commands
            if flight.delivery.action is not None or flight.step < len(commands) - flight.delivery.optional:
                self._notice(f"Could not deliver {flight.delivery.description}: {failure}. "
                             "It is set aside; /retry tries it again.", warning=True)
                self._finish(flight, given=False)
                return
            # An optional command at the end: what came before it did its part, so the item still counts.
            self._notice(f"While delivering {flight.delivery.description}, \"{commands[flight.step]}\" did not work: "
                         f"{failure}.", warning=True)
        flight.step += 1
        flight.attempts = 0
        if flight.delivery.action is not None or flight.step >= len(flight.delivery.commands):
            self._notice(f"Delivered {flight.delivery.description}.")
            self._finish(flight, given=True)
            self._pump_delivery(now)  # start the next item straight away
        else:
            self._send_step(flight, now)

    def _finish(self, flight: _InFlight, given: bool) -> None:
        self._in_flight = None
        if given:
            self.skipped.pop(flight.index, None)
        else:
            self.skipped[flight.index] = REFUSED
        if flight.index == self.delivered:  # a new item, as opposed to a second try of one that was set aside
            self.delivered += 1
        self._dirty = True

    def _advance(self) -> None:
        self.delivered += 1
        self._dirty = True

    def _apply_resync(self) -> None:
        self.delivered = max(0, self.delivered - self._pending_resync)
        self.skipped = {index: reason for index, reason in self.skipped.items() if index < self.delivered}
        self._second_tries = [index for index in self._second_tries if index < self.delivered]
        self._pending_resync = 0
        self._local_reset = True  # until the server knows, its higher count must not win
        self._dirty = True

    # ---------------------------------------------------------------------------------------------------------------
    # DeathLink
    # ---------------------------------------------------------------------------------------------------------------

    def _pump_deathlink(self, now: float) -> None:
        if not self._pending_kill:
            return
        if self._kill_deadline is None:
            self._kill_deadline = now + DEATHLINK_WINDOW
        if self.agent_attached and self.state.ready:
            self._pending_kill = False
            self._ignore_death_until = now + DEATHLINK_GRACE
            self._out.append(KillPlayer())
        elif now > self._kill_deadline:
            self._pending_kill = False  # the game stayed busy for too long; the moment has passed

    def _on_player_died(self, now: float) -> None:
        if now < self._ignore_death_until:
            self._ignore_death_until = 0.0  # that was the death we caused; the next one is the player's own
            return
        if self.death_link and self.connected:
            self._out.append(SendDeath("was killed in Rapture"))

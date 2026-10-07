"""
Shared pieces for the client tests: the loop that runs the client core against the simulated game, a "player" who
plays the simulated game, and what the game should have been told to do for a given list of received items.
"""
from __future__ import annotations

import contextlib
from collections.abc import Iterable, Iterator, Sequence
from typing import Any
from unittest import mock

from ..client import game_data
from ..client.core import SUPPORTED_SLOT_DATA_VERSION, BridgeCore
from ..client.protocol import (
    Action, KillPlayer, Notice, RunAction, RunCommand, SaveState, SendChecks, SendDeath, SendGoal,
)
from ..client.simulated import SimulatedGame
from ..data import (
    AUDIO_DIARIES, DIRECTORS_COMMENTARY_LEVELS, LEVELS, LITTLE_SISTERS_PER_LEVEL, LOCATION_TABLE,
    POWER_TO_THE_PEOPLE_PER_LEVEL, STORY_MILESTONES,
)
from ..items import ITEM_NAME_TO_ID
from ..locations import LOCATION_NAME_TO_ID

IDENTITY = ("seed", 0, 1)


def item(name: str) -> int:
    return ITEM_NAME_TO_ID[name]


def loc(name: str) -> int:
    return LOCATION_NAME_TO_ID[name]


@contextlib.contextmanager
def not_known_yet(*item_names: str) -> Iterator[None]:
    """Run the client as an older one that does not know the game classes of these items yet."""
    with mock.patch.dict(game_data.GIVE_CLASS), mock.patch.dict(game_data.BUNDLES):
        for name in item_names:
            del (game_data.BUNDLES if name in game_data.BUNDLES else game_data.GIVE_CLASS)[name]
        yield


def play_level(game: SimulatedGame, level: str) -> None:
    """Go to a level and do everything in it that can be a check."""
    game.travel(level)
    for diary in AUDIO_DIARIES:
        if diary.level == level:
            game.collect_diary(diary.number)
    for _ in range(LITTLE_SISTERS_PER_LEVEL.get(level, 0)):
        game.deal_with_little_sister()
    for _ in range(POWER_TO_THE_PEOPLE_PER_LEVEL.get(level, 0)):
        game.use_station()
    if level in DIRECTORS_COMMENTARY_LEVELS:
        game.collect_reel()
    for milestone in STORY_MILESTONES:
        if milestone.level == level:
            game.reach_milestone()


def expected_deliveries(item_ids: Sequence[int]) -> tuple[list[str], list[str]]:
    """What the game should have been asked to do for these received items, in order: (console commands, actions).

    Items the client cannot deliver yet (no known game class) and items that need nothing sent are left out.
    """
    commands: list[str] = []
    actions: list[str] = []
    for index, item_id in enumerate(item_ids):
        delivery = game_data.plan_delivery(game_data.ITEM_NAME[item_id], item_ids[:index + 1].count(item_id))
        if delivery is None:
            continue
        commands += delivery.commands
        if delivery.action is not None:
            actions.append(delivery.action)
    return commands, actions


class Harness:
    """Runs the core the way the real client loop does: read the game, tick, carry out the actions."""

    def __init__(self, access_items: bool = True, death_link: bool = False) -> None:
        self.core = BridgeCore()
        self.game = SimulatedGame()
        self.now = 0.0
        self.slot_data: dict[str, Any] = {
            "slot_data_version": SUPPORTED_SLOT_DATA_VERSION,
            "death_link": int(death_link),
            "level_access_items": {level.name: level.access_item for level in LEVELS} if access_items else {},
        }
        self.valid = {location.id for location in LOCATION_TABLE}
        self.server_checked: set[int] = set()
        self.server_items: list[int] = []
        self.storage: dict[str, Any] | None = None
        self.sent_checks: list[int] = []
        self.goals = 0
        self.deaths_sent: list[str] = []
        self.notices: list[Notice] = []
        self.attached = False
        self.activity = 0  # how many things the core has asked for, notices aside

    # -- the two connections ----------------------------------------------------------------------------------------

    def connect(self, identity: tuple[Any, ...] = IDENTITY) -> "Harness":
        self.core.on_connected(identity, self.slot_data, self.valid, self.server_checked, self.storage,
                               self.server_items)
        return self

    def disconnect(self) -> None:
        self.core.on_disconnected()

    def attach(self) -> "Harness":
        self.attached = True
        self.core.on_agent_attached(self.game.description)
        return self

    def detach(self) -> None:
        self.attached = False
        self.core.on_agent_detached("the game closed")

    def restart_client(self, identity: tuple[Any, ...] = IDENTITY) -> "Harness":
        """Close the client and start it again: a new core that only knows what the server kept."""
        self.core = BridgeCore()
        self.connect(identity)
        if self.attached:
            self.attach()
        return self

    def receive(self, *names: str) -> None:
        self.server_items += [item(name) for name in names]
        self.core.on_items(self.server_items)

    # -- the loop ---------------------------------------------------------------------------------------------------

    def step(self, count: int = 1, seconds: float = 0.25) -> None:
        for _ in range(count):
            self.now += seconds
            if self.attached:
                self.core.on_state(self.game.read_state())
                for event in self.game.poll_events():
                    self.core.on_event(event, self.now)
            self.core.tick(self.now)
            self.perform(self.core.drain())

    def settle(self, limit: int = 5000) -> None:
        """Keep the loop going until the client has nothing left to do."""
        quiet = 0
        for _ in range(limit):
            before = self.activity
            self.step()
            quiet = quiet + 1 if self.activity == before and not self.core.busy else 0
            if quiet >= 3:  # a level only counts after it has been seen a couple of times in a row
                return
        raise AssertionError("the client never came to rest")

    def perform(self, actions: Iterable[Action]) -> None:
        for action in actions:
            if isinstance(action, Notice):
                self.notices.append(action)
                continue
            self.activity += 1
            if isinstance(action, RunCommand):
                self.game.run_command(action.command_id, action.command)
            elif isinstance(action, RunAction):
                self.game.run_action(action.command_id, action.action)
            elif isinstance(action, KillPlayer):
                self.game.kill_player()
            elif isinstance(action, SendChecks):
                self.sent_checks += action.locations
                if self.core.connected:
                    self.server_receives(action.locations)
            elif isinstance(action, SendGoal):
                self.goals += 1
            elif isinstance(action, SendDeath):
                self.deaths_sent.append(action.cause)
            elif isinstance(action, SaveState):
                self.storage = action.data

    def server_receives(self, locations: Sequence[int]) -> None:
        """The server confirms the checks it receives."""
        self.server_checked.update(locations)
        self.core.on_checked(locations)

    def warnings(self) -> list[str]:
        return [notice.text for notice in self.notices if notice.warning]

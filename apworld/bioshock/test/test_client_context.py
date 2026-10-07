"""
Tests for the Archipelago wrapper.

The wrapper is driven with the packets a real server sends, through CommonClient's own packet handler, and a
simulated game. Where it has to reach the "real" game, it starts real helper processes that import a stand-in for
Frida (see test_client_frida.py). None of it needs a network or the game.
"""
import asyncio
import sys
import time
import types
import unittest
from collections.abc import Callable
from typing import Any
from unittest import mock

from CommonClient import process_server_cmd
from NetUtils import ClientStatus, NetworkItem, NetworkPlayer, NetworkSlot, SlotType

from ..client import context as context_module
from ..client.context import BioShockContext
from ..client.core import SUPPORTED_SLOT_DATA_VERSION
from ..client.frida_agent import FridaAgent
from ..client.protocol import AgentError, CommandStarted, GameState
from ..client.simulated import SimulatedGame
from ..data import LEVELS, LOCATION_TABLE
from ..items import ITEM_NAME_TO_ID
from .frida_harness import PYTHON, FakeFrida

STORAGE_KEY = "bioshock_0_1"


def slot_data(access_items: bool = True, death_link: bool = False) -> dict[str, Any]:
    return {
        "slot_data_version": SUPPORTED_SLOT_DATA_VERSION,
        "death_link": int(death_link),
        "level_access_items": {level.name: level.access_item for level in LEVELS} if access_items else {},
    }


class ContextTestBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        # Archipelago's settings file on this machine is neither read nor written.
        self.stored = mock.patch("Utils.persistent_store").start()
        mock.patch("Utils.persistent_load", return_value={}).start()
        self.addCleanup(mock.patch.stopall)

        self.ctx = BioShockContext()
        self.ctx.auto_attach = False
        self.ctx.python_candidates = lambda: []  # type: ignore[method-assign]  # no test starts a real Python by accident
        self.sent: list[dict[str, Any]] = []

        async def record(msgs: list[dict[str, Any]]) -> None:
            self.sent += msgs

        self.ctx.send_msgs = record  # type: ignore[method-assign]
        self.now = 0.0
        self.items: list[int] = []

    async def asyncTearDown(self) -> None:
        self.ctx.keep_alive_task.cancel()

    async def connect(self, data: dict[str, Any] | None = None, checked: tuple[int, ...] = (),
                      saved: dict[str, Any] | None = None, seed: str = "seed") -> None:
        all_locations = [location.id for location in LOCATION_TABLE]
        # A real RoomInfo packet also triggers the login prompt, so hand the wrapper just the part it reads.
        self.ctx.on_package("RoomInfo", {"seed_name": seed})
        await process_server_cmd(self.ctx, {
            "cmd": "Connected", "team": 0, "slot": 1,
            "players": [NetworkPlayer(0, 1, "Jack", "Jack")],
            "missing_locations": [location for location in all_locations if location not in checked],
            "checked_locations": list(checked),
            "slot_data": data if data is not None else slot_data(),
            "slot_info": {"1": NetworkSlot("Jack", "BioShock", SlotType.player, [])},
            "hint_points": 0,
        })
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertIn({"cmd": "Get", "keys": [STORAGE_KEY]}, self.sent, "the saved state must be requested")
        await process_server_cmd(self.ctx, {"cmd": "Retrieved", "keys": {STORAGE_KEY: saved}})
        await asyncio.sleep(0)

    async def receive(self, *names: str) -> None:
        start = len(self.items)
        new = [ITEM_NAME_TO_ID[name] for name in names]
        self.items += new
        await process_server_cmd(self.ctx, {
            "cmd": "ReceivedItems", "index": start,
            "items": [NetworkItem(item, -1, 0, 0) for item in new],
        })

    async def run_loop(self, count: int = 4) -> None:
        for _ in range(count):
            self.now += 0.25
            self.ctx.step(self.now)
            await self.ctx.perform(self.ctx.core.drain())

    def use(self, fake: FakeFrida, *candidates: tuple[str, ...]) -> None:
        """Let the client find "Frida" in the Python the tests run in."""
        self.ctx.python_candidates = lambda: list(candidates or (PYTHON,))  # type: ignore[method-assign]
        self.ctx.helper_env = fake.env
        self.addCleanup(self.ctx.drop_agent)
        self.addCleanup(self.ctx.stop_looking)

    async def until(self, condition: Callable[[], Any], what: str, timeout: float = 20.0) -> None:
        """Keep the client's loop going, at the same moment of its own clock, until something has happened.

        What a helper process does takes real time, which the client's own clock in these tests does not have.
        """
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                self.fail(f"timed out waiting for {what}")
            self.ctx.step(self.now)
            await self.ctx.perform(self.ctx.core.drain())
            await asyncio.sleep(0.01)

    async def settled(self) -> None:
        """Wait for the attempt to reach the game that is under way to have its outcome."""
        await self.until(lambda: not self.ctx.looking, "attaching to have an outcome")

    def packets(self, cmd: str) -> list[dict[str, Any]]:
        return [message for message in self.sent if message.get("cmd") == cmd]

    def checks_sent(self) -> list[int]:
        return sorted(location for message in self.packets("LocationChecks") for location in message["locations"])


class TestContext(ContextTestBase):
    async def test_login_asks_for_this_game_with_all_items(self) -> None:
        self.ctx.auth = "Jack"
        with mock.patch("Utils.get_unique_identifier", return_value="test-uuid"), self.assertLogs("Client"):
            await process_server_cmd(self.ctx, {
                "cmd": "RoomInfo", "seed_name": "seed", "version": (0, 6, 7), "generator_version": (0, 6, 7),
                "tags": [], "password": False, "hint_cost": 10, "location_check_points": 1, "games": ["BioShock"],
                "datapackage_checksums": dict(self.ctx.checksums), "permissions": {}, "time": time.time(),
            })
        connects = self.packets("Connect")
        self.assertEqual(len(connects), 1)
        self.assertEqual(connects[0]["game"], "BioShock")
        self.assertEqual(connects[0]["name"], "Jack")
        self.assertEqual(connects[0]["items_handling"], 0b111)
        self.assertTrue(connects[0]["slot_data"])
        self.assertEqual(self.packets("GetDataPackage"), [], "the bundled data package matches the world")
        self.assertEqual(self.ctx._room_seed, "seed")

    async def test_items_reach_the_game_and_progress_is_saved(self) -> None:
        await self.connect()
        game = self.ctx.start_simulation()
        await self.receive("Health Upgrade", "10 ADAM")
        await self.run_loop(8)
        self.assertEqual(game.commands, ["GiveItem 1 ShockGame.HealthUpgrade", "GiveItem 10 ShockGame.ADAM"])
        saves = self.packets("Set")
        self.assertTrue(saves)
        self.assertEqual(saves[-1]["key"], STORAGE_KEY)
        self.assertEqual(saves[-1]["operations"], [{"operation": "replace", "value": self.ctx.core.snapshot()}])
        self.assertEqual(saves[-1]["operations"][0]["value"]["delivered"], 2)

    async def test_items_that_arrive_before_the_saved_state_are_not_lost(self) -> None:
        self.ctx.on_package("RoomInfo", {"seed_name": "seed"})
        await process_server_cmd(self.ctx, {
            "cmd": "Connected", "team": 0, "slot": 1, "players": [NetworkPlayer(0, 1, "Jack", "Jack")],
            "missing_locations": [], "checked_locations": [], "slot_data": slot_data(),
            "slot_info": {"1": NetworkSlot("Jack", "BioShock", SlotType.player, [])}, "hint_points": 0,
        })
        await self.receive("Health Upgrade")  # the server sends items right after Connected
        game = self.ctx.start_simulation()
        await self.run_loop(4)
        self.assertEqual(game.commands, [], "nothing may be delivered before the saved count is known")
        await process_server_cmd(self.ctx, {"cmd": "Retrieved", "keys": {STORAGE_KEY: None}})
        await self.run_loop(4)
        self.assertEqual(game.commands, ["GiveItem 1 ShockGame.HealthUpgrade"])

    async def test_saved_delivery_count_is_respected(self) -> None:
        await self.receive("Health Upgrade", "EVE Upgrade")
        await self.connect(saved={"version": 1, "delivered": 1, "furthest": 0, "goal": False, "collected": []})
        game = self.ctx.start_simulation()
        await self.run_loop(6)
        self.assertEqual(game.commands, ["GiveItem 1 ShockGame.BioAmmoUpgrade"])

    async def test_checks_follow_level_access(self) -> None:
        await self.connect()
        game = self.ctx.start_simulation()
        game.collect_diary(3)   # Medical Pavilion: open
        game.collect_diary(42)  # Arcadia: locked
        await self.run_loop(3)
        self.assertEqual(self.checks_sent(), [1003])
        self.assertEqual(self.ctx.locations_checked, set(), "nothing for CommonClient to replay at the next login")

        await process_server_cmd(self.ctx, {"cmd": "RoomUpdate", "checked_locations": [1003]})
        await self.receive("Arcadia Access")
        await self.run_loop(3)
        self.assertEqual(self.checks_sent(), [1003, 1042])

    async def test_locations_already_checked_on_the_server_are_not_resent(self) -> None:
        await self.connect(checked=(1003,))
        game = self.ctx.start_simulation()
        game.collect_diary(3)
        await self.run_loop(3)
        self.assertEqual(self.checks_sent(), [])

    async def test_goal(self) -> None:
        await self.connect(slot_data(access_items=False))
        game = self.ctx.start_simulation()
        game.defeat_fontaine()
        await self.run_loop(3)
        self.assertEqual(self.packets("StatusUpdate"), [{"cmd": "StatusUpdate", "status": ClientStatus.CLIENT_GOAL}])
        self.assertFalse(self.ctx.finished_game, "nothing for CommonClient to replay at the next login")

    async def test_another_seed_gets_nothing_from_the_last_one(self) -> None:
        await self.connect(slot_data(access_items=False))
        game = self.ctx.start_simulation()
        game.collect_diary(3)
        game.defeat_fontaine()
        await self.run_loop(3)
        self.assertEqual(self.checks_sent(), [1003])
        self.assertEqual(len(self.packets("StatusUpdate")), 1)

        self.ctx.drop_agent()  # the player quits to the menu to start a new game ...
        await self.ctx.connection_closed()
        self.sent.clear()
        self.items = []
        await self.connect(slot_data(access_items=False), seed="another seed")  # ... and joins the next room
        await self.run_loop(3)
        self.assertEqual(self.checks_sent(), [], "the last seed's checks must not be sent to this one")
        self.assertEqual(self.packets("StatusUpdate"), [], "nor its goal")
        self.assertEqual(self.ctx.core.collected, set())

    async def test_lost_check_and_goal_are_sent_again_after_a_reconnect(self) -> None:
        await self.connect(slot_data(access_items=False))
        game = self.ctx.start_simulation()
        game.collect_diary(3)
        game.defeat_fontaine()
        await self.run_loop(3)
        await self.ctx.connection_closed()  # the connection drops before the server has confirmed anything
        self.sent.clear()
        self.items = []
        await self.connect(slot_data(access_items=False))
        await self.run_loop(3)
        self.assertEqual(self.checks_sent(), [1003])
        self.assertEqual(len(self.packets("StatusUpdate")), 1)

    async def test_deathlink_both_ways(self) -> None:
        self.ctx.server = types.SimpleNamespace(socket=types.SimpleNamespace(closed=False, open=True))
        await self.connect(slot_data(death_link=True))
        self.assertIn("DeathLink", self.ctx.tags)
        game = self.ctx.start_simulation()
        await self.run_loop(2)

        await process_server_cmd(self.ctx, {"cmd": "Bounced", "tags": ["DeathLink"],
                                            "data": {"time": time.time() + 1, "source": "Atlas", "cause": ""}})
        await self.run_loop(3)
        self.assertEqual(game.deaths, 1)
        self.assertEqual(self.packets("Bounce"), [], "a received death must not be sent back")

        game.die()
        await self.run_loop(2)
        bounces = self.packets("Bounce")
        self.assertEqual(len(bounces), 1)
        self.assertEqual(bounces[0]["tags"], ["DeathLink"])
        self.assertIn("Jack", bounces[0]["data"]["cause"])

    async def test_deathlink_off_does_not_tag_or_kill(self) -> None:
        await self.connect(slot_data(death_link=False))
        self.assertNotIn("DeathLink", self.ctx.tags)
        self.assertNotIn("DeathLink", BioShockContext.tags, "the shared class-level tag set must stay clean")

    async def test_reconnect_keeps_progress(self) -> None:
        await self.connect()
        game = self.ctx.start_simulation()
        await self.receive("Health Upgrade")
        await self.run_loop(6)
        await self.ctx.connection_closed()
        self.assertFalse(self.ctx.core.connected)

        self.items = []
        await self.connect(saved=None)  # even if the server lost the saved state
        await self.receive("Health Upgrade")
        await self.run_loop(6)
        self.assertEqual(game.commands, ["GiveItem 1 ShockGame.HealthUpgrade"], "not delivered twice")

    async def test_closing_records_the_item_that_was_on_its_way(self) -> None:
        await self.connect()
        game = self.ctx.start_simulation()
        await self.receive("Health Upgrade", "EVE Upgrade")
        await self.run_loop(1)
        self.assertEqual(game.commands, ["GiveItem 1 ShockGame.HealthUpgrade"])
        self.assertTrue(self.ctx.core.awaiting_answer)

        await self.ctx.shutdown()
        self.assertEqual(game.commands, ["GiveItem 1 ShockGame.HealthUpgrade"], "nothing new is started")
        self.assertEqual(self.packets("Set")[-1]["operations"][0]["value"]["delivered"], 1)
        self.assertTrue(game.closed)

    async def test_detaching_takes_the_answer_that_is_already_waiting(self) -> None:
        await self.connect()
        game = self.ctx.start_simulation()
        await self.receive("Health Upgrade")
        await self.run_loop(1)  # the command is with the game, which has answered; the client has not looked yet
        self.assertTrue(self.ctx.core.awaiting_answer)
        self.assertTrue(self.ctx.command_processor(self.ctx)("/detach"))
        self.assertEqual(self.ctx.core.delivered, 1)

        self.ctx.set_agent(game)  # /attach later, same running game
        await self.run_loop(4)
        self.assertEqual(game.commands, ["GiveItem 1 ShockGame.HealthUpgrade"], "not given a second time")
        self.assertEqual(self.packets("Set")[-1]["operations"][0]["value"]["delivered"], 1)

    async def test_closing_saves_even_if_the_loop_was_stopped_halfway(self) -> None:
        await self.connect()
        self.ctx.start_simulation()
        await self.receive("Health Upgrade")
        await self.run_loop(1)
        self.now += 0.25
        self.ctx.step(self.now)  # the game's answer is taken and a save is due ...
        self.ctx.core.drain()  # ... but the loop is cancelled before the save is sent
        self.assertEqual(self.ctx.core.delivered, 1)
        self.assertEqual(self.packets("Set")[-1]["operations"][0]["value"]["delivered"], 0)

        await self.ctx.shutdown()
        self.assertEqual(self.packets("Set")[-1]["operations"][0]["value"]["delivered"], 1)

    async def test_closing_while_the_game_is_stuck_in_a_command_sets_the_item_aside_for_good(self) -> None:
        await self.connect()
        game = self.ctx.start_simulation()
        game.paused = True  # stands for a game that has begun on the command and now shows its own error window
        await self.receive("Health Upgrade")
        await self.run_loop(1)
        flight = self.ctx.core._in_flight
        assert flight is not None
        self.ctx.core.on_event(CommandStarted(flight.command_id), self.now)
        with mock.patch.object(context_module, "CLOSING_WAIT_SECONDS", 0.05), self.assertLogs("Client") as logs:
            await self.ctx.shutdown()
        saved = self.packets("Set")[-1]["operations"][0]["value"]
        self.assertEqual((saved["delivered"], saved["skipped"]), (1, [[0, "refused"]]),
                         "or the next start of the client would send the game straight back into that command")
        self.assertTrue(any("went away while it was being given Health Upgrade" in line for line in logs.output))

    async def test_closing_with_a_command_only_waiting_in_the_game_sets_nothing_aside(self) -> None:
        await self.connect()
        game = self.ctx.start_simulation()
        game.paused = True
        await self.receive("Health Upgrade")
        await self.run_loop(1)
        with mock.patch.object(context_module, "CLOSING_WAIT_SECONDS", 0.05):
            await self.ctx.shutdown()
        saved = self.packets("Set")[-1]["operations"][0]["value"]
        self.assertEqual((saved["delivered"], saved["skipped"]), (0, []), "it is simply given next time")

    async def test_closing_does_not_wait_long_for_a_paused_game(self) -> None:
        await self.connect()
        game = self.ctx.start_simulation()
        game.paused = True
        await self.receive("Health Upgrade")
        await self.run_loop(1)
        with mock.patch.object(context_module, "CLOSING_WAIT_SECONDS", 0.05):
            started = time.monotonic()
            await self.ctx.shutdown()
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(game.commands, ["GiveItem 1 ShockGame.HealthUpgrade"], "the command is not sent again")
        self.assertEqual(self.packets("Set")[-1]["operations"][0]["value"]["delivered"], 0,
                         "an item nobody confirmed is not counted")

    async def test_another_slot_starts_clean(self) -> None:
        await self.connect()
        self.ctx.start_simulation()
        await self.receive("Health Upgrade")
        await self.run_loop(6)
        await self.ctx.connection_closed()

        self.items = []
        await self.connect(seed="another seed")
        self.assertEqual(self.ctx.core.delivered, 0)

    async def test_game_going_away_is_handled(self) -> None:
        class Broken(SimulatedGame):
            def read_state(self) -> GameState:
                raise AgentError("the game closed")

        await self.connect()
        self.ctx.set_agent(Broken())
        with self.assertLogs("Client", level="WARNING") as logs:
            await self.run_loop(1)
        self.assertIsNone(self.ctx.agent)
        self.assertFalse(self.ctx.core.agent_attached)
        self.assertTrue(any("the game closed" in line for line in logs.output))

    async def test_commands(self) -> None:
        await self.connect()
        commands = self.ctx.command_processor(self.ctx)
        with self.assertLogs("Client", level="INFO") as logs:
            self.assertFalse(commands("/sim diary 3"), "needs /simulate first")
            self.assertTrue(commands("/simulate"))
            self.assertIsInstance(self.ctx.agent, SimulatedGame)
            self.assertTrue(commands("/sim diary 3"))
            self.assertTrue(commands("/sim travel fort"))
            self.assertTrue(commands("/sim sister"))
            self.assertTrue(commands("/sim story"))
            self.assertFalse(commands("/sim travel nowhere"))
            self.assertFalse(commands("/sim diary"))
            self.assertFalse(commands("/sim dance"))
            self.assertTrue(commands("/sim bag"))
            self.assertTrue(commands("/sim pause"))
            self.assertTrue(self.ctx.agent.paused)
            self.assertTrue(commands("/sim pause"))
            await self.run_loop(3)
            self.assertTrue(commands("/bioshock"))
            self.assertFalse(commands("/resync soon"))
            self.assertTrue(commands("/resync 1"))
            self.assertTrue(commands("/retry"))
            self.assertTrue(commands("/deathlink"))
            self.assertTrue(commands("/simulate"))
        self.assertIsNone(self.ctx.agent)
        self.assertTrue(self.ctx.auto_attach)
        self.assertTrue(self.ctx.core.death_link)
        text = "\n".join(logs.output)
        self.assertIn("Released Today", text)
        self.assertIn("Now in Fort Frolic", text)
        self.assertIn("Fort Frolic: Photograph Kyle Fitzpatrick.", text)
        self.assertIn("is not one level", text)
        self.assertIn("Items: 0 of 0 given", text)
        self.assertIn("use it on a test seed", text)
        self.assertIn("No set-aside items can be tried", text)
        # The diary, plus finishing the two open levels on the way. What was done in Fort Frolic is held.
        self.assertEqual(self.checks_sent(), [1003, 5001, 5002])
        self.assertEqual(len(self.ctx.core.held_locations()), 6,
                         "the sister and the photograph, plus finishing Neptune's Bounty, Smuggler's Hideout, "
                         "Arcadia and the Market")

    async def test_what_the_game_confirmed_is_kept_when_the_game_goes_away(self) -> None:
        await self.connect()
        game = self.ctx.start_simulation()
        await self.receive("Health Upgrade")
        await self.run_loop(1)  # the command is with the game, which has answered; the client has not looked yet
        self.assertTrue(self.ctx.core.awaiting_answer)

        def gone() -> GameState:
            raise AgentError("the game closed")

        game.read_state = gone  # type: ignore[method-assign]
        with self.assertLogs("Client", level="WARNING"):
            await self.run_loop(1)
        self.assertIsNone(self.ctx.agent)
        self.assertEqual(self.ctx.core.delivered, 1, "the item arrived; giving it again next time would be wrong")

    # -- reaching the real game ---------------------------------------------------------------------------------------

    async def test_attach_without_frida_stops_trying(self) -> None:
        fake = FakeFrida(self, missing=True)
        self.use(fake)
        with mock.patch.object(FridaAgent, "start", wraps=FridaAgent.start) as start, \
                self.assertLogs("Client", level="WARNING") as logs:
            self.ctx.auto_attach = True
            await self.until(lambda: not self.ctx.auto_attach, "the client to stop trying")
            self.now = 100.0
            await self.run_loop(3)
            self.assertEqual(start.call_count, 1, "asking again would only fail the same way")
            self.assertFalse(self.ctx.looking)
            # The player names a Python: that is worth another try without having to type /attach as well.
            fake.order(missing=False)
            with self.assertLogs("Client", level="INFO"):
                self.assertTrue(self.ctx.command_processor(self.ctx)(f"/python {sys.executable}"))
                self.assertTrue(self.ctx.auto_attach)
                await self.until(lambda: self.ctx.agent is not None, "the client to find the game after all")
        self.assertEqual(start.call_count, 2)
        self.assertEqual(sum("pip install frida" in line for line in logs.output), 1)
        self.assertTrue(any("has no Frida" in line for line in logs.output), "what was tried is part of the answer")

    async def test_attach_retries_quietly_while_the_game_is_closed(self) -> None:
        fake = FakeFrida(self, running=False)
        self.use(fake)
        with self.assertLogs("Client", level="INFO") as logs:
            self.ctx.auto_attach = True
            self.now = 1.0
            await self.until(lambda: self.ctx._last_attach_problem, "the first try to fail")
            self.assertEqual(len(fake.calls("attach")), 1)
            self.now = 2.0  # too soon to try again
            self.ctx.step(self.now)
            await asyncio.sleep(0.3)
            self.ctx.step(self.now)
            self.assertEqual(len(fake.calls("attach")), 1)
            self.now = 10.0
            await self.until(lambda: len(fake.calls("attach")) == 2, "the second try")
            await self.settled()
            self.now = 20.0
            await self.until(lambda: len(fake.calls("attach")) == 3, "the third try")
            await self.settled()
            self.assertTrue(self.ctx.auto_attach)
            self.assertEqual(len(fake.calls("attach")), 3)
            self.assertEqual(sum("not running" in line for line in logs.output), 1, "say it once, not every 5 seconds")
            self.assertEqual(len(fake.calls("import")), 1, "and wait with one helper, not with a new one each time")

            fake.order(running=True)  # the player starts the game
            self.now = 30.0
            await self.until(lambda: self.ctx.agent is not None, "the client to find the game")
        self.assertIsInstance(self.ctx.agent, FridaAgent)
        self.assertEqual(len(fake.calls("import")), 1)
        self.assertTrue(any("Game connected: BioShock Remastered (Steam 1.0.127355)." in line for line in logs.output))

    async def test_broken_agent_script_is_explained_and_not_injected_again(self) -> None:
        fake = FakeFrida(self, script_error="ReferenceError: 'Proces' is not defined")
        self.use(fake)
        with self.assertLogs("Client", level="WARNING") as logs:
            self.ctx.auto_attach = True
            self.now = 1.0
            await self.until(lambda: not self.ctx.auto_attach, "the client to stop trying")
            self.now = 10.0
            await self.run_loop(2)
            self.now = 20.0
            await self.run_loop(2)
        self.assertEqual(len(fake.calls("attach")), 1, "injecting it again would fail the same way")
        self.assertEqual(fake.names()[-2:], ["detach", "shutdown"],
                         "the broken script is taken out of the game again, and the helper is gone")
        self.assertTrue(any("ReferenceError: 'Proces' is not defined (line 12)" in line for line in logs.output),
                        "the script's own error is what the player needs to see")

    async def test_attach_and_deliver_through_frida(self) -> None:
        fake = FakeFrida(self)
        self.use(fake)
        await self.connect()
        await self.receive("Health Upgrade")
        with self.assertLogs("Client", level="INFO") as logs:
            self.ctx.auto_attach = True
            await self.until(lambda: self.ctx.core.delivered == 1, "the item to be given")
        self.assertIsInstance(self.ctx.agent, FridaAgent)
        self.assertFalse(self.ctx.looking)
        self.assertTrue(any("[bioshock-ap] build: Steam" in line for line in logs.output),
                        "the agent's own messages appear in the client")
        self.assertTrue(any("Using Frida 17.0.0-fake from" in line for line in logs.output))
        self.assertEqual([(call["id"], call["command"]) for call in fake.calls("exec")],
                         [(1, "GiveItem 1 ShockGame.HealthUpgrade")])
        self.assertEqual(self.ctx.core.level, "Medical Pavilion")
        self.assertEqual(self.ctx._python, PYTHON, "next time, the Python that had Frida is asked first")
        self.assertEqual(self.ctx.python_candidates(), [PYTHON])
        await self.ctx.shutdown()
        await self.until(lambda: fake.names()[-2:] == ["detach", "shutdown"],
                         "the agent to be taken out of the game when the client closes")

    async def test_the_python_that_worked_is_asked_first_next_time(self) -> None:
        self.ctx._python = ("/last/python",)
        self.ctx.chosen_python = "/chosen/python"
        del self.ctx.python_candidates  # the real one
        with mock.patch.object(context_module.frida_agent, "python_candidates", side_effect=list) as find:
            self.assertEqual(self.ctx.python_candidates(), [("/last/python",), "/chosen/python"])
        find.assert_called_once()

    async def test_the_game_closing_and_coming_back(self) -> None:
        fake = FakeFrida(self)
        self.use(fake)
        await self.connect()
        with self.assertLogs("Client", level="INFO") as logs:
            self.ctx.auto_attach = True
            self.now = 1.0
            await self.until(lambda: self.ctx.agent is not None, "the client to find the game")
            fake.order(detach="process-terminated")
            await self.until(lambda: self.ctx.agent is None, "the client to notice that the game closed")
            self.assertTrue(any("Game disconnected: the game closed" in line for line in logs.output))
            fake.order(detach="", running=False)
            self.now = 10.0
            await self.until(lambda: len(fake.calls("import")) == 2, "a new helper to look for the game")
            await self.settled()
            fake.order(running=True)
            self.now = 20.0
            await self.until(lambda: self.ctx.agent is not None, "the client to find the game again")
        self.assertEqual(sum("Game connected" in line for line in logs.output), 2)

    async def test_a_helper_that_dies_while_waiting_for_the_game_is_replaced(self) -> None:
        fake = FakeFrida(self, running=False)
        self.use(fake)
        with self.assertLogs("Client", level="INFO") as logs:
            self.ctx.auto_attach = True
            self.now = 1.0
            await self.until(lambda: self.ctx._last_attach_problem, "the first try to fail")
            self.ctx._link._helper._process.kill()  # type: ignore[union-attr]
            await self.until(lambda: self.ctx._link is None, "the client to notice that its helper is gone")
            self.assertTrue(self.ctx.auto_attach, "that is no reason to stop looking for the game")
            self.assertEqual(len(fake.calls("import")), 1, "not before the next regular try")
            self.now = 10.0
            await self.until(lambda: len(fake.calls("import")) == 2, "a new helper to be started")
            await self.settled()
            fake.order(running=True)
            self.now = 20.0
            await self.until(lambda: self.ctx.agent is not None, "the client to find the game")
        self.assertEqual(sum("The helper that reaches the game stopped" in line for line in logs.output), 1)

    async def test_attach_command(self) -> None:
        fake = FakeFrida(self, running=False)
        self.use(fake)
        commands = self.ctx.command_processor(self.ctx)
        with self.assertLogs("Client", level="INFO") as logs:
            self.assertTrue(commands("/attach"))
            self.assertTrue(self.ctx.auto_attach)
            self.assertTrue(self.ctx.looking)
            await self.settled()
            self.assertTrue(commands("/attach"))  # asked again: say again what the trouble is
            await self.settled()
            self.assertEqual(sum("BioShock Remastered is not running. Waiting for the game." in line
                                 for line in logs.output), 2)
            fake.order(running=True)
            self.assertTrue(commands("/attach"))
            await self.until(lambda: self.ctx.agent is not None, "the client to find the game")
            self.assertTrue(commands("/attach"))
            self.assertTrue(commands("/python"))
            self.ctx.attach_game(announce=True)  # whoever asks: one game, one helper
            self.assertFalse(self.ctx.looking)
            self.assertIsNone(self.ctx._link)
        text = "\n".join(logs.output)
        self.assertEqual(text.count("Looking for the game."), 3)
        self.assertEqual(text.count("Already connected to the game."), 1)
        self.assertIn("The game is reached with Frida 17.0.0-fake from", text)
        self.assertEqual(len(fake.calls("import")), 1)

    async def test_detach_command_stops_looking_too(self) -> None:
        fake = FakeFrida(self, running=False)
        self.use(fake)
        commands = self.ctx.command_processor(self.ctx)
        with self.assertLogs("Client", level="INFO"):
            self.assertTrue(commands("/attach"))
            await self.settled()
            self.assertTrue(commands("/detach"))
        self.assertFalse(self.ctx.auto_attach)
        self.assertIsNone(self.ctx._link)
        await self.until(lambda: fake.names()[-1:] == ["shutdown"], "the helper to leave")
        self.now = 100.0
        await self.run_loop(3)
        self.assertEqual(len(fake.calls("import")), 1, "nothing looks for the game until /attach")

    async def test_the_simulated_game_and_the_real_one_do_not_mix(self) -> None:
        fake = FakeFrida(self, running=False)
        self.use(fake)
        commands = self.ctx.command_processor(self.ctx)
        with self.assertLogs("Client", level="INFO") as logs:
            self.ctx.auto_attach = True
            self.now = 1.0
            await self.until(lambda: self.ctx._last_attach_problem, "the first try to fail")
            self.assertIsNotNone(self.ctx._link)
            self.assertTrue(commands("/simulate"))
            self.assertIsNone(self.ctx._link, "the simulated game is on; nothing waits for the real one")

            self.assertTrue(commands("/attach"))  # the real game is wanted after all, but is not running
            await self.settled()
            self.ctx.step(self.now)
            self.assertTrue(any("BioShock Remastered is not running. The simulated game stays" in line
                                for line in logs.output))
            self.assertIsInstance(self.ctx.agent, SimulatedGame, "the simulated game stays until the real one is there")
            self.assertIsNone(self.ctx._link, "and no helper is left waiting behind it")

            fake.order(running=True)
            self.assertTrue(commands("/attach"))
            await self.until(lambda: isinstance(self.ctx.agent, FridaAgent), "the real game to take over")
        self.assertIn("Game connected: BioShock Remastered", "\n".join(logs.output))

    async def test_the_real_game_taking_over_from_the_simulated_one_gets_every_item_once(self) -> None:
        fake = FakeFrida(self)
        self.use(fake)
        await self.connect()
        game = self.ctx.start_simulation()
        names = ("Health Upgrade", "EVE Upgrade", "10 ADAM", "25 ADAM", "$25", "$50", "Auto-Hack Tool", "First Aid Kit")
        await self.receive(*names)
        await self.run_loop(1)  # the first item is with the simulated game
        commands = self.ctx.command_processor(self.ctx)
        with self.assertLogs("Client", level="INFO"):
            self.assertTrue(commands("/attach"))
            await self.until(lambda: self.ctx.core.delivered == len(names) and not self.ctx.core.busy,
                             "every item to be given")
        self.assertIsInstance(self.ctx.agent, FridaAgent)
        given = game.commands + [call["command"] for call in fake.calls("exec")]
        self.assertEqual(sorted(given), sorted(set(given)), "nothing twice")
        self.assertEqual(len(given), len(names))
        self.assertTrue(fake.calls("exec"), "the real game took over while items were still coming")

    async def test_a_game_run_as_administrator_is_not_asked_again_and_again(self) -> None:
        fake = FakeFrida(self, denied=True)
        self.use(fake)
        with self.assertLogs("Client", level="WARNING") as logs:
            self.ctx.auto_attach = True
            self.now = 1.0
            await self.until(lambda: not self.ctx.auto_attach, "the client to stop trying")
            self.now = 50.0
            await self.run_loop(3)
        self.assertEqual(len(fake.calls("attach")), 1, "every try may bring up a Windows prompt")
        self.assertTrue(any("administrator" in line for line in logs.output))

    async def test_python_command(self) -> None:
        commands = self.ctx.command_processor(self.ctx)
        with self.assertLogs("Client", level="INFO") as logs:
            self.assertTrue(commands("/python"))
            self.assertFalse(commands("/python C:/no/such/python.exe"))
            self.assertIsNone(self.ctx.chosen_python)
            self.stored.assert_not_called()

            self.ctx._python = ("/last/python",)
            self.ctx.auto_attach = False
            self.ctx._gave_up = True  # as after "no Python with Frida was found"
            self.assertTrue(commands(f'/python "{sys.executable}"'))
            self.assertEqual(self.ctx.chosen_python, sys.executable)
            self.assertIsNone(self.ctx._python, "what worked before the choice no longer comes first")
            self.assertTrue(self.ctx.auto_attach, "the client had given up by itself; a new choice is worth a new try")
            self.stored.assert_called_once_with("bioshock", "python", sys.executable)
            self.assertTrue(commands("/python"))

            self.assertTrue(commands("/detach"))
            self.assertTrue(commands("/python auto"))
            self.assertIsNone(self.ctx.chosen_python)
            self.stored.assert_called_with("bioshock", "python", "")
            self.assertFalse(self.ctx.auto_attach, "/detach holds until /attach, whatever else is typed")
        text = "\n".join(logs.output)
        self.assertIn("The client looks for a Python with Frida by itself.", text)
        self.assertIn("There is no file C:/no/such/python.exe", text)
        self.assertIn(f"Looking for Frida in {sys.executable} first from now on.", text)
        self.assertIn(f"You named this Python: {sys.executable}", text)
        self.assertIn("by itself again", text)

    async def test_python_command_restarts_the_search(self) -> None:
        fake = FakeFrida(self, running=False)
        self.use(fake)
        commands = self.ctx.command_processor(self.ctx)
        with self.assertLogs("Client", level="INFO"):
            self.ctx.auto_attach = True
            self.now = 1.0
            await self.until(lambda: self.ctx._last_attach_problem, "the first try to fail")
            self.assertTrue(commands(f"/python {sys.executable}"))
            self.assertIsNone(self.ctx._link, "the helper in the old Python is let go")
            self.now = 1.5  # no need to wait for the next regular try
            await self.until(lambda: len(fake.calls("import")) == 2, "a helper in the newly named Python")
            await self.settled()

            game = self.ctx.start_simulation()
            self.assertTrue(commands("/python auto"))
            self.assertIs(self.ctx.agent, game)
            self.assertFalse(self.ctx.auto_attach, "the simulated game is not pushed aside by a change of Python")

    async def test_the_python_named_earlier_is_remembered(self) -> None:
        for stored, expected in (({"bioshock": {"python": "C:/Python312/python.exe"}}, "C:/Python312/python.exe"),
                                 ({"bioshock": {"python": ""}}, None), ({"bioshock": {"python": 5}}, None),
                                 ({"client": {}}, None)):
            with self.subTest(stored=stored), mock.patch("Utils.persistent_load", return_value=stored):
                ctx = BioShockContext()
                self.addCleanup(ctx.keep_alive_task.cancel)
                self.assertEqual(ctx.chosen_python, expected)
                if expected:
                    with mock.patch.object(context_module.frida_agent.sys, "frozen", True, create=True), \
                            mock.patch.dict(context_module.frida_agent.os.environ, {}, clear=True):
                        self.assertEqual(ctx.python_candidates(), [(expected,)])

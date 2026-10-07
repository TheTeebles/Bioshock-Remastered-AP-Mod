"""
The client against Archipelago's own server code.

A seed is generated the usual way, MultiServer loads it, and the real client logs in and plays it with the simulated
game. The one thing replaced is the network: instead of a websocket, the two programs exchange the same text through
an in-memory pipe. The client's connection loop, the server's packet handling, item sending, data storage, DeathLink
bounces and the goal are all the real code.

The last class swaps the simulated game for the real way to the game: a helper process that holds a stand-in for
Frida (see test_client_frida.py).

What this still does not cover: a real socket (addresses, TLS, compression) and the real game.
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from collections.abc import Mapping
from typing import Any, ClassVar
from unittest import mock

import test  # noqa: F401  Archipelago's test package turns off the GUI and the saving of settings

import CommonClient
import MultiServer
import Utils
import worlds
from NetUtils import ClientStatus, NetworkItem

from ..client import context as context_module
from ..client.context import BioShockContext
from ..client.core import REFUSED, UNKNOWN
from ..client.frida_agent import FridaAgent
from ..client.protocol import Notice
from ..client.simulated import SimulatedGame
from ..data import LEVEL_BY_NAME, LEVEL_NAMES, LOCATION_TABLE, LocationCategory
from ..items import ITEM_NAME_TO_ID
from ..locations import LOCATION_NAME_TO_ID
from .bases import generate_seed
from .client_harness import expected_deliveries, play_level
from .frida_harness import PYTHON, FakeFrida

LOCATION_LEVEL = {location.id: location.level for location in LOCATION_TABLE}
COLLECTIBLES = {location.id for location in LOCATION_TABLE
                if location.category in (LocationCategory.AUDIO_DIARY, LocationCategory.DIRECTORS_COMMENTARY)}


def new_server(archive: str, password: str = "") -> MultiServer.Context:
    """Start a server for a generated seed, without a socket and without a save file.

    On start-up the server strips the name groups out of Archipelago's global data package. That only works once per
    process, so each server gets its own copy to strip.
    """
    games = {name: dict(package) for name, package in worlds.network_data_package["games"].items()}
    for package in games.values():
        package.setdefault("item_name_groups", {})
        package.setdefault("location_name_groups", {})
    with mock.patch.dict(worlds.network_data_package, {"games": games}):
        server = MultiServer.Context("", 0, "", password, 1, 10, False)
    server.load(archive)
    return server


class MemorySocket:
    """One end of an in-memory connection, with the parts of a websocket that the client and the server use."""

    def __init__(self, network: Network) -> None:
        self.network = network
        self.peer: MemorySocket = self
        self.inbox: asyncio.Queue[str | None] = asyncio.Queue()
        self.open = True
        self.closed = False
        # The server tells clients off for connecting without compression; say we have it.
        self.extensions = [mock.Mock(spec=MultiServer.PerMessageDeflate)]

    def push(self, data: str) -> None:
        if self.open and self.peer.open:
            self.network.traffic += 1
            self.peer.inbox.put_nowait(data)

    async def send(self, data: str) -> None:
        self.push(data)

    async def close(self, *_: Any) -> None:
        for end in (self, self.peer):
            if end.open:
                end.open = False
                end.closed = True
                end.inbox.put_nowait(None)

    def __aiter__(self) -> MemorySocket:
        return self

    async def __anext__(self) -> str:
        data = await self.inbox.get()
        if data is None:
            raise StopAsyncIteration
        return data


class Network:
    """Stands in for the internet between the clients and one server."""

    def __init__(self, server: MultiServer.Context) -> None:
        self.server = server
        self.rooms: dict[str, MultiServer.Context] = {}  # other servers, by a word in their address
        self.traffic = 0
        self.sockets: list[MemorySocket] = []
        self.handlers: list[asyncio.Task[None]] = []

    async def connect(self, address: str, **_: Any) -> MemorySocket:
        """Replaces websockets.connect: the server starts handling the new connection exactly as it would a real one."""
        server = next((room for name, room in self.rooms.items() if name in address), self.server)
        client_end, server_end = MemorySocket(self), MemorySocket(self)
        client_end.peer, server_end.peer = server_end, client_end
        self.sockets += [client_end, server_end]
        self.handlers.append(asyncio.create_task(MultiServer.server(server_end, "/", server),
                                                 name="server connection handler"))
        return client_end

    @staticmethod
    def broadcast(sockets: list[MemorySocket], message: str) -> None:
        """Replaces websockets.broadcast."""
        for socket in sockets:
            socket.push(message)

    async def quiet(self) -> None:
        """Wait until everything that was sent has been delivered and answered."""
        calm = 0
        for _ in range(100_000):
            before = self.traffic
            await asyncio.sleep(0)
            if self.traffic == before and all(socket.inbox.empty() for socket in self.sockets):
                calm += 1
                if calm >= 20:  # long enough for any follow-up that was scheduled as a task
                    return
            else:
                calm = 0
        raise AssertionError("the connection never went quiet")

    async def shutdown(self) -> None:
        for socket in self.sockets:
            await socket.close()
        await asyncio.gather(*self.handlers, return_exceptions=True)


class Player:
    def __init__(self, name: str, ctx: BioShockContext, game: SimulatedGame) -> None:
        self.name = name
        self.ctx = ctx
        self.game = game


class ServerTestBase(unittest.IsolatedAsyncioTestCase):
    players: ClassVar[Mapping[str, Mapping[str, Any]]] = {"Jack": {}}
    seed: ClassVar[int] = 1
    password: ClassVar[str] = ""  # what the room asks for; empty for an open room
    archive: ClassVar[str]
    _directory: ClassVar[tempfile.TemporaryDirectory[str]]

    @classmethod
    def setUpClass(cls) -> None:
        cls._directory = tempfile.TemporaryDirectory(prefix="bioshock_ap_test_")
        cls.addClassCleanup(cls._directory.cleanup)
        cls.archive = generate_seed(cls._directory.name, cls.players, cls.seed)

    async def asyncSetUp(self) -> None:
        self.server = new_server(self.archive, self.password)
        self.network = Network(self.server)
        for patcher in (
            mock.patch.object(CommonClient.websockets, "connect", self.network.connect),
            mock.patch.object(MultiServer.websockets, "broadcast", self.network.broadcast, create=True),
            # Keep the tests out of the player's Archipelago folder.
            mock.patch("Utils.persistent_store"),
            mock.patch("Utils.persistent_load", return_value={}),
            mock.patch("Utils.store_data_package_for_checksum"),
            mock.patch("Utils.get_unique_identifier", return_value="bioshock-test-client"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.joined: list[Player] = []
        self.now = 0.0

    async def asyncTearDown(self) -> None:
        for player in self.joined:
            await player.ctx.shutdown()
        await self.network.shutdown()

    # -- helpers ------------------------------------------------------------------------------------------------------

    async def join(self, name: str, game: SimulatedGame | None = None, reconnect_delay: int | None = None) -> Player:
        """Start a client for `name`, log it in and give it a (simulated) game."""
        ctx = BioShockContext("archipelago.test", self.password or None)
        ctx.auto_attach = False
        if reconnect_delay is not None:
            ctx.starting_reconnect_delay = reconnect_delay
        ctx.auth = name
        ctx.server_task = asyncio.create_task(CommonClient.server_loop(ctx), name=f"{name}: server loop")
        player = Player(name, ctx, game or SimulatedGame())
        self.joined.append(player)
        await self.network.quiet()
        self.assertTrue(ctx.core.connected, f"{name} could not log in")
        ctx.set_agent(player.game)
        return player

    async def leave(self, player: Player) -> None:
        """Close a player's client. The (simulated) game keeps running."""
        self.joined.remove(player)
        await player.ctx.shutdown()
        await self.network.quiet()

    async def settle(self) -> None:
        """Run every client's loop, and the network, until nothing more happens."""
        calm = 0
        for _ in range(20_000):
            self.now += 0.25
            traffic = self.network.traffic
            acted = False
            for player in self.joined:
                player.ctx.step(self.now)
                actions = player.ctx.core.drain()
                acted = acted or any(not isinstance(action, Notice) for action in actions)
                await player.ctx.perform(actions)
            await self.network.quiet()
            if acted or traffic != self.network.traffic or any(player.ctx.core.busy for player in self.joined):
                calm = 0
            else:
                calm += 1
                if calm >= 3:
                    return
        self.fail("the clients never came to rest")

    def slot(self, name: str) -> tuple[int, int]:
        return self.server.player_name_lookup[name]

    def server_client(self, name: str) -> MultiServer.Client:
        team, slot = self.slot(name)
        clients = self.server.clients[team][slot]
        self.assertEqual(len(clients), 1, f"{name} should be connected exactly once")
        return clients[0]

    def locations(self, name: str) -> set[int]:
        return set(self.server.locations[self.slot(name)[1]])

    def checked(self, name: str) -> set[int]:
        return set(self.server.location_checks[self.slot(name)])

    def received(self, name: str) -> list[int]:
        """Every item the server has for this player, in the order it sends them."""
        team, slot = self.slot(name)
        items = (MultiServer.get_start_inventory(self.server, slot, True)
                 + MultiServer.get_received_items(self.server, team, slot, True))
        return [item.item for item in items]

    def server_sends(self, name: str, *item_names: str) -> None:
        """Hand a player items the way the server console's /send does once it has looked the names up.
        (The command itself is not used because it needs the fuzzy-matching library to be installed.)"""
        team, slot = self.slot(name)
        MultiServer.send_items_to(self.server, team, slot,
                                  *(NetworkItem(ITEM_NAME_TO_ID[item_name], -1, 0) for item_name in item_names))
        MultiServer.send_new_items(self.server)

    def important_items_on(self, name: str, locations: set[int]) -> list[int]:
        """Locations among `locations` whose item (anyone's) is progression or useful, as the server has it."""
        table = self.server.locations[self.slot(name)[1]]
        return sorted(location for location in locations if location in table and table[location][2] & 0b011)

    def stored(self, name: str) -> dict[str, Any] | None:
        team, slot = self.slot(name)
        return self.server.stored_data.get(f"bioshock_{team}_{slot}")

    def has_goal(self, name: str) -> bool:
        return self.server.client_game_state[self.slot(name)] == ClientStatus.CLIENT_GOAL

    def assert_in_logic(self, name: str) -> None:
        """Every check the server has accepted belongs to a level whose access item the server had already sent."""
        received = set(self.received(name))
        for location in self.checked(name):
            access_item = LEVEL_BY_NAME[LOCATION_LEVEL[location]].access_item
            self.assertTrue(access_item is None or ITEM_NAME_TO_ID[access_item] in received,
                            f"location {location} was checked without {access_item}")

    def assert_finished(self, player: Player) -> None:
        self.assertEqual(self.checked(player.name), self.locations(player.name))
        self.assertTrue(self.has_goal(player.name))
        received = self.received(player.name)
        commands, actions = expected_deliveries(received)
        self.assertEqual(player.game.commands, commands, "every item is given once, in the order the server sent it")
        self.assertEqual(player.game.actions, actions)
        self.assertEqual(self.stored(player.name), player.ctx.core.snapshot())
        self.assertEqual(self.stored(player.name)["delivered"], len(received))


class TestSoloSeed(ServerTestBase):
    async def test_login(self) -> None:
        jack = await self.join("Jack")
        client = self.server_client("Jack")
        self.assertTrue(client.auth)
        self.assertEqual(client.items_handling, 0b111)
        self.assertEqual(tuple(client.version), tuple(Utils.version_tuple))
        self.assertNotIn("DeathLink", client.tags)

        self.assertEqual((jack.ctx.team, jack.ctx.slot), self.slot("Jack"))
        self.assertEqual(jack.ctx.core.valid_locations, self.locations("Jack"))
        self.assertEqual(len(self.locations("Jack")), 210)
        self.assertEqual(jack.ctx.core.access_items["Arcadia"], "Arcadia Access")
        self.assertIsNone(jack.ctx.core.access_items["Medical Pavilion"])
        self.assertIsNone(self.stored("Jack"), "nothing is stored before the client has run")

        await self.settle()
        self.assertEqual(self.stored("Jack"), jack.ctx.core.snapshot())
        self.assertEqual(jack.ctx.core.level, "Welcome to Rapture")

    async def test_whole_seed(self) -> None:
        jack = await self.join("Jack")
        held = 0
        for level in LEVEL_NAMES:
            play_level(jack.game, level)
            await self.settle()
            self.assert_in_logic("Jack")
            held = max(held, len(jack.ctx.core.held_locations()))
            self.assertEqual(self.checked("Jack") | set(jack.ctx.core.held_locations()),
                             jack.ctx.core.collected & self.locations("Jack"))
        self.assertGreater(held, 0, "this seed never held a check back, so it does not test level access")
        self.assertFalse(self.has_goal("Jack"))

        jack.game.defeat_fontaine()
        await self.settle()
        self.assert_finished(jack)

    async def test_finishing_without_a_single_diary_or_film_reel(self) -> None:
        self.assertEqual(self.important_items_on("Jack", COLLECTIBLES), [], "collectibles only hold filler")
        self.assertEqual(len(self.locations("Jack") & COLLECTIBLES), 132)

        jack = await self.join("Jack")
        for level in LEVEL_NAMES:
            play_level(jack.game, level)
            jack.game.diaries.clear()  # walked past every one of them
            jack.game.reels.clear()
            await self.settle()
            self.assert_in_logic("Jack")
        jack.game.defeat_fontaine()
        await self.settle()
        self.assertTrue(self.has_goal("Jack"))
        self.assertEqual(self.checked("Jack"), self.locations("Jack") - COLLECTIBLES)
        self.assertEqual(jack.ctx.core.held_locations(), [])
        self.assertEqual(len(self.received("Jack")), 78, "8 access items and 70 useful items; all the filler stayed")

    async def test_items_sent_by_the_server_itself(self) -> None:
        jack = await self.join("Jack")
        jack.game.collect_diary(42)  # an Arcadia diary, while Arcadia is still locked
        await self.settle()
        self.assertEqual(self.checked("Jack"), set())
        self.assertEqual(jack.ctx.core.held_locations(), [1042])

        self.server_sends("Jack", "Health Upgrade", "Arcadia Access")
        await self.settle()
        self.assertEqual(jack.game.commands[0], "GiveItem 1 ShockGame.HealthUpgrade")
        self.assertIn(1042, self.checked("Jack"), "the held check goes out once the access item arrives")
        self.assertEqual(jack.ctx.core.held_locations(), [])

    async def test_closing_and_reopening_the_client(self) -> None:
        jack = await self.join("Jack")
        for level in LEVEL_NAMES[:4]:
            play_level(jack.game, level)
            await self.settle()
        given = list(jack.game.commands)
        delivered = len(self.received("Jack"))
        held = jack.ctx.core.held_locations()
        self.assertGreater(len(given), 0)
        self.assertEqual(self.stored("Jack")["delivered"], delivered)

        await self.leave(jack)
        self.assertEqual(self.server.clients[0][self.slot("Jack")[1]], [], "the server saw the client leave")

        jack = await self.join("Jack", jack.game)  # a brand-new client, the same running game
        self.assertEqual(jack.ctx.core.delivered, delivered, "the count came back from the server's data storage")
        await self.settle()
        self.assertEqual(jack.game.commands, given, "nothing is given a second time")
        self.assertEqual(jack.ctx.core.held_locations(), held)

        for level in LEVEL_NAMES[4:]:
            play_level(jack.game, level)
            await self.settle()
        jack.game.defeat_fontaine()
        await self.settle()
        self.assert_finished(jack)

    async def test_closing_the_client_while_an_item_is_on_its_way(self) -> None:
        jack = await self.join("Jack")
        play_level(jack.game, "Medical Pavilion")
        for _ in range(3):  # far enough for items to arrive and the first to be sent on to the game
            self.now += 0.25
            jack.ctx.step(self.now)
            await jack.ctx.perform(jack.ctx.core.drain())
            await self.network.quiet()
        self.assertTrue(jack.ctx.core.awaiting_answer, "this test needs an item to be on its way")
        on_its_way = len(jack.game.commands)

        await self.leave(jack)
        self.assertEqual(len(jack.game.commands), on_its_way, "closing does not start another item")
        self.assertEqual(self.stored("Jack")["delivered"], jack.ctx.core.delivered)
        self.assertFalse(jack.ctx.core.awaiting_answer, "the answer was taken before closing")

        jack = await self.join("Jack", jack.game)
        await self.settle()
        commands, _ = expected_deliveries(self.received("Jack"))
        self.assertEqual(jack.game.commands, commands, "the item that was on its way is not given again")

    async def test_dropped_connection_reconnects_by_itself(self) -> None:
        jack = await self.join("Jack", reconnect_delay=0)
        play_level(jack.game, "Medical Pavilion")
        await self.settle()
        checked = self.checked("Jack")
        given = list(jack.game.commands)
        self.assertGreater(len(checked), 10)

        with self.assertLogs("Client", level="WARNING"):
            await jack.ctx.server.socket.peer.close()  # the server's end goes away
            await self.network.quiet()
        self.assertTrue(jack.ctx.core.connected, "the client logged in again without being asked")
        self.assertEqual((jack.ctx.team, jack.ctx.slot), self.slot("Jack"))
        self.server_client("Jack")

        jack.game.collect_diary(1)
        await self.settle()
        self.assertEqual(self.checked("Jack"), checked | {1001})
        self.assertEqual(jack.game.commands[:len(given)], given, "nothing is given a second time")
        self.assert_in_logic("Jack")

    async def test_playing_offline_then_connecting_again(self) -> None:
        jack = await self.join("Jack")
        await self.settle()
        with self.assertLogs("Client", level="WARNING"):
            await jack.ctx.server.socket.peer.close()
            await self.network.quiet()
        self.assertFalse(jack.ctx.core.connected)

        play_level(jack.game, "Welcome to Rapture")
        play_level(jack.game, "Medical Pavilion")
        await self.settle()
        self.assertEqual(self.checked("Jack"), set(), "nothing can be sent while offline")

        self.assertTrue(jack.ctx.command_processor(jack.ctx)("/connect"))
        await self.network.quiet()
        self.assertTrue(jack.ctx.core.connected)
        await self.settle()
        open_levels = {location for location in self.locations("Jack")
                       if LOCATION_LEVEL[location] in ("Welcome to Rapture", "Medical Pavilion")}
        self.assertGreaterEqual(self.checked("Jack"),
                                open_levels - {LOCATION_NAME_TO_ID["Medical Pavilion - Level Complete"]})
        self.assert_in_logic("Jack")
        commands, _ = expected_deliveries(self.received("Jack"))
        self.assertEqual(jack.game.commands, commands)

    async def test_the_clients_own_loop(self) -> None:
        """The same thing without the test turning the crank: the loop the real client runs, on the real clock."""
        jack = await self.join("Jack")
        with mock.patch.object(context_module, "POLL_SECONDS", 0.01), \
                mock.patch.object(context_module, "BUSY_POLL_SECONDS", 0.01):
            jack.ctx.game_task = asyncio.create_task(jack.ctx.game_loop(), name="BioShock game loop")
            play_level(jack.game, "Medical Pavilion")
            for _ in range(500):
                await asyncio.sleep(0.01)
                if len(self.checked("Jack")) >= 10 and not jack.ctx.core.busy \
                        and jack.ctx.core.delivered == len(self.received("Jack")):
                    break
        self.assertGreaterEqual(len(self.checked("Jack")), 10)
        commands, _ = expected_deliveries(self.received("Jack"))
        self.assertEqual(jack.game.commands, commands)
        self.assert_in_logic("Jack")


class TestOtherOptions(ServerTestBase):
    players = {"Jack": {
        "start_inventory": {"Health Upgrade": 2, "Arcadia Access": 1},
        "level_access": "access_items",
        "little_sister_checks": False,
        "power_to_the_people_checks": False,
        "trap_chance": 100,
    }}
    password = "would you kindly"

    async def test_start_inventory_is_delivered_and_counts_for_access(self) -> None:
        jack = await self.join("Jack")
        jack.game.collect_diary(42)  # Arcadia
        await self.settle()
        self.assertEqual(jack.game.commands[:2], ["GiveItem 1 ShockGame.HealthUpgrade"] * 2)
        self.assertIn(1042, self.checked("Jack"))

    async def test_whole_seed_with_fewer_checks_and_traps(self) -> None:
        jack = await self.join("Jack")
        self.assertEqual(len(self.locations("Jack")), 210 - 21 - 12)
        for level in LEVEL_NAMES:
            play_level(jack.game, level)  # the player still deals with every Little Sister and station
            await self.settle()
            self.assert_in_logic("Jack")
        jack.game.defeat_fontaine()
        await self.settle()
        self.assert_finished(jack)
        self.assertGreater(len(jack.game.actions), 20, "traps reached the game")

    async def test_wrong_password_is_asked_for_again(self) -> None:
        ctx = BioShockContext("archipelago.test", "please")
        ctx.auto_attach = False
        ctx.auth = "Jack"
        ctx.server_task = asyncio.create_task(CommonClient.server_loop(ctx), name="Jack: server loop")
        self.joined.append(Player("Jack", ctx, SimulatedGame()))
        with self.assertLogs("Client", level="ERROR") as logs:
            await self.network.quiet()
        self.assertTrue(any("Invalid password" in line for line in logs.output))
        self.assertFalse(ctx.core.connected)

        ctx.input_queue.put_nowait(self.password)  # the player types the right one at the prompt
        await self.network.quiet()
        self.assertTrue(ctx.core.connected)


class TestSecondSeed(ServerTestBase):
    """One client window, two seeds in a row."""
    players = {"Jack": {"level_access": "vanilla"}}
    second_archive: ClassVar[str]

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        directory = tempfile.TemporaryDirectory(prefix="bioshock_ap_test_")
        cls.addClassCleanup(directory.cleanup)
        cls.second_archive = generate_seed(directory.name, cls.players, cls.seed + 1)

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.second_server = new_server(self.second_archive)
        self.network.rooms["second-room"] = self.second_server

    async def test_the_next_seed_starts_clean(self) -> None:
        jack = await self.join("Jack")
        for level in LEVEL_NAMES:
            play_level(jack.game, level)
        jack.game.defeat_fontaine()
        await self.settle()
        self.assert_finished(jack)

        jack.ctx.drop_agent()  # the player quits to the main menu to start a new game ...
        # ... and joins the next room
        self.assertTrue(jack.ctx.command_processor(jack.ctx)("/connect second-room.test"))
        await self.network.quiet()
        jack.ctx.input_queue.put_nowait("Jack")  # a new address, so the client asks for the slot name again
        await self.network.quiet()
        self.assertTrue(jack.ctx.core.connected)
        self.assertNotEqual(self.server.seed_name, self.second_server.seed_name)
        self.assertEqual(jack.ctx.core.identity[0], self.second_server.seed_name)

        team, slot = self.second_server.player_name_lookup["Jack"]
        await self.settle()
        self.assertEqual(self.second_server.location_checks[team, slot], set(),
                         "nothing from the finished seed is sent to the new one")
        self.assertNotEqual(self.second_server.client_game_state[team, slot], ClientStatus.CLIENT_GOAL)
        self.assertEqual(jack.ctx.core.delivered, 0)

        jack.game = SimulatedGame()  # the new game
        jack.ctx.set_agent(jack.game)
        play_level(jack.game, "Welcome to Rapture")
        await self.settle()
        self.assertEqual(self.second_server.location_checks[team, slot], {1001, 1002, 3001, 7001, 7002})
        commands, _ = expected_deliveries([item.item for item in MultiServer.get_received_items(
            self.second_server, team, slot, True)])
        self.assertEqual(jack.game.commands, commands)


class TestTwoPlayers(ServerTestBase):
    players = {
        # Jack's last access item is always somewhere in Atlas's world, so Jack cannot finish alone.
        "Jack": {"death_link": True, "non_local_items": ["Point Prometheus Access"]},
        "Atlas": {"death_link": True},
    }

    async def test_both_finish(self) -> None:
        jack, atlas = await self.join("Jack"), await self.join("Atlas")
        self.assertNotEqual(self.slot("Jack"), self.slot("Atlas"))
        for name in ("Jack", "Atlas"):  # nobody's progression or useful item sits on anybody's collectible
            self.assertEqual(self.important_items_on(name, COLLECTIBLES), [])
        for level in LEVEL_NAMES:
            for player in (jack, atlas):
                play_level(player.game, level)
            await self.settle()
            self.assert_in_logic("Jack")
            self.assert_in_logic("Atlas")
        for player in (jack, atlas):
            player.game.defeat_fontaine()
        await self.settle()
        self.assert_finished(jack)
        self.assert_finished(atlas)

        team, jack_slot = self.slot("Jack")
        from_atlas = [item for item in MultiServer.get_received_items(self.server, team, jack_slot, True)
                      if item.player != jack_slot]
        self.assertGreater(len(from_atlas), 10, "this seed should mix the two worlds")

    async def test_one_player_waits_for_the_other(self) -> None:
        jack, atlas = await self.join("Jack"), await self.join("Atlas")
        for level in LEVEL_NAMES:
            play_level(jack.game, level)
        jack.game.defeat_fontaine()
        await self.settle()
        self.assert_in_logic("Jack")
        self.assertLess(len(self.checked("Jack")), len(self.locations("Jack")),
                        "some of Jack's levels stay locked until Atlas finds their access items")
        held = set(jack.ctx.core.held_locations())
        self.assertEqual(held, self.locations("Jack") - self.checked("Jack"))
        self.assertTrue(jack.ctx.core.goal_reached)
        self.assertFalse(self.has_goal("Jack"), "beating Fontaine does not count until the last level is unlocked")

        for level in LEVEL_NAMES:
            play_level(atlas.game, level)
            await self.settle()
            self.assert_in_logic("Jack")
            self.assert_in_logic("Atlas")
        atlas.game.defeat_fontaine()
        await self.settle()
        self.assert_finished(jack)
        self.assert_finished(atlas)

    async def test_release_sends_everything_at_once(self) -> None:
        jack, atlas = await self.join("Jack"), await self.join("Atlas")
        await self.settle()
        self.assertEqual(jack.game.commands, [])

        self.assertTrue(self.server.commandprocessor("/release Atlas"))  # the host gives out all of Atlas's world
        await self.settle()
        received = self.received("Jack")
        self.assertGreater(len(received), 20)
        self.assertIn(ITEM_NAME_TO_ID["Point Prometheus Access"], received)
        commands, _ = expected_deliveries(received)
        self.assertEqual(jack.game.commands, commands)
        self.assertEqual(self.stored("Jack")["delivered"], len(received))
        self.assertEqual(self.checked("Atlas"), self.locations("Atlas"))

        play_level(atlas.game, "Medical Pavilion")  # already checked by the release: nothing to send, nothing breaks
        await self.settle()
        self.assertEqual(self.checked("Atlas"), self.locations("Atlas"))
        self.assertEqual(atlas.ctx.core.held_locations(), [])

    async def test_deathlink(self) -> None:
        jack, atlas = await self.join("Jack"), await self.join("Atlas")
        await self.settle()
        self.assertIn("DeathLink", self.server_client("Jack").tags)
        self.assertIn("DeathLink", self.server_client("Atlas").tags)

        jack.game.die()
        await self.settle()
        self.assertEqual((jack.game.deaths, atlas.game.deaths), (1, 1), "Atlas dies with Jack, and it stops there")

        atlas.game.die()
        await self.settle()
        self.assertEqual((jack.game.deaths, atlas.game.deaths), (2, 2))


class TestThroughTheHelper(ServerTestBase):
    """Server, client, helper process and "Frida": everything between Archipelago and the game's console."""

    async def join_through_the_helper(self, name: str, fake: FakeFrida) -> Player:
        ctx = BioShockContext("archipelago.test", None)
        ctx.python_candidates = lambda: [PYTHON]  # type: ignore[method-assign]
        ctx.helper_env = fake.env
        ctx.auth = name
        ctx.server_task = asyncio.create_task(CommonClient.server_loop(ctx), name=f"{name}: server loop")
        player = Player(name, ctx, SimulatedGame())  # the simulated game stays unused
        self.joined.append(player)
        await self.network.quiet()
        self.assertTrue(ctx.core.connected, f"{name} could not log in")
        ctx.game_task = asyncio.create_task(ctx.game_loop(), name="BioShock game loop")
        return player

    async def until(self, condition: Any, what: str, seconds: float = 30.0) -> None:
        for _ in range(int(seconds / 0.01)):
            if condition():
                return
            await asyncio.sleep(0.01)
        self.fail(f"timed out waiting for {what}")

    async def test_items_and_checks_travel_the_whole_way(self) -> None:
        fake = FakeFrida(self)
        sent = ("Health Upgrade", "Armored Shell 2", "Crossbow", "Crossbow Damage Increase", "Ammo Bundle",
                "EVE Drain Trap", "Invention Components", "Progressive Winter Blast", "Security Alarm Trap")
        with mock.patch.object(context_module, "POLL_SECONDS", 0.01), \
                mock.patch.object(context_module, "BUSY_POLL_SECONDS", 0.01), self.assertLogs("Client") as logs:
            jack = await self.join_through_the_helper("Jack", fake)
            core = jack.ctx.core
            await self.until(lambda: isinstance(jack.ctx.agent, FridaAgent), "the client to find the game")
            self.server_sends("Jack", *sent)
            await self.until(lambda: len(self.received("Jack")) >= len(sent) and not core.busy
                             and core.delivered == len(self.received("Jack")), "the items to reach the game")

            received = self.received("Jack")
            commands, actions = expected_deliveries(received)
            self.assertEqual([call["command"] for call in fake.calls("exec")], commands)
            self.assertEqual([call["name"] for call in fake.calls("action")], actions)
            self.assertIn("GiveItem 1 ShockDesignerClasses.ArmoredBodyTwo", commands)
            self.assertIn("GiveWeapon ShockGame.Crossbow", commands)
            self.assertIn("StartSecurityAlarm", commands)
            self.assertEqual(actions, ["eve_drain"])
            self.assertIn("GiveItem 1 ShockDesignerClasses.SteelScrewComponent", commands)
            # The stand-in agent cannot do traps of its own. Everything else has a command.
            self.assertEqual(sorted(core.skipped.values()), [REFUSED])
            self.assertEqual(core.level, "Medical Pavilion", "from the name of the map the game is in")

            # The player walks into Neptune's Bounty: Medical Pavilion is finished, and that is a check.
            before = len(self.checked("Jack"))
            fake.order(state={"map": "2-Fisheries_int.bsm"})
            await self.until(lambda: core.level == "Neptune's Bounty" and len(self.checked("Jack")) > before
                             and not core.busy and core.delivered == len(self.received("Jack")),
                             "the level change to turn into checks, and their items to arrive")
            self.assertIn(5002, self.checked("Jack"), "Medical Pavilion - Level Complete")
            self.assert_in_logic("Jack")
            commands, actions = expected_deliveries(self.received("Jack"))
            self.assertEqual([call["command"] for call in fake.calls("exec")], commands)

            await self.until(lambda: self.stored("Jack") == core.snapshot(), "the progress to be saved on the server")
            self.assertEqual(self.stored("Jack")["delivered"], len(self.received("Jack")))
            await self.leave(jack)
            await self.until(lambda: fake.names()[-2:] == ["detach", "shutdown"],
                             "the agent to be taken out of the game when the client closes")
        text = "\n".join(logs.output)
        self.assertIn("Game connected: BioShock Remastered (Steam 1.0.127355).", text)
        self.assertIn("Delivered Armored Shell 2.", text)
        self.assertIn("Delivered Ammo Bundle.", text)
        self.assertIn("Could not deliver EVE Drain Trap", text)
        self.assertEqual(len(fake.calls("import")), 1)

    async def test_an_item_the_game_gives_up_over_is_not_given_again_by_itself(self) -> None:
        fake = FakeFrida(self, dies_on="HealthUpgrade", raises=["C++ exception: the number 1"])
        with mock.patch.object(context_module, "POLL_SECONDS", 0.01), \
                mock.patch.object(context_module, "BUSY_POLL_SECONDS", 0.01), \
                mock.patch.object(context_module, "ATTACH_RETRY_SECONDS", 0.05), self.assertLogs("Client") as logs:
            jack = await self.join_through_the_helper("Jack", fake)
            core = jack.ctx.core
            await self.until(lambda: isinstance(jack.ctx.agent, FridaAgent), "the client to find the game")
            first = jack.ctx.agent
            self.server_sends("Jack", "Health Upgrade", "EVE Upgrade")
            # The game gives up over the first item and is gone. The player starts it again, the client finds it
            # again, and carries on with the next item.
            await self.until(lambda: len(self.received("Jack")) >= 2 and not core.busy
                             and core.delivered == len(self.received("Jack")) and jack.ctx.agent is not first,
                             "the second item to reach the game that was started again")
            self.assertIsNot(jack.ctx.agent, first)
            self.assertEqual([call["command"] for call in fake.calls("exec")],
                             ["GiveItem 1 ShockGame.HealthUpgrade", "GiveItem 1 ShockGame.BioAmmoUpgrade"],
                             "each asked for once: the game is not sent back into what closed it")
            self.assertEqual(core.skipped, {0: REFUSED})
            await self.until(lambda: self.stored("Jack") == core.snapshot(), "the progress to be saved on the server")
            self.assertEqual(self.stored("Jack")["skipped"], [[0, REFUSED]],
                             "a client started afresh would know to leave that item alone too")

            fake.order(dies_on="", raises=[])  # as if an update had fixed whatever it was
            jack.ctx.command_processor(jack.ctx)("/retry")
            await self.until(lambda: core.skipped == {} and not core.busy, "the item to arrive when asked for again")
            self.assertEqual([call["command"] for call in fake.calls("exec")][-1], "GiveItem 1 ShockGame.HealthUpgrade")
            await self.leave(jack)
        text = "\n".join(logs.output)
        self.assertIn('Before the game went away it raised, while running "GiveItem 1 ShockGame.HealthUpgrade": '
                      "C++ exception: the number 1", text)
        self.assertIn("Game disconnected: the game closed", text)
        self.assertIn("The game went away while it was being given Health Upgrade.", text)
        self.assertIn("Delivered EVE Upgrade.", text)
        self.assertLess(text.index("Before the game went away it raised"), text.index("The game went away while"),
                        "first the game's own word, then what the client makes of it")

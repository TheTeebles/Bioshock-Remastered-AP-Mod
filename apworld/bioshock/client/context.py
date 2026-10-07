"""
The Archipelago side of the BioShock client.

A CommonClient that feeds BridgeCore what the server and the game say, then carries out what the core decides.
All the decisions live in core.py; this file only moves data between the server, the core and the game agent.

Importing this module pulls in CommonClient, so the world only imports it when the client is launched.
"""
from __future__ import annotations

import asyncio
import os
import time
from argparse import Namespace
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

import Utils
from CommonClient import ClientCommandProcessor, CommonContext, get_base_parser, handle_url_arg, logger, server_loop
from MultiServer import mark_raw
from NetUtils import ClientStatus
from Utils import async_start, gui_enabled

from ..data import GAME_NAME
from . import frida_agent
from .core import BridgeCore
from .frida_agent import AgentUnusable, FridaAgent
from .protocol import (
    Action, AgentError, GameAgent, KillPlayer, Notice, RunAction, RunCommand, SaveState, SendChecks, SendDeath,
    SendGoal,
)
from .simulated import SimulatedGame, resolve_level

if TYPE_CHECKING:
    import kvui

POLL_SECONDS = 0.25  # how often the game is read
BUSY_POLL_SECONDS = 0.05  # while items are being delivered
ATTACH_RETRY_SECONDS = 5.0
CLOSING_WAIT_SECONDS = 1.0  # how long closing the client waits for the game to confirm an item on its way
STORAGE_CATEGORY = "bioshock"  # in Archipelago's own settings file on this PC

SIM_HELP = ("story | sister | station | reel | diary <1-122> | travel <level> | fontaine | die | loading | pause | "
            "bag   (story, sister, station and reel use the level you travelled to)")


def stored_python() -> str | None:
    """The Python the player named with /python on this PC, if any."""
    value = Utils.persistent_load().get(STORAGE_CATEGORY, {}).get("python")
    return value if isinstance(value, str) and value else None


class BioShockCommandProcessor(ClientCommandProcessor):
    ctx: BioShockContext

    def _cmd_bioshock(self) -> bool:
        """Show what the client is doing: connections, delivered items and held checks."""
        for line in self.ctx.core.status_lines():
            self.output(line)
        return True

    def _cmd_attach(self) -> bool:
        """Connect to the running game now. The client also keeps trying on its own."""
        self.ctx.auto_attach = True
        self.ctx._gave_up = False
        if isinstance(self.ctx.agent, FridaAgent):
            self.output("Already connected to the game.")
        else:
            self.ctx.attach_game(announce=True)
            self.output("Looking for the game.")
        return True

    def _cmd_detach(self) -> bool:
        """Disconnect from the game and stop reconnecting to it until /attach."""
        self.ctx.auto_attach = False
        self.ctx._gave_up = False  # the player's own decision now
        self.ctx.stop_looking()
        self.ctx.drop_agent()
        return True

    @mark_raw
    def _cmd_python(self, path: str = "") -> bool:
        """Show or name the Python that has Frida, e.g. /python C:/Python312/python.exe. /python auto forgets it."""
        ctx = self.ctx
        path = path.strip().strip('"')
        if not path:
            if isinstance(ctx.agent, FridaAgent):
                self.output(f"The game is reached with Frida {ctx.agent.frida_version} from {ctx.agent.python}.")
            self.output(f"You named this Python: {ctx.chosen_python}" if ctx.chosen_python else
                        "The client looks for a Python with Frida by itself. /python <path to python.exe> names one.")
            return True
        if path.lower() == "auto":
            ctx.choose_python(None)
            self.output("The client looks for a Python with Frida by itself again.")
        elif not os.path.isfile(path):
            self.output(f"There is no file {path}")
            return False
        else:
            ctx.choose_python(path)
            self.output(f"Looking for Frida in {path} first from now on.")
        if ctx._gave_up:  # the client had stopped looking by itself; a new choice is worth a new try
            ctx._gave_up = False
            ctx.auto_attach = True
        return True

    def _cmd_simulate(self) -> bool:
        """Use a simulated game instead of the real one, to try the client out. Run it again to stop."""
        if isinstance(self.ctx.agent, SimulatedGame):
            self.ctx.drop_agent()
            self.ctx.auto_attach = True
            self.output("Simulated game stopped." + (
                " What it was given counts as given; /resync gives it again." if self.ctx.core.delivered else ""))
        else:
            self.ctx.start_simulation()
            self.output("Simulated game started. It sends real checks and counts items as given, so use it on a "
                        "test seed, not on one you are playing for real.")
            self.output(f"Play it with /sim: {SIM_HELP}")
        return True

    @mark_raw
    def _cmd_sim(self, text: str = "") -> bool:
        """Play the simulated game, e.g. /sim story, /sim travel arcadia, /sim sister, /sim fontaine."""
        game = self.ctx.agent
        if not isinstance(game, SimulatedGame):
            self.output("Start the simulated game with /simulate first.")
            return False
        verb, _, rest = text.strip().partition(" ")
        rest = rest.strip()
        try:
            if verb == "diary":
                if not rest.isdigit():
                    self.output("Which diary? For example: /sim diary 3")
                    return False
                self.output(f"Picked up audio diary {rest} ({game.collect_diary(int(rest))}).")
            elif verb == "story":
                self.output(f"{game.level}: {game.reach_milestone()}.")
            elif verb == "sister":
                game.deal_with_little_sister()
                self.output(f"Dealt with a Little Sister in {game.level}.")
            elif verb == "station":
                game.use_station()
                self.output(f"Used a Power to the People station in {game.level}.")
            elif verb == "reel":
                game.collect_reel()
                self.output(f"Picked up the film reel in {game.level}.")
            elif verb == "travel":
                game.travel(resolve_level(rest))
                self.output(f"Now in {game.level}.")
            elif verb == "fontaine":
                game.defeat_fontaine()
                self.output("Fontaine is defeated.")
            elif verb == "die":
                game.die()
                self.output("You died.")
            elif verb == "loading":
                game.loading = not game.loading
                self.output(f"Loading screen {'on' if game.loading else 'off'}.")
            elif verb == "pause":
                game.paused = not game.paused
                self.output("Game paused: items wait until it continues." if game.paused else "Game continues.")
            elif verb == "bag":
                self.output(f"In {game.level}. Given so far: "
                            + (", ".join(f"{count}x {name}" for name, count in sorted(game.inventory.items()))
                               or "nothing"))
            else:
                self.output(f"/sim {SIM_HELP}")
                return False
        except ValueError as error:  # the simulated game explains what was wrong
            self.output(str(error))
            return False
        return True

    def _cmd_resync(self, count: str = "") -> bool:
        """Give received items again, e.g. after loading an older save. /resync 5 gives the last 5 again."""
        if count and not count.isdigit():
            self.output("Use /resync for everything, or /resync <number> for the most recent items.")
            return False
        again = self.ctx.core.resync(int(count) if count else None)
        self.output(f"Giving {again} item(s) again." if again else "Nothing has been given yet.")
        return True

    def _cmd_retry(self) -> bool:
        """Try again to give the items that were set aside because the game refused them."""
        again = self.ctx.core.retry_set_aside()
        self.output(f"Trying {again} set-aside item(s) again." if again else "No set-aside items can be tried.")
        return True

    def _cmd_deathlink(self) -> bool:
        """Turn DeathLink on or off for this session."""
        self.ctx.core.death_link = not self.ctx.core.death_link
        async_start(self.ctx.update_death_link(self.ctx.core.death_link), name="BioShock DeathLink")
        self.output(f"DeathLink {'on' if self.ctx.core.death_link else 'off'}.")
        return True


class BioShockContext(CommonContext):
    game = GAME_NAME
    items_handling = 0b111  # the server sends every item, including our own and the starting inventory
    command_processor = BioShockCommandProcessor

    def __init__(self, server_address: str | None = None, password: str | None = None) -> None:
        super().__init__(server_address, password)
        self.tags = set(self.tags)  # the class-level set is shared by every context
        self.core = BridgeCore()
        self.agent: GameAgent | None = None
        self.auto_attach = True
        self.game_task: asyncio.Task[None] | None = None
        self.chosen_python = stored_python()
        self.helper_env: Mapping[str, str] | None = None  # environment for the helper; the client's own when None
        self._link: FridaAgent | None = None  # on its way into the game, not there yet
        self._python: tuple[str, ...] | None = None  # the Python that had Frida last time
        self._announce = False
        self._gave_up = False  # the client itself stopped looking for the game, as opposed to the player
        self._next_attach = 0.0
        self._last_step = 0.0
        self._last_attach_problem = ""
        self._room_seed: str | None = None
        self._slot_data: dict[str, Any] | None = None
        self._core_connected = False

    # ---------------------------------------------------------------------------------------------------------------
    # Archipelago server
    # ---------------------------------------------------------------------------------------------------------------

    @property
    def storage_key(self) -> str:
        return f"bioshock_{self.team}_{self.slot}"

    async def server_auth(self, password_requested: bool = False) -> None:
        if password_requested and not self.password:
            await super().server_auth(password_requested)
        await self.get_username()
        await self.send_connect(game=self.game)

    def on_package(self, cmd: str, args: dict[str, Any]) -> None:
        if cmd == "RoomInfo":
            self._room_seed = args.get("seed_name")
        elif cmd == "Connected":
            # The core needs the saved state before it may deliver anything, so fetch that first.
            self._slot_data = args.get("slot_data") or {}
            self._core_connected = False
            async_start(self.send_msgs([{"cmd": "Get", "keys": [self.storage_key]}]), name="BioShock saved state")
        elif cmd == "Retrieved":
            keys = args.get("keys", {})
            if self.slot is not None and self.storage_key in keys and not self._core_connected \
                    and self._slot_data is not None:
                self._core_connected = True
                self.core.on_connected(
                    (self._room_seed, self.team, self.slot), self._slot_data, self.server_locations,
                    self.checked_locations, keys[self.storage_key], self._received_item_ids())
                async_start(self.update_death_link(self.core.death_link), name="BioShock DeathLink")
        elif cmd == "ReceivedItems":
            if self._core_connected:
                self.core.on_items(self._received_item_ids())
        elif cmd == "RoomUpdate":
            if self._core_connected and "checked_locations" in args:
                self.core.on_checked(args["checked_locations"])

    def _received_item_ids(self) -> list[int]:
        return [item.item for item in self.items_received]

    async def connection_closed(self) -> None:
        self._core_connected = False
        self._slot_data = None
        self.core.on_disconnected()
        await super().connection_closed()

    def on_deathlink(self, data: dict[str, Any]) -> None:
        super().on_deathlink(data)
        self.core.on_deathlink()

    # ---------------------------------------------------------------------------------------------------------------
    # Game agent
    # ---------------------------------------------------------------------------------------------------------------

    def set_agent(self, agent: GameAgent) -> None:
        self.drop_agent()
        self.agent = agent
        self.core.on_agent_attached(agent.description)

    def drop_agent(self, reason: str = "") -> None:
        if self.agent is None:
            return
        # Take the answers that are already waiting, so that an item the game confirmed just before it went away,
        # or just before the player let go of it, is not given again next time.
        try:
            for event in self.agent.poll_events():
                self.core.on_event(event, self._last_step)
        except AgentError:
            pass
        try:
            self.agent.close()
        except Exception as error:  # closing is best effort; the game may already be gone
            logger.debug(f"Closing the game agent failed: {error}")
        self.agent = None
        self.core.on_agent_detached(reason)

    def python_candidates(self) -> list[tuple[str, ...]]:
        """Where to look for Frida: the Python that had it last time, the one the player named, then this PC's."""
        return frida_agent.python_candidates([self._python, self.chosen_python])

    def choose_python(self, path: str | None) -> None:
        """Remember, on this PC, which Python the player wants used. None goes back to looking for one."""
        self.chosen_python = path
        self._python = None
        Utils.persistent_store(STORAGE_CATEGORY, "python", path or "")
        if not isinstance(self.agent, FridaAgent):  # an attempt under way would still be using the old choice
            self.stop_looking()
            self._next_attach = 0.0

    def attach_game(self, announce: bool) -> None:
        """Start looking for the running game, or look again. Returns at once; step() reports how it went.

        `announce` reports a problem even if the same one was reported before.
        """
        if self.agent is not None and not isinstance(self.agent, SimulatedGame):
            return
        self._announce = self._announce or announce
        if self._link is None:
            self._link = FridaAgent.start(self.python_candidates(), self.helper_env)
        else:
            self._link.try_again()

    @property
    def looking(self) -> bool:
        """True while an attempt to reach the game is under way and has no outcome yet."""
        return self._link is not None and self._link.busy

    def stop_looking(self) -> None:
        """Give up an attempt to reach the game that has not got there yet."""
        link, self._link = self._link, None
        if link is not None:
            link.close()

    def _follow_attach(self) -> None:
        """See how the attempt to reach the game is going, and act on the outcome once there is one."""
        link = self._link
        if link is None:
            return
        try:
            if not link.poll_attach():
                return
        except AgentUnusable as error:
            self.stop_looking()
            self.auto_attach = False  # asking again would only fail the same way; /attach tries once more
            self._gave_up = True
            self._announce = False
            logger.warning(str(error))
            return
        except AgentError as error:
            if not link.alive or self.agent is not None:
                self.stop_looking()  # the next attempt starts afresh; with a simulated game there is none
            if self._announce or str(error) != self._last_attach_problem:
                logger.info(f"{error}. " + ("Waiting for the game." if self.agent is None else
                                            "The simulated game stays; /attach tries again."))
            self._last_attach_problem = str(error)
            self._announce = False
            return
        self._link = None
        self._last_attach_problem = ""
        self._announce = False
        self._gave_up = False
        self._python = link.command
        self.set_agent(link)

    def start_simulation(self) -> SimulatedGame:
        self.auto_attach = False
        self._gave_up = False
        self.stop_looking()
        game = SimulatedGame()
        self.set_agent(game)
        return game

    # ---------------------------------------------------------------------------------------------------------------
    # The loop
    # ---------------------------------------------------------------------------------------------------------------

    def step(self, now: float) -> None:
        """Read the game once and let the core react. Does no I/O with the server."""
        self._last_step = now
        if self.agent is None and self.auto_attach and now >= self._next_attach:
            self._next_attach = now + ATTACH_RETRY_SECONDS
            self.attach_game(announce=False)
        self._follow_attach()

        agent = self.agent
        if agent is not None:
            try:
                state = agent.read_state()
                events = agent.poll_events()
            except AgentError as error:
                self.drop_agent(str(error))
            else:
                self.core.on_state(state)
                for event in events:
                    self.core.on_event(event, now)
        self.core.tick(now)

    async def perform(self, actions: Iterable[Action]) -> None:
        """Carry out the core's decisions."""
        for action in actions:
            if isinstance(action, Notice):
                (logger.warning if action.warning else logger.info)(action.text)
            elif isinstance(action, SendChecks):
                # Deliberately not recorded in `locations_checked` (nor the goal in `finished_game`): CommonClient
                # replays those on every login, including a login to a different seed. The core re-sends what a
                # reconnect needs by itself.
                await self.check_locations(action.locations)
            elif isinstance(action, SendGoal):
                await self.send_msgs([{"cmd": "StatusUpdate", "status": ClientStatus.CLIENT_GOAL}])
            elif isinstance(action, SendDeath):
                await self.send_death(f"{self.player_names.get(self.slot, 'Jack')} {action.cause}")
            elif isinstance(action, SaveState):
                await self.send_msgs([{
                    "cmd": "Set", "key": self.storage_key, "default": {}, "want_reply": False,
                    "operations": [{"operation": "replace", "value": action.data}],
                }])
            else:
                self._to_agent(action)

    def _to_agent(self, action: Action) -> None:
        agent = self.agent
        if agent is None:
            return
        try:
            if isinstance(action, RunCommand):
                agent.run_command(action.command_id, action.command)
            elif isinstance(action, RunAction):
                agent.run_action(action.command_id, action.action)
            elif isinstance(action, KillPlayer):
                agent.kill_player()
        except AgentError as error:
            self.drop_agent(str(error))

    async def game_loop(self) -> None:
        while not self.exit_event.is_set():
            try:
                self.step(time.monotonic())
                await self.perform(self.core.drain())
            except Exception:
                logger.exception("The BioShock client hit an unexpected error and will keep going.")
            await asyncio.sleep(BUSY_POLL_SECONDS if self.core.busy else POLL_SECONDS)

    async def finish_delivery(self) -> None:
        """Before closing: start no more items, and give the game a moment to confirm the one on its way.

        Without this the client would not know that item arrived, and would give it again next time.
        """
        self.auto_attach = False
        self.stop_looking()
        self.core.stop_deliveries()
        deadline = time.monotonic() + CLOSING_WAIT_SECONDS
        while True:
            self.step(self._last_step)  # same time as before: this is about answers, not about timeouts
            await self.perform(self.core.drain())
            if not self.core.awaiting_answer or time.monotonic() >= deadline:
                break
            await asyncio.sleep(BUSY_POLL_SECONDS)
        if self.core.connected:  # the loop may have been stopped halfway through saving
            await self.perform([SaveState(self.core.snapshot())])

    async def shutdown(self) -> None:
        if self.game_task is not None:
            self.game_task.cancel()
        try:
            await self.finish_delivery()
        except Exception:
            logger.exception("Could not finish the last delivery while closing.")
        self.auto_attach = False
        self.stop_looking()
        self.drop_agent()
        try:
            # Letting go of the game can settle an item too: one the game was stuck in is set aside. The server
            # has to hear of that, or the next start of the client would send the game straight back into it.
            self.core.tick(self._last_step)
            await self.perform(self.core.drain())
        except Exception:
            logger.exception("Could not save the last of the progress while closing.")
        await super().shutdown()

    def make_gui(self) -> type[kvui.GameManager]:
        ui = super().make_gui()
        ui.base_title = "Archipelago BioShock Client"
        return ui


async def main(args: Namespace) -> None:
    ctx = BioShockContext(args.connect, args.password)
    ctx.auth = args.name
    ctx.server_task = asyncio.create_task(server_loop(ctx), name="server loop")
    if gui_enabled:
        ctx.run_gui()
    ctx.run_cli()
    if args.simulate:
        ctx.start_simulation()
    ctx.game_task = asyncio.create_task(ctx.game_loop(), name="BioShock game loop")

    await ctx.exit_event.wait()
    await ctx.shutdown()


def launch(*args: str) -> None:
    import colorama
    import Utils

    Utils.init_logging("BioShockClient", exception_logger="Client")
    parser = get_base_parser(description="BioShock Remastered client for Archipelago.")
    parser.add_argument("--name", default=None, help="Slot Name to connect as.")
    parser.add_argument("--simulate", action="store_true",
                        help="Start with a simulated game instead of attaching to BioShock.")
    parser.add_argument("url", nargs="?", help="Archipelago connection url")
    launch_args = handle_url_arg(parser.parse_args(args), parser)

    colorama.just_fix_windows_console()
    asyncio.run(main(launch_args))
    colorama.deinit()

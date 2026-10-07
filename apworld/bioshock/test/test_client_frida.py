"""
Tests for the way to the real game: the helper process that holds Frida, and the adapter that talks to it.

Real helper processes are started, in the Python the tests run in, with a stand-in `frida` package on their path
(fake_frida.py). So the pipes, the threads and the way a helper comes and goes are the real thing; only Frida and
the game are not.
"""
import ast
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from typing import Any
from unittest import mock

from ..client import frida_agent
from ..client.frida_agent import (
    AgentScriptFailed, AgentUnusable, FridaAgent, FridaUnavailable, GameNotRunning, answer_from_message,
    event_from_message, missing_class, python_candidates, raised_from_message, raised_summary, routine_lookup,
    state_from_agent,
)
from ..client.protocol import ActionResult, AgentError, AgentMessage, CommandResult, CommandStarted, GameState
from .frida_harness import PYTHON, PYTHON_WITHOUT_FRIDA, FakeFrida, RawHelper, wait_for

GAME_IN_MEDICAL = GameState(ready=True, map="1-medical")


# --------------------------------------------------------------------------------------------------------------------
# The helper, spoken to directly
# --------------------------------------------------------------------------------------------------------------------

class TestHelper(unittest.TestCase):
    def attached(self, fake: FakeFrida, source: str | None = None) -> RawHelper:
        helper = RawHelper(self, fake, source)
        helper.expect("hello")
        helper.send(op="setup", process="BioshockHD.exe", source="// the agent")
        helper.send(op="attach")
        helper.expect("attached")
        return helper

    def test_a_whole_conversation(self) -> None:
        fake = FakeFrida(self)
        helper = RawHelper(self, fake)
        hello = helper.expect("hello")
        self.assertEqual(hello["frida"], "17.0.0-fake")
        self.assertEqual(os.path.realpath(hello["python"]), os.path.realpath(sys.executable))

        helper.send(op="setup", process="BioshockHD.exe", source="// the agent")
        helper.send(op="attach")
        attached = helper.expect("attached")
        self.assertEqual(attached["state"]["build"], "Steam 1.0.127355")
        self.assertEqual(helper.events("log"), [{"event": "log", "level": "info",
                                                 "text": "[bioshock-ap] build: Steam 1.0.127355"}])
        self.assertLess(helper.lines.index(json.dumps(helper.events("log")[0])),
                        helper.lines.index(json.dumps(attached)), "what the agent said while loading comes first")

        helper.send(op="exec", id=5, command="GiveItem 10 ShockGame.ADAM")
        helper.send(op="action", id=6, name="eve_drain")
        self.assertEqual(helper.expect("message")["message"]["payload"],
                         {"type": "exec_started", "id": 5, "command": "GiveItem 10 ShockGame.ADAM"})
        self.assertEqual(helper.expect("message", 2)["message"]["payload"],
                         {"type": "exec_result", "id": 5, "command": "GiveItem 10 ShockGame.ADAM", "handled": True,
                          "via": "player", "output": [], "outputLines": 0, "raised": 0})
        self.assertEqual(helper.expect("message", 3)["message"]["payload"]["type"], "action_result")
        self.assertEqual(helper.expect("state", 3)["state"]["map"], "1-Medical.bsm", "the state keeps coming")

        helper.close_input()
        self.assertEqual(helper.wait(), 0)
        self.assertEqual(fake.names(), ["import", "attach", "create_script", "load", "exec", "action", "detach",
                                        "shutdown"], "it takes the agent out of the game before it leaves")
        self.assertEqual(fake.calls("attach")[0]["name"], "BioshockHD.exe")
        self.assertEqual(fake.calls("create_script")[0]["start"], "// the agent")
        self.assertEqual(helper.errors, [])

    def test_the_folder_it_is_started_in_is_not_searched_for_modules(self) -> None:
        fake = FakeFrida(self)
        RawHelper(self, fake).expect("hello")
        self.assertEqual(fake.calls("import")[0]["path0"], fake.folder,
                         "a json.py lying around in the current folder must not be picked up")

    def test_only_the_conversation_is_on_its_output(self) -> None:
        """Old versions of Frida print what a script logs. That must not land in the middle of the protocol."""
        fake = FakeFrida(self, old_frida=True)
        helper = self.attached(fake)
        helper.expect("state", 2)
        helper.close_input()
        helper.wait()
        for line in helper.lines:
            self.assertIn("event", json.loads(line))
        self.assertEqual(helper.events("log"), [])
        self.assertEqual(helper.errors, ["[bioshock-ap] build: Steam 1.0.127355"])

    def test_it_ignores_what_it_does_not_understand(self) -> None:
        fake = FakeFrida(self)
        helper = RawHelper(self, fake)
        helper.expect("hello")
        helper.write("this is not JSON")
        helper.write("[1, 2, 3]")
        helper.send(op="dance")
        helper.send(op="exec", id=1, command="too early")  # nothing is attached yet
        helper.send(op="setup", process="BioshockHD.exe", source="// the agent")
        helper.send(op="attach")
        helper.expect("attached")
        helper.send(op="attach")  # already attached: nothing to do
        helper.send(op="exec", id=2, command="GiveItem 1 ShockGame.HealthUpgrade")
        helper.expect("message")
        self.assertEqual([call["id"] for call in fake.calls("exec")], [2])
        self.assertEqual(len(fake.calls("attach")), 1)
        self.assertEqual(len(helper.events("attached")), 1)

    def test_a_python_without_frida_says_so_and_ends(self) -> None:
        fake = FakeFrida(self, missing=True)
        for command in (PYTHON, PYTHON_WITHOUT_FRIDA):
            with self.subTest(command=command):
                helper = RawHelper(self, fake, command=command)
                failed = helper.expect("failed")
                self.assertEqual(failed["kind"], "frida_missing")
                self.assertIn("No module named 'frida'", failed["text"])
                self.assertEqual(helper.wait(), 3)
                self.assertEqual(helper.events("hello"), [])

    def test_attaching_that_fails_can_be_tried_again(self) -> None:
        fake = FakeFrida(self, running=False)
        helper = RawHelper(self, fake)
        helper.expect("hello")
        helper.send(op="setup", process="BioshockHD.exe", source="// the agent")
        tries = 0
        for orders, kind, words in (
                ({"running": False}, "not_running", "unable to find process"),
                ({"running": True, "ambiguous": True}, "ambiguous", "ambiguous name"),
                ({"ambiguous": False, "denied": True}, "denied", "unable to access process"),
                ({"denied": False, "attach_error": "process is not responding"}, "attach",
                 "InvalidOperationError: process is not responding"),
        ):
            with self.subTest(kind=kind):
                fake.order(**orders)
                tries += 1
                helper.send(op="attach")
                failed = helper.expect("failed", tries)
                self.assertEqual(failed["kind"], kind)
                self.assertIn(words, failed["text"])
        fake.order(attach_error="")
        helper.send(op="attach")
        helper.expect("attached")
        self.assertEqual(len(fake.calls("import")), 1, "all of that in one and the same helper")
        self.assertEqual(len(fake.calls("attach")), 5)

    def test_a_script_that_throws_while_loading(self) -> None:
        fake = FakeFrida(self, script_error="TypeError: not a function")
        helper = RawHelper(self, fake)
        helper.expect("hello")
        helper.send(op="setup", process="BioshockHD.exe", source="// the agent")
        helper.send(op="attach")
        failed = helper.expect("failed")
        self.assertEqual(failed["kind"], "script")
        self.assertIn("unable to find method 'state'", failed["text"])
        error = helper.expect("message")
        self.assertEqual(error["message"]["description"], "TypeError: not a function")
        self.assertLess(helper.lines.index(json.dumps(error)), helper.lines.index(json.dumps(failed)),
                        "what the script itself reported arrives before the verdict")
        self.assertEqual(fake.names()[-1], "detach", "the broken script is taken out of the game again")
        time.sleep(0.3)
        self.assertEqual(helper.events("detached"), [], "being taken out by the helper is not news")
        self.assertEqual(len(helper.events("failed")), 1)

    def test_the_game_closing_while_the_script_loads(self) -> None:
        for delay in (0.0, 0.15):  # Frida's word on why arrives before the call that fails, or after
            with self.subTest(delay=delay):
                fake = FakeFrida(self, closes_while_loading=True, detach_delay=delay)
                helper = RawHelper(self, fake)
                helper.expect("hello")
                helper.send(op="setup", process="BioshockHD.exe", source="// the agent")
                helper.send(op="attach")
                failed = helper.expect("failed")
                self.assertEqual((failed["kind"], failed["text"]), ("closed", "process-terminated"),
                                 "not a broken script: that would stop the client from trying again")
                time.sleep(0.3)
                self.assertEqual(helper.events("detached"), [])
                self.assertEqual(helper.events("attached"), [])
                self.assertEqual(len(helper.events("failed")), 1)

    def test_the_game_closing_before_anything_listens_for_it(self) -> None:
        fake = FakeFrida(self, closes_at_once=True)
        helper = RawHelper(self, fake)
        helper.expect("hello")
        helper.send(op="setup", process="BioshockHD.exe", source="// the agent")
        helper.send(op="attach")
        failed = helper.expect("failed")
        self.assertEqual((failed["kind"], failed["text"]), ("closed", "the game closed"),
                         "worth another try, which a broken script would not be")
        fake.order(closes_at_once=False)
        helper.send(op="attach")
        helper.expect("attached")

    def test_the_game_closing_later(self) -> None:
        for delay in (0.0, 0.15):  # Frida's word on why arrives before the call that fails, or after
            with self.subTest(delay=delay):
                fake = FakeFrida(self)
                helper = self.attached(fake)
                helper.expect("state", 2)
                fake.order(detach="process-terminated", detach_delay=delay)
                self.assertEqual(helper.expect("detached")["reason"], "process-terminated",
                                 "the cause, not the call that failed because of it")
                states = len(helper.events("state"))
                helper.send(op="exec", id=9, command="GiveItem 1 ShockGame.HealthUpgrade")
                time.sleep(0.4)
                self.assertEqual(len(helper.events("detached")), 1, "said once, and never as the helper's own doing")
                self.assertEqual(helper.events("detached")[0]["reason"], "process-terminated")
                self.assertLessEqual(len(helper.events("state")), states + 1, "no more state after the game is gone")
                self.assertEqual(fake.calls("exec"), [], "and no more commands into it")
                helper.close_input()
                self.assertEqual(helper.wait(), 0)

    def test_a_call_that_fails(self) -> None:
        fake = FakeFrida(self)
        helper = self.attached(fake)
        fake.order(state_error="script is destroyed")
        self.assertEqual(helper.expect("detached")["reason"], "InvalidOperationError: script is destroyed")
        wait_for(lambda: "detach" in fake.names(), "the script and its hook to be taken out of the game")
        self.assertEqual(len(helper.events("detached")), 1)

    def test_it_can_attach_again_after_the_game_went_away(self) -> None:
        fake = FakeFrida(self)
        helper = self.attached(fake)
        fake.order(detach="process-terminated")
        helper.expect("detached")
        fake.order(detach="")
        helper.send(op="attach")
        helper.expect("attached", 2)
        helper.send(op="exec", id=3, command="GiveItem 1 ShockGame.HealthUpgrade")
        self.assertEqual(helper.expect("message")["message"]["payload"]["id"], 3)

    def test_it_leaves_when_its_input_closes_even_if_the_game_has_frozen(self) -> None:
        source = frida_agent.load_helper_source()
        self.assertIn("LINGER_SECONDS = 5.0", source)
        fake = FakeFrida(self)
        helper = self.attached(fake, source.replace("LINGER_SECONDS = 5.0", "LINGER_SECONDS = 0.5"))
        helper.expect("state", 2)
        fake.order(hang=True)
        time.sleep(0.3)  # by now its main thread is inside a call that does not return
        states = len(helper.events("state"))
        time.sleep(0.3)
        self.assertEqual(len(helper.events("state")), states, "the helper is stuck, as intended")
        started = time.monotonic()
        helper.close_input()
        self.assertEqual(helper.wait(10), 0)
        self.assertGreater(time.monotonic() - started, 0.3, "it gave the stuck call a moment first")

    def test_it_leaves_when_the_client_dies(self) -> None:
        """The client is killed without a chance to tidy up. Its helper must still take the agent out and go."""
        client = r'''
import json, subprocess, sys, time
bootstrap, source = json.loads(sys.stdin.readline())
helper = subprocess.Popen([sys.executable, "-c", bootstrap], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
def send(value):
    helper.stdin.write(json.dumps(value).encode() + b"\n")
    helper.stdin.flush()
send(source)
send({"op": "setup", "process": "BioshockHD.exe", "source": "// the agent"})
send({"op": "attach"})
while b'"attached"' not in helper.stdout.readline():
    pass
print("attached", flush=True)
time.sleep(600)
'''
        fake = FakeFrida(self)
        process = subprocess.Popen([sys.executable, "-c", client], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   env=fake.env)
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps([frida_agent.BOOTSTRAP, frida_agent.load_helper_source()]).encode() + b"\n")
        process.stdin.flush()
        self.assertEqual(process.stdout.readline().strip(), b"attached")
        self.assertNotIn("detach", fake.names())
        process.kill()
        wait_for(lambda: fake.names()[-2:] == ["detach", "shutdown"], "the orphaned helper to tidy up and leave")
        process.stdin.close()
        process.stdout.close()

    def test_it_runs_in_other_pythons(self) -> None:
        """The player's Python need not be the version Archipelago uses."""
        others = [path for minor in range(7, 20) for path in [shutil.which(f"python3.{minor}")]
                  if path and minor != sys.version_info[1]]
        if not others:
            self.skipTest("no other Python on this machine")
        for path in others:
            with self.subTest(python=path):
                fake = FakeFrida(self)
                helper = RawHelper(self, fake, command=(path,))
                helper.expect("hello")
                helper.send(op="setup", process="BioshockHD.exe", source="// the agent")
                helper.send(op="attach")
                helper.expect("attached")
                helper.send(op="exec", id=1, command="GiveItem 10 ShockGame.ADAM")
                helper.expect("message")
                helper.close_input()
                self.assertEqual(helper.wait(), 0)
                self.assertEqual(helper.errors, [])
                self.assertEqual(fake.names()[-2:], ["detach", "shutdown"])

    def test_it_is_written_for_old_pythons_and_needs_nothing_but_frida(self) -> None:
        source = frida_agent.load_helper_source()
        tree = ast.parse(source, feature_version=(3, 7))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, "it is not part of a package where it runs")
                imported.add((node.module or "").split(".")[0])
        self.assertEqual(imported, {"__future__", "frida", "json", "os", "queue", "sys", "threading", "time",
                                    "traceback"})
        for node in ast.walk(tree):  # things Python 3.7 does not have, which parsing as 3.7 does not catch
            self.assertNotIsInstance(node, ast.NamedExpr)
            if isinstance(node, ast.arguments):
                self.assertEqual(node.posonlyargs, [])
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.assertIsNone(node.returns, "annotations would have to be valid in every Python too")

    def test_the_command_that_starts_it_survives_any_shell_free_launch(self) -> None:
        self.assertNotIn('"', frida_agent.BOOTSTRAP, "double quotes are where Windows command lines go wrong")
        self.assertNotIn("%", frida_agent.BOOTSTRAP)
        self.assertNotIn("\n", frida_agent.BOOTSTRAP)
        compile(frida_agent.BOOTSTRAP, "bootstrap", "exec")


# --------------------------------------------------------------------------------------------------------------------
# The adapter
# --------------------------------------------------------------------------------------------------------------------

class FridaAgentTestBase(unittest.TestCase):
    def start(self, fake: FakeFrida | None, candidates: Any = (PYTHON,)) -> FridaAgent:
        agent = FridaAgent.start(candidates, fake.env if fake is not None else None)
        self.addCleanup(agent.close)
        return agent

    def outcome(self, agent: FridaAgent) -> Any:
        """Wait for attaching to have an outcome: True, or the error it raised."""
        def poll() -> Any:
            try:
                return agent.poll_attach()
            except AgentError as error:
                return error

        return wait_for(poll, "attaching to have an outcome")

    def attached(self, fake: FakeFrida) -> FridaAgent:
        agent = self.start(fake)
        self.assertIs(self.outcome(agent), True)
        return agent

    def events(self, agent: FridaAgent, count: int) -> list[Any]:
        """Collect events until there are `count` of them."""
        events: list[Any] = []

        def more() -> bool:
            events.extend(agent.poll_events())
            return len(events) >= count

        wait_for(more, f"{count} events (got {events})")
        return events

    def gone(self, agent: FridaAgent) -> AgentError:
        """Wait until reading the state fails, and return why."""
        def failed() -> AgentError | None:
            try:
                agent.read_state()
            except AgentError as error:
                return error
            return None

        return wait_for(failed, "the agent to notice that the game is gone")


class TestFridaAgent(FridaAgentTestBase):
    def test_both_scripts_are_packaged(self) -> None:
        agent_source = frida_agent.load_agent_source()
        self.assertIn("rpc.exports", agent_source)
        self.assertIn("exec(id, command)", agent_source)
        self.assertIn("def main()", frida_agent.load_helper_source())
        self.assertRaises(AgentScriptFailed, frida_agent._packaged, "no_such_file.js")  # an apworld that lost a file
        with mock.patch.object(frida_agent.pkgutil, "get_data", return_value=None):
            self.assertRaises(AgentScriptFailed, frida_agent.load_agent_source)
            agent = FridaAgent.start([PYTHON])
            self.assertRaises(AgentUnusable, agent.poll_attach)
            self.assertFalse(agent.alive)

    def test_attach_run_and_detach(self) -> None:
        fake = FakeFrida(self)
        agent = self.start(fake)
        self.assertTrue(agent.busy)
        self.assertEqual(agent.description, "BioShock Remastered")
        self.assertIs(self.outcome(agent), True)
        self.assertFalse(agent.busy)
        self.assertTrue(agent.alive)
        self.assertEqual(agent.description, "BioShock Remastered (Steam 1.0.127355)")
        self.assertEqual(agent.command, PYTHON)
        self.assertEqual(agent.frida_version, "17.0.0-fake")
        self.assertEqual(fake.calls("create_script")[0]["length"], len(frida_agent.load_agent_source()),
                         "the agent that ships with the apworld is what goes into the game")

        self.assertEqual(self.events(agent, 2), [
            AgentMessage(f"Using Frida 17.0.0-fake from {agent.python}."),
            AgentMessage("[bioshock-ap] build: Steam 1.0.127355"),
        ], "which Frida is in use, and what the agent prints, are shown to the player")
        self.assertEqual(agent.read_state(), GAME_IN_MEDICAL)

        agent.run_command(5, "GiveItem 1 ShockGame.HealthUpgrade")
        agent.run_action(6, "eve_drain")
        agent.kill_player()
        events = self.events(agent, 4)
        self.assertEqual(events[:3], [CommandStarted(5), CommandResult(5, True), ActionResult(6, False, "not yet")])
        self.assertIsInstance(events[3], AgentMessage, "a failed DeathLink kill is reported, not dropped")
        self.assertEqual([(call["id"], call["command"]) for call in fake.calls("exec")],
                         [(5, "GiveItem 1 ShockGame.HealthUpgrade")])
        self.assertEqual([(call["id"], call["name"]) for call in fake.calls("action")],
                         [(6, "eve_drain"), (0, "kill")])

        fake.order(state={"loading": True, "map": "3-Arcadia_fra"})
        wait_for(lambda: agent.read_state() == GameState(ready=False, map="3-arcadia", in_level=False),
                 "the game's news to arrive")

        fake.order(detach="process-terminated")
        self.assertEqual(str(self.gone(agent)), "the game closed")
        self.assertFalse(agent.alive)
        self.assertRaises(AgentError, agent.run_command, 7, "x")
        self.assertEqual(agent.poll_events(), [], "asking a gone agent for events is harmless")

    def test_what_the_game_prints_in_answer_is_shown(self) -> None:
        fake = FakeFrida(self)
        agent = self.attached(fake)
        self.events(agent, 2)
        agent.run_command(1, "GiveItem 1 ShockGame.HealthUpgrade")
        self.assertEqual(self.events(agent, 2), [CommandStarted(1), CommandResult(1, True)],
                         "nothing printed, nothing said")
        fake.order(prints=["Unable to find class ShockDesignerClasses.NoSuchThing", " ", "second", "third", "4th"])
        agent.run_command(2, "GiveItem 1 ShockDesignerClasses.NoSuchThing")
        self.assertEqual(self.events(agent, 3), [
            CommandStarted(2),
            CommandResult(2, True),
            AgentMessage('The game answered "GiveItem 1 ShockDesignerClasses.NoSuchThing" with: Unable to find class '
                         "ShockDesignerClasses.NoSuchThing | second | third (and 1 more line)"),
        ])

    def test_what_the_game_raises_during_a_command_is_shown(self) -> None:
        fake = FakeFrida(self, raises=['C++ exception: "Can\'t find file for package \'Sounds\'"'])
        agent = self.attached(fake)
        self.events(agent, 2)
        agent.run_command(1, "GiveItem 1 ShockDesignerClasses.ArmoredBodyTwo")
        self.assertEqual(self.events(agent, 3), [
            CommandStarted(1),
            AgentMessage('While running "GiveItem 1 ShockDesignerClasses.ArmoredBodyTwo" the game raised: '
                         'C++ exception: "Can\'t find file for package \'Sounds\'"'),
            CommandResult(1, True),
        ], "what the game raised on the way comes before how the command ended, and the command is not held up")

        fake.order(raises=["first", "second", "third", "fourth"], raised=30, prints=["and it printed this"])
        agent.run_command(2, "GiveItem 1 A")
        self.assertEqual(self.events(agent, 4), [
            CommandStarted(2),
            AgentMessage('While running "GiveItem 1 A" the game raised: first | second | third (and 27 more)'),
            CommandResult(2, True),
            AgentMessage('The game answered "GiveItem 1 A" with: and it printed this'),
        ])

        fake.order(raises=[], raised=0, prints=[], handled=False)
        agent.run_command(3, "GiveItem 1 B")
        self.assertEqual(self.events(agent, 2), [CommandStarted(3), CommandResult(3, False)],
                         "nothing raised, nothing said, and nothing left over from the command before")

    def test_a_class_the_game_does_not_have_is_not_delivered(self) -> None:
        # What the real game did on 2026-10-07: the command came back as handled, nothing arrived, and on the way
        # the game had raised this.
        fake = FakeFrida(self, raises=["C++ exception: \"Failed to find object 'Class ShockGame.FastTwitchTwo'\""])
        agent = self.attached(fake)
        self.events(agent, 2)
        agent.run_command(1, "GiveItem 1 ShockGame.FastTwitchTwo")
        self.assertEqual(self.events(agent, 2), [
            CommandStarted(1), ActionResult(1, False, "the game has no class ShockGame.FastTwitchTwo"),
        ], "not delivered, said once, and not to be asked for again right away")
        agent.run_command(2, "GiveItem 1 ShockDesignerClasses.ArmoredBodyTwo")
        self.assertEqual(self.events(agent, 3), [
            CommandStarted(2),
            AgentMessage('While running "GiveItem 1 ShockDesignerClasses.ArmoredBodyTwo" the game raised: '
                         "C++ exception: \"Failed to find object 'Class ShockGame.FastTwitchTwo'\""),
            CommandResult(2, True),
        ], "something else it did not find on the way does not undo the item it was asked for")
        fake.order(handled=False)
        agent.run_command(3, "GiveItem 1 ShockGame.FastTwitchTwo")
        self.assertEqual(self.events(agent, 3)[2], CommandResult(3, False),
                         "a command nobody took is refused in the usual way")

    def test_what_the_game_raises_with_every_item_of_the_designer_package_is_not_shown(self) -> None:
        # Seen in the game on 2026-10-07: asked for ShockDesignerClasses.ArmoredBodyTwo, it also looks for the class
        # in ShockGame, does not find it, and gives the item all the same, with its window.
        routine = "C++ exception: \"Failed to find object 'Class ShockGame.ArmoredBodyTwo'\""
        fake = FakeFrida(self, raises=[routine])
        agent = self.attached(fake)
        self.events(agent, 2)
        agent.run_command(1, "GiveItem 1 ShockDesignerClasses.ArmoredBodyTwo")
        self.assertEqual(self.events(agent, 2), [CommandStarted(1), CommandResult(1, True)],
                         "delivered, and not a word about the look into ShockGame")

        fake.order(raises=[routine, "something else", routine], raised=9)
        agent.run_command(2, "GiveItem 1 ShockDesignerClasses.ArmoredBodyTwo")
        self.assertEqual(self.events(agent, 3), [
            CommandStarted(2),
            AgentMessage('While running "GiveItem 1 ShockDesignerClasses.ArmoredBodyTwo" the game raised: '
                         "something else (and 6 more)"),
            CommandResult(2, True),
        ], "anything else it raises is still told, and the routine ones are not among the 'more'")

        # A class the designer package does not have: the look into ShockGame fails as always, and then so does
        # the look where the class should have been.
        fake.order(raises=["C++ exception: \"Failed to find object 'Class ShockGame.NoSuchThing'\"",
                           "C++ exception: \"Failed to find object 'Class ShockDesignerClasses.NoSuchThing'\""],
                   raised=2)
        agent.run_command(3, "GiveItem 1 ShockDesignerClasses.NoSuchThing")
        self.assertEqual(self.events(agent, 2), [
            CommandStarted(3), ActionResult(3, False, "the game has no class ShockDesignerClasses.NoSuchThing")])

        fake.order(dies_on="Crashy", raises=["C++ exception: \"Failed to find object 'Class ShockGame.Crashy'\""])
        agent.run_command(4, "GiveItem 1 ShockDesignerClasses.Crashy")
        self.gone(agent)
        self.assertEqual(agent.poll_events(), [CommandStarted(4)], "nor is it offered as the reason the game went")

    def test_a_game_that_goes_away_in_the_middle_of_a_command(self) -> None:
        fake = FakeFrida(self, dies_on="Crashy", raises=["C++ exception: the number 1"])
        agent = self.attached(fake)
        self.events(agent, 2)
        agent.run_command(1, "GiveItem 1 ShockGame.Fine")
        self.assertEqual(len(self.events(agent, 3)), 3)
        agent.run_command(2, "GiveItem 1 ShockGame.Crashy")
        self.assertEqual(str(self.gone(agent)), "the game closed")
        self.assertEqual(agent.poll_events(), [
            CommandStarted(2),
            AgentMessage('Before the game went away it raised, while running "GiveItem 1 ShockGame.Crashy": '
                         "C++ exception: the number 1"),
        ], "the command began, the game said why it gave up, and no answer ever came")

    def test_a_game_that_goes_away_without_a_word(self) -> None:
        fake = FakeFrida(self, dies_on="Crashy")
        agent = self.attached(fake)
        self.events(agent, 2)
        agent.run_command(2, "GiveItem 1 ShockGame.Crashy")
        self.gone(agent)
        self.assertEqual(agent.poll_events(), [CommandStarted(2)])

    def test_what_was_raised_is_told_when_the_helper_dies_too(self) -> None:
        fake = FakeFrida(self, raises=["something"], silent=False)
        agent = self.attached(fake)
        self.events(agent, 2)
        agent._raised[9] = frida_agent._Raised("GiveItem 1 X", ["something"])  # as if no result had come yet
        agent._helper._process.kill()  # type: ignore[union-attr]
        self.gone(agent)
        self.assertEqual(agent.poll_events(), [
            AgentMessage('Before the game went away it raised, while running "GiveItem 1 X": something')])

    def test_closing_takes_the_agent_out_of_the_game(self) -> None:
        fake = FakeFrida(self)
        agent = self.attached(fake)
        process = agent._helper._process  # type: ignore[union-attr]
        agent.close()
        agent.close()  # twice is fine
        wait_for(lambda: fake.names()[-2:] == ["detach", "shutdown"], "the helper to tidy up")
        wait_for(lambda: process.poll() is not None, "the helper to leave")
        self.assertEqual(process.returncode, 0, "it left by itself; nobody had to stop it")
        self.assertRaises(AgentError, agent.read_state)
        self.assertRaises(AgentError, agent.run_command, 1, "x")
        self.assertFalse(agent.poll_attach())

    def test_what_the_game_confirmed_before_it_went_away_is_still_delivered(self) -> None:
        fake = FakeFrida(self)
        agent = self.attached(fake)
        self.events(agent, 2)
        agent.run_command(5, "GiveItem 1 ShockGame.HealthUpgrade")
        wait_for(lambda: fake.calls("exec"), "the command to reach the game")
        fake.order(detach="process-terminated")
        self.gone(agent)
        self.assertEqual(agent.poll_events(), [CommandStarted(5), CommandResult(5, True)])

    def test_a_failing_call_means_the_game_is_gone(self) -> None:
        fake = FakeFrida(self)
        agent = self.attached(fake)
        fake.order(state_error="script is destroyed")
        self.assertIn("script is destroyed", str(self.gone(agent)))
        self.assertRaises(AgentError, agent.run_command, 1, "x")
        agent.close()
        wait_for(lambda: "detach" in fake.names(), "the script and its hook to be taken out of the game")

    def test_a_game_that_stops_answering_is_not_ready(self) -> None:
        fake = FakeFrida(self)
        with mock.patch.object(frida_agent, "STALE_SECONDS", 0.4):
            agent = self.attached(fake)
            self.assertEqual(agent.read_state(), GAME_IN_MEDICAL)
            fake.order(hang=True)
            wait_for(lambda: agent.read_state() == GameState(ready=False, map="1-medical"),
                     "the game to count as not ready, in the place it was last seen")
            self.assertTrue(agent.alive, "frozen is not gone")
            fake.order(hang=False)
            wait_for(lambda: agent.read_state() == GAME_IN_MEDICAL, "the game to be back")

    def test_a_helper_that_dies_is_noticed(self) -> None:
        fake = FakeFrida(self)
        agent = self.attached(fake)
        agent._helper._process.kill()  # type: ignore[union-attr]
        self.assertIn("the helper that reaches the game stopped", str(self.gone(agent)))
        self.assertFalse(agent.alive)

    def test_a_helper_whose_output_stays_open_after_it_died_is_noticed_too(self) -> None:
        """Something the helper started can keep its pipes open. Its being gone has to be enough."""
        fake = FakeFrida(self)
        agent = self.attached(fake)
        helper = agent._helper
        assert helper is not None

        class SaysItIsGone:
            def __init__(self, real: Any) -> None:
                self._real = real

            def poll(self) -> int:
                return 1

            def __getattr__(self, name: str) -> Any:
                return getattr(self._real, name)

        helper._process = SaysItIsGone(helper._process)  # type: ignore[assignment]
        with mock.patch.object(frida_agent, "QUIET_SECONDS", 0.2), mock.patch.object(frida_agent, "STALE_SECONDS", 99):
            fake.order(hang=True)  # it says nothing more, and its output never closes
            self.assertIn("the helper that reaches the game stopped", str(self.gone(agent)))

    def test_a_hung_helper_is_stopped_the_hard_way(self) -> None:
        fake = FakeFrida(self)
        agent = self.attached(fake)
        process = agent._helper._process  # type: ignore[union-attr]
        fake.order(hang=True)
        time.sleep(0.3)  # its main thread is now inside a call that does not return
        with mock.patch.object(frida_agent, "EXIT_SECONDS", 0.3):
            agent.close()
            wait_for(lambda: process.poll() is not None, "the helper to be stopped", timeout=4.0)
        self.assertNotEqual(process.returncode, 0)

    def test_a_program_that_does_not_read_its_input_is_stopped_all_the_same(self) -> None:
        """On Windows a pipe holds very little, so what is sent to a program that is not reading blocks the sender.

        Stopping that program must not wait for the sender. Here the same is brought about with a lot of text.
        """
        sleeper = (sys.executable, "-c", "import time; time.sleep(600)")
        with mock.patch.object(frida_agent, "load_helper_source", return_value="#" * 2_000_000), \
                mock.patch.object(frida_agent, "HELLO_SECONDS", 0.3), mock.patch.object(frida_agent, "EXIT_SECONDS", 0.3):
            agent = self.start(None, candidates=(sleeper,))
            process = agent._helper._process  # type: ignore[union-attr]
            self.assertIsInstance(self.outcome(agent), FridaUnavailable)
            wait_for(lambda: process.poll() is not None, "the program to be stopped", timeout=4.0)

    # -- attaching that does not work ---------------------------------------------------------------------------------

    def test_game_not_running_then_started(self) -> None:
        fake = FakeFrida(self, running=False)
        agent = self.start(fake)
        error = self.outcome(agent)
        self.assertIsInstance(error, GameNotRunning)
        self.assertEqual(str(error), "BioShock Remastered is not running")
        self.assertTrue(agent.alive, "the same helper can try again")
        self.assertFalse(agent.busy)
        self.assertFalse(agent.poll_attach(), "the problem is reported once")

        agent.try_again()
        self.assertTrue(agent.busy)
        agent.try_again()  # while a try is under way: nothing
        self.assertIsInstance(self.outcome(agent), GameNotRunning)
        self.assertEqual(len(fake.calls("attach")), 2)

        fake.order(running=True)
        agent.try_again()
        self.assertIs(self.outcome(agent), True)
        self.assertEqual(len(fake.calls("import")), 1, "one helper did all of that")
        self.assertEqual(self.events(agent, 2)[1], AgentMessage("[bioshock-ap] build: Steam 1.0.127355"))

    def test_two_copies_of_the_game_are_not_called_not_running(self) -> None:
        agent = self.start(FakeFrida(self, ambiguous=True))
        error = self.outcome(agent)
        self.assertNotIsInstance(error, (GameNotRunning, AgentUnusable))
        self.assertIn("More than one BioshockHD.exe is running", str(error))
        self.assertIn("pid: 4012", str(error))

    def test_a_game_run_as_administrator(self) -> None:
        fake = FakeFrida(self, denied=True)
        agent = self.start(fake)
        error = self.outcome(agent)
        self.assertIsInstance(error, AgentUnusable, "every further try may bring up a Windows prompt for administrator "
                                                    "rights, so there is none until the player asks")
        self.assertIn("administrator", str(error))
        self.assertIn("unable to access process", str(error))
        self.assertIn("/attach tries again", str(error))
        self.assertFalse(agent.alive)
        self.assertEqual(len(fake.calls("attach")), 1)

    def test_other_trouble_attaching(self) -> None:
        agent = self.start(FakeFrida(self, attach_error="process is not responding"))
        error = self.outcome(agent)
        self.assertNotIsInstance(error, AgentUnusable)
        self.assertEqual(str(error), "Could not attach to the game: InvalidOperationError: process is not responding")
        self.assertFalse(str(error).endswith("."), "the client adds what happens next")

    def test_script_that_fails_to_load(self) -> None:
        fake = FakeFrida(self, script_error="TypeError: not a function")
        agent = self.start(fake)
        error = self.outcome(agent)
        self.assertIsInstance(error, AgentScriptFailed)
        self.assertIsInstance(error, AgentUnusable)
        self.assertIn("TypeError: not a function (line 12)", str(error))
        self.assertNotIn("unable to find method", str(error), "the consequence is not the cause")
        self.assertFalse(agent.alive)
        wait_for(lambda: fake.names()[-2:] == ["detach", "shutdown"], "the broken script to be taken out again")

    def test_game_closing_while_attaching_is_worth_another_try(self) -> None:
        fake = FakeFrida(self, closes_while_loading=True)
        agent = self.start(fake)
        error = self.outcome(agent)
        self.assertNotIsInstance(error, AgentUnusable)
        self.assertEqual(str(error), "The game closed while the client was connecting to it")
        fake.order(closes_while_loading=False)
        agent.try_again()
        self.assertIs(self.outcome(agent), True)
        self.assertEqual([event for event in self.events(agent, 2) if "build" in event.text],
                         [AgentMessage("[bioshock-ap] build: Steam 1.0.127355")],
                         "what the first, failed, try said is not repeated")

    def test_helper_dying_while_attaching(self) -> None:
        fake = FakeFrida(self, hang=True)  # the agent's first answer never comes
        agent = self.start(fake)
        wait_for(lambda: not agent.poll_attach() and fake.calls("load"),
                 "the helper to be in the middle of attaching")
        agent._helper._process.kill()  # type: ignore[union-attr]
        error = self.outcome(agent)
        self.assertIsInstance(error, AgentUnusable, "a new helper every few seconds would only die the same way")
        self.assertIn("stopped while it was getting in", str(error))
        self.assertFalse(agent.alive)

    def test_helper_dying_between_tries(self) -> None:
        fake = FakeFrida(self, running=False)
        agent = self.start(fake)
        self.assertIsInstance(self.outcome(agent), GameNotRunning)
        agent._helper._process.kill()  # type: ignore[union-attr]
        error = self.outcome(agent)
        self.assertNotIsInstance(error, AgentUnusable)
        self.assertIn("The helper that reaches the game stopped", str(error))
        self.assertFalse(agent.alive, "the next try needs a new helper")
        agent.try_again()  # and this one can do nothing more
        self.assertFalse(agent.poll_attach())

    def test_helper_found_dead_at_the_next_try(self) -> None:
        """Nobody looked in between. It died while waiting, and that is still how it has to be reported."""
        fake = FakeFrida(self, running=False)
        agent = self.start(fake)
        self.assertIsInstance(self.outcome(agent), GameNotRunning)
        helper = agent._helper
        assert helper is not None
        helper._process.kill()
        wait_for(lambda: None in helper._received, "the helper's output to close")
        agent.try_again()
        error = self.outcome(agent)
        self.assertNotIsInstance(error, AgentUnusable, "worth a new helper: it did not die getting into the game")
        self.assertIn("The helper that reaches the game stopped", str(error))

    def test_attaching_that_takes_forever(self) -> None:
        fake = FakeFrida(self, hang=True)
        with mock.patch.object(frida_agent, "ATTACH_SECONDS", 0.5), mock.patch.object(frida_agent, "EXIT_SECONDS", 0.2):
            agent = self.start(fake)
            process = agent._helper._process  # type: ignore[union-attr]
            error = self.outcome(agent)
            self.assertIsInstance(error, AgentUnusable)
            self.assertIn("The game did not let the client in", str(error))
            wait_for(lambda: process.poll() is not None, "the stuck helper to be stopped", timeout=4.0)

    # -- finding a Python that has Frida ------------------------------------------------------------------------------

    def test_no_python_at_all(self) -> None:
        agent = self.start(None, candidates=())
        error = self.outcome(agent)
        self.assertIsInstance(error, FridaUnavailable)
        self.assertIn("pip install frida", str(error))
        self.assertIn("No Python was found on this PC.", str(error))
        self.assertFalse(agent.alive)
        self.assertFalse(agent.busy)

    def test_pythons_are_tried_until_one_has_frida(self) -> None:
        fake = FakeFrida(self)
        missing = (os.path.join(fake.folder, "no-such-python"),)
        broken = (sys.executable, "-c", "import sys; print('Python was not found', file=sys.stderr); sys.exit(9)")
        agent = self.start(fake, candidates=(missing, broken, PYTHON_WITHOUT_FRIDA, PYTHON, missing))
        self.assertIs(self.outcome(agent), True)
        self.assertEqual(agent.command, PYTHON, "the one that worked is remembered")
        self.assertEqual(len(fake.calls("import")), 1)
        self.assertEqual(agent.read_state(), GAME_IN_MEDICAL)

    def test_no_python_with_frida(self) -> None:
        fake = FakeFrida(self, missing=True)
        missing = (os.path.join(fake.folder, "no-such-python"),)
        broken = (sys.executable, "-c", "import sys; print('Python was not found', file=sys.stderr); sys.exit(9)")
        agent = self.start(fake, candidates=(missing, broken, PYTHON_WITHOUT_FRIDA, PYTHON))
        error = self.outcome(agent)
        self.assertIsInstance(error, FridaUnavailable)
        text = str(error)
        self.assertIn("pip install frida", text)
        self.assertIn("/python", text)
        self.assertIn(f"{missing[0]} could not be started", text)
        self.assertIn("stopped right away: Python was not found", text)
        self.assertEqual(text.count("has no Frida (ModuleNotFoundError: No module named 'frida')"), 1, text)
        self.assertEqual(text.count("has no Frida (ImportError: No module named 'frida')"), 1, text)
        self.assertNotIn("No Python was found", text)
        self.assertFalse(agent.alive)

    def test_a_python_that_never_answers(self) -> None:
        sleeper = (sys.executable, "-c", "import time; time.sleep(600)")
        fake = FakeFrida(self)
        with mock.patch.object(frida_agent, "HELLO_SECONDS", 0.4), mock.patch.object(frida_agent, "EXIT_SECONDS", 0.2):
            agent = self.start(fake, candidates=(sleeper, PYTHON))
            first = agent._helper._process  # type: ignore[union-attr]
            self.assertIs(self.outcome(agent), True, "the next Python is tried")
            wait_for(lambda: first.poll() is not None, "the silent one to be stopped", timeout=4.0)
            agent = self.start(fake, candidates=(sleeper,))
            error = self.outcome(agent)
            self.assertIsInstance(error, FridaUnavailable)
            self.assertIn("did not answer", str(error))


class TestPythonCandidates(unittest.TestCase):
    def candidates(self, path: tuple[str, ...], files: tuple[str, ...] = (), frozen: bool = False,
                   variable: str | None = None, preferred: Any = (), windows: bool = False) -> list[Any]:
        """What is found on a PC whose PATH lists these folders and which has these files."""
        environment = {"PATH": os.pathsep.join(path)}
        if variable is not None:
            environment[frida_agent.PYTHON_VARIABLE] = variable
        with mock.patch.object(frida_agent.os.path, "isfile", lambda name: name in files), \
                mock.patch.object(frida_agent.os, "access", lambda name, mode: True), \
                mock.patch.object(frida_agent.sys, "frozen", frozen, create=True), \
                mock.patch.object(frida_agent.sys, "executable", "/ap/python"), \
                mock.patch.object(frida_agent.os, "name", "nt" if windows else "posix"), \
                mock.patch.dict(frida_agent.os.environ, environment, clear=True):
            return python_candidates(preferred)

    def test_order(self) -> None:
        path = ("/tools/Scripts", "/windows", "/path")
        files = ("/tools/Scripts/frida", "/tools/python.exe", "/tools/Scripts/python3", "/windows/py", "/path/python",
                 "/path/python3")
        self.assertEqual(
            self.candidates(path, files, variable="/named/python", preferred=[("/last/python",), "/chosen/python"]),
            [("/last/python",), ("/chosen/python",), ("/named/python",), ("/ap/python",), ("/tools/python.exe",),
             ("/tools/Scripts/python3",), ("/windows/py", "-3"), ("/path/python",)])

    def test_the_first_folder_on_the_path_that_has_it_wins(self) -> None:
        files = ("/b/python", "/c/python", "/c/python3")
        self.assertEqual(self.candidates(("/a", "/b", "/c"), files, frozen=True), [("/b/python",), ("/c/python3",)])

    def test_the_packaged_archipelago_is_not_a_python(self) -> None:
        self.assertEqual(self.candidates(("/path",), ("/path/python",), frozen=True), [("/path/python",)])
        self.assertEqual(self.candidates(("/path",), ("/path/python",)), [("/ap/python",), ("/path/python",)])

    def test_nothing_found(self) -> None:
        self.assertEqual(self.candidates((), frozen=True), [])
        self.assertEqual(self.candidates(("/empty",), frozen=True, preferred=[None, "", ()]), [])

    def test_the_same_python_is_listed_once(self) -> None:
        files = ("/ap/Scripts/frida", "/ap/python", "/ap/../ap/python3")
        self.assertEqual(
            self.candidates(("/ap/Scripts", "/ap", "/ap/../ap"), files, preferred=["/ap/python", ("/ap/python",)]),
            [("/ap/python",), ("/ap/../ap/python3",)])
        self.assertEqual(self.candidates(("/ap/../ap",), ("/ap/../ap/python",)), [("/ap/python",)],
                         "the same file by another spelling")

    def test_a_virtual_environment_is_not_mistaken_for_the_python_it_was_made_from(self) -> None:
        with mock.patch.object(frida_agent.os.path, "realpath", lambda path: "/usr/bin/python3"):
            self.assertEqual(self.candidates(("/venv/bin",), ("/venv/bin/python",)),
                             [("/ap/python",), ("/venv/bin/python",)])

    def test_the_current_folder_is_not_searched(self) -> None:
        """Windows would look there first. A python.exe lying next to Archipelago is not the player's Python."""
        files = ("./python", "python", os.path.join(os.curdir, "python"), "/path/python3")
        self.assertEqual(self.candidates((".", "", "/path"), files, frozen=True), [("/path/python3",)])

    def test_a_file_that_cannot_be_run_is_passed_over(self) -> None:
        files = ("/a/python", "/b/python")
        with mock.patch.object(frida_agent.os.path, "isfile", lambda name: name in files), \
                mock.patch.object(frida_agent.os, "access", lambda name, mode: name != "/a/python"), \
                mock.patch.dict(frida_agent.os.environ, {"PATH": os.pathsep.join(("/a", "/b"))}, clear=True):
            self.assertEqual(frida_agent._program("python"), "/b/python")
            self.assertIsNone(frida_agent._program("py"))

    def test_on_windows_only_real_programs_count(self) -> None:
        path = ("/Windows", "/pyenv/shims", "/Program Files/Python312")
        files = ("/Windows/py.exe", "/pyenv/shims/python.bat", "/pyenv/shims/python", "/pyenv/shims/python3.cmd",
                 "/Program Files/Python312/python.exe")
        self.assertEqual(self.candidates(path, files, frozen=True, windows=True),
                         [("/Windows/py.exe", "-3"), ("/Program Files/Python312/python.exe",)],
                         "a .bat wrapper's quoting rules would mangle what the helper is started with")
        self.assertEqual(self.candidates(path, files, frozen=True), [("/pyenv/shims/python",)],
                         "elsewhere a program has no ending")

    def test_folders_in_quotes_on_the_path(self) -> None:
        self.assertEqual(self.candidates(('"/Program Files/Python"',), ("/Program Files/Python/python",), frozen=True),
                         [("/Program Files/Python/python",)])


# --------------------------------------------------------------------------------------------------------------------
# What the agent says, in the client's terms
# --------------------------------------------------------------------------------------------------------------------

class TestTranslation(unittest.TestCase):
    def test_state_translation(self) -> None:
        base = {"execAvailable": True, "hookInstalled": True, "levelValue": 9317, "loading": False,
                "inGame": True, "fontainePhase": 3}
        self.assertEqual(state_from_agent(base), GameState(ready=True, level_value=9317, fontaine_phase=3))
        self.assertEqual(state_from_agent(dict(base, loading=True)),
                         GameState(ready=False, level_value=9317, in_level=False, fontaine_phase=3))
        self.assertFalse(state_from_agent(dict(base, loading=True)).ready)
        self.assertFalse(state_from_agent(dict(base, inGame=False)).ready)
        self.assertFalse(state_from_agent(dict(base, execAvailable=False)).ready)
        self.assertFalse(state_from_agent(dict(base, hookInstalled=False)).ready)
        self.assertFalse(state_from_agent(dict(base, engineReady=False)).ready, "the game is still starting up")
        self.assertTrue(state_from_agent(dict(base, engineReady=True)).ready)
        self.assertTrue(state_from_agent(dict(base, loading=None, inGame=None)).ready,
                        "unknown is not a reason to wait forever")
        self.assertIsNone(state_from_agent(dict(base, levelValue=0)).level_value)
        self.assertEqual(state_from_agent({}), GameState())

    def test_state_translation_with_the_newer_agent(self) -> None:
        """What agent 2026-10-06.3 adds: the map's name, and whether the player can be given anything right now."""
        base = {"agent": "2026-10-06.3", "execAvailable": True, "hookInstalled": True, "engineReady": True,
                "levelValue": 1030, "loading": False, "inGame": True, "fontainePhase": 117,
                "playerPath": "ready", "playerReady": True, "map": "1-Medical.bsm",
                "levels": ["1-medical: 9224 actors, room for 11027", "Entry.bsm: 16 actors, room for 47"]}
        self.assertEqual(state_from_agent(base), GameState(ready=True, map="1-medical", fontaine_phase=117),
                         "the level number is not passed on by an agent that names the map")
        # In a menu or a pause no map is named. The number must not stand in then: after loading a save it can be
        # anything, such as 8187, which is Arcadia's value in a game played straight through.
        self.assertEqual(state_from_agent(dict(base, map=None, playerReady=False, levelValue=8187)),
                         GameState(ready=False, fontaine_phase=117))
        # A loading screen and the main menu are known for what they are; a pause is not.
        self.assertIs(state_from_agent(dict(base, map=None, playerReady=False, loading=True)).in_level, False)
        self.assertIs(state_from_agent(dict(base, map=None, playerReady=False, inGame=False)).in_level, False)
        self.assertIsNone(state_from_agent(dict(base, map=None, playerReady=False)).in_level)
        self.assertIsNone(state_from_agent(base).in_level)
        self.assertFalse(state_from_agent(dict(base, playerReady=False)).ready,
                         "no controller or no body: a command would go to the engine alone and come back refused")
        self.assertFalse(state_from_agent(dict(base, playerReady=False, playerPath="unknown")).ready)
        self.assertFalse(state_from_agent(dict(base, map="museum")).ready, "not in a Challenge Room or the museum")
        self.assertFalse(state_from_agent(dict(base, map="0-Lighthouse")).ready, "not before Rapture")
        self.assertFalse(state_from_agent(dict(base, map="Entry.bsm")).ready)
        self.assertTrue(state_from_agent(dict(base, map="7-BossFight")).ready)
        self.assertTrue(state_from_agent(dict(base, map="9-newlevel")).ready, "an unknown name is not held against it")
        self.assertIsNone(state_from_agent(dict(base, map=None)).map)

    def test_message_translation(self) -> None:
        def sent(payload: dict[str, Any]) -> Any:
            return event_from_message({"type": "send", "payload": payload})

        self.assertEqual(sent({"type": "exec_result", "id": 3, "handled": False}), CommandResult(3, False))
        self.assertEqual(sent({"type": "exec_result", "id": 3, "handled": False, "fatal": True, "reason": "boom"}),
                         ActionResult(3, False, "boom"), "a command the agent failed on is not tried again")
        self.assertEqual(sent({"type": "exec_result", "id": 3, "handled": False, "fatal": True}),
                         ActionResult(3, False, "the game agent failed"))
        self.assertIsNone(sent({"type": "exec_result", "id": None, "handled": True}), "typed at the Frida prompt")
        self.assertEqual(sent({"type": "exec_started", "id": 3, "command": "x"}), CommandStarted(3))
        self.assertIsNone(sent({"type": "exec_started", "id": None, "command": "x"}), "typed at the Frida prompt")
        self.assertIsNone(sent({"type": "exec_raised", "id": 3, "command": "x", "what": "y"}),
                          "kept for the line that sums the command up; not an event of its own")
        self.assertEqual(sent({"type": "action_result", "id": 4, "ok": True}), ActionResult(4, True))
        self.assertIsInstance(sent({"type": "exec_disabled", "reason": "different build"}), AgentMessage)
        self.assertIsNone(sent({"type": "something new"}))
        self.assertIsNone(event_from_message({"type": "send", "payload": "text"}))
        self.assertEqual(event_from_message({"type": "error", "description": "ReferenceError", "lineNumber": 7}),
                         AgentMessage("Agent script error: ReferenceError (line 7)"))
        self.assertEqual(event_from_message({"type": "error", "description": "ReferenceError"}),
                         AgentMessage("Agent script error: ReferenceError"))

    def test_answer_translation(self) -> None:
        def answer(**payload: Any) -> Any:
            return answer_from_message({"type": "send", "payload": dict({"type": "exec_result", "id": 3,
                                                                         "command": "get A B"}, **payload)})

        self.assertIsNone(answer())
        self.assertIsNone(answer(output=[]))
        self.assertIsNone(answer(output=["", "  "]))
        self.assertIsNone(answer(output=["typed at the Frida prompt"], id=None))
        self.assertIsNone(answer(output=["not a command result"], type="action_result"))
        self.assertIsNone(answer_from_message({"type": "send", "payload": "text"}))
        self.assertIsNone(answer_from_message({"type": "error", "description": "x"}))
        self.assertEqual(answer(output=["200.000000"]), AgentMessage('The game answered "get A B" with: 200.000000'))
        self.assertEqual(answer(output=["a", "b", "c"], outputLines=3).text, 'The game answered "get A B" with: a | b | c')
        self.assertEqual(answer(output=["a", "b", "c", "d"], outputLines=250).text,
                         'The game answered "get A B" with: a | b | c (and 247 more lines)')
        self.assertEqual(answer(output=["a", "b", "c", "d"]).text,
                         'The game answered "get A B" with: a | b | c (and 1 more line)')
        self.assertEqual(answer(output=["a", "", "b", " ", "c"], outputLines=5).text,
                         'The game answered "get A B" with: a | b | c', "empty lines are not counted as more")
        self.assertEqual(answer(output=["", "a", "", "b", "c", "d", ""], outputLines=30).text,
                         'The game answered "get A B" with: a | b | c (and 24 more lines)')

    def test_raised_translation(self) -> None:
        def raised(**payload: Any) -> Any:
            return raised_from_message({"type": "send", "payload": dict({"type": "exec_raised", "id": 3,
                                                                        "command": "GiveItem 1 A", "what": "x"},
                                                                       **payload)})

        self.assertEqual(raised(), (3, "GiveItem 1 A", "x"))
        self.assertEqual(raised(what="  spaced  "), (3, "GiveItem 1 A", "spaced"))
        self.assertIsNone(raised(id=None), "typed at the Frida prompt")
        self.assertIsNone(raised(what=""))
        self.assertIsNone(raised(what="   "))
        self.assertIsNone(raised(type="exec_result"))
        self.assertIsNone(raised_from_message({"type": "send", "payload": "text"}))
        self.assertIsNone(raised_from_message({"type": "error", "description": "x"}))
        self.assertEqual(raised_from_message({"type": "send", "payload": {"type": "exec_raised", "id": 4,
                                                                          "what": "y"}}), (4, "?", "y"))

        said = "C++ exception: \"Failed to find object 'Class ShockGame.NoSuchThing'\""
        self.assertEqual(missing_class("GiveItem 1 ShockGame.NoSuchThing", [said]), "ShockGame.NoSuchThing")
        self.assertEqual(missing_class("giveitem 1 shockgame.nosuchthing", ["first", said]), "ShockGame.NoSuchThing",
                         "the game does not tell capitals from small letters, so neither does this")
        self.assertEqual(missing_class("GiveWeapon ShockGame.NoSuchThing", [said]), "ShockGame.NoSuchThing")
        self.assertIsNone(missing_class("GiveItem 1 ShockGame.Other", [said]), "another class than the one asked for")
        self.assertIsNone(missing_class("GiveItem 1 ShockGame.NoSuchThingElse", [said]), "not a part of a name")
        self.assertIsNone(missing_class("GiveItem 1 ShockGame.NoSuchThing", []))
        self.assertIsNone(missing_class("GiveItem 1 ShockGame.NoSuchThing",
                                        ["C++ exception: \"Failed to find object 'Sound ShockGame.NoSuchThing'\""]),
                          "a sound of that name is not the class")
        self.assertIsNone(missing_class("GiveItem 1 ShockGame.NoSuchThing", ["debug text: \"all is well\""]))

        def routine(command: str, class_name: str, kind: str = "Class") -> bool:
            return routine_lookup(command, f"C++ exception: \"Failed to find object '{kind} {class_name}'\"")

        self.assertTrue(routine("GiveItem 1 ShockDesignerClasses.ArmoredBodyTwo", "ShockGame.ArmoredBodyTwo"))
        self.assertTrue(routine("giveitem 1 shockdesignerclasses.armoredbodytwo", "ShockGame.ArmoredBodyTwo"))
        self.assertFalse(routine("GiveItem 1 ShockGame.ArmoredBodyTwo", "ShockGame.ArmoredBodyTwo"),
                         "asked for in ShockGame and not found there: that is the item missing, not routine")
        self.assertFalse(routine("GiveItem 1 ShockDesignerClasses.ArmoredBodyTwo",
                                 "ShockDesignerClasses.ArmoredBodyTwo"), "not found where it was asked for")
        self.assertFalse(routine("GiveItem 1 ShockDesignerClasses.ArmoredBodyTwo", "ShockGame.SomethingElse"))
        self.assertFalse(routine("GiveItem 1 ShockDesignerClasses.ArmoredBodyTwo", "ShockGame.BodyTwo"),
                         "the end of a name is not the name")
        self.assertFalse(routine("GiveItem 1 ShockDesignerClasses.ArmoredBodyTwo", "ShockGame.ArmoredBodyTwo",
                                 kind="Sound"))
        self.assertFalse(routine_lookup("GiveItem 1 ShockDesignerClasses.ArmoredBodyTwo", "debug text: \"hello\""))
        self.assertFalse(routine("StartSecurityAlarm", "ShockGame.ArmoredBodyTwo"))

        self.assertEqual(raised_summary("get A", ["one"]).text, 'While running "get A" the game raised: one')
        self.assertEqual(raised_summary("get A", ["one", "two", "three"], 3).text,
                         'While running "get A" the game raised: one | two | three')
        self.assertEqual(raised_summary("get A", ["one", "two", "three", "four"]).text,
                         'While running "get A" the game raised: one | two | three (and 1 more)')
        self.assertEqual(raised_summary("get A", ["one", "two"], 40).text,
                         'While running "get A" the game raised: one | two (and 38 more)')
        self.assertEqual(raised_summary("get A", ["one", "two"], 1).text,
                         'While running "get A" the game raised: one | two', "a count that is too low is not believed")
        self.assertEqual(raised_summary("get A", ["one", "two", "three", "four"], 2).text,
                         'While running "get A" the game raised: one | two | three (and 1 more)')
        self.assertEqual(raised_summary("get A", ["one"], gone=True).text,
                         'Before the game went away it raised, while running "get A": one')

    def test_why_the_game_went_away_is_said_in_plain_words(self) -> None:
        for reason in ("process-terminated", "process-replaced", "connection-terminated", "device-lost",
                       "application-requested"):
            self.assertNotIn("-", frida_agent.DETACH_REASONS[reason])

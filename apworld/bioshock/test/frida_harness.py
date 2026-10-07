"""
What the tests of the Frida side share: a stand-in Frida installation for the helper process to import, a way to
talk to a helper directly, and a way to wait for things that happen in another process.
"""
from __future__ import annotations

import json
import os
import pkgutil
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections.abc import Callable
from typing import Any, TypeVar

from ..client import frida_agent

T = TypeVar("T")

PYTHON = (sys.executable,)  # the Python the tests run in doubles as "the player's Python"
# A Python that certainly has no Frida: it ignores the folder the stand-in is in, and has no site-packages either.
PYTHON_WITHOUT_FRIDA = (sys.executable, "-I", "-S")


def wait_for(condition: Callable[[], T], what: str, timeout: float = 20.0) -> T:
    """Wait until `condition()` returns something true, and return that."""
    deadline = time.monotonic() + timeout
    while True:
        value = condition()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.01)


class FakeFrida:
    """A folder holding the stand-in `frida` package, the orders it follows and the record of what it was asked."""

    def __init__(self, test: unittest.TestCase, **orders: Any) -> None:
        self.folder = tempfile.mkdtemp(prefix="bioshock-fake-frida-")
        test.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        package = os.path.join(self.folder, "frida")
        os.mkdir(package)
        source = pkgutil.get_data(__package__, "fake_frida.py")
        assert source, "fake_frida.py belongs next to the tests"
        with open(os.path.join(package, "__init__.py"), "wb") as stream:
            stream.write(source)
        self.orders: dict[str, Any] = {}
        self.order(**orders)

    def order(self, **orders: Any) -> None:
        """Change how "Frida" and the game behave from now on."""
        self.orders.update(orders)
        temporary = os.path.join(self.folder, "orders.tmp")
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(self.orders, stream)
        os.replace(temporary, os.path.join(self.folder, "orders.json"))  # in one step: never half a file

    @property
    def env(self) -> dict[str, str]:
        """The environment a helper needs to find this stand-in instead of a real Frida."""
        env = {name: value for name, value in os.environ.items() if name not in ("PYTHONPATH", "PYTHONHOME")}
        env.update(PYTHONPATH=self.folder, FAKE_FRIDA_DIR=self.folder, PYTHONDONTWRITEBYTECODE="1")
        return env

    def calls(self, name: str | None = None) -> list[dict[str, Any]]:
        """What has been asked of "Frida" so far, optionally only the calls of one kind."""
        records: list[dict[str, Any]] = []
        try:
            with open(os.path.join(self.folder, "calls.jsonl"), encoding="utf-8") as stream:
                for line in stream:
                    try:
                        records.append(json.loads(line))
                    except ValueError:
                        break  # a line that is still being written
        except FileNotFoundError:
            pass
        return [record for record in records if name is None or record["call"] == name]

    def names(self) -> list[str]:
        return [record["call"] for record in self.calls()]


class RawHelper:
    """A helper process started the way the client starts one, for tests that speak its protocol themselves."""

    def __init__(self, test: unittest.TestCase, fake: FakeFrida, source: str | None = None,
                 command: tuple[str, ...] = PYTHON) -> None:
        self.process = subprocess.Popen(
            [*command, "-c", frida_agent.BOOTSTRAP], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=fake.env)
        test.addCleanup(self._clean_up)
        self.lines: list[str] = []  # every line of its output, conversation or not
        self.errors: list[str] = []
        self._threads = [threading.Thread(target=self._read, args=(self.process.stdout, self.lines), daemon=True),
                         threading.Thread(target=self._read, args=(self.process.stderr, self.errors), daemon=True)]
        for thread in self._threads:
            thread.start()
        self.write(json.dumps(frida_agent.load_helper_source() if source is None else source))

    @staticmethod
    def _read(stream: Any, sink: list[str]) -> None:
        for raw in stream:
            sink.append(raw.decode("utf-8", "replace").rstrip("\n"))
        stream.close()

    def write(self, line: str) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(line.encode("utf-8") + b"\n")
        self.process.stdin.flush()

    def send(self, **operation: Any) -> None:
        self.write(json.dumps(operation))

    def events(self, name: str | None = None) -> list[dict[str, Any]]:
        events = [json.loads(line) for line in list(self.lines)]
        return [event for event in events if name is None or event["event"] == name]

    def expect(self, name: str, count: int = 1) -> dict[str, Any]:
        """Wait for the `count`-th event of this kind and return it."""
        return wait_for(lambda: len(self.events(name)) >= count and self.events(name)[count - 1],
                        f"the helper to say '{name}' (it said {[e['event'] for e in self.events()][-6:]}, "
                        f"and wrote {self.errors[-3:]})")

    def close_input(self) -> None:
        assert self.process.stdin is not None
        self.process.stdin.close()

    def wait(self, timeout: float = 20.0) -> int:
        code = self.process.wait(timeout)
        for thread in self._threads:
            thread.join(5)
        return code

    def _clean_up(self) -> None:
        try:
            self.close_input()
        except OSError:
            pass
        if self.process.poll() is None:
            try:
                self.process.wait(5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        for thread in self._threads:
            thread.join(5)

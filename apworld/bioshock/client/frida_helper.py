"""
The part of the client that runs in the player's own Python. It is never imported by Archipelago.

The Archipelago you install is a packaged program with its own Python inside, and Frida cannot be added to that
one. So the client starts this file in a Python that does have Frida (see frida_agent.py for how it finds one) and
talks to it over the process's standard input and output, one JSON object per line. That also keeps Frida's
threads, and anything that goes wrong inside them, out of the client.

Only the standard library and `frida` may be used here, and nothing newer than Python 3.7 understands: this has to
run in whatever Python the player happens to have.

    client -> helper                                    helper -> client
    {"op": "setup", "process": ..., "source": ...}      {"event": "hello", "frida": ..., "python": ...}
    {"op": "attach"}                                    {"event": "failed", "kind": ..., "text": ...}
    {"op": "exec", "id": 7, "command": "..."}           {"event": "attached", "state": {...}}
    {"op": "action", "id": 8, "name": "..."}            {"event": "state", "state": {...}}      several a second
                                                        {"event": "message", "message": {...}}  what the agent sent
                                                        {"event": "log", "level": ..., "text": ...}
                                                        {"event": "detached", "reason": ...}

`failed` kinds: frida_missing (this Python has no Frida; the helper ends), not_running, ambiguous, denied, attach,
closed (all worth another `attach` later) and script (the agent did not start; trying again would not help).

The helper ends when its input closes, and only then. That is how the client stops it, and it is also what happens
by itself when the client dies, so a helper never stays behind. It takes the agent out of the game before it goes.
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import traceback

HELPER_VERSION = 1
STATE_SECONDS = 0.1  # how often the game's state is read and passed on
REASON_SECONDS = 0.3  # how long a failed call waits for Frida's own word on what happened
LINGER_SECONDS = 5.0  # how long the helper may take to leave once its input has closed


class Channel:
    """Writes events for the client. Used from the main thread and from Frida's own thread."""

    def __init__(self, stream):
        self._stream = stream
        self._lock = threading.Lock()

    def send(self, event, **fields):
        fields["event"] = event
        line = (json.dumps(fields, default=repr) + "\n").encode("utf-8")
        with self._lock:
            try:
                self._stream.write(line)
                self._stream.flush()
            except (OSError, ValueError):
                pass  # the client is gone; the closed input ends the helper


class Attachment:
    """One Frida session with the agent in it."""

    def __init__(self, session):
        self.session = session
        self.exports = None
        self.detached = ""  # why Frida says the session ended, if it ended by itself
        self.reported = False  # the client has been told that it ended, or the helper is ending it itself


class Game:
    def __init__(self, frida, channel):
        self._frida = frida
        self._channel = channel
        self._not_found = getattr(frida, "ProcessNotFoundError", ())
        self._denied = getattr(frida, "PermissionDeniedError", ())
        self._process = ""
        self._source = ""
        self._current = None  # the Attachment in use
        self._lock = threading.Lock()  # guards _current and what the client is told about it

    def setup(self, process, source):
        self._process = process
        self._source = source

    # -- attaching ----------------------------------------------------------------------------------------------------

    def attach(self):
        if self._current is not None:
            return
        try:
            session = self._frida.attach(self._process)
        except self._not_found as error:
            # Frida uses the same error for "no such process" and "more than one".
            kind = "ambiguous" if "ambiguous" in str(error).lower() else "not_running"
            self._channel.send("failed", kind=kind, text=str(error))
            return
        except self._denied as error:
            self._channel.send("failed", kind="denied", text=str(error))
            return
        except Exception as error:  # the wrong kind of process, a game that does not respond, ...
            self._channel.send("failed", kind="attach", text=describe(error))
            return

        attachment = Attachment(session)
        try:
            # First of all: from here on the session may end at any moment, and that has to be heard.
            session.on("detached", lambda reason="", *rest: self._on_detached(attachment, reason))
            script = session.create_script(self._source)
            script.on("message", lambda message, data=None: self._channel.send("message", message=message))
            if hasattr(script, "set_log_handler"):  # otherwise Frida prints what the agent logs
                script.set_log_handler(lambda level, text: self._channel.send("log", level=level, text=text))
            script.load()
            attachment.exports = getattr(script, "exports_sync", None) or script.exports
            # A script that throws while it loads still "loads"; only its first call fails.
            state = attachment.exports.state()
        except Exception as error:
            # Was it the script, or did the game close while Frida was getting in? Frida's word on that has to be
            # read before the session is let go of, because letting go ends the session too.
            self._wait_for_reason(attachment)
            with self._lock:
                closed = attachment.detached
                if not closed and getattr(session, "is_detached", False) is True:
                    closed = "the game closed"  # it had ended before there was anyone to hear it
                attachment.reported = True
            self._release(attachment)
            if closed:
                self._channel.send("failed", kind="closed", text=closed)
            else:
                self._channel.send("failed", kind="script", text=describe(error))
            return

        with self._lock:
            if not attachment.detached:
                self._current = attachment
                self._channel.send("attached", state=state)
                return
            attachment.reported = True
        self._release(attachment)
        self._channel.send("failed", kind="closed", text=attachment.detached)

    def _on_detached(self, attachment, reason):
        """Called on Frida's thread when the session ends: the game closed, usually."""
        with self._lock:
            if attachment.reported:
                return  # the helper's own doing (detaching fires this too), or old news
            attachment.detached = str(reason) or "the game closed"
            if self._current is attachment:
                self._current = None
                attachment.reported = True
                self._channel.send("detached", reason=attachment.detached)

    # -- while attached -----------------------------------------------------------------------------------------------

    def push_state(self):
        attachment = self._current
        if attachment is None:
            return
        try:
            state = attachment.exports.state()
        except Exception as error:
            self._lost(attachment, describe(error))
            return
        self._channel.send("state", state=state)

    def call(self, name, *arguments):
        attachment = self._current
        if attachment is None:
            return  # nothing to call; the client has been told, or is about to be
        try:
            getattr(attachment.exports, name)(*arguments)
        except Exception as error:
            self._lost(attachment, describe(error))

    def _lost(self, attachment, reason):
        """A call into the game failed. Whatever is left of the agent is taken out again."""
        self._wait_for_reason(attachment)
        with self._lock:
            if self._current is attachment:
                self._current = None
            if not attachment.reported:
                attachment.reported = True
                self._channel.send("detached", reason=attachment.detached or reason)
        self._release(attachment)

    @staticmethod
    def _wait_for_reason(attachment):
        """When the game closes, the call that fails and Frida's word on why arrive in either order. The why is
        what the player should be told; the failed call is only its consequence. So give the why a moment."""
        deadline = time.monotonic() + REASON_SECONDS
        while not attachment.detached and time.monotonic() < deadline:
            time.sleep(0.01)

    def detach(self):
        with self._lock:
            attachment, self._current = self._current, None
            if attachment is not None:
                attachment.reported = True  # the client asked for this
        if attachment is not None:
            self._release(attachment)

    @staticmethod
    def _release(attachment):
        try:  # also after a failure: the script may still be in the game, with its hook installed
            attachment.session.detach()
        except Exception:
            pass


def describe(error):
    return "%s: %s" % (type(error).__name__, error)


def read_operations(stream, operations):
    try:
        for raw in stream:
            try:
                operation = json.loads(raw.decode("utf-8"))
            except ValueError:
                continue
            if isinstance(operation, dict):
                operations.put(operation)
    except (OSError, ValueError):
        pass
    operations.put(None)  # the input has closed: time to go
    # The main thread may be stuck in a call into a game that has frozen, and would then never see that.
    # Nobody is listening any more, so do not stay behind.
    time.sleep(LINGER_SECONDS)
    os._exit(0)


def main():
    channel = Channel(sys.stdout.buffer)
    # Frida prints what a script logs when it is not told otherwise, and a stray line would break the protocol.
    sys.stdout = sys.stderr
    try:
        import frida
    except Exception as error:  # not installed, or installed for another Python
        channel.send("failed", kind="frida_missing", text=describe(error), python=sys.executable)
        return 3
    channel.send("hello", helper=HELPER_VERSION, frida=str(getattr(frida, "__version__", "?")),
                 python=sys.executable)

    operations = queue.Queue()
    reader = threading.Thread(target=read_operations, args=(sys.stdin.buffer, operations))
    reader.daemon = True
    reader.start()

    game = Game(frida, channel)
    next_state = time.monotonic()
    try:
        while True:
            try:
                operation = operations.get(timeout=max(0.0, next_state - time.monotonic()))
            except queue.Empty:
                next_state = time.monotonic() + STATE_SECONDS
                game.push_state()
                continue
            if operation is None:
                break
            name = operation.get("op")
            if name == "setup":
                game.setup(str(operation.get("process", "")), str(operation.get("source", "")))
            elif name == "attach":
                game.attach()
            elif name == "exec":
                game.call("exec", operation.get("id"), operation.get("command"))
            elif name == "action":
                game.call("action", operation.get("id"), operation.get("name"))
    finally:
        game.detach()
        try:  # what Frida's own tools do before they leave
            frida.shutdown()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except BaseException:
        traceback.print_exc()
        code = 1
    try:
        sys.stderr.flush()
    except Exception:
        pass
    # Leave at once. Letting Python wind down first would have it trip over the thread that may still be reading
    # the input, and over whatever Frida still has running.
    os._exit(code)

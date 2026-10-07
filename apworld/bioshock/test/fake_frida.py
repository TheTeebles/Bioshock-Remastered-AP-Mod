"""
A stand-in for the `frida` package, for testing the helper process without Frida or the game.

The tests copy this file into a temporary folder as frida/__init__.py and start the helper with that folder on its
path, so the helper imports it as if it were the real thing. Being in another process, it cannot be handed objects
by the test. It takes its orders from the folder named by FAKE_FRIDA_DIR instead:

    orders.json   read again on every call, so a test can change how the game behaves while the helper runs
    calls.jsonl   one line for everything that was asked of "Frida", for the test to read back

Like the real one, it delivers messages, log lines and the end of a session on a thread of its own, never on the
thread that called it.

Orders (all optional):
    missing                 importing the package fails, as if Frida were not installed
    running                 False: the game is not running
    ambiguous               two copies of the game are running
    denied, attach_error    attaching fails with a permission error, or with this text
    script_error            the script throws this while it loads
    closes_while_loading    the game closes while the script loads
    closes_at_once          the game closes the moment Frida is in, before anything can listen for it
    old_frida               an old Frida: the script cannot be given a log handler, so what it logs is printed,
                            and its exports are reached as `exports`, not `exports_sync`
    state                   values to lay over the default answer of state()
    state_error             state() fails with this text
    hang                    state() does not return while this order stands
    detach                  the session ends for this reason (Frida's word for it, e.g. process-terminated)
    detach_delay            ... and Frida says so this many seconds after the call that noticed. It also delays
                            Frida's word when the game closes while the script loads
    handled                 False: commands come back as not handled
    silent                  commands get no answer at all: they wait in the agent, as in a paused game
    prints                  lines the game prints in answer to every command
    raises                  what the game raises, and deals with, while every command runs (the agent's words for it)
    raised                  how many it raised in all, where that is more than the agent passed on
    dies_on                 the game gives up over any command containing this text: the command starts, whatever
                            `raises` says is raised, and then the game is gone without an answer
"""
import json
import os
import queue
import sys
import threading
import time

__version__ = "17.0.0-fake"

_FOLDER = os.environ["FAKE_FRIDA_DIR"]
_RECORD_LOCK = threading.Lock()

DEFAULT_STATE = {
    "agent": "fake", "build": "Steam 1.0.127355", "execAvailable": True, "hookInstalled": True, "engineReady": True,
    "pending": 0, "levelValue": 1030, "loading": False, "inGame": True, "fontainePhase": None,
    "playerPath": "ready", "playerReady": True, "map": "1-Medical.bsm",
}


def _orders():
    for _ in range(50):  # the test replaces the file in one step, but be patient all the same
        try:
            with open(os.path.join(_FOLDER, "orders.json"), encoding="utf-8") as stream:
                return json.load(stream)
        except (OSError, ValueError):
            time.sleep(0.01)
    return {}


def _record(call, **details):
    details["call"] = call
    with _RECORD_LOCK:
        with open(os.path.join(_FOLDER, "calls.jsonl"), "a", encoding="utf-8") as stream:
            stream.write(json.dumps(details) + "\n")


if _orders().get("missing"):
    raise ImportError("No module named 'frida'")

_record("import", pid=os.getpid(), python=list(sys.version_info[:2]), path0=sys.path[0] if sys.path else None)


class ProcessNotFoundError(Exception):
    pass


class PermissionDeniedError(Exception):
    pass


class InvalidOperationError(Exception):
    pass


class RPCException(Exception):
    pass


class _Dispatcher(threading.Thread):
    """Frida's own thread: everything Frida tells its user arrives on it, in order."""

    def __init__(self):
        super().__init__(name="fake frida", daemon=True)
        self._jobs = queue.Queue()
        self.start()

    def run(self):
        while True:
            job = self._jobs.get()
            try:
                job()
            except Exception:
                import traceback
                traceback.print_exc()

    def post(self, job, delay=0.0):
        if delay:
            timer = threading.Timer(delay, self._jobs.put, (job,))
            timer.daemon = True
            timer.start()
        else:
            self._jobs.put(job)

    def flush(self):
        done = threading.Event()
        self._jobs.put(done.set)
        done.wait()


_DISPATCHER = _Dispatcher()


class _Exports:
    def __init__(self, script):
        self._script = script

    def state(self):
        orders = self._script.check()
        while orders.get("hang"):
            time.sleep(0.02)
            orders = self._script.check()
        if orders.get("script_error"):
            raise RPCException("unable to find method 'state'")
        if orders.get("state_error"):
            raise InvalidOperationError(orders["state_error"])
        state = dict(DEFAULT_STATE)
        state.update(orders.get("state") or {})
        return state

    def exec(self, command_id, command):
        orders = self._script.check()
        _record("exec", id=command_id, command=command)
        if orders.get("silent"):
            return 1
        # Like the real agent: first that the command has gone into the game, then what the game raises while it
        # runs, then how it ended.
        self._script.post({"type": "exec_started", "id": command_id, "command": command})
        raises = orders.get("raises") or []
        for what in raises:
            self._script.post({"type": "exec_raised", "id": command_id, "command": command, "what": what})
        if orders.get("dies_on") and orders["dies_on"] in command:
            self._script.end("process-terminated", float(orders.get("detach_delay", 0.0)))
            return 1
        prints = orders.get("prints") or []
        self._script.post({"type": "exec_result", "id": command_id, "command": command,
                           "handled": orders.get("handled", True), "via": "player",
                           "output": prints[:20], "outputLines": len(prints),
                           "raised": orders.get("raised", len(raises))})
        return 1

    def action(self, command_id, name):
        self._script.check()
        _record("action", id=command_id, name=name)
        self._script.post({"type": "action_result", "id": command_id, "name": name, "ok": False,
                           "reason": "not yet"})


class _OldScript:
    """A script as old versions of Frida had it: no way to be given a log handler, and `exports` for the calls."""

    def __init__(self, session, source):
        self._session = session
        self._handlers = {}
        self._log_handler = None
        self.exports = _Exports(self)
        _record("create_script", length=len(source), start=source[:40])

    def on(self, name, handler):
        self._handlers[name] = handler

    def load(self):
        orders = _orders()
        _record("load")
        self._log("info", "[bioshock-ap] build: Steam 1.0.127355")  # what agent.js prints first
        if orders.get("script_error"):
            # How Frida reports a script that throws while loading: load() returns normally, an error message
            # arrives, and the script has no exports.
            self.post({"type": "error", "description": orders["script_error"], "lineNumber": 12, "stack": "..."},
                      wrap=False)
        _DISPATCHER.flush()  # what a script says while it loads has arrived by the time load() returns
        if orders.get("closes_while_loading"):
            delay = float(orders.get("detach_delay", 0.0))
            self._session.end("process-terminated", delay)
            if not delay:
                _DISPATCHER.flush()

    def post(self, payload, wrap=True):
        message = {"type": "send", "payload": payload} if wrap else payload
        handler = self._handlers.get("message")
        if handler is not None:
            _DISPATCHER.post(lambda: handler(message, None))

    def end(self, reason, delay=0.0):
        """The game goes away."""
        self._session.end(reason, delay)

    def check(self):
        """Every call into the script starts here: it fails once the session has ended."""
        orders = _orders()
        if orders.get("detach") and not self._session.ended:
            self._session.end(orders["detach"], float(orders.get("detach_delay", 0.0)))
        if self._session.ended:
            raise InvalidOperationError("script has been destroyed")
        return orders

    def _log(self, level, text):
        if self._log_handler is not None:
            handler = self._log_handler
            _DISPATCHER.post(lambda: handler(level, text))
        else:
            print(text)  # what Frida does with a script's log lines when it has not been told otherwise


class _Script(_OldScript):
    def __init__(self, session, source):
        super().__init__(session, source)
        self.exports_sync = self.exports
        del self.exports

    def set_log_handler(self, handler):
        self._log_handler = handler


class _Session:
    def __init__(self):
        self._handlers = {}
        self.ended = False

    @property
    def is_detached(self):
        return self.ended

    def create_script(self, source):
        if self.ended:
            raise InvalidOperationError("session is gone")
        return (_OldScript if _orders().get("old_frida") else _Script)(self, source)

    def on(self, name, handler):
        self._handlers[name] = handler

    def end(self, reason, delay=0.0):
        self.ended = True
        handler = self._handlers.get("detached")
        if handler is not None:
            _DISPATCHER.post(lambda: handler(reason, None), delay)

    def detach(self):
        _record("detach")
        if not self.ended:
            self.end("application-requested")
            _DISPATCHER.flush()  # like the real one: whoever detaches has been told so by the time this returns


def attach(name):
    orders = _orders()
    _record("attach", name=name)
    if orders.get("ambiguous"):
        raise ProcessNotFoundError("ambiguous name; it matches: %s (pid: 4012), %s (pid: 7780)" % (name, name))
    if not orders.get("running", True):
        raise ProcessNotFoundError("unable to find process with name '%s'" % name)
    if orders.get("denied"):
        raise PermissionDeniedError("unable to access process with pid 4012 from the current user account")
    if orders.get("attach_error"):
        raise InvalidOperationError(orders["attach_error"])
    session = _Session()
    if orders.get("closes_at_once"):
        session.ended = True  # nobody is listening yet, so nobody is told
    return session


def shutdown():
    _record("shutdown")

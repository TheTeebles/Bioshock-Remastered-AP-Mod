# BioShock APWorld source (v0.1.5)

This folder mirrors `worlds/bioshock/` from an Archipelago source checkout: the world, and the client that ships with
it. The design and rationale live in `claude/bioshock-ap-design.md`.

## Layout

- `bioshock/*.py` and `bioshock/docs/`: the world (items, locations, options, logic) and its pages.
- `bioshock/client/`: the BioShock Client.
  - `core.py` makes every decision; `context.py` is the Archipelago wrapper and the `/` commands.
  - `game_data.py` holds the tables: which console command gives which item, which map is which level.
  - `simulated.py` is a stand-in game.
  - `frida_agent.py`, `frida_helper.py` and `agent.js` are the link to the real game. The agent has given items in
    the real game by hand; the client has not been run against it yet.
    - `frida_agent.py` is the client's side: it finds a Python that has Frida and starts the helper in it.
    - `frida_helper.py` runs in that Python, never in Archipelago's. It may only use the standard library and
      Frida, and has to stay valid for Python 3.7.
    - `agent.js` runs inside the game.
- `bioshock/components.py`: the Launcher entry.
- `bioshock/test/`: 322 tests.

## Rebuild `bioshock.apworld`

1. Clone Archipelago (0.6.7 or newer) and copy `apworld/bioshock/` to `worlds/bioshock/`.
2. Run the tests: `python -m pytest worlds/bioshock`, and optionally `python -m pytest test/general`.
3. Package it: `python Launcher.py "Build APWorlds" -- "BioShock"`. The output is `build/apworlds/bioshock.apworld`.

Install by double-clicking the `.apworld` or copying it into Archipelago's `custom_worlds` folder.

## Tests

| File | What it covers |
|---|---|
| `test_data.py` | The world's tables. All story location IDs are pinned here |
| `test_logic.py` | Access rules, the item pool, every combination of the check options, the settings that are refused, and whole seeds through Archipelago's own generator |
| `test_client_core.py` | The client's decisions, against the simulated game. Also holds the item table against the two class lists below |
| `test_client_context.py` | The Archipelago wrapper, fed the packets a server sends |
| `test_client_frida.py` | The way into the real game: the helper process spoken to directly, the adapter (including what the game raises during a command, and a game that goes away in the middle of one), and finding a Python |
| `test_client_playthrough.py` | Whole generated seeds played start to finish, with Archipelago's logic as referee |
| `test_client_server.py` | The client against Archipelago's own server code, over an in-memory pipe instead of a socket. The last class runs server, client, helper and a stand-in Frida together |

Shared pieces:

- `client_harness.py`: what the client tests share.
- `bases.py`: the helper that runs Archipelago's generator.
- `frida_harness.py` and `fake_frida.py`: a stand-in `frida` package for real helper processes to import, which
  takes its orders from a file so that a test can change how "the game" behaves while the helper runs.
- `runtime_classes.txt`: the classes of `ShockGame` and `ShockDesignerClasses` that the running game listed
  (Steam build, 2026-10-07): 1,277 names, with every class of the designer package among them. This is the list
  that counts for whether a class can be given.
- `shockgame_classes.txt`: the 654 class names of the game's `ShockGame` script package (Steam build), exported
  with UE Explorer. Names only. It is what the package on disk declares, which is not always what the running
  game has: the six higher levels it lists (the Research Camera's rewards) are classes of the designer package
  there.

The tests of the helper start real Python processes, so they need to be able to do that, and take about ten
seconds.

## The agent script

`client-poc/bioshock_ap_agent.js` is the agent for running by hand with the Frida CLI. It is the same file as
`bioshock/client/agent.js` (version 2026-10-07.1); keep the two identical.
`client-poc/agent_mock_test.js` tests it under Node against a fake Frida runtime and a fake piece of game memory:
`node agent_mock_test.js bioshock_ap_agent.js`.

## Reference

- `reference/bioshock_classes.txt`: the class list of the game's script packages, exported with UE Explorer
  (1,760 names).
- `reference/bioshock_runtime_classes.txt`: the classes the running game listed for `obj list`: the biggest 2,997
  of about 5,040, and all 831 of the designer package (3,010 names).

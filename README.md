# Bioshock-Remastered-AP-Mod

A mod to use BioShock Remastered with [Archipelago](https://archipelago.gg) for randomization: the APWorld, the
client that ships inside it, and the script the client puts into the running game.

> **Early development (v0.1.5).** Seeds generate, the client talks to Archipelago and puts items into the real game
> on the Steam version, and it notices levels reached, most story objectives, Little Sisters and audio diaries.
> Power to the People stations, film reels and five story objectives are not noticed yet, so a whole seed can only
> be played end to end with the client's simulated game for now.

## Where things stand

| Part | State |
|---|---|
| Generating seeds | Works. Tested on Archipelago 0.6.7 and 0.6.8-dev |
| The client's Archipelago side: login, items, held checks, goal, DeathLink, saved progress | Works. Can be tried without the game, with `/simulate` |
| Getting into the game and giving items (Steam build 1.0.127355) | Works in the real game, by the client itself: ADAM, tonics, plasmids, slots, upgrades, weapons, ammo and more. The Security Alarm trap works too |
| A game command for every item | Every class the client names is one the running game lists. Not every item has been given yet |
| Following which level you are in, "Level Complete" checks, holding items while paused | Works in the real game |
| Story objectives | 28 of 33 noticed, from the game's own quest list. Seen sending checks in the real game. Electro Bolt, Ryan's ambush, the ice wall, Peach Wilkins and the self-destruct are still to find |
| Little Sisters | Noticed when one is rescued or harvested (not which). Seen in the game with the probe; the client's first real run missed one, and a fix is waiting to be tried |
| Audio diaries | All 122 noticed when picked up, from the diary the game hands you. Built and tested without the game; not tried in the real game yet |
| Power to the People stations and film reels | Not noticed yet. Being looked for with the probe |
| DeathLink and the EVE Drain and Pickpocket traps inside the game | Not built yet |
| The GOG and Epic versions | Not supported yet |

[claude/bioshock-ap-design.md](claude/bioshock-ap-design.md) has the whole picture: what was found out about the
game, how everything is built and tested, what each test in the real game showed, and what comes next.

## What is where

| Path | What it is |
|---|---|
| [`apworld/bioshock/`](apworld/bioshock) | The APWorld's source: the world, the client in `client/`, its pages in `docs/` and its tests in `test/` |
| [`apworld/README.md`](apworld/README.md) | How the source is laid out, how to run the tests and how to build the `.apworld` |
| [`client-poc/`](client-poc) | The in-game script for running by hand with the Frida command line, its tests, a script that sends one command through it, and the probe (below) |
| [`claude/bioshock-ap-design.md`](claude/bioshock-ap-design.md) | Design, findings, test notes and the list of what comes next |
| [`reference/`](reference) | Class names of the game, and nothing but names: what its script packages declare, and what the running game listed |

## Trying it

There is no ready-built download here yet, so `bioshock.apworld` has to be built first.
[apworld/README.md](apworld/README.md) has the steps. Then:

1. Install [Archipelago](https://github.com/ArchipelagoMW/Archipelago/releases/latest) 0.6.7 or newer.
2. Install `bioshock.apworld`: double-click it, or copy it into Archipelago's `custom_worlds` folder.
3. Follow the [setup guide](apworld/bioshock/docs/setup_en.md). It covers generating a game, connecting, what the
   real game does today, and playing a seed with the simulated game.

[What the randomizer does](apworld/bioshock/docs/en_BioShock.md) describes the checks, the items and the goal.

## Working on it

`apworld/bioshock/` goes into an Archipelago source checkout as `worlds/bioshock/`.
[apworld/README.md](apworld/README.md) has the steps for running the tests and building the `.apworld`.

`apworld/bioshock/client/agent.js` and `client-poc/bioshock_ap_agent.js` are the same file and have to stay that
way. Their tests run with `node client-poc/agent_mock_test.js apworld/bioshock/client/agent.js`.

### The probe

[`client-poc/probe_script_calls.js`](client-poc/probe_script_calls.js) is how the checks above were found. It goes
into the running game on its own (close the client first):

```
frida -n BioshockHD.exe -l probe_script_calls.js -o probe_log.txt
```

At its prompt, among others:

| Command | What it does |
|---|---|
| `classes('sister')` | Every loaded class whose name matches, with how many objects each has |
| `fields('SpawnedGatherer')` | Every property of a class and the classes it extends, with where each sits |
| `snap('^Quest$')`, then `diff()` | Remember the objects of those classes, do one thing in the game, and see which properties changed |
| `quests()`, `sisters()`, `diaries()`, `diaryTable()` | The game's quests, Little Sisters, audio diaries and every diary it knows |
| `where('PlaceableWeaponUpgradeStation')` | Each object of the matching classes, where it is and how far away |
| `goto('PlaceableWeaponUpgradeStation', 0)` | Moves you next to one, for testing. Save first: it writes your position straight into memory |

Its tests run with `node client-poc/probe_mock_test.js`.

## Credits

BioShock is a 2K game. This is a fan project and is not affiliated with 2K. No game files and none of the game's
scripts are in this repository; the class lists hold names only.

What is known here about the inside of the game rests on other people's work: the
[LiveSplit autosplitter](https://github.com/PrototypeAlpha/BSR-ASL), CodeBlueDev's
[Cheat Engine table](https://github.com/CodeBlueDev/BioshockRemasteredCheatTable), the
[BioShock Remastered VR](https://github.com/BioVRDev/Bioshock-Remastered-VR) mod, UE Explorer, and the guides
players have written. Section 9 of the design document lists them all. The client reaches the game with
[Frida](https://frida.re).

The repository's license is [CC0 1.0](LICENSE).

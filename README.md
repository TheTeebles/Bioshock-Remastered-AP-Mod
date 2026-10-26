# Bioshock-Remastered-AP-Mod

A mod to use BioShock Remastered with [Archipelago](https://archipelago.gg) for randomization: the APWorld, the
client that ships inside it, and the script the client puts into the running game.

> **First release (v0.2.0).** Seeds generate, the client talks to Archipelago and puts items into the real game on
> the Steam version, and every kind of check has been seen working there. No whole seed has been played start to
> finish yet, so expect rough edges. [CHANGELOG.md](CHANGELOG.md) lists what is in each release.

## Where things stand

| Part | State |
|---|---|
| Generating seeds | Works. Tested on Archipelago 0.6.7 and 0.6.8-dev |
| The client's Archipelago side: login, items, held checks, goal, DeathLink, saved progress | Works. Can be tried without the game, with `/simulate` |
| Getting into the game and giving items (Steam build 1.0.127355) | Works in the real game, by the client itself: ADAM, tonics, plasmids, slots, upgrades, weapons, ammo and more. The Security Alarm trap works too |
| A game command for every item | Every class the client names is one the running game lists. Not every item has been given yet |
| Following which level you are in, "Level Complete" checks, holding items while paused | Works in the real game |
| Story objectives | All 28 noticed, from the game's own quest list. Seen sending checks in the real game |
| Little Sisters | Noticed when one is rescued or harvested (not which). Works in the real game |
| Audio diaries | All 122 noticed when picked up, from the diary the game hands you. Works in the real game |
| Power to the People stations | Noticed from each station's use counter. Works in the real game. Stations open no menu: using one sends its check and closes it, and the 12 weapon upgrades come from the multiworld and wait for their weapon |
| Gatherer's Garden purchases (Shop Checks option) | Off by default; `first_appearances` (61 checks) or `all` (273), every level's Garden, read from the game's map files. Each line is renamed after the multiworld item it holds, priced and given an icon, and buying it sends the check (Steam). Works in the real game at Neptune's Bounty; the other levels' Gardens are not tried in game yet |
| World items, safes and research (World Item Checks, Safe Checks, Research Checks options) | Off by default. Plasmid and tonic pickups, first time holding each weapon, the 41 wall safes, and each research level. Pickups can keep their item or be shuffled into the multiworld (Telekinesis then becomes progression). Works in the real game |
| Open Rapture (Level Access option) | Experimental. Access items open the bathysphere one chapter at a time, in any order the seed allows, and level-change doors go where the seed says. Each part works in the real game; no whole seed played yet. The first bathysphere menu in a level could show the vanilla list; a fix is in but not yet tried in game (if it still happens, close it and pull the switch again) |
| Universal Tracker map tab | A page per floor and per level drawn from the game's own map screen, with a pin on every check that has a place, and a dot where you stand while the client is connected |
| Director's Commentary film reels | Not checks: a reel unlocks its film for the whole PC, not the save |
| DeathLink in the game | Works in the real game, both ways: your deaths are sent, and a received death kills Jack through the game's own OnKilled, so he collapses and wakes in a Vita-Chamber |
| The EVE Drain and Pickpocket traps inside the game | Work in the real game (EVE drops to zero, half your dollars go); the EVE bar redraws at once; the dollars counter does not flash up (accepted by Teebs) |
| The GOG and Epic versions | Not supported, and not planned (Teebs, 2026-10-25) |

[docs/design.md](docs/design.md) has the whole picture: what was found out about the
game, how everything is built and tested, what each test in the real game showed, and what comes next.

## What is where

| Path | What it is |
|---|---|
| [`apworld/bioshock/`](apworld/bioshock) | The APWorld's source: the world, the client in `client/`, its pages in `docs/` and its tests in `test/` |
| [`apworld/README.md`](apworld/README.md) | How the source is laid out, how to run the tests and how to build the `.apworld` |
| [`frida/`](frida) | What runs against the game through Frida by hand: the agent's tests, a script that sends one command through the agent, and the probe (below) with its tests |
| [`tools/`](tools) | Scripts for working on the mod: the map file readers, the tracker builder, the AP icon maker, the test-log reader and the release script |
| [`docs/design.md`](docs/design.md) | Design, findings, test notes and the list of what comes next |
| [`reference/`](reference) | Class names of the game, and nothing but names: what its script packages declare, and what the running game listed |

## Trying it

1. Install [Archipelago](https://github.com/ArchipelagoMW/Archipelago/releases/latest) 0.6.7 or newer.
2. Download `bioshock.apworld` from the [latest release](https://github.com/TheTeebles/Bioshock-Remastered-AP-Mod/releases/latest)
   and install it: double-click it, or copy it into Archipelago's `custom_worlds` folder. To build it yourself
   instead, [apworld/README.md](apworld/README.md) has the steps.
3. Follow the [setup guide](apworld/bioshock/docs/setup_en.md). It covers generating a game, connecting, what the
   real game does today, and playing a seed with the simulated game.

[What the randomizer does](apworld/bioshock/docs/en_BioShock.md) describes the checks, the items and the goal.

## Working on it

`apworld/bioshock/` goes into an Archipelago source checkout as `worlds/bioshock/`.
[apworld/README.md](apworld/README.md) has the steps for running the tests and building the `.apworld`.

The game agent is `apworld/bioshock/client/agent.js`, the one script for the client and for running by hand with the
Frida command line (`frida -n BioshockHD.exe -l apworld/bioshock/client/agent.js`). Its tests run with
`node frida/agent_mock_test.js`.

### The probe

[`frida/probe_script_calls.js`](frida/probe_script_calls.js) is how the checks above were found. It goes
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

Its tests run with `node frida/probe_mock_test.js`.

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

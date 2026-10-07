# Bioshock-Remastered-AP-Mod

A mod to use BioShock Remastered with [Archipelago](https://archipelago.gg) for randomization: the APWorld, the
client that ships inside it, and the script the client puts into the running game.

> **Early development (v0.1.4).** Seeds generate, the client talks to Archipelago, and items have been put into the
> real game on the Steam version, by hand. Most of what you do in the game is not noticed yet, so a whole seed can
> only be played end to end with the client's simulated game for now.

## Where things stand

| Part | State |
|---|---|
| Generating seeds | Works. Tested on Archipelago 0.6.7 and 0.6.8-dev |
| The client's Archipelago side: login, items, held checks, goal, DeathLink, saved progress | Works. Can be tried without the game, with `/simulate` |
| Getting into the game and giving items (Steam build 1.0.127355) | Seen working in the real game, done by hand: ADAM, gene tonics of the first and second level, a second-level plasmid and a plasmid slot |
| A game command for every item | Every class the client names is one the running game lists. Most have not been given yet |
| The client doing all that by itself, and following which level you are in | Built and tested without the game. Not run in the real game yet |
| Noticing story objectives, Little Sisters, Power to the People stations, audio diaries and film reels | Not started |
| DeathLink and the EVE Drain and Pickpocket traps inside the game | Not built yet |
| The GOG and Epic versions | Not supported yet |

[claude/bioshock-ap-design.md](claude/bioshock-ap-design.md) has the whole picture: what was found out about the
game, how everything is built and tested, what each test in the real game showed, and what comes next.

## What is where

| Path | What it is |
|---|---|
| [`apworld/bioshock/`](apworld/bioshock) | The APWorld's source: the world, the client in `client/`, its pages in `docs/` and its tests in `test/` |
| [`apworld/README.md`](apworld/README.md) | How the source is laid out, how to run the tests and how to build the `.apworld` |
| [`client-poc/`](client-poc) | The in-game script for running by hand with the Frida command line, its tests, and a script that sends one command through it |
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
way.

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

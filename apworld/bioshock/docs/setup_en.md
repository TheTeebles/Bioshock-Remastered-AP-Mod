# BioShock Remastered Randomizer Setup Guide

> **Early development.** Seeds generate, the client talks to Archipelago, and items can be put into the real game on
> the Steam version. Most of what you do in the game is not noticed by the client yet, so a whole seed can only be
> played end to end with the client's simulated game for now (see "Trying it without the game"). "What to expect
> from the real game today" below says exactly where things stand.

## Required Software

- [Archipelago](https://github.com/ArchipelagoMW/Archipelago/releases/latest) 0.6.7 or newer
- The `bioshock.apworld` file, installed by double-clicking it or copying it into Archipelago's `custom_worlds` folder
- BioShock Remastered for PC, the Steam version, on Windows
- For the real game: Python with the Frida package (see "Python and Frida")

## Generating a game

BioShock is a custom world, so the game has to be generated on a PC that has the apworld installed. The Archipelago
website cannot generate it, but it can host the result.

1. Create a YAML with the options you want. "Generate Template Options" in the Archipelago Launcher writes a template
   to `Players/Templates/BioShock.yaml`. Out of the box, audio diaries and film reels only hold filler, so nobody
   has to hunt for them. `audio_diary_checks` and `directors_commentary_checks` change that (`off`, `filler_only`
   or `all`).
2. Put it in the `Players` folder together with everyone else's YAMLs and run "Generate" from the Launcher.
3. Host the zip that appears in the `output` folder: upload it to the website's Host Game page, or run the server
   yourself. The [Archipelago Setup Guide](https://archipelago.gg/tutorial/Archipelago/setup_en) explains both.

## Connecting

1. Open the Archipelago Launcher and start **BioShock Client**.
2. Enter the server address at the top and connect, then give your slot name when asked.
3. Start BioShock Remastered and load your save. The client looks for the game every few seconds and says so when it
   has found it ("Game connected").

Items are given while you are in one of Rapture's levels with the game running. During loading screens, in the
menu, in the Challenge Rooms and before the bathysphere ride at the very start they wait.

### Python and Frida

The client reaches into the running game with [Frida](https://frida.re). Frida is a Python package, and the
Archipelago you install cannot have packages added to it, so the client borrows a Python of your own:

1. Install Python 3 from [python.org](https://www.python.org/downloads/) and tick "Add python.exe to PATH" in the
   installer.
2. Open a terminal (Command Prompt) and run `pip install frida`.

That is all. When the client looks for the game it starts a small helper in that Python, which stays in the
background for as long as the client is open and goes away with it. The client says which Python and which version
of Frida it is using when it connects to the game.

If the client cannot find a Python with Frida in it, it says which ones it tried and why each did not work. If your
Python is somewhere unusual, name it with `/python C:\path\to\python.exe`. The client remembers that.

If the game was started as administrator, Archipelago has to be started as administrator too.

### What to expect from the real game today

- **Steam, build 1.0.127355.** Everything in the real game was done by hand so far, one command at a time; the
  client doing it by itself has not been run against the game yet.
  - *Seen arriving in the real game:* ADAM, gene tonics of the first and second level, a second-level plasmid
    and a plasmid slot. All but the ADAM come with the game's own window for equipping or swapping, as if you
    had picked them up.
  - *Taken by the game without complaint, not yet confirmed in the inventory:* a weapon, a weapon upgrade and an
    EVE hypo. The Security Alarm trap rings for a minute; whether it brings security bots is not settled.
  - *Should work, not tried yet:* every other item. Each class the client names is one the running game listed
    when asked. Also following which level you are in, sending "Level Complete" checks when you reach the next
    level, and reporting the goal when Fontaine is defeated.
  - If the game has no class of the name the client gives it, the client notices and says so ("Could not
    deliver ...: the game has no class ..."), and sets the item aside. If the client says "Delivered" and you got
    nothing, that is worth reporting, with the item's name.
- **GOG and Epic:** not supported yet. The client's game side was made for the Steam version.
- **Not yet, on any version:** noticing story objectives, Little Sisters, Power to the People stations, audio
  diaries and film reels; the EVE Drain and Pickpocket traps; and DeathLink. The client names every item it could
  not give you and keeps it for later.

## Client commands

| Command | What it does |
| --- | --- |
| `/bioshock` | Shows what the client is doing: connections, items given, items set aside, checks held back. |
| `/resync` | Gives you every received item again. `/resync 5` gives only the last five again. |
| `/retry` | Tries again to give the items the game refused. |
| `/attach` | Looks for the running game right now. |
| `/detach` | Lets go of the game and stops looking for it until `/attach`. |
| `/python` | Shows which Python and Frida reach the game. `/python <path>` names the Python to use, `/python auto` goes back to looking for one. |
| `/deathlink` | Turns DeathLink on or off for this session. |
| `/simulate` | Swaps the real game for a simulated one, or back. |
| `/sim ...` | Plays the simulated game (see below). |

## Trying it without the game

Type `/simulate` in the client. It now behaves as if BioShock were running, and you play with `/sim`:

- `/sim travel arcadia` moves to a level (part of the name is enough)
- `/sim story` does the next story objective in the level you are in
- `/sim sister`, `/sim station`, `/sim reel` do the next one of those in the level you are in
- `/sim diary 42` picks up an audio diary (1 to 122, in story order)
- `/sim fontaine` beats the final boss
- `/sim die` dies, to try DeathLink
- `/sim loading` and `/sim pause` switch a loading screen or a pause on and off, to see items wait
- `/sim bag` lists what the client has given you

Checks, items, held checks and the goal all go through the real Archipelago server, so this is a good way to try a
seed with friends before the game side is ready.

Use it on a test seed only. The simulated game sends real checks, and what it is given counts as given, so the real
game would not get those items afterwards without `/resync`.

## Good to know

- **Held checks.** With Level Access on, a level's checks are only sent once you have its access item. The client
  remembers what you found and sends it the moment the item arrives. `/bioshock` lists what is waiting.
- **One save per seed.** The client cannot tell saves apart yet. Whatever the loaded save has done is reported to the
  room you are connected to, so start a new game for every seed and never load a save from another one while
  connected.
- **Items that could not be given.** They are set aside, not lost. Items the client does not know how to give yet
  arrive by themselves once an update teaches it. Items the game refused wait for `/retry`.
- **If the game closes while an item is arriving.** That item is set aside as well, in case it is what closed the
  game, and the client carries on with the next one when the game is back. `/retry` tries it again. The client's
  log says what the game reported just before, if it reported anything.
- **Pausing and loading.** Items wait and arrive when the game is running again.
- **Reloading an older save.** Items you were given after that save are gone from the game, but the client has
  already counted them. Use `/resync` (or `/resync <number>`) to get them again.
- **Starting a new game on the same slot.** Type `/resync` to get your items again. The client tries to notice a new
  game and remind you.
- **Closing the client.** Progress is stored on the Archipelago server, so you can close and reopen it at any time.
  If the client crashes or is killed in the instant between giving an item and recording it, that one item is given
  again next time. Progress made while the client is disconnected from Archipelago is only kept while it stays open.
- **One client per slot.** Two BioShock clients on the same slot would both give you every item.

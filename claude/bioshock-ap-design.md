# BioShock Remastered × Archipelago: Design and Feasibility

*Last updated 2026-10-07. Status: APWorld v0.1.4. Items can be put into the real game on the Steam build, by hand:
ADAM, a first-level tonic, a plasmid slot, and second levels of two tonics and a plasmid from the designer package,
each with the game's own window. Every class the client names is one the running game listed when asked, and a
class the game does not have is now reported instead of passing for delivered. The client itself (helper process,
map names) has still not met the real game. Noticing what the player does (story objectives, Little Sisters,
stations, diaries) is the big piece still missing.*

## 1. Summary

- **Target:** BioShock Remastered on PC, Steam build first (GOG and Epic later). Game name in Archipelago:
  `BioShock`.
- **Verdict:** feasible, and the hard part of giving items is proven. Console commands such as `GiveItem` run
  *inside* the game process on the game thread, sent to the local player's own command entry.
- **Decision (2026-09-30):** deliver items in-process with Frida instead of key binds and simulated key presses. See
  section 3.
- **Goal (2026-09-30):** checks should also include world items (plasmids, tonics, weapons) and safes, not just audio
  diaries. Frida makes this possible by intercepting the game's pickup code. See section 5.
- **Decision (2026-10-03):** nothing important may depend on audio diaries or film reels. Some are hard to find even
  in a normal playthrough. By default they are extra checks that only ever hold filler, and 33 story objectives
  carry the important items instead. See section 4.
- **Decision (2026-10-06):** Frida runs in a helper process in the player's own Python, so the Archipelago people
  install can be used as it is. See section 3.
- **Decision (2026-10-07):** exceptions raised while a command runs are left to the game. The agent watches them
  and passes them on, and never takes one away. See section 3.
- **Done:**
  - APWorld with 210 default checks, 78 of which may hold important items, and 8 progression items. It passes its
    own tests and Archipelago's general test suite on 0.6.7 stable and 0.6.8-dev. Every combination of the check
    options either generates or is refused with an explanation (section 4, "How the world was tested").
  - The client's Archipelago side (section 6): login, item delivery, held checks, goal, DeathLink, saved progress and
    `/resync`. It ships in the `.apworld` as "BioShock Client" and can be tried without the game, with its simulated
    game.
  - **Seen in the real game** (Steam build, by hand at the Frida prompt, section 7):
    - 2026-10-06: attaching, the game-thread hook, engine commands, the route to the local player, and `GiveItem 10
      ShockGame.ADAM` arriving (0 ADAM before, 10 after). Also read correctly: the name of the map the player is
      in, and the player's health, EVE, dollars and ADAM.
    - 2026-10-07: a first-level tonic and a plasmid slot arriving, and second levels of two tonics and a plasmid
      from `ShockDesignerClasses`, each with the game's own window; a weapon, a weapon upgrade and an EVE hypo
      taken without complaint; the alarm command ringing for a minute; and the game saying, in an exception with
      text, that it has no class of a name it was given.
    - 2026-10-07: the game's own list of its classes. All 114 the client names are in it.
  - **Built on that in v0.1.3 and v0.1.4, tested without the game:**
    - the client reaches the game from the packaged Archipelago, through a helper process (section 3);
    - levels are told apart by map name, which also works after loading a save (section 6);
    - a game class or command for every item (section 6, "Which command gives which item");
    - the agent passes on what the game prints in answer to a command, and what it raises while the command runs;
    - a class the game does not have is reported as not delivered; an item the game closes over is set aside
      instead of being given again the moment the game is back (section 6, "Rules the client follows").
- **Not done:**
  - Noticing story objectives, Little Sisters, Power to the People stations, audio diaries and film reels. With the
    real game, only "Level Complete" checks (from the map the player reaches) and the goal can be sent so far.
  - The EVE Drain and Pickpocket traps, and DeathLink inside the game.
  - The GOG and Epic builds.
- **Next** (section 7, "Next session"): the few commands still to try by hand, then the first full run of client,
  real game and a real seed. After that, check detection.

## 2. Feasibility findings (rebuilt from research)

| Question | Finding | Source |
|---|---|---|
| Engine / binary | `BioshockHD.exe`, 32-bit (x86), modified Unreal Engine 2.5 | Steam forum; BioShock Remastered VR README |
| Builds in the wild | Steam 1.0.127355-L (after Aug 2022), EGS 1.0.127355-L, GOG / old Steam 1.0.122872. Module size tells them apart: 23556096 / 23552000 / 24207360 | BSR-ASL autosplitter |
| Current level | **The map's name**, read from the level object the hook is handed: `ULevel::URL.Map` at level `+0x7C`, e.g. `1-medical` (live, 2026-10-06). The autosplitter's number (`int` at `BioshockHD.exe+0x1386004`, 0 while loading) is only right when the level was entered by playing: after loading a save, Medical Pavilion read 1030. It is not the room in the actor list either (9,224 actors, room for 11,027 at the time) | Live tests; BSR-ASL |
| Loading / in-game | loading `int` at `+0x1356680`; in-game `bool` at `[+0x1356620]+0x214 → +0x6E8 → +0x38` | BSR-ASL |
| Goal detection | Fontaine phase `byte` at `[+0x1356200]+0x0 → +0x14 → +0x1C → +0x1148`, level ID 1309. Phase 3 → 4 means the game is beaten | BSR-ASL |
| Player stats | `PlayerController+0x450` → Pawn. Pawn `+0x57C` health (float), `+0xAF8` EVE (float), `+0xAEC` dollars, `+0xAF4` ADAM: measured on build 1.0.122283, and **read correctly on 1.0.127355** (dollars 126 on screen and in memory; ADAM 0, then 10 after the gift). Not checked yet: `+0x774` max health, `+0xAFC` max EVE, `+0xAF0` max dollars | CodeBlueDev Cheat Engine table; live test |
| Console | `-allowconsole` no longer works on Steam. Key binds in `User.ini` still run commands, including `exec <file>` for a file in the System folder | Steam guides and discussions |
| Useful commands | `GiveItem <qty> <Class>`, `GiveWeapon <Class>`, `AddWeaponStatUpgrade <Weapon> <Stat>`, `StartSecurityAlarm` / `StopSecurityAlarm`, `Suicide`, `GiveAll`, `GiveHealth`, `GiveBioAmmo`, `God`. Which class is which item: section 6, "Which command gives which item". For looking around: `obj list class=class` prints every class with its size. Adding `inside=<package>` does not narrow that down (the two conditions are joined by "or"); `obj list package=<package>` alone should, and is still to be tried. `obj linkers` prints only its heading | Steam guides; gameplay.tips; live tests |
| Script classes | 1,760 classes in the game's script packages, exported with UE Explorer on 2026-10-06: ShockGame 654, ShockAI 540, Engine 315, Scripting 99, VengeanceShared 80, Tyrion 27, Core 10, the IG effects packages 34, FMODAudio 1. Names only; kept in the project as `reference/bioshock_classes.txt`. `ShockDesignerClasses`, the package guides name for higher item levels, is not one of the script packages | Teebs's export |
| Classes in the running game | About 5,040, going by what `obj list class=class` printed on 2026-10-07 (standing in Medical Pavilion). The agent kept the 2,997 biggest: ShockDesignerClasses 818, FXClass 674, ShockAI 486, ShockGame 446, Engine 199, Scripting 79, and 24 smaller packages. `obj list package=ShockDesignerClasses` then gave that package whole: 831 classes. Names only, in the project as `reference/bioshock_runtime_classes.txt` (3,010). **It is not the export:** six second and third levels that the export lists under ShockGame (`FastTwitchTwo` and the other research rewards) are classes of ShockDesignerClasses in the running game, and `GiveItem 1 ShockGame.FastTwitchTwo` gave nothing. The designer package also holds the twelve U-Invent components (`BatteryComponent`, ...), the hypos the shops sell, pickups and recipes | Live, through the agent |
| Exceptions | The engine raises C++ exceptions as routine and catches them itself. Seen live: `Failed to find object 'Class ShockGame.NoSuchThing'`, thrown as text while `GiveItem` ran, after which the command came back as handled. In a 32-bit process, Frida's default way of calling a function takes every exception raised during the call away from the program and reports "system error"; the function is abandoned where it stood. That cut two `GiveItem` commands short on 2026-10-07 (section 7) | Live tests; Frida's documentation of `NativeFunction` (`exceptions: 'steal'` and `'propagate'`) |
| Package modding | UE Explorer decompiles `ShockGame.u`; the Deep Pockets mod edits default properties by hex. TFC Installer injects object property edits. There is no map editor | Nexus Mods |
| DLL injection | Proven. BioShock Remastered VR loads through a `dxgi.dll` proxy and uses MinHook. It finds engine functions such as `APlayerController::eventPlayerCalcView` by signature scan, against the Steam build | GitHub BioVRDev/Bioshock-Remastered-VR |
| In-process console | Proven by the VR mod. It calls `UGameEngine::Exec(const TCHAR* Cmd, FOutputDevice& Ar)` (the console's entry point, `__thiscall`) from a queue drained on the game thread. Steam build: engine pointer `+0x1375368`, vtable `+0xE0DFF4` (checked before every call), Exec `+0x4C5970`, `this` = engine `+0x40` (the FExec sub-object). A stub `FOutputDevice` whose slots pop 8 bytes works. `set`/`get` are handled; `get` returns class defaults, not live values. Epic addresses differ. **Confirmed live through Frida on 2026-10-06 for `get`. `GiveItem` comes back "not handled" on this path; sent to the local player's own FExec instead it works** (section 3) | VR mod `Game/EngineExec.cpp`, `Game/ExecQueue.cpp`; live tests |
| Game-thread hook points | World-delta function at `+0x53D850` on Steam, with a 47-byte signature for other builds (called twice per frame on the game thread). `eventPlayerCalcView` (about 4 calls per frame), found by string xref. **`Present` runs on a different thread**, so it is not safe for engine calls | VR mod `Camera/CameraHook.cpp` |
| Object layouts | The VR mod documents measured offsets for `Actor`, `Controller`/`PlayerController`, `Pawn`, `HUD` and `Hands`. It also shows how UE Explorer's CLI exports 1,765 script classes from `Build\Final\BakedScripts\pc\*.u`, so field offsets can be computed from declaration order | VR mod `docs/ENGINE-MAP.md`, `docs/UNREALSCRIPT.md` |
| Backtracking | The bathysphere network reaches every level except Welcome to Rapture, right up to the final fight | Steam forum; GameFAQs walkthrough |

## 3. Architecture

**Three parts:**

- **Archipelago client (Python):** based on CommonClient, ships inside the `.apworld` and runs from the Archipelago
  Launcher. It talks to the AP server and tracks locked checks, delivered items and DeathLink.
- **Helper (Python, in the player's own Python):** holds Frida. The client starts it and talks to it through its
  standard input and output. See "Packaging" below.
- **In-game agent (JavaScript, run by Frida):** injected into `BioshockHD.exe`. It reads game state, runs console
  commands, and later hooks pickups. Helper and agent exchange JSON messages through Frida (`rpc.exports` and
  `send()`).

**Where the code lives.** Everything is inside the apworld, under `worlds/bioshock/client/` (file by file in
section 6). The world itself never imports the client, so generating a seed does not load it.

**Giving items.** The agent queues commands like `GiveItem 1 ShockGame.Incineration` and runs them from a hook on
the function that ticks a level. That means on the game thread, at most one command per tick, and never from Frida's
own thread. No key binds, no `User.ini` edits, no need for the game to have focus. Each call returns whether
something took the command.

There are two places a command can be sent, and it matters which:

- **The engine** (`UGameEngine::Exec`). Handles engine commands such as `get` and `set`. Confirmed working live.
  It does **not** reach commands that belong to the player: `GiveItem` came back "not handled" and nothing was given.
- **The local player** (the engine's `UPlayer` object, also an `FExec`). This is where key binds and the console
  send what the player types, so cheats such as `GiveItem` are handled here. **Confirmed live:** `GiveItem 10
  ShockGame.ADAM` came back "handled by the player" and the ADAM arrived. The agent reaches the player from the
  engine object and checks every link before using it (section 6, "Finding the local player").

The agent asks the player first and the engine second.

**What "handled" does and does not mean.** It means a command of that name exists and was run. It does not mean the
item arrived. **Confirmed live (2026-10-07):** `GiveItem 1 ShockGame.NoSuchThing` came back as handled, and nothing
arrived. But the game does say that it has no such class, not by printing it but by raising an exception with the
text `Failed to find object 'Class ShockGame.NoSuchThing'`, which it then deals with itself. The agent passes on
what the game raises while a command runs, and the client takes that particular sentence for what it is: the item
is reported as not delivered and set aside. What "handled" still cannot tell is whether a class that exists did
what was hoped.

**Exceptions are left to the game (decided 2026-10-07).** The agent calls the game's command entry with Frida's
`exceptions: 'propagate'`. Frida's default, `'steal'`, takes every exception raised during the call away from the
program and hands it to the script as an error. That is meant for experiments ("may leave the application in an
undefined state", in Frida's words), and it is wrong for this engine, which raises exceptions as routine and
catches them itself: the first exception ends the call where it stands. On 2026-10-07 that is what happened to
`GiveItem 1 ShockDesignerClasses.ArmoredBodyTwo` and `...ElectricBoltTwo`: the item was added, something was
raised, Frida reported "system error", the rest of the command never ran (no window, nothing equipped), and the
agent, taking it for a fault, turned commands off. What was raised turned out to be routine: for an item of the
designer package the game also looks for a class of that name in `ShockGame`, does not find one (`Failed to find
object 'Class ShockGame.ArmoredBodyTwo'`), and carries on. Left alone, the same commands end with the game's
window, as they should. Since then:

- The game gets every exception. The agent only looks: it has Frida show it the exceptions of the process
  (`Process.setExceptionHandler`), answers "not mine" to every one of them, and describes those raised on the game
  thread while one of its commands runs. `watch(false)` at the prompt stops the describing.
- What it can describe: text the engine throws (`C++ exception: "..."`), a number, the name of any other thrown
  type, what the program tells a debugger (`debug text: "..."`), and a fault in the processor's sense with where
  it happened. For that it needs the exception's record, which Frida does not pass on: the agent finds it on the
  thread's stack next to the saved registers, and takes it only if it holds the address the exception was raised
  at. Nothing is read that is not known to be there.
- The price: if the game gives up over a command (its own error window, or a crash), nothing stops that any more.
  Before, Frida would have kept such a game limping. The agent says that a command has begun before it calls the
  game, so the client knows which command the game was busy with when it went away (section 6).
- None of the describing has more than a stand-in behind it except what was seen live: thrown text, read
  correctly, from records where the agent looked for them. That is the case that matters most.

**Reading state.** Start with plain memory reads: map name, loading flag, Fontaine phase, Pawn stats, and later the
story-objective, Little Sister, station, diary and film reel state. Move to hooks (e.g. on pickup events) once the
reverse engineering finds them. Hooks are event-driven, so nothing is missed between polls.

**What Frida unlocks early.** The old "Phase 2 DLL" list becomes ordinary agent work:

- suppressing vanilla pickups, so world plasmids, tonics and weapons can be shuffled and safes can be checks
  (section 5)
- blocking bathysphere travel, for hard level locks
- Gatherer's Garden, research and Tenenbaum-gift checks

**Why not key binds.** A bind needs the game focused. A simulated key press can land in the wrong window, the bound key
can collide with the player's own input, binds don't fire in menus or while loading, and nothing confirms the command
ran.

**Why not an external `pymem` call.** Running engine code on a thread the client creates races the game thread. That is
the kind of bug that works for weeks and then corrupts something during a level load.

**Packaging (decided 2026-10-06): a helper in the player's own Python.** The Archipelago people install is a packaged
program. Packages cannot be added to its Python, and Python cannot load native modules from inside a zipped
`.apworld`. Frida is a native package. So the client does not import Frida at all. It starts
`client/frida_helper.py` in a Python that has Frida, and the two talk in JSON, one object per line, over the
helper's standard input and output.

- **What the player needs:** Python 3 from python.org and `pip install frida`. Nothing goes into the game folder
  or into Archipelago's.
- **Finding a Python:** in this order, the one that had Frida last time, the one the player named with `/python
  <path>` (remembered in Archipelago's own settings file), the environment variable `BIOSHOCK_AP_PYTHON`,
  Archipelago's own Python when it runs from source, the Python next to a `frida` command on the PATH, the `py`
  launcher, then `python` and `python3`. Whether one has Frida is found out by starting it; a Python without it
  says so and the next is tried. If none works, the client lists what it tried and why each failed. The PATH is
  searched by the client itself: only real programs count (no `.bat` wrappers on Windows), and never the current
  folder, which Windows would look in first.
- **Starting the helper** needs no file on disk: `python -c <one line>` reads the helper's text from the first line
  of its input and runs it. The one line also takes the current folder off the module path.
- **The conversation:** `setup` (process name and agent script), `attach`, `exec`, `action` one way; `hello`,
  `failed` (with a kind: no Frida, game not running, more than one game, refused by Windows, script broken, game
  closed meanwhile), `attached`, `state` ten times a second, `message`, `log` and `detached` the other.
  `frida_helper.py` documents it.
- **What it must never do, and how that is kept:**
  - *Stay behind.* The helper ends when its input closes, which is how the client stops it and also what happens
    by itself when the client dies. If its main thread is stuck in a call into a frozen game, a watchdog ends the
    process five seconds after the input closed. The client stops a helper that still has not gone after six.
  - *Leave the agent in the game.* On the way out, and after any failed call, it detaches the Frida session, which
    unloads the agent and its hook.
  - *Freeze the client.* Nothing in the client waits for the helper or the game. Starting, attaching and every call
    return at once; answers are picked up on the next pass of the client's loop. A game that stops answering for
    three seconds counts as "not ready" where it was last seen, and nothing more.
  - *Try for ever what cannot work.* A broken agent script, a helper that dies while attaching, no Python with
    Frida, and being refused by Windows each stop the automatic retries with an explanation; `/attach` tries
    again. (Being refused matters most: Frida may ask Windows for administrator rights each time, and a prompt
    every five seconds would be unbearable.) The game not running yet is retried every five seconds with the
    same helper, and said once.
- **Not tried on Windows yet.** All of this is tested on Linux with real helper processes and a stand-in `frida`
  package (section 6). Windows differs in ways the tests cannot show (console windows, pipe sizes, the `py`
  launcher); the code was written with those in mind, and one of them, a pipe that fills up when the other side
  does not read, is simulated in a test.

Ways to spare players the Python install later:

1. **Ship Frida inside the `.apworld`.** Frida's Windows wheel is a single native module (tens of megabytes) that works
   on every Python from 3.7; the client would unpack it to a cache folder and start the helper in Archipelago's own
   Python, if that can be made to run a script. Platform-specific and large, but no setup for the player.
2. **Frida Gadget** loaded by Ultimate ASI Loader (MIT, has x86 builds), running the agent in script mode and talking
   to the client over a localhost socket. No Python at all. Downside: a long-standing Frida issue (#1159) where the
   process lingers after quitting in Gadget script mode on Windows, and a file in the game folder.
3. **Port the agent to a small C++ DLL** (MinHook, loaded by the ASI loader). The logic carries over one-to-one, the
   footprint is smallest and there's no JS runtime. Iteration is slower, so do this only if Frida causes trouble.

Seen so far: Frida 17.22.2 installed with pip attaches to the 32-bit game from the command line. Whether any
antivirus objects to Frida's injector, or to a Python started with `-c`, is not known.

**Build safety.** Prefer signature scans to fixed addresses, and verify vtables before every call, as the VR mod does.
On an unknown build the agent should refuse and log, never guess. Steam and Epic addresses differ.

## 4. APWorld v0.1.4

- **Folder / file:** `worlds/bioshock` → `bioshock.apworld`.
- **Manifest:** `minimum_ap_version` 0.6.7, `world_version` 0.1.4.
- **Versions:** 0.1.1 added the client. 0.1.2 added story checks and turned audio diaries and film reels into
  optional extras. 0.1.3 changed only the client (the way into the real game, map names, item classes), and so
  does 0.1.4 (exceptions left to the game, the item table corrected and completed from the running game); seeds
  generate exactly as in 0.1.2.
- **Logic:** uses Archipelago's rule builder.

### Regions and logic

- Regions are `Menu` plus one per level, in story order: Welcome to Rapture, Medical Pavilion, Neptune's Bounty,
  Smuggler's Hideout, Arcadia, Farmer's Market, Fort Frolic, Hephaestus, Rapture Central Control, Olympus Heights,
  Apollo Square, Point Prometheus, Proving Grounds.
- With `level_access: access_items` (the default), each level's entrance requires its own access item. There are 8:
  - Neptune's Bounty Access (also covers Smuggler's Hideout)
  - Arcadia Access
  - Farmer's Market Access
  - Fort Frolic Access
  - Hephaestus Access (also covers Rapture Central Control)
  - Olympus Heights Access
  - Apollo Square Access
  - Point Prometheus Access (also covers Proving Grounds)
- Welcome to Rapture and Medical Pavilion are always open.
- **Gating is soft on purpose.** The player can still play the story straight through. The client holds a level's checks
  until the access item arrives, then sends everything already collected there. Logic is therefore non-cumulative: a
  level needs only its own item.
- If the agent later adds hard travel blocking, add an option that makes the access requirements cumulative.
- Shuffling world items (section 5) adds *physical* gates (e.g. Incinerate! for the Medical Pavilion ice wall). Those are
  cumulative however access items are set.
- **Goal:** the "Defeat Frank Fontaine" event in Proving Grounds, which needs Point Prometheus Access in access mode.

### Checks

| Type | Count | IDs | Option (default) | May hold | Detection plan |
|---|---|---|---|---|---|
| Story objectives | 33 | 7001–7033 | `story_checks` (on) | anything | the game's own record of finished goals (RE, section 7) |
| Little Sisters | 21 | 2001–2021 | `little_sister_checks` (on) | anything | k-th sister dealt with in the current level (rescue or harvest) |
| Power to the People | 12 | 4001–4012 | `power_to_the_people_checks` (on) | anything | k-th station used in the current level; vanilla upgrade still granted |
| Level completion | 12 | 5001–5012 (5000 + level index) | `level_completion_checks` (on) | anything | first story transition out of the level (level value) |
| Audio Diaries | 122 | 1001–1122 (diary number 1–122, story order) | `audio_diary_checks` (`filler_only`) | filler and traps only | diary list in player inventory (RE) |
| Director's Commentary reels | 10 | 3001–3010 | `directors_commentary_checks` (`filler_only`) | filler and traps only | reel pickup (RE) |

IDs 6001–6999 are kept for world items and containers (section 5).

**Why the collectibles are extras.** Diaries and reels are easy to walk past and some are well hidden, so nothing a
player needs should sit on one. The two options have three settings:

- `filler_only` (default): they are checks, marked `EXCLUDED`. Archipelago then never puts a progression or useful
  item there, from any player's game. A player who skips every one of them misses nothing that matters.
- `off`: they are not checks.
- `all`: they are ordinary checks.

Old YAMLs that say `true` or `false` are read as `all` and `off`.

**Story objectives.** Bosses and main objectives. Every one is needed to finish the game, so none can be missed:

| Level | Objectives |
|---|---|
| Welcome to Rapture | Inject Electro Bolt · Survive Ryan's Ambush |
| Medical Pavilion | Melt the Ice to Dental Services · Clear the Way to Surgery · Defeat Dr. Steinman |
| Neptune's Bounty | Photograph the Spider Splicers · Defeat Peach Wilkins |
| Smuggler's Hideout | Open the Submarine Bay |
| Arcadia | Bring Langford the Rosa Gallica · Open Langford's Safe · Collect the Chlorophyll Solution · Release the Lazarus Vector · Defend Langford's Lab |
| Farmer's Market | Collect the Distilled Water · Collect the Enzyme Samples |
| Fort Frolic | Photograph Kyle Fitzpatrick · Martin Finnegan · Silas Cobb · Hector Rodriguez · Complete Cohen's Masterpiece |
| Hephaestus | Find the Nitroglycerin · Assemble the EMP Bomb · Overload the Core |
| Rapture Central Control | Confront Andrew Ryan · Stop the Self-Destruct |
| Olympus Heights | Take the First Dose of Lot 192 |
| Apollo Square | Take the Second Dose of Lot 192 |
| Point Prometheus | Get the Big Daddy Voice Box · Collect the Big Daddy Pheromones · Find the Big Daddy Bodysuit · Helmet · Boots |
| Proving Grounds | Escort the Little Sister |

- Each has a fixed number in `data.py` (location ID 7000 + number), and a test pins all 33 IDs. A new objective gets
  the next free number; existing ones are never renumbered.
- Picking up weapons, plasmids and tonics is left out on purpose. Those become checks of their own in v0.2
  (section 5). Electro Bolt is the exception: it is a scripted story moment and stays vanilla.

**Other counts.**

- **Per-level Little Sisters:** Medical Pavilion 2, Neptune's Bounty 3, Arcadia 2, Farmer's Market 1, Fort Frolic 3,
  Hephaestus 3, Olympus Heights 2, Apollo Square 2, Point Prometheus 3.
- **Per-level stations:** Neptune's Bounty 1, Arcadia 1, Farmer's Market 1, Fort Frolic 2, Hephaestus 2,
  Olympus Heights 2, Apollo Square 1, Point Prometheus 2.
- **Reels:** Welcome to Rapture, Medical Pavilion, Neptune's Bounty, Farmer's Market, Fort Frolic, Hephaestus,
  Rapture Central Control, Apollo Square, Point Prometheus, Proving Grounds.
- Welcome to Rapture can't be revisited, so its two diaries and its reel are filler-only whatever the options say.
  Its two story checks can't be missed, so they are ordinary checks.

### Item pool (default: 210)

**Sized to fit.** Archipelago never puts progression or useful items on filler-only checks. So the world makes at most
as many of them as it has other checks, not counting locations the player excluded in their YAML. Otherwise a
one-player game could not be filled. The order is access items, plasmids, character upgrades, tonics; the first group
that does not fit is thinned at random, and the rest of the pool is filler.

**Default:** 78 checks may hold important items, so the pool has the 8 access items, all 18 plasmid items, all 32
upgrades and 20 of the 28 tonics (a different 20 per seed), plus 132 filler for the 132 collectibles. Tonics that are
left out are still bought, invented or researched in the game as usual.

- **Progression (8):** the level access items.
- **Useful, plasmids (18):**
  - Progressive Electro Bolt ×2, Progressive Incinerate! ×2 (level 1 of each is a vanilla pickup)
  - Progressive Winter Blast ×3, Progressive Insect Swarm ×3
  - Progressive Cyclone Trap ×2, Progressive Sonic Boom ×2, Progressive Hypnotize Big Daddy ×2
  - Enrage!, Target Dummy
- **Useful, character upgrades (32):** Health Upgrade ×8, EVE Upgrade ×8, Plasmid Slot ×4, and ×4 each of Physical,
  Engineering and Combat Tonic Slot.
- **Useful, gene tonics (up to 28):** every tonic that is bought, crafted, researched or gifted. The 30 tonics found
  at fixed spots stay vanilla.
- **Filler (the rest, weighted):** 10/25 ADAM, $25/$50/$100, First Aid Kit, EVE Hypo, Auto-Hack Tool, Ammo Bundle,
  Film, Invention Components.
- **Traps:** `trap_chance` percent of filler (default 0). EVE Drain, Pickpocket, Security Alarm. Traps take
  the place of filler, so with both collectible options `off` a one-player pool has no filler and no traps.
- **Reserved IDs, not shuffled yet:** Telekinesis, Security Bullseye, the 7 weapons (400–406) and the 12 weapon
  upgrades (450–461). The client can give all of them, so they already work with the server's `/send`.

### Options

| Option | Values | Default |
|---|---|---|
| `level_access` | `vanilla` / `access_items` | `access_items` |
| `story_checks` | toggle | on |
| `little_sister_checks` | toggle | on |
| `power_to_the_people_checks` | toggle | on |
| `level_completion_checks` | toggle | on |
| `audio_diary_checks` | `off` / `filler_only` / `all` | `filler_only` |
| `directors_commentary_checks` | `off` / `filler_only` / `all` | `filler_only` |
| `trap_chance` | 0–100 | 0 |
| `death_link` | toggle | off |

Archipelago's standard `start_inventory_from_pool` is supported too.

Presets: "No Collectibles", "Collectible Hunt" (both collectibles `all`), "Audio Diaries Only" and "Open Rapture".

**Settings that are refused.** Generation stops before it starts, with a message naming the player and the way out,
when:

1. every kind of check is switched off;
2. Level Access is on and fewer checks may hold an access item than there are access items to place (8, minus any
   that `start_inventory_from_pool` hands the player);
3. Level Access is on and none of those checks is open from the start. Welcome to Rapture and Medical Pavilion need
   no access item, so this means story, Little Sister and level completion checks are all off (or excluded there)
   and the collectibles are not `all`. Power to the People stations only begin in Neptune's Bounty. An access item
   in `start_inventory` lifts it.

Rule 3 applies in multiworlds too. Without it Archipelago's fill gives up whenever no other player has room for the
first access item either.

### Slot data (client contract)

```json
{
  "slot_data_version": 2, "world_version": "0.1.4",
  "level_access": 1, "story_checks": 1, "little_sister_checks": 1, "power_to_the_people_checks": 1,
  "level_completion_checks": 1, "audio_diary_checks": 1, "directors_commentary_checks": 1,
  "trap_chance": 0, "death_link": 0,
  "level_access_items": {"Welcome to Rapture": null, "Neptune's Bounty": "Neptune's Bounty Access", "...": "..."}
}
```

- `audio_diary_checks` and `directors_commentary_checks` are 0 (off), 1 (filler only) or 2 (all).
- The client does not read the check options. It sends only locations the server lists for the slot.
- Version 2 marks the story checks. A v0.1.1 client that meets it tells the player to update, because it would never
  send a story check.

### How the world was tested

- **Every combination of the check options** (288), each as a one-player game: it fills, can be finished and has
  nothing important on a filler-only check, or it is refused up front. 9 are refused.
- **Through Archipelago's real generator**, on 0.6.7 and 0.6.8-dev, from the packaged `.apworld`:
  - 40 mixed multiworlds: two BioShock players with random options next to three of 44 other games. All generated.
  - 1,000 seeds of four BioShock players with random options: 879 generated, 121 refused by the three rules above,
    none failed.
  - Across both batches about 160,000 filler-only checks, none holding an important item.
- **A second reviewer** tried unusual YAML settings (exclusions, priorities, start inventories, item links, local
  and non-local items) and reports more than 15,000 generated seeds. Nothing important ever landed on a collectible.
  Found and fixed: rule 3 only covered one-player games, an access item handed over by `start_inventory_from_pool`
  was still counted, Chlorophyll Solution was missing next to the other two Lazarus Vector ingredients, "Melt the
  Ice" could have meant the optional ice at Twilight Fields, and the slot data version had not been raised.
  After the fixes, the reviewer's own 3,000-seed run of two to four BioShock players gave 2,305 seeds and 695
  refusals, no failures.

**Known limits of generation.** These are generic Archipelago behaviour that this world is easy to hit with:

- **`priority_locations` on checks behind an access item** (for example the group "Power to the People") can make
  the fill fail. The priority fill has to put progression there, and with Level Access the only progression is the
  access items that gate those same checks.
- **The pool is an exact fit.** By default there is exactly as much filler as there are filler-only checks. Options
  that pin filler (`local_items` or `non_local_items` on filler or traps), or item links that swap filler for useful
  items, can leave Archipelago without a place for it.
- **`random` on every check option** rolls a refused combination about 3 times in 100 per player, which stops the
  whole multiworld with the message. Generating again rolls again.
- **Stardew Valley** places some spare copies of its items as filler and relabels them as progression once the seed
  is filled. One of those can show up on a diary. Nothing depends on it.

## 5. World items and containers (planned for APWorld v0.2)

**Goal:** more checks from the world itself. The plasmids, gene tonics and weapons placed around Rapture, plus its
safes, become checks, and the items that were there go into the multiworld pool.

**Does Frida make this possible? Yes.** It's the part that needs code running inside the game. Diaries only have to be
*detected*. A world item also has to be *suppressed*: grabbing Incinerate! from the Crematorium must send the check
without giving you Incinerate!. That means intercepting the game's own pickup code. An external memory reader can't do
that; an injected agent can.

### What becomes a check

| Category | Count | Proposed location IDs | Vanilla item goes to the pool? | Notes |
|---|---:|---|---|---|
| World plasmids | 3 | 6001–6003 | yes | Incinerate!, Telekinesis, Security Bullseye. Electro Bolt stays vanilla (tutorial) |
| World gene tonics | 30 | 6011–6040 | yes | loose, or in one specific container (e.g. Frozen Field is on Martin Finnegan's corpse) |
| Weapons | 7 | 6051–6057 | yes | Pistol, Machine Gun, Shotgun, Grenade Launcher, Research Camera, Chemical Thrower, Crossbow. The Pistol is in Welcome to Rapture, so its spot only gets filler |
| Safes | ~40 (3–4 per level, est.) | 6101–6199 | no, vanilla loot stays | opening or hacking the safe is the check. Some safes hold quest items (Langford's keypad safe has the Market Key), so contents must stay |
| Other containers | ~250 (est.) | 6201+ | no | desks, trash cans, crates, cash registers, placed corpses. A later "Loot Sanity" option |
| Loose consumables | hundreds | — | — | ammo, food and money lying around. Out of scope |
| Quest items | — | — | — | keys, Lazarus Vector parts, bomb parts, Big Daddy suit parts stay vanilla; they are too entangled with level scripts |

- **Pool impact:** 40 more items (3 plasmids, 30 tonics, 7 weapons) and about 80 more locations with safes on.
- **Uncertain counts:** safe and container counts come from walkthroughs; the agent's dump (below) replaces them with the
  real numbers.
- **Later candidates:** Gatherer's Garden purchases, Research Camera rewards and Tenenbaum's gifts work the same way.

### How the agent does it

1. **Map the world from the game itself.** Walk each level's actor list and dump every pickup and container: class,
   object name and position. That becomes the real location table and its IDs, with no guide errors. Turning object
   names into text needs the engine's name table (GNames), which can be found by its fixed first entries ("None",
   "ByteProperty", …).
2. **Find the one chokepoint.** Every item reaches the player's inventory through some grant path, whether it was lying
   loose or inside a container. Hooking there covers both.
   - **How to find it:** record the functions the game thread runs while you press Use on an item with Frida's Stalker,
     and diff against idle frames.
   - **Main candidate, `UObject::ProcessEvent`:** its address can be derived instead of guessed. `eventPlayerCalcView`,
     already found by the VR mod's technique, is a thin wrapper that calls ProcessEvent through the vtable, so reading
     its instructions with Frida's `Instruction.parse` gives the vtable slot.
3. **Intercept, don't just observe.**
   - Replace the grant function. If the item comes from a randomized location, skip the original call, send the check,
     and hide the pickup (turn off its visibility and collision).
   - ProcessEvent runs thousands of times per frame, so the filter has to be native code. Frida's CModule compiles a
     small C filter into the agent, and only matches reach JavaScript.
4. **Containers.**
   - Opening a safe for the first time sends its check (hook the open/search, or read its opened/hacked state).
   - If a container holds a shuffled unique item (a tonic on a corpse), remove that entry from the container's item list
     when the level loads.
5. **Stay consistent across saves.** Blocked pickups still exist in the level data. On every level load the agent
   re-applies the client's list of checked locations: hide collected pickups and strip shuffled items out of
   containers. Reloading a save never hands back a vanilla item.
6. **Treat scripted pickups separately.** Some pickups trigger story scripts, like the Electro Bolt tutorial and possibly
   the Incinerate! and Telekinesis tutorials or Cohen's Crossbow. Blocking those could stall the story. Instead let the
   script run, then remove the granted item and deliver the Archipelago item. Test each one by hand.

### Logic changes

- **New progression items, which are physical gates (unlike the soft access items):**
  - Incinerate! melts the Medical Pavilion ice wall.
  - Telekinesis clears the Medical Pavilion wreckage.
  - The Research Camera is needed for Peach Wilkins' photos in Neptune's Bounty and Cohen's photos in Fort Frolic.

  Every level after a gate needs the gate item, so these requirements are cumulative. A level's entrance becomes: its
  access item (soft) **and** all earlier physical gates **and** its combat tier.
- **Combat logic for weapons:** for example at least 1 firearm from Neptune's Bounty on, 2 from Arcadia, 3 from
  Hephaestus and 4 from Point Prometheus. Tune after playtesting.
- **Gates inside a level:** locations behind the ice wall need Incinerate!, and so on. That map comes from one
  playthrough with the agent's dump running. v0.2 splits levels into sub-regions where needed.
- **Story checks behind a gate** need the gate item too: "Melt the Ice to Dental Services" needs Incinerate!, "Clear
  the Way to Surgery" needs Telekinesis, the photo objectives need the Research Camera.
- **Early-game risk:** if Incinerate! is shuffled, the player can be stuck in Medical Pavilion with only a handful of
  checks before the ice wall. Add a `story_items: vanilla | early | anywhere` option. Default to `vanilla` until
  playtested; `early` forces those items into the first spheres.

### New options (v0.2)

- `world_item_checks`: plasmids, tonics and weapons. Off by default until suppression works.
- `safe_checks`: safes. These don't need suppression, so they can ship first.
- `story_items`: see above.
- `loot_sanity` (later): every placed container.

### Risks

- **Crashes from a wrong guess.** The VR mod decided against hooking ProcessEvent because a wrong guess crashes the
  game. Mitigation: derive the address instead of guessing, and run a log-only hook before blocking anything.
- **Breaking scripted sequences.** Blocking the wrong event can stall one. Mitigation: a per-pickup allowlist, and "let
  it happen, then swap" for scripted pickups.
- **Frame rate.** Mitigation: keep the hot-path filter in CModule.
- **Game versions.** Mitigation: find everything by signature or derivation, and refuse on a mismatch.

## 6. The client

### What is built (v0.1.4)

The client ships inside the `.apworld` and shows up in the Launcher as **BioShock Client**. Its Archipelago side is
finished. It does not need the game: `/simulate` swaps in a simulated game that is played with `/sim` commands, and
everything else (server, checks, items, held checks, goal) is real.

"Live" below means seen in the real game on the Steam build. "Built" means written and tested without the game.

| Requirement | Status |
|---|---|
| Log in, also with a password or from a room link | done |
| Give items one at a time, in order, counted only when the game confirms | done |
| Set aside items that cannot be given, and give them later | done |
| Hold a level's checks until its access item arrives, then send them | done |
| "Level Complete" checks from story progress | built on map names; not live yet |
| Goal, held like any other Proving Grounds check | built; the Fontaine phase value has not been seen live |
| Progress stored on the server, `/resync` | done |
| Reconnects, client restarts, a second seed in the same client | done |
| DeathLink both ways without echoing a death back | client done; the agent cannot kill the player or see a death yet |
| Traps | Security Alarm is a console command (`StartSecurityAlarm`): **live**, it rings for a minute, and no security bots were seen. The agent has no EVE Drain or Pickpocket yet |
| Attach to the game with Frida, hook the game thread | **live**, by hand |
| Run commands | **live**, by hand: engine commands through the engine, `GiveItem` and the other cheats through the local player |
| Leave what the game raises to the game, and pass it on | **live**: commands that raise something now run to their end, and the game's own words arrive |
| The client doing that by itself, from the packaged Archipelago | built (helper process, section 3); not live yet |
| Tell which level the player is in | map name read **live** (`1-medical`); the other names are the game's own map files, matched to levels by name |
| Game classes and commands for items | all of them. Every class named is in the running game's own list (**live**). Seen arriving: ADAM, a tonic, a plasmid slot, three second levels. Details below |
| Tell a class the game does not have from one it has | built on what the game was seen to say; not live in the client |
| Set an item aside when the game goes away in the middle of it | built; not live |
| Story objectives as checks | client done (the agent reports them by key, e.g. `steinman`); detection not started |
| Detect story objectives, Little Sisters, stations, diaries, reels | not started; needs reverse engineering |

### How it is put together

`worlds/bioshock/client/`:

| File | Job |
|---|---|
| `core.py` | Every decision: what to send to Archipelago, what to give the game, what to hold. No sockets, no game |
| `protocol.py` | The plain data that crosses the two seams: game state and events in, actions out |
| `game_data.py` | Tables: item → console command, game event → location ID, map → level, story order |
| `context.py` | The CommonClient wrapper and the `/` commands. Moves data between server, core and agent |
| `simulated.py` | A stand-in game with the same interface as the real agent |
| `frida_agent.py` | The real agent as the client sees it: finds a Python, starts the helper, translates what comes back |
| `frida_helper.py` | Runs in the player's Python, never in Archipelago's: holds Frida, loads `agent.js`, passes messages |
| `agent.js` | Runs inside the game: console commands on the game thread, state reads |

`components.py` registers the Launcher entry.

### Rules the client follows

- **Delivery.** Items go to the game in the order the server sent them, one at a time, and count as given only when
  the game answers. A command is never sent a second time while its first copy may still be waiting inside the game,
  so a long pause cannot make an item arrive twice. A refused command is tried three times. Nothing is sent while the
  game is loading or not in a level.
- **Set aside, not lost.** An item the client has no game class for, or that the game refused, is recorded as set
  aside. The first kind is given by itself once a newer client knows how. The second waits for `/retry`. Refused
  covers three more cases:
  - *The game says it has no such class.* The command comes back as handled, but while it ran the game raised
    `Failed to find object 'Class <the class the command named>'`. The client reports "the game has no class ..."
    and does not ask again. Only that sentence, about that class, counts: a game that fails to find something else
    on the way may still have given the item. The plainest case of that is routine: asked for
    `ShockDesignerClasses.ArmoredBodyTwo`, the game also looks for `ShockGame.ArmoredBodyTwo`, does not find it,
    and gives the item all the same. The client leaves that one line out of what it shows, and shows whatever
    else the game raises. (The sentence is the English one; whether a game set to another language words it
    differently is not known. If it does, a missing class passes for delivered, as before, and the routine line
    is shown.)
  - *The game goes away in the middle of the command.* The agent says when a command goes into the game and when
    it comes back. If the game is gone in between, that item is set aside: giving it again the moment the game is
    back would, if the command is what closed the game, close it again, every time. A command that was only
    waiting in the agent (a paused game, say) is simply given again. The client's log shows what the game raised
    just before, which is the best account of why there is.
  - *The agent's own code fails on the command.* It turns commands off and says which command it was.
- **Level access.** A found location is sent only once its level's access item has been received. Until then it is
  kept, and it goes out the moment the item arrives. The goal follows the same rule with Point Prometheus Access.
  Story objectives are checks of the level they happen in and follow the same rule.
- **Level completion.** A level is finished when the player first reaches a later one. Arcadia and Farmer's Market
  both finish on reaching Fort Frolic, because the story returns to Arcadia after the market.
- **Saved progress.** The count of items dealt with, the set-aside list, furthest level, found locations and goal are
  kept in Archipelago data storage under `bioshock_<team>_<slot>`. On reconnect the client's and the server's copies
  are merged and neither goes backwards, except for a `/resync` the player asked for.
- **Another seed.** Nothing the client remembers carries over when it connects to a different seed. CommonClient
  0.6.7 replays its own list of sent checks and the goal at every login, whatever the seed, so the client does not use
  that list. What the loaded save shows is another matter (see "Known limits").
- **Closing.** On a normal close the client starts no more items, waits up to a second for the one on its way to be
  confirmed, and saves. What can still make an item arrive twice is a crash in the instant between the game running
  the command and the client recording it.
- **DeathLink.** A received death kills the player once the game is ready, and the death that causes is not sent
  back. A death that arrives while no game is running, or that cannot be carried out within 30 seconds, is dropped.
  `/deathlink` holds for the rest of the session.

### Where the player is

**The map's name decides.** The agent reads it from the level object its hook is handed (`ULevel::URL.Map`, level
`+0x7C`) and reports the map of the level the player has a body in. It is the same however the player got there.
The client lower-cases it and strips the folder, `.bsm` and the language ending (`_int`, `_deu`, `_fra`, ...; every
map also exists in seven language versions).

| Map | Level |
|---|---|
| `0-lighthouse` | plane crash and lighthouse: a new game starts here, nothing is handed over yet |
| `1-welcome` | Welcome to Rapture |
| `1-medical` | Medical Pavilion (**read live**) |
| `2-fisheries` | Neptune's Bounty |
| `2-subbay` | Smuggler's Hideout |
| `3-arcadia` | Arcadia |
| `3-market` | Farmer's Market |
| `4-recreation` | Fort Frolic |
| `5-hephaestus` | Hephaestus |
| `5-ryan` | Rapture Central Control |
| `6-resi` | Olympus Heights |
| `6-slums` | Apollo Square |
| `7-science` | Point Prometheus |
| `7-gauntlet` | Proving Grounds |
| `7-bossfight` | the Fontaine fight |
| `entry`, `autoplay`, `museum`, `challengeroomcombat`, `challengeroomdecoy`, `challengeroomelectric` | not part of the story; nothing is handed over there |

The names are the game's own map files (listed from the Steam build on 2026-10-06). Only `1-medical` has been seen
in the running game; the others are matched to their levels by name, which is plain for all of them except perhaps
`6-resi` (residential: Olympus Heights) and `6-slums` (Apollo Square).

A map with a name the client does not know changes nothing: no level, no checks, and items are still handed over.

**The goal** needs the fight to be seen in the arena (phase 3) and then won (phase 4). Once the fight has been seen,
the client keeps watching for the win until the player is known to be somewhere else: in another map, on a loading
screen, or back at the main menu. A moment in which the game names no map (a pause, a hitch) changes nothing, so
that it cannot cost the player the goal; what is read during a loading screen is ignored, so that it cannot hand
one out. The phase value has not been watched in a real fight yet.

**The level number is only a fallback now.** The autosplitter's number is not an ID. In a game played straight
through its values follow a pattern (a fixed value when a level is entered and larger ones later; the engine's
growth rule `next = (n + 1) + 3 × (n + 1) / 8 + 32` reproduces all 14 second values the autosplitter lists), and the
client still knows that table. But in a loaded Medical Pavilion save it read 1030, which is in nobody's table, and
it was not the room in the level's actor list either (9,224 actors, room for 11,027). The client only looks at the
number when the agent is one that cannot name maps at all. From an agent that can, the number is dropped before
the client's logic sees it: where such an agent names no map (menu, pause, loading), the number could be anything,
another level's value included, and a wrong "Level Complete" cannot be taken back.

### Finding the local player

`GiveItem` has to go to the local player's own command entry. The agent gets there in steps, each checked against
the one before, and refuses to call anything if a check fails. **Every step was confirmed in the running game on
2026-10-06**, and the command then worked.

| Step | How | Measured live |
|---|---|---|
| The player object | Engine → its client object (`+0x4C`) → the first entry of the client's list of viewports (`+0x44`). That entry is the local player | client vtable `module+0xe4dbe0`; 1 viewport, room for 33 |
| Is it the player? | Its `Actor` field (`+0x48`) is a controller whose `Player` field (`+0x590`) points back, and the player's three vtables lie inside the game | vtables `module+0xe4e448`, `+0xe4e430` (output device, at `+0x40`), `+0xe4e338` (FExec, at `+0x44`) |
| The level | The hooked function is the one that ticks a level, so its `this` is a level. Its actor list is at `+0x44`, and the first actor is the LevelInfo, whose `Level` field (`+0xF8`) points at itself | level vtable `module+0xe0d90c`; the hook fires for the game level and for the engine's small "Entry" level |
| Is the player in it? | The controller's own `Level` is this level's LevelInfo, and it has a body: its `Pawn` (`+0x450`) points back at it (`+0x450`) | controller vtable `module+0xd81c84` |
| The call | Slot 0 of the vtable at player `+0x44`, as on the engine. Only in the tick of the level the player is in, and never while loading | Exec at `module+0x8525c0` |

- The player object lives as long as the game does, so it is looked for once. The controller is read from it fresh
  for every command, because a new one is made for every level.
- If the engine's route does not check out, the agent falls back on searching the level's actors for a controller
  whose HUD points back at it. That was the first version's way in, and it found the same player.
- Values that only look like pointers are never followed blindly. They are checked with `VirtualQuery` first, so
  that the agent never touches a guard page under a thread's stack.
- If no player is found within three seconds of the first tick, commands go to the engine alone and the agent says
  so. With the player found, a command waits (it is not handed to the engine alone) while a level is loading or the
  player has no body.
- `state()` says whether the player can be given anything right now (`playerReady`). The client hands over items
  only then, and only in a map where items belong.

### Which command gives which item

Three sources:

- **The running game's own list of classes** (section 2), since 2026-10-07. It says what exists where it matters.
  All 114 classes the client names are in it: 62 in `ShockGame`, and in `ShockDesignerClasses` the 38 higher
  levels, the 2 hypos and the 12 components. A test holds the client's table against the list. Names are compared
  without regard to capitals, as the game does (the guides write `SpringboardTrap`, the game
  `SpringBoardTrapTwo`).
- **The class list of the script packages** (section 2), exported from the files on disk. All 62 `ShockGame`
  classes the client names are in it.
- **Console guides for BioShock Remastered** (section 9). They say what each class is called in the game, and that
  the higher levels of plasmids and tonics live in a package of their own, `ShockDesignerClasses`.

**A mistake worth remembering.** The export lists six higher levels under `ShockGame`, all of them what the
Research Camera rewards: Static Discharge 2 (`ChargedBurstsTwo`), Photographer's Eye 2 (`EyeForDetailTwo`),
SportBoost 2 (`FastTwitchTwo`), Extra Nutrition 3 (`HealthyConsumerThree`), Wrench Jockey 2 (`MeleeMasterTwo`) and
Security Expert 2 (`SecuritySystemsExpertTwo`). In 0.1.3 the client gave those from `ShockGame`, on the reasoning
that a class known to exist is the safer one to name. The game disagreed: `GiveItem 1 ShockGame.FastTwitchTwo`
gave nothing and raised `Failed to find object`, and the running game's list has all six in the designer package,
where the guides had them all along. What a package's file declares and what the running game has are two
things, and only the second counts.

In the last column, "listed" means the class is in the running game's own list. That says it exists, not that
giving it does what is hoped. "Arrived" was seen in the game. "Taken" means the command ran and the game raised
nothing; nobody has reported yet whether the thing turned up.

| Items | Command | How far it is confirmed |
|---|---|---|
| ADAM, dollars | `GiveItem <n> ShockGame.ADAM`, `ShockGame.Credits` | ADAM **arrived**; dollars listed |
| Health and EVE Upgrade | `GiveItem 1 ShockGame.HealthUpgrade`, `ShockGame.BioAmmoUpgrade` | listed |
| Plasmid Slot; Physical, Engineering, Combat Tonic Slot | `GiveItem 1 ShockGame.ActiveGeneticSlotUpgrade`, `PhysicalGeneticSlotUpgrade`, `EngineeringGeneticSlotUpgrade`, `WeaponsGeneticSlotUpgrade` | Plasmid Slot **arrived**, with the game's window; the others listed |
| Plasmids, level 1 | `GiveItem 1 ShockGame.<Class>`: ElectricBolt, Incineration, Telekinesis, IcicleAssault (Winter Blast), InsectSwarmPlasmid, SpringboardTrap (Cyclone Trap), BerserkRage (Enrage!), SecurityBeacon (Security Bullseye), DecoyHuman (Target Dummy), AirBlast (Sonic Boom), SummonProtector (Hypnotize Big Daddy) | listed |
| Gene tonics, level 1 (31) | `GiveItem 1 ShockGame.<Class>`, e.g. ArmoredBody (Armored Shell), ChameleonBlood (Natural Camouflage), SuperHeated (Human Inferno), StationExpert (Safecracker), SlowFlow (Speedy Hacker). The full table is `TONIC_CLASS` in `game_data.py` | Armored Shell **arrived**, with the game's window for equipping or swapping; the others listed |
| Levels 2 and 3 of plasmids and tonics (38) | `GiveItem 1 ShockDesignerClasses.<Class>Two` or `...Three` | Armored Shell 2, Electro Bolt 2 and SportBoost 2 **arrived, each with the game's window**. (With the agent that cut commands short, the first two arrived without it and unequipped.) The others listed, Insect Swarm 2 and 3 as `InsectSwarmPlasmidTwo` and `...Three`. For each of these the game raises one `Failed to find object 'Class ShockGame.<Class>Two'` on the way, which means nothing |
| First Aid Kit, EVE Hypo | `GiveItem 1 ShockDesignerClasses.MedHypo`, `BioAmmoHypo` | EVE Hypo taken; First Aid Kit listed |
| Auto-Hack Tool, Film | `GiveItem 1 ShockGame.AutoHack`, `GiveItem 5 ShockGame.Film` | listed |
| Ammo Bundle | `GiveItem` three times: 12 `Pistol_Bullet`, 40 `MachineGun_Bullet`, 8 `Shotgun_00Buck` | listed; the amounts are a first guess |
| Invention Components | `GiveItem 1 ShockDesignerClasses.<Name>Component`, twelve times: Alcohol, Battery, BrassTube, ChlorophyllSolution, DistilledWater, EmptyHypo, EnzymeSample, Glue, Kerosene, RubberHose, ShellCasing, SteelScrew | listed (that is where the names come from); one of each is a first guess |
| Weapons (7) | `GiveWeapon ShockGame.<Class>`: Pistol, MachineGun, Shotgun, GrenadeLauncher, ResearchCamera, ChemicalThrower, Crossbow | Crossbow taken; all listed |
| Weapon upgrades (12) | `AddWeaponStatUpgrade <Weapon> <Stat>`: Pistol MagazineSize and Damage, MachineGun Damage and Kickback, Shotgun RateOfFire and Damage, GrenadeLauncher Damage and Immunity, ChemicalThrower ConsumptionRate and Range, Crossbow BreakageChance and Damage | Pistol Damage taken; the stat names are from guides alone |
| Security Alarm Trap | `StartSecurityAlarm` | **rang for a minute and ended by itself**, three times; no security bots came, in a level that was almost played out. To be tried in a fresher one |
| EVE Drain Trap, Pickpocket Trap | agent actions (`eve_drain`, `pickpocket`); the agent has none yet | not built |
| Level access items | nothing is sent; they only unlock checks | by design |

Things to keep in mind:

- **Progressive plasmids** give the class of the next level: with world pickups vanilla, the first copy of
  Progressive Electro Bolt is `ShockDesignerClasses.ElectricBoltTwo`.
- **A guide warns** against giving `ShockGame.ElectricBolt` to a player who already has it: the game tries to play
  the first-plasmid scene and fails. The default pool never gives level 1 of Electro Bolt or Incinerate!, but
  `/resync` into a save that already has what is being given again is the same situation for other items, and what
  the game does then is not known.
- **Classes in the list that look like items but are not obtainable in the finished game**, per the guides: Organic
  Pockets, Shutdown Expert (`ExtendedShutdown`), and about forty unused tonics. Parasitic Healing, Teleportation
  and Revival, which old guides also list, are not in the class list at all. None of these is in the item table.

### How it was tested

322 automated tests in Python, on Archipelago 0.6.7 and 0.6.8-dev, and again from the built `.apworld` loaded
as a zip, plus 61 for the agent under Node. The world's own tests are described in section 4.

- **Decision logic** against the simulated game: delivery, pauses, refusals, set-aside items, access, completion,
  goal, DeathLink, `/resync`, saved state, map names, and items that take several commands.
- **Whole seeds** from the real generator, played start to finish: in story order, rushed to the end and back, and
  with client restarts and dropped connections in between. Archipelago's own logic is the referee. A check sent
  before the world's rules allow it fails the test, and so does one still held once they do.
- **Without the collectibles.** A default seed is played to the goal without picking up a single diary or reel, in
  the core tests and against the real server: every access item and every useful item still arrives.
- **Against Archipelago's own server code.** `MultiServer` loads a generated seed and the real client logs in and
  plays it. Only the network socket is replaced, by an in-memory pipe that carries the same text. Covered: login,
  passwords, slot data, checks, items, data storage, lost connections, closing the client mid-delivery, a second seed
  in the same client, two players finishing each other's seeds, a release, DeathLink between two clients, and the
  client's own loop on the real clock.
- **The way into the game, without the game.** Real helper processes are started, in the Python the tests run in,
  with a stand-in `frida` package on their path. The pipes, the threads and the way a helper comes and goes are the
  real thing; only Frida and the game are not. The stand-in takes its orders from a file, so a test can have the
  game not running, start it, close it, freeze it, refuse a command or break the script while the helper runs.
  Covered, among others:
  - the whole conversation, and that nothing but the conversation is on the helper's output even when an old Frida
    prints what the script logs;
  - every way attaching can fail, and trying again with the same helper;
  - the game closing at each stage: before anything can listen for it, while the script loads, and later, with
    Frida's word on why arriving before or after the call that fails;
  - the helper leaving when its input closes: when the client closes it, when the client is killed outright, and
    when the helper's main thread is stuck in a frozen game;
  - Pythons tried in order (one that does not exist, one that stops at once, one without Frida, one that never
    answers), and a program that does not read its input being stopped all the same;
  - the helper running unchanged in Python 3.10, 3.11 and 3.12, written to parse as Python 3.7, importing nothing
    but the standard library and Frida;
  - server, client, helper and stand-in together: items sent by the server arrive as console commands in order,
    and walking into the next map turns into a "Level Complete" check;
  - the same four with a game that gives up over one item: the client says what the game raised, sets that item
    aside, finds the game again when it is back, carries on with the next item, and gives the first one only
    when `/retry` asks for it.
- **Against the game's own list.** Every class the client names is held against what the running game printed
  ("Which command gives which item", above), and a second test records where the export of the script package
  and the running game differ.
- **Deliberate bugs.** 204 one-line breakages in the client, the helper and the world (gate always open,
  count not saved, goal ignoring access, old checks replayed into a new seed, collectibles holding anything, a
  misspelt class name, levels two and three swapped, the helper staying behind, the same trouble announced every
  five seconds, a class the game does not have passing for delivered, and so on) were each caught by at least
  one test.
- **`agent.js`:** 61 tests under Node against a fake Frida runtime with a fake piece of game memory (levels,
  actors, a player, an engine that prints and raises), and 87 deliberate breakages that are each caught. One of
  the tests plants pointer-like rubbish that leads into a guard page and checks it is never read.
  - *The failure of 2026-10-07 is one of the tests.* The stand-in game raises an exception of its own while a
    command runs and deals with it. With Frida's default the stand-in ends the call there, as the real Frida
    did, and the agent of that day then prints the same two lines the real log has. With the present agent the
    command runs to its end and the exception is described.
  - What a stand-in cannot show is how Windows and Frida really hand an exception over: where the record of it
    lies, and what the registers hold. The agent looks in two places and takes nothing it cannot recognise. In
    the real game the first try found the record and read the text out of it.
- **A second reviewer** read the client against CommonClient and the Frida API and found real problems, all fixed and
  now tested: old checks and the goal replayed into the next seed, an item arriving up to three times after a long
  pause, set-aside items never arriving, `/deathlink` and an offline `/resync` being undone by a reconnect, a broken
  agent script being injected again every five seconds, and the goal being lost if the arena's actor list grew.
- **A reviewer for 0.1.3** read the helper, the adapter and the tables with fresh eyes, drove helpers directly, and
  reports running the Frida tests 53 more times, also under load, without a failure (14 more runs here afterwards,
  some four at a time, gave none either). Found, fixed and now tested:
  - a broken agent script would have been reported as "the game closed" with the real Frida, and injected again
    every five seconds. Letting go of a session ends it, Frida says so at once, and the helper read that as the
    game closing. The stand-in Frida said it a moment later, which hid the bug; it now behaves like the real one;
  - when the real game took over from the simulated one (or the other way round) while items were arriving, one
    item was given twice;
  - the six research rewards "named in the wrong package". This one was a wrong turn: the change it led to is the
    mistake described under "Which command gives which item", undone in 0.1.4;
  - being refused by Windows was retried every five seconds;
  - the level number could stand in for a missing map name;
  - `/python` undid a deliberate `/detach`; the current folder was searched for a Python on Windows; smaller
    things.

Not tested: a real network socket, anything on Windows, and the client's own link to the real game. The agent
script has been run in the game by hand (section 7); the client has never attached to it by itself.

**One agent file.** `client-poc/bioshock_ap_agent.js` and the apworld's `client/agent.js` are the same file
(version 2026-10-07.1). Two of its comments are behind the facts: the opening one still counts the game's printed
answers and what the game raises as "not seen yet", though both were seen with this very version (section 7), and
the one on named actions says the player object is still to be located. They are to be put right in the next
version of the agent and not in this one, so that one version number never stands for two different files.

### Known limits

- **A save is not tied to a seed.** Whatever the loaded save has done is reported to the room the client is connected
  to. Loading an old save while connected to a new seed would send that save's progress. A marker inside the save, or
  a confirmation step when a save looks too far along, would fix it.
- **The item count belongs to the slot, not to a save file.** Loading an older save or starting a new game needs
  `/resync`. Keeping the count inside the save would fix that, if a spare counter can be found in the game.
- **"Delivered" means the command ran and the game did not say it lacks the class.** Whether the item arrived, and
  did what it should, is not checked (section 3). For ADAM and dollars the agent could compare the player's
  numbers before and after; for the rest it would have to read the inventory.
- **The agent looks at every exception in the game's process**, on any thread, for as long as it is loaded, even
  though it only has something to say about those raised during its own commands. For each it does a few
  comparisons and answers "not mine". `state()` counts them (`exceptionsSeen`), so what this costs can be judged
  from a real session; `watch(false)` turns the describing off but not the looking.
- **Offline progress is kept in memory only.** What happens while disconnected from Archipelago is sent on reconnect,
  but is lost if the client is closed first (unless the game still shows it).
- Two clients on one slot would both give every item.
- The player has to install Python and Frida (packaging, section 3). Windows and the Steam build only.

### Still to build (all of it needs the game)

1. See the client and the item table work in the real game (section 7, "Next session"), and correct what does not.
2. Detect story objectives, Little Sisters and stations, then diaries and reels, and add them to the agent's
   `state()`. Story objectives come first: with the default options they and the Little Sisters hold most of what
   matters. The core already understands those fields; today only the simulated game fills them in. Section 7
   lists where to look.
3. Player death and kill for DeathLink (`Suicide` is a console command; a death should show in the health value),
   and the EVE Drain and Pickpocket traps (the player's EVE and dollars are at known offsets).
4. Item details:
   - Tonics, plasmids and slot upgrades open the game's own window in the middle of play. Fine for a pickup the
     player walks into; for an item that arrives out of nowhere, perhaps during a fight, it may want holding
     until a quiet moment.
   - What the game raises for a class of the designer package that does not exist (only tried with `ShockGame`).
   - Tell an item that arrived from a command that merely ran (a missing class is caught now; the rest is not).
   - What the game does with a plasmid or tonic the player already has, and above the slot caps.
   - Ammo Bundle and Invention Components amounts, and ammo for weapons the player owns.
   - Whether the alarm trap brings security bots where there is something to send them.
5. The other builds. On Steam, `UGameEngine::Exec` turned out to be slot 0 of the FExec vtable, so elsewhere it can
   be read from the object instead of from a fixed address. The engine pointer still has to be found there; the hook
   point has a signature. The VR mod found GOG laid out quite differently.
6. Tie a save to its seed, and keep the item count in the save (see "Known limits").
7. World items and safes (v0.2, section 5):
   - Send the agent the list of randomized pickups for the current level.
   - Report blocked pickups and opened safes as checks.
   - After every level load and save reload, re-send the checked locations so the agent can hide collected pickups and
     strip shuffled items from containers.

### Client ↔ agent messages

These travel through the helper, which passes them on as they are (section 3 has the helper's own words).

Built:

- client → agent:
  - `exec(id, command)` queues a console command.
  - `state()` returns `{agent, build, execAvailable, hookInstalled, engineReady, pending, levelValue, loading,
    inGame, fontainePhase, playerPath, playerReady, map, levels, watching, exceptionsSeen}`. The helper asks ten
    times a second.
    - `playerPath`: `unknown` while the local player is still being looked for, then `ready` or `unavailable`.
    - `playerReady`: the player has a controller and a body in a level that is ticking.
    - `map`: the map of that level, or null.
    - `levels`: what ticked in the last second, for diagnosis.
    - `watching`: whether what the game raises during a command is described. `exceptionsSeen`: how many
      exceptions the whole process has raised since the agent was loaded.
    - The client gives items only when commands are available, the hook is in, the engine is up, the game is not
      loading, a game is loaded, the player is ready and the map is one where items belong.
  - `action(id, name)` asks for a named action: `eve_drain`, `pickpocket`, `kill`. The agent answers "not
    supported" to all of them for now.
- agent → client, for one command, in this order:
  - `exec_started {id, command}`, the moment before the command goes into the game.
  - `exec_raised {id, command, what}`, once for each exception the game raises on the game thread while the
    command runs: at most 20 for a command, and the same one handed up from function to function is told once.
    `what` is one line, for example `C++ exception: "Failed to find object 'Class ShockGame.NoSuchThing'"`.
  - `exec_result {id, command, handled, via, output, outputLines, raised}`. `via` is `player`, `engine` or null.
    `output` is the start of what the game printed while the command ran (at most 20 lines; `outputLines` is the
    full count). `raised` is how many exceptions the game raised in all. With `fatal` and `reason` when the
    agent's own code failed on the command. If the game gives up over a command, no result follows.
- agent → client, otherwise:
  - `action_result {id, name, ok, reason}`, `exec_disabled {reason}`.
  - Whatever the agent logs is shown in the client. What the game printed in answer to a command, and what it
    raised meanwhile, are each summed up in one line.
- at the Frida prompt, the same is printed instead: `raised during "<command>": ...` as it happens, then who took
  the command, what the game printed, and how many exceptions it raised.

Later:

- `state()` grows `milestones` (the keys of the story objectives reached, e.g. `["electro_bolt", "ice_wall"]`),
  `little_sisters`, `stations`, `diaries` and `reels`, and the agent reports deaths.
- `set_randomized(level, pickups)`, `set_collected(locations)`.
- `pickup_blocked {level, class, name, position}`, `container_opened {…}`.

## 7. Verify on a PC (reverse-engineering checklist)

**Live test log**

- **2026-10-06, Steam build, Frida 17.22.2, agent loaded by hand with the `frida` command.** It printed:

  ```
  [bioshock-ap] build: Steam 1.0.127355
  [bioshock-ap] game-thread hook installed at module+0x53d850
  [bioshock-ap] FExec slot 0 is module+0x4c5970 (matches the fixed Exec address)
  [bioshock-ap] ready. Try give("get ShockPlayer Health"), then give("GiveItem 10 ShockGame.ADAM")
  ```

  What that confirms:
  - Frida attaches to the 32-bit game, and the script loads on Frida 17 unchanged.
  - The module size identifies the build.
  - The world-delta function's signature matches at the VR mod's fixed offset, so the hook is on the right function.
  - The engine pointer is set and its vtable is the expected one. Otherwise the agent would have turned commands off.
  - `UGameEngine::Exec` is slot 0 of the FExec vtable.

- **2026-10-06, same session: `give(...)` and `state()`.** Loaded save, standing in Medical Pavilion.

  | Typed | Result |
  |---|---|
  | `give("get ShockPlayer Health")` | `handled`. No crash |
  | `give("GiveItem 10 ShockGame.ADAM")` | `NOT handled`. No crash, no ADAM |
  | `state()` | `execAvailable`, `hookInstalled`, `engineReady` and `inGame` true, `loading` false, `pending` 0, `levelValue` 1030, `fontainePhase` 117 |

  What that settles:
  - Commands run inside the game, on the game thread, without harm, and run at once even with the game window in
    the background.
  - The engine's `Exec` does not hand `GiveItem` to the player. Items need the player's own command entry.
  - The level number is not 7039 or 9712 here, so it does not survive loading a save.
  - `fontainePhase` reads rubbish outside the final fight, as expected. The client only looks at it in the arena.

- **2026-10-06, agent 2026-10-06.2: the local player.** Same save, Medical Pavilion.

  | Typed | Result |
  |---|---|
  | (on loading) | `found the local player: controller 0x9949f6a0, player 0x27d90dc0` |
  | `probe()` | every step to the player confirmed (section 6, "Finding the local player"), plus the engine's own route to it |
  | `give("GiveItem 10 ShockGame.ADAM")` | `handled by the player`. **The ADAM arrived: 0 before, 10 after; dollars $126 before and after** |
  | `state()` | `playerPath` `ready`; `map` null; `levels` showing 9,224 actors with room for 11,027, and 16 with room for 47 |

  What that settles:
  - Items can be given. The local player's command entry is the right door, and the chain of checks to it holds on
    this build.
  - The map's name was one field further on than predicted: `+0x7C`, not `+0x78`. `probe()` printed the level's
    address fields: protocol `Bioshock` at `+0x60`, map `1-medical` at `+0x7C`, portal `MedicalStart` at `+0xA0`.
    The engine's own small level is called `Entry.bsm`. Fixed in agent 2026-10-06.3.
  - The level number (1030) is not the room in the actor list (11,027) either. It is not used any more where a map
    name is known.
  - The player's numbers sit where the older build had them: health, EVE, dollars (126) and ADAM (0, then 10).
    The game's code still contains the old instructions for health (once) and EVE (twice), not for dollars and ADAM.
  - The engine leads to the player directly: engine `+0x4C` → client → its viewport list at `+0x44` → first entry.
    Agent 2026-10-06.3 uses that first and keeps the search through the actors as a fallback.

- **2026-10-06, the game's files.** The names of all map files (section 6, "Where the player is") and the class
  list of the script packages (section 2), both from Teebs's PC.

- **Built after that, for 0.1.3:** agent 2026-10-06.4 (map at `+0x7C`, the engine's route, `playerReady`, and what
  the game prints in answer to a command), the helper process, the client's map table and the item table.

- **2026-10-07, agent 2026-10-06.4, three sessions: the first lines of the list below.** A loaded save, Medical
  Pavilion.

  | Typed | Result |
  |---|---|
  | `give("GiveItem 1 ShockGame.ArmoredBody")` | `handled by the player`. **The tonic arrived**, and the game opened its window for equipping it or swapping another out |
  | `give("GiveItem 1 ShockDesignerClasses.ArmoredBodyTwo")` | **The tonic was added to the inventory**, with no window. Then `commands disabled: the game faulted while running "GiveItem 1 ShockDesignerClasses.ArmoredBodyTwo" (system error)` |
  | `give("GiveItem 1 ShockDesignerClasses.ElectricBoltTwo")`, after a restart | The same: **granted, without any fanfare, and not equipped**, then the same error |

  What that settles:
  - `ShockDesignerClasses` is real and `GiveItem` reaches into it. 38 items hang on that.
  - The error was the agent's doing. "system error" is Frida's word for an exception that is not a fault of the
    processor, the kind a program raises on purpose, and the agent called the game in the way that takes such an
    exception away from it and ends the call (section 3). The missing window and the missing equip fit a command
    cut off right after the item was added. Which exception it was is not known: that agent could not say.

- **2026-10-07, agent 2026-10-07.1: exceptions left to the game, and described.** Two sessions, same save. The
  second has a log; the first is as Teebs told it.

  | Typed | Result |
  |---|---|
  | `give("GiveItem 1 ShockGame.FastTwitchTwo")` | `raised during "...": C++ exception: "Failed to find object 'Class ShockGame.FastTwitchTwo'"`, then `handled by the player`. **Nothing arrived** |
  | `give("GiveItem 1 ShockGame.ActiveGeneticSlotUpgrade")` | **A plasmid slot, with the game's window, as normal** |
  | `give("GiveWeapon ShockGame.Crossbow")` | `handled by the player`, nothing raised |
  | `give("AddWeaponStatUpgrade Pistol Damage")` | `handled by the player`, nothing raised |
  | `give("GiveItem 1 ShockDesignerClasses.BioAmmoHypo")` | `handled by the player`, nothing raised |
  | `give("StartSecurityAlarm")` | `handled by the player`. **The alarm rang for a minute.** Nothing was summoned; the alarm had been set off in that area once before |
  | `give("GiveItem 1 ShockGame.NoSuchThing")` | `raised during "...": C++ exception: "Failed to find object 'Class ShockGame.NoSuchThing'"`, then `handled by the player`, then `the game raised 1 exception of its own meanwhile, and carried on` |
  | `give("obj linkers")` | `handled by the player`. Printed `Linkers:` and nothing else |
  | `give("obj list class=class inside=ShockDesignerClasses")` | `handled by the player`. Printed every class in the game with its size, biggest first: about 5,040 lines, of which the agent kept 3,000 |

  What that settles:
  - Leaving exceptions to the game works. A command during which the game raises something runs to its end,
    commands stay on, and the agent reads the game's own words out of the exception. The record of the exception
    was where the agent looked for it.
  - **A wrong class can be told from a right one.** The game says `Failed to find object 'Class ...'`. The
    command still comes back as handled, so the sentence is the only sign there is, and the client now reads it.
  - **The six research rewards are not classes of `ShockGame` in the running game**, whatever the export of the
    package says. They are in `ShockDesignerClasses` with every other higher level.
  - **The game's own class list.** Every class the client names is in it; Insect Swarm 2 and 3 are
    `InsectSwarmPlasmidTwo` and `...Three`; the twelve U-Invent components are there by name. Kept as
    `reference/bioshock_runtime_classes.txt` (names only), with what the third session added.
  - `obj list` takes its conditions as "this or that": `class=class inside=<package>` lists every class and
    everything in the package. `obj linkers` has nothing to say in this build.
  - The alarm command ends by itself after a minute.
  - Still open after these two sessions: whether the crossbow, the pistol upgrade and the hypo turned up in the
    game (nothing was raised, and nobody looked).

- **2026-10-07, agent 2026-10-07.1, a third session: the second levels again.** Same save.

  | Typed | Result |
  |---|---|
  | `give("GiveItem 1 ShockDesignerClasses.ArmoredBodyTwo")` | `raised during "...": C++ exception: "Failed to find object 'Class ShockGame.ArmoredBodyTwo'"`, then `handled by the player`. **Granted, with its window** |
  | `give("GiveItem 1 ShockDesignerClasses.ElectricBoltTwo")` | the same, naming `ShockGame.ElectricBoltTwo`. **Granted, with its window** |
  | `give("GiveItem 1 ShockDesignerClasses.FastTwitchTwo")` | the same, naming `ShockGame.FastTwitchTwo`. **Granted, with its window** |
  | `give("obj list package=ShockDesignerClasses")` | `handled by the player`. Printed the 846 objects of that package and nothing else: 831 classes and 15 loot slots |
  | `give("StartSecurityAlert")` (a slip of the keys) | `NOT handled` |
  | `give("StartSecurityAlarm")` | `handled by the player`. The alarm again, and again no bots; the level is almost played out in this save |

  What that settles:
  - **Second levels work.** Left to finish, the command ends with the game's own window, for a tonic, a plasmid
    and a research reward alike.
  - **What the first agent choked on** was the game looking for the class in `ShockGame` as well, not finding
    it, and thinking nothing of it. One such exception comes with every item of the designer package. It is
    not a sign of anything, and the client does not show it.
  - A command the game does not have comes back `NOT handled`, as it should.
  - `obj list package=<package>` is the way to list one package. The 13 classes of the designer package that the
    long list had cut off are sounds and physics bodies; nothing for the item table.

**Next session**

*A. What is left to try at the Frida prompt.* In a save that does not matter, standing in a level, with the agent
that ships with 0.1.4: `frida -n BioshockHD.exe -l bioshock_ap_agent.js -o frida_log.txt` (the first line it prints
should say `agent 2026-10-07.1`). Each line is one command; after each, look at the game. A line starting `raised
during` is the game's own word on the command.

| # | Type | What it should do | What it settles |
|---|---|---|---|
| 1 | `give("GiveItem 1 ShockDesignerClasses.NoSuchThing")` | nothing; one or two `raised during` lines | **what the game says about a class the designer package does not have**. If it only names `ShockGame.NoSuchThing`, the client cannot tell such a slip from a real item |
| 2 | `give("GiveItem 5 ShockGame.Film")`, `give("GiveItem 12 ShockGame.Pistol_Bullet")` | film and pistol rounds | counted filler |
| 3 | `give("GiveItem 1 ShockDesignerClasses.BatteryComponent")` | one battery for the U-Invent | invention components |
| 4 | `give("StartSecurityAlarm")` in a level that still has its cameras and turrets | an alarm that brings security bots | whether the trap has teeth |
| 5 | `state()` at the main menu, in a second level, and with the game paused | | `inGame`, `map` and `playerReady` in those places; `exceptionsSeen` says how many exceptions the game raises when left alone |
| 6 | look for the crossbow, the pistol's damage upgrade and the EVE hypo given on 2026-10-07, or give them again | | whether "taken without complaint" means "arrived" |

None of these holds up part B.

*B. The first full run.* The 0.1.4 apworld, a solo seed with default options, hosted locally or on the website.

1. Start BioShock Client from the Launcher and connect to the room.
2. Start the game and load a save (or start a new game; items start arriving in Welcome to Rapture).
3. The client should say which Python and Frida it uses, then `Game connected: BioShock Remastered (Steam
   1.0.127355).` `/bioshock` should name the level.
4. From the server: `/send <slot name> Health Upgrade`, then a tonic, a weapon upgrade, an Ammo Bundle. Each should
   arrive within a second and the client should say `Delivered ...`. A line `While running "..." the game raised:
   ...` is the game's own word on a command and worth keeping.
5. Walk into the next level. The client should send "Level Complete" for the one just left.
6. Pause the game and send another item: it should wait and arrive on resume.
7. Close the client. The game should carry on undisturbed, and no `python.exe` should stay behind in Task Manager.
8. Keep the client's log (`logs/BioShockClient_<date>.txt` in the Archipelago folder).

*C. Two text extracts for check detection.* From the folder UE Explorer exported to, in a Command Prompt. Both
produce declarations only, no code, and that is all that is needed:

```
findstr /s /i /c:"exec function" *.uc > exec_functions.txt
findstr /s /i /r /c:"^var" /c:"^class " /c:"function " QuestManager.uc Quest.uc QuestLog.uc FactDatabase.uc PlayerStatsManager.uc AwardAchievementsManager.uc Collectable.uc DeveloperFilm.uc WeaponUpgradeStation.uc ActionAwardAchievement.uc ActionCompleteQuestObjective.uc MessagePlayerSavedGatherer.uc MessagePlayerCollectedGatherer.uc > declarations.txt
```

The first lists every console command the game has. The second shows what the classes that look like they keep
score are made of.

**Where to look for checks** (from the class list; nothing here has been tried)

| Check | Classes that look related |
|---|---|
| Story objectives | `ShockGame.Quest`, `QuestLog`, `QuestManager`, `ActionCompleteQuest`, `ActionCompleteQuestObjective`, `ActionInitiateQuest`; `FactDatabase`, `ActionAssertFact`; `Scripting.ActionAwardAchievement` (several objectives are achievements) |
| Little Sisters ("gatherers") | `ShockGame.MessagePlayerSavedGatherer`, `MessagePlayerCollectedGatherer`, `MessagePlayerFinishedHarvesting`, `MessagePlayerPacifiedGatherer`; `ShockAI.MessageGathererSaved`; `ShockGame.PlayerStatsManager` |
| Power to the People | `ShockGame.WeaponUpgradeStation`, `UpgradeableWeaponStat`, `MessagePlayerUsedMachine`, `MessagePlayerFinishedUsingMachine` |
| Audio diaries | `ShockGame.Collectable` (a guess), `SpeechManager`, `PlayerStatsManager` |
| Film reels | `ShockGame.DeveloperFilm` |
| Saves and loads | `ShockGame.ActionSaveGame`, `ActionAutoSave`, `Engine.MessageSavegameRestored`, `Engine.CheckpointInfo` |

Two routes look promising. The level scripts are built from `Action...` and `Message...` objects, so one hook on
whatever runs an action or sends a message would see objectives completed, sisters saved and machines used as they
happen, by name. And the engine registers every native function under a readable name (the VR mod found
`intUObjectexecGetPropertyTextByName` as a string in the game), which is a way to find the function behind a
declaration without guessing at addresses.

**Checklist**

- [x] Run the agent by hand: Frida attaches to the 32-bit game, `build: Steam 1.0.127355`, `game-thread hook
  installed`, `FExec slot 0 ... matches the fixed Exec address`, `give("get ShockPlayer Health")` handled.
- [x] Does `GiveItem` work through `UGameEngine::Exec`? **No.** Player-level commands need the local player's own
  entry.
- [x] Does `probe()` confirm every step to the local player, and does `give("GiveItem 10 ShockGame.ADAM")` then
  come back `handled by the player`, with the ADAM arriving? **Yes.**
- [x] From the same `probe()`: the map's name (`1-medical`, at `+0x7C`), the engine's own route to the player, and
  health, EVE, dollars and ADAM at the older build's offsets. The list's "room for" number is not the level number.
- [x] The name of every map: from the game's map folder.
- [x] Every class name in the script packages: exported with UE Explorer.
- [x] Does the game have the classes the client names? **Yes, all of them**, going by its own list. Six were named
  in the wrong package until 0.1.4.
- [x] Can a wrong class be told from a right one? **Yes**: `Failed to find object 'Class ...'`, raised while the
  command runs.
- [x] Does the alarm end by itself? **Yes**, after a minute.
- [x] What does the game do with a second level when the command is left to finish? **It gives it, with its
  window.**
- [ ] The rest of "Next session", part A.
- [ ] The client itself with the real game: "Next session", part B.
- [ ] `state()` in a few places. Do `loading` and `inGame` behave, and is `inGame` false at the main menu? What is
  `map` in the menu, and during a loading screen?
- [ ] Queue a command, then open the pause menu before it runs (or queue it while paused). Does it wait (`pending`
  stays 1 in `state()`) and run on resume, or does it run in the menu?
- [ ] What `GiveItem` does for a plasmid or tonic already owned, above slot caps, or with a higher level already owned.
- [ ] Whether higher tonic levels stack with lower ones or replace them.
- [ ] The Fontaine phase value during the real fight (3 in the fight, 4 when it is won?).
- [ ] **Where story progress lives.** The 33 story checks need the game's own record of finished objectives. See
  "Where to look for checks" above. Older ideas, none tried:
  - the goal list behind the Goals screen, if finished goals stay in it;
  - the log of radio messages, the same Messages screen that lists the audio diaries;
  - for the "collect" objectives, the quest items in the inventory (Rosa Gallica, Chlorophyll Solution, Distilled
    Water, Enzyme Samples, Nitroglycerin, Lot 192, the Big Daddy parts).

  Look for one source that covers as many as possible and note which objectives it misses. An objective that cannot
  be detected has to come out of the table before a release.
- [ ] Little Sister rescued/harvested counters and Power to the People station usage.
- [ ] Where collected-diary state lives (diary inventory list), and collected film reels.
- [ ] The Pawn's maximum health, maximum EVE and maximum dollars (the older build's `+0x774`, `+0xAFC`, `+0xAF0`).
- [ ] Per-level counts: Little Sisters, and Farmer's Market stations (sources say 1 or 2; the total should be 12).
- [ ] Health and EVE upgrade totals (one guide says 8 each).
- [ ] That Smuggler's Hideout and Rapture Central Control can be revisited.
- [ ] That the Proving Grounds reel can be collected before the final fight.

**For world items and safes (v0.2):**

- [ ] Find the name table (GNames) and walk a level's actor list. Dump every pickup and container per level (class,
  name, position). This settles the real counts.
- [ ] Derive `UObject::ProcessEvent` from `eventPlayerCalcView`'s code. Run a log-only hook to see which events fire
  when you pick something up, open a safe, and search a container.
- [ ] Trace one pickup with Stalker and find the inventory grant function, and how it knows which actor the item came
  from.
- [ ] Test blocking on one harmless world tonic first (e.g. Security Expert in Medical Pavilion): is the check sent, the
  item withheld, and the pickup hidden, and does that survive save/load?
- [ ] Find how a safe records being opened or hacked, and how a container stores its item list.
- [ ] Check each scripted pickup (Incinerate!, Telekinesis, Crossbow reward, Grenade Launcher conveyor) for script side
  effects before deciding block vs "let it happen, then swap".
- [ ] Map which locations sit behind the ice wall, the wreckage and the photo quests, for sub-region logic.

## 8. Roadmap

1. **Done:** rebuilt feasibility; the APWorld; the client's Archipelago side with a simulated game (v0.1.1); story
   checks and optional collectibles (v0.1.2); the in-game agent, which gives items in the real game by hand; the
   client's own way into the game, map names and the item table (v0.1.3, not run in the game yet); the first
   round of commands by hand, and what it taught: exceptions left to the game, a missing class caught, the item
   table held against the running game's own list (v0.1.4).
2. **Next, on a PC with the Steam build** (section 7, "Next session"):
   - the few commands still to try by hand
   - the first full run: client, real game, a real seed
   - the two text extracts
3. **Check detection:** story objectives first, then Little Sisters and stations, then diaries and reels. Then
   death and kill for DeathLink, and the two traps the agent still lacks.
4. **Friends playtest:** async multiworld, then fix counts and edge cases. The multiworld side can be rehearsed
   before step 3 with the simulated game.
5. **APWorld v0.2, world items and safes** (section 5), no separate DLL phase needed:
   1. Build the pickup and container dump, then generate the new location tables from it.
   2. Ship `safe_checks` first; it needs detection only.
   3. Add `world_item_checks` once blocking and save consistency work. Incinerate!, Telekinesis, the Research Camera and
      the weapons then become real progression, with the `story_items` option.
6. **Later:**
   - sparing players the Python install (section 3)
   - the GOG and Epic builds
   - hard level locks
   - Gatherer's Garden, research and Tenenbaum-gift checks
   - `loot_sanity`

## 9. Sources

- LiveSplit autosplitter for BioShock Remastered: https://github.com/PrototypeAlpha/BSR-ASL
- Cheat Engine table (v1.0.122283): https://github.com/CodeBlueDev/BioshockRemasteredCheatTable
- BioShock Remastered VR (DLL injection, in-process `UGameEngine::Exec`, engine map): https://github.com/BioVRDev/Bioshock-Remastered-VR (no license file, so learn from it and credit it, but don't copy code)
- Frida JavaScript API (`thiscall`/`stdcall` ABIs on Windows x86, `Interceptor`, `Memory.scanSync`, `Socket`; for 0.1.4 the `exceptions` option of `NativeFunction` and `Process.setExceptionHandler`): https://frida.re/docs/javascript-api/
- Frida Gadget (script mode config): https://frida.re/docs/gadget/ ; Windows script-mode lingering-process issue: https://github.com/frida/frida/issues/1159
- Ultimate ASI Loader (x86 proxy DLLs, MIT): https://github.com/ThirteenAG/Ultimate-ASI-Loader
- Safes and containers per level (walkthroughs): https://www.gamebanshee.com/bioshock/walkthrough/arcadia.php and https://portforward.com/games/walkthroughs/BioShock/Medical-Pavilion.htm
- Console commands via binds: https://steamcommunity.com/sharedfiles/filedetails/?id=842210214 and https://steamcommunity.com/app/409710/discussions/0/3425572470593675999/
- Which class is which item:
  - plasmids, tonics by track, slot upgrades, ammo, with in-game names (the same guide as above): https://steamcommunity.com/sharedfiles/filedetails/?id=842210214
  - higher levels in `ShockDesignerClasses`, weapon upgrades: https://steamcommunity.com/sharedfiles/filedetails/?id=2496257372
  - `GiveWeapon`, ammo, `AddWeaponStatUpgrade`: https://gameplay.tips/guides/6938-bioshock-remastered.html
- The class list of the game's script packages: exported from the Steam build with UE Explorer by Teebs, 2026-10-06
  (`reference/bioshock_classes.txt` in the project)
- The running game's own list of classes: printed by the game for `obj list` and read out through the agent by
  Teebs, 2026-10-07 (`reference/bioshock_runtime_classes.txt` in the project: the biggest 2,997 classes, and all
  831 of the designer package)
- Modding tools (UE Explorer, TFC Installer): https://www.nexusmods.com/bioshock/articles/1 and https://www.nexusmods.com/bioshock/mods/51
- 32-bit executable: https://steamcommunity.com/app/409710/discussions/0/1693795812297092525/
- No return to Welcome to Rapture: https://steamcommunity.com/app/409710/discussions/0/2268068817152903686/
- Audio diaries 1–100: https://steamcommunity.com/sharedfiles/filedetails/?id=764197488
- Per-level walkthrough (story objectives, diaries, Little Sisters, stations, reels): https://gamefaqs.gamespot.com/xbox360/931329-bioshock/faqs/81461
- Story objectives, cross-checks:
  - Welcome to Rapture ambush: https://www.gamebanshee.com/bioshock/walkthrough/welcometorapture.php and https://www.supercheats.com/guides/bioshock/welcome-to-rapture
  - Which ice is mandatory: https://gamefaqs.gamespot.com/xbox360/931329-bioshock/faqs/81461/medical-pavilion
  - Peach Wilkins must die for the way on to open: https://kb.speeddemosarchive.com/BioShock/Neptune's_Bounty
  - Lazarus Vector ingredients by level: https://www.gamebanshee.com/bioshock/walkthrough/farmersmarket.php and https://portforward.com/games/walkthroughs/BioShock/Arcadia.htm
- Director's Commentary reels: https://gamefaqs.gamespot.com/ps4/192289-bioshock-the-collection/faqs/74394
- Power to the People stations: https://gamefaqs.gamespot.com/xbox360/931329-bioshock/faqs/49892
- Upgrade and slot totals: https://gamefaqs.gamespot.com/xbox360/931329-bioshock/faqs/64031
- Plasmids, tonics and weapons (PC data incl. patch items): https://www.gamebanshee.com/bioshock/ (plasmids, combattonics.php, engineeringtonics.php, physicaltonics.php, weapons)
- Archipelago APWorld spec and rule builder: https://github.com/ArchipelagoMW/Archipelago/tree/main/docs
- Archipelago client and server source (`CommonClient.py`, `MultiServer.py`, 0.6.7 and main), for the client and its tests:
  https://github.com/ArchipelagoMW/Archipelago

## 10. Where the files are kept

- **The Claude Project** ("Bioshock 1 Remastered AP") holds every source file and this document. It is what a new
  session starts from.
- **GitHub:** https://github.com/TheTeebles/Bioshock-Remastered-AP-Mod, Teebs's own repository. It is to hold the
  same files under the same paths. Only there:
  - `README.md` at the top level. The Project keeps a copy as `github/README.md`.
  - `LICENSE` (CC0 1.0) and `.gitignore`, from when Teebs made the repository. The `.gitignore` is GitHub's one
    for Python, with the game's own file types added (`*.u`, `*.uc`, `*.bsm`) so that an export of the game's
    scripts cannot be committed by accident. It leaves out `dist/` and `build/`, so built files such as
    `bioshock.apworld` are not kept in the repository.
  - `.gitattributes`: text files are stored and checked out with LF line ends (`* text=auto eol=lf`), and
    `*.apworld` is binary.
- **How it gets there (2026-10-07).** The session of Claude's that did this work could not push to the
  repository, and without a token it could not read it either. Access was not switched on for the session, and
  the session had nothing to ask for it with. Teebs gave a token for that one repository; with it the session
  could clone, but its git gate refuses every push to a repository that is not on the session's own list,
  whatever the credential ("not in this session's authorized repository set ... add the repository to the
  session's sources"). That was not worked around, and the token was not used again. So the work goes over as a
  **git bundle**: a file holding the commits, made on top of the repository's own `main`. On the PC, inside a
  clone of the repository:

  ```
  git pull path\to\bioshock-ap.bundle claude/import-project
  git push
  ```

  The first line brings the commits in (onto `main`, as a fast-forward when nothing else has happened there),
  the second sends them to GitHub.
- **Later the same day** Teebs installed the Claude GitHub App on the repository, and the push was tried again.
  It was refused in the same words. The app may be needed, but it was not enough: the session's own list of
  repositories had not changed, and the session had no tool to ask for the repository with (the gate names one,
  `add_repo`). An app installed afterwards did not help a session that had begun without the repository.
- A session that has the repository among its sources can push by itself. One that can reach the repository
  should compare it with the Project before changing either one. One that cannot reach it has none of its
  commits, so a bundle made there would not join on to what is on GitHub: it should ask for the repository to be
  added to the session before it commits anything.

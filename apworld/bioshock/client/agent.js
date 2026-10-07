/*
 * BioShock Remastered x Archipelago: the in-game agent (runs inside BioshockHD.exe through Frida).
 *
 * What it does today:
 *   - runs console commands such as `GiveItem 10 ShockGame.ADAM` on the game thread, with no key presses
 *   - passes on what the game prints in answer to a command, and which exceptions it raises while the command runs
 *   - reports the map the player is in, the loading flag and the phase of the Fontaine fight
 *   - reports each Little Sister rescued or harvested, as a `little_sister` message naming the map
 *
 * Seen working on the real game (Steam build 1.0.127355, by hand, 2026-10-06 and 2026-10-07):
 *   - attaching, build detection and the game-thread hook
 *   - `get ...` through the engine's command entry
 *   - `GiveItem 10 ShockGame.ADAM` through the local player's command entry: handled, and the ADAM arrived
 *     (0 before, 10 after, on the game's own screen)
 *   - `GiveItem 1 ShockGame.ArmoredBody`: the tonic arrived, with the game's own window for equipping or swapping it
 *   - `GiveItem 1 ShockDesignerClasses.ArmoredBodyTwo`: the tonic arrived, so classes of that package can be given.
 *     The agent of that day then lost the command half way; see "exceptions" below
 *   - every step probe() reports: the level, its LevelInfo, the player controller, the player object, the map name,
 *     and the player's health, EVE, dollars and ADAM at the offsets below
 * Not seen yet: any text coming back from a command, and anything about the exceptions the game raises (new in
 * this version).
 *
 * How commands run (technique and Steam addresses from the BioShock Remastered VR mod,
 * https://github.com/BioVRDev/Bioshock-Remastered-VR):
 *   - A command is sent to an FExec object: Exec(const TCHAR* Cmd, FOutputDevice& Ar), __thiscall, slot 0 of the
 *     object's FExec vtable. The engine is one (its FExec part is at engine + 0x40). It only takes engine commands.
 *     The local player is another, and that is where cheats such as GiveItem are handled. The agent asks the
 *     player first and the engine second.
 *   - Engine code must only run on the game thread, so commands are queued and run from a hook on the function that
 *     ticks a level once per frame. At most one command runs per tick.
 *   - Before every call the objects involved are checked against each other. Where they do not fit, the agent
 *     refuses instead of crashing.
 *   - Exceptions raised during the call are left to the game. A program of this kind raises exceptions as routine
 *     and deals with them itself, and Frida's default is to take every exception raised during a call away from
 *     the program, which abandons the command half done. That happened on 2026-10-07: the tonic was added, Frida
 *     reported "system error", and the rest of the command never ran. The agent now only watches: what is raised
 *     on the game thread while a command runs is described and passed on, and the game gets every one of them.
 *
 * Where the other state values come from: the LiveSplit autosplitter, https://github.com/PrototypeAlpha/BSR-ASL.
 * Its level number is only right in a game played straight through, not after loading a save. Use `map`.
 *
 * Try it by hand (Windows, Steam build, game running and loaded into a level):
 *   pip install frida-tools
 *   frida -n BioshockHD.exe -l <path to this file>
 * then type at the prompt:
 *   probe()                              prints what the agent found in the game (reads only)
 *   give("GiveItem 10 ShockGame.ADAM")   gives 10 ADAM
 *   give("get ShockPlayer Health")       any console command; what the game prints in answer is shown
 *   state()                              what the agent can see
 *   watch(false)                         stops reporting what the game raises during a command; watch() resumes
 */
'use strict';

const AGENT_VERSION = '2026-10-07.2';
const GAME = 'BioshockHD.exe';

// Engine addresses for running commands. Only measured on the Steam build so far.
const STEAM_EXEC = {
  enginePtrRva: 0x1375368, // global UGameEngine*
  engineVtableRva: 0xE0DFF4, // UGameEngine vtable, used to verify the pointer before calling
  engineExecRva: 0x4C5970, // UGameEngine::Exec
  execThisOffset: 0x40, // FExec sub-object inside UGameEngine
  deltaFnRva: 0x53D850, // the function that ticks a level (game thread)
};

// Where things sit inside the game's objects, on the Steam build. "VR mod" was measured by the VR mod. "live" was
// predicted from how the engine declares its classes and then confirmed against the running game with probe()
// (2026-10-06). All of it is checked against the live objects again every time before it is relied on.
const LAYOUT = {
  levelActors: 0x44, // live: ULevel's actor list {data, count, max}. The hooked function reads it first thing
  levelMap: 0x7C, // live: ULevel::URL.Map, the name of the map ("1-medical")
  actorLevel: 0xF8, // VR mod: AActor::Level, the level's LevelInfo. A LevelInfo points at itself
  controllerPawn: 0x450, // VR mod: AController::Pawn
  pawnController: 0x450, // VR mod: APawn::Controller
  controllerPlayer: 0x590, // live: APlayerController::Player, the first field after AController's own
  controllerHud: 0x71C, // VR mod: APlayerController::myHUD
  hudOwner: 0x470, // VR mod: AHUD::PlayerOwner
  playerOutput: 0x40, // live: UPlayer is a UObject (0x40 bytes), then an FOutputDevice, ...
  playerExec: 0x44, // live: ... then an FExec, the part commands are sent to ...
  playerActor: 0x48, // live: ... then UPlayer::Actor, the controller this player drives
  engineClient: 0x4C, // live: UEngine::Client
  clientViewports: 0x44, // live: UClient's list of viewports {data, count, max}; the first one is the local player
};

// The player's numbers, as a Cheat Engine table found them on the older 1.0.122283 build
// (https://github.com/CodeBlueDev/BioshockRemasteredCheatTable). probe() checks whether this build's code still uses
// the same offsets and shows the values, so they can be compared with the screen. Nothing relies on them yet.
const PAWN_STATS = [
  { name: 'health', offset: 0x57C, float: true, code: 'F3 0F 11 86 7C 05 00 00 F3' },
  { name: 'EVE', offset: 0xAF8, float: true, code: 'F3 0F 11 86 F8 0A 00 00 F3' },
  { name: 'dollars', offset: 0xAEC, float: false, code: '29 86 EC 0A 00 00' },
  { name: 'ADAM', offset: 0xAF4, float: false, code: '29 9E F4 0A 00 00' },
];

// How the engine describes its objects and classes, on the Steam build (found with client-poc/probe_script_calls.js,
// 2026-10-07). The name table is an array of entries with the name's text (UTF-16) at entry + nameText. An object
// keeps its name's index and its class; a class keeps the class it extends and its first field; a field keeps the
// next one; a property keeps its offset into an object. All of it is checked against names before it is used.
const STEAM_OBJECTS = {
  names: 0x13904EC,
  nameText: 0x10,
  objectName: 0x28,
  objectClass: 0x30,
  classSuper: 0x40,
  structChildren: 0x5C,
  fieldNext: 0x44,
  propertyOffset: 0x74,
};

// Known builds, told apart by module size. State paths are [module offset, then pointer offsets...]; the last
// offset is where the value itself lives.
const BUILDS = {
  23556096: {
    name: 'Steam 1.0.127355',
    exec: STEAM_EXEC,
    level: [0x1386004],
    loading: [0x1356680],
    inGame: [0x1356620, 0x214, 0x6E8, 0x38],
    fontainePhase: [0x1356200, 0x0, 0x14, 0x1C, 0x1148],
    objects: STEAM_OBJECTS,
  },
  23552000: {
    name: 'Epic 1.0.127355',
    exec: null, // the VR mod found the Steam addresses do not fit this build
    level: [0x13853E8],
    loading: [0x1355A68],
    inGame: [0x1355A08, 0x214, 0x6E8, 0x38],
    fontainePhase: [0x13555DC, 0x0, 0x18, 0x1C, 0x1148],
  },
  24207360: {
    name: 'GOG / old Steam 1.0.122872',
    exec: null,
    level: [0x130287C],
    loading: [0x12D2730],
    inGame: [0x12D270C, 0x214, 0x6E8, 0x38],
    fontainePhase: [0x12D22C4, 0x0, 0x14, 0x1C, 0x1148],
  },
};

// Byte signature of the level-tick function ('??' = the SEH scope-table address, which moves between builds).
const DELTA_SIGNATURE =
  '55 8B EC 6A FF 68 ?? ?? ?? ?? 64 A1 00 00 00 00 50 64 89 25 00 00 00 00 83 EC 44 53 56 8B F1 ' +
  'C7 45 E4 00 00 00 00 57 89 75 DC 8B 46 44 8B 38';
const DELTA_SIGNATURE_LENGTH = 47;

const MIN_INTERVAL_MS = 50; // spread bursts out; the tick function fires more than once per frame
const SEARCH_INTERVAL_MS = 500; // how often a level is searched for the local player while it is still unknown
const SEARCH_PATIENCE_MS = 3000; // after this long without finding it, commands go to the engine alone
const SLOW_SEARCH_INTERVAL_MS = 5000; // ... and the search carries on at this pace
const MAX_ACTORS = 200000; // more than any level has; an actor count above this means the layout does not fit
const MAX_OUTPUT_LINES = 3000; // how much of a command's printed answer is kept
const MAX_LINE_LENGTH = 300;
const MAX_LINES_TO_CLIENT = 20; // how much of it travels with the result; the prompt shows all that was kept
const MAX_RAISED = 20; // how many of the exceptions the game raises during one command are described and passed on

// How the game's command entry is called. `exceptions: 'propagate'` leaves what is raised during the call to the
// game, which catches its own. Frida's default ('steal') would take every one of them away from it.
const EXEC_CALL = { abi: 'thiscall', exceptions: 'propagate' };

const mod = Process.getModuleByName(GAME);
const base = mod.base;
const moduleStart = base.toUInt32();
const moduleEnd = moduleStart + mod.size;

function log(message) {
  console.log(`[bioshock-ap] ${message}`);
}

// An unknown build still gets the Steam command addresses: the vtable check decides whether they are usable.
const build = BUILDS[mod.size] || { name: `unknown (module size ${mod.size})`, exec: STEAM_EXEC };
const execInfo = build.exec;
log(`agent ${AGENT_VERSION}, build: ${build.name}`);

// ---------------------------------------------------------------------------------------------------------------
// Reading memory
// ---------------------------------------------------------------------------------------------------------------

// Plain reads, for addresses that are known to be real: fields of an object the game itself is using.
function ptrAt(address) {
  try {
    return address.readPointer();
  } catch (e) {
    return null;
  }
}

function s32At(address) {
  try {
    return address.readS32();
  } catch (e) {
    return null;
  }
}

function same(a, b) {
  return a !== null && b !== null && a.equals(b);
}

function inModule(pointer) {
  if (pointer === null) {
    return false;
  }
  const value = pointer.toUInt32();
  return value >= moduleStart && value < moduleEnd;
}

function where(pointer) {
  if (pointer === null) {
    return 'unreadable';
  }
  return inModule(pointer) ? `module+0x${(pointer.toUInt32() - moduleStart).toString(16)}` : `${pointer}`;
}

// Careful reads, for values that only look like pointers. Merely touching a guard page (the page under a thread's
// stack) disarms it, so such an address is first looked up with VirtualQuery and only ordinary, committed memory is
// read. Without VirtualQuery nothing that needs a careful read is attempted.
const QUERY_SIZE = 28; // MEMORY_BASIC_INFORMATION in a 32-bit process
const virtualQuery = findVirtualQuery();
const queryBuffer = Memory.alloc(QUERY_SIZE);
const pageVerdicts = new Map();

function findVirtualQuery() {
  try {
    const address = Process.getModuleByName('kernel32.dll').getExportByName('VirtualQuery');
    return new NativeFunction(address, 'uint', ['pointer', 'pointer', 'uint'], 'stdcall');
  } catch (e) {
    return null;
  }
}

function readable(pointer) {
  if (virtualQuery === null || pointer === null) {
    return false;
  }
  const value = pointer.toUInt32();
  if (value < 0x10000) {
    return false;
  }
  const page = value >>> 12;
  let verdict = pageVerdicts.get(page);
  if (verdict === undefined) {
    verdict = false;
    try {
      if (virtualQuery(pointer, queryBuffer, QUERY_SIZE) !== 0) {
        const state = queryBuffer.add(16).readU32();
        const protect = queryBuffer.add(20).readU32();
        const access = protect & 0xFF;
        const committed = state === 0x1000;
        const guarded = (protect & 0x100) !== 0;
        verdict = committed && !guarded && access !== 0 && access !== 0x01 && access !== 0x10;
      }
    } catch (e) {
      verdict = false;
    }
    if (pageVerdicts.size > 8192) {
      pageVerdicts.clear();
    }
    pageVerdicts.set(page, verdict);
  }
  return verdict;
}

// Reads a pointer-sized value at an address that may not be real. Null when it is not safe or not possible.
function carefulPtr(address) {
  if (address === null || (address.toUInt32() & 3) !== 0 || !readable(address)) {
    return null;
  }
  return ptrAt(address);
}

function carefulS32(address) {
  const value = carefulPtr(address);
  return value === null ? null : value.toInt32();
}

// An engine string is {data, count, max}; count includes the closing zero. Null unless it is plain printable text.
function stringAt(address) {
  const data = carefulPtr(address);
  const count = carefulS32(address.add(4));
  const max = carefulS32(address.add(8));
  if (data === null || count === null || max === null || count < 2 || count > 200 || max < count) {
    return null;
  }
  if (!readable(data) || !readable(data.add((count - 1) * 2))) {
    return null;
  }
  try {
    const text = data.readUtf16String(count - 1);
    return /^[\x20-\x7E]+$/.test(text) ? text : null;
  } catch (e) {
    return null;
  }
}

// Follows a pointer path and reads the value at its end. Returns null if any step is missing or unreadable.
function readPath(path, read) {
  if (!path) {
    return null;
  }
  try {
    let address = base.add(path[0]);
    for (let i = 1; i < path.length; i++) {
      const next = address.readPointer();
      if (next.isNull()) {
        return null;
      }
      address = next.add(path[i]);
    }
    return read(address);
  } catch (e) {
    return null;
  }
}

// ---------------------------------------------------------------------------------------------------------------
// Levels and the local player
// ---------------------------------------------------------------------------------------------------------------

// The hooked function ticks one level, so its `this` is that level. It runs for every level that is loaded: the
// one being played and the engine's small "entry" level.
//
// The first actor of a level is its LevelInfo, and a LevelInfo's Level field points at itself. That is the check
// that `this` really is a level with the layout this agent expects. The first two reads are the ones the hooked
// function itself is about to make.
function levelInfoOf(level) {
  const actors = ptrAt(level.add(LAYOUT.levelActors));
  const count = s32At(level.add(LAYOUT.levelActors + 4));
  if (actors === null || actors.isNull() || count === null || count < 1 || count > MAX_ACTORS) {
    return null;
  }
  const info = ptrAt(actors);
  if (info === null || info.isNull()) {
    return null;
  }
  return same(carefulPtr(info.add(LAYOUT.actorLevel)), info) ? info : null;
}

// Every actor of a level, and their addresses as a set. Only call this for a level that passed levelInfoOf().
function actorsOf(level) {
  const data = ptrAt(level.add(LAYOUT.levelActors));
  const count = s32At(level.add(LAYOUT.levelActors + 4));
  const actors = [];
  const addresses = new Set();
  for (let i = 0; i < count; i++) {
    const actor = ptrAt(data.add(i * 4));
    if (actor !== null && !actor.isNull()) {
      actors.push(actor);
      addresses.add(actor.toUInt32());
    }
  }
  return { actors, addresses };
}

// The player controllers of a level: actors whose HUD is an actor of the same level that points back at them.
// Nothing is followed here except pointers to actors the level itself lists.
function playerControllersOf(level) {
  const { actors, addresses } = actorsOf(level);
  const found = [];
  for (const actor of actors) {
    const hud = carefulPtr(actor.add(LAYOUT.controllerHud)); // past the end of most actors, hence careful
    if (hud === null || hud.isNull() || !addresses.has(hud.toUInt32())) {
      continue;
    }
    if (same(carefulPtr(hud.add(LAYOUT.hudOwner)), actor)) {
      found.push(actor);
    }
  }
  return { controllers: found, addresses, actorCount: actors.length };
}

// Is this the player object that drives this controller? Each must point at the other, and the player's three
// vtables (its own, the FOutputDevice one and the FExec one) must belong to the game.
function isPlayerOf(player, controller) {
  if (player === null || player.isNull() || controller === null || controller.isNull()) {
    return false;
  }
  return same(carefulPtr(player.add(LAYOUT.playerActor)), controller) &&
    same(carefulPtr(controller.add(LAYOUT.controllerPlayer)), player) &&
    inModule(carefulPtr(controller)) &&
    inModule(carefulPtr(player)) &&
    inModule(carefulPtr(player.add(LAYOUT.playerOutput))) &&
    inModule(carefulPtr(player.add(LAYOUT.playerExec)));
}

// The engine's own route to the local player: engine -> client -> first viewport. Cheap, and it does not depend on
// which level is loaded. The result is only used if the player and its controller point at each other.
function playerFromEngine() {
  if (execInfo === null) {
    return null;
  }
  let engine;
  try {
    const self = engineExecThis(); // also checks that the engine is the one these offsets were measured on
    engine = self === null ? null : self.sub(execInfo.execThisOffset);
  } catch (e) {
    return null;
  }
  if (engine === null) {
    return null;
  }
  const client = ptrAt(engine.add(LAYOUT.engineClient));
  if (client === null || client.isNull() || !inModule(carefulPtr(client))) {
    return null;
  }
  const count = carefulS32(client.add(LAYOUT.clientViewports + 4));
  if (count === null || count < 1 || count > 16) {
    return null;
  }
  const player = carefulPtr(carefulPtr(client.add(LAYOUT.clientViewports)));
  if (player === null || player.isNull()) {
    return null;
  }
  return isPlayerOf(player, carefulPtr(player.add(LAYOUT.playerActor))) ? player : null;
}

let viewport = null; // the local player's object. The engine keeps one for as long as the game runs
// 'unknown' while still looking, then 'ready' or 'unavailable'. Without careful reads there is nothing to look with.
let playerPath = virtualQuery === null ? 'unavailable' : 'unknown';
if (virtualQuery === null) {
  log('VirtualQuery was not found, so the local player is not looked for and commands go to the engine alone.');
}
let firstTickAt = 0;
const lastSearch = new Map(); // level address -> when it was last searched
const levelsSeen = new Map(); // level address -> what the last look at it showed
const execFunctions = new Map(); // address of an Exec function -> something that can call it

function lookForPlayer(level, now) {
  if (viewport !== null || virtualQuery === null) {
    return;
  }
  const direct = playerFromEngine();
  if (direct !== null) {
    viewport = direct;
    playerPath = 'ready';
    log(`found the local player through the engine: player ${direct}. Commands go to the player first.`);
    return;
  }
  if (playerPath === 'unknown' && now - firstTickAt > SEARCH_PATIENCE_MS) {
    playerPath = 'unavailable';
    log('could not find the local player, so commands go to the engine alone. Run probe() and send what it prints.');
  }
  const key = level.toString();
  const interval = playerPath === 'unavailable' ? SLOW_SEARCH_INTERVAL_MS : SEARCH_INTERVAL_MS;
  if (lastSearch.has(key) && now - lastSearch.get(key) < interval) {
    return;
  }
  if (lastSearch.size > 8) {
    lastSearch.clear();
  }
  lastSearch.set(key, now);
  pageVerdicts.clear();

  for (const controller of playerControllersOf(level).controllers) {
    const player = ptrAt(controller.add(LAYOUT.controllerPlayer));
    if (isPlayerOf(player, controller)) {
      viewport = player;
      playerPath = 'ready';
      log(`found the local player among the actors: controller ${controller}, player ${player}. ` +
        'Commands go to the player first.');
      return;
    }
  }
}

// Where to send a command so that it reaches the local player, or null if that is not possible in this tick.
// Everything is checked again on every call: the player's controller is replaced whenever a level loads.
function playerTarget(level, info) {
  if (viewport === null || info === null) {
    return null;
  }
  const controller = ptrAt(viewport.add(LAYOUT.playerActor));
  if (controller === null || controller.isNull() || !isPlayerOf(viewport, controller)) {
    return null;
  }
  if (!same(carefulPtr(controller.add(LAYOUT.actorLevel)), info)) {
    return null; // the controller lives in another level than the one being ticked right now
  }
  const pawn = carefulPtr(controller.add(LAYOUT.controllerPawn));
  if (pawn === null || pawn.isNull() || !same(carefulPtr(pawn.add(LAYOUT.pawnController)), controller)) {
    return null; // no body to give anything to: a menu, or the moment between two levels
  }
  const self = viewport.add(LAYOUT.playerExec);
  const exec = carefulPtr(ptrAt(self)); // slot 0 of the FExec vtable, as on the engine
  if (!inModule(exec)) {
    return null;
  }
  const key = exec.toString();
  if (!execFunctions.has(key)) {
    execFunctions.set(key, new NativeFunction(exec, 'int', ['pointer', 'pointer', 'pointer'], EXEC_CALL));
  }
  return { self, exec: execFunctions.get(key), controller };
}

function noteLevel(level, info, now) {
  const key = level.toString();
  const seen = levelsSeen.get(key);
  if (seen !== undefined && now - seen.readAt < 250) {
    seen.seenAt = now;
    return;
  }
  if (levelsSeen.size > 8) {
    levelsSeen.clear();
  }
  pageVerdicts.clear(); // what a page is can change; ask again a few times a second
  levelsSeen.set(key, {
    seenAt: now,
    readAt: now,
    map: stringAt(level.add(LAYOUT.levelMap)),
    actors: s32At(level.add(LAYOUT.levelActors + 4)),
    capacity: s32At(level.add(LAYOUT.levelActors + 8)),
    hasPlayer: playerTarget(level, info) !== null,
  });
}

// The levels that ticked within the last second, the most recent one last.
function recentLevels() {
  const now = Date.now();
  return [...levelsSeen.values()].filter((seen) => now - seen.seenAt < 1000).sort((a, b) => a.seenAt - b.seenAt);
}

// Whether the engine object exists and is the one these addresses were measured for. Never throws.
function engineIsUp() {
  if (execInfo === null || disabled) {
    return false;
  }
  try {
    return engineExecThis() !== null;
  } catch (e) {
    disable(e.message); // a different build: say so now, instead of leaving the client waiting forever
    return false;
  }
}

function readState() {
  const engineReady = engineIsUp(); // first: it may find out that commands have to be turned off
  const loading = readPath(build.loading, (p) => p.readS32());
  const inGame = readPath(build.inGame, (p) => p.readU8());
  const levels = recentLevels();
  const playerLevel = levels.filter((seen) => seen.hasPlayer).pop(); // after a level change, the newer one
  return {
    agent: AGENT_VERSION,
    build: build.name,
    execAvailable: execInfo !== null && !disabled,
    hookInstalled: deltaFunction !== null,
    engineReady,
    pending: queue.length,
    levelValue: readPath(build.level, (p) => p.readS32()),
    loading: loading === null ? null : loading !== 0,
    // Where the path is known, a chain that cannot be followed means no game is loaded (the main menu, say).
    // null is only for builds where the agent has no way to tell.
    inGame: build.inGame ? inGame !== null && inGame !== 0 : null,
    fontainePhase: readPath(build.fontainePhase, (p) => p.readU8()),
    playerPath,
    // True while the level the player is in is ticking and the player has a controller and a body there.
    playerReady: playerLevel !== undefined,
    map: playerLevel === undefined ? null : playerLevel.map,
    levels: levels.map(
      (seen) => `${seen.map === null ? '?' : seen.map}: ${seen.actors} actors, room for ${seen.capacity}`),
    // Whether what the game raises during a command is reported, and how many exceptions the whole process has
    // raised since the agent was loaded. The second is for judging what watching costs.
    watching,
    exceptionsSeen,
    // Little Sisters this agent saw rescued or harvested, by map. The client counts them as they are reported.
    littleSistersSeen: Object.fromEntries(sistersResolved),
    sisterTrouble,
  };
}

// ---------------------------------------------------------------------------------------------------------------
// Game-thread hook point
// ---------------------------------------------------------------------------------------------------------------

function findDeltaFunction() {
  if (execInfo !== null) {
    const guess = base.add(execInfo.deltaFnRva);
    try {
      if (Memory.scanSync(guess, DELTA_SIGNATURE_LENGTH, DELTA_SIGNATURE).length === 1) {
        return guess;
      }
    } catch (e) {
      // The fixed offset is not readable on this build; fall through to the scan.
    }
  }

  const hits = [];
  for (const range of mod.enumerateRanges('r-x')) {
    for (const match of Memory.scanSync(range.base, range.size, DELTA_SIGNATURE)) {
      hits.push(match.address);
    }
  }
  if (hits.length === 1) {
    return hits[0];
  }
  log(`level-tick function: ${hits.length} signature matches, refusing to hook`);
  return null;
}

// ---------------------------------------------------------------------------------------------------------------
// Exec
// ---------------------------------------------------------------------------------------------------------------

// The FOutputDevice every command is given. The engine reports through its virtual slots, and the way it prints a
// line is a call with two stack arguments: the text and what kind of line it is. Every slot here is the same
// stdcall function, which pops those two arguments (8 bytes, the size the VR mod found works) and returns 1.
// While a command runs, it also keeps the text. That is how the answer to `get`, or to one of the engine's listing
// commands, can be read.
let output = null; // the lines printed by the command that is running; null between commands
let outputDropped = 0;

function keepLine(text) {
  if (output === null) {
    return;
  }
  if (output.length >= MAX_OUTPUT_LINES) {
    outputDropped++;
    return;
  }
  if (!readable(text)) {
    return; // not text after all: another slot was called, or this build passes something else
  }
  let line;
  try {
    line = text.readUtf16String();
  } catch (e) {
    return;
  }
  if (typeof line === 'string') {
    output.push(line.slice(0, MAX_LINE_LENGTH).replace(/[^\x20-\x7E]/g, '?'));
  }
}

const outputStub = new NativeCallback((text) => {
  try {
    keepLine(text);
  } catch (e) {
    // nothing may escape into the game from here
  }
  return 1;
}, 'int', ['pointer', 'pointer'], 'stdcall');
const outputVtable = Memory.alloc(24 * Process.pointerSize);
for (let i = 0; i < 24; i++) {
  outputVtable.add(i * Process.pointerSize).writePointer(outputStub);
}
const outputDevice = Memory.alloc(Process.pointerSize);
outputDevice.writePointer(outputVtable);

const engineExec = execInfo === null ? null : new NativeFunction(
  base.add(execInfo.engineExecRva), 'int', ['pointer', 'pointer', 'pointer'], EXEC_CALL);

// Returns the FExec `this` pointer, null while the engine is still starting up, or throws on a build mismatch.
function engineExecThis() {
  const engine = base.add(execInfo.enginePtrRva).readPointer();
  if (engine.isNull()) {
    return null;
  }
  const vtable = engine.readPointer();
  const expected = base.add(execInfo.engineVtableRva);
  if (!vtable.equals(expected)) {
    throw new Error(`UGameEngine vtable is ${vtable}, expected ${expected}: this is a different build`);
  }
  return engine.add(execInfo.execThisOffset);
}

// ---------------------------------------------------------------------------------------------------------------
// What the game raises while a command runs
// ---------------------------------------------------------------------------------------------------------------

// Frida shows every exception in the process to this script before the game gets it. Nothing is done about any of
// them here. But those raised on the game thread while a command of ours runs say what the game made of the
// command, so they are described and passed on: at the prompt as they happen, to the client as messages.
const CPP_EXCEPTION = 0xE06D7363; // what `throw` raises in a program built with Microsoft's compiler
const DEBUG_TEXT = 0x40010006; // OutputDebugStringA
const DEBUG_TEXT_WIDE = 0x4001000A; // OutputDebugStringW
const THREAD_NAME = 0x406D1388; // a thread telling a debugger its name: says nothing about a command
// An exception record in a 32-bit process: its code, flags and a further record, then ...
const RECORD_ADDRESS = 0x0C; // ... where it was raised,
const RECORD_COUNT = 0x10; // how many arguments it carries (15 at most)
const RECORD_ARGUMENTS = 0x14; // and the arguments
const RECORD_SEARCH = 0x70; // how far below the saved registers a record is looked for
const POINTERS_TO_WIDE_TEXT = ['.PA_W', '.PB_W', '.PAG', '.PBG']; // the compiler's names for (const) TCHAR*
const POINTERS_TO_TEXT = ['.PAD', '.PBD']; // ... and for (const) char*

let raising = null; // { id, command, count, reported, last } while a command runs; null between commands
let commandThread = null; // the thread that command runs on
let handlerInstalled = false;
let watching = false;
let exceptionsSeen = 0; // every exception in the process since the agent was loaded, on any thread

function whereInProcess(pointer) {
  if (pointer === null || pointer === undefined) {
    return 'an unknown place';
  }
  if (inModule(pointer)) {
    return where(pointer);
  }
  try {
    const owner = Process.findModuleByAddress(pointer);
    if (owner !== null) {
      return `${owner.name}+${pointer.sub(owner.base)}`;
    }
  } catch (e) {
    // no module there
  }
  return `${pointer}`;
}

// Text the game points at, or null. Nothing is read beyond the page the text starts on and the next one, if that
// one is there, and the bytes are taken as they are: whatever is not plain text becomes a question mark.
function textAt(pointer, wide) {
  if (pointer === null || pointer.isNull() || !readable(pointer)) {
    return null;
  }
  const unit = wide ? 2 : 1;
  let room = 0x1000 - (pointer.toUInt32() & 0xFFF);
  if (readable(pointer.add(room))) {
    room += 0x1000;
  }
  const size = Math.min(room - (room % unit), MAX_LINE_LENGTH * unit);
  let bytes;
  try {
    bytes = new Uint8Array(pointer.readByteArray(size));
  } catch (e) {
    return null;
  }
  let text = '';
  for (let i = 0; i + unit <= bytes.length; i += unit) {
    const code = wide ? bytes[i] | (bytes[i + 1] << 8) : bytes[i];
    if (code === 0) {
      break;
    }
    if (code >= 0x20 && code <= 0x7E) {
      text += String.fromCharCode(code);
    } else {
      text += code === 9 || code === 10 || code === 13 ? ' ' : '?'; // a tab or the end of a line, or anything else
    }
  }
  text = text.trim();
  return text.length === 0 ? null : text;
}

// Frida says where an exception was raised and what the thread's registers were, but not what was raised. Windows
// keeps that, the exception record, on the thread's stack: just below the saved registers Frida points at, and for
// an exception raised by a call (which `throw` and debug text are) also where that call's argument says. A record
// is recognised by holding the address the exception was raised at.
function exceptionRecord(details) {
  const candidates = [];
  const saved = details.nativeContext;
  if (saved && !saved.isNull()) {
    for (let below = RECORD_ARGUMENTS; below <= RECORD_SEARCH; below += 4) {
      candidates.push(saved.sub(below));
    }
  }
  const stack = details.context ? details.context.sp : null;
  if (stack && !stack.isNull()) {
    candidates.push(carefulPtr(stack.sub(4)));
  }
  for (const record of candidates) {
    if (record === null || record.isNull() || !same(carefulPtr(record.add(RECORD_ADDRESS)), details.address)) {
      continue;
    }
    const count = carefulS32(record.add(RECORD_COUNT));
    if (count !== null && count >= 0 && count <= 15) {
      return record;
    }
  }
  return null;
}

// What a `throw` threw. An engine of this family throws text for what it means to catch itself (a package or an
// object that is not there, say), so the text is the part worth having.
function describeThrown(args) {
  const object = args.length > 1 ? args[1] : null;
  const info = args.length > 2 ? args[2] : null;
  if (object === null || object.isNull() || info === null || info.isNull()) {
    return 'C++ exception, passed on from the one before';
  }
  // The compiler's note on what was thrown: its list of types (+0xC), the first of them (+4), that type's
  // descriptor (+4) and the descriptor's name (+8).
  const types = carefulPtr(info.add(0x0C));
  const first = types === null || types.isNull() ? null : carefulPtr(types.add(4));
  const descriptor = first === null || first.isNull() ? null : carefulPtr(first.add(4));
  const name = descriptor === null || descriptor.isNull() ? null : textAt(descriptor.add(8), false);
  if (name === null) {
    return 'C++ exception';
  }
  const wide = POINTERS_TO_WIDE_TEXT.includes(name);
  if (wide || POINTERS_TO_TEXT.includes(name)) {
    const text = textAt(carefulPtr(object), wide);
    return text === null ? `C++ exception of type ${name}` : `C++ exception: "${text}"`;
  }
  if (name === '.H') {
    const number = carefulS32(object);
    return number === null ? 'C++ exception: a number' : `C++ exception: the number ${number}`;
  }
  return `C++ exception of type ${name}`;
}

// One line about an exception, or null for one that says nothing about a command.
function describeRaised(details) {
  const at = whereInProcess(details.address);
  const type = String(details.type);
  if (type !== 'system') {
    // A fault in the processor's sense. What the game does about it is the game's business.
    const memory = details.memory;
    const touched = memory && memory.address ? ` (${memory.operation} of ${memory.address})` : '';
    return `${type.replace(/-/g, ' ')}${touched} at ${at}`;
  }
  const record = exceptionRecord(details);
  const head = record === null ? null : carefulPtr(record);
  if (head === null) {
    return `an exception at ${at}`;
  }
  const code = head.toUInt32();
  const count = carefulS32(record.add(RECORD_COUNT));
  const args = [];
  for (let i = 0; i < count; i++) {
    args.push(carefulPtr(record.add(RECORD_ARGUMENTS + 4 * i)));
  }
  if (code === THREAD_NAME) {
    return null;
  }
  if (code === CPP_EXCEPTION) {
    return describeThrown(args);
  }
  if (code === DEBUG_TEXT || code === DEBUG_TEXT_WIDE) {
    const text = args.length > 1 ? textAt(args[1], code === DEBUG_TEXT_WIDE) : null;
    return text === null ? 'debug text that could not be read' : `debug text: "${text}"`;
  }
  return `exception 0x${code.toString(16)} at ${at}`;
}

// Called by Frida for every exception in the process, on the thread that raised it, before the game sees it.
function onException(details) {
  try {
    exceptionsSeen++;
    const during = raising;
    if (during === null || !watching || Process.getCurrentThreadId() !== commandThread) {
      return false;
    }
    if (during.reported >= MAX_RAISED) {
      during.count++; // enough has been said about this command; the rest is only counted, which costs nothing
      return false;
    }
    const what = describeRaised(details);
    if (what === null) {
      return false;
    }
    during.count++;
    if (what === during.last) {
      return false; // the same again: one exception handed up from function to function
    }
    during.last = what;
    during.reported++;
    if (during.id === null) {
      log(`raised during "${during.command}": ${what}`);
    } else {
      send({ type: 'exec_raised', id: during.id, command: during.command, what });
    }
  } catch (e) {
    // watching must never get in the game's way
  }
  return false; // never dealt with here: the game gets every one of them
}

try {
  Process.setExceptionHandler(onException);
  handlerInstalled = true;
  watching = true;
} catch (e) {
  log(`cannot watch what the game raises during a command (${e.message}). Commands run all the same.`);
}

// ---------------------------------------------------------------------------------------------------------------
// Command queue (filled from any thread, drained only on the game thread)
// ---------------------------------------------------------------------------------------------------------------

const queue = []; // { id, command }; id is null for commands typed at the Frida prompt
let running = false;
let disabled = execInfo === null;
let disabledReason = disabled ? `running commands is not supported on the ${build.name} build yet` : '';
let lastRun = 0;

function disable(reason) {
  if (disabled) {
    return;
  }
  disabled = true;
  disabledReason = reason;
  log(`commands disabled: ${reason}`);
  send({ type: 'exec_disabled', reason });
}

// Runs one command. Says who took it ('player', 'engine' or null), what the game printed meanwhile and how many
// exceptions it raised. If the game gives up over the command, this never comes back: the game's own handling of
// that takes over from here.
function runCommand(entry, player, engineSelf) {
  const wide = Memory.allocUtf16String(entry.command);
  const during = { id: entry.id, command: entry.command, count: 0, reported: 0, last: null };
  output = [];
  outputDropped = 0;
  commandThread = Process.getCurrentThreadId();
  raising = during;
  try {
    let via = null;
    if (player !== null && player.exec(player.self, wide, outputDevice) !== 0) {
      via = 'player';
    } else if (engineExec(engineSelf, wide, outputDevice) !== 0) {
      via = 'engine';
    }
    return { via, output, dropped: outputDropped, raised: during.count };
  } finally {
    output = null; // whatever calls the output device after this is not an answer to a command of ours
    raising = null; // ... and whatever is raised after this has nothing to do with one
  }
}

function runQueuedCommand(level, info, now) {
  if (running || disabled || queue.length === 0) {
    return;
  }
  if (now - lastRun < MIN_INTERVAL_MS) {
    return;
  }
  running = true;
  try {
    const self = engineExecThis();
    if (self === null) {
      return; // engine not up yet: keep the command queued
    }
    let player = null;
    if (playerPath === 'unknown') {
      return; // still looking for the local player; the command waits for the answer
    }
    if (playerPath === 'ready') {
      // Player commands only make sense in the tick of the level the player is in, and never while a level is
      // loading. Until then the command stays queued.
      const loading = readPath(build.loading, (p) => p.readS32());
      player = loading ? null : playerTarget(level, info);
      if (player === null) {
        return;
      }
    }
    const entry = queue.shift();
    lastRun = now;
    if (entry.id !== null) {
      // From here on the command is inside the game. If the game closes before the result follows, the client
      // knows which command it was busy with, and does not send it again by itself.
      send({ type: 'exec_started', id: entry.id, command: entry.command });
    }
    let ran;
    try {
      ran = runCommand(entry, player, self);
    } catch (e) {
      // What the game raises is left to the game, so this is the agent's own doing. Say which command it was: the
      // client then sets that item aside instead of trying it again in every session.
      const reason = `the agent failed while running "${entry.command}" (${e.message})`;
      send({ type: 'exec_result', id: entry.id, command: entry.command, handled: false, fatal: true, reason });
      disable(reason);
      return;
    }
    const via = ran.via;
    const handled = via !== null;
    if (entry.id === null) {
      log(`${handled ? `handled by the ${via}` : 'NOT handled'}: ${entry.command}`);
      ran.output.forEach((line) => log(`  | ${line}`));
      if (ran.dropped > 0) {
        log(`  | ... and ${ran.dropped} more lines`);
      }
      if (ran.raised > 0) {
        log(`  the game raised ${ran.raised} exception${ran.raised === 1 ? '' : 's'} of its own meanwhile, ` +
          'and carried on');
      }
    }
    send({
      type: 'exec_result', id: entry.id, command: entry.command, handled, via,
      output: ran.output.slice(0, MAX_LINES_TO_CLIENT), outputLines: ran.output.length + ran.dropped,
      raised: ran.raised,
    });
  } catch (e) {
    disable(e.message);
  } finally {
    running = false;
  }
}

function enqueue(id, command) {
  if (disabled) {
    send({ type: 'exec_result', id, command, handled: false, reason: disabledReason });
    return queue.length;
  }
  queue.push({ id, command: String(command) });
  return queue.length;
}

// ---------------------------------------------------------------------------------------------------------------
// probe(): a read-only report of what the agent finds, for working out layouts on a new build
// ---------------------------------------------------------------------------------------------------------------

let probing = false;
let probeUntil = 0; // set by the first tick after probe() was typed
const probed = new Map(); // level address -> report lines

function hex(value) {
  return value === null ? '?' : `0x${(value >>> 0).toString(16)}`;
}

// Engine strings found in a stretch of an object, as `+offset "text"`.
function stringsIn(object, from, to) {
  const found = [];
  for (let offset = from; offset < to && found.length < 8; offset += 4) {
    const text = stringAt(object.add(offset));
    if (text !== null) {
      found.push(`+0x${offset.toString(16)} "${text}"`);
    }
  }
  return found;
}

// Which fields of `object` (in a range) hold `target`.
function fieldsHolding(object, from, to, target) {
  const offsets = [];
  for (let offset = from; offset < to; offset += 4) {
    if (same(carefulPtr(object.add(offset)), target)) {
      offsets.push(`+0x${offset.toString(16)}`);
    }
  }
  return offsets;
}

function describeController(controller, addresses) {
  const lines = [];
  const pawn = ptrAt(controller.add(LAYOUT.controllerPawn));
  let pawnNote = 'none';
  if (pawn !== null && !pawn.isNull()) {
    const listed = addresses.has(pawn.toUInt32());
    const back = listed && same(ptrAt(pawn.add(LAYOUT.pawnController)), controller);
    pawnNote = `${pawn} (${listed ? 'an actor of this level' : 'NOT an actor of this level'}, ` +
      `${back ? 'points back' : 'does not point back'})`;
  }
  lines.push(`  player controller ${controller}: vtable ${where(ptrAt(controller))}, pawn ${pawnNote}`);
  if (pawnNote.includes('points back')) {
    const stats = PAWN_STATS.map((stat) => {
      const at = pawn.add(stat.offset);
      let value = null;
      try {
        value = readable(at) ? (stat.float ? Math.round(at.readFloat() * 10) / 10 : at.readS32()) : null;
      } catch (e) {
        value = null;
      }
      return `${stat.name} ${value === null ? '?' : value}`;
    });
    lines.push(`    pawn numbers at the older build's offsets: ${stats.join(', ')}`);
  }

  const player = ptrAt(controller.add(LAYOUT.controllerPlayer));
  const back = player !== null && !player.isNull() && same(carefulPtr(player.add(LAYOUT.playerActor)), controller);
  lines.push(`    Player field (+0x${LAYOUT.controllerPlayer.toString(16)}): ${player}, ` +
    `${back ? 'points back at the controller' : 'does NOT point back at the controller'}`);
  if (back) {
    const execVtable = carefulPtr(player.add(LAYOUT.playerExec));
    const exec = carefulPtr(execVtable);
    lines.push(`    player vtables: ${where(carefulPtr(player))}, ` +
      `${where(carefulPtr(player.add(LAYOUT.playerOutput)))}, ${where(execVtable)}; Exec ${where(exec)}`);
    if (inModule(exec)) {
      lines.push(`    Exec starts with ${hex(carefulS32(exec))} ${hex(carefulS32(exec.add(4)))}`);
    }
    lines.push(`    fields of the player holding the controller: ` +
      `${fieldsHolding(player, 0x40, 0x100, controller).join(', ') || 'none'}`);
    return lines;
  }
  // The prediction did not hold. Look for any field of the controller whose target points back at it early on.
  const candidates = [];
  for (let offset = 0x450; offset < 0xA00 && candidates.length < 6; offset += 4) {
    const target = ptrAt(controller.add(offset));
    if (target === null || target.isNull() || addresses.has(target.toUInt32()) || !inModule(carefulPtr(target))) {
      continue; // not an object, or one of the level's actors (the pawn, the HUD)
    }
    const backAt = fieldsHolding(target, 0x40, 0x80, controller);
    if (backAt.length > 0) {
      candidates.push(`+0x${offset.toString(16)} -> ${target} (holds the controller at ${backAt.join(', ')})`);
    }
  }
  lines.push(`    other fields whose target points back: ${candidates.join('; ') || 'none'}`);
  return lines;
}

// How the engine object leads to the local player, if it does: engine -> some object -> a short list whose first
// entry is the player. Knowing this would spare the search through the actors.
function enginePathTo(player) {
  if (execInfo === null) {
    return 'not looked for on this build';
  }
  const engine = ptrAt(base.add(execInfo.enginePtrRva));
  if (engine === null || engine.isNull()) {
    return 'engine not up';
  }
  for (let offset = 0x44; offset < 0x200; offset += 4) {
    const holder = ptrAt(engine.add(offset));
    if (holder === null || holder.isNull() || !inModule(carefulPtr(holder))) {
      continue;
    }
    for (let inner = 0x40; inner < 0x180; inner += 4) {
      const count = carefulS32(holder.add(inner + 4));
      const max = carefulS32(holder.add(inner + 8));
      if (count === null || max === null || count < 1 || count > 8 || max < count || max > 64) {
        continue;
      }
      if (same(carefulPtr(carefulPtr(holder.add(inner))), player)) {
        return `engine+0x${offset.toString(16)} -> ${holder} (vtable ${where(carefulPtr(holder))}), ` +
          `its list at +0x${inner.toString(16)} (${count} of ${max}) starts with the player`;
      }
    }
  }
  return 'not found in the first 0x200 bytes of the engine';
}

function describeLevel(level) {
  const lines = [];
  const info = levelInfoOf(level);
  const count = s32At(level.add(LAYOUT.levelActors + 4));
  const max = s32At(level.add(LAYOUT.levelActors + 8));
  lines.push(`level ${level}: vtable ${where(ptrAt(level))}, ${count} actors, room for ${max}, ` +
    `LevelInfo ${info === null ? 'NOT confirmed' : `${info} confirmed`}`);
  lines.push(`  map (+0x${LAYOUT.levelMap.toString(16)}): ${JSON.stringify(stringAt(level.add(LAYOUT.levelMap)))}; ` +
    `strings: ${stringsIn(level, 0x40, 0x140).join(', ') || 'none'}`);
  if (info === null) {
    lines.push(`  first words: ${[0x40, 0x44, 0x48, 0x4C, 0x50].map((o) => hex(s32At(level.add(o)))).join(' ')}`);
    return lines;
  }
  const { controllers, addresses, actorCount } = playerControllersOf(level);
  lines.push(`  ${actorCount} actors read, ${controllers.length} player controller(s)`);
  for (const controller of controllers) {
    for (const line of describeController(controller, addresses)) {
      lines.push(line);
    }
  }
  return lines;
}

function probeTick(level, now) {
  if (probeUntil === 0) {
    probeUntil = now + 1000; // long enough for every loaded level to tick
  }
  const key = level.toString();
  if (!probed.has(key) && probed.size < 4) {
    pageVerdicts.clear();
    let lines;
    try {
      lines = describeLevel(level);
    } catch (e) {
      lines = [`level ${level}: the probe failed (${e.message})`];
    }
    probed.set(key, lines);
  }
  if (now < probeUntil) {
    return;
  }
  probing = false;
  log(`probe: level value ${readPath(build.level, (p) => p.readS32())}, ` +
    `loading ${readPath(build.loading, (p) => p.readS32())}, ` +
    `careful reads ${virtualQuery === null ? 'NOT available' : 'available'}`);
  for (const lines of probed.values()) {
    for (const line of lines) {
      log(line);
    }
  }
  log(`  player path: ${playerPath}${viewport === null ? '' : `, player ${viewport}; ${enginePathTo(viewport)}`}`);
  log('probe: done');
}

// ---------------------------------------------------------------------------------------------------------------
// Objects by name: the engine's name table, and the classes and properties of objects
// ---------------------------------------------------------------------------------------------------------------

const objectModel = build.objects || null;
const nameCache = new Map(); // name index -> text. An index keeps its text for as long as the game runs

function nameOf(index) {
  if (objectModel === null || index === null || index < 0 || index > 0x400000) {
    return null;
  }
  if (nameCache.has(index)) {
    return nameCache.get(index);
  }
  const table = carefulPtr(base.add(objectModel.names));
  const count = carefulS32(base.add(objectModel.names + 4));
  if (table === null || count === null || index >= count) {
    return null;
  }
  const entry = carefulPtr(table.add(index * 4));
  if (entry === null || entry.isNull() || !readable(entry.add(objectModel.nameText)) ||
      !readable(entry.add(objectModel.nameText + 128))) {
    return null;
  }
  let text = null;
  try {
    text = entry.add(objectModel.nameText).readUtf16String(64);
  } catch (e) {
    return null;
  }
  if (text === null || !/^[\w.-]+$/.test(text)) {
    return null;
  }
  nameCache.set(index, text);
  return text;
}

function nameOfObject(object) {
  return object === null || object.isNull() ? null : nameOf(carefulS32(object.add(objectModel.objectName)));
}

function classOf(object) {
  return carefulPtr(object.add(objectModel.objectClass));
}

// The class itself, then the classes it extends, up to Object.
function lineageOf(cls) {
  const lineage = [];
  let current = cls;
  while (current !== null && !current.isNull() && lineage.length < 40) {
    lineage.push(current);
    if (nameOfObject(current) === 'Object') {
      break;
    }
    current = carefulPtr(current.add(objectModel.classSuper));
  }
  return lineage;
}

// Where a bool property sits in an object of this class: its offset and its bit. Bools declared one after another
// share a word, one bit each in the order they are declared.
function findBoolProperty(cls, wanted) {
  for (const owner of lineageOf(cls)) {
    const sharing = new Map();
    let field = carefulPtr(owner.add(objectModel.structChildren));
    for (let n = 0; field !== null && !field.isNull() && n < 1000; n++) {
      if (nameOfObject(classOf(field)) === 'BoolProperty') {
        const offset = carefulS32(field.add(objectModel.propertyOffset));
        const bit = sharing.get(offset) || 0;
        sharing.set(offset, bit + 1);
        if (nameOfObject(field) === wanted) {
          return offset === null || offset < 0x30 || offset > 0x8000 || bit > 31 ? null : { offset, mask: (1 << bit) >>> 0 };
        }
      }
      field = carefulPtr(field.add(objectModel.fieldNext));
    }
  }
  return null;
}

// ---------------------------------------------------------------------------------------------------------------
// Little Sisters
// ---------------------------------------------------------------------------------------------------------------

// A Little Sister is an actor whose class extends Gatherer. Her HasBeenSavedOrPacified turns true when she is
// rescued or harvested, and she is deleted a little later. She is also deleted whenever she climbs into a vent,
// and a new one comes out of it later, but then the flag stays false. Both seen in Medical Pavilion, 2026-10-07.
const SISTER_INTERVAL_MS = 100;
const SISTER_FLAG = 'HasBeenSavedOrPacified';
const sisterClasses = new Map(); // class address -> where its flag is, or null for classes that are not sisters
const sistersSeen = new Map(); // actor address -> whether her flag was already true when last looked at
const sistersResolved = new Map(); // map -> sisters this agent saw rescued or harvested there
let lastSisterLook = 0;
let sisterTrouble = null;

function sisterFlagOf(cls) {
  const key = cls.toString();
  if (sisterClasses.has(key)) {
    return sisterClasses.get(key);
  }
  let flag = null;
  if (lineageOf(cls).some((c) => nameOfObject(c) === 'Gatherer')) {
    flag = findBoolProperty(cls, SISTER_FLAG);
    if (flag === null) {
      sisterTrouble = `could not find ${SISTER_FLAG} in ${nameOfObject(cls)}`;
      log(`Little Sisters cannot be watched: ${sisterTrouble}`);
    } else {
      log(`watching Little Sisters of class ${nameOfObject(cls)}: ${SISTER_FLAG} at +0x${flag.offset.toString(16)}, ` +
        `bit 0x${flag.mask.toString(16)}`);
    }
  }
  if (sisterClasses.size > 4096) {
    sisterClasses.clear();
  }
  sisterClasses.set(key, flag);
  return flag;
}

// Runs on the game thread for the level the player is in, a few times a second.
function watchSisters(level, map, now) {
  if (objectModel === null || virtualQuery === null || now - lastSisterLook < SISTER_INTERVAL_MS) {
    return;
  }
  lastSisterLook = now;
  const present = new Set();
  for (const actor of actorsOf(level).actors) {
    const cls = ptrAt(actor.add(objectModel.objectClass)); // a listed actor is a real object: plain reads will do
    if (cls === null || cls.isNull()) {
      continue;
    }
    const flag = sisterFlagOf(cls);
    if (flag === null) {
      continue;
    }
    const key = actor.toString();
    present.add(key);
    const word = s32At(actor.add(flag.offset));
    const resolved = word !== null && ((word >>> 0) & flag.mask) !== 0;
    if (resolved && sistersSeen.get(key) !== true) {
      const count = (sistersResolved.get(map) || 0) + 1;
      sistersResolved.set(map, count);
      log(`a Little Sister was rescued or harvested in ${map} (${count} seen there by this agent)`);
      send({ type: 'little_sister', map });
    }
    sistersSeen.set(key, resolved);
  }
  for (const key of [...sistersSeen.keys()]) {
    if (!present.has(key)) {
      sistersSeen.delete(key); // deleted: rescued, harvested or gone into a vent. Its address may be used again
    }
  }
}

// ---------------------------------------------------------------------------------------------------------------
// The hook
// ---------------------------------------------------------------------------------------------------------------

let complained = false;

function onLevelTick(level) {
  try {
    const now = Date.now();
    if (firstTickAt === 0) {
      firstTickAt = now;
    }
    const info = levelInfoOf(level);
    if (info !== null) {
      lookForPlayer(level, now);
      noteLevel(level, info, now);
      const seen = levelsSeen.get(level.toString());
      if (seen !== undefined && seen.hasPlayer && seen.map !== null) {
        watchSisters(level, seen.map, now);
      }
    } else if (playerPath === 'unknown' && now - firstTickAt > SEARCH_PATIENCE_MS) {
      playerPath = 'unavailable';
      log('the hooked function is not handing over levels as expected, so commands go to the engine alone.');
    }
    if (probing) {
      probeTick(level, now);
    }
    runQueuedCommand(level, info, now);
  } catch (e) {
    if (!complained) {
      complained = true;
      log(`problem inside the game-thread hook: ${e.message}`);
    }
  }
}

const deltaFunction = findDeltaFunction();
if (deltaFunction === null) {
  log('no game-thread hook installed, so queued commands will never run');
} else {
  Interceptor.attach(deltaFunction, {
    onEnter() {
      onLevelTick(this.context.ecx);
    },
  });
  log(`game-thread hook installed at module+${deltaFunction.sub(base)}`);
}

// Read-only startup check (safe off the game thread): is UGameEngine::Exec also slot 0 of the FExec vtable? If so,
// the fixed Exec address isn't needed and the same trick works for other FExec objects, like the local player.
if (!disabled) {
  try {
    const self = engineExecThis();
    if (self === null) {
      log('engine not initialised yet; load into a level before sending commands');
    } else {
      const slot0 = self.readPointer().readPointer();
      const matches = slot0.equals(base.add(execInfo.engineExecRva));
      log(`FExec slot 0 is module+${slot0.sub(base)} ` +
        `(${matches ? 'matches' : 'does NOT match'} the fixed Exec address)`);
    }
  } catch (e) {
    disable(e.message);
  }
} else {
  log(`commands disabled: ${disabledReason}`);
}

// ---------------------------------------------------------------------------------------------------------------
// Entry points: `give()`, `state()` and `probe()` for the Frida prompt, rpc exports for the Archipelago client
// ---------------------------------------------------------------------------------------------------------------

globalThis.give = function (command) {
  const waiting = enqueue(null, command);
  return disabled ? `not queued: ${disabledReason}` : `queued (${waiting} waiting)`;
};

globalThis.state = readState;

globalThis.watch = function (on) {
  watching = handlerInstalled && on !== false;
  if (watching) {
    return 'reporting what the game raises while a command runs';
  }
  return handlerInstalled ? 'not reporting what the game raises; watch() turns it on again'
    : 'this Frida gives the agent no way to watch';
};

// How often each stat's instruction occurs in the game's code. Once means this build uses the same offset.
function statCodeMatches() {
  return PAWN_STATS.map((stat) => {
    let count = 0;
    try {
      for (const range of mod.enumerateRanges('r-x')) {
        count += Memory.scanSync(range.base, range.size, stat.code).length;
      }
    } catch (e) {
      return `${stat.name} ?`;
    }
    return `${stat.name} ${count}`;
  }).join(', ');
}

globalThis.probe = function () {
  if (deltaFunction === null) {
    return 'no game-thread hook, nothing to probe';
  }
  log(`probe: stat instructions found in the game's code: ${statCodeMatches()}`);
  probed.clear();
  probeUntil = 0;
  probing = true;
  return 'probing for one second; the report follows (the game has to be running, not paused)';
};

rpc.exports = {
  // Queue a console command. The answer arrives as an `exec_result` message carrying the same id.
  exec(id, command) {
    return enqueue(id, command);
  },
  state() {
    return readState();
  },
  // Named actions (traps, DeathLink). None are implemented yet: they need the player object, which is still to be
  // located.
  action(id, name) {
    send({ type: 'action_result', id, name, ok: false, reason: 'the agent does not support this yet' });
  },
};

log('ready. Try state(), probe() or give("GiveItem 10 ShockGame.ADAM")');

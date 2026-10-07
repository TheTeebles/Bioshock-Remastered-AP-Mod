/*
 * Runs agent.js against a fake Frida runtime and a fake piece of game memory, so the agent's own logic (finding the
 * local player, the command queue, ids, rate limit, build selection, state reading, refusing where objects do not
 * fit, describing what the game raises) can be checked without the game.
 *
 * It cannot check the addresses and layouts themselves, nor how Frida and Windows really hand over an exception.
 * Only the real game can do that.
 *
 *   node agent_mock_test.js path/to/agent.js
 */
'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const agentPath = process.argv[2] || 'agent.js';
const source = fs.readFileSync(agentPath, 'utf8');

const BASE = 0x400000;
const STEAM_SIZE = 23556096;
const EPIC_SIZE = 23552000;
const STEAM = { enginePtr: 0x1375368, vtable: 0xE0DFF4, exec: 0x4C5970, delta: 0x53D850 };
const VIRTUAL_QUERY = 0x76000000;
const PLAYER_EXEC = BASE + 0x700000; // where the fake player's Exec function lives
const ENGINE = 0x50000000;
const GUARD_PAGE = 0x00190000; // a page that must never be read
const KERNELBASE = 0x75000000; // where the fake system library that raises exceptions for programs sits
const RAISED_AT = KERNELBASE + 0x12345;
const CPP = 0xE06D7363; const DEBUG_TEXT = 0x40010006; const DEBUG_TEXT_WIDE = 0x4001000A;
const THREAD_NAME = 0x406D1388;

class Ptr {
  constructor(value, runtime) { this.value = value >>> 0; this.runtime = runtime; }
  add(n) { return new Ptr(this.value + (n instanceof Ptr ? n.value : n), this.runtime); }
  sub(n) { return new Ptr(this.value - (n instanceof Ptr ? n.value : n), this.runtime); }
  equals(other) { return this.value === other.value; }
  isNull() { return this.value === 0; }
  toUInt32() { return this.value; }
  toInt32() { return this.value | 0; }
  toString() { return '0x' + this.value.toString(16); }
  read() {
    if ((this.value >>> 12) === (GUARD_PAGE >>> 12)) { this.runtime.guardTouched = true; }
    if (!this.runtime.memory.has(this.value)) { throw new Error(`access violation accessing ${this}`); }
    return this.runtime.memory.get(this.value);
  }
  readPointer() { return new Ptr(this.read(), this.runtime); }
  readS32() { return this.read() | 0; }
  readU32() { return this.read() >>> 0; }
  readU8() { return this.read() & 0xFF; }
  readFloat() { return this.read() / 10; } // the fake memory keeps floats as tenths
  readUtf16String(length) {
    if ((this.value >>> 12) === (GUARD_PAGE >>> 12)) { this.runtime.guardTouched = true; }
    if (!this.runtime.text.has(this.value)) { throw new Error(`access violation accessing ${this}`); }
    return this.runtime.text.get(this.value).slice(0, length);
  }
  // Raw bytes, as the real one reads them: every page touched has to be there, or the read fails.
  readByteArray(length) {
    for (let page = this.value >>> 12; page <= (this.value + length - 1) >>> 12; page++) {
      if (page === (GUARD_PAGE >>> 12)) { this.runtime.guardTouched = true; }
      if (!this.runtime.committed(page * 0x1000)) { throw new Error(`access violation accessing ${this}`); }
    }
    this.runtime.bytesRead.push(length);
    const bytes = new Uint8Array(length);
    const wide = this.runtime.text.get(this.value);
    const plain = this.runtime.ansi.get(this.value);
    if (wide !== undefined) {
      for (let i = 0; i < wide.length && 2 * i + 1 < length; i++) {
        bytes[2 * i] = wide.charCodeAt(i) & 0xFF; bytes[2 * i + 1] = wide.charCodeAt(i) >>> 8;
      }
    } else if (plain !== undefined) {
      for (let i = 0; i < plain.length && i < length; i++) { bytes[i] = plain.charCodeAt(i) & 0xFF; }
    }
    return bytes.buffer;
  }
  writePointer(p) { this.runtime.memory.set(this.value, p.value); return this; }
}

function makeRuntime({
  moduleSize = STEAM_SIZE, deltaAt = STEAM.delta, scanHits = null, virtualQuery = true, exceptionHandler = true,
} = {}) {
  const runtime = {
    memory: new Map(), text: new Map(), messages: [], logs: [], time: 1000, hooks: [], execCalls: [],
    strings: new Map(), refuse: new Set(), faults: new Set(), playerOnly: new Set(), nextAlloc: 0x70000000,
    levels: [], guardTouched: false, queries: 0, nextObject: 0x20000000,
    prints: new Map(), // part of a command -> the lines the "game" prints when it runs
    outputDevice: null, // the callback the agent put in its output device
    nextText: 0x30000000,
    ansi: new Map(), // text of one byte a character, by address
    bytesRead: [], // the size of every raw read
    nativeOptions: new Map(), // address of a native function -> how the agent asked for it to be called
    exceptionHandler: null, // what the agent gave Process.setExceptionHandler
    thread: 1, // the thread everything runs on unless a test says otherwise
    raises: new Map(), // part of a command -> the exceptions the "game" raises, and deals with, while it runs
    answers: [], // what the agent's handler answered each time: true would take the exception away from the game
    nextStack: 0x0A000000,
  };
  const ptr = (n) => new Ptr(n, runtime);
  const committed = (address) => {
    const page = address >>> 12;
    for (const key of runtime.memory.keys()) { if ((key >>> 12) === page) { return true; } }
    for (const key of runtime.text.keys()) { if ((key >>> 12) === page) { return true; } }
    for (const key of runtime.ansi.keys()) { if ((key >>> 12) === page) { return true; } }
    return false;
  };
  runtime.committed = committed;
  const context = {
    console: { log: (text) => runtime.logs.push(text) },
    Date: { now: () => runtime.time },
    JSON, Map, Set, Math,
    send: (message) => runtime.messages.push(message),
    rpc: { exports: {} },
    Process: {
      pointerSize: 4,
      getCurrentThreadId: () => runtime.thread,
      setExceptionHandler: (callback) => {
        if (!exceptionHandler) { throw new Error('not a function'); }
        runtime.exceptionHandler = callback;
      },
      findModuleByAddress: (address) => (address.value >= KERNELBASE && address.value < KERNELBASE + 0x200000
        ? { name: 'KERNELBASE.dll', base: ptr(KERNELBASE) } : null),
      getModuleByName: (name) => {
        if (name === 'kernel32.dll') {
          if (!virtualQuery) { throw new Error('unable to find module'); }
          return { getExportByName: () => ptr(VIRTUAL_QUERY) };
        }
        return {
          base: ptr(BASE), size: moduleSize,
          enumerateRanges: () => [{ base: ptr(BASE + 0x1000), size: 0x1000000 }],
        };
      },
    },
    Memory: {
      alloc: (size) => { const p = ptr(runtime.nextAlloc); runtime.nextAlloc += size + 16; return p; },
      allocUtf16String: (text) => { const p = ptr(runtime.nextAlloc); runtime.nextAlloc += 2 * text.length + 16;
        runtime.strings.set(p.value, text); return p; },
      scanSync: (address, size, pattern) => {
        if (size === 47) { return deltaAt !== null && address.value === BASE + deltaAt ? [{ address, size }] : []; }
        if (!pattern.startsWith('55 8B EC')) { return pattern.includes('F4 0A') ? [] : [{ address, size: 6 }]; }
        return (scanHits || []).map((rva) => ({ address: ptr(BASE + rva), size: 47 }));
      },
    },
    NativeCallback: function (callback) { runtime.outputDevice = callback; return ptr(0x66660000); },
    NativeFunction: function (address, returns, takes, options) {
      runtime.nativeOptions.set(address.value, options);
      if (address.value === VIRTUAL_QUERY) {
        return (queried, buffer) => {
          runtime.queries++;
          const guard = (queried.value >>> 12) === (GUARD_PAGE >>> 12);
          const ok = guard || committed(queried.value);
          runtime.memory.set(buffer.value + 16, ok ? 0x1000 : 0x10000); // State: committed or free
          runtime.memory.set(buffer.value + 20, guard ? 0x104 : (ok ? 0x04 : 0)); // Protect, with PAGE_GUARD
          return 28;
        };
      }
      return (self, wide) => {
        const command = runtime.strings.get(wide.value);
        const onPlayer = address.value === PLAYER_EXEC;
        const who = onPlayer ? 'player' : 'engine';
        runtime.execCalls.push({ address: address.value, self: self.value, command, who });
        if ([...runtime.faults].some((text) => command.includes(text))) {
          throw new Error('expected a pointer'); // the call itself going wrong, on the agent's side of it
        }
        if ([...runtime.refuse].some((text) => command.includes(text))) { return 0; }
        const playerCommand = [...runtime.playerOnly].some((text) => command.includes(text));
        if (onPlayer !== playerCommand) { return 0; } // each side only takes its own kind of command
        for (const [part, raised] of runtime.raises) {
          if (!command.includes(part)) { continue; }
          for (const spec of raised) {
            if (!options || options.exceptions !== 'propagate') {
              // Frida's default: the exception never reaches the game, and the call ends here and now.
              throw new Error('system error');
            }
            runtime.raise(spec, spec.thread); // otherwise the script is shown it, and the game then deals with it
          }
        }
        for (const [part, lines] of runtime.prints) {
          if (command.includes(part)) { lines.forEach((line) => runtime.print(line)); }
        }
        return 1;
      };
    },
    Interceptor: { attach: (address, callbacks) => runtime.hooks.push({ address: address.value, callbacks }) },
  };
  context.globalThis = context;
  runtime.context = context;
  runtime.load = () => { vm.runInNewContext(source, context, { filename: agentPath }); return runtime; };

  // One frame: the hooked function runs once for every loaded level, with the level in ecx.
  runtime.frame = (ms = 100) => {
    runtime.time += ms;
    const levels = runtime.levels.length > 0 ? runtime.levels : [0x12340000]; // or for something unreadable
    for (const level of levels) {
      runtime.hooks.forEach((h) => h.callbacks.onEnter.call({ context: { ecx: ptr(level) } }));
    }
  };
  runtime.frames = (count, ms = 100) => { for (let i = 0; i < count; i++) { runtime.frame(ms); } };

  runtime.engine = (address = ENGINE, vtableRva = STEAM.vtable) => {
    runtime.memory.set(BASE + STEAM.enginePtr, address);
    if (address !== 0) {
      runtime.memory.set(address, BASE + vtableRva); // the object's own vtable
      runtime.memory.set(address + 0x40, 0x51000000); // FExec sub-object's vtable pointer
      runtime.memory.set(0x51000000, BASE + STEAM.exec); // slot 0
    }
  };

  // The "game" printing a line: it calls the first slot of the output device with the text and a kind.
  runtime.print = (line) => {
    const at = typeof line === 'number' ? line : runtime.nextText;
    if (typeof line !== 'number') { runtime.text.set(at, line); runtime.nextText += 0x2000; }
    return runtime.outputDevice(ptr(at), ptr(0x2F0));
  };

  // ---- a fake piece of the game's object memory ----
  const poke = (address, value) => runtime.memory.set(address >>> 0, value >>> 0);
  runtime.poke = poke;

  // ---- exceptions ----
  // What Frida tells a script about an exception, with what Windows leaves on the thread's stack for it: the saved
  // registers and, somewhere near, the exception record. `layout` says where the record is:
  //   compact   right below the saved registers, only as long as its arguments (how 32-bit Windows writes it)
  //   full      right below, at its full size
  //   padded    below, with a gap
  //   argument  nowhere near; only the argument of the call that raised it leads there
  //   none      nowhere to be found
  runtime.exception = ({ code = CPP, args = [], type = 'system', layout = 'compact', at = RAISED_AT, memory } = {}) => {
    const stack = runtime.nextStack; runtime.nextStack += 0x10000;
    const saved = stack + 0x4000;
    for (let below = 0; below <= 0x100; below += 4) { poke(saved - below, 0); }
    const sp = stack + 0x9000; poke(sp - 4, 0);
    const record = { compact: saved - (0x14 + 4 * args.length), full: saved - 0x50, padded: saved - 0x60,
      argument: stack + 0x8000, none: null }[layout];
    if (record !== null) {
      [code, 0, 0, at, args.length, ...args].forEach((value, i) => poke(record + 4 * i, value));
    }
    if (layout === 'argument') { poke(sp - 4, record); }
    return { type, address: ptr(at), memory, context: { sp: ptr(sp), pc: ptr(at) }, nativeContext: ptr(saved) };
  };
  // The "game" raising one: Frida shows it to the script first, on the thread that raised it.
  runtime.raise = (spec, thread = runtime.thread) => {
    if (runtime.exceptionHandler === null) { return undefined; }
    const before = runtime.thread; runtime.thread = thread;
    try {
      const answer = runtime.exceptionHandler('details' in spec ? spec.details : runtime.exception(spec));
      runtime.answers.push(answer);
      return answer;
    } finally {
      runtime.thread = before;
    }
  };
  // What the compiler leaves for a `throw`: the arguments of the exception, leading to the thrown thing and to the
  // name of its type. A string is thrown as a pointer to that text; `plain` makes it one byte a character.
  runtime.thrown = (typeName, value, { plain = false } = {}) => {
    const [object, info, types, first, descriptor] = [0, 0, 0, 0, 0].map(() => runtime.pages(3, 2));
    poke(info + 0xC, types); poke(types, 1); poke(types + 4, first); poke(first + 4, descriptor);
    if (typeName !== null) { runtime.ansi.set(descriptor + 8, typeName); }
    if (typeof value === 'string') { poke(object, runtime.words(value, plain)); } else { poke(object, value); }
    return [0x19930520, object, info];
  };
  // Memory that starts on a page of its own: `count` pages are set aside, and the first `there` of them exist.
  runtime.pages = (count, there) => {
    const at = (runtime.nextObject + 0xFFF) & ~0xFFF; runtime.nextObject = at + count * 0x1000;
    for (let i = 0; i < there; i++) { poke(at + i * 0x1000, 0); }
    return at;
  };
  // Text somewhere in memory, with room behind it. Returns where.
  runtime.words = (text, plain = false) => {
    const at = runtime.pages(3, 2);
    (plain ? runtime.ansi : runtime.text).set(at, text);
    return at;
  };
  runtime.object = (size = 0x1000) => {
    const at = runtime.nextObject; runtime.nextObject += size; poke(at, BASE + 0x900000); return at;
  };
  runtime.string = (at, text) => { // an engine string {data, count, max} at `at`
    const data = runtime.object(0x1000); runtime.text.set(data, text);
    poke(at, data); poke(at + 4, text.length + 1); poke(at + 8, text.length + 1);
  };
  // A level with a LevelInfo and `extra` plain actors. Returns its address.
  runtime.level = (map, { extra = 3, capacity = null } = {}) => {
    const level = runtime.object();
    const info = runtime.object(); poke(info + 0xF8, info);
    const actors = [info];
    for (let i = 0; i < extra; i++) {
      const actor = runtime.object(0x460); poke(actor + 0xF8, info); // small: +0x71C is outside it
      actors.push(actor);
    }
    const data = runtime.object(0x4000);
    runtime.levelData.set(level, { data, actors, info });
    actors.forEach((actor, i) => poke(data + 4 * i, actor));
    poke(level + 0x44, data); poke(level + 0x48, actors.length); poke(level + 0x4C, capacity || actors.length);
    runtime.string(level + 0x7C, map);
    runtime.levels.push(level);
    return level;
  };
  runtime.levelData = new Map();
  runtime.addActor = (level, actor) => {
    const entry = runtime.levelData.get(level);
    poke(actor + 0xF8, entry.info);
    poke(entry.data + 4 * entry.actors.length, actor); entry.actors.push(actor);
    poke(level + 0x48, entry.actors.length);
    if (runtime.memory.get(level + 0x4C) < entry.actors.length) { poke(level + 0x4C, entry.actors.length); }
  };
  runtime.removeActor = (level, actor) => {
    const entry = runtime.levelData.get(level);
    entry.actors = entry.actors.filter((a) => a !== actor);
    entry.actors.forEach((a, i) => poke(entry.data + 4 * i, a));
    poke(level + 0x48, entry.actors.length);
  };
  // The engine's names and classes, as on the Steam build: a name table, and class objects with their name at
  // +0x28, class at +0x30, the class they extend at +0x40 and first field at +0x5C; a field's next at +0x44 and a
  // property's offset at +0x74.
  runtime.objectModel = () => {
    const names = [];
    const table = runtime.object(0x4000);
    poke(BASE + 0x13904EC, table);
    const name = (text) => {
      let index = names.indexOf(text);
      if (index < 0) {
        index = names.length; names.push(text);
        const entry = runtime.object(0x1000); runtime.text.set(entry + 0x10, text);
        poke(table + 4 * index, entry); poke(BASE + 0x13904F0, names.length);
      }
      return index;
    };
    name('None');
    const all = runtime.object(0x10000); // the table of all objects
    let objectCount = 0;
    poke(BASE + 0x139042C, all);
    const make = (text, cls, size = 0x200) => {
      const at = runtime.object(size); poke(at + 0x28, name(text)); poke(at + 0x30, cls);
      poke(all + 4 * objectCount, at); objectCount++; poke(BASE + 0x1390430, objectCount);
      return at;
    };
    const classClass = make('Class', 0); poke(classClass + 0x30, classClass);
    const cls = (text, extendsFrom) => { const at = make(text, classClass); poke(at + 0x40, extendsFrom); return at; };
    const objectClass = cls('Object', 0);
    const boolProperty = cls('BoolProperty', objectClass);
    const intProperty = cls('IntProperty', objectClass);
    const fields = (owner, list) => {
      let previous = null;
      for (const [text, kind, offset] of list) {
        const field = make(text, kind); poke(field + 0x74, offset);
        if (previous === null) { poke(owner + 0x5C, field); } else { poke(previous + 0x44, field); }
        previous = field;
      }
    };
    const actor = cls('Actor', objectClass);
    fields(actor, [['bHidden', boolProperty, 0xCC], ['bDeleteMe', boolProperty, 0xCC]]);
    const gatherer = cls('Gatherer', actor);
    fields(gatherer, [['bIsSaved', boolProperty, 0xFA8], ['VulnerableState', intProperty, 0xFFC],
      ['bCannotBecomeUnconscious', boolProperty, 0x1038], ['HasBeenSavedOrPacified', boolProperty, 0x1038],
      ['bIsGathering', boolProperty, 0x1038]]);
    const spawned = cls('SpawnedGatherer', gatherer);
    const splicer = cls('Splicer', actor);
    const questClass = cls('Quest', objectClass);
    fields(questClass, [['NumberOfObjectivesToComplete', intProperty, 0x90],
      ['HasSeenCurrentHint', boolProperty, 0xA8], ['Completed', boolProperty, 0xA8], ['Hidden', boolProperty, 0xB0]]);
    const quest = (text, completed = false) => {
      const at = make(text, questClass); poke(at + 0xA8, completed ? 0x3 : 0x1); return at;
    };
    return { spawned, splicer, make, quest };
  };
  // A Little Sister in a level. Her HasBeenSavedOrPacified is bit 0x2 of +0x1038.
  runtime.sister = (level, model) => {
    const at = runtime.object(0x1100); poke(at + 0x30, model.spawned); poke(at + 0x1038, 0);
    runtime.addActor(level, at);
    return at;
  };

  // The local player in a level: controller, pawn, HUD and the player object (viewport), all pointing at each other.
  runtime.player = (level, { viewport = null } = {}) => {
    const controller = runtime.object(); const pawn = runtime.object(); const hud = runtime.object();
    const player = viewport || runtime.object();
    [controller, pawn, hud].forEach((actor) => runtime.addActor(level, actor));
    poke(controller + 0x450, pawn); poke(pawn + 0x450, controller);
    poke(controller + 0x71C, hud); poke(hud + 0x470, controller);
    poke(controller + 0x590, player);
    poke(player, BASE + 0x910000); poke(player + 0x40, BASE + 0x920000); poke(player + 0x44, BASE + 0x930000);
    poke(BASE + 0x930000, PLAYER_EXEC); poke(PLAYER_EXEC, 0xE944E983); poke(PLAYER_EXEC + 4, 0x12345678);
    poke(player + 0x48, controller);
    poke(pawn + 0x57C, 875); poke(pawn + 0xAF8, 1000); poke(pawn + 0xAEC, 123); poke(pawn + 0xAF4, 40);
    return { controller, pawn, hud, player };
  };
  // The engine's own route to a player object: engine -> client -> list of viewports.
  runtime.client = (player) => {
    const client = runtime.object(); const list = runtime.object();
    poke(ENGINE + 0x4C, client); poke(client + 0x44, list); poke(client + 0x48, 1); poke(client + 0x4C, 33);
    poke(list, player);
    return client;
  };
  // A ready-made game: the engine, an entry level and a level with the local player in it.
  runtime.game = () => {
    runtime.engine();
    const entry = runtime.level('Entry', { extra: 1 });
    const level = runtime.level('1-medical', { extra: 6, capacity: 1030 });
    const local = runtime.player(level);
    runtime.playerOnly.add('GiveItem');
    return { entry, level, ...local };
  };

  runtime.results = () => runtime.messages.filter((m) => m.type === 'exec_result');
  runtime.logged = (text) => runtime.logs.some((line) => line.includes(text));
  return runtime;
}

const exec = (r, id, command) => r.context.rpc.exports.exec(id, command);
const state = (r) => r.context.rpc.exports.state();
const raisedMessages = (r) => r.messages.filter((m) => m.type === 'exec_raised');

// What the client is told about one exception the game raises while a command runs: the text, or null if nothing.
function described(build) {
  const r = makeRuntime(); r.game(); r.load(); r.frame();
  r.raises.set('Go', [build(r)]);
  exec(r, 1, 'GiveItem 1 Go'); r.frame();
  assert.deepStrictEqual(r.answers, [false], 'the exception was not left to the game');
  assert.strictEqual(r.guardTouched, false, 'a guard page was read');
  assert.deepStrictEqual(r.results().map((m) => m.handled), [true], 'the command itself must not be affected');
  const told = raisedMessages(r);
  assert.ok(told.length <= 1);
  return told.length === 0 ? null : told[0].what;
}

const tests = {
  // ---- the local player's command path ----
  'finds the local player and sends its commands there': () => {
    const r = makeRuntime(); const game = r.game(); r.load();
    assert.strictEqual(r.hooks.length, 1);
    assert.strictEqual(r.hooks[0].address, BASE + STEAM.delta);
    r.frame();
    assert.strictEqual(state(r).playerPath, 'ready');
    assert.strictEqual(exec(r, 7, 'GiveItem 10 ShockGame.ADAM'), 1);
    assert.strictEqual(r.results().length, 0, 'nothing runs until the game thread ticks');
    r.frame();
    assert.deepStrictEqual(r.results().map((m) => [m.id, m.command, m.handled, m.via]),
      [[7, 'GiveItem 10 ShockGame.ADAM', true, 'player']]);
    assert.deepStrictEqual(r.execCalls.map((c) => [c.who, c.self]), [['player', game.player + 0x44]],
      'this must be the FExec part of the player object, and the engine is not asked');
  },
  'the engine leads straight to the player, with no search through the actors': () => {
    const r = makeRuntime(); const game = r.game();
    r.client(game.player);
    r.poke(game.controller + 0x71C, 0); // no HUD, so a search through the actors would not find this controller
    r.load(); r.frame();
    assert.strictEqual(state(r).playerPath, 'ready');
    assert.ok(r.logged('found the local player through the engine'));
    exec(r, 1, 'GiveItem 1 A'); r.frame();
    assert.deepStrictEqual(r.execCalls.map((c) => [c.who, c.self]), [['player', game.player + 0x44]]);
  },
  'the engine\'s route is not trusted when its player and controller do not point at each other': () => {
    const r = makeRuntime(); const game = r.game();
    const imposter = r.object(); const stranger = r.object();
    [0, 0x40, 0x44].forEach((offset) => r.poke(imposter + offset, BASE + 0x930000));
    r.poke(imposter + 0x48, stranger); // its "controller" does not point back
    r.client(imposter);
    r.load(); r.frame();
    assert.ok(r.logged('found the local player among the actors'), 'the search still finds the real one');
    exec(r, 1, 'GiveItem 1 A'); r.frame();
    assert.deepStrictEqual(r.execCalls.map((c) => c.self), [game.player + 0x44]);
  },
  'a player without a body is not given anything': () => {
    const r = makeRuntime(); const game = r.game(); r.load(); r.frame();
    r.poke(game.controller + 0x450, 0); // a menu, or the moment between two levels
    r.frames(4);
    assert.strictEqual(state(r).playerReady, false);
    exec(r, 1, 'GiveItem 1 A'); r.frames(5);
    assert.strictEqual(r.execCalls.length, 0);
    r.poke(game.controller + 0x450, game.pawn); r.frames(4);
    assert.strictEqual(state(r).playerReady, true);
    assert.deepStrictEqual(r.results().map((m) => m.via), ['player']);
    r.poke(game.pawn + 0x450, 0); // a pawn that belongs to somebody else does not count either
    exec(r, 2, 'GiveItem 1 B'); r.frames(5);
    assert.strictEqual(r.results().length, 1);
  },
  'a command the player does not take goes on to the engine': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frame();
    exec(r, 1, 'get ShockPlayer Health'); r.frame();
    assert.deepStrictEqual(r.execCalls.map((c) => c.who), ['player', 'engine']);
    assert.strictEqual(r.execCalls[1].self, ENGINE + 0x40, 'this must be the FExec sub-object of the engine');
    assert.deepStrictEqual(r.results().map((m) => [m.handled, m.via]), [[true, 'engine']]);
  },
  'a command nobody takes is reported as not handled': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frame(); r.refuse.add('Bogus');
    exec(r, 3, 'GiveItem 1 Bogus.Class'); r.frame();
    assert.deepStrictEqual(r.results().map((m) => [m.id, m.handled, m.via]), [[3, false, null]]);
    assert.strictEqual(r.execCalls.length, 2, 'both were asked');
  },
  'player commands only run in the tick of the level the player is in': () => {
    const r = makeRuntime(); const game = r.game(); r.load(); r.frame();
    assert.deepStrictEqual(r.levels, [game.entry, game.level], 'the entry level ticks first in every frame');
    exec(r, 1, 'GiveItem 1 A'); exec(r, 2, 'GiveItem 1 B');
    r.frame();
    assert.deepStrictEqual(r.results().map((m) => m.id), [1], 'one command per frame, although two levels ticked');
    r.frame();
    assert.deepStrictEqual(r.results().map((m) => [m.id, m.via]), [[1, 'player'], [2, 'player']]);
  },
  'commands wait while a level is loading': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frame();
    r.memory.set(BASE + 0x1356680, 1);
    exec(r, 1, 'GiveItem 1 A'); r.frames(5);
    assert.strictEqual(r.results().length, 0);
    assert.strictEqual(state(r).pending, 1);
    r.memory.set(BASE + 0x1356680, 0); r.frame();
    assert.deepStrictEqual(r.results().map((m) => m.via), ['player']);
  },
  'commands wait while the player has no controller in a ticking level': () => {
    const r = makeRuntime(); const game = r.game(); r.load(); r.frame();
    const elsewhere = r.object(); r.poke(game.controller + 0xF8, elsewhere); // as if its level were gone
    exec(r, 1, 'GiveItem 1 A'); r.frames(5);
    assert.strictEqual(r.execCalls.length, 0, 'not even the engine is asked: the item would be reported as refused');
    r.poke(game.controller + 0xF8, r.levelData.get(game.level).info); r.frame();
    assert.strictEqual(r.results().length, 1);
  },
  'a controller that no longer points at the player is not used': () => {
    const r = makeRuntime(); const game = r.game(); r.load(); r.frame();
    r.poke(game.controller + 0x590, 0); // as if the engine had taken the player away from it
    exec(r, 1, 'GiveItem 1 A'); r.frames(5);
    assert.strictEqual(r.execCalls.length, 0);
    r.poke(game.controller + 0x590, game.player); r.frame();
    assert.deepStrictEqual(r.results().map((m) => m.via), ['player']);
  },
  'an actor that only looks like a player controller is passed over': () => {
    const r = makeRuntime(); r.engine();
    const level = r.level('1-medical', { extra: 2 });
    // The imposter comes first in the level's list. Everything about it fits, except that what sits where its HUD
    // would be is not an actor of the level.
    const imposter = r.object(); r.addActor(level, imposter);
    const notAnActor = r.object(); r.poke(imposter + 0x71C, notAnActor); r.poke(notAnActor + 0x470, imposter);
    const fakePlayer = r.object(); r.poke(imposter + 0x590, fakePlayer); r.poke(fakePlayer + 0x48, imposter);
    [0, 0x40, 0x44].forEach((offset) => r.poke(fakePlayer + offset, BASE + 0x930000));
    const real = r.player(level); r.playerOnly.add('GiveItem');
    r.load(); r.frame();
    exec(r, 1, 'GiveItem 1 A'); r.frame();
    assert.deepStrictEqual(r.execCalls.map((c) => c.self), [real.player + 0x44]);
  },
  'a new controller after a level change is picked up without searching again': () => {
    const r = makeRuntime(); const game = r.game(); r.load(); r.frame();
    const next = r.level('2-fisheries', { extra: 4 }); r.levels.splice(r.levels.indexOf(game.level), 1);
    const queriesBefore = r.queries;
    const fresh = r.player(next, { viewport: game.player }); // same player object, new controller
    assert.notStrictEqual(fresh.controller, game.controller);
    exec(r, 1, 'GiveItem 1 A'); r.frame();
    assert.deepStrictEqual(r.results().map((m) => m.via), ['player']);
    assert.ok(r.queries - queriesBefore < 50, 'no new search through the actors');
    r.frames(3);
    assert.strictEqual(state(r).map, '2-fisheries');
  },
  'commands wait while the player is still being looked for, then go to the engine alone': () => {
    const r = makeRuntime(); r.engine(); r.level('Entry', { extra: 2 }); r.load();
    exec(r, 1, 'get ShockPlayer Health'); r.frames(20);
    assert.strictEqual(r.results().length, 0, 'two seconds in, still looking');
    assert.strictEqual(state(r).playerPath, 'unknown');
    r.frames(15);
    assert.strictEqual(state(r).playerPath, 'unavailable');
    assert.deepStrictEqual(r.results().map((m) => [m.handled, m.via]), [[true, 'engine']]);
    assert.ok(r.logged('could not find the local player'));
  },
  'a player that turns up later is found and used': () => {
    const r = makeRuntime(); r.engine(); r.level('Entry', { extra: 2 }); r.load(); r.frames(40);
    assert.strictEqual(state(r).playerPath, 'unavailable');
    const level = r.level('1-welcome'); r.player(level); r.playerOnly.add('GiveItem');
    r.frame();
    assert.strictEqual(state(r).playerPath, 'ready', 'a level never seen before is searched at once');
    exec(r, 1, 'GiveItem 1 A'); r.frame();
    assert.deepStrictEqual(r.results().map((m) => m.via), ['player']);
  },
  'a player object that does not point back is not used': () => {
    const r = makeRuntime(); const game = r.game(); r.poke(game.player + 0x48, game.pawn); r.load();
    r.frames(40);
    assert.strictEqual(state(r).playerPath, 'unavailable');
    exec(r, 1, 'GiveItem 1 A'); r.frame();
    assert.deepStrictEqual(r.execCalls.map((c) => c.who), ['engine']);
  },
  'a player object without game vtables is not used': () => {
    const r = makeRuntime(); const game = r.game(); r.poke(game.player + 0x44, 0x33330000); r.load();
    r.frames(40);
    assert.strictEqual(state(r).playerPath, 'unavailable');
  },
  'a player whose Exec is not in the game is not called': () => {
    const r = makeRuntime(); const game = r.game(); r.load(); r.frame();
    assert.strictEqual(state(r).playerPath, 'ready');
    r.poke(BASE + 0x930000, 0x33330000); // slot 0 now points outside the game
    exec(r, 1, 'GiveItem 1 A'); r.frames(5);
    assert.strictEqual(r.execCalls.length, 0);
    assert.strictEqual(game.player > 0, true);
  },
  'values that only look like pointers are never followed into a guard page': () => {
    const r = makeRuntime(); const game = r.game();
    // Rubbish where the predicted fields would be: in every plain actor (past its end), and in the controller.
    for (const actor of r.levelData.get(game.level).actors) {
      if (![game.controller, game.pawn, game.hud].includes(actor)) { r.poke(actor + 0x71C, GUARD_PAGE + 0x10); }
    }
    r.poke(game.controller + 0x590, GUARD_PAGE + 0x20); // the Player prediction is wrong on this "build"
    for (let offset = 0x5A0; offset < 0x700; offset += 4) { r.poke(game.controller + offset, GUARD_PAGE + offset); }
    r.poke(game.level + 0x7C, GUARD_PAGE + 0x40); r.poke(game.level + 0x60, GUARD_PAGE + 0x80); // "strings"
    r.poke(game.level + 0x64, 5); r.poke(game.level + 0x68, 8);
    r.poke(ENGINE + 0x60, GUARD_PAGE + 0x100); r.poke(ENGINE + 0x64, GUARD_PAGE + 0x104); // engine fields
    r.load();
    r.context.probe(); r.frames(40);
    exec(r, 1, 'get ShockPlayer Health'); r.frames(3);
    assert.ok(r.logged('probe: done'));
    assert.ok(r.logged('does NOT point back at the controller'));
    assert.strictEqual(state(r).playerPath, 'unavailable');
    assert.strictEqual(r.guardTouched, false, 'a guard page was read');
    assert.strictEqual(r.results().length, 1);
  },
  'without VirtualQuery the player is not looked for at all': () => {
    const r = makeRuntime({ virtualQuery: false }); r.game(); r.load();
    assert.strictEqual(state(r).playerPath, 'unavailable');
    exec(r, 1, 'get ShockPlayer Health'); r.frame();
    assert.deepStrictEqual(r.results().map((m) => m.via), ['engine'], 'no waiting either');
    assert.ok(r.logged('VirtualQuery was not found'));
  },
  'an object whose first actor is not a LevelInfo is not treated as a level': () => {
    const r = makeRuntime(); const game = r.game();
    r.poke(r.levelData.get(game.level).info + 0xF8, 0); // the check that it is a level fails
    r.load(); r.frames(40);
    assert.strictEqual(state(r).playerPath, 'unavailable', 'its actors were not searched');
    assert.deepStrictEqual([...state(r).levels], ['Entry: 2 actors, room for 2']);
    r.context.probe(); r.frames(12);
    assert.ok(r.logged('LevelInfo NOT confirmed'));
    assert.ok(r.logged('first words:'));
  },
  'something that is not a level in the hook is left alone': () => {
    const r = makeRuntime(); r.engine(); r.load(); // no levels: the hook is handed an unreadable pointer
    exec(r, 1, 'get ShockPlayer Health'); r.frames(40);
    assert.strictEqual(state(r).playerPath, 'unavailable');
    assert.deepStrictEqual(r.results().map((m) => m.via), ['engine']);
    assert.ok(!r.logged('problem inside the game-thread hook'));
  },

  // ---- probe() and state() ----
  'probe prints what it found': () => {
    const r = makeRuntime(); const game = r.game();
    const client = r.object(); r.poke(ENGINE + 0x60, client); // engine -> some object -> [player], found by search
    const list = r.object(); r.poke(list, game.player);
    r.poke(client + 0x58, list); r.poke(client + 0x5C, 1); r.poke(client + 0x60, 1);
    r.load(); r.frame();
    assert.ok(r.context.probe().startsWith('probing'));
    r.frames(12);
    const report = r.logs.join('\n');
    assert.ok(report.includes('probe: level value'), report);
    assert.ok(report.includes("stat instructions found in the game's code: health 1, EVE 1, dollars 1, ADAM 0"),
      report);
    assert.ok(report.includes("pawn numbers at the older build's offsets: health 87.5, EVE 100, dollars 123, ADAM 40"),
      report);
    assert.ok(report.includes('"1-medical"') && report.includes('"Entry"'), report);
    assert.ok(report.includes('room for 1030'), report);
    assert.ok(report.includes('1 player controller(s)'), report);
    assert.ok(report.includes('points back at the controller'), report);
    assert.ok(report.includes('Exec module+0x700000'), report);
    assert.ok(report.includes('Exec starts with 0xe944e983 0x12345678'), report);
    assert.ok(report.includes('fields of the player holding the controller: +0x48'), report);
    assert.ok(report.includes('player path: ready'), report);
    assert.ok(report.includes('engine+0x60') && report.includes('its list at +0x58'), report);
    assert.ok(report.includes('probe: done'), report);
    assert.strictEqual(r.logs.filter((line) => line.includes('probe: done')).length, 1);
    r.frames(12);
    assert.strictEqual(r.logs.filter((line) => line.includes('probe: done')).length, 1, 'one report per probe()');
  },
  'probe shows where the player really is when the prediction is off': () => {
    const r = makeRuntime(); const game = r.game();
    r.poke(game.controller + 0x590, 0); r.poke(game.controller + 0x5A4, game.player); // the field is elsewhere
    r.load(); r.context.probe(); r.frames(12);
    const report = r.logs.join('\n');
    assert.ok(report.includes('does NOT point back at the controller'), report);
    assert.ok(report.includes(`+0x5a4 -> 0x${game.player.toString(16)} (holds the controller at +0x48)`), report);
  },
  'state reports the map, the levels and the player path': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frames(2);
    const seen = state(r);
    assert.strictEqual(seen.map, '1-medical');
    assert.strictEqual(seen.playerPath, 'ready');
    assert.strictEqual(seen.playerReady, true);
    assert.deepStrictEqual([...seen.levels], ['Entry: 2 actors, room for 2', '1-medical: 10 actors, room for 1030']);
    assert.ok(typeof seen.agent === 'string');
    r.levels.length = 0; r.frames(30); // nothing ticks any more (a loading screen, say)
    assert.strictEqual(state(r).map, null);
    assert.strictEqual(state(r).playerReady, false);
    assert.strictEqual(state(r).levels.length, 0);
  },

  'a map name that is not plain text is not reported': () => {
    const r = makeRuntime(); const game = r.game();
    r.text.set(r.memory.get(game.level + 0x7C), '1-med\u0001cal'); // same length, one unprintable character
    r.load(); r.frames(2);
    assert.strictEqual(state(r).map, null);
    assert.strictEqual(state(r).playerPath, 'ready', 'the rest still works');
  },

  // ---- the queue ----
  'one command per tick and a minimum gap': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frame();
    exec(r, 1, 'GiveItem 1 a'); exec(r, 2, 'GiveItem 1 b');
    r.frame(100); r.frame(10);
    assert.strictEqual(r.results().length, 1, 'second command must wait for the gap');
    r.frame(100);
    assert.deepStrictEqual(r.results().map((m) => m.id), [1, 2]);
  },
  'keeps commands queued while the engine pointer is null': () => {
    const r = makeRuntime(); r.game(); r.engine(0); r.load(); r.frame();
    exec(r, 1, 'GiveItem 1 a'); r.frame(); r.frame();
    assert.strictEqual(r.results().length, 0);
    assert.strictEqual(state(r).pending, 1);
    r.engine(); r.frame();
    assert.strictEqual(r.results().length, 1);
  },
  'refuses on a vtable mismatch instead of calling': () => {
    const r = makeRuntime(); r.game(); r.engine(ENGINE, 0x123456); r.load();
    assert.ok(r.messages.some((m) => m.type === 'exec_disabled'));
    assert.strictEqual(state(r).execAvailable, false);
    exec(r, 9, 'GiveItem 1 X'); r.frame();
    assert.strictEqual(r.execCalls.length, 0, 'neither the player nor the engine may be called');
    assert.strictEqual(r.results()[0].handled, false);
    assert.strictEqual(r.results()[0].id, 9);
  },
  'vtable changing later disables commands': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frame();
    r.engine(ENGINE, 0x999999);
    exec(r, 1, 'GiveItem 1 a'); r.frame();
    assert.strictEqual(r.execCalls.length, 0);
    assert.ok(r.messages.some((m) => m.type === 'exec_disabled'));
  },
  'a command the agent itself fails on is reported as final, and commands stop': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frame(); r.faults.add('Crashy');
    exec(r, 5, 'GiveItem 1 Crashy.Class'); exec(r, 6, 'GiveItem 1 Fine.Class');
    r.frame(); r.frame(); r.frame();
    assert.deepStrictEqual(r.results().map((m) => [m.id, m.handled, m.fatal === true]), [[5, false, true]]);
    assert.strictEqual(r.execCalls.length, 1, 'nothing else is run by an agent that has failed');
    const disabled = r.messages.filter((m) => m.type === 'exec_disabled');
    assert.strictEqual(disabled.length, 1);
    assert.strictEqual(disabled[0].reason,
      'the agent failed while running "GiveItem 1 Crashy.Class" (expected a pointer)');
    assert.strictEqual(r.results()[0].reason, disabled[0].reason);
    assert.strictEqual(state(r).execAvailable, false);
  },
  'commands typed at the prompt are logged with who took them': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frame();
    assert.strictEqual(r.context.give('GiveItem 10 ShockGame.ADAM'), 'queued (1 waiting)');
    r.frame();
    r.context.give('get ShockPlayer Health'); r.frame();
    r.refuse.add('Nope'); r.context.give('Nope'); r.frame();
    assert.ok(r.logged('handled by the player: GiveItem 10 ShockGame.ADAM'));
    assert.ok(r.logged('handled by the engine: get ShockPlayer Health'));
    assert.ok(r.logged('NOT handled: Nope'));
    assert.deepStrictEqual(r.results().map((m) => m.id), [null, null, null]);
  },

  // ---- what the game prints in answer ----
  'what the game prints while a command runs is passed on': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frame();
    r.prints.set('get ShockPlayer Health', ['200.000000']);
    r.prints.set('obj linkers', ['Linkers:', '', 'ShockGame (Package): Names=1', 'caf\u00e9\ttab']);
    exec(r, 1, 'get ShockPlayer Health'); r.frame();
    exec(r, 2, 'obj linkers'); r.frame();
    exec(r, 3, 'GiveItem 1 ShockGame.HealthUpgrade'); r.frame();
    assert.deepStrictEqual(r.results().map((m) => [m.id, m.via, [...m.output], m.outputLines]), [
      [1, 'engine', ['200.000000'], 1],
      [2, 'engine', ['Linkers:', '', 'ShockGame (Package): Names=1', 'caf??tab'], 4],
      [3, 'player', [], 0],
    ]);
    assert.strictEqual(r.outputDevice(new Ptr(0x66660000, r), new Ptr(0, r)), 1, 'the game is always told it worked');
  },
  'at the prompt the printed answer is shown in full, and the client gets the start of it': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frame();
    const lines = []; for (let i = 0; i < 3100; i++) { lines.push(`object ${i}`); }
    r.prints.set('obj list', lines);
    r.context.give('obj list class=class'); r.frame();
    assert.ok(r.logged('handled by the engine: obj list class=class'));
    assert.ok(r.logged('  | object 0') && r.logged('  | object 2999'));
    assert.ok(!r.logged('  | object 3000'));
    assert.ok(r.logged('  | ... and 100 more lines'));
    assert.strictEqual(r.results()[0].output.length, 20);
    assert.strictEqual(r.results()[0].outputLines, 3100);
    exec(r, 5, 'obj list again'); r.frame();
    assert.ok(!r.logged('obj list again'), 'what the client asks for is not echoed at the prompt');
    assert.strictEqual(r.results()[1].outputLines, 3100, 'the count starts again for every command');
  },
  'a very long line is cut': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frame();
    r.prints.set('long', ['x'.repeat(1000)]);
    exec(r, 1, 'long'); r.frame();
    assert.strictEqual(r.results()[0].output[0].length, 300);
  },
  'what is printed when no command is running is ignored': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frame();
    assert.strictEqual(r.print('the game talking to itself'), 1);
    exec(r, 1, 'get ShockPlayer Health'); r.frame();
    assert.deepStrictEqual([...r.results()[0].output], []);
    assert.strictEqual(r.print('later'), 1);
    exec(r, 2, 'get ShockPlayer Health'); r.frame();
    assert.deepStrictEqual([...r.results()[1].output], []);
  },
  'something that is not text is not read as text': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frame();
    r.prints.set('get ShockPlayer Health', [GUARD_PAGE + 0x10, 0x12340000, 8, 'the real line']);
    exec(r, 1, 'get ShockPlayer Health'); r.frame();
    assert.deepStrictEqual([...r.results()[0].output], ['the real line']);
    assert.strictEqual(r.guardTouched, false, 'a guard page was read');
  },
  'a command the agent fails on leaves no half answer behind': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frame();
    r.faults.add('Crashy');
    exec(r, 5, 'GiveItem 1 Crashy.Class'); r.frame();
    assert.strictEqual(r.print('after the failure'), 1, 'still safe to call');
    assert.strictEqual(r.results()[0].fatal, true);
    assert.strictEqual(r.results()[0].output, undefined);
    assert.strictEqual(r.raise({ args: r.thrown('.PAG', 'later') }), false);
    assert.ok(!r.messages.some((m) => m.type === 'exec_raised'), 'and what is raised later belongs to no command');
  },
  'without VirtualQuery nothing is read, so nothing is kept': () => {
    const r = makeRuntime({ virtualQuery: false }); r.game(); r.load();
    r.prints.set('get ShockPlayer Health', ['200.000000']);
    exec(r, 1, 'get ShockPlayer Health'); r.frame();
    assert.deepStrictEqual(r.results().map((m) => [m.via, [...m.output]]), [['engine', []]]);
  },

  // ---- what the game raises while a command runs ----
  'the game is called so that what it raises stays its own': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frame();
    exec(r, 1, 'GiveItem 1 A'); r.frame(); exec(r, 2, 'get ShockPlayer Health'); r.frame();
    assert.deepStrictEqual(r.execCalls.map((c) => c.who), ['player', 'player', 'engine']);
    for (const address of [PLAYER_EXEC, BASE + STEAM.exec]) {
      assert.deepStrictEqual({ ...r.nativeOptions.get(address) }, { abi: 'thiscall', exceptions: 'propagate' });
    }
  },
  'an exception the game raises and deals with does not stop the command': () => {
    // What happened in the real game on 2026-10-07, with Frida left at its default: the tonic was given, the game
    // raised something of its own on the way, Frida took that for a fault and the agent turned commands off.
    const r = makeRuntime(); r.game(); r.load(); r.frame();
    r.raises.set('ArmoredBodyTwo', [{ args: r.thrown('.PAG', "Can't find file for package 'Sounds'") }]);
    exec(r, 2, 'GiveItem 1 ShockDesignerClasses.ArmoredBodyTwo'); r.frame();
    exec(r, 3, 'GiveItem 1 ShockGame.Next'); r.frame();
    assert.deepStrictEqual(r.results().map((m) => [m.id, m.handled, m.via, m.raised, m.fatal === true]),
      [[2, true, 'player', 1, false], [3, true, 'player', 0, false]]);
    assert.ok(!r.messages.some((m) => m.type === 'exec_disabled'), 'commands stay on');
    assert.strictEqual(state(r).execAvailable, true);
    assert.deepStrictEqual(r.messages.filter((m) => m.id === 2).map((m) => m.type),
      ['exec_started', 'exec_raised', 'exec_result'], 'in the order things happened');
    const told = raisedMessages(r)[0];
    assert.deepStrictEqual([told.id, told.command, told.what], [2, 'GiveItem 1 ShockDesignerClasses.ArmoredBodyTwo',
      'C++ exception: "Can\'t find file for package \'Sounds\'"']);
    assert.deepStrictEqual(r.answers, [false], 'the exception was left to the game');
  },
  'what the game raises is described': () => {
    const text = "Can't find file for package 'Sounds'";
    const cases = [
      // text the engine throws, wherever Windows put the record of it
      [(r) => ({ args: r.thrown('.PAG', text) }), `C++ exception: "${text}"`],
      [(r) => ({ args: r.thrown('.PB_W', text), layout: 'full' }), `C++ exception: "${text}"`],
      [(r) => ({ args: r.thrown('.PA_W', text), layout: 'padded' }), `C++ exception: "${text}"`],
      [(r) => ({ args: r.thrown('.PBG', text), layout: 'argument' }), `C++ exception: "${text}"`],
      [(r) => ({ args: r.thrown('.PAD', 'plain text', { plain: true }) }), 'C++ exception: "plain text"'],
      [(r) => ({ args: r.thrown('.PBD', 'plain text', { plain: true }) }), 'C++ exception: "plain text"'],
      // other things that get thrown
      [(r) => ({ args: r.thrown('.H', 1) }), 'C++ exception: the number 1'],
      [(r) => ({ args: r.thrown('.?AVbad_alloc@std@@', 0) }), 'C++ exception of type .?AVbad_alloc@std@@'],
      [() => ({ args: [0x19930520, 0, 0] }), 'C++ exception, passed on from the one before'],
      [() => ({ args: [0x19930520] }), 'C++ exception, passed on from the one before'],
      // text that is odd, long or not there
      [(r) => ({ args: r.thrown('.PAG', 'caf\u00e9\ttab\u0001') }), 'C++ exception: "caf? tab?"'],
      [(r) => ({ args: r.thrown('.PAG', '  padded \r\n') }), 'C++ exception: "padded"'],
      [(r) => ({ args: r.thrown('.PAG', 'x'.repeat(1000)) }), `C++ exception: "${'x'.repeat(300)}"`],
      [(r) => ({ args: r.thrown('.PAG', '') }), 'C++ exception of type .PAG'],
      [(r) => ({ args: r.thrown('.PAG', 0x12340000) }), 'C++ exception of type .PAG'],
      [(r) => ({ args: r.thrown('.PAG', GUARD_PAGE + 0x10) }), 'C++ exception of type .PAG'],
      [(r) => ({ args: r.thrown(null, 5) }), 'C++ exception'],
      [(r) => ({ args: [0x19930520, r.pages(1, 1), GUARD_PAGE + 0x40] }), 'C++ exception'],
      [(r) => ({ args: [0x19930520, r.pages(1, 1), 0x12340000] }), 'C++ exception'],
      // what a program tells a debugger
      [(r) => ({ code: DEBUG_TEXT, args: [24, r.words('Warning: Failed to load\r\n', true)] }),
        'debug text: "Warning: Failed to load"'],
      [(r) => ({ code: DEBUG_TEXT_WIDE, args: [5, r.words('wide'), 5, r.words('not this one', true)] }),
        'debug text: "wide"'],
      [() => ({ code: DEBUG_TEXT, args: [5, 0x12340000] }), 'debug text that could not be read'],
      [() => ({ code: DEBUG_TEXT, args: [5, GUARD_PAGE + 0x20] }), 'debug text that could not be read'],
      [() => ({ code: DEBUG_TEXT, args: [5] }), 'debug text that could not be read'],
      [() => ({ code: THREAD_NAME, args: [0x1000, 0, 1, 0] }), null],
      // anything else
      [() => ({ code: 0xC0000008 }), 'exception 0xc0000008 at KERNELBASE.dll+0x12345'],
      [() => ({ code: 0xC0000008, args: new Array(15).fill(7), layout: 'compact' }),
        'exception 0xc0000008 at KERNELBASE.dll+0x12345'],
      [() => ({ layout: 'none' }), 'an exception at KERNELBASE.dll+0x12345'],
      [(r) => ({ details: { type: 'system', address: new Ptr(RAISED_AT, r) } }),
        'an exception at KERNELBASE.dll+0x12345'],
      [(r) => ({ type: 'access-violation', at: BASE + 0x4C1234,
        memory: { operation: 'read', address: new Ptr(0x10, r) } }),
      'access violation (read of 0x10) at module+0x4c1234'],
      [() => ({ type: 'illegal-instruction', at: 0x12345678 }), 'illegal instruction at 0x12345678'],
    ];
    for (const [build, expected] of cases) {
      assert.strictEqual(described(build), expected);
    }
  },
  'a record that only looks like one is not taken for one': () => {
    // The right address, but more arguments than a record can have: not a record.
    const r = makeRuntime(); r.game(); r.load(); r.frame();
    const details = r.exception({ layout: 'none' });
    const lookalike = details.nativeContext.value - 0x30;
    r.poke(lookalike, CPP); r.poke(lookalike + 0xC, RAISED_AT); r.poke(lookalike + 0x10, 16);
    r.raises.set('Go', [{ details }]);
    exec(r, 1, 'GiveItem 1 Go'); r.frame();
    r.poke(lookalike + 0x10, -1); // ... or fewer than none
    exec(r, 2, 'GiveItem 1 Go'); r.frame();
    r.poke(lookalike + 0x10, 15); // with as many as a record can have, it is one
    exec(r, 3, 'GiveItem 1 Go'); r.frame();
    assert.deepStrictEqual(raisedMessages(r).map((m) => [m.id, m.what]), [
      [1, 'an exception at KERNELBASE.dll+0x12345'], [2, 'an exception at KERNELBASE.dll+0x12345'],
      [3, 'C++ exception, passed on from the one before']]);
  },
  'text is read no further than the memory that is there': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frame();
    const page = r.pages(3, 1); // of the three pages only the first is there
    r.text.set(page + 0xFFA, 'abcdef'); // room for six bytes of it: three characters, and no closing zero
    const args = r.thrown('.PAG', page + 0xFFA);
    r.raises.set('Go', [{ args }]);
    exec(r, 1, 'GiveItem 1 Go'); r.frame();
    assert.strictEqual(raisedMessages(r)[0].what, 'C++ exception: "abc"');
    assert.ok(r.bytesRead.includes(6), `sizes read: ${r.bytesRead}`);
  },
  'text that runs on into the next page is read to its end': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frame();
    const page = r.pages(3, 2);
    r.text.set(page + 0xFFA, 'abcdef');
    r.raises.set('Go', [{ args: r.thrown('.PAG', page + 0xFFA) }]);
    exec(r, 1, 'GiveItem 1 Go'); r.frame();
    assert.strictEqual(raisedMessages(r)[0].what, 'C++ exception: "abcdef"');
  },
  'one exception handed up from function to function is told once': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frame();
    const first = { args: r.thrown('.PAG', 'first') }; const second = { args: r.thrown('.PAG', 'second') };
    r.raises.set('Go', [first, first, first, second, first]);
    exec(r, 1, 'GiveItem 1 Go'); r.frame();
    assert.deepStrictEqual(raisedMessages(r).map((m) => m.what),
      ['C++ exception: "first"', 'C++ exception: "second"', 'C++ exception: "first"']);
    assert.strictEqual(r.results()[0].raised, 5, 'the count is of all of them');
    assert.deepStrictEqual(r.answers, [false, false, false, false, false], 'told or not, the game gets them all');
    exec(r, 2, 'GiveItem 1 Go'); r.frame();
    assert.strictEqual(raisedMessages(r).filter((m) => m.id === 2).length, 3,
      'what was told for the last command is told again for the next');
  },
  'only so many are passed on': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frame();
    const many = []; for (let i = 0; i < 25; i++) { many.push({ code: 0xC0000100 + i }); }
    r.raises.set('Go', many);
    exec(r, 1, 'GiveItem 1 Go'); r.frame();
    assert.strictEqual(raisedMessages(r).length, 20);
    assert.strictEqual(raisedMessages(r)[19].what, 'exception 0xc0000113 at KERNELBASE.dll+0x12345');
    assert.strictEqual(r.results()[0].raised, 25);
    assert.strictEqual(r.answers.length, 25);
    assert.ok(r.answers.every((answer) => answer === false), 'passed on or not, the game gets them all');
    exec(r, 2, 'GiveItem 1 Go'); r.frame();
    assert.strictEqual(raisedMessages(r).filter((m) => m.id === 2).length, 20, 'and for every command anew');
  },
  'exceptions on other threads and outside commands are none of the agent\'s business': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frame();
    const spec = { args: r.thrown('.PAG', 'elsewhere') };
    assert.strictEqual(r.raise(spec), false, 'before any command');
    r.raises.set('Go', [{ ...spec, thread: 2 }]);
    exec(r, 1, 'GiveItem 1 Go'); r.frame();
    assert.strictEqual(r.raise(spec), false, 'after it');
    assert.strictEqual(raisedMessages(r).length, 0);
    assert.strictEqual(r.results()[0].raised, 0);
    assert.deepStrictEqual(r.answers, [false, false, false]);
    assert.strictEqual(state(r).exceptionsSeen, 3, 'they are counted all the same');
    assert.strictEqual(state(r).watching, true);
    assert.strictEqual(r.bytesRead.length, 0, 'and nothing is read to describe them');
  },
  'nothing is ever taken away from the game': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frame();
    r.raises.set('Go', [
      { args: r.thrown('.H', 1) },
      { type: 'access-violation', at: BASE + 0x1000, memory: { operation: 'write', address: new Ptr(0, r) } },
      { type: 'breakpoint' }, { type: 'stack-overflow' }, { layout: 'none' }, { code: THREAD_NAME },
      { details: null }, { details: {} },
    ]);
    exec(r, 1, 'GiveItem 1 Go'); r.frame();
    r.context.give('GiveItem 1 Go'); r.frame();
    r.context.watch(false); exec(r, 2, 'GiveItem 1 Go'); r.frame();
    assert.strictEqual(r.answers.length, 24);
    assert.ok(r.answers.every((answer) => answer === false), `answers: ${r.answers}`);
    assert.deepStrictEqual(r.results().map((m) => m.handled), [true, true, true], 'and every command went through');
  },
  'a description that goes wrong does not take the next one with it': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frame();
    r.raises.set('Go', [{ details: null }, { args: r.thrown('.PAG', 'still told') }]);
    exec(r, 1, 'GiveItem 1 Go'); r.frame();
    assert.deepStrictEqual(raisedMessages(r).map((m) => m.what), ['C++ exception: "still told"']);
    assert.deepStrictEqual(r.answers, [false, false]);
  },
  'at the prompt, what the game raises is shown as it happens': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frame();
    r.raises.set('Go', [{ args: r.thrown('.PAG', 'no such package') },
      { code: DEBUG_TEXT, args: [3, r.words('hi', true)] }]);
    r.context.give('GiveItem 1 Go'); r.frame();
    const lines = r.logs.map((line) => line.replace('[bioshock-ap] ', ''));
    const first = lines.indexOf('raised during "GiveItem 1 Go": C++ exception: "no such package"');
    const second = lines.indexOf('raised during "GiveItem 1 Go": debug text: "hi"');
    const done = lines.indexOf('handled by the player: GiveItem 1 Go');
    assert.ok(first >= 0 && second > first && done > second, lines.join('\n'));
    assert.strictEqual(lines[done + 1], '  the game raised 2 exceptions of its own meanwhile, and carried on');
    assert.ok(!r.messages.some((m) => m.type === 'exec_raised' || m.type === 'exec_started'),
      'the client is not told about what is typed at the prompt');
    r.raises.set('Go', [{ code: 0xC0000008 }]);
    r.context.give('GiveItem 1 Go'); r.frame();
    assert.ok(r.logged('  the game raised 1 exception of its own meanwhile, and carried on'));
    r.raises.clear(); r.context.give('GiveItem 1 Go'); r.frame();
    assert.strictEqual(r.logs.filter((line) => line.includes('of its own meanwhile')).length, 2,
      'and nothing is said when nothing was raised');
  },
  'watch(false) stops the reports and watch() resumes them': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frame();
    r.raises.set('Go', [{ args: r.thrown('.PAG', 'text') }]);
    assert.ok(r.context.watch(false).startsWith('not reporting'));
    assert.strictEqual(state(r).watching, false);
    exec(r, 1, 'GiveItem 1 Go'); r.frame();
    assert.deepStrictEqual(r.results().map((m) => [m.handled, m.raised]), [[true, 0]]);
    assert.strictEqual(raisedMessages(r).length, 0);
    assert.ok(r.context.watch().startsWith('reporting'));
    assert.strictEqual(state(r).watching, true);
    exec(r, 2, 'GiveItem 1 Go'); r.frame();
    assert.deepStrictEqual(raisedMessages(r).map((m) => m.id), [2]);
    assert.ok(r.context.watch(true).startsWith('reporting'));
  },
  'a Frida that shows no exceptions still runs commands': () => {
    const r = makeRuntime({ exceptionHandler: false }); r.game(); r.load(); r.frame();
    assert.ok(r.logged('cannot watch what the game raises during a command (not a function)'));
    assert.strictEqual(state(r).watching, false);
    assert.ok(r.context.watch().startsWith('this Frida'));
    assert.strictEqual(state(r).watching, false, 'asking for it does not make it so');
    exec(r, 1, 'GiveItem 1 A'); r.frame();
    assert.deepStrictEqual(r.results().map((m) => [m.handled, m.raised]), [[true, 0]]);
  },
  'the client is told when a command goes into the game': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frame();
    r.memory.set(BASE + 0x1356680, 1); // a loading screen
    exec(r, 4, 'GiveItem 1 A'); r.frames(3);
    assert.ok(!r.messages.some((m) => m.type === 'exec_started'), 'not while it only waits in the queue');
    r.memory.set(BASE + 0x1356680, 0); r.frame();
    assert.deepStrictEqual(r.messages.filter((m) => m.id === 4).map((m) => [m.type, m.command]),
      [['exec_started', 'GiveItem 1 A'], ['exec_result', 'GiveItem 1 A']]);
    r.refuse.add('Nope'); exec(r, 5, 'Nope'); r.frame();
    assert.deepStrictEqual(r.messages.filter((m) => m.id === 5).map((m) => m.type), ['exec_started', 'exec_result']);
  },

  // ---- builds and state ----
  'Epic build: state works, commands are off': () => {
    const r = makeRuntime({ moduleSize: EPIC_SIZE, deltaAt: null, scanHits: [0x53D000] });
    r.memory.set(BASE + 0x13853E8, 9317); r.memory.set(BASE + 0x1355A68, 0);
    r.load();
    const seen = state(r);
    assert.strictEqual(seen.build, 'Epic 1.0.127355');
    assert.strictEqual(seen.execAvailable, false);
    assert.strictEqual(seen.levelValue, 9317);
    assert.strictEqual(seen.loading, false);
    assert.strictEqual(seen.hookInstalled, true, 'the hook is found by signature');
    assert.strictEqual(r.hooks[0].address, BASE + 0x53D000);
    assert.ok(r.context.give('x').startsWith('not queued'));
  },
  'ambiguous signature installs no hook': () => {
    const r = makeRuntime({ deltaAt: null, scanHits: [0x1000, 0x2000] }); r.engine(); r.load();
    assert.strictEqual(r.hooks.length, 0);
    assert.strictEqual(state(r).hookInstalled, false);
    assert.ok(r.context.probe().startsWith('no game-thread hook'));
  },
  'reads state through pointer paths': () => {
    const r = makeRuntime(); r.engine();
    r.memory.set(BASE + 0x1386004, 1309); // level value
    r.memory.set(BASE + 0x1356680, 1); // loading
    r.memory.set(BASE + 0x1356620, 0x60000000); r.memory.set(0x60000000 + 0x214, 0x61000000);
    r.memory.set(0x61000000 + 0x6E8, 0x62000000); r.memory.set(0x62000000 + 0x38, 1); // inGame
    r.memory.set(BASE + 0x1356200, 0x63000000); r.memory.set(0x63000000, 0x64000000);
    r.memory.set(0x64000000 + 0x14, 0x65000000); r.memory.set(0x65000000 + 0x1C, 0x66000000);
    r.memory.set(0x66000000 + 0x1148, 3); // fontaine phase
    r.load();
    const seen = r.context.state();
    assert.deepStrictEqual(
      { level: seen.levelValue, loading: seen.loading, inGame: seen.inGame, phase: seen.fontainePhase },
      { level: 1309, loading: true, inGame: true, phase: 3 });
  },
  'missing pointers are not a crash, and mean no game is loaded': () => {
    const r = makeRuntime(); r.engine(); r.memory.set(BASE + 0x1356620, 0); r.load();
    const seen = state(r);
    assert.strictEqual(seen.inGame, false, 'a known path that cannot be followed is "not in a game"');
    assert.strictEqual(seen.fontainePhase, null);
    assert.strictEqual(seen.levelValue, null);
  },
  'engineReady follows the engine pointer': () => {
    const r = makeRuntime(); r.engine(0); r.load();
    assert.strictEqual(state(r).engineReady, false, 'still starting up');
    assert.strictEqual(state(r).execAvailable, true, 'nothing is wrong, it is just early');
    r.engine();
    assert.strictEqual(state(r).engineReady, true);
  },
  'a different build that shows up later is reported once, through state()': () => {
    const r = makeRuntime(); r.engine(0); r.load();
    r.engine(ENGINE, 0x424242);
    for (let i = 0; i < 3; i++) {
      const seen = state(r);
      assert.strictEqual(seen.engineReady, false);
      assert.strictEqual(seen.execAvailable, false);
    }
    assert.strictEqual(r.messages.filter((m) => m.type === 'exec_disabled').length, 1);
  },
  'unknown build tries the Steam addresses behind the vtable check': () => {
    const good = makeRuntime({ moduleSize: 12345 }); good.engine(); good.load();
    assert.strictEqual(state(good).execAvailable, true);
    const bad = makeRuntime({ moduleSize: 12345, deltaAt: null, scanHits: [] }); bad.load();
    assert.strictEqual(state(bad).execAvailable, false, 'unreadable engine pointer');
    assert.strictEqual(state(bad).levelValue, null);
    assert.strictEqual(state(bad).inGame, null, 'no path for this build: cannot tell');
  },
  // ---- Little Sisters ----
  'a Little Sister rescued or harvested is reported once, with the map': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    const other = r.object(0x1100); r.poke(other + 0x30, model.splicer); r.addActor(game.level, other);
    const sister = r.sister(game.level, model);
    r.load(); r.frames(3);
    assert.deepStrictEqual(r.messages.filter((m) => m.type === 'little_sister'), []);
    assert.ok(r.logged('watching Little Sisters of class SpawnedGatherer: HasBeenSavedOrPacified at +0x1038, bit 0x2'));
    r.poke(sister + 0x1038, 0x9); // other bits of the same word: not resolved
    r.frames(3);
    assert.deepStrictEqual(r.messages.filter((m) => m.type === 'little_sister'), []);
    r.poke(sister + 0x1038, 0xB);
    r.frames(5);
    assert.deepStrictEqual(r.messages.filter((m) => m.type === 'little_sister').map((m) => m.map), ['1-medical']);
    r.removeActor(game.level, sister); r.frames(3); // deleted after the rescue
    assert.strictEqual(r.messages.filter((m) => m.type === 'little_sister').length, 1);
    assert.strictEqual(JSON.stringify(state(r).littleSistersSeen), '{"1-medical":1}');
  },
  'a Little Sister going into a vent is not reported': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    const sister = r.sister(game.level, model);
    r.load(); r.frames(3);
    r.removeActor(game.level, sister); r.frames(3);
    const again = r.sister(game.level, model); // out of another vent, as a new object
    r.frames(3);
    assert.deepStrictEqual(r.messages.filter((m) => m.type === 'little_sister'), []);
    r.poke(again + 0x1038, 0x2); r.frames(2);
    assert.strictEqual(r.messages.filter((m) => m.type === 'little_sister').length, 1);
  },
  'sisters are only looked for in the level the player is in': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    const elsewhere = r.sister(game.entry, model); r.poke(elsewhere + 0x1038, 0x2);
    r.load(); r.frames(3);
    assert.deepStrictEqual(r.messages.filter((m) => m.type === 'little_sister'), []);
  },
  'a build without the object layout watches no sisters': () => {
    const r = makeRuntime({ moduleSize: EPIC_SIZE }); const game = r.game(); const model = r.objectModel();
    const sister = r.sister(game.level, model); r.poke(sister + 0x1038, 0x2);
    r.load(); r.frames(3);
    assert.deepStrictEqual(r.messages.filter((m) => m.type === 'little_sister'), []);
  },
  // ---- quests ----
  'state() lists the completed quests, read from the table of all objects': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    model.quest('QuarantineKey', true); model.quest('ResearchSplicers'); model.quest('GoToDeck', true);
    const later = model.quest('GatherChloro');
    r.load();
    assert.strictEqual(state(r).completedQuests, null, 'not looked up yet');
    r.frames(3);
    assert.ok(r.logged('watching 4 quests: Completed at +0xa8, bit 0x2'));
    assert.strictEqual(JSON.stringify(state(r).completedQuests), '["GoToDeck","QuarantineKey"]');
    r.poke(later + 0xA8, 0x2);
    r.frames(6);
    assert.strictEqual(JSON.stringify(state(r).completedQuests), '["GatherChloro","GoToDeck","QuarantineKey"]');
    void game;
  },
  'a new map has the quests looked up again': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    model.quest('QuarantineKey', true);
    r.load(); r.frames(3);
    assert.strictEqual(state(r).questsWatched, 1);
    model.quest('ResearchSplicers', true); // made by loading a save
    r.string(game.level + 0x7C, '2-fisheries');
    r.frames(10);
    assert.strictEqual(state(r).questsWatched, 2);
    assert.strictEqual(JSON.stringify(state(r).completedQuests), '["QuarantineKey","ResearchSplicers"]');
  },
  'unsupported actions answer straight away': () => {
    const r = makeRuntime(); r.engine(); r.load();
    r.context.rpc.exports.action(4, 'eve_drain');
    const answers = r.messages.filter((m) => m.type === 'action_result');
    assert.deepStrictEqual(answers.map((m) => [m.id, m.ok]), [[4, false]]);
  },
};

let failed = 0;
for (const [name, test] of Object.entries(tests)) {
  try { test(); console.log(`ok   ${name}`); } catch (e) { failed++; console.log(`FAIL ${name}\n     ${e.message}`); }
}
console.log(`${Object.keys(tests).length - failed} of ${Object.keys(tests).length} passed`);
process.exit(failed ? 1 : 0);

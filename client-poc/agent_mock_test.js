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
const HEAP_GET = 0x76000100; const HEAP_ALLOC = 0x76000200; // GetProcessHeap and HeapAlloc
const HEAP_VALIDATE = 0x76000400; const HEAP_REALLOC = 0x76000500; // HeapValidate and HeapReAlloc
const CREATE_FILE = 0x76000300; // CreateFileW
const PROCESS_EVENT = 0x736070; // where the Steam build runs script events
const OPEN_UPGRADE_MENU = 0x1dbf70; // where the Steam build opens a Power to the People station's menu
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
  writeU32(v) { this.runtime.memory.set(this.value, v >>> 0); return this; }
  writeS32(v) { this.runtime.memory.set(this.value, v >>> 0); return this; }
  compare(other) { return this.value < other.value ? -1 : (this.value > other.value ? 1 : 0); }
  writeUtf16String(text) { this.runtime.strings.set(this.value, text); this.runtime.text.set(this.value, text); return this; }
  writeFloat(f) { this.runtime.memory.set(this.value, Math.round(f * 10) >>> 0); return this; }
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
    scriptCalls: [], // what the agent ran through ProcessEvent: object, function, the frame's words
    heapAllocs: [], // the size of everything the agent took from the game's heap
    processHeap: new Set(), // blocks the game took from the process heap
    reallocs: [], // the size of every block the agent grew
    onScriptCall: null,
  };
  // ProcessEvent's first instructions, as the agent checks them before calling it
  runtime.memory.set(BASE + PROCESS_EVENT, 0x81EC8B55); runtime.memory.set(BASE + PROCESS_EVENT + 4, 0x000400EC);
  runtime.memory.set(BASE + OPEN_UPGRADE_MENU, 0x64EC8B55);
  runtime.replaced = new Map(); // address -> what the agent put there instead
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
    ptr: (value) => ptr(Number(value)),
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
          return { getExportByName: (name) => ptr({ GetProcessHeap: HEAP_GET, HeapAlloc: HEAP_ALLOC, CreateFileW: CREATE_FILE,
            HeapValidate: HEAP_VALIDATE, HeapReAlloc: HEAP_REALLOC }[name] ||
            VIRTUAL_QUERY) };
        }
        return {
          base: ptr(BASE), size: moduleSize, path: 'E:\\Game\\Build\\Final\\BioshockHD.exe',
          enumerateRanges: () => [{ base: ptr(BASE + 0x1000), size: 0x1000000 }],
        };
      },
    },
    Memory: {
      alloc: (size) => { const p = ptr(runtime.nextAlloc); runtime.nextAlloc += size + 16; return p; },
      allocUtf16String: (text) => { const p = ptr(runtime.nextAlloc); runtime.nextAlloc += 2 * text.length + 16;
        runtime.strings.set(p.value, text); return p; },
      copy: (to, from, size) => { // every word and text in the range, moved over
        for (const store of [runtime.memory, runtime.text, runtime.ansi, runtime.strings]) {
          for (const [at, value] of [...store]) {
            if (at >= from.value && at < from.value + size) { store.set(to.value + at - from.value, value); }
          }
        }
      },
      scanSync: (address, size, pattern) => {
        if (size === 47) { return deltaAt !== null && address.value === BASE + deltaAt ? [{ address, size }] : []; }
        if (!pattern.startsWith('55 8B EC')) { return pattern.includes('F4 0A') ? [] : [{ address, size: 6 }]; }
        return (scanHits || []).map((rva) => ({ address: ptr(BASE + rva), size: 47 }));
      },
    },
    NativeCallback: function (callback, returns, takes, abi) {
      if (abi === 'thiscall') { return { callback }; } // a replacement for a function of the game's
      runtime.outputDevice = callback; return ptr(0x66660000);
    },
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
      if (address.value === HEAP_GET) { return () => ptr(0x00A00000); }
      if (address.value === HEAP_VALIDATE) { // a block the game took from the process heap
        return (heap, flags, block) => (runtime.processHeap.has(block.value) ? 1 : 0);
      }
      if (address.value === HEAP_REALLOC) {
        return (heap, flags, block, size) => {
          const p = runtime.nextAlloc; runtime.nextAlloc += size + 16;
          for (let at = 0; at < size; at += 4) {
            if (runtime.memory.has(block.value + at)) { runtime.memory.set(p + at, runtime.memory.get(block.value + at)); }
          }
          runtime.processHeap.add(p); runtime.reallocs.push(size);
          return ptr(p);
        };
      }
      if (address.value === HEAP_ALLOC) {
        return (heap, flags, size) => {
          const p = ptr(runtime.nextAlloc); runtime.nextAlloc += size + 16; runtime.heapAllocs.push(size); return p;
        };
      }
      if (runtime.nativeBodies && runtime.nativeBodies.has(address.value)) { // a script function's C++ body
        return (self, frame, result) => {
          const read = (at) => runtime.memory.get(frame.value + at);
          runtime.nativeCalls.push({ name: runtime.nativeBodies.get(address.value), self: self.value, fn: read(4),
            object: read(8), vtable: read(0), result: result.value });
          if (runtime.onNativeCall) { runtime.onNativeCall(); }
        };
      }
      if (address.value === BASE + PROCESS_EVENT) {
        return (self, frame) => {
          const read = (at) => runtime.memory.get(frame.value + at);
          runtime.scriptCalls.push({ self: self.value, fn: read(4), object: read(8), parms: read(0x10), vtable: read(0) });
          if (runtime.onScriptCall) { runtime.onScriptCall(self.value, read(4)); }
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
        const dollars = /^GiveItem (\d+) ShockGame\.Credits$/.exec(command);
        if (dollars && runtime.creditsAt) { // the game adds them to the player's Credits
          runtime.memory.set(runtime.creditsAt, (runtime.memory.get(runtime.creditsAt) || 0) + Number(dollars[1]));
        }
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
    Interceptor: {
      replace: (address, replacement) => { runtime.replaced.set(address.value, replacement.callback); },
      attach: (address, callbacks) => {
        const hook = { address: address.value, callbacks };
        runtime.hooks.push(hook);
        return { detach: () => { runtime.hooks = runtime.hooks.filter((h) => h !== hook); } };
      },
    },
  };
  context.globalThis = context;
  runtime.context = context;
  runtime.load = () => { vm.runInNewContext(source, context, { filename: agentPath }); return runtime; };

  // One frame: the hooked function runs once for every loaded level, with the level in ecx.
  runtime.frame = (ms = 100) => {
    runtime.time += ms;
    const levels = runtime.levels.length > 0 ? runtime.levels : [0x12340000]; // or for something unreadable
    for (const level of levels) {
      runtime.hooks.filter((h) => h.address !== BASE + PROCESS_EVENT && h.address !== CREATE_FILE)
        .forEach((h) => h.callbacks.onEnter.call({ context: { ecx: ptr(level) } }));
    }
  };
  // The game running one script event through ProcessEvent: the hooks there see the object and its frame.
  runtime.gameEvent = (self, fn) => {
    const frame = runtime.object(0x40); poke(frame + 4, fn); poke(frame + 8, self); poke(frame, 0x11336134);
    const stack = runtime.object(0x40); poke(stack + 4, frame);
    runtime.hooks.filter((h) => h.address === BASE + PROCESS_EVENT)
      .forEach((h) => h.callbacks.onEnter.call({ context: { ecx: ptr(self), esp: ptr(stack) } }));
  };
  // The game opening a file: the path it ends up opening, after the hooks on CreateFileW.
  runtime.openFile = (path) => {
    const at = runtime.object(0x10); runtime.text.set(at, path);
    const args = [ptr(at)];
    runtime.hooks.filter((h) => h.address === CREATE_FILE).forEach((h) => h.callbacks.onEnter.call({}, args));
    return runtime.strings.has(args[0].value) ? runtime.strings.get(args[0].value) : runtime.text.get(args[0].value);
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
    // An object's name is a name and a number, as in the engine: "SecurityCrate_WallSafe2" is the name
    // "SecurityCrate_WallSafe" with number 3 (0 is no number).
    const rename = (at, text) => {
      const split = /^(.*?[^\d])(0|[1-9]\d*)$/.exec(text);
      poke(at + 0x28, name(split ? split[1] : text)); poke(at + 0x2C, split ? Number(split[2]) + 1 : 0);
    };
    const make = (text, cls, size = 0x200) => {
      const at = runtime.object(size); rename(at, text); poke(at + 0x30, cls);
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
    const questLog = cls('QuestLog', objectClass);
    const diary = (className) => make(className, cls(className, questLog));
    const stationClass = cls('PlaceableWeaponUpgradeStation', actor);
    fields(stationClass, [['NumTimesUsed', intProperty, 0x524]]);
    const station = (used = 0) => { const at = make('PlaceableWeaponUpgradeStation0', stationClass, 0x600); poke(at + 0x524, used); return at; };
    const functionClass = cls('Function', objectClass);
    const objectProperty = cls('ObjectProperty', objectClass);
    const stateClass = cls('State', objectClass);
    // A station in use: CurrentPlayer is set, and it is in its Interacting state, which has a FinishInteraction of
    // its own. The state frame is at +0x10, the state 4 bytes into it.
    fields(stationClass, [['FinishInteraction', functionClass, 0], ['NumTimesUsed', intProperty, 0x524],
      ['CurrentPlayer', objectProperty, 0x540]]);
    const finishInClass = runtime.memory.get(stationClass + 0x5C);
    const interacting = make('Interacting', stateClass);
    const finishInState = make('FinishInteraction', functionClass);
    poke(interacting + 0x5C, finishInState);
    const inUse = (stationAt, player) => {
      const frame = runtime.object(0x40); poke(frame + 4, interacting); poke(stationAt + 0x10, frame);
      poke(stationAt + 0x540, player);
    };
    // Weapons: their Holder is whoever has them.
    const weaponClass = cls('Weapon', actor);
    fields(weaponClass, [['Holder', objectProperty, 0x400]]);
    const weapon = (className, holder) => {
      const at = make(`${className}0`, cls(className, weaponClass), 0x600); poke(at + 0x400, holder); return at;
    };
    const pawnClass = cls('ShockPawn', actor);
    const onKilled = make('OnKilled', functionClass);
    poke(pawnClass + 0x5C, onKilled);
    const playerClass = cls('ShockPlayer', pawnClass);
    const tick = make('Tick', functionClass);
    // Gatherer's Gardens: a VendingTable whose VendingLootSpec array holds the lines, 0x20 bytes each. The array
    // property points to its inner struct property a few words after its offset, and that one to the struct.
    const strProperty = cls('StrProperty', objectClass);
    const arrayProperty = cls('ArrayProperty', objectClass);
    const structProperty = cls('StructProperty', objectClass);
    const scriptStruct = cls('ScriptStruct', objectClass);
    const spec = make('VendingLootSpec', scriptStruct);
    fields(spec, [['ItemClass', objectProperty, 0], ['PickupClass', objectProperty, 4], ['StackSize', intProperty, 8],
      ['CostAdjustment', intProperty, 0xC], ['DisplayWhenHacked', boolProperty, 0x10],
      ['DisplayWhenUnHacked', boolProperty, 0x10], ['SupplySize', intProperty, 0x14],
      ['AmountPurchased', intProperty, 0x18], ['cost', intProperty, 0x1C]]);
    const tableClass = cls('VendingLootTable', objectClass);
    fields(tableClass, [['VendingLootSpec', arrayProperty, 0x3C]]);
    const specsField = runtime.memory.get(tableClass + 0x5C);
    const inner = make('VendingLootSpec', structProperty); poke(specsField + 0x7C, inner); poke(inner + 0x78, spec);
    const vendingClass = cls('VendingStation', actor);
    fields(vendingClass, [['VendingTable', objectProperty, 0x5A0]]);
    const gardenClass = cls('PlaceableGrowthStation', cls('GrowthStation', vendingClass));
    const gene = cls('Enrage', objectClass);
    // A Garden with this many lines, each selling one of a gene. Returns the Garden and its lines' addresses.
    const garden = (count) => {
      const at = make('PlaceableGrowthStation0', gardenClass, 0x600);
      const table = make('VendingLootTable0', tableClass);
      const data = runtime.object(0x20 * count);
      poke(at + 0x5A0, table); poke(table + 0x3C, data); poke(table + 0x40, count); poke(table + 0x44, count);
      const lines = Array.from({ length: count }, (_, n) => data + 0x20 * n);
      lines.forEach((line) => {
        [gene, 0, 1, 10, 0, 1, 0, 40].forEach((value, i) => poke(line + 4 * i, value)); // CostAdjustment 1.0
      });
      return { at, lines };
    };
    // The placeholder: class chips, whose default values (an array in the class at +0x54C) hold its texts.
    const item = cls('Item', objectClass);
    const floatProperty = cls('FloatProperty', objectClass);
    fields(item, [['Description', strProperty, 0x44], ['FriendlyName', strProperty, 0x50],
      ['CreditValue', floatProperty, 0x60]]);
    // An item class worth `credit` ADAM, with its default values in an array at +0x54C like chips.
    const itemClass = (className, credit) => {
      const at = cls(className, item);
      const values = runtime.object(0x100);
      poke(at + 0x54C, values); poke(at + 0x550, 188); poke(at + 0x554, 188);
      runtime.string(values + 0x50, className); poke(values + 0x60, credit * 10);
      return at;
    };
    const chips = make('chips', classClass, 0x1100); poke(chips + 0x40, cls('Consumable', item));
    for (let at = 0x1000; at < 0x1100; at += 0x1000) { poke(chips + at, 0); }
    poke(chips + 0xFFC, 0); poke(chips + 0x10FC, 0);
    const defaults = runtime.object(0x100);
    poke(chips + 0x54C, defaults); poke(chips + 0x550, 188); poke(chips + 0x554, 188);
    runtime.string(defaults + 0x44, 'Salty and crunchy.'); runtime.string(defaults + 0x50, 'Potato Chips');
    // The player's EVE and dollars as ShockPlayer declares them, and (unless left out) its functions for taking some.
    const removeBioAmmo = make('RemoveBioAmmo', functionClass);
    const removeCredits = make('RemoveCredits', functionClass);
    const updateUIStats = make('UpdateUIStats', functionClass);
    const updateUIBioAmmoBank = make('UpdateUIBioAmmoBank', functionClass);
    const playerStats = (functions = true, redraws = false) => {
      fields(playerClass, [['BioAmmo', floatProperty, 0xAF8], ['Credits', intProperty, 0xAEC]]);
      if (functions) { // in front of the properties
        poke(removeCredits + 0x44, runtime.memory.get(playerClass + 0x5C));
        poke(removeBioAmmo + 0x44, removeCredits); poke(playerClass + 0x5C, removeBioAmmo);
      }
      if (redraws) { // the HUD's natives, in front as well
        poke(updateUIBioAmmoBank + 0x44, runtime.memory.get(playerClass + 0x5C));
        poke(updateUIStats + 0x44, updateUIBioAmmoBank); poke(playerClass + 0x5C, updateUIStats);
      }
    };
    // Open Rapture: the level's BathysphereManager, with Destinations {Title, MapName, StartLocationLabel} (28 bytes
    // each) at +0x80 and UnlockedDestinations at +0x8C; and scripted level changes (ActionChangeLevel) with their
    // two strings and resolveInfoList rows {PropertyName, Variable} that hand them names.
    const nameProperty = cls('NameProperty', objectClass);
    const arrayOfStructs = (owner, arrayName, offset, structName, structFields) => {
      const struct = make(structName, scriptStruct);
      fields(struct, structFields);
      fields(owner, [[arrayName, arrayProperty, offset]]);
      const arrayField = runtime.memory.get(owner + 0x5C);
      const innerField = make(arrayName, structProperty); poke(arrayField + 0x7C, innerField); poke(innerField + 0x78, struct);
    };
    const managerClass = cls('BathysphereManager', objectClass);
    fields(managerClass, [['UnlockedDestinations', arrayProperty, 0x8C]]);
    const unlockedField = runtime.memory.get(managerClass + 0x5C);
    arrayOfStructs(managerClass, 'Destinations', 0x80, 'BathysphereDestination',
      [['Title', strProperty, 0], ['MapName', nameProperty, 0xC], ['StartLocationLabel', nameProperty, 0x14]]);
    poke(runtime.memory.get(managerClass + 0x5C) + 0x44, unlockedField); // Destinations, then UnlockedDestinations
    const unlockDestination = make('UnlockDestination', functionClass); // the script function the levels call
    poke(unlockedField + 0x44, unlockDestination);
    // ShockPlayer.UnlockBathysphereDestination(name MapName), which makes the level's manager when there is none.
    const unlockBathysphereDestination = make('UnlockBathysphereDestination', functionClass);
    fields(unlockBathysphereDestination, [['MapName', nameProperty, 0]]);
    const playerUnlocks = () => {
      poke(unlockBathysphereDestination + 0x44, runtime.memory.get(playerClass + 0x5C));
      poke(playerClass + 0x5C, unlockBathysphereDestination);
    };
    const STOPS = [['Medical Pavilion', '1-Medical', 'Backtrack_MedicalStart'], ["Neptune's Bounty", '2-Fisheries', 'PlayerStart'],
      ['Arcadia', '3-Arcadia', 'ArcBathyEndPS'], ['Fort Frolic', '4-Recreation', 'PlayerStart'],
      ['Hephaestus', '5-Hephaestus', 'EngBathyStartPS'], ['Apollo Square', '6-Slums', 'PlayerStart_FromDeck7Bathy'],
      ['Point Prometheus', '7-Science', 'FromResidential'], ['', 'None', 'None']];
    ['FromFisheriesPS', 'FromSubBayPS', '6-Resi', 'PlayerStart_First', '3-Arcadia', '2-SubBay', 'FromArcadiaPS', 'MapName', 'StartLocationLabel'].forEach(name);
    // The manager: every destination, `unlocked` offered, with room for `room`; `onHeap` if its buffer is the
    // process heap's.
    const bathysphere = (unlocked, room = unlocked.length, onHeap = false) => {
      const at = make('BioshockBathyspheres', managerClass, 0x100);
      const destinations = runtime.object(28 * STOPS.length);
      STOPS.forEach(([title, map, label], i) => {
        runtime.string(destinations + 28 * i, title || 'x');
        poke(destinations + 28 * i + 0xC, name(map)); poke(destinations + 28 * i + 0x10, 0);
        poke(destinations + 28 * i + 0x14, name(label)); poke(destinations + 28 * i + 0x18, 0);
      });
      poke(at + 0x80, destinations); poke(at + 0x84, STOPS.length); poke(at + 0x88, STOPS.length);
      const data = runtime.object(Math.max(4, 4 * room));
      unlocked.forEach((index, i) => poke(data + 4 * i, index));
      for (let i = unlocked.length; i < room; i++) { poke(data + 4 * i, 0); }
      if (onHeap) { runtime.processHeap.add(data); }
      poke(at + 0x8C, data); poke(at + 0x90, unlocked.length); poke(at + 0x94, room);
      return at;
    };
    const unlockedOf = (manager) => {
      const data = runtime.memory.get(manager + 0x8C);
      return Array.from({ length: runtime.memory.get(manager + 0x90) }, (_, i) => runtime.memory.get(data + 4 * i));
    };
    const arrivalOf = (manager, i) => names[runtime.memory.get(runtime.memory.get(manager + 0x80) + 28 * i + 0x14)];
    const lineOf = (manager, i) => {
      const at = runtime.memory.get(manager + 0x80) + 28 * i;
      return { title: runtime.text.get(runtime.memory.get(at)), map: names[runtime.memory.get(at + 0xC)],
        arrival: names[runtime.memory.get(at + 0x14)] };
    };
    // The game's UnlockDestination: adds the destination whose MapName is the one handed to it, in order, in a
    // buffer of its own.
    const gameUnlocks = (parms, manager) => {
      const wanted = runtime.memory.get(parms);
      const destinations = runtime.memory.get(manager + 0x80);
      const index = STOPS.findIndex((_, i) => runtime.memory.get(destinations + 28 * i + 0xC) === wanted);
      const now = unlockedOf(manager);
      if (index < 0 || now.includes(index)) { return; }
      const next = [...now, index].sort((a, b) => a - b);
      const data = runtime.object(4 * next.length);
      next.forEach((value, i) => poke(data + 4 * i, value));
      poke(manager + 0x8C, data); poke(manager + 0x90, next.length); poke(manager + 0x94, next.length);
    };
    const changeClass = cls('ActionChangeLevel', cls('Action', objectClass));
    fields(changeClass, [['MapName', strProperty, 0x80], ['StartLocationLabel', strProperty, 0x8C]]);
    const mapField = runtime.memory.get(changeClass + 0x5C);
    arrayOfStructs(changeClass, 'resolveInfoList', 0x98, 'ResolveInfo',
      [['PropertyName', nameProperty, 0], ['Variable', nameProperty, 8]]);
    poke(runtime.memory.get(changeClass + 0x5C) + 0x44, mapField);
    // A level change to this map and arrival point, with room for `room` characters in each string.
    const levelChange = (map, label, room = 0) => {
      const at = make('ActionChangeLevel0', changeClass, 0x100);
      for (const [offset, text] of [[0x80, map], [0x8C, label]]) {
        runtime.string(at + offset, text);
        if (room) { poke(at + offset + 8, room); }
      }
      const rows = runtime.object(0x20);
      poke(rows, name('MapName')); poke(rows + 4, 0); poke(rows + 8, name(map)); poke(rows + 12, 0);
      poke(rows + 16, name('StartLocationLabel')); poke(rows + 20, 0); poke(rows + 24, name(label)); poke(rows + 28, 0);
      poke(at + 0x98, rows); poke(at + 0x9C, 2); poke(at + 0xA0, 2);
      return at;
    };
    // A script note (ActionScriptNote) whose Label has room for `room` characters: a string the game made that
    // nothing reads while playing.
    const noteClass = cls('ActionScriptNote', cls('Action', objectClass));
    fields(noteClass, [['Label', strProperty, 0x80]]);
    const note = (text, room) => {
      const at = make('ActionScriptNote0', noteClass, 0x100);
      runtime.string(at + 0x80, text);
      poke(at + 0x88, room);
      return at;
    };
    // Plasmids and tonics lying in a level and safes (bIsOpened). As in game (probe .8): the pickup's
    // LootSlot (+0x520) holds LootSpec (a LootItemSpecification, ItemClass +0x4C) and Loot (the ItemStack rolled
    // from it, ItemClass +0x50, None until rolled); taking the pickup gives the ItemStack's class.
    const classProperty = cls('ClassProperty', objectClass);
    const pickupClass = cls('PlasmidPickup', actor);
    fields(pickupClass, [['PickupMesh', objectProperty, 0x518], ['LootSlot', objectProperty, 0x520]]);
    const lootSlotClass = cls('LootSlot', objectClass);
    fields(lootSlotClass, [['LootSpec', objectProperty, 0x40], ['Loot', objectProperty, 0x44]]);
    const specClass = cls('LootItemSpecification', cls('DeletableObject', objectClass));
    fields(specClass, [['ItemClass', classProperty, 0x4C]]);
    const stackClass = cls('ItemStack', cls('ReferenceCountedObject', objectClass));
    fields(stackClass, [['ItemClass', classProperty, 0x50]]);
    const pickupKinds = new Map();
    const pickup = (className, actorName, gives, rolled = true) => {
      if (!pickupKinds.has(className)) { pickupKinds.set(className, cls(className, pickupClass)); }
      const at = make(actorName, pickupKinds.get(className), 0x600); poke(at + 0xCC, 0);
      const slot = make('LootSlot', lootSlotClass, 0x80); poke(at + 0x520, slot);
      const spec = make('LootItemSpecification', specClass, 0x80); poke(spec + 0x4C, gives); poke(slot + 0x40, spec);
      if (rolled) { const stack = make('ItemStack', stackClass, 0x80); poke(stack + 0x50, gives); poke(slot + 0x44, stack); }
      if (runtime.levels.length > 1) { runtime.addActor(runtime.levels[1], at); } // the player's level
      return at;
    };
    // What a pickup gives: its LootSpec's class and its ItemStack's (0 when not rolled).
    const gives = (at) => {
      const slot = runtime.memory.get(at + 0x520); const stack = runtime.memory.get(slot + 0x44);
      return [runtime.memory.get(runtime.memory.get(slot + 0x40) + 0x4C), stack ? runtime.memory.get(stack + 0x50) : 0];
    };
    const roll = (at) => { // the game rolls the pickup: an ItemStack of what LootSpec says
      const slot = runtime.memory.get(at + 0x520);
      const stack = make('ItemStack', stackClass, 0x80);
      poke(stack + 0x50, runtime.memory.get(runtime.memory.get(slot + 0x40) + 0x4C)); poke(slot + 0x44, stack);
    };
    const containerClass = cls('AnimatedContainer', actor);
    fields(containerClass, [['bIsOpened', boolProperty, 0x5DC]]);
    const crateClass = cls('SecurityCrate', containerClass);
    const containerKinds = { SecurityCrate_Safe: cls('SecurityCrate_Safe', crateClass),
      SecurityCrate_WallSafe: cls('SecurityCrate_WallSafe', crateClass),
      Lockbox: cls('Lockbox', cls('ReactiveActor', actor)) }; // out of scope, and not an AnimatedContainer
    fields(containerKinds.Lockbox, [['bCanSoftLock', boolProperty, 0x5E0]]);
    const container = (className, actorName) => {
      const at = make(actorName, containerKinds[className], 0x600); poke(at + 0x5DC, 0);
      if (runtime.levels.length > 1) { runtime.addActor(runtime.levels[1], at); }
      return at;
    };
    // Research, as ShockPlayer declares it: ResearchTracks {ResearchName, CurrentScore} (16 bytes each here) and
    // ResearchLevels {ResearchName, LevelNumber, ScoreRequired, AwardItemClass} (20 bytes each).
    const research = (tracks, levels) => {
      arrayOfStructs(playerClass, 'ResearchTracks', 0xB00, 'ResearchTrack',
        [['ResearchName', nameProperty, 0], ['CurrentScore', intProperty, 8], ['MaxScore', intProperty, 0xC]]);
      const tracksField = runtime.memory.get(playerClass + 0x5C);
      arrayOfStructs(playerClass, 'ResearchLevels', 0xB10, 'ResearchLevel',
        [['ResearchName', nameProperty, 0], ['LevelNumber', intProperty, 8], ['ScoreRequired', intProperty, 0xC],
          ['AwardItemClass', classProperty, 0x10]]);
      poke(runtime.memory.get(playerClass + 0x5C) + 0x44, tracksField); // ResearchLevels, then ResearchTracks
      const fill = (offset, rows, size, write) => (pawn) => {
        const data = runtime.object(Math.max(4, size * rows.length));
        rows.forEach((row, i) => write(data + size * i, row));
        poke(pawn + offset, data); poke(pawn + offset + 4, rows.length); poke(pawn + offset + 8, rows.length);
        return data;
      };
      return {
        tracks: fill(0xB00, tracks, 16, (at, [subject, score]) => {
          poke(at, name(subject)); poke(at + 4, 0); poke(at + 8, score); poke(at + 0xC, 100);
        }),
        levels: fill(0xB10, levels, 20, (at, [subject, number, score, award]) => {
          poke(at, name(subject)); poke(at + 4, 0); poke(at + 8, number); poke(at + 0xC, score); poke(at + 0x10, award);
        }),
      };
    };
    // Story flags: global_ variables are VariableBool objects with Value at +0x40, made by
    // Script.newGlobalVariable(VariableName, variableType) -> ReturnValue.
    const storyFlags = () => {
      const variableClass = cls('Variable', objectClass);
      const variableBool = cls('VariableBool', variableClass);
      fields(variableBool, [['bSomethingElse', boolProperty, 0x40], ['Value', boolProperty, 0x40]]);
      const scriptClass = cls('Script', objectClass);
      const newGlobal = make('newGlobalVariable', functionClass);
      fields(newGlobal, [['VariableName', nameProperty, 0], ['variableType', classProperty, 8],
        ['ReturnValue', objectProperty, 0xC]]);
      poke(scriptClass + 0x5C, newGlobal);
      const script = make('Script', scriptClass);
      const variable = (text) => { const at = make(text, variableBool, 0x80); poke(at + 0x40, 0); return at; };
      return { variableBool, script, newGlobal, variable };
    };
    const textOf = (at) => runtime.text.get(runtime.memory.get(at));
    const changeOf = (change) => ({
      map: runtime.text.get(runtime.memory.get(change + 0x80)), label: runtime.text.get(runtime.memory.get(change + 0x8C)),
      rows: [names[runtime.memory.get(runtime.memory.get(change + 0x98) + 8)],
        names[runtime.memory.get(runtime.memory.get(change + 0x98) + 24)]],
    });
    return { nameIndex: name, rename, storyFlags, unlockBathysphereDestination, playerUnlocks, bathysphere, unlockedOf, arrivalOf, lineOf, gameUnlocks, unlockDestination, levelChange, changeOf, note, textOf, playerStats, removeBioAmmo, removeCredits, updateUIStats, updateUIBioAmmoBank, spawned, splicer, make, quest, diary, station, playerClass, onKilled, tick, inUse, finishInState,
      finishInClass, weapon, garden, chips, defaults, itemClass, item, pickup, gives, roll, container, research };
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
const gardenLines = (r, id, lines) => r.context.rpc.exports.action(id, 'garden_lines ' +
  JSON.stringify({ level: 'Medical Pavilion', map: '1-medical', lines }));
const travelRules = (r, id, rules) => r.context.rpc.exports.action(id, `travel_rules ${JSON.stringify(rules)}`);
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
    assert.match(r.messages.find((m) => m.type === 'exec_disabled').reason || JSON.stringify(r.messages.find((m) => m.type === 'exec_disabled')), /different build/);
  },
  'an engine that matched and then changes is the game quitting, not another build': () => {
    const r = makeRuntime(); r.game(); r.load(); r.frame();
    assert.strictEqual(state(r).engineReady, true);
    r.engine(ENGINE, 0x424242); state(r);
    const told = r.messages.find((m) => m.type === 'exec_disabled');
    assert.match(JSON.stringify(told), /shutting down/);
    assert.strictEqual(told.quitting, true);
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
  'the actors of levels the player is not in are not looked through': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    const elsewhere = r.sister(game.entry, model); r.poke(elsewhere + 0x1038, 0x2);
    r.load(); r.frames(3);
    assert.deepStrictEqual(r.messages.filter((m) => m.type === 'little_sister'), []);
  },
  'a sister in the table of all objects is followed even when the player level does not list her': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    const sister = model.make('SpawnedGatherer0', model.spawned, 0x1100);
    r.load(); r.frames(5);
    assert.strictEqual(state(r).sistersWatched, 1);
    r.poke(sister + 0x1038, 0x2); r.frames(3);
    assert.deepStrictEqual(r.messages.filter((m) => m.type === 'little_sister').map((m) => m.map), ['1-medical']);
  },
  'a deleted sister stops being followed, and a new one at her address starts afresh': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    const sister = r.sister(game.level, model);
    r.load(); r.frames(3);
    r.removeActor(game.level, sister); r.poke(sister, 0x1234); r.frames(3); // deleted: its vtable changed
    assert.strictEqual(state(r).sistersWatched, 0);
    r.poke(sister, 0); r.poke(sister + 0x1038, 0); r.addActor(game.level, sister); r.frames(3);
    assert.strictEqual(state(r).sistersWatched, 1);
    r.poke(sister + 0x1038, 0x2); r.frames(3);
    assert.strictEqual(r.messages.filter((m) => m.type === 'little_sister').length, 1);
  },
  'radio messages and diaries are listed in state() without a line each in the log': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    model.diary('Med_At_PickUpRadio');
    r.load(); r.frames(5);
    assert.strictEqual(JSON.stringify(state(r).logsReceived), '["Med_At_PickUpRadio"]');
    assert.ok(!r.logged('received Med_At_PickUpRadio'));
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
  'state() lists the diaries received while it watches': () => {
    const r = makeRuntime(); r.game(); const model = r.objectModel();
    model.quest('QuarantineKey', true);
    r.load(); r.frames(3);
    assert.strictEqual(JSON.stringify(state(r).logsReceived), '[]');
    model.diary('vo_ducky_fleespang');
    r.frames(3);
    assert.strictEqual(JSON.stringify(state(r).logsReceived), '["vo_ducky_fleespang"]');
  },
  'state() counts the Power to the People stations used, by map': () => {
    const r = makeRuntime(); r.game(); const model = r.objectModel();
    const station = model.station(); model.station();
    r.load(); r.frames(3);
    assert.strictEqual(JSON.stringify(state(r).stationsUsed), '{}');
    assert.ok(r.logged('watching stations of class PlaceableWeaponUpgradeStation: NumTimesUsed at +0x524'));
    r.poke(station + 0x524, 1); r.frames(3);
    assert.strictEqual(JSON.stringify(state(r).stationsUsed), '{"1-medical":1}');
    r.poke(station + 0x524, 2); r.frames(3);
    assert.strictEqual(JSON.stringify(state(r).stationsUsed), '{"1-medical":1}', 'one station used twice is one');
  },
  // ---- DeathLink ----
  'a death is reported once, and again after a Vita-Chamber': () => {
    const r = makeRuntime(); const game = r.game(); r.load(); r.frames(3);
    const died = () => r.messages.filter((m) => m.type === 'player_died');
    r.poke(game.pawn + 0x57C, 0); r.frames(3);
    assert.deepStrictEqual(died().map((m) => m.map), ['1-medical']);
    r.poke(game.pawn + 0x57C, 1000); r.frames(2); // revived
    r.poke(game.pawn + 0x57C, 0); r.frames(2);
    assert.strictEqual(died().length, 2);
  },
  'a new body that is already dead is no death': () => {
    const r = makeRuntime(); const game = r.game(); r.load(); r.frames(3);
    const other = r.object(); r.addActor(game.level, other);
    r.poke(other + 0x450, game.controller); r.poke(other + 0x57C, 0);
    r.poke(game.controller + 0x450, other); r.frames(3); // a save loaded with the player dead in it
    assert.strictEqual(r.messages.filter((m) => m.type === 'player_died').length, 0);
  },
  'kill empties the health and calls OnKilled, with a frame copied from the game\'s own calls': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    r.poke(game.pawn + 0x30, model.playerClass);
    r.load(); r.frames(2);
    r.context.rpc.exports.action(0, 'kill');
    r.frames(2);
    assert.strictEqual(r.memory.get(game.pawn + 0x57C), 875, 'nothing before a call of the game\'s has been seen');
    assert.strictEqual(r.messages.filter((m) => m.type === 'action_result').length, 0);
    r.gameEvent(game.hud, model.tick);
    r.frames(1);
    assert.strictEqual(r.memory.get(game.pawn + 0x57C), 0);
    assert.deepStrictEqual(r.scriptCalls.map((c) => [c.self, c.fn, c.object, c.vtable]),
      [[game.pawn, model.onKilled, game.pawn, 0x11336134]], 'OnKilled on the player, in a frame like the game\'s');
    assert.deepStrictEqual(r.messages.filter((m) => m.type === 'action_result').map((m) => [m.id, m.name, m.ok]),
      [[0, 'kill', true]]);
    assert.ok(!r.hooks.some((h) => h.address === BASE + PROCESS_EVENT), 'the watch on the game\'s calls is gone');
    r.frames(1);
    assert.strictEqual(r.messages.filter((m) => m.type === 'player_died').length, 1, 'and the death is seen');
  },
  'kill waits for a body': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    r.poke(game.pawn + 0x30, model.playerClass);
    r.load(); r.frames(2);
    r.poke(game.controller + 0x450, 0); // between two levels
    r.context.rpc.exports.action(0, 'kill'); r.frames(3);
    r.gameEvent(game.hud, model.tick); r.frames(2);
    assert.strictEqual(r.messages.filter((m) => m.type === 'action_result').length, 0);
    r.poke(game.controller + 0x450, game.pawn); r.frames(3);
    r.gameEvent(game.hud, model.tick); r.frames(1);
    assert.strictEqual(r.memory.get(game.pawn + 0x57C), 0);
    assert.strictEqual(r.scriptCalls.length, 1);
  },
  'a kill that cannot be carried out is said, and the health is left alone': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    r.poke(game.pawn + 0x30, model.playerClass);
    r.memory.set(BASE + PROCESS_EVENT, 0x90909090); // some other code there
    r.load(); r.frames(2);
    r.context.rpc.exports.action(0, 'kill'); r.frames(2);
    assert.strictEqual(r.memory.get(game.pawn + 0x57C), 875);
    const answers = r.messages.filter((m) => m.type === 'action_result');
    assert.deepStrictEqual(answers.map((m) => m.ok), [false]);
    assert.ok(answers[0].reason.includes('not what this agent expects'), answers[0].reason);
  },
  'a kill after the game has answered by itself ends after a while': () => {
    const r = makeRuntime(); const game = r.game(); r.load(); r.frames(2);
    r.poke(game.controller + 0x450, 0);
    r.context.rpc.exports.action(5, 'kill'); r.frames(310);
    const answers = r.messages.filter((m) => m.type === 'action_result');
    assert.deepStrictEqual(answers.map((m) => [m.id, m.ok]), [[5, false]]);
  },
  'touch_stations skips the station menu, then finishes the station through its state': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    const station = model.station(0);
    r.load(); r.frames(2);
    r.context.rpc.exports.action(1, 'touch_stations');
    assert.deepStrictEqual(r.messages.filter((m) => m.type === 'action_result').map((m) => [m.id, m.name, m.ok]),
      [[1, 'touch_stations', true]]);
    assert.ok(r.replaced.has(BASE + OPEN_UPGRADE_MENU), 'the native that opens the menu is replaced');
    assert.strictEqual(r.context.rpc.exports.state().stationTouch, true);
    model.inUse(station, game.pawn);
    r.replaced.get(BASE + OPEN_UPGRADE_MENU)(r.context.ptr(game.pawn)); // the player uses the station
    r.gameEvent(game.hud, model.tick); // a call of the game's to copy
    r.frames(2);
    const calls = r.scriptCalls.map((c) => [c.self, c.fn]);
    assert.deepStrictEqual(calls, [[station, model.finishInState]], "the state's FinishInteraction, on the station");
    assert.strictEqual(r.memory.get(r.scriptCalls[0].parms), 1, 'SuccessfullyCompleted is true');
    r.frames(5);
    assert.strictEqual(r.scriptCalls.length, 1, 'once');
  },
  'touch_stations is refused when the game code is not the expected one': () => {
    const r = makeRuntime(); r.game(); r.objectModel();
    r.memory.set(BASE + OPEN_UPGRADE_MENU, 0x90909090);
    r.load(); r.frames(2);
    r.context.rpc.exports.action(2, 'touch_stations');
    const answers = r.messages.filter((m) => m.type === 'action_result');
    assert.deepStrictEqual(answers.map((m) => [m.id, m.ok]), [[2, false]]);
    assert.ok(!r.replaced.has(BASE + OPEN_UPGRADE_MENU));
    assert.strictEqual(r.context.rpc.exports.state().stationTouch, false);
  },
  'weapons held are the ones whose Holder is the player': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    r.load();
    assert.strictEqual(r.context.rpc.exports.state().weaponsPending, true, 'not looked yet');
    r.frames(2);
    assert.deepStrictEqual([...r.context.rpc.exports.state().weaponsHeld], [], 'no weapon anywhere: none held');
    assert.strictEqual(r.context.rpc.exports.state().weaponsPending, false, 'looked');
    model.weapon('Shotgun', game.pawn); model.weapon('Pistol', 0x7777000); model.weapon('Crossbow', game.pawn);
    r.frames(3);
    assert.deepStrictEqual([...r.context.rpc.exports.state().weaponsHeld], ['Crossbow', 'Shotgun']);
  },
  'after a level change the weapons are looked at again before upgrades go in': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    model.weapon('Shotgun', game.pawn);
    r.load(); r.frames(3);
    assert.deepStrictEqual([...state(r).weaponsHeld], ['Shotgun']);
    const next = r.level('2-fisheries', { extra: 4 }); r.levels.splice(r.levels.indexOf(game.level), 1);
    r.player(next, { viewport: game.player });
    r.poke(BASE + 0x1390430, 12000); // a table big enough that a pass over it takes a few ticks
    r.frame();
    assert.strictEqual(state(r).weaponsHeld, null, 'the last level\'s weapons no longer count');
    assert.strictEqual(state(r).weaponsPending, true);
    r.frames(4);
    assert.deepStrictEqual([...state(r).weaponsHeld], [], 'the shotgun was the last body\'s');
    assert.strictEqual(state(r).weaponsPending, false);
  },
  // ---- Open Rapture ----
  'travel_rules makes the bathysphere offer exactly the stops given, at their arrival points': () => {
    const r = makeRuntime(); r.game(); const model = r.objectModel();
    const manager = model.bathysphere([0, 1], 8);
    r.load(); r.frames(2);
    travelRules(r, 4, { stops: [{ map: '1-medical', arrival: 'Backtrack_MedicalStart' },
      { map: '3-arcadia', arrival: 'FromFisheriesPS' }, { map: '4-recreation', arrival: 'PlayerStart' }], redirects: [] });
    assert.deepStrictEqual(r.messages.filter((m) => m.type === 'action_result').map((m) => [m.id, m.name, m.ok]),
      [[4, 'travel_rules', true]]);
    r.frames(8);
    assert.deepStrictEqual(model.unlockedOf(manager), [0, 2, 3]);
    assert.strictEqual(model.arrivalOf(manager, 2), 'FromFisheriesPS', 'a first ride to Arcadia docks with the submarine');
    assert.strictEqual(model.arrivalOf(manager, 3), 'PlayerStart');
    assert.deepStrictEqual([...state(r).travelOffered], [0, 2, 3]);
    assert.strictEqual(state(r).travelTrouble, null);
    r.poke(r.memory.get(manager + 0x8C) + 4, 1); r.poke(manager + 0x90, 2); // the level's script unlocks its own again
    r.frames(6);
    assert.deepStrictEqual(model.unlockedOf(manager), [0, 2, 3], 'taken back out');
  },
  'more stops than the array has room for are added by the game itself, one at a time': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    const manager = model.bathysphere([1, 2], 2);
    r.onScriptCall = (self, fn) => {
      if (fn === model.unlockDestination) { model.gameUnlocks(r.scriptCalls.at(-1).parms, self); }
    };
    r.load(); r.frames(2);
    travelRules(r, 1, { stops: [{ map: '1-medical' }, { map: '2-fisheries' }, { map: '7-science' }], redirects: [] });
    r.frames(4);
    assert.deepStrictEqual(model.unlockedOf(manager), [1], 'what must go is dropped in place');
    assert.strictEqual(r.scriptCalls.length, 0, 'no call of the game\'s to copy yet');
    r.gameEvent(game.hud, model.tick);
    r.frames(30); // one destination each time the bathysphere is looked at
    assert.deepStrictEqual(model.unlockedOf(manager), [0, 1, 6]);
    assert.deepStrictEqual(r.scriptCalls.map((c) => [c.self, c.fn]),
      [[manager, model.unlockDestination], [manager, model.unlockDestination]]);
    assert.deepStrictEqual(r.reallocs, [], 'no memory of ours');
    assert.deepStrictEqual(r.heapAllocs, []);
    assert.strictEqual(state(r).travelTrouble, null);
  },
  'a destination the game does not unlock when asked is asked for once': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    const manager = model.bathysphere([1], 1);
    r.load(); r.frames(2);
    travelRules(r, 1, { stops: [{ map: '1-medical' }, { map: '2-fisheries' }], redirects: [] });
    r.frames(10); r.gameEvent(game.hud, model.tick); r.frames(30);
    assert.strictEqual(r.scriptCalls.length, 1);
    assert.deepStrictEqual(model.unlockedOf(manager), [1]);
    assert.match(state(r).travelTrouble, /did not unlock bathysphere destination 0/);
  },
  "a stop that stands in for another level points Apollo Square's line there, and back": () => {
    for (const withNote of [true, false]) {
      const r = makeRuntime(); r.game(); const model = r.objectModel();
      const manager = model.bathysphere([0, 1], 8);
      const spare = withNote ? model.note('a note', 40) : null;
      const spareBuffer = withNote ? r.memory.get(spare + 0x80) : null;
      r.load(); r.frames(2);
      travelRules(r, 1, { stops: [{ map: '1-medical', arrival: 'Backtrack_MedicalStart' },
        { map: '6-slums', arrival: 'PlayerStart_First', to: '6-resi', title: 'Olympus Heights' }], redirects: [] });
      r.frames(8);
      assert.deepStrictEqual(model.unlockedOf(manager), [0, 5]);
      assert.deepStrictEqual(model.lineOf(manager, 5), { title: withNote ? 'Olympus Heights' : 'Apollo Square',
        map: '6-Resi', arrival: 'PlayerStart_First' });
      if (withNote) {
        assert.strictEqual(model.textOf(spare + 0x80), 'Apollo Square', 'the note holds the old title meanwhile');
      }
      assert.strictEqual(state(r).travelTrouble, null);
      r.frames(6);
      assert.strictEqual(model.lineOf(manager, 5).map, '6-Resi', 'still found by the station it was');
      travelRules(r, 2, { stops: [{ map: '1-medical', arrival: 'Backtrack_MedicalStart' },
        { map: '6-slums', arrival: 'PlayerStart_FromDeck7Bathy' }, { map: '7-science', arrival: 'FromResidential' }],
      redirects: [] });
      r.frames(8);
      assert.deepStrictEqual(model.unlockedOf(manager), [0, 5, 6]);
      assert.deepStrictEqual(model.lineOf(manager, 5), { title: 'Apollo Square', map: '6-Slums',
        arrival: 'PlayerStart_FromDeck7Bathy' });
      if (withNote) {
        assert.strictEqual(r.memory.get(spare + 0x80), spareBuffer, 'its string given back');
      }
    }
  },
  "a save made with Apollo Square's line pointed at Olympus Heights still has it put back": () => {
    const r = makeRuntime(); r.game(); const model = r.objectModel();
    const manager = model.bathysphere([0, 5], 8);
    const line = r.memory.get(manager + 0x80) + 28 * 5;
    r.string(line, 'Olympus Heights');
    r.poke(line + 0xC, model.nameIndex('6-Resi'));
    r.load(); r.frames(2);
    travelRules(r, 1, { stops: [{ map: '1-medical', arrival: 'Backtrack_MedicalStart' },
      { map: '6-slums', arrival: 'PlayerStart_FromDeck7Bathy' }], redirects: [] });
    r.frames(12);
    assert.deepStrictEqual(model.unlockedOf(manager), [0, 5]);
    assert.deepStrictEqual(model.lineOf(manager, 5), { title: 'Apollo Square', map: '6-Slums',
      arrival: 'PlayerStart_FromDeck7Bathy' });
  },
  'an arrival point the game does not know yet is looked up again on the next level': () => {
    const r = makeRuntime(); const g = r.game(); const model = r.objectModel();
    r.string(g.level + 0x7C, '4-Recreation');
    const manager = model.bathysphere([0, 5], 8);
    r.load(); r.frames(2);
    travelRules(r, 1, { stops: [{ map: '1-medical', arrival: 'Backtrack_MedicalStart' },
      { map: '6-slums', arrival: 'Hammer_Start' }], redirects: [] });
    r.frames(12);
    assert.strictEqual(model.arrivalOf(manager, 5), 'PlayerStart_FromDeck7Bathy', 'left as it was');
    model.nameIndex('Hammer_Start'); // a level that has it was loaded
    r.string(g.level + 0x7C, '1-Medical');
    r.frames(12);
    assert.strictEqual(model.arrivalOf(manager, 5), 'Hammer_Start');
  },
  'a story flag that exists is set in place, and Olympus Heights is still offered': () => {
    const r = makeRuntime(); r.game(); const model = r.objectModel();
    const manager = model.bathysphere([0], 8);
    const flags = model.storyFlags(); const flag = flags.variable('global_transitionedFromRyan');
    r.load(); r.frames(2);
    travelRules(r, 1, { stops: [{ map: '1-medical' }, { map: '6-slums', arrival: 'PlayerStart_First', to: '6-resi',
      title: 'Olympus Heights' }], flags: { global_transitionedFromRyan: '1' }, redirects: [] });
    r.frames(12);
    assert.deepStrictEqual(model.unlockedOf(manager), [0, 5], 'the rest of the rules still apply');
    assert.strictEqual(r.memory.get(flag + 0x40), 2, 'Value is the second flag in its word');
    assert.ok(r.logged('story flag global_transitionedFromRyan = 1'));
    assert.strictEqual(r.scriptCalls.length, 0, 'no call needed');
    assert.strictEqual(state(r).travelTrouble, null);
  },
  'a story flag from a skipped chapter is made by the game, then set': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    model.bathysphere([0], 8);
    const flags = model.storyFlags(); model.nameIndex('global_transitionedFromRyan');
    let made = null;
    r.onScriptCall = (self, fn) => {
      if (fn === flags.newGlobal) {
        const parms = r.scriptCalls.at(-1).parms;
        assert.strictEqual(self, flags.script);
        assert.strictEqual(r.memory.get(parms), model.nameIndex('global_transitionedFromRyan'));
        assert.strictEqual(r.memory.get(parms + 8), flags.variableBool);
        made = flags.variable('global_transitionedFromRyan');
        r.poke(parms + 0xC, made);
      }
    };
    r.load(); r.frames(2);
    travelRules(r, 1, { stops: [{ map: '1-medical' }, { map: '6-slums', to: '6-resi', title: 'Olympus Heights' }],
      flags: { global_transitionedFromRyan: '1' }, redirects: [] });
    r.frames(12);
    assert.strictEqual(made, null, 'no call of the game\'s to copy yet');
    r.gameEvent(game.hud, model.tick); r.frames(40);
    assert.notStrictEqual(made, null);
    assert.strictEqual(r.memory.get(made + 0x40), 2);
    assert.ok(r.logged('the game made story flag global_transitionedFromRyan'));
    assert.strictEqual(r.scriptCalls.filter((c) => c.fn === flags.newGlobal).length, 1, 'once');
    assert.strictEqual(state(r).travelTrouble, null);
  },
  'a destination the game unlocked and then lost (a save loaded in the same map) is asked for again': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    const manager = model.bathysphere([0, 1], 2);
    r.onScriptCall = (self, fn) => {
      if (fn === model.unlockDestination) { model.gameUnlocks(r.scriptCalls.at(-1).parms, self); }
    };
    r.load(); r.frames(2);
    travelRules(r, 1, { stops: [{ map: '1-medical' }, { map: '2-fisheries' }, { map: '4-recreation' }], redirects: [] });
    r.frames(4); r.gameEvent(game.hud, model.tick); r.frames(30);
    assert.deepStrictEqual(model.unlockedOf(manager), [0, 1, 3]);
    const calls = r.scriptCalls.length;
    const buffer = r.object(8); r.poke(buffer, 0); r.poke(buffer + 4, 1); // the save's own array, room for 2
    r.poke(manager + 0x8C, buffer); r.poke(manager + 0x90, 2); r.poke(manager + 0x94, 2);
    r.frames(4); r.gameEvent(game.hud, model.tick); r.frames(30);
    assert.ok(r.scriptCalls.length > calls, 'asked again');
    assert.deepStrictEqual(model.unlockedOf(manager), [0, 1, 3]);
    assert.strictEqual(state(r).travelTrouble, null);
  },
  'a crossing into a locked chapter is pointed at the hub, and back once the chapter opens': () => {
    const r = makeRuntime(); const g = r.game(); const model = r.objectModel();
    r.string(g.level + 0x7C, '2-SubBay');
    model.bathysphere([0], 8);
    const change = model.levelChange('3-Arcadia', 'FromFisheriesPS', 32);
    const back = model.levelChange('2-Fisheries', 'FromSubBayPS', 32);
    r.load(); r.frames(2);
    const locked = { stops: [{ map: '1-medical' }], redirects: [
      { from: '2-subbay', to: '3-arcadia', map: '1-medical', arrival: 'Backtrack_MedicalStart' }] };
    travelRules(r, 1, locked);
    r.frames(8);
    assert.deepStrictEqual(model.changeOf(change),
      { map: '1-Medical', label: 'Backtrack_MedicalStart', rows: ['1-Medical', 'Backtrack_MedicalStart'] });
    assert.deepStrictEqual(model.changeOf(back).rows, ['2-Fisheries', 'FromSubBayPS'], 'the way back is left alone');
    assert.strictEqual(state(r).travelRedirects, 1);
    travelRules(r, 2, { stops: [{ map: '1-medical' }, { map: '3-arcadia' }], redirects: [] });
    r.frames(8);
    assert.deepStrictEqual(model.changeOf(change),
      { map: '3-Arcadia', label: 'FromFisheriesPS', rows: ['3-Arcadia', 'FromFisheriesPS'] });
    assert.strictEqual(state(r).travelRedirects, 0);
  },
  'a crossing a save kept pointed at the hub goes where the story sends it once its chapter is open': () => {
    const r = makeRuntime(); const g = r.game(); const model = r.objectModel();
    r.string(g.level + 0x7C, '2-SubBay');
    model.bathysphere([0], 8);
    model.levelChange('3-Arcadia', 'FromArcadiaPS', 32); // nothing to do with it
    const saved = model.levelChange('1-Medical', 'Backtrack_MedicalStart', 32);
    const fallback = model.levelChange('2-SubBay', 'FromArcadiaPS', 32);
    r.load(); r.frames(2);
    travelRules(r, 1, { stops: [{ map: '1-medical' }, { map: '3-arcadia' }], redirects: [], restores: [
      { from: '2-subbay', to: '3-arcadia', arrival: 'FromFisheriesPS', hub: '1-medical',
        hubArrival: 'Backtrack_MedicalStart' }] });
    r.frames(8);
    assert.deepStrictEqual(model.changeOf(saved),
      { map: '3-Arcadia', label: 'FromFisheriesPS', rows: ['3-Arcadia', 'FromFisheriesPS'] });
    assert.deepStrictEqual(model.changeOf(fallback).rows, ['2-SubBay', 'FromArcadiaPS'], 'the fallback is never undone');
    assert.ok(r.logged('that a save kept pointed at 1-medical leads to 3-arcadia again'));
  },
  'rules turned off put a redirected crossing back': () => {
    const r = makeRuntime(); const g = r.game(); const model = r.objectModel();
    r.string(g.level + 0x7C, '2-SubBay');
    model.bathysphere([0], 8);
    const change = model.levelChange('3-Arcadia', 'FromFisheriesPS', 32);
    r.load(); r.frames(2);
    travelRules(r, 1, { stops: [{ map: '1-medical' }], redirects: [
      { from: '2-subbay', to: '3-arcadia', map: '1-medical', arrival: 'Backtrack_MedicalStart' }] });
    r.frames(8);
    assert.strictEqual(model.changeOf(change).map, '1-Medical');
    travelRules(r, 2, { off: true });
    r.frames(8);
    assert.deepStrictEqual(model.changeOf(change),
      { map: '3-Arcadia', label: 'FromFisheriesPS', rows: ['3-Arcadia', 'FromFisheriesPS'] });
  },
  'a save loaded in the same map: the last level\'s crossings are forgotten and the new ones redirected': () => {
    const r = makeRuntime(); const g = r.game(); const model = r.objectModel();
    r.string(g.level + 0x7C, '2-SubBay');
    model.bathysphere([0], 8);
    const old = model.levelChange('3-Arcadia', 'FromFisheriesPS', 32);
    r.load(); r.frames(2);
    travelRules(r, 1, { stops: [{ map: '1-medical' }], redirects: [
      { from: '2-subbay', to: '3-arcadia', map: '1-medical', arrival: 'Backtrack_MedicalStart' }] });
    r.frames(8);
    assert.strictEqual(model.changeOf(old).map, '1-Medical');
    r.poke(old + 0x30, 0); // freed with its level
    r.levels.splice(1, 1);
    const again = r.level('2-SubBay', { extra: 6, capacity: 1030 }); r.player(again, { viewport: g.player });
    const fresh = model.levelChange('3-Arcadia', 'FromFisheriesPS', 32);
    r.frames(30);
    assert.strictEqual(model.changeOf(fresh).map, '1-Medical');
    assert.strictEqual(state(r).travelRedirects, 1);
  },
  'a redirect taken is reported when the hub level comes up': () => {
    const r = makeRuntime(); const g = r.game(); const model = r.objectModel();
    r.string(g.level + 0x7C, '2-SubBay');
    model.levelChange('3-Arcadia', 'FromFisheriesPS', 32);
    model.bathysphere([0], 8);
    r.load(); r.frames(2);
    travelRules(r, 1, { stops: [{ map: '1-medical' }], redirects: [
      { from: '2-subbay', to: '3-arcadia', map: '1-medical', arrival: 'Backtrack_MedicalStart' }] });
    r.frames(8);
    r.string(g.level + 0x7C, '1-Medical');
    r.frames(4);
    assert.deepStrictEqual(r.messages.filter((m) => m.type === 'travel_redirected').map((m) => [m.from, m.to]),
      [['2-subbay', '3-arcadia']]);
  },
  'where the hub does not fit, a crossing takes the fallback, which does': () => {
    const r = makeRuntime(); const g = r.game(); const model = r.objectModel();
    r.string(g.level + 0x7C, '2-SubBay');
    model.bathysphere([0], 8);
    const change = model.levelChange('3-Arcadia', 'FromFisheriesPS');
    r.load(); r.frames(2);
    travelRules(r, 1, { stops: [{ map: '1-medical' }], redirects: [{ from: '2-subbay', to: '3-arcadia', map: '1-medical',
      arrival: 'Backtrack_MedicalStart', fallback: { map: '2-subbay', arrival: 'FromArcadiaPS' } }] });
    r.frames(8);
    assert.deepStrictEqual(model.changeOf(change),
      { map: '2-SubBay', label: 'FromArcadiaPS', rows: ['2-SubBay', 'FromArcadiaPS'] });
    assert.strictEqual(state(r).travelTrouble, null);
    r.frames(6);
    assert.deepStrictEqual(model.changeOf(change).rows, ['2-SubBay', 'FromArcadiaPS'], 'and it stays so');
  },
  'where the hub does not fit, the crossing borrows script notes\' strings, and gives them back when it opens': () => {
    const r = makeRuntime(); const g = r.game(); const model = r.objectModel();
    r.string(g.level + 0x7C, '2-SubBay');
    model.bathysphere([0], 8);
    const short = model.note('tiny', 4);
    const first = model.note('A long note about the submarine', 40);
    const second = model.note('Another long note about the dock', 40);
    const change = model.levelChange('3-Arcadia', 'FromFisheriesPS');
    const labelBuffer = r.memory.get(change + 0x8C);
    r.load(); r.frames(2);
    travelRules(r, 1, { stops: [{ map: '1-medical' }], redirects: [{ from: '2-subbay', to: '3-arcadia', map: '1-medical',
      arrival: 'Backtrack_MedicalStart', fallback: { map: '2-subbay', arrival: 'FromArcadiaPS' } }] });
    r.frames(8);
    assert.deepStrictEqual(model.changeOf(change),
      { map: '1-Medical', label: 'Backtrack_MedicalStart', rows: ['1-Medical', 'Backtrack_MedicalStart'] });
    assert.strictEqual(model.textOf(short + 0x80), 'tiny', 'a note without room is left alone');
    // "1-Medical" fits where "3-Arcadia" was; the arrival point does not, so it takes the first note's buffer.
    assert.strictEqual(r.memory.get(first + 0x80), labelBuffer, 'the note now owns the old buffer: one owner each');
    assert.strictEqual(model.textOf(second + 0x80), 'Another long note about the dock', 'only what is needed');
    assert.strictEqual(state(r).travelTrouble, null);
    travelRules(r, 2, { stops: [{ map: '1-medical' }, { map: '3-arcadia' }], redirects: [] });
    r.frames(8);
    assert.deepStrictEqual(model.changeOf(change),
      { map: '3-Arcadia', label: 'FromFisheriesPS', rows: ['3-Arcadia', 'FromFisheriesPS'] });
  },
  'a level change whose strings have no room and no note to borrow from is left alone and said': () => {
    const r = makeRuntime(); const g = r.game(); const model = r.objectModel();
    r.string(g.level + 0x7C, '5-Ryan');
    model.bathysphere([0], 8);
    const change = model.levelChange('6-Resi', 'PlayerStart_First');
    r.load(); r.frames(2);
    travelRules(r, 1, { stops: [{ map: '1-medical' }], redirects: [
      { from: '5-ryan', to: '6-resi', map: '1-medical', arrival: 'Backtrack_MedicalStart' }] });
    r.frames(8);
    assert.deepStrictEqual(model.changeOf(change).rows, ['6-Resi', 'PlayerStart_First']);
    assert.match(state(r).travelTrouble, /could not point the level change to 6-resi elsewhere/);
  },
  // ---- Gatherer's Gardens ----
  'garden_lines makes each line a copy of chips with its own name, priced from the item': () => {
    const r = makeRuntime(); r.game(); const model = r.objectModel();
    const { lines } = model.garden(3);
    const sold = r.memory.get(lines[1]);
    r.load(); r.frames(2);
    gardenLines(r, 7, [{ line: 1, name: 'Hookshot', description: 'For Halthrax', price: 80, bought: false },
      { line: 3, name: 'Bomb', description: 'Yours', price: 20, bought: false }]);
    assert.deepStrictEqual(r.messages.filter((m) => m.type === 'action_result').map((m) => [m.id, m.name, m.ok]),
      [[7, 'garden_lines', true]]);
    r.frames(8);
    const copy = r.memory.get(lines[0]);
    assert.ok(copy >= 0x70000000, 'line 1 sells a copy made by the agent');
    assert.strictEqual(r.memory.get(copy + 0x28), r.memory.get(model.chips + 0x28), 'still named chips');
    assert.strictEqual(r.memory.get(copy + 0x40), r.memory.get(model.chips + 0x40), 'still a Consumable');
    const values = r.memory.get(copy + 0x54C);
    assert.notStrictEqual(values, model.defaults, 'with default values of its own');
    assert.strictEqual(r.strings.get(r.memory.get(values + 0x50)), 'Hookshot');
    assert.strictEqual(r.strings.get(r.memory.get(values + 0x44)), 'For Halthrax');
    assert.strictEqual(r.text.get(r.memory.get(model.defaults + 0x50)), 'Potato Chips', 'chips itself is untouched');
    assert.strictEqual(r.memory.get(lines[0] + 0xC), 200, '80 ADAM is 20 times the price of chips');
    assert.notStrictEqual(r.memory.get(lines[2]), copy, 'line 3 has a copy of its own');
    assert.strictEqual(r.memory.get(lines[2] + 0xC), 50);
    assert.strictEqual(r.memory.get(lines[1]), sold, 'line 2 is not a check');
    assert.strictEqual(r.memory.get(lines[1] + 0xC), 10);
    assert.strictEqual(state(r).gardenLines, 2);
    assert.strictEqual(state(r).gardenTrouble, null);
    assert.ok(r.heapAllocs.includes(0x1000), 'the copies are on the game\'s heap, which outlives the agent');
  },
  'our own BioShock items cost what the game charges for them': () => {
    const r = makeRuntime(); r.game(); const model = r.objectModel();
    const enrage = model.itemClass('BerserkRage', 30); model.itemClass('SlowFlow', 20);
    const { lines } = model.garden(4);
    r.poke(lines[3], enrage); r.poke(lines[3] + 0xC, 20); // line 4 sells Enrage at twice its value
    r.load(); r.frames(2);
    gardenLines(r, 1, [
      { line: 1, name: 'Enrage!', description: '', price: 80, class: 'BerserkRage', known: false },
      { line: 2, name: 'Speedy Hacker', description: '', price: 80, class: 'SlowFlow', known: false },
      { line: 3, name: 'Clever Inventor', description: '', price: 80, class: 'EfficientCrafter', known: false },
      { line: 4, name: 'Hookshot', description: '', price: 20 },
    ]);
    r.frames(8);
    assert.strictEqual(r.memory.get(lines[0] + 0xC), 150, 'Enrage: 30 x 2 = 60 ADAM, as the Garden sells it here');
    assert.strictEqual(r.memory.get(lines[1] + 0xC), 50, 'Speedy Hacker is not sold here: its value, 20 ADAM');
    assert.strictEqual(r.memory.get(lines[2] + 0xC), 200, 'a class that is not loaded: the client\'s price');
    assert.strictEqual(r.memory.get(lines[3] + 0xC), 50, 'someone else\'s item: the client\'s price');
    const nameOf = (at) => r.memory.get(at + 0x28);
    assert.strictEqual(nameOf(r.memory.get(lines[0])), nameOf(enrage), 'Enrage\'s line has its name, for its icon');
    assert.strictEqual(nameOf(r.memory.get(lines[2])), nameOf(model.chips), 'no class loaded: the chips icon');
    assert.strictEqual(nameOf(r.memory.get(lines[3])), nameOf(model.chips));
  },
  'redirect_movies hands the game the client\'s menu, whose lines then take the AP icon': () => {
    const r = makeRuntime(); r.game(); const model = r.objectModel();
    const enrage = model.itemClass('BerserkRage', 30); const thrower = model.itemClass('ChemicalThrower', 0);
    const { lines } = model.garden(4);
    r.load(); r.frames(2);
    assert.strictEqual(state(r).exe, 'E:\\Game\\Build\\Final\\BioshockHD.exe', 'for the client to find the menu');
    const redirect = (id, offer) => r.context.rpc.exports.action(id, `redirect_movies ${JSON.stringify(offer)}`);
    redirect(1, { folder: 'C:\\cache\\FlashMovies\\', files: ['craftingStationPC.swf', 'APIcons.swf'] });
    redirect(2, { folder: '', files: [] });
    assert.strictEqual(JSON.stringify(r.messages.filter((m) => m.type === 'action_result').map((m) => [m.id, m.ok])),
      '[[1,true],[2,false]]');
    gardenLines(r, 3, [{ line: 1, name: 'Hookshot', description: '', price: 80 },
      { line: 2, name: 'Enrage!', description: '', price: 80, class: 'BerserkRage', known: false },
      { line: 3, name: 'Chemical Thrower Range', description: '', price: 80, icon: 'ChemicalThrower' },
      { line: 4, name: 'Pistol Clip Size', description: '', price: 80, icon: 'Pistol' }]);
    r.frames(8);
    const nameOf = (at) => r.memory.get(at + 0x28);
    assert.strictEqual(nameOf(r.memory.get(lines[0])), nameOf(model.chips), 'the menu open now is the game\'s own');
    assert.strictEqual(state(r).apIcon, false);
    const game = 'E:\\Game\\Build\\Final\\BakedScripts\\PC\\..\\FlashMovies\\';
    assert.strictEqual(r.openFile(`${game}craftingstationPC.swf`), 'C:\\cache\\FlashMovies\\craftingStationPC.swf');
    assert.strictEqual(r.openFile(`${game}APIcons.swf`), 'C:\\cache\\FlashMovies\\APIcons.swf');
    assert.strictEqual(r.openFile(`${game}PlasmiNow.swf`), `${game}PlasmiNow.swf`, 'other movies are the game\'s');
    assert.strictEqual(r.openFile('E:\\Saves\\craftingStationPC.swf'), 'E:\\Saves\\craftingStationPC.swf');
    assert.strictEqual(state(r).apIcon, true);
    r.frames(8);
    assert.strictEqual(nameOf(r.memory.get(lines[0])), nameOf(model.item), 'someone else\'s item: named Item, for the AP icon');
    assert.strictEqual(nameOf(r.memory.get(lines[1])), nameOf(enrage), 'our own item keeps its real icon');
    assert.strictEqual(nameOf(r.memory.get(lines[2])), nameOf(thrower), 'a weapon upgrade shows its weapon');
    assert.strictEqual(r.memory.get(lines[2] + 0xC), 200, '... at the client\'s price, not the weapon\'s value');
    assert.strictEqual(nameOf(r.memory.get(lines[3])), nameOf(model.item), 'its weapon not loaded: the AP icon');
    redirect(4, { folder: 'D:\\other', files: ['APIcons.swf'] });
    assert.strictEqual(r.hooks.filter((h) => h.address === CREATE_FILE).length, 1, 'one hook, whatever is asked');
    assert.strictEqual(r.openFile(`${game}APIcons.swf`), 'D:\\other\\APIcons.swf');
  },
  'a Garden line bought is reported by map, and sold out in every Garden of the level': () => {
    const r = makeRuntime(); r.game(); const model = r.objectModel();
    const first = model.garden(2); const second = model.garden(2);
    r.load(); r.frames(2);
    gardenLines(r, 1, [{ line: 2, name: 'Hookshot', description: '', price: 80, bought: false }]);
    r.frames(8);
    assert.strictEqual(JSON.stringify(state(r).gardenBought), '{"1-medical":[]}');
    r.poke(first.lines[1] + 0x18, 1); // bought
    r.frames(8);
    assert.strictEqual(JSON.stringify(state(r).gardenBought), '{"1-medical":[2]}');
    assert.strictEqual(r.memory.get(second.lines[1] + 0x18), 1, 'the same line elsewhere is sold out');
  },
  'a Garden line the client says is bought is sold out': () => {
    const r = makeRuntime(); r.game(); const model = r.objectModel();
    const { lines } = model.garden(2);
    r.load(); r.frames(2);
    gardenLines(r, 1, [{ line: 1, name: 'Hookshot', description: '', price: 80, bought: true }]);
    r.frames(8);
    assert.strictEqual(r.memory.get(lines[0] + 0x18), 1);
    assert.ok(r.memory.get(lines[0]) < 0x70000000, 'and keeps what it sold');
  },
  'Garden lines meant for another map are left alone': () => {
    const r = makeRuntime(); r.game(); const model = r.objectModel();
    const { lines } = model.garden(2);
    r.load(); r.frames(2);
    r.context.rpc.exports.action(1, 'garden_lines ' + JSON.stringify(
      { level: 'Arcadia', map: '3-arcadia', lines: [{ line: 1, name: 'Hookshot', description: '', price: 80 }] }));
    r.frames(8);
    assert.ok(r.memory.get(lines[0]) < 0x70000000);
    assert.strictEqual(r.memory.get(lines[0] + 0xC), 10);
  },
  'Garden lines without the chips class are left alone': () => {
    const r = makeRuntime(); r.game(); const model = r.objectModel();
    const { lines } = model.garden(2);
    r.poke(model.chips + 0x28, r.memory.get(lines[0] + 0x28)); // no class of that name is loaded
    r.load(); r.frames(2);
    gardenLines(r, 1, [{ line: 1, name: 'Hookshot', description: '', price: 80, bought: false }]);
    r.frames(8);
    assert.ok(r.memory.get(lines[0]) < 0x70000000);
  },
  // ---- Traps ----
  'eve_drain and pickpocket call the game\'s RemoveBioAmmo and RemoveCredits': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    r.poke(game.pawn + 0x30, model.playerClass); model.playerStats(); r.creditsAt = game.pawn + 0xAEC;
    r.load(); r.frames(2);
    r.context.rpc.exports.action(1, 'eve_drain');
    r.context.rpc.exports.action(2, 'pickpocket');
    r.frames(2);
    assert.strictEqual(r.messages.filter((m) => m.type === 'action_result').length, 0, 'waiting for a call to copy');
    r.gameEvent(game.hud, model.tick);
    r.frames(1);
    const calls = r.scriptCalls.map((c) => [c.self, c.fn, r.memory.get(c.parms)]);
    const fns = calls.map(([self, fn]) => [self, fn]);
    assert.deepStrictEqual(fns, [[game.pawn, model.removeBioAmmo], [game.pawn, model.removeCredits]]);
    assert.strictEqual(calls[0][2], 1000, 'all 100 EVE');
    assert.strictEqual(calls[1][2], 62, 'half of 123 dollars, rounded up');
    assert.deepStrictEqual(r.messages.filter((m) => m.type === 'action_result').map((m) => [m.id, m.name, m.ok]),
      [[1, 'eve_drain', true], [2, 'pickpocket', true]]);
  },
  'a Remove function that fails is not called again, and the trap writes the number instead': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    r.poke(game.pawn + 0x30, model.playerClass); model.playerStats(); r.creditsAt = game.pawn + 0xAEC;
    r.load(); r.frames(2);
    r.onScriptCall = () => { throw new Error('access violation accessing 0x74'); }; // as seen in the game
    r.context.rpc.exports.action(1, 'eve_drain');
    r.context.rpc.exports.action(2, 'pickpocket');
    r.frames(2);
    r.gameEvent(game.hud, model.tick);
    r.frames(1);
    assert.strictEqual(r.memory.get(game.pawn + 0xAF8), 0, 'EVE emptied');
    assert.strictEqual(r.memory.get(game.pawn + 0xAEC), 61, '62 of 123 dollars taken');
    assert.deepStrictEqual(r.messages.filter((m) => m.type === 'action_result').map((m) => [m.id, m.ok]),
      [[1, true], [2, true]]);
    const called = r.scriptCalls.length;
    r.poke(game.pawn + 0xAEC, 100);
    r.context.rpc.exports.action(3, 'pickpocket');
    r.frames(2);
    assert.strictEqual(r.scriptCalls.length, called, 'RemoveCredits is not tried again');
    assert.strictEqual(r.memory.get(game.pawn + 0xAEC), 50);
  },
  'a number written by a trap is redrawn on the HUD by calling UpdateUIStats\'s C++ body': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    r.poke(game.pawn + 0x30, model.playerClass); model.playerStats(false, true); r.creditsAt = game.pawn + 0xAEC;
    r.poke(model.updateUIStats + 0xC0, BASE + 0x1D86F0); r.poke(model.updateUIBioAmmoBank + 0xC0, BASE + 0x1D8910);
    r.nativeBodies = new Map([[BASE + 0x1D86F0, 'UpdateUIStats'], [BASE + 0x1D8910, 'UpdateUIBioAmmoBank']]);
    r.nativeCalls = [];
    r.load(); r.frames(2);
    r.context.rpc.exports.action(1, 'eve_drain');
    r.frames(3);
    assert.strictEqual(r.memory.get(game.pawn + 0xAF8), 0, 'EVE emptied');
    assert.strictEqual(r.nativeCalls.length, 0, 'waiting for a call to copy the frame\'s vtable from');
    r.gameEvent(game.hud, model.tick);
    r.frames(2);
    assert.strictEqual(r.scriptCalls.length, 0, 'no script function called through ProcessEvent');
    assert.deepStrictEqual(r.nativeCalls.map((c) => [c.name, c.self, c.fn, c.object]),
      [['UpdateUIStats', game.pawn, model.updateUIStats, game.pawn],
        ['UpdateUIBioAmmoBank', game.pawn, model.updateUIBioAmmoBank, game.pawn]]);
    assert.ok(r.nativeCalls.every((c) => c.vtable !== 0 && c.vtable !== undefined), 'the copied frame\'s vtable');
    assert.strictEqual(r.memory.get(game.pawn + 0xAEC), 123, 'no dollar taken or given');
    assert.deepStrictEqual(r.execCalls.map((c) => c.command), [], 'no console command, so no "Collected" message');
    r.frames(5);
    assert.strictEqual(r.nativeCalls.length, 2, 'redrawn once per trap');
  },
  'a HUD redraw that fails is not tried again': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    r.poke(game.pawn + 0x30, model.playerClass); model.playerStats(false, true); r.creditsAt = game.pawn + 0xAEC;
    r.poke(model.updateUIStats + 0xC0, BASE + 0x1D86F0); r.poke(model.updateUIBioAmmoBank + 0xC0, 0x1234);
    r.nativeBodies = new Map([[BASE + 0x1D86F0, 'UpdateUIStats']]);
    r.nativeCalls = [];
    r.onNativeCall = () => { throw new Error('access violation accessing 0x74'); };
    r.load(); r.frames(2);
    r.context.rpc.exports.action(1, 'eve_drain');
    r.frames(3);
    r.gameEvent(game.hud, model.tick);
    r.frames(2);
    assert.strictEqual(r.nativeCalls.length, 1, 'UpdateUIBioAmmoBank is not reached either');
    r.context.rpc.exports.action(2, 'pickpocket');
    r.frames(3);
    assert.strictEqual(r.memory.get(game.pawn + 0xAEC), 61, 'the trap still works');
    assert.strictEqual(r.nativeCalls.length, 1, 'not called again');
  },
  'without the Remove functions the trap writes the number itself': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    r.poke(game.pawn + 0x30, model.playerClass); model.playerStats(false); r.creditsAt = game.pawn + 0xAEC;
    r.load(); r.frames(2);
    r.context.rpc.exports.action(1, 'eve_drain');
    r.context.rpc.exports.action(2, 'pickpocket');
    r.frames(2);
    assert.strictEqual(r.memory.get(game.pawn + 0xAF8), 0);
    assert.strictEqual(r.memory.get(game.pawn + 0xAEC), 61);
    assert.deepStrictEqual(r.messages.filter((m) => m.type === 'action_result').map((m) => [m.id, m.ok]),
      [[1, true], [2, true]]);
  },
  'a trap is refused where the player has no such number': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    r.poke(game.pawn + 0x30, model.playerClass); // no BioAmmo declared
    r.load(); r.frames(2);
    r.context.rpc.exports.action(1, 'eve_drain');
    r.frames(2);
    assert.strictEqual(r.memory.get(game.pawn + 0xAF8), 1000, 'left alone');
    const answers = r.messages.filter((m) => m.type === 'action_result');
    assert.deepStrictEqual(answers.map((m) => [m.id, m.ok]), [[1, false]]);
    assert.ok(answers[0].reason.includes('no BioAmmo'), answers[0].reason);
  },
  'state() reports the plasmids and tonics taken, and the ones still lying in the map': () => {
    const r = makeRuntime(); r.game(); const model = r.objectModel();
    const telekinesis = model.itemClass('Telekinesis', 10);
    const taken = model.pickup('ActivePlasmidPickup', 'ActivePlasmidPickup0', telekinesis);
    model.pickup('WeaponsPlasmidPickup', 'WeaponsPlasmidPickup1', model.itemClass('WrenchJockey', 10));
    r.load(); r.frames(10);
    assert.strictEqual(JSON.stringify(state(r).pickupsTaken), '{}');
    assert.strictEqual(JSON.stringify(state(r).pickupsHere),
      '{"map":"1-medical","names":["ActivePlasmidPickup0","WeaponsPlasmidPickup1"]}');
    r.poke(taken + 0xCC, 0x2); r.frames(10); // bDeleteMe: the player took it
    assert.strictEqual(JSON.stringify(state(r).pickupsTaken), '{"1-medical":["ActivePlasmidPickup0"]}');
    assert.strictEqual(JSON.stringify(state(r).pickupsHere), '{"map":"1-medical","names":["WeaponsPlasmidPickup1"]}');
    assert.ok(r.logged('ActivePlasmidPickup0 taken in 1-medical'));
  },
  'a pickup the level no longer lists counts as taken': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    const taken = model.pickup('PhysicalPlasmidPickup', 'PhysicalPlasmidPickup0', model.itemClass('Medic', 10));
    r.load(); r.frames(10);
    r.removeActor(game.level, taken); r.frames(10);
    assert.strictEqual(JSON.stringify(state(r).pickupsTaken), '{"1-medical":["PhysicalPlasmidPickup0"]}');
  },
  'loading a save makes new pickups with the same names, which are not taken': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    const medic = model.itemClass('Medic', 10);
    const first = model.pickup('PhysicalPlasmidPickup', 'PhysicalPlasmidPickup0', medic);
    r.load(); r.frames(10);
    r.removeActor(game.level, first); r.poke(first + 0x30, 0); // the save's level has its own copy
    model.pickup('PhysicalPlasmidPickup', 'PhysicalPlasmidPickup0', medic); r.frames(10);
    assert.strictEqual(JSON.stringify(state(r).pickupsTaken), '{}');
    assert.strictEqual(JSON.stringify(state(r).pickupsHere), '{"map":"1-medical","names":["PhysicalPlasmidPickup0"]}');
  },
  'pickups of another level object are not compared with the last one': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    const gone = model.pickup('PhysicalPlasmidPickup', 'PhysicalPlasmidPickup0', model.itemClass('Medic', 10));
    r.load(); r.frames(10);
    r.removeActor(game.level, gone);
    r.levels.splice(1, 1); // the level is unloaded, and the same map is made anew (a save loaded)
    const again = r.level('1-medical', { extra: 6, capacity: 1030 }); r.player(again, { viewport: game.player });
    r.frames(30);
    assert.strictEqual(state(r).playerReady, true);
    assert.strictEqual(JSON.stringify(state(r).pickupsTaken), '{}');
    assert.strictEqual(JSON.stringify(state(r).pickupsHere), '{"map":"1-medical","names":[]}');
  },
  'a pickup missed by one pass only is not taken': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    const flicker = model.pickup('PhysicalPlasmidPickup', 'PhysicalPlasmidPickup0', model.itemClass('Medic', 10));
    r.load(); r.frames(10);
    r.removeActor(game.level, flicker); r.frames(1);
    assert.strictEqual(state(r).pickupsHere, null, 'two passes disagree: no list');
    r.addActor(game.level, flicker); r.frames(10);
    assert.strictEqual(JSON.stringify(state(r).pickupsTaken), '{}');
    assert.strictEqual(JSON.stringify(state(r).pickupsHere), '{"map":"1-medical","names":["PhysicalPlasmidPickup0"]}');
  },
  'passes made while a level is loading take nothing away': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    const medic = model.itemClass('Medic', 10);
    const first = model.pickup('PhysicalPlasmidPickup', 'PhysicalPlasmidPickup0', medic);
    r.load(); r.frames(10);
    r.memory.set(BASE + 0x1356680, 1); // a save loading
    r.removeActor(game.level, first); r.poke(first + 0x30, 0); r.frames(10);
    assert.strictEqual(state(r).pickupsHere, null);
    model.pickup('PhysicalPlasmidPickup', 'PhysicalPlasmidPickup0', medic);
    r.memory.set(BASE + 0x1356680, 0); r.frames(10);
    assert.strictEqual(JSON.stringify(state(r).pickupsTaken), '{}');
    assert.strictEqual(JSON.stringify(state(r).pickupsHere), '{"map":"1-medical","names":["PhysicalPlasmidPickup0"]}');
  },
  'a pickup whose memory now holds another object is not emptied': () => {
    const r = makeRuntime(); r.game(); const model = r.objectModel();
    const jockey = model.itemClass('WrenchJockey', 10); const incinerate = model.itemClass('Incinerate', 10);
    const reused = model.pickup('WeaponsPlasmidPickup', 'WeaponsPlasmidPickup1', jockey);
    const count = r.memory.get(BASE + 0x1390430); const all = r.memory.get(BASE + 0x139042C);
    const big = r.object(4 * 40000); // a table of 40000 objects, mostly empty slots: a pass now takes ten ticks
    for (let i = 0; i < count; i++) { r.poke(big + 4 * i, r.memory.get(all + 4 * i)); }
    r.poke(BASE + 0x139042C, big); r.poke(BASE + 0x1390430, 40000);
    r.load(); r.frames(25);
    model.rename(reused, 'ActivePlasmidPickup2');
    const slot = r.memory.get(reused + 0x520);
    r.poke(r.memory.get(slot + 0x40) + 0x4C, incinerate); r.poke(r.memory.get(slot + 0x44) + 0x50, incinerate);
    r.context.rpc.exports.action(7, 'empty_pickups ' + JSON.stringify(
      { placeholder: 'chips', maps: { '1-medical': ['WeaponsPlasmidPickup1'] } }));
    r.frames(7);
    assert.deepStrictEqual(model.gives(reused), [incinerate, incinerate], 'the pass has not seen the new name yet');
    assert.strictEqual(state(r).pickupsEmptied, 0);
  },
  'pickups are emptied whatever spelling of the map the game uses': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    r.string(game.level + 0x7C, '1-Medical_int');
    const jockey = model.itemClass('WrenchJockey', 10);
    const shuffled = model.pickup('WeaponsPlasmidPickup', 'WeaponsPlasmidPickup1', jockey);
    r.load(); r.frames(3);
    r.context.rpc.exports.action(7, 'empty_pickups ' + JSON.stringify(
      { placeholder: 'chips', maps: { '1-medical': ['WeaponsPlasmidPickup1'] } }));
    r.frames(10);
    assert.deepStrictEqual(model.gives(shuffled), [model.chips, model.chips]);
  },
  'state() reports the safes open, by map, and leaves lockboxes alone': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    const safe = model.container('SecurityCrate_Safe', 'SecurityCrate_Safe2');
    const lockbox = model.container('Lockbox', 'Lockbox0');
    model.container('Lockbox', 'Lockbox1');
    const wall = model.container('SecurityCrate_WallSafe', 'SecurityCrate_WallSafe0'); // by Medical's Telekinesis
    r.load(); r.frames(10);
    assert.strictEqual(JSON.stringify(state(r).containersOpened), '{}');
    r.poke(safe + 0x5DC, 1); r.poke(lockbox + 0x5E0, 1); r.poke(wall + 0x5DC, 1); r.frames(10);
    const open = '{"1-medical":["SecurityCrate_Safe2","SecurityCrate_WallSafe0"]}';
    assert.strictEqual(JSON.stringify(state(r).containersOpened), open);
    [safe, lockbox, wall].forEach((at) => r.removeActor(game.level, at));
    r.string(game.level + 0x7C, '2-fisheries'); r.frames(10); // another level: what was open stays reported
    assert.strictEqual(JSON.stringify(state(r).containersOpened), open);
    assert.strictEqual(state(r).worldTrouble, null, 'no complaint about lockboxes');
  },
  'shuffled pickups give the placeholder, and the kept ones their own item': () => {
    const r = makeRuntime(); r.game(); const model = r.objectModel();
    const jockey = model.itemClass('WrenchJockey', 10); const incinerate = model.itemClass('Incinerate', 10);
    const shuffled = model.pickup('WeaponsPlasmidPickup', 'WeaponsPlasmidPickup1', jockey);
    const kept = model.pickup('ActivePlasmidPickup', 'ActivePlasmidPickup2', incinerate);
    r.load(); r.frames(3);
    r.context.rpc.exports.action(7, 'empty_pickups ' + JSON.stringify(
      { placeholder: 'chips', maps: { '1-medical': ['WeaponsPlasmidPickup1'], '2-fisheries': ['x'] } }));
    assert.deepStrictEqual(r.messages.filter((m) => m.type === 'action_result').map((m) => [m.id, m.ok]), [[7, true]]);
    r.frames(10);
    assert.deepStrictEqual(model.gives(shuffled), [model.chips, model.chips], 'its LootSpec and its rolled ItemStack');
    assert.deepStrictEqual(model.gives(kept), [incinerate, incinerate]);
    assert.strictEqual(state(r).pickupsEmptied, 1);
    assert.ok(r.logged('WeaponsPlasmidPickup1 in 1-medical now gives chips instead of WrenchJockey'));
    assert.strictEqual(state(r).worldTrouble, null);
    r.context.rpc.exports.action(8, 'empty_pickups ' + JSON.stringify({ placeholder: 'Bread', maps: {} }));
    assert.deepStrictEqual(r.messages.filter((m) => m.type === 'action_result').map((m) => [m.id, m.ok]),
      [[7, true], [8, false]]);
  },
  'a pickup not rolled yet gives the placeholder once it rolls': () => {
    const r = makeRuntime(); r.game(); const model = r.objectModel();
    const jockey = model.itemClass('WrenchJockey', 10);
    const later = model.pickup('WeaponsPlasmidPickup', 'WeaponsPlasmidPickup1', jockey, false);
    r.load(); r.frames(3);
    r.context.rpc.exports.action(7, 'empty_pickups ' + JSON.stringify(
      { placeholder: 'chips', maps: { '1-medical': ['WeaponsPlasmidPickup1'] } }));
    r.frames(10);
    assert.deepStrictEqual(model.gives(later), [model.chips, 0], 'what it will roll');
    model.roll(later); r.frames(10); // rolled from the swapped LootSpec: chips already
    assert.deepStrictEqual(model.gives(later), [model.chips, model.chips]);
    const stack = r.memory.get(r.memory.get(later + 0x520) + 0x44);
    r.poke(stack + 0x50, jockey); r.frames(10); // a stack rolled from before the swap is swapped on a later pass
    assert.deepStrictEqual(model.gives(later), [model.chips, model.chips]);
    assert.strictEqual(state(r).pickupsEmptied, 1);
  },
  'state() reports the level of each research subject and the table of levels': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    r.poke(game.pawn + 0x30, model.playerClass);
    const sportBoost = model.itemClass('SportBoost', 10);
    const fill = model.research([['ThuggishSplicer', 30], ['Bouncer', 0]],
      [['ThuggishSplicer', 1, 10, 0], ['ThuggishSplicer', 2, 25, sportBoost], ['ThuggishSplicer', 3, 60, 0],
        ['Bouncer', 1, 20, 0]]);
    const tracks = fill.tracks(game.pawn); fill.levels(game.pawn);
    r.load(); r.frames(15);
    assert.strictEqual(JSON.stringify(state(r).researchLevels), '{"ThuggishSplicer":2,"Bouncer":0}');
    assert.strictEqual(state(r).researchTable.length, 4);
    assert.strictEqual(state(r).holdItems, false, 'levels found on attaching open no research screen');
    assert.strictEqual(JSON.stringify(state(r).researchTable[1]),
      '{"name":"ThuggishSplicer","level":2,"score":25,"award":"SportBoost"}');
    r.poke(tracks + 16 + 8, 20); r.frames(15); // a photo of a Bouncer
    assert.strictEqual(JSON.stringify(state(r).researchLevels), '{"ThuggishSplicer":2,"Bouncer":1}');
    assert.ok(r.logged('research: Bouncer at level 1'));
    assert.strictEqual(state(r).holdItems, true, 'items wait while the research screen opens');
    assert.strictEqual(state(r).researchTrouble, null);
  },
  'research awards can be emptied': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    r.poke(game.pawn + 0x30, model.playerClass);
    const sportBoost = model.itemClass('SportBoost', 10);
    const fill = model.research([['ThuggishSplicer', 0]], [['ThuggishSplicer', 1, 10, 0], ['ThuggishSplicer', 2, 25, sportBoost],
      ['ThuggishSplicer', 3, 60, game.pawn]]); // a field read wrong: not a class at all
    fill.tracks(game.pawn); const levels = fill.levels(game.pawn);
    r.load(); r.frames(15);
    r.context.rpc.exports.action(5, 'empty_research_awards'); r.frames(15);
    assert.deepStrictEqual(r.messages.filter((m) => m.type === 'action_result').map((m) => [m.id, m.ok]), [[5, true]]);
    assert.strictEqual(r.memory.get(levels + 20 + 0x10), model.chips);
    assert.strictEqual(r.memory.get(levels + 0x10), 0, 'a level that gives nothing is left alone');
    assert.strictEqual(r.memory.get(levels + 40 + 0x10), game.pawn, 'only item classes are swapped');
    assert.strictEqual(state(r).researchAwardsEmptied, 1);
  },
  'a player without research tables says so once': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    r.poke(game.pawn + 0x30, model.playerClass);
    r.load(); r.frames(30);
    assert.strictEqual(JSON.stringify(state(r).researchLevels), '{}');
    assert.ok(state(r).researchTrouble.includes('no ResearchTracks'));
  },
  'a trap waits for a body and gives up after a while': () => {
    const r = makeRuntime(); const game = r.game(); const model = r.objectModel();
    r.poke(game.pawn + 0x30, model.playerClass); model.playerStats(); r.creditsAt = game.pawn + 0xAEC;
    r.load(); r.frames(2);
    r.poke(game.controller + 0x450, 0); // between two levels
    r.context.rpc.exports.action(3, 'pickpocket'); r.frames(3);
    assert.strictEqual(r.messages.filter((m) => m.type === 'action_result').length, 0);
    r.frames(310);
    assert.deepStrictEqual(r.messages.filter((m) => m.type === 'action_result').map((m) => [m.id, m.ok]),
      [[3, false]]);
    assert.strictEqual(r.memory.get(game.pawn + 0xAEC), 123);
  },
  'unsupported actions answer straight away': () => {
    const r = makeRuntime(); r.engine(); r.load();
    r.context.rpc.exports.action(4, 'flood_the_level');
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

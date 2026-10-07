/*
 * Runs probe_script_calls.js against a fake Frida runtime with byte-level fake memory: a name table, a table of
 * natives, and script calls with their frames and bytecode. It checks that the probe finds what it looks for, learns
 * the layout from sampled calls, and prints watched calls by name. The native filter is played by JavaScript that does
 * what the C source says. It cannot check the real game's layout; only the game can.
 *
 *   node probe_mock_test.js [path/to/probe_script_calls.js]
 */
'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const probePath = process.argv[2] || `${__dirname}/probe_script_calls.js`;
const source = fs.readFileSync(probePath, 'utf8');

const BASE = 0x400000;
const SIZE = 0x200000;
const RANGES = [
  { base: BASE + 0x1000, size: 0xFF000, protection: 'r-x' },
  { base: BASE + 0x100000, size: 0x80000, protection: 'r--' },
  { base: BASE + 0x180000, size: 0x20000, protection: 'rw-' },
];
const NATIVE_FUNCTION = BASE + 0x1234;
const NAMES_ARRAY = BASE + 0x180100;
const NAMES_DATA = 0x11000000;
const ENTRY_BASE = 0x10000000;
const NAME_TEXT = 0xC;
const OBJECT_NAME = 0x20;
const FRAME_OBJECT = 0x8;
const FRAME_CODE = 0xC;
// Script functions the fake game calls, so that a hook sees many different names.
const FUNCTIONS = ['PostBeginPlay', 'Touch', 'Bump', 'Landed', 'Destroyed', 'Trigger', 'UnTrigger', 'BeginState',
  'EndState', 'Died', 'TakeDamage', 'HitWall', 'Falling', 'ZoneChange', 'PlayerTick', 'UpdateHud', 'Use', 'Fire'];
const CALLED = ['Tick', 'Timer', ...FUNCTIONS];
const GNATIVES = BASE + 0x181000;
const UNDEFINED_NATIVE = BASE + 0x1F00;
const nativeAt = (opcode) => BASE + 0x2000 + opcode * 0x10;

function makeRuntime({ withNatives = true, cmodule = true, gnatives = null, gobjects = true } = {}) {
  const pages = new Map();
  const page = (address, create) => {
    const key = address >>> 12;
    let bytes = pages.get(key);
    if (bytes === undefined && create) {
      bytes = new Uint8Array(4096);
      pages.set(key, bytes);
    }
    return bytes;
  };
  const getByte = (address) => {
    const bytes = page(address, false);
    if (bytes === undefined) {
      throw new Error(`access violation accessing 0x${(address >>> 0).toString(16)}`);
    }
    return bytes[address & 0xFFF];
  };
  const setByte = (address, value) => { page(address, true)[address & 0xFFF] = value & 0xFF; };
  const writeU32 = (address, value) => { for (let i = 0; i < 4; i++) { setByte(address + i, value >>> (8 * i)); } };
  const readU32 = (address) => {
    let value = 0;
    for (let i = 0; i < 4; i++) { value |= getByte(address + i) << (8 * i); }
    return value >>> 0;
  };
  const writeWide = (address, text) => {
    for (let i = 0; i < text.length; i++) { setByte(address + 2 * i, text.charCodeAt(i)); setByte(address + 2 * i + 1, 0); }
    setByte(address + 2 * text.length, 0); setByte(address + 2 * text.length + 1, 0);
  };
  const writeAnsi = (address, text) => {
    for (let i = 0; i < text.length; i++) { setByte(address + i, text.charCodeAt(i)); }
    setByte(address + text.length, 0);
  };

  class Ptr {
    constructor(value) { this.value = value >>> 0; }
    add(n) { return new Ptr(this.value + (n instanceof Ptr ? n.value : n)); }
    sub(n) { return new Ptr(this.value - (n instanceof Ptr ? n.value : n)); }
    equals(other) { return this.value === other.value; }
    isNull() { return this.value === 0; }
    toUInt32() { return this.value; }
    toInt32() { return this.value | 0; }
    toString() { return `0x${this.value.toString(16)}`; }
    readU32() { return readU32(this.value); }
    readS32() { return readU32(this.value) | 0; }
    readU8() { return getByte(this.value); }
    readFloat() { return new Float32Array(new Uint32Array([readU32(this.value)]).buffer)[0]; }
    readPointer() { return new Ptr(readU32(this.value)); }
    writeS32(v) { writeU32(this.value, v); return this; }
    readByteArray(length) {
      const out = new Uint8Array(length);
      for (let i = 0; i < length; i++) { out[i] = getByte(this.value + i); }
      return out.buffer;
    }
    readUtf16String(length) {
      let text = '';
      for (let i = 0; i < length; i++) {
        const c = getByte(this.value + 2 * i) | (getByte(this.value + 2 * i + 1) << 8);
        if (c === 0) { break; }
        text += String.fromCharCode(c);
      }
      return text;
    }
    readCString(length) {
      let text = '';
      for (let i = 0; i < length; i++) {
        const c = getByte(this.value + i);
        if (c === 0) { break; }
        text += String.fromCharCode(c);
      }
      return text;
    }
  }
  const ptr = (n) => new Ptr(typeof n === 'string' ? parseInt(n, 16) : n);

  // The game: a name table, objects, frames.
  const names = ['None', 'ByteProperty', 'IntProperty', 'BoolProperty', 'Tick', 'CompleteQuest',
    'InteractedWithGatherer', 'PlayerPickedUpLog', 'QuestManager0', 'ActionCompleteQuest3', 'ShockPlayer0',
    'AwardAchievementsManager0', 'Timer', 'AQuestName', ...FUNCTIONS, 'Class', 'Quest', 'AwardAchievementsManager',
    'NameProperty', 'Package', 'Completed', 'Active', 'NumberOfObjectivesCompleted', 'NumberOfObjectivesToComplete',
    'HintName', 'FriendlyName', 'Description', 'DumpQuest', 'Function', 'NumGatherersHarvested',
    'NumGatherersInteracted', 'PlayerRespawned', 'SavedGatherer', 'Medical_FindRyan', 'Medical_GetKey', 'Core'];
  while (names.length < 1500) { names.push(`Filler${names.length}`); }
  const index = (text) => names.indexOf(text);
  names.forEach((text, i) => {
    const entry = ENTRY_BASE + i * 0x100;
    writeU32(entry, i);
    writeWide(entry + NAME_TEXT, text);
    writeU32(NAMES_DATA + 4 * i, entry);
  });
  writeU32(NAMES_ARRAY, NAMES_DATA);
  writeU32(NAMES_ARRAY + 4, names.length);
  writeU32(NAMES_ARRAY + 8, 2048);
  // A decoy triple before it that fails the "None" test.
  writeU32(BASE + 0x180010, 0x12340000);
  writeU32(BASE + 0x180014, 5000);
  writeU32(BASE + 0x180018, 6000);

  if (withNatives) {
    setByte(BASE + 0x1000FF, 0);
    writeAnsi(BASE + 0x100100, 'intUObjectexecVirtualFunction');
    writeAnsi(BASE + 0x100200, 'intAActorexecVirtualFunctionality'); // a longer name that must not match
    writeU32(BASE + 0x100800, BASE + 0x100100);
    writeU32(BASE + 0x100804, NATIVE_FUNCTION);
  }
  if (gnatives !== null) {
    // The table of natives by opcode: the first 0xD0 in use, the rest execUndefined.
    for (let opcode = 0; opcode < 4096; opcode++) {
      const value = opcode === gnatives.virtualAt ? NATIVE_FUNCTION : opcode < 0xD0 ? nativeAt(opcode) : UNDEFINED_NATIVE;
      writeU32(GNATIVES + 4 * opcode, value);
    }
  }
  if (gobjects) {
    // The object table: name at +0x20, class at +0x24. Fields: next at +0x30; a class's first field at +0x40;
    // a property's offset at +0x48 and a bool's mask at +0x4C.
    const TABLE = BASE + 0x185000;
    const DATA = 0x12000000;
    let nextAt = 0x13000000;
    let slot = 0;
    const make = (name, cls) => {
      const at = nextAt;
      nextAt += 0x100;
      writeU32(at, 0x00500000);
      writeU32(at + 0x20, index(name));
      writeU32(at + 0x24, cls === 'self' ? at : cls);
      writeU32(DATA + 4 * slot++, at);
      return at;
    };
    const classClass = make('Class', 'self');
    const cls = (name) => make(name, classClass);
    const intProperty = cls('IntProperty');
    const boolProperty = cls('BoolProperty');
    const nameProperty = cls('NameProperty');
    const functionClass = cls('Function');
    const packageClass = cls('Package');
    for (let i = 0; i < 40; i++) { make('Core', packageClass); }
    const fields = (owner, list) => {
      let previous = null;
      for (const [name, kind, offset, mask] of list) {
        const field = make(name, kind);
        writeU32(field + 0x48, offset);
        if (mask) { writeU32(field + 0x4C, mask); }
        if (previous === null) { writeU32(owner + 0x40, field); } else { writeU32(previous + 0x30, field); }
        previous = field;
      }
    };
    const quest = cls('Quest');
    fields(quest, [['DumpQuest', functionClass, 0], ['Active', boolProperty, 0x60, 2], ['Completed', boolProperty, 0x60, 1],
      ['NumberOfObjectivesCompleted', intProperty, 0x5C], ['NumberOfObjectivesToComplete', intProperty, 0x58],
      ['Description', nameProperty, 0x54], ['FriendlyName', nameProperty, 0x50], ['HintName', nameProperty, 0x4C]]);
    const manager = cls('AwardAchievementsManager');
    fields(manager, [['SavedGatherer', functionClass, 0], ['PlayerRespawned', boolProperty, 0x48, 4],
      ['NumGatherersInteracted', intProperty, 0x44], ['NumGatherersHarvested', intProperty, 0x40]]);
    const q1 = make('Medical_FindRyan', quest);
    writeU32(q1 + 0x60, 3); writeU32(q1 + 0x5C, 2); writeU32(q1 + 0x58, 2);
    const q2 = make('Medical_GetKey', quest);
    writeU32(q2 + 0x60, 2); writeU32(q2 + 0x5C, 0); writeU32(q2 + 0x58, 1);
    const m = make('AwardAchievementsManager0', manager);
    writeU32(m + 0x40, 1); writeU32(m + 0x44, 3);
    writeU32(TABLE, DATA);
    writeU32(TABLE + 4, 6000);
    writeU32(TABLE + 8, 8192);
    page(DATA + 4 * 6000, true);
  }
  // Code and data pages exist in full, as in the real module.
  for (const range of RANGES) {
    for (let a = range.base; a < range.base + range.size; a += 0x1000) { page(a, true); }
  }

  let nextObject = 0x20000000;
  const object = (name) => {
    const at = nextObject;
    nextObject += 0x1000;
    writeU32(at, 0x00500000); // a vtable
    writeU32(at + 4, at >>> 8); // its index, a number that is not a name
    writeU32(at + OBJECT_NAME, index(name));
    return new Ptr(at);
  };
  let nextFrame = 0x30000000;
  const frame = (caller, functionName) => {
    const at = nextFrame;
    nextFrame += 0x1000;
    const code = at + 0x800;
    writeU32(at, 0x00510000);
    writeU32(at + 4, 0x00520000); // Node: a function object
    writeU32(at + FRAME_OBJECT, caller.value);
    writeU32(at + FRAME_CODE, code);
    writeU32(at + 0x10, 0); // Locals
    writeU32(code, index(functionName));
    setByte(code + 4, 0x21); writeU32(code + 5, index('AQuestName')); // a name constant as the argument
    return new Ptr(at);
  };

  const constantFrame = (caller, value) => {
    const at = frame(caller, 'None');
    writeU32(readU32(at.value + FRAME_CODE), value);
    return at;
  };
  const runtime = {
    logs: [], hooks: [], timers: [], compiled: null, ptr, object, frame, constantFrame, index, writeU32, time: 1000,
  };

  const context = {
    console: { log: (text) => runtime.logs.push(String(text)) },
    Date: { now: () => runtime.time },
    Map, Set, Math, JSON, Array, Uint8Array, Uint32Array, Float32Array, Error, parseInt, String, Number,
    globalThis: null,
    ptr,
    setTimeout: (fn) => runtime.timers.push(fn),
    Process: {
      getModuleByName: (name) => {
        if (name === 'kernel32.dll') { return { getExportByName: () => ptr(0x76000000) }; }
        return {
          base: ptr(BASE), size: SIZE,
          enumerateRanges: (protection) => RANGES
            .filter((r) => protection === 'rw-' ? r.protection === 'rw-' : true)
            .map((r) => ({ base: ptr(r.base), size: r.size, protection: r.protection })),
        };
      },
      findRangeByAddress: (address) => {
        const r = RANGES.find((x) => address.value >= x.base && address.value < x.base + x.size);
        return r === undefined ? null : { base: ptr(r.base), size: r.size, protection: r.protection };
      },
    },
    Memory: {
      alloc: (size) => { const p = ptr(nextObject); nextObject += Math.max(0x1000, size); page(p.value, true); return p; },
      scanSync: (address, size, pattern) => {
        const wanted = pattern.split(' ').map((b) => parseInt(b, 16));
        const found = [];
        const bytes = new Uint8Array(address.readByteArray(size));
        outer: for (let i = 0; i + wanted.length <= bytes.length; i++) {
          for (let j = 0; j < wanted.length; j++) { if (bytes[i + j] !== wanted[j]) { continue outer; } }
          found.push({ address: address.add(i), size: wanted.length });
        }
        return found;
      },
    },
    NativeFunction: function (address) {
      // VirtualQuery: committed and read-write wherever the fake memory has a page.
      return (pointer, buffer) => {
        const committed = page(pointer.value, false) !== undefined;
        writeU32(buffer.value + 16, committed ? 0x1000 : 0x10000);
        writeU32(buffer.value + 20, committed ? 0x04 : 0x01);
        return 28;
      };
    },
    NativeCallback: function (fn) { return fn; },
    CModule: function (text, symbols) {
      if (!cmodule) { throw new Error('compilation failed'); }
      assert.ok(text.includes('onEnter'));
      runtime.compiled = { text, symbols };
      return { onEnter: 'native' };
    },
    Interceptor: {
      attach: (target, callbacks) => {
        const hook = { target, callbacks, detached: false };
        runtime.hooks.push(hook);
        return { detach: () => { hook.detached = true; } };
      },
    },
  };
  context.globalThis = context;

  // One script call: through the JS listener, or what the C filter does.
  runtime.call = (self, frameAt, target = NATIVE_FUNCTION) => {
    for (const hook of runtime.hooks) {
      if (hook.detached || hook.target.value !== target) { continue; }
      if (typeof hook.callbacks.onEnter === 'function') {
        hook.callbacks.onEnter.call({ context: { ecx: self } }, [frameAt, ptr(0)]);
      } else {
        const { settings, watched, on_watched: onWatched, on_sample: onSample } = runtime.compiled.symbols;
        if (!settings.add(12).readS32()) { continue; }
        const code = frameAt.add(settings.readS32()).readPointer();
        const nameIndex = code.readS32();
        let hit = false;
        for (let i = 0; i < settings.add(4).readS32(); i++) {
          if (watched.add(4 * i).readS32() === nameIndex) { onWatched(self, frameAt, nameIndex); hit = true; break; }
        }
        if (!hit) {
          runtime.ticks = (runtime.ticks || 0) + 1;
          if (runtime.ticks % settings.add(8).readS32() === 0) { onSample(nameIndex); }
        }
      }
    }
  };
  runtime.flush = (ms = 0) => {
    runtime.time += ms;
    const timers = runtime.timers.splice(0);
    timers.forEach((fn) => fn());
  };
  runtime.run = () => vm.runInNewContext(source, context, { filename: probePath });
  runtime.context = context;
  return runtime;
}

const tests = [];
function test(name, fn) { tests.push({ name, fn }); }

function startedProbe(options) {
  const rt = makeRuntime(options);
  rt.run();
  return rt;
}

function playCalls(rt, count) {
  const player = rt.object('ShockPlayer0');
  for (let i = 0; i < count; i++) {
    rt.call(player, rt.frame(player, CALLED[i % CALLED.length]));
  }
}

test('finds the name table and the native by name', () => {
  const rt = startedProbe();
  const text = rt.logs.join('\n');
  assert.match(text, /name table at module\+0x180100: 1500 names, text at entry\+0xc, UTF-16/);
  assert.match(text, /"intUObjectexecVirtualFunction" at module\+0x100100, named at module\+0x100800, function after it: module\+0x1234/);
  assert.ok(!/Functionality/.test(text));
  assert.strictEqual(rt.hooks.length, 1, 'only the sampling listener so far');
});

test('learns the layout from sampled calls, then hands over to the native filter', () => {
  const rt = startedProbe();
  playCalls(rt, 400);
  rt.flush();
  const text = rt.logs.join('\n');
  assert.match(text, /named native at module\+0x100800 \(module\+0x1234\): 400 calls, bytecode at frame\+0xc \(400\), 20 different names/);
  assert.match(text, /execVirtualFunction is module\+0x1234: bytecode at frame\+0xc, object name at object\+0x20, calling object at frame\+0x8/);
  assert.ok(rt.hooks[0].detached);
  assert.strictEqual(rt.hooks.length, 2);
  assert.strictEqual(typeof rt.hooks[1].callbacks.onEnter, 'string', 'the filter is native code');
  const status = rt.context.status();
  assert.strictEqual(status.codeOffset, '0xc');
  assert.strictEqual(status.objectNameOffset, '0x20');
  assert.strictEqual(status.callerOffset, '0x8');
  assert.ok(status.watched.includes('CompleteQuest'));
  assert.match(text, /"FailQuest" is not in the name table/);
});

test('reads quests and the achievements manager straight from the object table', () => {
  const rt = startedProbe();
  const text = rt.logs.join('\n');
  assert.match(text, /object table at module\+0x185000: 6000 objects, name at object\+0x20, class at object\+0x24/);
  assert.match(text, /Quest: class found, 2 objects/);
  assert.match(text, /Quest: fields from class\+0x40, next at field\+0x30: DumpQuest, Active, Completed/);
  assert.match(text, /Quest: property offsets at property\+0x48/);
  assert.match(text, /Medical_FindRyan: completed true, active true, objectives 2\/2/);
  assert.match(text, /Medical_GetKey: completed false, active true, objectives 0\/1/);
  assert.match(text, /AwardAchievementsManager0: PlayerRespawned false, NumGatherersInteracted 3, NumGatherersHarvested 1/);
  rt.writeU32(0x13000000, 0); // no effect on what follows: quests() scans again
  assert.strictEqual(rt.context.quests(), '2 listed');
});

test('without an object table it says so and goes on to the script calls', () => {
  const rt = startedProbe({ gobjects: false });
  assert.match(rt.logs.join('\n'), /could not find the object table/);
  assert.strictEqual(rt.context.quests(), 'no object table');
  assert.strictEqual(rt.hooks.length, 1);
});

test('waits until enough calls were sampled', () => {
  const rt = startedProbe();
  playCalls(rt, 100);
  rt.flush();
  assert.strictEqual(rt.hooks.length, 1);
  assert.strictEqual(rt.timers.length, 1, 'looks again later');
});

test('a watched call prints its name, object, caller and the bytecode after it', () => {
  const rt = startedProbe();
  playCalls(rt, 400);
  rt.flush();
  rt.logs.length = 0;
  const manager = rt.object('QuestManager0');
  const action = rt.object('ActionCompleteQuest3');
  rt.call(manager, rt.frame(action, 'CompleteQuest'));
  playCalls(rt, 640);
  assert.strictEqual(rt.logs.length, 1, rt.logs.join('\n'));
  assert.match(rt.logs[0], /^\[probe\] CompleteQuest on QuestManager0, called from ActionCompleteQuest3; bytecode after the name: 21 0d 00 00 00/);
  // Every 64th of the other calls is counted, so the total is right and the split is only a sample.
  const top = rt.context.top(50);
  const total = top.map((line) => Number(line.split(': ')[1])).reduce((a, b) => a + b, 0);
  assert.strictEqual(total, 640);
  assert.ok(top.every((line) => CALLED.includes(line.split(': ')[0])), top.join(', '));
});

test('watch() adds a name, and stop() takes the hook out', () => {
  const rt = startedProbe();
  playCalls(rt, 400);
  rt.flush();
  assert.strictEqual(rt.context.watch('Tick'), 'watching Tick');
  assert.strictEqual(rt.context.watch('NoSuchFunction'), 'NoSuchFunction not found');
  rt.logs.length = 0;
  playCalls(rt, CALLED.length);
  assert.strictEqual(rt.logs.filter((l) => l.includes('Tick on ShockPlayer0')).length, 1);
  assert.strictEqual(rt.context.stop(), 'hook removed');
  assert.ok(rt.hooks.every((h) => h.detached));
  rt.logs.length = 0;
  playCalls(rt, 4);
  assert.strictEqual(rt.logs.length, 0);
});

test('without either table of natives it watches no calls, and quests() still works', () => {
  const rt = startedProbe({ withNatives: false });
  assert.match(rt.logs.join('\n'), /could not find the table of natives by opcode either/);
  assert.strictEqual(rt.hooks.length, 0);
  assert.strictEqual(rt.context.quests(), '2 listed');
});

test('without natives by name, it takes execVirtualFunction from the table by opcode', () => {
  const rt = startedProbe({ withNatives: false, gnatives: { virtualAt: 0x1B } });
  playCalls(rt, 400);
  rt.flush();
  const text = rt.logs.join('\n');
  assert.match(text, /natives by opcode at module\+0x181000: 208 in use, the rest module\+0x1f00/);
  assert.match(text, /GNatives\[0x1b\], execVirtualFunction in the stock engine: module\+0x1234/);
  assert.match(text, /execVirtualFunction is module\+0x1234: bytecode at frame\+0xc/);
  assert.strictEqual(typeof rt.hooks[rt.hooks.length - 1].callbacks.onEnter, 'string');
});

test('when opcode 0x1b is something else, it tries the others and picks the one that names functions', () => {
  const rt = startedProbe({ withNatives: false, gnatives: { virtualAt: 0x1C } });
  const player = rt.object('ShockPlayer0');
  // GNatives[0x1b] reads a small constant from the bytecode, which looks like a name index too.
  for (let i = 0; i < 400; i++) { rt.call(player, rt.constantFrame(player, 1 + (i % 3)), nativeAt(0x1B)); }
  rt.flush(16000);
  let text = rt.logs.join('\n');
  assert.match(text, /GNatives\[0x1b\] \(module\+0x21b0\): 400 calls, .*3 different names.* - not it/);
  assert.match(text, /trying \d+ more natives at once/);
  for (let i = 0; i < 400; i++) { rt.call(player, rt.constantFrame(player, 1 + (i % 3)), nativeAt(0x1D)); }
  playCalls(rt, 400);
  rt.flush(16000);
  text = rt.logs.join('\n');
  assert.match(text, /execVirtualFunction is module\+0x1234/);
  assert.ok(rt.hooks.filter((h) => !h.detached).length === 1, 'only the filter is left');
});

test('use() hooks a native by opcode when asked', () => {
  const rt = startedProbe({ withNatives: false, gnatives: { virtualAt: 0x1C } });
  rt.flush(16000); // nothing called: 0x1b fails, and so does the broad round
  rt.flush(16000);
  assert.match(rt.logs.join('\n'), /could not tell which native/);
  assert.strictEqual(rt.context.use(0x1C), 'sampling');
  playCalls(rt, 400);
  rt.flush();
  assert.match(rt.logs.join('\n'), /execVirtualFunction is module\+0x1234/);
});

test('a filter that does not compile leaves no hook behind', () => {
  const rt = startedProbe({ cmodule: false });
  playCalls(rt, 400);
  rt.flush();
  assert.match(rt.logs.join('\n'), /could not compile the native filter/);
  assert.ok(rt.hooks.every((h) => h.detached));
});

let failed = 0;
for (const { name, fn } of tests) {
  try {
    fn();
    console.log(`ok   ${name}`);
  } catch (e) {
    failed++;
    console.log(`FAIL ${name}\n${e.stack}`);
  }
}
console.log(`${tests.length - failed} of ${tests.length} passed`);
process.exit(failed ? 1 : 0);

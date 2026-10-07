/*
 * Probe: watch the game's UnrealScript calls by name, to find the hooks for check detection.
 *
 * The functions that matter (QuestManager.CompleteQuest, AwardAchievementsManager.InteractedWithGatherer,
 * PlayerPickedUpLog, WeaponUpgraded, GameFinished, ...) are script functions called from other script, never console
 * commands. Script calls a non-final function through one native, UObject::execVirtualFunction, which reads the
 * function's name (an FName index) straight from the bytecode. So one hook there, plus the engine's name table,
 * names every script-to-script call the game makes.
 *
 * Run it on its own (close the BioShock Client first), standing in a level:
 *
 *   frida -n BioshockHD.exe -l probe_script_calls.js -o probe_log.txt
 *
 * It does everything read-only until the hook goes in, and says at each step what it found. Then play: rescue or
 * harvest a Little Sister, pick up an audio diary, finish an objective, use a Power to the People station. Each
 * watched call prints one line. At the prompt:
 *
 *   status()          what was found, and how many calls have been seen
 *   watch('Name')     also print calls of another function name
 *   top(20)           the 20 most frequent function names so far (to see what the hook can see at all)
 *   stop()            take the hook out
 */
'use strict';

const GAME = 'BioshockHD.exe';
const PROBE_VERSION = '2026-10-07.1';
const mod = Process.getModuleByName(GAME);
const base = mod.base;

const WATCHED = [
  // story objectives
  'InitiateQuest', 'CompleteQuest', 'OnCompletedQuestObjective', 'OnUnCompletedQuestObjective', 'FailQuest',
  'ReplaceQuest',
  // Little Sisters
  'SavedGatherer', 'CollectedGatherer', 'InteractedWithGatherer', 'PacifiedGatherer', 'EventGatherers',
  // audio diaries
  'PlayerPickedUpLog', 'PickedUpUnplayedLog', 'EventLog',
  // Power to the People
  'WeaponUpgraded', 'EventWeaponFullyUpgraded',
  // goal, achievements, alarms
  'GameFinished', 'AwardAchievement', 'EventAwardAchievement', 'PlayerTriggeredAlarm',
];

function log(message) {
  console.log(`[probe] ${message}`);
}

function hex(value) {
  return `0x${(value >>> 0).toString(16)}`;
}

function rel(pointer) {
  const offset = pointer.sub(base).toUInt32();
  return offset < mod.size ? `module+${hex(offset)}` : `${pointer}`;
}

log(`probe ${PROBE_VERSION}, ${GAME} at ${base}, size ${mod.size}`);

// ---------------------------------------------------------------------------------------------------------------
// Careful reads: only committed, unguarded memory is touched
// ---------------------------------------------------------------------------------------------------------------

const QUERY_SIZE = 28;
const virtualQuery = new NativeFunction(Process.getModuleByName('kernel32.dll').getExportByName('VirtualQuery'),
  'uint', ['pointer', 'pointer', 'uint'], 'stdcall');
const queryBuffer = Memory.alloc(QUERY_SIZE);
const pageVerdicts = new Map();

function readablePage(value) {
  const page = value >>> 12;
  let verdict = pageVerdicts.get(page);
  if (verdict === undefined) {
    verdict = false;
    if (virtualQuery(ptr(value), queryBuffer, QUERY_SIZE) !== 0) {
      const state = queryBuffer.add(16).readU32();
      const protect = queryBuffer.add(20).readU32();
      const access = protect & 0xFF;
      verdict = state === 0x1000 && (protect & 0x100) === 0 && access !== 0 && access !== 0x01 && access !== 0x10;
    }
    if (pageVerdicts.size > 16384) {
      pageVerdicts.clear();
    }
    pageVerdicts.set(page, verdict);
  }
  return verdict;
}

function readable(pointer, length) {
  if (pointer === null) {
    return false;
  }
  const value = pointer.toUInt32();
  if (value < 0x10000) {
    return false;
  }
  return readablePage(value) && readablePage(value + (length || 4) - 1);
}

function u32(pointer) {
  return readable(pointer, 4) ? pointer.readU32() : null;
}

function pointerAt(pointer) {
  const value = u32(pointer);
  return value === null ? null : ptr(value);
}

// ---------------------------------------------------------------------------------------------------------------
// The name table (GNames): a TArray {data, count, max} of FNameEntry pointers in the module's data, entry 0 "None"
// ---------------------------------------------------------------------------------------------------------------

const IDENTIFIER = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;

function entryText(entry, offset, wide) {
  const at = entry.add(offset);
  if (!readable(at, 16)) {
    return null;
  }
  try {
    return wide ? at.readUtf16String(64) : at.readCString(64);
  } catch (e) {
    return null;
  }
}

function findNames() {
  for (const range of mod.enumerateRanges('rw-')) {
    let words;
    try {
      words = new Uint32Array(range.base.readByteArray(range.size & ~3));
    } catch (e) {
      log(`skipped ${rel(range.base)} (${range.size} bytes): ${e.message}`);
      continue;
    }
    for (let i = 0; i + 2 < words.length; i++) {
      const count = words[i + 1];
      const max = words[i + 2];
      if (count < 1000 || count > 2000000 || max < count || max > 4000000) {
        continue;
      }
      const data = ptr(words[i]);
      const first = pointerAt(data);
      const second = pointerAt(data.add(4));
      if (first === null || second === null) {
        continue;
      }
      for (const wide of [true, false]) {
        for (let offset = 0; offset <= 0x14; offset += 4) {
          if (entryText(first, offset, wide) === 'None' && entryText(second, offset, wide) === 'ByteProperty') {
            return { array: range.base.add(i * 4), data, count, offset, wide };
          }
        }
      }
    }
  }
  return null;
}

const names = findNames();
if (names === null) {
  log('could not find the name table: no {data, count, max} in the module whose first entries are "None", ' +
      '"ByteProperty". Nothing else can be named, so the probe stops here.');
  throw new Error('no name table');
}
log(`name table at ${rel(names.array)}: ${names.count} names, text at entry+${hex(names.offset)}, ` +
    `${names.wide ? 'UTF-16' : 'ANSI'}`);

const nameCache = new Map();

function nameCount() {
  const count = u32(names.array.add(4));
  return count === null ? names.count : count;
}

function nameOf(index) {
  if (index === null || index < 0 || index >= nameCount()) {
    return null;
  }
  let text = nameCache.get(index);
  if (text === undefined) {
    const data = pointerAt(names.array);
    const entry = data === null ? null : pointerAt(data.add(index * 4));
    text = entry === null || entry.isNull() ? null : entryText(entry, names.offset, names.wide);
    if (text !== null && !IDENTIFIER.test(text)) {
      text = null;
    }
    nameCache.set(index, text);
  }
  return text;
}

let nameIndex = null; // lower-case text -> index, built once

function indexOfName(wanted) {
  if (nameIndex === null) {
    nameIndex = new Map();
    const total = nameCount();
    for (let index = 0; index < total; index++) {
      const text = nameOf(index);
      if (text !== null && !nameIndex.has(text.toLowerCase())) {
        nameIndex.set(text.toLowerCase(), index);
      }
    }
    nameCache.clear();
  }
  const index = nameIndex.get(wanted.toLowerCase());
  return index === undefined ? -1 : index;
}

// ---------------------------------------------------------------------------------------------------------------
// execVirtualFunction, from the table of natives by name ("intUObjectexecVirtualFunction" -> function)
// ---------------------------------------------------------------------------------------------------------------

function findNative(nativeName) {
  const pattern = Array.from(nativeName, (c) => c.charCodeAt(0).toString(16).padStart(2, '0')).join(' ') + ' 00';
  const found = [];
  for (const range of mod.enumerateRanges('r--')) {
    for (const match of Memory.scanSync(range.base, range.size, pattern)) {
      const previous = match.address.sub(1);
      if (readable(previous, 1) && previous.readU8() !== 0) {
        continue; // the end of a longer name
      }
      const value = match.address.toUInt32();
      const bytes = [value & 0xFF, (value >>> 8) & 0xFF, (value >>> 16) & 0xFF, value >>> 24];
      const reference = bytes.map((b) => b.toString(16).padStart(2, '0')).join(' ');
      for (const dataRange of mod.enumerateRanges('r--')) {
        for (const ref of Memory.scanSync(dataRange.base, dataRange.size, reference)) {
          for (const offset of [4, -4]) {
            const target = pointerAt(ref.address.add(offset));
            const range2 = target === null ? null : Process.findRangeByAddress(target);
            if (range2 !== null && range2.protection.indexOf('x') !== -1 && rel(target).startsWith('module')) {
              found.push({ name: nativeName, string: match.address, entry: ref.address, offset, target });
            }
          }
        }
      }
    }
  }
  return found;
}

const natives = findNative('intUObjectexecVirtualFunction');
for (const native of natives) {
  log(`"${native.name}" at ${rel(native.string)}, named at ${rel(native.entry)}, ` +
      `function ${native.offset > 0 ? 'after' : 'before'} it: ${rel(native.target)}`);
}
if (natives.length === 0) {
  log('could not find "intUObjectexecVirtualFunction" in a table of natives. Please send the log as it is.');
  throw new Error('no execVirtualFunction');
}
const preferred = natives.filter((native) => native.offset === 4);
const virtualFunction = (preferred.length > 0 ? preferred : natives)[0].target;
if (new Set(natives.map((native) => native.target.toString())).size > 1) {
  log(`more than one candidate; using ${rel(virtualFunction)}`);
}

// ---------------------------------------------------------------------------------------------------------------
// Calibration, in JavaScript and read-only: where in the FFrame the bytecode pointer is, and where an object keeps
// its own name. A short listener samples calls, then makes way for the native filter below.
// ---------------------------------------------------------------------------------------------------------------

const FRAME_OFFSETS = [];
for (let offset = 4; offset <= 0x24; offset += 4) {
  FRAME_OFFSETS.push(offset);
}
const OBJECT_OFFSETS = [];
for (let offset = 0x8; offset <= 0x40; offset += 4) {
  OBJECT_OFFSETS.push(offset);
}

function calibrate(samples) {
  const frameVotes = new Map(FRAME_OFFSETS.map((offset) => [offset, 0]));
  const objectVotes = new Map(OBJECT_OFFSETS.map((offset) => [offset, 0]));
  for (const { self, frame } of samples) {
    for (const offset of FRAME_OFFSETS) {
      const code = pointerAt(frame.add(offset));
      const index = code === null ? null : u32(code);
      if (index !== null && index !== 0 && nameOf(index) !== null) { // 0, "None", is what zeroed memory reads as
        frameVotes.set(offset, frameVotes.get(offset) + 1);
      }
    }
    for (const offset of OBJECT_OFFSETS) {
      const text = nameOf(u32(self.add(offset)));
      if (text !== null && /\d$/.test(text)) { // an object's own name ends in its number: "QuestManager0"
        objectVotes.set(offset, objectVotes.get(offset) + 1);
      }
    }
  }
  const best = (votes) => [...votes.entries()].sort((a, b) => b[1] - a[1])[0];
  const object = best(objectVotes);
  // The object running the calling code, also in the frame: a pointer to something with an object's name.
  const callerVotes = new Map(FRAME_OFFSETS.map((offset) => [offset, 0]));
  for (const { frame } of samples) {
    for (const offset of FRAME_OFFSETS) {
      const caller = pointerAt(frame.add(offset));
      const text = caller === null ? null : nameOf(u32(caller.add(object[0])));
      if (text !== null && /\d$/.test(text)) {
        callerVotes.set(offset, callerVotes.get(offset) + 1);
      }
    }
  }
  return { frame: best(frameVotes), object, caller: best(callerVotes), total: samples.length };
}

let codeOffset = null;
let objectNameOffset = null;
let callerOffset = null;
const SAMPLE_COUNT = 400;
const samples = [];
let sampler = null;

function startSampling() {
  sampler = Interceptor.attach(virtualFunction, {
    onEnter(args) {
      if (samples.length < SAMPLE_COUNT) {
        samples.push({ self: this.context.ecx, frame: args[0] });
      }
    },
  });
  log(`sampling script calls at ${rel(virtualFunction)} to learn the layout (stand in a level, unpaused)`);
  waitForSamples();
}

function waitForSamples() {
  if (samples.length < SAMPLE_COUNT) {
    setTimeout(waitForSamples, 200);
    return;
  }
  sampler.detach();
  sampler = null;
  const result = calibrate(samples);
  log(`layout votes from ${result.total} calls: bytecode at frame+${hex(result.frame[0])} (${result.frame[1]}), ` +
      `object name at object+${hex(result.object[0])} (${result.object[1]})`);
  if (result.frame[1] < result.total * 0.9) {
    log('the bytecode pointer is not clear enough to filter on safely; stopping here. Please send the log.');
    return;
  }
  codeOffset = result.frame[0];
  objectNameOffset = result.object[1] >= result.total * 0.5 ? result.object[0] : null;
  callerOffset = objectNameOffset !== null && result.caller[1] >= result.total * 0.5 ? result.caller[0] : null;
  log(`calling object at frame+${hex(result.caller[0])} (${result.caller[1]})` +
      (callerOffset === null ? ', too unclear to use' : ''));
  installFilter();
}

// ---------------------------------------------------------------------------------------------------------------
// The filter: native code on every call, JavaScript only for a watched name (and a sampled count of the rest)
// ---------------------------------------------------------------------------------------------------------------

const MAX_WATCHED = 64;
const watchedTable = Memory.alloc(4 * (MAX_WATCHED + 1));
const settings = Memory.alloc(16); // [code offset, watched count, sample every n, enabled]
const watchedNames = new Map(); // index -> name
const counts = new Map(); // name index -> sampled count
let seen = 0;
let filter = null;
let listener = null;

function objectName(object) {
  if (objectNameOffset === null || object === null) {
    return '?';
  }
  return nameOf(u32(object.add(objectNameOffset))) || '?';
}

function nextBytes(code, length) {
  if (!readable(code, length)) {
    return '';
  }
  return Array.from(new Uint8Array(code.readByteArray(length)), (b) => b.toString(16).padStart(2, '0')).join(' ');
}

const onWatched = new NativeCallback((self, frame, index) => {
  const caller = callerOffset === null ? null : pointerAt(frame.add(callerOffset));
  const code = pointerAt(frame.add(codeOffset));
  log(`${nameOf(index)} on ${objectName(self)}, called from ${objectName(caller)}; ` +
      `bytecode after the name: ${code === null ? '?' : nextBytes(code.add(4), 24)}`);
}, 'void', ['pointer', 'pointer', 'int']);

const onSample = new NativeCallback((index) => {
  seen++;
  counts.set(index, (counts.get(index) || 0) + 1);
}, 'void', ['int']);

const FILTER_SOURCE = `
#include <gum/guminterceptor.h>

extern int settings[4];
extern int watched[${MAX_WATCHED + 1}];
extern void on_watched (void * self, void * frame, int index);
extern void on_sample (int index);

static unsigned int ticks;

void
onEnter (GumInvocationContext * ic)
{
  if (!settings[3])
    return;
  unsigned char * frame = (unsigned char *) gum_invocation_context_get_nth_argument (ic, 0);
  if (frame == NULL)
    return;
  unsigned char * code = *(unsigned char **) (frame + settings[0]);
  if (code == NULL)
    return;
  int index = *(int *) code;
  for (int i = 0; i < settings[1]; i++)
  {
    if (watched[i] == index)
    {
      on_watched ((void *) ic->cpu_context->ecx, frame, index);
      return;
    }
  }
  if (++ticks % (unsigned int) settings[2] == 0)
    on_sample (index);
}
`;

function writeWatched() {
  const indices = [...watchedNames.keys()].slice(0, MAX_WATCHED);
  indices.forEach((index, i) => watchedTable.add(i * 4).writeS32(index));
  settings.add(4).writeS32(indices.length);
}

function installFilter() {
  settings.writeS32(codeOffset);
  settings.add(8).writeS32(64);
  for (const wanted of WATCHED) {
    addWatched(wanted, false);
  }
  writeWatched();
  try {
    filter = new CModule(FILTER_SOURCE, {
      settings, watched: watchedTable, on_watched: onWatched, on_sample: onSample,
    });
  } catch (e) {
    log(`could not compile the native filter (${e.message}); stopping here. Please send the log.`);
    return;
  }
  settings.add(12).writeS32(1);
  listener = Interceptor.attach(virtualFunction, filter);
  log(`watching ${watchedNames.size} names. Play, and each watched call prints a line. ` +
      'status(), watch("Name"), top(20), stop() at the prompt');
}

function addWatched(wanted, update) {
  const index = indexOfName(wanted);
  if (index < 0) {
    log(`"${wanted}" is not in the name table, so no script function by that name exists right now`);
    return false;
  }
  watchedNames.set(index, nameOf(index));
  if (update) {
    writeWatched();
  }
  return true;
}

// ---------------------------------------------------------------------------------------------------------------
// The prompt
// ---------------------------------------------------------------------------------------------------------------

globalThis.status = function () {
  return {
    probe: PROBE_VERSION,
    names: `${nameCount()} at ${rel(names.array)}`,
    virtualFunction: rel(virtualFunction),
    codeOffset: codeOffset === null ? null : hex(codeOffset),
    objectNameOffset: objectNameOffset === null ? null : hex(objectNameOffset),
    callerOffset: callerOffset === null ? null : hex(callerOffset),
    hooked: listener !== null,
    watched: [...watchedNames.values()],
    sampledCalls: seen * 64,
  };
};

globalThis.watch = function (name) {
  if (watchedNames.size >= MAX_WATCHED) {
    return 'already watching as many names as the filter holds';
  }
  return addWatched(name, true) ? `watching ${name}` : `${name} not found`;
};

globalThis.top = function (n) {
  return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, n || 20)
    .map(([index, count]) => `${nameOf(index) || index}: ${count * 64}`);
};

globalThis.stop = function () {
  settings.add(12).writeS32(0);
  if (listener !== null) {
    listener.detach();
    listener = null;
  }
  if (sampler !== null) {
    sampler.detach();
    sampler = null;
  }
  return 'hook removed';
};

startSampling();

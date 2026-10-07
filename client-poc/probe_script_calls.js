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
 *   use(0x1b)         if it could not decide which native is execVirtualFunction: hook GNatives[0x1b]
 *   quests()          the game's quests as they are now: name, completed, objectives done (read from memory)
 *   managers()        the counters AwardAchievementsManager keeps (sisters harvested and so on)
 */
'use strict';

const GAME = 'BioshockHD.exe';
const PROBE_VERSION = '2026-10-07.3';
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
// The object table (GObjects), read-only: a TArray of UObject pointers. Each object keeps its name (an FName index)
// and its class (a pointer to another object) at offsets learned here. From a class, its properties: the class's
// first child, each child's next, and each property's offset into an instance. Names of the declared properties
// (from the UE Explorer export) tell which pointers are which.
// ---------------------------------------------------------------------------------------------------------------

const DECLARED = {
  Quest: ['HintName', 'Text', 'FriendlyName', 'Description', 'ObjectiveDescription', 'CompletedDescription',
    'CompletedObjectiveDescription', 'LevelFriendlyName', 'CompleteMessage', 'ObjectiveMessage', 'ParentName',
    'MapUIRegion', 'ArrowActor', 'ArrowActorLevelLabel', 'ReleventLevelLabel', 'TimeToComplete', 'FailureTime',
    'NumberOfObjectivesToComplete', 'NumberOfObjectivesCompleted', 'CompleteWhenAllChildrenAreCompleted',
    'QuestHints', 'CurrentHintName', 'HintReminderTime', 'HasSeenCurrentHint', 'Completed', 'ADAMAward', 'Hidden',
    'Active', 'Parent', 'Children', 'ReplacedBy', 'ObjectiveIcon', 'DumpQuest'],
  AwardAchievementsManager: ['NumMachinesHacked', 'NumItemsCrafted', 'WasSecurityEverTriggered',
    'DidDamageUsingNonWrenchWeapon', 'AmmoCrafted', 'NumGatherersHarvested', 'NumGatherersInteracted',
    'NumTracksMaxed', 'PlayerOwner', 'DifficultyChanged', 'PlayerRespawned', 'JustCraftedAnItem',
    'NumGatherersInGame', 'MachinesHackedForAward', 'TotalLogsInGame', 'TotalPassivePlasmidsInGame',
    'ItemsCraftedForAward', 'CraftableAmmoTypes', 'SavedGatherer', 'CollectedGatherer', 'GameFinished',
    'AwardAchievement', 'PlayerPickedUpLog', 'WeaponUpgraded'],
};

const objects = { table: null, count: 0, nameOffset: null, classOffset: null, layout: null };

function objectAt(index) {
  const data = pointerAt(objects.table);
  const object = data === null ? null : pointerAt(data.add(index * 4));
  return object === null || object.isNull() ? null : object;
}

function nameOfObject(object) {
  return object === null ? null : nameOf(u32(object.add(objects.nameOffset)));
}

function classOf(object) {
  return object === null ? null : pointerAt(object.add(objects.classOffset));
}

function classNameOf(object) {
  return nameOfObject(classOf(object));
}

function findObjects() {
  for (const range of mod.enumerateRanges('rw-')) {
    let words;
    try {
      words = new Uint32Array(range.base.readByteArray(range.size & ~3));
    } catch (e) {
      continue;
    }
    for (let i = 0; i + 2 < words.length; i++) {
      const count = words[i + 1];
      const max = words[i + 2];
      const array = range.base.add(i * 4);
      if (count < 5000 || count > 2000000 || max < count || max > 4000000 || array.equals(names.array)) {
        continue;
      }
      const data = ptr(words[i]);
      const sampled = [];
      for (let k = 0; k < 400 && sampled.length < 64; k++) {
        const object = pointerAt(data.add(k * 4));
        if (object === null) {
          break;
        }
        if (!object.isNull() && readable(object, 0x60)) {
          sampled.push(object);
        }
      }
      if (sampled.length < 32) {
        continue;
      }
      for (let nameOffset = 0x8; nameOffset <= 0x40; nameOffset += 4) {
        const named = sampled.filter((o) => {
          const index = u32(o.add(nameOffset));
          return index !== 0 && nameOf(index) !== null;
        }).length;
        if (named < sampled.length * 0.9) {
          continue;
        }
        for (let classOffset = 0x8; classOffset <= 0x48; classOffset += 4) {
          if (classOffset === nameOffset) {
            continue;
          }
          const classed = sampled.filter((o) => {
            const cls = pointerAt(o.add(classOffset));
            const meta = cls === null ? null : pointerAt(cls.add(classOffset));
            return meta !== null && nameOf(u32(meta.add(nameOffset))) === 'Class';
          }).length;
          if (classed >= sampled.length * 0.9) {
            return { table: array, count, nameOffset, classOffset };
          }
        }
      }
    }
  }
  return null;
}

// Every object of the given class names, and the classes of those names, in one pass over the table.
function scanObjects(classNames) {
  const wanted = new Set(classNames);
  const found = { instances: new Map(), classes: new Map() };
  const total = u32(objects.table.add(4)) || objects.count;
  for (let index = 0; index < total; index++) {
    const object = objectAt(index);
    if (object === null) {
      continue;
    }
    const className = classNameOf(object);
    if (className === 'Class') {
      const name = nameOfObject(object);
      if (wanted.has(name)) {
        found.classes.set(name, object);
      }
    } else if (wanted.has(className)) {
      if (!found.instances.has(className)) {
        found.instances.set(className, []);
      }
      found.instances.get(className).push(object);
    }
  }
  return found;
}

// Children and Next: pointers that lead from the class to its declared fields and from field to field.
function findLayout(cls, declared) {
  const known = new Set(declared);
  for (let childrenOffset = 0x20; childrenOffset <= 0x100; childrenOffset += 4) {
    const first = pointerAt(cls.add(childrenOffset));
    if (first === null || !readable(first, 0x80) || !known.has(nameOfObject(first))) {
      continue;
    }
    for (let nextOffset = 0x20; nextOffset <= 0x60; nextOffset += 4) {
      const chain = [];
      let field = first;
      while (field !== null && !field.isNull() && chain.length < 300 && readable(field, 0x80)) {
        chain.push(field);
        field = pointerAt(field.add(nextOffset));
      }
      const hits = chain.filter((f) => known.has(nameOfObject(f))).length;
      if (hits >= Math.min(4, declared.length) && hits >= chain.length * 0.6) {
        return { childrenOffset, nextOffset, chain };
      }
    }
  }
  return null;
}

// The property's offset into an instance: the field offset, the same for every property, where the numbers are
// distinct (bools aside), plausible and in step with the declaration order.
function findPropertyOffset(chain) {
  const properties = chain.filter((f) => /Property$/.test(classNameOf(f) || ''));
  let best = null;
  for (let at = 0x24; at <= 0x80; at += 4) {
    const values = properties.map((p) => u32(p.add(at)));
    if (values.some((v) => v === null || v < 0x20 || v > 0x4000)) {
      continue;
    }
    const distinct = new Set(values).size;
    if (best === null || distinct > best.distinct) {
      best = { at, distinct, of: properties.length };
    }
  }
  return best;
}

function hexWords(object, from, to) {
  const out = [];
  for (let at = from; at < to; at += 4) {
    const value = u32(object.add(at));
    out.push(value === null ? '????????' : value.toString(16).padStart(8, '0'));
  }
  return out.join(' ');
}

const layouts = new Map(); // class name -> {properties: Map name -> {offset, kind, mask}}

function learnClass(className, cls) {
  const layout = findLayout(cls, DECLARED[className]);
  if (layout === null) {
    log(`${className}: could not find its fields. Its first 0x100 bytes: ${hexWords(cls, 0, 0x100)}`);
    return null;
  }
  const chainNames = layout.chain.map((f) => nameOfObject(f) || '?');
  log(`${className}: fields from class+${hex(layout.childrenOffset)}, next at field+${hex(layout.nextOffset)}: ` +
      chainNames.join(', '));
  if (objects.layout === null) {
    objects.layout = layout;
  }
  const offset = findPropertyOffset(layout.chain);
  for (const field of layout.chain.slice(0, 4)) {
    log(`  ${nameOfObject(field)} (${classNameOf(field)}): ${hexWords(field, 0x20, 0x70)}`);
  }
  if (offset === null) {
    log(`${className}: could not tell where a property keeps its offset`);
    return null;
  }
  log(`${className}: property offsets at property+${hex(offset.at)} (${offset.distinct} distinct of ${offset.of})`);
  const properties = new Map();
  for (const field of layout.chain) {
    const kind = classNameOf(field) || '';
    if (/Property$/.test(kind)) {
      // A bool keeps its bit mask right after the offset, more or less: the first power of two after it.
      let mask = null;
      if (kind === 'BoolProperty') {
        for (let at = offset.at + 4; at <= offset.at + 0x20 && mask === null; at += 4) {
          const value = u32(field.add(at));
          if (value !== null && value !== 0 && (value & (value - 1)) === 0) {
            mask = value;
          }
        }
      }
      properties.set(nameOfObject(field), { offset: u32(field.add(offset.at)), kind, mask });
    }
  }
  layouts.set(className, properties);
  return properties;
}

function readProperty(object, property) {
  if (property === undefined || property.offset === null) {
    return '?';
  }
  const at = object.add(property.offset);
  const value = u32(at);
  if (value === null) {
    return '?';
  }
  if (property.kind === 'BoolProperty') {
    return property.mask === null ? `0x${value.toString(16)}` : String((value & property.mask) !== 0);
  }
  if (property.kind === 'NameProperty') {
    return nameOf(value) || `#${value}`;
  }
  if (property.kind === 'FloatProperty') {
    return at.readFloat().toFixed(1);
  }
  return String(value | 0);
}

let found = null;

function describeQuests(limit) {
  const quests = found === null ? [] : (found.instances.get('Quest') || []);
  const properties = layouts.get('Quest');
  const lines = [`${quests.length} quests`];
  for (const quest of quests.slice(0, limit)) {
    if (properties === undefined) {
      lines.push(`  ${nameOfObject(quest)}`);
      continue;
    }
    lines.push(`  ${nameOfObject(quest)}: completed ${readProperty(quest, properties.get('Completed'))}, ` +
      `active ${readProperty(quest, properties.get('Active'))}, objectives ` +
      `${readProperty(quest, properties.get('NumberOfObjectivesCompleted'))}/` +
      `${readProperty(quest, properties.get('NumberOfObjectivesToComplete'))}`);
  }
  return lines;
}

function describeManagers() {
  const managers = found === null ? [] : (found.instances.get('AwardAchievementsManager') || []);
  const properties = layouts.get('AwardAchievementsManager');
  const lines = [`${managers.length} AwardAchievementsManager`];
  for (const manager of managers) {
    const values = properties === undefined ? 'fields unknown'
      : [...properties.entries()].filter(([, p]) => p.kind !== 'ArrayProperty' && p.kind !== 'ObjectProperty')
        .map(([name, p]) => `${name} ${readProperty(manager, p)}`).join(', ');
    lines.push(`  ${nameOfObject(manager)}: ${values}`);
  }
  return lines;
}

function rescan() {
  found = scanObjects(Object.keys(DECLARED));
  for (const className of Object.keys(DECLARED)) {
    const count = (found.instances.get(className) || []).length;
    log(`${className}: class ${found.classes.has(className) ? 'found' : 'not found'}, ${count} objects`);
  }
}

const objectTable = findObjects();
if (objectTable === null) {
  log('could not find the object table; quests() and managers() are not available');
} else {
  Object.assign(objects, objectTable);
  log(`object table at ${rel(objects.table)}: ${objects.count} objects, name at object+${hex(objects.nameOffset)}, ` +
      `class at object+${hex(objects.classOffset)}`);
  rescan();
  for (const className of Object.keys(DECLARED)) {
    if (found.classes.has(className)) {
      learnClass(className, found.classes.get(className));
    }
  }
  for (const line of describeQuests(40)) {
    log(line);
  }
  for (const line of describeManagers()) {
    log(line);
  }
}

globalThis.quests = function (limit) {
  if (objects.table === null) {
    return 'no object table';
  }
  rescan();
  const lines = describeQuests(limit || 200);
  lines.forEach((line) => log(line));
  return `${lines.length - 1} listed`;
};

globalThis.managers = function () {
  if (objects.table === null) {
    return 'no object table';
  }
  rescan();
  const lines = describeManagers();
  lines.forEach((line) => log(line));
  return 'listed';
};

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

// The natives by opcode (GNatives): 4096 code pointers in the module's data. Most opcodes are unused and all point at
// the same function (execUndefined), which also fills the end of the table, so the table ends where that run does.
const NATIVE_SLOTS = 4096;
const EX_VIRTUAL_FUNCTION = 0x1B;

function findGNatives() {
  const code = mod.enumerateRanges('r-x').map((r) => [r.base.toUInt32(), r.base.toUInt32() + r.size]);
  const isCode = (value) => code.some(([from, to]) => value >= from && value < to);
  const found = [];
  for (const range of mod.enumerateRanges('rw-')) {
    let words;
    try {
      words = new Uint32Array(range.base.readByteArray(range.size & ~3));
    } catch (e) {
      continue;
    }
    let run = 0;
    for (let i = 1; i <= words.length; i++) {
      if (i < words.length && words[i] === words[i - 1] && isCode(words[i])) {
        run++;
        continue;
      }
      // words[i - 1] ends a run of run + 1 equal code pointers
      const end = i - 1;
      const start = end - (NATIVE_SLOTS - 1);
      if (run + 1 >= 256 && start >= 0) {
        const undefinedNative = words[end];
        let defined = 0;
        let pointers = 0;
        for (let k = start; k <= end; k++) {
          if (isCode(words[k])) {
            pointers++;
            if (words[k] !== undefinedNative) {
              defined++;
            }
          }
        }
        const head = Array.from(words.subarray(start, start + 0x40));
        if (pointers === NATIVE_SLOTS && defined >= 100 && head.filter((w) => w !== undefinedNative).length >= 0x30) {
          found.push({ table: range.base.add(start * 4), words: Array.from(words.subarray(start, end + 1)),
            undefinedNative, defined });
        }
      }
      run = 0;
    }
  }
  return found;
}

const candidates = []; // {label, target}, in order of preference
const natives = findNative('intUObjectexecVirtualFunction');
for (const native of natives) {
  log(`"${native.name}" at ${rel(native.string)}, named at ${rel(native.entry)}, ` +
      `function ${native.offset > 0 ? 'after' : 'before'} it: ${rel(native.target)}`);
  candidates.push({ label: `named native at ${rel(native.entry)}`, target: native.target });
}
let gnatives = null;
if (natives.length === 0) {
  log('no table of natives by name; looking for the table of natives by opcode instead');
  const tables = findGNatives();
  for (const table of tables) {
    log(`natives by opcode at ${rel(table.table)}: ${table.defined} in use, the rest ${rel(ptr(table.undefinedNative))}`);
  }
  if (tables.length === 0) {
    log('could not find the table of natives by opcode either, so no script calls are watched. ' +
        'quests() and managers() still work.');
  } else {
    gnatives = tables[0];
    const target = ptr(gnatives.words[EX_VIRTUAL_FUNCTION]);
    log(`GNatives[${hex(EX_VIRTUAL_FUNCTION)}], execVirtualFunction in the stock engine: ${rel(target)}`);
    candidates.push({ label: `GNatives[${hex(EX_VIRTUAL_FUNCTION)}]`, target });
  }
}
let virtualFunction = null;

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
  const frame = best(frameVotes);
  const seenNames = new Map();
  for (const sample of samples) {
    const code = pointerAt(sample.frame.add(frame[0]));
    const text = code === null ? null : nameOf(u32(code));
    if (text !== null) {
      seenNames.set(text, (seenNames.get(text) || 0) + 1);
    }
  }
  const common = [...seenNames.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6).map(([text]) => text);
  return { frame, object, caller: best(callerVotes), total: samples.length, distinct: seenNames.size, common };
}

let codeOffset = null;
let objectNameOffset = null;
let callerOffset = null;
const SAMPLE_COUNT = 400;
const SAMPLE_WAIT_MS = 15000;
const MIN_DISTINCT = 15; // a call per function name: many different names, unlike a native that reads a constant
let samplers = []; // {label, target, samples, listener}

function sample(list) {
  samplers = list.map(({ label, target }) => {
    const entry = { label, target, samples: [], listener: null };
    entry.listener = Interceptor.attach(target, {
      onEnter(args) {
        if (entry.samples.length < SAMPLE_COUNT) {
          entry.samples.push({ self: this.context.ecx, frame: args[0] });
        }
      },
    });
    return entry;
  });
}

function stopSampling() {
  for (const entry of samplers) {
    if (entry.listener !== null) {
      entry.listener.detach();
      entry.listener = null;
    }
  }
}

function judge(entry) {
  const result = calibrate(entry.samples);
  result.ok = result.total >= 100 && result.frame[1] >= result.total * 0.9 && result.distinct >= MIN_DISTINCT;
  log(`${entry.label} (${rel(entry.target)}): ${result.total} calls, bytecode at frame+${hex(result.frame[0])} ` +
      `(${result.frame[1]}), ${result.distinct} different names, e.g. ${result.common.join(', ') || 'none'}` +
      (result.ok ? '' : ' - not it'));
  return result;
}

function startSampling() {
  log('sampling script calls to learn the layout (stand in a level, unpaused)');
  sample(candidates);
  waitForSamples(Date.now(), false);
}

function waitForSamples(started, broad) {
  for (const entry of samplers) {
    if (entry.listener !== null && entry.samples.length >= SAMPLE_COUNT) {
      entry.listener.detach(); // a busy native costs the game time on every call while it is hooked
      entry.listener = null;
    }
  }
  const full = samplers.every((entry) => entry.samples.length >= SAMPLE_COUNT);
  if (!full && Date.now() - started < SAMPLE_WAIT_MS) {
    setTimeout(() => waitForSamples(started, broad), 200);
    return;
  }
  stopSampling();
  const judged = samplers.map((entry) => ({ entry, result: judge(entry) }));
  const good = judged.filter(({ result }) => result.ok);
  if (good.length > 0 && !broad) {
    choose(good[0].entry.target, good[0].result);
    return;
  }
  if (broad) {
    good.sort((a, b) => b.result.distinct - a.result.distinct);
    if (good.length === 1 || (good.length > 1 && good[0].result.distinct >= 2 * good[1].result.distinct)) {
      choose(good[0].entry.target, good[0].result);
    } else {
      log('could not tell which native is execVirtualFunction. Send the log; use(0xNN) hooks one by its opcode.');
    }
    return;
  }
  if (gnatives === null) {
    log('the named native does not behave like execVirtualFunction; stopping here. Please send the log.');
    return;
  }
  // The engine may number its opcodes differently: try every native in use among the first ones.
  const seen = new Set(candidates.map((c) => c.target.toString()));
  const broadList = [];
  for (let opcode = 0; opcode < 0x60; opcode++) {
    const value = gnatives.words[opcode];
    const target = ptr(value);
    if (value !== gnatives.undefinedNative && !seen.has(target.toString())) {
      seen.add(target.toString());
      broadList.push({ label: `GNatives[${hex(opcode)}]`, target });
    }
  }
  log(`trying ${broadList.length} more natives at once for a few seconds; the game may stutter meanwhile`);
  sample(broadList);
  waitForSamples(Date.now(), true);
}

function choose(target, result) {
  virtualFunction = target;
  codeOffset = result.frame[0];
  objectNameOffset = result.object[1] >= result.total * 0.5 ? result.object[0] : null;
  callerOffset = objectNameOffset !== null && result.caller[1] >= result.total * 0.5 ? result.caller[0] : null;
  log(`execVirtualFunction is ${rel(target)}: bytecode at frame+${hex(codeOffset)}, object name at ` +
      `${objectNameOffset === null ? 'unknown' : `object+${hex(objectNameOffset)}`}, calling object at ` +
      `${callerOffset === null ? 'unknown' : `frame+${hex(callerOffset)}`}`);
  installFilter();
}

globalThis.use = function (opcode) {
  if (gnatives === null) {
    return 'no table of natives by opcode was found';
  }
  if (listener !== null) {
    return 'already hooked; stop() first';
  }
  const target = ptr(gnatives.words[opcode]);
  log(`sampling GNatives[${hex(opcode)}] at ${rel(target)}`);
  sample([{ label: `GNatives[${hex(opcode)}]`, target }]);
  const started = Date.now();
  const wait = () => {
    if (samplers[0].samples.length < SAMPLE_COUNT && Date.now() - started < SAMPLE_WAIT_MS) {
      setTimeout(wait, 200);
      return;
    }
    stopSampling();
    const result = judge(samplers[0]);
    if (result.frame[1] < result.total * 0.9) {
      log('its bytecode does not hold names; not hooking it');
      return;
    }
    choose(target, result);
  };
  wait();
  return 'sampling';
};

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
    virtualFunction: virtualFunction === null ? null : rel(virtualFunction),
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
  stopSampling();
  return 'hook removed';
};

if (candidates.length > 0) {
  startSampling();
}

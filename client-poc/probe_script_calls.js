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
 *   classes('sister') every class whose name has "sister" in it, with how many objects each has
 *   snap('Sister')    remember the objects of those classes; do one thing in the game, then
 *   diff()            print which words of them changed since (and remember the new values)
 *   fields('SpawnedGatherer')  every property of a class (and the classes it extends) and where it sits
 *   sisters()         print each Little Sister's flags whenever one changes (sisters() again stops)
 *   diaries()         every audio diary object (the ones picked up) and every diary class the game has loaded
 *   diaryTable()      every audio diary the game knows, with its title, creator and level (after one pickup)
 *   where('PlaceableWeaponUpgradeStation')  each object of the matching classes, where it is and how far away
 *   goto('PlaceableWeaponUpgradeStation', 0)  move the player next to the first of them (save first: this
 *                     writes the player's position straight into memory). goto(pattern, n, 150) stands 150 units
 *                     off its front instead of 80; a negative distance stands behind it
 */
'use strict';

const GAME = 'BioshockHD.exe';
const PROBE_VERSION = '2026-10-08.10';
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

const objects = { table: null, count: 0, nameOffset: null, classOffset: null, layout: null, propertyOffset: null,
  superOffset: undefined };

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
  if (objects.propertyOffset === null) {
    objects.propertyOffset = offset.at;
  }
  const properties = new Map();
  for (const field of layout.chain) {
    const kind = classNameOf(field) || '';
    if (/Property$/.test(kind)) {
      // Bools declared one after another share a word, one bit each in declaration order (seen in the game: a
      // quest's Completed reads 2 where HasSeenCurrentHint, declared just before it, is the 1).
      let mask = null;
      const at = u32(field.add(offset.at));
      if (kind === 'BoolProperty') {
        const sharing = [...properties.values()].filter((p) => p.kind === 'BoolProperty' && p.offset === at).length;
        mask = 1 << sharing;
      }
      properties.set(nameOfObject(field), { offset: at, kind, mask });
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
  if (property.kind === 'StrProperty') {
    const count = u32(at.add(4));
    if (value === 0 || count === null || count < 1 || count > 300 || !readable(ptr(value), count * 2)) {
      return '""';
    }
    return JSON.stringify(ptr(value).readUtf16String(count - 1));
  }
  if (property.kind === 'FloatProperty') {
    return at.readFloat().toFixed(1);
  }
  return String(value | 0);
}

let found = null;

// The object that holds this one (its package or level), a few words before the name. Which word is voted on
// across the quests: the one that most often points at an object that has a class.
let outerOffset;

function outerNameOf(object) {
  if (outerOffset === undefined) {
    const quests = found === null ? [] : (found.instances.get('Quest') || []);
    let best = { at: null, votes: 0 };
    for (const at of [objects.nameOffset - 4, objects.nameOffset - 8, objects.nameOffset - 12]) {
      const votes = quests.slice(0, 50).filter((q) => {
        const outer = pointerAt(q.add(at));
        return outer !== null && !outer.isNull() && readable(outer, 0x40) && classNameOf(outer) !== null;
      }).length;
      if (votes > best.votes) {
        best = { at, votes };
      }
    }
    outerOffset = best.votes >= Math.min(3, quests.length) ? best.at : null;
  }
  if (outerOffset === null) {
    return '?';
  }
  const outer = pointerAt(object.add(outerOffset));
  return outer !== null && !outer.isNull() && readable(outer, 0x40) ? (nameOfObject(outer) || '?') : '?';
}

function describeQuests(limit, withText) {
  const quests = found === null ? [] : (found.instances.get('Quest') || []);
  const properties = layouts.get('Quest');
  const lines = [`${quests.length} quests`];
  for (const quest of quests.slice(0, limit)) {
    if (properties === undefined) {
      lines.push(`  ${nameOfObject(quest)}`);
      continue;
    }
    const field = (name) => readProperty(quest, properties.get(name));
    let line = `  ${nameOfObject(quest)} in ${outerNameOf(quest)}: ` +
      `completed ${field('Completed')}, active ${field('Active')}, hidden ${field('Hidden')}, ` +
      `objectives ${field('NumberOfObjectivesCompleted')}/${field('NumberOfObjectivesToComplete')}`;
    if (withText) {
      line += `, parent ${field('ParentName')}, level ${field('LevelFriendlyName')}, ` +
        `name ${field('FriendlyName')}, objective ${field('ObjectiveDescription')}`;
    }
    lines.push(line);
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
  for (const line of describeQuests(1000, true)) {
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
  const lines = describeQuests(limit || 1000, false);
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

// Any class, by part of its name: classes('sister') lists the classes and how many objects each has.
// snap('LittleSister') remembers the first 0x800 bytes of every object of the matching classes, and diff() later
// says which words changed, so one rescue or harvest between the two shows where the game keeps it.
function matchingObjects(pattern) {
  const regex = new RegExp(pattern, 'i');
  const byClass = new Map();
  const total = u32(objects.table.add(4)) || objects.count;
  for (let index = 0; index < total; index++) {
    const object = objectAt(index);
    if (object === null) {
      continue;
    }
    const className = classNameOf(object);
    if (className === null || className === 'Class' || !regex.test(className)) {
      continue;
    }
    if (!byClass.has(className)) {
      byClass.set(className, []);
    }
    byClass.get(className).push(object);
  }
  return byClass;
}

globalThis.classes = function (pattern) {
  if (objects.table === null) {
    return 'no object table';
  }
  const regex = new RegExp(pattern || '.', 'i');
  const counts = new Map();
  const total = u32(objects.table.add(4)) || objects.count;
  for (let index = 0; index < total; index++) {
    const object = objectAt(index);
    const className = classNameOf(object);
    if (className === null) {
      continue;
    }
    if (className === 'Class') {
      const name = nameOfObject(object);
      if (name !== null && regex.test(name) && !counts.has(name)) {
        counts.set(name, 0);
      }
    } else if (regex.test(className)) {
      counts.set(className, (counts.get(className) || 0) + 1);
    }
  }
  const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  for (const [name, count] of sorted.slice(0, 200)) {
    log(`  ${name}: ${count} objects`);
  }
  return `${counts.size} classes`;
};

// The class a class extends: the word in a class that points at another class, the same word in every class,
// leading up to Object. Voted on across the classes already learned.
function findSuperOffset() {
  const known = [...found.classes.values()];
  for (let at = 0x20; at <= 0x100; at += 4) {
    if (at === objects.classOffset) {
      continue;
    }
    const reachesObject = known.filter((cls) => {
      let current = cls;
      for (let step = 0; step < 30 && current !== null && !current.isNull(); step++) {
        if (nameOfObject(current) === 'Object') {
          return step > 0;
        }
        const next = pointerAt(current.add(at));
        if (next === null || next.isNull() || classNameOf(next) !== 'Class') {
          return false;
        }
        current = next;
      }
      return false;
    }).length;
    if (known.length > 0 && reachesObject === known.length) {
      return at;
    }
  }
  return null;
}

function findClass(className) {
  const total = u32(objects.table.add(4)) || objects.count;
  for (let index = 0; index < total; index++) {
    const object = objectAt(index);
    if (object !== null && nameOfObject(object) === className && classNameOf(object) === 'Class') {
      return object;
    }
  }
  return null;
}

// Every property of a class and the classes above it, with where it sits in an object, using the layout learned
// from Quest. Also kept so diff() can name the words that change.
function learnAnyClass(className) {
  if (objects.layout === null || objects.propertyOffset === null) {
    return { error: 'the layout of classes is not known (Quest was not learned)' };
  }
  if (objects.superOffset === undefined) {
    objects.superOffset = findSuperOffset();
    log(`classes extend the class at class+${objects.superOffset === null ? '?' : hex(objects.superOffset)}`);
  }
  let cls = findClass(className);
  if (cls === null) {
    return { error: `no class named ${className}` };
  }
  const lineage = [];
  const properties = new Map();
  while (cls !== null && !cls.isNull() && lineage.length < 30) {
    const name = nameOfObject(cls);
    lineage.push(name);
    if (name === 'Object') {
      break;
    }
    let field = pointerAt(cls.add(objects.layout.childrenOffset));
    const bools = new Map();
    for (let n = 0; field !== null && !field.isNull() && n < 5000 && readable(field, 0x80); n++) {
      const kind = classNameOf(field) || '';
      if (/Property$/.test(kind)) {
        const at = u32(field.add(objects.propertyOffset));
        let mask = null;
        if (kind === 'BoolProperty') {
          const sharing = bools.get(at) || 0;
          bools.set(at, sharing + 1);
          mask = 1 << sharing;
        }
        const fieldName = nameOfObject(field);
        if (!properties.has(fieldName)) {
          properties.set(fieldName, { offset: at, kind, mask, owner: name });
        }
      }
      field = pointerAt(field.add(objects.layout.nextOffset));
    }
    if (objects.superOffset === null) {
      break;
    }
    cls = pointerAt(cls.add(objects.superOffset));
  }
  if (!layouts.has(className)) {
    layouts.set(className, properties);
  }
  return { lineage, properties };
}

globalThis.fields = function (className) {
  if (objects.table === null) {
    return 'no object table';
  }
  const learned = learnAnyClass(className);
  if (learned.error) {
    return learned.error;
  }
  log(`${className}: ${learned.lineage.join(' < ')}`);
  const sorted = [...learned.properties.entries()].sort((a, b) => a[1].offset - b[1].offset);
  for (const [name, p] of sorted) {
    log(`  +${hex(p.offset)}${p.mask === null ? '' : ` bit ${hex(p.mask)}`} ${name} (${p.kind}, ${p.owner})`);
  }
  return `${learned.properties.size} properties`;
};

// ---- moving the player, for testing ----

const PLAYER_CLASS = 'ShockPlayer';
const UNREAL_ROTATION = 65536; // a full turn

function actorPlace(object) {
  const actor = learnAnyClass('Actor');
  if (actor.error) {
    return actor;
  }
  const location = actor.properties.get('Location');
  const rotation = actor.properties.get('Rotation');
  if (location === undefined || rotation === undefined) {
    return { error: 'Actor has no Location or Rotation property here' };
  }
  const at = object.add(location.offset);
  return {
    at,
    x: at.readFloat(), y: at.add(4).readFloat(), z: at.add(8).readFloat(),
    yaw: object.add(rotation.offset + 4).readS32(),
    velocity: actor.properties.get('Velocity'),
  };
}

function thePlayer() {
  const players = matchingObjects(`^${PLAYER_CLASS}$`).get(PLAYER_CLASS) || [];
  if (players.length !== 1) {
    log(`found ${players.length} ${PLAYER_CLASS} objects, where one was expected`);
  }
  return players.length === 1 ? players[0] : null;
}

function placeText(place) {
  return `(${place.x.toFixed(0)}, ${place.y.toFixed(0)}, ${place.z.toFixed(0)})`;
}

globalThis.where = function (pattern) {
  if (objects.table === null) {
    return 'no object table';
  }
  const player = thePlayer();
  const from = player === null ? null : actorPlace(player);
  if (from !== null && from.error) {
    return from.error;
  }
  let n = 0;
  for (const [className, list] of matchingObjects(pattern)) {
    for (const object of list) {
      const place = actorPlace(object);
      if (place.error) {
        return place.error;
      }
      const away = from === null ? '' :
        `, ${Math.hypot(place.x - from.x, place.y - from.y, place.z - from.z).toFixed(0)} away`;
      log(`  ${n}: ${className} ${nameOfObject(object)} at ${placeText(place)}${away}`);
      n++;
    }
  }
  return n === 0 ? `nothing matches ${pattern} here` : `${n} found; goto('${pattern}', n) goes to one`;
};

globalThis.goto = function (pattern, n = 0, distance = 80) {
  if (objects.table === null) {
    return 'no object table';
  }
  const player = thePlayer();
  if (player === null) {
    return `could not find the one ${PLAYER_CLASS}: load a save first`;
  }
  const targets = [...matchingObjects(pattern).values()].flat();
  if (n < 0 || n >= targets.length) {
    return targets.length === 0 ? `nothing matches ${pattern} here` : `pick n from 0 to ${targets.length - 1}`;
  }
  const target = actorPlace(targets[n]);
  const from = actorPlace(player);
  if (target.error || from.error) {
    return target.error || from.error;
  }
  const angle = (target.yaw / UNREAL_ROTATION) * 2 * Math.PI;
  const x = target.x + Math.cos(angle) * distance;
  const y = target.y + Math.sin(angle) * distance;
  const z = target.z + 40; // a little above, so as not to start in the floor
  from.at.writeFloat(x);
  from.at.add(4).writeFloat(y);
  from.at.add(8).writeFloat(z);
  if (from.velocity !== undefined) {
    const velocity = player.add(from.velocity.offset);
    [0, 4, 8].forEach((k) => velocity.add(k).writeFloat(0));
  }
  log(`moved the player from ${placeText(from)} to (${x.toFixed(0)}, ${y.toFixed(0)}, ${z.toFixed(0)}), ` +
    `next to ${nameOfObject(targets[n])} at ${placeText(target)}`);
  return 'moved';
};

// What a changed word is, by the properties at that offset.
function labelOf(className, offset, before, after) {
  const properties = layouts.get(className);
  if (properties === undefined) {
    return '';
  }
  const names = [];
  for (const [name, p] of properties) {
    if (p.offset !== offset) {
      continue;
    }
    if (p.mask === null) {
      names.push(name);
    } else if (((before ^ after) & p.mask) !== 0) {
      names.push(`${name} ${(after & p.mask) !== 0}`);
    }
  }
  return names.length === 0 ? '' : ` (${names.join(', ')})`;
}

// Little Sisters as the client would see them: every 100 ms read the flags of each sister known so far, every
// 2 s look for new ones, and print whenever a flag changes or a sister appears or goes away.
const SISTER_CLASSES = '^(Spawned|PlayerEscorted)?Gatherer$';
const SISTER_FLAGS = ['HasBeenSavedOrPacified', 'bIsSaved', 'IntentionallyPacified', 'bIsUnconscious', 'bDeleteMe',
  'CurrentVent', 'VulnerableState'];
let sisterWatch = null;

function sisterState(object, className) {
  const properties = layouts.get(className);
  const end = properties === undefined ? 0 : Math.max(0x40, ...SISTER_FLAGS.map((name) =>
    (properties.has(name) ? properties.get(name).offset + 4 : 0)));
  if (properties === undefined || !readable(object, end)) {
    return null;
  }
  return SISTER_FLAGS.map((name) => {
    const p = properties.get(name);
    if (p === undefined) {
      return `${name} ?`;
    }
    if (p.kind === 'ObjectProperty') {
      const value = u32(object.add(p.offset));
      return `${name} ${value ? (nameOfObject(ptr(value)) || 'set') : 'none'}`;
    }
    if (p.kind === 'ByteProperty') {
      return `${name} ${object.add(p.offset).readU8()}`;
    }
    return `${name} ${readProperty(object, p)}`;
  }).join(', ');
}

globalThis.sisters = function () {
  if (objects.table === null) {
    return 'no object table';
  }
  if (sisterWatch !== null) {
    sisterWatch.stopped = true;
    sisterWatch = null;
    return 'stopped watching sisters';
  }
  const watch = { stopped: false, known: new Map(), lastScan: null };
  sisterWatch = watch;
  const tick = () => {
    if (watch.stopped) {
      return;
    }
    const now = Date.now();
    if (watch.lastScan === null || now - watch.lastScan >= 2000) {
      watch.lastScan = now;
      for (const [className, list] of matchingObjects(SISTER_CLASSES)) {
        if (!layouts.has(className)) {
          learnAnyClass(className);
        }
        for (const object of list) {
          const key = object.toString();
          if (!watch.known.has(key)) {
            const state = sisterState(object, className);
            watch.known.set(key, { object, className, state, vtable: u32(object) });
            log(`sister ${className} at ${object} appeared: ${state}`);
          }
        }
      }
    }
    for (const [key, sister] of watch.known) {
      const vtable = u32(sister.object);
      const state = vtable === sister.vtable ? sisterState(sister.object, sister.className) : null;
      if (state === null) {
        log(`sister at ${key} is gone (last seen: ${sister.state})`);
        watch.known.delete(key);
      } else if (state !== sister.state) {
        log(`sister at ${key}: ${state}`);
        sister.state = state;
      }
    }
    setTimeout(tick, 100);
  };
  tick();
  return `watching ${watch.known.size} sisters; sisters() again stops`;
};

// Audio diaries: each one is a class of its own that extends QuestLog, and the player is given an object of that
// class when picking it up. diaries() lists the diary objects that exist (with what they say about themselves) and
// every diary class the game has loaded.
function extendsClass(cls, wanted, cache) {
  const key = cls.toString();
  if (cache.has(key)) {
    return cache.get(key);
  }
  let found = false;
  let current = cls;
  for (let step = 0; step < 30 && current !== null && !current.isNull(); step++) {
    const name = nameOfObject(current);
    if (name === wanted) {
      found = true;
      break;
    }
    if (name === 'Object' || objects.superOffset === null) {
      break;
    }
    current = pointerAt(current.add(objects.superOffset));
  }
  cache.set(key, found);
  return found;
}

function stringAt(at) {
  const data = u32(at);
  const count = u32(at.add(4));
  if (!data || count === null || count < 1 || count > 2000 || !readable(ptr(data), count * 2)) {
    return '';
  }
  return ptr(data).readUtf16String(count - 1);
}

globalThis.diaries = function () {
  if (objects.table === null) {
    return 'no object table';
  }
  if (objects.superOffset === undefined) {
    objects.superOffset = findSuperOffset();
  }
  const cache = new Map();
  const classes = [];
  const instances = [];
  const total = u32(objects.table.add(4)) || objects.count;
  for (let index = 0; index < total; index++) {
    const object = objectAt(index);
    const cls = classOf(object);
    if (object === null || cls === null || cls.isNull()) {
      continue;
    }
    if (nameOfObject(cls) === 'Class') {
      if (nameOfObject(object) !== 'QuestLog' && extendsClass(object, 'QuestLog', cache)) {
        classes.push(nameOfObject(object));
      }
    } else if (extendsClass(cls, 'QuestLog', cache)) {
      instances.push(object);
    }
  }
  log(`${classes.length} diary classes loaded: ${classes.sort().join(', ')}`);
  log(`${instances.length} diary objects:`);
  for (const object of instances) {
    const className = classNameOf(object);
    if (!layouts.has(className)) {
      learnAnyClass(className);
    }
    const p = layouts.get(className) || new Map();
    const text = (name) => (p.has(name) ? JSON.stringify(stringAt(object.add(p.get(name).offset))) : '?');
    const nameField = (name) => (p.has(name) ? (nameOf(u32(object.add(p.get(name).offset))) || '?') : '?');
    let entry = '?';
    if (p.has('Entry')) {
      const array = object.add(p.get('Entry').offset);
      const data = u32(array);
      const count = u32(array.add(4));
      entry = count ? `${count} lines, first ${JSON.stringify(stringAt(ptr(data)).slice(0, 80))}` : 'none';
    }
    log(`  ${nameOfObject(object)} (${className}) owner ${nameOfObject(pointerAt(object.add(objects.nameOffset - 8))) || '?'}: ` +
        `creator ${text('CreatorFriendlyName')} (${nameField('Creator')}), level ${text('RelevantLevel')}, ` +
        `date ${text('CreatedDate')}, type ${nameField('LogType')}, entry ${entry}`);
  }
  return `${classes.length} classes, ${instances.length} objects`;
};

// Every diary at once, from the classes' default values (each class keeps a block of them, laid out like an
// object of the class). Where that block is, is found from one diary object: the word in its class that points at
// a block holding the same creator text. Needs one diary or radio message to have been received in this game.
globalThis.diaryTable = function () {
  if (objects.table === null) {
    return 'no object table';
  }
  if (objects.superOffset === undefined) {
    objects.superOffset = findSuperOffset();
  }
  const cache = new Map();
  const classes = [];
  let sample = null;
  const total = u32(objects.table.add(4)) || objects.count;
  for (let index = 0; index < total; index++) {
    const object = objectAt(index);
    const cls = classOf(object);
    if (object === null || cls === null || cls.isNull()) {
      continue;
    }
    if (nameOfObject(cls) === 'Class') {
      if (extendsClass(object, 'QuestLog', cache)) {
        classes.push(object);
      }
    } else if (sample === null && extendsClass(cls, 'QuestLog', cache)) {
      sample = object;
    }
  }
  if (sample === null) {
    return 'no diary or radio message received yet in this game; pick one up and try again';
  }
  const sampleClass = classOf(sample);
  learnAnyClass(classNameOf(sample));
  const p = layouts.get(classNameOf(sample));
  const creator = p.get('CreatorFriendlyName');
  const wanted = stringAt(sample.add(creator.offset));
  // The block is either pointed at from the class (a list {data, count, max}) or lies inside the class itself.
  let defaultsAt = null;
  let inline = false;
  for (let d = 0x30; d <= 0x1000 && defaultsAt === null; d += 4) {
    if (!readable(sampleClass.add(d), 4)) {
      break;
    }
    const data = u32(sampleClass.add(d));
    if (data && readable(ptr(data), creator.offset + 12) && stringAt(ptr(data).add(creator.offset)) === wanted) {
      defaultsAt = d;
    }
  }
  if (defaultsAt === null) {
    for (let d = 0; d <= 0x1000 && defaultsAt === null; d += 4) {
      if (readable(sampleClass.add(d + creator.offset), 12) &&
          stringAt(sampleClass.add(d + creator.offset)) === wanted) {
        defaultsAt = d;
        inline = true;
      }
    }
  }
  if (defaultsAt === null) {
    return `could not find where a class keeps its default values (looked for ${JSON.stringify(wanted)} at ` +
      `+${hex(creator.offset)} of a block, within class+0x1000). Class words: ${hexWords(sampleClass, 0x30, 0x130)}`;
  }
  log(`default values ${inline ? 'inside the class from' : 'pointed at from'} class+${hex(defaultsAt)}, ` +
      `${classes.length} QuestLog classes`);
  const strings = [...p.entries()].filter(([, q]) => q.kind === 'StrProperty');
  const kinds = new Map();
  const rows = [];
  for (const cls of classes) {
    const data = inline ? cls.add(defaultsAt) : ptr(u32(cls.add(defaultsAt)) || 0);
    if (data.isNull() || !readable(data, creator.offset + 12)) {
      continue;
    }
    const defaults = data;
    const type = p.has('LogType') ? (nameOf(u32(defaults.add(p.get('LogType').offset))) || '?') : '?';
    kinds.set(type, (kinds.get(type) || 0) + 1);
    if (type !== 'Log') {
      continue;
    }
    const texts = strings.map(([name, q]) => `${name} ${JSON.stringify(stringAt(defaults.add(q.offset)))}`);
    let entry = '';
    if (p.has('Entry')) {
      const array = defaults.add(p.get('Entry').offset);
      if (u32(array.add(4))) {
        entry = stringAt(ptr(u32(array))).slice(0, 60);
      }
    }
    rows.push(`  ${nameOfObject(cls)}: ${texts.join(', ')}, entry ${JSON.stringify(entry)}`);
  }
  rows.sort().forEach((row) => log(row));
  log(`types: ${[...kinds.entries()].map(([k, n]) => `${k} ${n}`).join(', ')}`);
  return `${rows.length} diaries listed`;
};

const SNAP_BYTES = 0x800;
let snapshot = null;

globalThis.snap = function (pattern) {
  if (objects.table === null) {
    return 'no object table';
  }
  snapshot = { pattern, objects: new Map() };
  let count = 0;
  for (const [className, list] of matchingObjects(pattern)) {
    if (!layouts.has(className)) {
      learnAnyClass(className);
    }
    for (const object of list.slice(0, 300)) {
      // Enough to cover every property the class has (a Little Sister's own flags sit past +0x1000).
      const known = layouts.has(className) ? [...layouts.get(className).values()].map((p) => p.offset + 0x10) : [];
      let length = Math.min(0x4000, Math.max(SNAP_BYTES, ...known.map((end) => (end + 0xFF) & ~0xFF)));
      while (length > 0x40 && !readable(object, length)) {
        length >>= 1;
      }
      if (!readable(object, length)) {
        continue;
      }
      snapshot.objects.set(object.toString(), { object, className, words: new Uint32Array(object.readByteArray(length)) });
      count++;
    }
    log(`  ${className}: ${list.length} objects`);
  }
  return `${count} objects remembered; do the thing, then diff()`;
};

globalThis.diff = function () {
  if (snapshot === null) {
    return 'snap(pattern) first';
  }
  let changed = 0;
  for (const { object, className, words } of snapshot.objects.values()) {
    if (!readable(object, words.length * 4)) {
      log(`  ${className} at ${object}: gone`);
      continue;
    }
    const now = new Uint32Array(object.readByteArray(words.length * 4));
    const lines = [];
    for (let i = 0; i < words.length; i++) {
      if (now[i] !== words[i]) {
        lines.push(`+${hex(i * 4)}${labelOf(className, i * 4, words[i], now[i])}: ` +
          `${words[i].toString(16)} -> ${now[i].toString(16)}`);
      }
    }
    // Words that name a property are printed in full; the rest (positions, timers) only when there are few.
    const named = lines.filter((line) => line.includes(' ('));
    const label = `${className} at ${object}`;
    if (now[0] !== words[0]) {
      changed++;
      log(`  ${label}: deleted (its vtable changed)${named.length ? `; before that: ${named.join(', ')}` : ''}`);
    } else if (lines.length > 0 && lines.length <= 40) {
      changed++;
      log(`  ${label}: ${lines.join(', ')}`);
    } else if (lines.length > 40) {
      changed++;
      log(`  ${label}: ${lines.length} words changed; named: ${named.join(', ') || 'none'}`);
    }
    snapshot.objects.get(object.toString()).words = now;
  }
  return `${changed} of ${snapshot.objects.size} objects changed (diff() again compares with now)`;
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

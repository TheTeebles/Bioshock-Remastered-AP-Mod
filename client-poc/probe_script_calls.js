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
 *   stock()           every shop loaded in the level (vending machines, Gatherer's Gardens, Power to the People
 *                     stations): where it is and the values of its own properties, arrays and structs opened up.
 *                     stock('Vend') lists the objects of the classes matching 'Vend' instead
 *   garden()          the nearest Gatherer's Garden's lines, numbered: what each gives, how many are left, bought
 *   gardenItem(7, 'chips')  make line 7 of garden() give the class named 'chips' instead, to see whether buying it
 *                     then gives that (and not the gene) while AmountPurchased still counts the purchase.
 *                     gardenItem(7) puts the line's own class back
 *   gardenCost(7, 20) set line 7's CostAdjustment (the price multiplier) to 20, to see how the price follows it
 *                     and which currency the line asks for
 *   defaults('chips') the text properties of a class's default values (what a new chips object starts with)
 *   placeholders()    every loaded item class (Item and below) with its shown name and how many objects of it exist,
 *                     to pick placeholder items for Garden lines
 *   gardenClone(7, 'Hookshot')  make line 7 give a copy of the chips class made in memory, named 'Hookshot', so
 *                     each line can have its own name without renaming real items (save first: the game has
 *                     never seen this class, so buying it, saving or changing levels may crash)
 *   rename('chips', 'FriendlyName', 'Archipelago Item')  rewrite one of them, to see whether the Garden shows it
 *   upgrades()        every weapon upgrade stat (UpgradeableWeaponStat) with its values, numbered
 *   setUpgrade(4, 0)  set PointsAllocated of stat number 4 to 0, to see whether that takes the upgrade back
 *   preview(2, 'Hookshot', 'For Halthrax')  retitle upgrade line 2 of upgrades() and hide the others
 *   stationHooks()    print when the station's begin, confirm and cancel run
 *   blockConfirm()    make confirming an upgrade do what cancelling does
 *   stationCode()     print the machine code of the station's begin, confirm and cancel, and of the C++ functions
 *                     they call; then print a line each time one of those C++ functions runs
 *   blockDeep()       after stationCode(): confirming runs the C++ cancel instead of the C++ confirm
 *   deadStat(2)       give upgrade line 2 a stat name no weapon uses, so claiming it upgrades nothing
 *   traceCalls()      print every script function the game runs from now on that it did not run in the first
 *                     three seconds (so not the ones it runs every frame); traceStop() ends it with a count of each
 *   skipCall('AddStatUpgrade')  the game skips that script function whenever it would run it; skipCall() undoes it
 *                     (crashed the game when tried on GetHoldableByClassName: do not use)
 *   touch()           after events(): using a Power to the People station no longer opens its menu; the station
 *                     finishes its interaction at once (FinishInteraction) and should close, with no upgrade given
 *   events()          find the function the game calls script functions through (UObject::ProcessEvent): watches
 *                     the player's virtual functions for one second (the game must be running, not paused)
 *   hurt(10)          after events(): the player takes 10 damage through the game's own TakeSimpleDamage, as a
 *                     failed hack does. hurt(1000) should kill. hurt(10, 'TakeScriptedDamage') tries the other one,
 *                     hurt(10, 'RemoveHealth') takes health away directly
 *   die()             after events(): empties the player's health and calls OnKilled, the game's own end of a death
 *                     (what its damage code calls when health runs out). Should end in a Vita-Chamber
 *   check()           after events(): calls SetInvincible(true), then SetInvincible(false), on the player and reads
 *                     the flag after each, to see whether calls through events() run the game's script at all
 *                     (events() looks for ProcessEvent: the function that calls the one it watched)
 *   peek()            after events(): prints the start of that function's machine code, and the raw words of a few
 *                     script functions, to see what it checks before it runs one
 */
'use strict';

const GAME = 'BioshockHD.exe';
const PROBE_VERSION = '2026-10-14.15';
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
    // Only good pages are remembered: one not yet in use (say, on the main menu) may hold objects after a load.
    if (verdict) {
      if (pageVerdicts.size > 16384) {
        pageVerdicts.clear();
      }
      pageVerdicts.set(page, verdict);
    }
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

// Pages are judged once and remembered, so one freed since then still reads as fine: such a read fails, and the
// page is judged again next time.
function u32(pointer) {
  if (!readable(pointer, 4)) {
    return null;
  }
  try {
    return pointer.readU32();
  } catch (e) {
    pageVerdicts.delete(pointer.toUInt32() >>> 12);
    return null;
  }
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
    if (text !== null) {
      nameCache.set(index, text); // a name not readable yet is looked up again next time
    }
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
          properties.set(fieldName, { offset: at, kind, mask, owner: name, field });
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

// ---- what the shops sell, for item purchases ----

// The fields of a struct (or a class's own fields), laid out the way classes are.
function structFields(struct) {
  const list = [];
  const bools = new Map();
  let field = pointerAt(struct.add(objects.layout.childrenOffset));
  for (let n = 0; field !== null && !field.isNull() && n < 500 && readable(field, 0x80); n++) {
    const kind = classNameOf(field) || '';
    if (/Property$/.test(kind)) {
      const offset = u32(field.add(objects.propertyOffset));
      let mask = null;
      if (kind === 'BoolProperty') {
        const sharing = bools.get(offset) || 0;
        bools.set(offset, sharing + 1);
        mask = 1 << sharing;
      }
      list.push({ name: nameOfObject(field), offset, kind, mask, field });
    }
    field = pointerAt(field.add(objects.layout.nextOffset));
  }
  return list;
}

// A pointer kept in a property after its offset: an array's inner property (it has the array's name), or a struct
// property's struct.
function linkedTo(field, wanted) {
  for (let at = objects.propertyOffset + 4; at <= objects.propertyOffset + 0x40; at += 4) {
    const target = pointerAt(field.add(at));
    if (target !== null && !target.isNull() && readable(target, 0x80) && wanted(target)) {
      return target;
    }
  }
  return null;
}

function innerOf(field) {
  const name = nameOfObject(field);
  return linkedTo(field, (t) => /Property$/.test(classNameOf(t) || '') && nameOfObject(t) === name);
}

function structOf(field) {
  return linkedTo(field, (t) => /^(Script)?Struct$/.test(classNameOf(t) || ''));
}

const KIND_SIZE = {
  ByteProperty: 1, BoolProperty: 4, IntProperty: 4, FloatProperty: 4, NameProperty: 4, ObjectProperty: 4,
  ClassProperty: 4, StrProperty: 12, ArrayProperty: 12,
};

function sizeOf(property, depth = 0) {
  if (property.kind === 'StructProperty') {
    const struct = structOf(property.field);
    if (struct === null || depth > 4) {
      return null;
    }
    let end = 0;
    for (const f of structFields(struct)) {
      const size = sizeOf(f, depth + 1);
      if (size === null) {
        return null;
      }
      end = Math.max(end, f.offset + size);
    }
    return (end + 3) & ~3;
  }
  return KIND_SIZE[property.kind] || null;
}

function objectText(value) {
  if (value === 0) {
    return 'None';
  }
  const object = ptr(value);
  if (!readable(object, 0x40)) {
    return hex(value);
  }
  const name = nameOfObject(object);
  const className = classNameOf(object);
  if (name === null || className === null) {
    return hex(value);
  }
  return className === 'Class' ? `class ${name}` : `${name} (${className})`;
}

// One value, read the way its property says. Structs and arrays are opened up a few levels deep.
function valueText(at, property, depth = 0) {
  const word = u32(at);
  if (word === null) {
    return '?';
  }
  switch (property.kind) {
    case 'BoolProperty':
      return property.mask === null ? hex(word) : String((word & property.mask) !== 0);
    case 'ByteProperty':
      return String(at.readU8());
    case 'IntProperty':
      return String(word | 0);
    case 'FloatProperty':
      return String(Math.round(at.readFloat() * 100) / 100);
    case 'NameProperty':
      return nameOf(word) || `#${word}`;
    case 'StrProperty':
      return JSON.stringify(stringAt(at));
    case 'ObjectProperty':
    case 'ClassProperty':
      return objectText(word);
    case 'StructProperty': {
      const struct = structOf(property.field);
      if (struct === null || depth > 3) {
        return `{${hexWords(at, 0, 12)} ...}`;
      }
      return `{${structFields(struct).map((f) => `${f.name}: ${valueText(at.add(f.offset), f, depth + 1)}`).join(', ')}}`;
    }
    case 'ArrayProperty': {
      const count = u32(at.add(4));
      if (word === 0 || count === null || count === 0) {
        return '[]';
      }
      const inner = innerOf(property.field);
      const kind = inner === null ? null : classNameOf(inner);
      const element = inner === null ? null : { kind, field: inner, mask: kind === 'BoolProperty' ? 1 : null };
      const size = element === null ? null : sizeOf(element);
      if (count > 500 || size === null || depth > 3 || !readable(ptr(word), count * size)) {
        return `[${count} of ${kind || '?'}${size === null ? '' : `, ${size} bytes each`}]`;
      }
      const shown = [];
      for (let i = 0; i < Math.min(count, 60); i++) {
        shown.push(valueText(ptr(word).add(i * size), element, depth + 1));
      }
      return `[${count}: ${shown.join(', ')}${count > 60 ? ', ...' : ''}]`;
    }
    default:
      return hex(word);
  }
}

// The properties a class adds below Actor (or below Object, for things that are not actors), with their values.
function ownValues(object, indent, depth) {
  const cls = classOf(object);
  const learned = learnAnyClass(classNameOf(object));
  if (cls === null || learned.error) {
    log(`${indent}${learned.error || 'no class'}`);
    return [];
  }
  const below = new Set(learned.lineage.slice(0, Math.max(0, learned.lineage.indexOf('Actor'))));
  if (learned.lineage.indexOf('Actor') < 0) {
    learned.lineage.filter((name) => name !== 'Object').forEach((name) => below.add(name));
  }
  const followed = [];
  const sorted = [...learned.properties.entries()].filter(([, p]) => below.has(p.owner))
    .sort((a, b) => a[1].offset - b[1].offset);
  for (const [name, p] of sorted) {
    const text = valueText(object.add(p.offset), p);
    log(`${indent}${name} (${p.owner}) = ${text}`);
    if (p.kind === 'ObjectProperty' && depth < 1) {
      const target = pointerAt(object.add(p.offset));
      if (target !== null && !target.isNull() && readable(target, 0x40) &&
          /Vend|Loot|Stock|Upgrade|Shop|Store/i.test(classNameOf(target) || '')) {
        followed.push(target);
      }
    }
  }
  for (const target of followed) {
    log(`${indent}-> ${nameOfObject(target)} (${classNameOf(target)}):`);
    ownValues(target, `${indent}    `, depth + 1);
  }
  return sorted;
}

const SHOP_CLASSES = ['VendingStation', 'WeaponUpgradeStation'];

globalThis.stock = function (pattern) {
  if (objects.table === null) {
    return 'no object table';
  }
  const actor = learnAnyClass('Actor');
  if (actor.error) {
    return actor.error;
  }
  const regex = pattern ? new RegExp(pattern, 'i') : null;
  const player = thePlayer();
  const from = player === null ? null : actorPlace(player);
  const caches = new Map(SHOP_CLASSES.map((shop) => [shop, new Map()]));
  const total = u32(objects.table.add(4)) || objects.count;
  let n = 0;
  for (let index = 0; index < total; index++) {
    const object = objectAt(index);
    const cls = object === null ? null : classOf(object);
    const className = cls === null ? null : nameOfObject(cls);
    if (className === null || className === 'Class') {
      continue;
    }
    const wanted = regex !== null ? regex.test(className) :
      SHOP_CLASSES.some((shop) => extendsClass(cls, shop, caches.get(shop)));
    if (!wanted) {
      continue;
    }
    const place = actorPlace(object);
    const away = from === null || from.error || place.error ? '' :
      `, ${Math.hypot(place.x - from.x, place.y - from.y, place.z - from.z).toFixed(0)} away`;
    log(`${n}: ${nameOfObject(object)} (${learnAnyClass(className).lineage.join(' < ')})` +
        `${place.error ? '' : ` at ${placeText(place)}`}${away}`);
    ownValues(object, '    ', 0);
    n++;
  }
  return n === 0 ? 'no shops loaded here' : `${n} shops`;
};

// ---- a Gatherer's Garden's lines, and what buying one gives ----

const gardenSwaps = new Map();  // line address -> the ItemClass it had

function nearestGarden() {
  const player = thePlayer();
  const from = player === null ? null : actorPlace(player);
  const cache = new Map();
  let best = null;
  const total = u32(objects.table.add(4)) || objects.count;
  for (let index = 0; index < total; index++) {
    const object = objectAt(index);
    const cls = object === null ? null : classOf(object);
    if (cls === null || classNameOf(object) === 'Class' || !extendsClass(cls, 'GrowthStation', cache)) {
      continue;
    }
    const place = actorPlace(object);
    const away = from === null || from.error || place.error ? 0 :
      Math.hypot(place.x - from.x, place.y - from.y, place.z - from.z);
    if (best === null || away < best.away) {
      best = { object, away };
    }
  }
  return best;
}

// The lines of a Garden's VendingLootSpec array: where each sits and its fields.
function gardenLines(garden) {
  const station = learnAnyClass(classNameOf(garden));
  const tableProperty = station.error ? undefined : station.properties.get('VendingTable');
  if (tableProperty === undefined) {
    return { error: station.error || 'no VendingTable property' };
  }
  const table = pointerAt(garden.add(tableProperty.offset));
  if (table === null || table.isNull()) {
    return { error: 'the Garden has no VendingTable' };
  }
  const lootTable = learnAnyClass('VendingLootTable');
  const specs = lootTable.error ? undefined : lootTable.properties.get('VendingLootSpec');
  if (specs === undefined) {
    return { error: lootTable.error || 'no VendingLootSpec array' };
  }
  const inner = innerOf(specs.field);
  const struct = inner === null ? null : structOf(inner);
  const size = inner === null ? null : sizeOf({ kind: 'StructProperty', field: inner });
  if (struct === null || size === null) {
    return { error: 'the VendingLootSpec struct could not be read' };
  }
  const fields = new Map(structFields(struct).map((f) => [f.name, f]));
  const data = pointerAt(table.add(specs.offset));
  const count = u32(table.add(specs.offset + 4)) || 0;
  const lines = [];
  for (let n = 0; data !== null && !data.isNull() && n < count && n < 100; n++) {
    lines.push(data.add(n * size));
  }
  return { table, lines, fields };
}

function lineText(line, fields) {
  return [...fields.values()].map((f) => `${f.name}: ${valueText(line.add(f.offset), f)}`).join(', ');
}

globalThis.garden = function () {
  if (objects.table === null) {
    return 'no object table';
  }
  const found = nearestGarden();
  if (found === null) {
    return "no Gatherer's Garden loaded here";
  }
  const read = gardenLines(found.object);
  if (read.error) {
    return read.error;
  }
  log(`${nameOfObject(found.object)} ${found.away.toFixed(0)} away, table ${nameOfObject(read.table)}:`);
  read.lines.forEach((line, n) => {
    const swapped = gardenSwaps.has(line.toString()) ? ' (swapped)' : '';
    log(`  ${n}: ${lineText(line, read.fields)}${swapped}`);
  });
  return `${read.lines.length} lines`;
};

globalThis.gardenItem = function (n, className) {
  if (objects.table === null) {
    return 'no object table';
  }
  const found = nearestGarden();
  const read = found === null ? { error: "no Gatherer's Garden loaded here" } : gardenLines(found.object);
  if (read.error) {
    return read.error;
  }
  const line = read.lines[n];
  const itemClass = read.fields.get('ItemClass');
  if (line === undefined || itemClass === undefined) {
    return line === undefined ? `pick n from 0 to ${read.lines.length - 1}` : 'no ItemClass field';
  }
  const at = line.add(itemClass.offset);
  const key = line.toString();
  let wanted;
  if (className === undefined) {
    if (!gardenSwaps.has(key)) {
      return 'that line was not swapped';
    }
    wanted = ptr(gardenSwaps.get(key));
    gardenSwaps.delete(key);
  } else {
    wanted = findClass(className);
    if (wanted === null) {
      return `no class named ${className} is loaded`;
    }
    if (!gardenSwaps.has(key)) {
      gardenSwaps.set(key, u32(at));
    }
  }
  const before = valueText(at, itemClass);
  at.writePointer(wanted);
  log(`line ${n}: ItemClass ${before} -> ${valueText(at, itemClass)}`);
  log(`  now ${lineText(line, read.fields)}`);
  return className === undefined ? 'put back' : 'open the Garden and buy that line';
};

globalThis.gardenCost = function (n, adjustment) {
  if (objects.table === null) {
    return 'no object table';
  }
  const found = nearestGarden();
  const read = found === null ? { error: "no Gatherer's Garden loaded here" } : gardenLines(found.object);
  if (read.error) {
    return read.error;
  }
  const line = read.lines[n];
  const field = read.fields.get('CostAdjustment');
  if (line === undefined || field === undefined) {
    return line === undefined ? `pick n from 0 to ${read.lines.length - 1}` : 'no CostAdjustment field';
  }
  const at = line.add(field.offset);
  const before = valueText(at, field);
  if (field.kind === 'FloatProperty') {
    at.writeFloat(adjustment);
  } else if (field.kind === 'ByteProperty') {
    at.writeU8(adjustment);
  } else {
    at.writeS32(adjustment);
  }
  log(`line ${n}: CostAdjustment (${field.kind}) ${before} -> ${valueText(at, field)}`);
  log(`  now ${lineText(line, read.fields)}`);
  return 'open the Garden and look at that line\'s price';
};

// A class's default values. Either a default object in the object table (an object of the class whose name starts
// with "Default"), or an array (data, count, capacity) in the class object whose bytes hold the class's properties at
// their usual offsets. Found by looking for readable text at the class's string properties.
const keptStrings = [];

function textsAt(base, strings) {
  return strings.map(([name, p]) => [name, p, readable(base.add(p.offset), 12) ? stringAt(base.add(p.offset)) : '']);
}

function classDefaults(className, quiet = false) {
  const say = quiet ? () => {} : log;
  const learned = learnAnyClass(className);
  const cls = learned.error ? null : findClass(className);
  if (cls === null) {
    return { error: learned.error || `no class named ${className}` };
  }
  const strings = [...learned.properties.entries()].filter(([, p]) => p.kind === 'StrProperty');
  say(`${className}: ${learned.lineage.join(' < ')}; text properties: ` +
      `${strings.map(([name, p]) => `${name} +${hex(p.offset)} (${p.owner})`).join(', ') || 'none'}`);
  if (strings.length === 0) {
    return { error: `${className} has no text properties` };
  }
  const candidates = [];
  const instances = [];
  // default objects
  const total = u32(objects.table.add(4)) || objects.count;
  for (let index = 0; index < total; index++) {
    const object = objectAt(index);
    if (object === null || classOf(object) === null || !classOf(object).equals(cls)) {
      continue;
    }
    const name = nameOfObject(object) || '';
    say(`  object ${name} of class ${className} at ${object}`);
    instances.push(object);
    if (/^Default/i.test(name)) {
      candidates.push({ where: `default object ${name}`, data: object });
    }
  }
  // arrays in the class object
  const end = Math.max(...strings.map(([, p]) => p.offset)) + 12;
  for (let at = 0x30; at < 0x1000; at += 4) {
    const data = pointerAt(cls.add(at));
    const count = u32(cls.add(at + 4));
    const capacity = u32(cls.add(at + 8));
    if (data === null || data.isNull() || count === null || capacity === null || count < end ||
        count > 0x40000 || capacity < count || !readable(data, end)) {
      continue;
    }
    candidates.push({ where: `array at class+${hex(at)} (${count} bytes)`, data, at });
  }
  let best = null;
  for (const candidate of candidates) {
    const texts = textsAt(candidate.data, strings);
    const filled = texts.filter(([, , text]) => text.length > 0).length;
    say(`  ${candidate.where}: ${filled} of ${texts.length} text properties hold text` +
        `${filled > 0 ? ` (${texts.filter(([, , t]) => t).map(([n, , t]) => `${n} ${JSON.stringify(t.slice(0, 40))}`).join(', ')})` : ''}`);
    if (filled > 0 && (best === null || filled > best.filled)) {
      best = { ...candidate, texts, filled };
    }
  }
  if (best === null) {
    return { error: `no default values with text found for ${className} (${candidates.length} places tried)` };
  }
  return { ...best, instances };
}

globalThis.defaults = function (className) {
  if (objects.table === null) {
    return 'no object table';
  }
  const found = classDefaults(className);
  if (found.error) {
    log(found.error);
    return found.error;
  }
  log(`${className}: default values in ${found.where} (${found.data})`);
  for (const [name, p, text] of found.texts) {
    log(`  +${hex(p.offset)} ${name} (${p.owner}) = ${JSON.stringify(text)}`);
  }
  return `${found.filled} of ${found.texts.length} text properties filled`;
};

// Every default value of an item class (its price and icon are somewhere in there), or only the properties whose
// names match a pattern.
function itemValues(className, pattern, indent = '  ') {
  const found = classDefaults(className, true);
  if (found.error) {
    log(`${indent}${className}: ${found.error}`);
    return 0;
  }
  const learned = learnAnyClass(className);
  // an array of default values is this many bytes long; a default object is taken to be big enough
  const count = found.at === undefined ? 0x2000 : (u32(findClass(className).add(found.at + 4)) || 0);
  const sorted = [...learned.properties.entries()].sort((a, b) => a[1].offset - b[1].offset)
    .filter(([name, p]) => p.offset < count && (pattern === undefined || pattern.test(name)));
  for (const [name, p] of sorted) {
    log(`${indent}+${hex(p.offset)} ${name} (${p.kind}, ${p.owner}) = ${valueText(found.data.add(p.offset), p)}`);
  }
  return sorted.length;
}

globalThis.values = function (className, pattern) {
  if (objects.table === null) {
    return 'no object table';
  }
  const n = itemValues(className, pattern === undefined ? undefined : new RegExp(pattern, 'i'));
  return `${n} values`;
};

// What each line of the nearest Garden sells: the item class, its price and anything that looks like an icon.
const PRICE_OR_ICON = /price|cost|adam|value|icon|tex|mat|image|hud|inventory|sprite|portrait|picture/i;
globalThis.gardenGenes = function () {
  if (objects.table === null) {
    return 'no object table';
  }
  const found = nearestGarden();
  const read = found === null ? { error: "no Gatherer's Garden loaded here" } : gardenLines(found.object);
  if (read.error) {
    return read.error;
  }
  const field = (line, name) => {
    const f = read.fields.get(name);
    return f === undefined ? '?' : valueText(line.add(f.offset), f);
  };
  const seen = new Set();
  read.lines.forEach((line, n) => {
    const itemClass = pointerAt(line.add(read.fields.get('ItemClass').offset));
    const className = itemClass === null || itemClass.isNull() ? null : nameOfObject(itemClass);
    log(`${n}: ${className} CostAdjustment ${field(line, 'CostAdjustment')} cost ${field(line, 'cost')} ` +
        `PickupClass ${field(line, 'PickupClass')}`);
    if (className !== null && !seen.has(className)) {
      seen.add(className);
      itemValues(className, PRICE_OR_ICON, '    ');
    }
  });
  log('chips:');
  itemValues('chips', PRICE_OR_ICON, '    ');
  return `${read.lines.length} lines; open the Garden first so cost is filled`;
};

globalThis.rename = function (className, propertyName, text) {
  if (objects.table === null) {
    return 'no object table';
  }
  const found = classDefaults(className);
  if (found.error) {
    log(found.error);
    return found.error;
  }
  const entry = found.texts.find(([name]) => name === propertyName);
  if (entry === undefined) {
    return `${className} has no text property ${propertyName}`;
  }
  // A new buffer the game did not allocate; kept alive here, and never handed to the game to free. Written into the
  // class's default values and into every object of the class (Items are objects, and the game may show one of them).
  const buffer = Memory.allocUtf16String(text);
  keptStrings.push(buffer);
  const places = [['defaults', found.data], ...found.instances.map((o) => [`object at ${o}`, o])];
  for (const [label, base] of places) {
    const at = base.add(entry[1].offset);
    const before = readable(at, 12) ? stringAt(at) : '?';
    at.writePointer(buffer);
    at.add(4).writeU32(text.length + 1);
    at.add(8).writeU32(text.length + 1);
    log(`${className}.${propertyName} in ${label}: ${JSON.stringify(before)} -> ${JSON.stringify(stringAt(at))}`);
  }
  return 'open the Garden (or look at a chips pickup) to see it';
};

globalThis.placeholders = function () {
  if (objects.table === null) {
    return 'no object table';
  }
  const cache = new Map();
  const counts = new Map();
  const classes = [];
  const total = u32(objects.table.add(4)) || objects.count;
  for (let index = 0; index < total; index++) {
    const object = objectAt(index);
    const cls = object === null ? null : classOf(object);
    if (cls === null) {
      continue;
    }
    if (classNameOf(object) === 'Class') {
      if (extendsClass(object, 'Item', cache)) {
        classes.push(object);
      }
    } else {
      const key = cls.toString();
      counts.set(key, (counts.get(key) || 0) + 1);
    }
  }
  for (const cls of classes) {
    const name = nameOfObject(cls);
    const found = classDefaults(name, true);
    const friendly = found.error ? '?' : (found.texts.find(([n]) => n === 'FriendlyName') || [, , ''])[2];
    const lineage = learnAnyClass(name).lineage || [];
    log(`${name}: ${JSON.stringify(friendly)}, ${counts.get(cls.toString()) || 0} objects (${lineage.slice(1, -1).join(' < ')})`);
  }
  return `${classes.length} item classes`;
};

// A copy of an item class made in memory: the class object's bytes copied, with its own copy of the default values
// so its texts can differ. The copy is not in the object table; it keeps the original's name and index.
const CLASS_COPY_SIZE = 0x1000; // at most; the class object is at least 0xb58 bytes (arrays at +0x54c and +0xb4c)
const CLASS_MIN_SIZE = 0xb58;
const clones = [];

function cloneClass(className, friendlyName, description) {
  const found = classDefaults(className, true);
  const cls = findClass(className);
  if (found.error || cls === null || found.at === undefined) {
    return { error: found.error || `${className}'s default values are not in the class` };
  }
  // As much of the class as can be read, up to CLASS_COPY_SIZE: the memory after it may belong to nobody.
  let size = CLASS_COPY_SIZE;
  while (size >= CLASS_MIN_SIZE && !readable(cls, size)) {
    size -= 0x10;
  }
  if (size < Math.max(CLASS_MIN_SIZE, found.at + 12)) {
    return { error: `cannot read ${hex(CLASS_MIN_SIZE)} bytes of class ${className}` };
  }
  const copy = Memory.alloc(CLASS_COPY_SIZE);
  Memory.copy(copy, cls, size);
  const count = u32(cls.add(found.at + 4));
  const defaults = Memory.alloc(count);
  Memory.copy(defaults, found.data, count);
  copy.add(found.at).writePointer(defaults);
  copy.add(found.at + 8).writeU32(count);
  const kept = { copy, defaults, texts: [] };
  for (const [name, text] of [['FriendlyName', friendlyName], ['Description', description]]) {
    const entry = found.texts.find(([n]) => n === name);
    if (entry === undefined || text === undefined) {
      continue;
    }
    const buffer = Memory.allocUtf16String(text);
    kept.texts.push(buffer);
    const at = defaults.add(entry[1].offset);
    at.writePointer(buffer);
    at.add(4).writeU32(text.length + 1);
    at.add(8).writeU32(text.length + 1);
  }
  clones.push(kept);
  return kept;
}

globalThis.gardenClone = function (n, friendlyName, description, className = 'chips') {
  if (objects.table === null) {
    return 'no object table';
  }
  const found = nearestGarden();
  const read = found === null ? { error: "no Gatherer's Garden loaded here" } : gardenLines(found.object);
  if (read.error) {
    return read.error;
  }
  const line = read.lines[n];
  const itemClass = read.fields.get('ItemClass');
  if (line === undefined || itemClass === undefined) {
    return line === undefined ? `pick n from 0 to ${read.lines.length - 1}` : 'no ItemClass field';
  }
  const clone = cloneClass(className, friendlyName || `AP Item ${n}`, description || 'Sends a check to the multiworld.');
  if (clone.error) {
    log(clone.error);
    return clone.error;
  }
  const at = line.add(itemClass.offset);
  const key = line.toString();
  if (!gardenSwaps.has(key)) {
    gardenSwaps.set(key, u32(at));
  }
  at.writePointer(clone.copy);
  log(`line ${n}: ItemClass -> a copy of ${className} at ${clone.copy}, named ${JSON.stringify(friendlyName || `AP Item ${n}`)}`);
  return 'open the Garden and look at that line; gardenItem(n) puts the line back';
};

// ---- weapon upgrades, for taking back what a Power to the People station gave ----

function weaponStats() {
  return [...matchingObjects('^UpgradeableWeaponStat$').values()].flat();
}

// The Item class has an Icon property, but every Garden item leaves it None, so the Garden may pick the icon by the
// class's name. This renames the copy on line n (made by gardenClone) after another class, to see whose icon shows.
// Do not save while a copy carries another class's name: the save would name that class for the line.
globalThis.gardenIcon = function (n, className) {
  if (objects.table === null) {
    return 'no object table';
  }
  const found = nearestGarden();
  const read = found === null ? { error: "no Gatherer's Garden loaded here" } : gardenLines(found.object);
  if (read.error) {
    return read.error;
  }
  const line = read.lines[n];
  if (line === undefined) {
    return `pick n from 0 to ${read.lines.length - 1}`;
  }
  const copy = pointerAt(line.add(read.fields.get('ItemClass').offset));
  if (copy === null || !clones.some((c) => c.copy.equals(copy))) {
    return 'that line does not hold a copy: run gardenClone(n) first';
  }
  const wanted = findClass(className);
  if (wanted === null) {
    return `no class named ${className} is loaded`;
  }
  const before = nameOfObject(copy);
  copy.add(objects.nameOffset).writeU32(u32(wanted.add(objects.nameOffset)));
  log(`line ${n}: the copy's class name ${before} -> ${nameOfObject(copy)}`);
  return 'open the Garden and look at that line\'s icon (and do not save)';
};

// Every object whose name matches a pattern, with its class and the objects that may hold it (its package): to
// find how the Garden turns a class name into an icon, and which package a class is in.
globalThis.named = function (pattern, limit = 300) {
  if (objects.table === null) {
    return 'no object table';
  }
  const regex = new RegExp(pattern, 'i');
  const holder = (object, at) => {
    const outer = pointerAt(object.add(at));
    return outer === null || outer.isNull() || !readable(outer, 0x40) ? '-' : (nameOfObject(outer) || '?');
  };
  const total = u32(objects.table.add(4)) || objects.count;
  let n = 0;
  for (let index = 0; index < total && n < limit; index++) {
    const object = objectAt(index);
    const name = object === null ? null : nameOfObject(object);
    if (name === null || !regex.test(name)) {
      continue;
    }
    const outers = [0x1c, 0x20, 0x24].map((at) => `+${hex(at)} ${holder(object, at)}`).join(', ');
    log(`${index}: ${name} (${classNameOf(object)}) held by? ${outers}`);
    n++;
  }
  return `${n} objects`;
};

// ---- the Garden's icons: FlashMovie objects named after the item class ----

function objectNamedOfClass(name, className) {
  const total = u32(objects.table.add(4)) || objects.count;
  for (let index = 0; index < total; index++) {
    const object = objectAt(index);
    if (object !== null && nameOfObject(object) === name && classNameOf(object) === className) {
      return object;
    }
  }
  return null;
}

function bytesText(at, length) {
  if (!readable(at, length)) {
    return '?';
  }
  const bytes = new Uint8Array(at.readByteArray(length));
  const hexText = [...bytes].map((b) => b.toString(16).padStart(2, '0')).join(' ');
  const ascii = [...bytes].map((b) => (b >= 0x20 && b < 0x7f ? String.fromCharCode(b) : '.')).join('');
  return `${hexText}  ${ascii}`;
}

// Every FlashMovie loaded, by name.
globalThis.movies = function (pattern = '.', limit = 300) {
  if (objects.table === null) {
    return 'no object table';
  }
  const regex = new RegExp(pattern, 'i');
  const total = u32(objects.table.add(4)) || objects.count;
  const names = [];
  for (let index = 0; index < total; index++) {
    const object = objectAt(index);
    if (object !== null && classNameOf(object) === 'FlashMovie') {
      const name = nameOfObject(object) || '?';
      if (regex.test(name)) {
        names.push(`${name} (${object}, in ${pointerAt(object.add(0x1c)) === null ? '?' :
          nameOfObject(pointerAt(object.add(0x1c)))})`);
      }
    }
  }
  names.slice(0, limit).forEach((n) => log(`  ${n}`));
  return `${names.length} FlashMovies`;
};

// One FlashMovie: its class's properties and values, then every word of the object that could be an array
// (data, count, capacity), with the first bytes of its data, to find the movie file (SWF/GFX) it holds.
globalThis.movie = function (name = 'AirBlast') {
  if (objects.table === null) {
    return 'no object table';
  }
  const object = objectNamedOfClass(name, 'FlashMovie');
  if (object === null) {
    return `no FlashMovie named ${name} is loaded; open the Garden first`;
  }
  const learned = learnAnyClass('FlashMovie');
  log(`FlashMovie ${name} at ${object}: ${learned.error || learned.lineage.join(' < ')}`);
  if (!learned.error) {
    [...learned.properties.entries()].sort((a, b) => a[1].offset - b[1].offset).forEach(([field, p]) => {
      log(`  +${hex(p.offset)} ${field} (${p.kind}, ${p.owner}) = ${valueText(object.add(p.offset), p)}`);
    });
  }
  log(`  words: ${hexWords(object, 0, 0x100)}`);
  for (let at = 0x30; at < 0x200; at += 4) {
    const data = pointerAt(object.add(at));
    const count = u32(object.add(at + 4));
    const capacity = u32(object.add(at + 8));
    if (data === null || data.isNull() || count === null || capacity === null || count < 4 ||
        count > 0x4000000 || capacity < count || !readable(data, 16)) {
      continue;
    }
    log(`  array? at +${hex(at)}: ${count} items, data ${data}: ${bytesText(data, 32)}`);
  }
  for (let at = 0x30; at < 0x200; at += 4) {
    const target = pointerAt(object.add(at));
    if (target !== null && !target.isNull() && readable(target, 16)) {
      const first = bytesText(target, 4);
      if (/ (FWS|CWS|GFX|CFX|ZWS)/.test(` ${first.split('  ')[1] || ''}`)) {
        log(`  +${hex(at)} points at a movie file: ${bytesText(target, 32)}`);
      }
    }
  }
  return 'done';
};

// Which files the game opens, and when: to see whether the Garden's Flash movies (and sharedlibrary.swf, where the
// item icons are) are read again on a level load or when the Garden opens, which is when a changed copy could be
// handed over instead. files() starts watching CreateFileW, filesStop() stops.
const fileWatch = { hooks: [], seen: [] };

globalThis.files = function (pattern = 'swf|gsc|gfx|FlashMovies') {
  if (fileWatch.hooks.length > 0) {
    return 'already watching; filesStop() first';
  }
  const regex = new RegExp(pattern, 'i');
  fileWatch.seen = [];
  for (const [module, fname, wide] of [['kernel32.dll', 'CreateFileW', true], ['kernel32.dll', 'CreateFileA', false],
    ['kernelbase.dll', 'CreateFileW', true], ['kernelbase.dll', 'CreateFileA', false]]) {
    let at = null;
    try {
      at = Process.getModuleByName(module).getExportByName(fname);
    } catch (e) {
      continue;
    }
    fileWatch.hooks.push(Interceptor.attach(at, {
      onEnter(args) {
        let path = null;
        try {
          path = wide ? args[0].readUtf16String() : args[0].readAnsiString();
        } catch (e) {
          return;
        }
        if (path !== null && regex.test(path)) {
          const line = `${module}!${fname} ${path}`;
          fileWatch.seen.push(line);
          log(`file: ${line}`);
        }
      },
    }));
  }
  return `watching ${fileWatch.hooks.length} functions; load a save, open the Garden, then filesStop()`;
};

globalThis.filesStop = function () {
  fileWatch.hooks.forEach((h) => h.detach());
  fileWatch.hooks = [];
  return `stopped; ${fileWatch.seen.length} files opened`;
};

// Hands the game our own copies of some Flash movies: when it opens a file in a FlashMovies folder whose name is one
// of the files in `folder`, it gets the file in `folder` instead. Used to give the Garden menu (craftingStationPC.swf)
// a copy that imports the AP icon from APIcons.swf. Menus are read again on every load, so load a save after this.
const redirects = { hook: null, names: new Map(), kept: [], count: 0 };

globalThis.redirect = function (folder, names = ['craftingStationPC.swf', 'APIcons.swf']) {
  if (redirects.hook !== null) {
    return 'already redirecting; redirectStop() first';
  }
  redirects.names = new Map();
  for (const name of names) {
    const path = `${folder.replace(/[\\/]+$/, '')}\\${name}`;
    try {
      new File(path, 'rb').close();
      redirects.names.set(name.toLowerCase(), path);
    } catch (e) {
      log(`no ${path}: ${e.message}`);
    }
  }
  if (redirects.names.size === 0) {
    return 'none of those files are in that folder';
  }
  let at = null;
  for (const module of ['kernelbase.dll', 'kernel32.dll']) {
    try {
      at = Process.getModuleByName(module).getExportByName('CreateFileW');
      break;
    } catch (e) {
      // try the next
    }
  }
  if (at === null) {
    return 'no CreateFileW';
  }
  redirects.hook = Interceptor.attach(at, {
    onEnter(args) {
      let path = null;
      try {
        path = args[0].readUtf16String();
      } catch (e) {
        return;
      }
      if (path === null || !/flashmovies[\\/][^\\/]+$/i.test(path)) {
        return;
      }
      const ours = redirects.names.get(path.split(/[\\/]/).pop().toLowerCase());
      if (ours !== undefined) {
        const wide = Memory.allocUtf16String(ours);
        redirects.kept.push(wide);
        if (redirects.kept.length > 200) {
          redirects.kept.shift();
        }
        args[0] = wide;
        redirects.count++;
        log(`file: ${path} -> ${ours}`);
      }
    },
  });
  return `redirecting ${[...redirects.names.keys()].join(', ')}; now load a save`;
};

globalThis.redirectStop = function () {
  if (redirects.hook !== null) {
    redirects.hook.detach();
    redirects.hook = null;
  }
  return `stopped after ${redirects.count} redirects`;
};

globalThis.upgrades = function () {
  if (objects.table === null) {
    return 'no object table';
  }
  const stats = weaponStats();
  stats.forEach((stat, n) => {
    log(`${n}: ${nameOfObject(stat)} in ${outerNameOf(stat)} at ${stat}`);
    ownValues(stat, '    ', 1);
  });
  return `${stats.length} weapon stats`;
};

globalThis.setUpgrade = function (n, points) {
  const stats = weaponStats();
  const stat = stats[n];
  const learned = stat === undefined ? null : learnAnyClass('UpgradeableWeaponStat');
  const property = learned === null || learned.error ? undefined : learned.properties.get('PointsAllocated');
  if (property === undefined) {
    return stat === undefined ? `pick n from 0 to ${stats.length - 1}` : 'PointsAllocated not found';
  }
  const at = stat.add(property.offset);
  const before = at.readS32();
  at.writeS32(points);
  log(`${nameOfObject(stat)}: PointsAllocated ${before} -> ${points}`);
  return 'set';
};

// ---- a preview in the station: one line that names a multiworld item, and confirm turned into cancel ----

// Writes text into a string's own buffer (an FString: data, count with the terminator, capacity), cut to fit, so
// the game never has to free memory it did not allocate.
function writeInPlace(at, text) {
  const data = pointerAt(at);
  const capacity = u32(at.add(8));
  if (data === null || data.isNull() || capacity === null || capacity < 2 || !readable(data, capacity * 2)) {
    return null;
  }
  const kept = text.slice(0, capacity - 1);
  data.writeUtf16String(kept);
  at.add(4).writeU32(kept.length + 1);
  return kept;
}

// Shows one upgrade line, stat n of upgrades(), retitled; every other line is marked as bought so it is not listed.
globalThis.preview = function (n, title, text) {
  const stats = weaponStats();
  const learned = learnAnyClass('UpgradeableWeaponStat');
  if (learned.error) {
    return learned.error;
  }
  const points = learned.properties.get('PointsAllocated');
  const name = learned.properties.get('FriendlyName');
  const description = learned.properties.get('Description');
  if (n < 0 || n >= stats.length) {
    return `pick n from 0 to ${stats.length - 1}`;
  }
  stats.forEach((stat, k) => stat.add(points.offset).writeS32(k === n ? 0 : 1));
  const stat = stats[n];
  const shownTitle = writeInPlace(stat.add(name.offset), title || 'Archipelago item');
  const lines = pointerAt(stat.add(description.offset));
  const shownText = lines === null || lines.isNull() ? null :
    writeInPlace(lines, text || 'Sends a check to the multiworld.');
  log(`${nameOfObject(stat)} now reads ${JSON.stringify(shownTitle)}: ${JSON.stringify(shownText)}; ` +
      `the other ${stats.length - 1} are marked as bought`);
  return 'open the station';
};

// A native script function's C++ entry point: the one word, at the same place in every function, that points into
// the game's code and differs between the three station functions.
const stationNatives = { found: null, hooks: [] };

function findStationNatives() {
  const names = ['BeginWeaponUpgradeInteraction', 'ConfirmWeaponUpgrade', 'CancelWeaponUpgrade'];
  const fns = names.map((fname) => findFunction('ShockPlayer', fname));
  const missing = fns.find((f) => f.error);
  if (missing) {
    return { error: missing.error };
  }
  const code = mod.enumerateRanges('r-x').map((r) => [r.base.toUInt32(), r.base.toUInt32() + r.size]);
  const isCode = (value) => value !== null && code.some(([from, to]) => value >= from && value < to);
  for (let at = 0x30; at <= 0x100; at += 4) {
    const words = fns.map((f) => u32(f.function.add(at)));
    if (words.every(isCode) && new Set(words).size === 3) {
      const found = {};
      names.forEach((fname, k) => { found[fname] = ptr(words[k]); });
      log(`station natives at function+${hex(at)}: ` + names.map((fname) => `${fname} ${rel(found[fname])}`).join(', '));
      return { found };
    }
  }
  fns.forEach((f, k) => log(`  ${names[k]}: ${hexWords(f.function, 0x30, 0x100)}`));
  return { error: 'no word points into the code for all three' };
}

// Logs each call of the three; blockConfirm() then makes confirming do what cancelling does.
globalThis.stationHooks = function () {
  if (stationNatives.found === null) {
    const result = findStationNatives();
    if (result.error) {
      return result.error;
    }
    stationNatives.found = result.found;
  }
  if (stationNatives.hooks.length === 0) {
    for (const [fname, at] of Object.entries(stationNatives.found)) {
      stationNatives.hooks.push(Interceptor.attach(at, { onEnter() { log(`${fname} called`); } }));
    }
  }
  return 'watching the station: open it, pick, confirm or cancel';
};

globalThis.blockConfirm = function () {
  const hooked = globalThis.stationHooks();
  if (stationNatives.found === null) {
    return hooked;
  }
  stationNatives.hooks.forEach((hook) => hook.detach());
  stationNatives.hooks = [];
  Interceptor.flush();
  Interceptor.replace(stationNatives.found.ConfirmWeaponUpgrade, stationNatives.found.CancelWeaponUpgrade);
  for (const fname of ['BeginWeaponUpgradeInteraction', 'CancelWeaponUpgrade']) {
    stationNatives.hooks.push(Interceptor.attach(stationNatives.found[fname],
      { onEnter() { log(`${fname} called`); } }));
  }
  return 'confirming now cancels (and logs "CancelWeaponUpgrade called")';
};

// The code each station stub calls: calls and jumps to a fixed address inside the game, followed one level down.
const stationCalls = { unique: null, hooks: [] };

function callTargets(start, count) {
  const targets = [];
  let at = start;
  for (let n = 0; n < count; n++) {
    let instruction;
    try {
      instruction = Instruction.parse(at);
    } catch (e) {
      break;
    }
    const op = instruction.operands === undefined ? undefined : instruction.operands[0];
    if ((instruction.mnemonic === 'call' || instruction.mnemonic === 'jmp') && op !== undefined && op.type === 'imm') {
      const target = ptr(op.value);
      if (target.compare(base) >= 0 && target.compare(base.add(mod.size)) < 0) {
        targets.push(target);
      }
    }
    if (instruction.mnemonic === 'ret' || (instruction.mnemonic === 'jmp' && op !== undefined && op.type !== 'imm')) {
      break;
    }
    if (instruction.mnemonic === 'jmp') {
      break;
    }
    at = instruction.next;
  }
  return targets;
}

globalThis.stationCode = function () {
  if (stationNatives.found === null) {
    const result = findStationNatives();
    if (result.error) {
      return result.error;
    }
    stationNatives.found = result.found;
  }
  const calls = {};
  for (const [fname, at] of Object.entries(stationNatives.found)) {
    log(`${fname} at ${rel(at)}:`);
    disassemble(at, 40).forEach((line) => log(`  ${line}`));
    calls[fname] = callTargets(at, 40).map((t) => t.toString());
    log(`  calls ${calls[fname].map((t) => rel(ptr(t))).join(', ') || 'nothing at a fixed address'}`);
  }
  // A target only one stub calls is that stub's own C++ method; the shared ones are the interpreter's helpers.
  const counts = new Map();
  Object.values(calls).forEach((list) => new Set(list).forEach((t) => counts.set(t, (counts.get(t) || 0) + 1)));
  stationCalls.unique = {};
  for (const [fname, list] of Object.entries(calls)) {
    const own = [...new Set(list)].filter((t) => counts.get(t) === 1);
    stationCalls.unique[fname] = own.map((t) => ptr(t));
    for (const t of own) {
      log(`${fname} -> ${rel(ptr(t))}:`);
      disassemble(ptr(t), 80).forEach((line) => log(`  ${line}`));
      log(`  calls ${callTargets(ptr(t), 80).map(rel).join(', ')}`);
    }
  }
  stationCalls.hooks.forEach((hook) => hook.detach());
  stationCalls.hooks = [];
  for (const [fname, own] of Object.entries(stationCalls.unique)) {
    own.forEach((t) => stationCalls.hooks.push(Interceptor.attach(t,
      { onEnter() { log(`${fname}'s ${rel(t)} called`); } })));
  }
  return 'printed; now open the station, pick, confirm, and send the log';
};

globalThis.blockDeep = function () {
  if (stationCalls.unique === null) {
    return 'run stationCode() first';
  }
  const confirm = stationCalls.unique.ConfirmWeaponUpgrade;
  const cancel = stationCalls.unique.CancelWeaponUpgrade;
  if (confirm.length !== 1 || cancel.length !== 1) {
    return `confirm calls ${confirm.length} and cancel ${cancel.length} functions of their own; ` +
      'not sure which to swap, send the log';
  }
  stationCalls.hooks.forEach((hook) => hook.detach());
  stationCalls.hooks = [];
  Interceptor.flush();
  Interceptor.replace(confirm[0], cancel[0]);
  stationCalls.hooks.push(Interceptor.attach(cancel[0], { onEnter() { log(`C++ cancel ${rel(cancel[0])} called`); } }));
  return `C++ confirm ${rel(confirm[0])} now runs C++ cancel ${rel(cancel[0])}`;
};

// Points stat n's StatName at a name no weapon uses (APCheckNothing, or Testing when that is not a name yet), so whatever the claim upgrades by that
// name, nothing on the weapon changes.
globalThis.deadStat = function (n, nameText = 'APCheckNothing') {
  const stats = weaponStats();
  const learned = learnAnyClass('UpgradeableWeaponStat');
  if (learned.error) {
    return learned.error;
  }
  const field = learned.properties.get('StatName');
  if (n < 0 || n >= stats.length || field === undefined) {
    return field === undefined ? 'StatName not found' : `pick n from 0 to ${stats.length - 1}`;
  }
  let index = indexOfName(nameText);
  if (index < 0) {
    index = indexOfName('Testing'); // in the name table: ShockPlayer logs with it
  }
  if (index < 0) {
    return `neither ${nameText} nor Testing is in the name table`;
  }
  const at = stats[n].add(field.offset);
  const before = nameOf(at.readS32());
  at.writeS32(index);
  log(`${nameOfObject(stats[n])}: StatName ${before} -> ${nameOf(index)} (put back with deadStat(${n}, '${before}'))`);
  return 'set';
};

// Every script function the game runs goes through the function DeathLink calls (module+0x736070): the object in
// ecx, and one argument, the call's frame, with the function at frame+4.
const SCRIPT_RUN = 0x736070;
const SCRIPT_RUN_START = 0x81EC8B55; // push ebp; mov ebp, esp; sub esp, ...
const scriptTrace = { hook: null, started: 0, warm: null, counts: null, names: new Map(), skip: null, replaced: false };

function scriptRunAt() {
  const at = base.add(SCRIPT_RUN);
  return u32(at) === SCRIPT_RUN_START ? at : null;
}

function functionLabel(fn) {
  let label = scriptTrace.names.get(fn.toString());
  if (label === undefined) {
    label = nameOfObject(fn) || `function at ${fn}`;
    scriptTrace.names.set(fn.toString(), label);
  }
  return label;
}

globalThis.traceCalls = function () {
  const at = scriptRunAt();
  if (at === null) {
    return `module+${hex(SCRIPT_RUN)} does not start the way it should; is this the Steam build?`;
  }
  if (scriptTrace.hook !== null) {
    return 'already tracing; traceStop() first';
  }
  scriptTrace.started = Date.now();
  scriptTrace.warm = new Set();
  scriptTrace.counts = new Map();
  scriptTrace.hook = Interceptor.attach(at, {
    onEnter(args) {
      const fn = pointerAt(args[0].add(4));
      if (fn === null) {
        return;
      }
      const label = functionLabel(fn);
      scriptTrace.counts.set(label, (scriptTrace.counts.get(label) || 0) + 1);
      if (Date.now() - scriptTrace.started < 3000) {
        scriptTrace.warm.add(label);
      } else if (!scriptTrace.warm.has(label) && scriptTrace.counts.get(label) <= 3) {
        log(`call: ${label} on ${nameOfObject(this.context.ecx)} (${classNameOf(this.context.ecx)})`);
      }
    },
  });
  setTimeout(() => {
    if (scriptTrace.hook !== null) {
      log(`trace: learned ${scriptTrace.warm.size} functions the game runs all the time; open the station now`);
    }
  }, 3000);
  return 'tracing: wait for the "open the station now" line (about three seconds), then open it, pick, confirm';
};

globalThis.traceStop = function () {
  if (scriptTrace.hook === null) {
    return 'not tracing';
  }
  scriptTrace.hook.detach();
  scriptTrace.hook = null;
  const rare = [...scriptTrace.counts.entries()].filter(([label]) => !scriptTrace.warm.has(label));
  log(`trace: ${rare.length} functions that were not running at the start:`);
  rare.forEach(([label, count]) => log(`  ${label}: ${count}`));
  return 'stopped';
};

globalThis.skipCall = function (functionName) {
  const at = scriptRunAt();
  if (at === null) {
    return `module+${hex(SCRIPT_RUN)} does not start the way it should; is this the Steam build?`;
  }
  scriptTrace.skip = functionName || null;
  if (!scriptTrace.replaced && scriptTrace.skip !== null) {
    const original = new NativeFunction(at, 'void', ['pointer', 'pointer'], 'thiscall');
    Interceptor.replace(at, new NativeCallback(function (self, frame) {
      const fn = pointerAt(frame.add(4));
      if (scriptTrace.skip !== null && fn !== null && nameOfObject(fn) === scriptTrace.skip) {
        log(`skipped ${functionLabel(fn)} on ${nameOfObject(self)}`);
        return;
      }
      original(self, frame);
    }, 'void', ['pointer', 'pointer'], 'thiscall'));
    scriptTrace.replaced = true;
  }
  return scriptTrace.skip === null ? 'nothing skipped' : `the game now skips ${scriptTrace.skip}`;
};

// ---- Choice: which C++ code asks for the weapon when a station upgrade is confirmed ----

// Logs the native call chain each time the game runs the named script function (by default the one the station's
// confirm asks first). The first frames inside the game's module that are not the script interpreter are the
// station menu's own C++ confirm: once known, it can be replaced the way Touch replaces the menu opener, so that
// confirming sends a check instead of applying the vanilla upgrade.
const callers = { hook: null, wanted: null, seen: 0 };

globalThis.whoCalls = function (functionName = 'GetHoldableByClassName', limit = 3) {
  const at = scriptRunAt();
  if (at === null) {
    return `module+${hex(SCRIPT_RUN)} does not start the way it should; is this the Steam build?`;
  }
  if (callers.hook !== null) {
    callers.hook.detach();
  }
  callers.wanted = functionName;
  callers.seen = 0;
  callers.hook = Interceptor.attach(at, {
    onEnter(args) {
      const fn = pointerAt(args[0].add(4));
      if (fn === null || callers.seen >= limit || nameOfObject(fn) !== callers.wanted) {
        return;
      }
      callers.seen += 1;
      let frames;
      try {
        frames = Thread.backtrace(this.context, Backtracer.ACCURATE);
      } catch (e) {
        frames = Thread.backtrace(this.context, Backtracer.FUZZY);
      }
      log(`${callers.wanted} on ${nameOfObject(this.context.ecx)} (${classNameOf(this.context.ecx)}), called from:`);
      frames.slice(0, 16).forEach((frame, k) => log(`  #${k} ${rel(frame)}`));
      if (callers.seen >= limit) {
        log(`whoCalls: logged ${limit}; whoCallsStop() or whoCalls() again for more`);
      }
    },
  });
  return `watching ${functionName}: open a station, pick an upgrade, confirm, then send the log`;
};

globalThis.whoCallsStop = function () {
  if (callers.hook === null) {
    return 'not watching';
  }
  callers.hook.detach();
  callers.hook = null;
  return 'stopped';
};

// ---- Touch: a station that sends its check and closes, with no menu ----

// UShockPlayer::BeginWeaponUpgradeInteraction, which opens the menu (what the script native's stub calls).
const OPEN_UPGRADE_MENU = 0x1dbf70;
const OPEN_UPGRADE_MENU_START = 0x64EC8B55; // push ebp; mov ebp, esp; mov eax, fs:[0]
const touching = { replaced: false, finish: null };

function stationInUse() {
  const learned = learnAnyClass('PlaceableWeaponUpgradeStation');
  const current = learned.error ? undefined : learned.properties.get('CurrentPlayer');
  if (current === undefined) {
    return null;
  }
  const list = matchingObjects('^PlaceableWeaponUpgradeStation$').get('PlaceableWeaponUpgradeStation') || [];
  return list.find((station) => {
    const player = pointerAt(station.add(current.offset));
    return player !== null && !player.isNull();
  }) || null;
}

function stationUses(station) {
  const used = learnAnyClass('PlaceableWeaponUpgradeStation').properties.get('NumTimesUsed');
  return used === undefined ? null : station.add(used.offset).readS32();
}

// The state an object is in: its state frame (a pointer in the object's first words) holds the current state, an
// object of class State, a few words in.
function stateOf(object) {
  for (let at = 4; at < objects.nameOffset; at += 4) {
    const frame = pointerAt(object.add(at));
    if (frame === null || frame.isNull() || !readable(frame, 0x20)) {
      continue;
    }
    for (let k = 4; k < 0x20; k += 4) {
      const state = pointerAt(frame.add(k));
      if (state !== null && !state.isNull() && readable(state, 0x40) && classNameOf(state) === 'State') {
        return { state, where: `object+${hex(at)} -> +${hex(k)}` };
      }
    }
  }
  return null;
}

function finishTouchedStation() {
  const station = stationInUse();
  if (station === null) {
    log('touch: the menu was skipped, but no station has a CurrentPlayer');
    return;
  }
  let { found } = touching.finish;
  const current = stateOf(station);
  if (current === null) {
    log(`touch: could not tell the station's state; its first words: ${hexWords(station, 0, objects.nameOffset)}`);
  } else {
    const inState = findFunctionFrom(current.state, 'FinishInteraction');
    log(`touch: the station is in state ${nameOfObject(current.state)} (${current.where}); ` +
      `${inState === null ? 'no FinishInteraction of its own' : `its FinishInteraction is at ${inState.function}`} ` +
      `(the class's is at ${found.function})`);
    if (inState !== null) {
      found = inState;
    }
  }
  const size = Math.max(found.size, 0x40);
  const block = Memory.alloc(size);
  for (let at = 0; at < size; at += 4) {
    block.add(at).writeU32(0);
  }
  // A success flag, if FinishInteraction has one, is true: the station was used.
  for (const p of found.parameters.values()) {
    if (p.kind === 'BoolProperty') {
      block.add(p.offset).writeU32(1);
    }
  }
  const before = stationUses(station);
  callScript(station, found.function, block);
  const after = stateOf(station);
  log(`touch: FinishInteraction called on the station at ${station}; NumTimesUsed ${before} -> ${stationUses(station)}, ` +
    `state now ${after === null ? '?' : nameOfObject(after.state)}`);
  block.isNull();
  setTimeout(() => {
    const player = stationInUse();
    log(`touch: a second later, NumTimesUsed ${stationUses(station)}, ` +
      `${player === null ? 'no station in use' : 'a station is still in use'}. Is it closed, and are the controls back?`);
  }, 1000);
}

globalThis.touch = function () {
  if (processEvent.call === null) {
    return 'run events() first, and wait for its report';
  }
  const at = base.add(OPEN_UPGRADE_MENU);
  if (u32(at) !== OPEN_UPGRADE_MENU_START) {
    return `module+${hex(OPEN_UPGRADE_MENU)} does not start the way it should; is this the Steam build?`;
  }
  const found = findFunction('PlaceableWeaponUpgradeStation', 'FinishInteraction');
  if (found.error) {
    return found.error;
  }
  const described = [...found.parameters.entries()].map(([name, p]) => `${name} +${hex(p.offset)} ${p.kind}`);
  log(`touch: ${found.owner}.FinishInteraction(${described.join(', ')})`);
  touching.finish = { found };
  if (!touching.replaced) {
    Interceptor.replace(at, new NativeCallback(function () {
      log('touch: the station tried to open its menu; skipped');
      runOnGameThread(finishTouchedStation);
    }, 'void', ['pointer'], 'thiscall'));
    touching.replaced = true;
  }
  return 'use a station now: no menu should open, and it should close';
};

// ---- calling the game's script functions, for DeathLink ----

// A script function of a class or the classes it extends, and its parameters (offsets into the parameter block).
function findFunction(className, functionName) {
  if (objects.layout === null || objects.propertyOffset === null) {
    return { error: 'the layout of classes is not known (Quest was not learned)' };
  }
  if (objects.superOffset === undefined) {
    objects.superOffset = findSuperOffset();
  }
  const found = findFunctionFrom(findClass(className), functionName);
  return found === null ? { error: `${className} has no function ${functionName}` } : found;
}

// The same, starting from any class or state and going up through the ones it extends. A state's version of a
// function is what the game runs while the object is in that state.
function findFunctionFrom(start, functionName) {
  let cls = start;
  for (let depth = 0; cls !== null && !cls.isNull() && depth < 30; depth++) {
    let field = pointerAt(cls.add(objects.layout.childrenOffset));
    for (let n = 0; field !== null && !field.isNull() && n < 5000 && readable(field, 0x80); n++) {
      if (nameOfObject(field) === functionName && classNameOf(field) === 'Function') {
        const parameters = new Map();
        let size = 0;
        let parameter = pointerAt(field.add(objects.layout.childrenOffset));
        for (let k = 0; parameter !== null && !parameter.isNull() && k < 40 && readable(parameter, 0x80); k++) {
          const kind = classNameOf(parameter) || '?';
          if (/Property$/.test(kind)) {
            const offset = u32(parameter.add(objects.propertyOffset));
            parameters.set(nameOfObject(parameter), { offset, kind });
            size = Math.max(size, offset + 16);
          }
          parameter = pointerAt(parameter.add(objects.layout.nextOffset));
        }
        return { function: field, owner: nameOfObject(cls), parameters, size };
      }
      field = pointerAt(field.add(objects.layout.nextOffset));
    }
    if (objects.superOffset === null) {
      break;
    }
    cls = pointerAt(cls.add(objects.superOffset));
  }
  return null;
}

const VTABLE_SLOTS = 48;
const PAWN_HEALTH = 0x57C; // Pawn.Health
const processEvent = { target: null, slot: null, call: null, template: null };

globalThis.events = function () {
  if (objects.table === null) {
    return 'no object table';
  }
  const player = thePlayer();
  if (player === null) {
    return `could not find the one ${PLAYER_CLASS}: load a save first`;
  }
  const vtable = pointerAt(player);
  const code = mod.enumerateRanges('r-x');
  const inCode = (p) => code.some((r) => p.compare(r.base) >= 0 && p.compare(r.base.add(r.size)) < 0);
  const slots = new Map(); // target -> {slots, calls, functions, names}
  for (let slot = 0; slot < VTABLE_SLOTS; slot++) {
    const target = pointerAt(vtable.add(slot * 4));
    if (target === null || !inCode(target)) {
      log(`  vtable slot ${slot} is not code; the table ends at ${slot}`);
      break;
    }
    const key = target.toString();
    if (slots.has(key)) {
      slots.get(key).slots.push(slot);
      continue;
    }
    const entry = { target, slots: [slot], calls: 0, functions: 0, names: new Map(), callers: new Map(),
      listener: null };
    slots.set(key, entry);
    try {
      entry.listener = Interceptor.attach(target, {
        onEnter(args) {
          entry.calls++;
          const first = pointerAt(this.context.esp.add(4));
          if (first !== null && !first.isNull() && readable(first, 0x40) && classNameOf(first) === 'Function') {
            entry.functions++;
            const name = nameOfObject(first) || '?';
            entry.names.set(name, (entry.names.get(name) || 0) + 1);
            const from = this.returnAddress.toString();
            entry.callers.set(from, (entry.callers.get(from) || 0) + 1);
          }
        },
      });
    } catch (e) {
      log(`  vtable slot ${slot} (${rel(target)}) cannot be watched: ${e.message}`);
    }
  }
  setTimeout(() => {
    let best = null;
    for (const entry of slots.values()) {
      if (entry.listener !== null) {
        entry.listener.detach();
      }
      if (entry.functions > 0) {
        const names = [...entry.names.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8)
          .map(([name, count]) => `${name} ${count}`).join(', ');
        log(`  slot ${entry.slots.join('/')} at ${rel(entry.target)}: ${entry.calls} calls, ` +
          `${entry.functions} with a script function first: ${names}`);
        if (entry.functions === entry.calls && (best === null || entry.functions > best.functions)) {
          best = entry;
        }
      }
    }
    if (best === null) {
      log('events: no virtual function of the player is called with a script function first. ' +
        'Was the game paused? Please send this log.');
      return;
    }
    // On this build that slot is ProcessRemoteFunction (it only decides whether a call goes over the network and
    // returns). ProcessEvent is the function that calls it: found from where those calls return to.
    const slotOf = new Map();
    for (const entry of slots.values()) {
      entry.slots.forEach((slot) => slotOf.set(entry.target.toString(), slot));
    }
    const callers = [...best.callers.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4);
    let chosen = null;
    for (const [from, count] of callers) {
      const start = functionStart(ptr(from));
      const slot = start === null ? undefined : slotOf.get(start.toString());
      log(`  slot ${best.slots[0]} is called from ${rel(ptr(from))} (${count} times), in the function at ` +
        `${start === null ? '?' : rel(start)}${slot === undefined ? '' : `, vtable slot ${slot}`}`);
      // The busiest caller: ProcessEvent, called for every event. The other one, vtable slot 25 on this build, is
      // CallFunction (script calling script), which takes a stack frame rather than parameters.
      if (start !== null && chosen === null) {
        chosen = { start, slot };
      }
    }
    if (chosen === null) {
      log('events: could not tell which function calls it. Please send this log.');
      return;
    }
    for (const line of disassemble(chosen.start, 60)) {
      log(`    ${line}`);
    }
    // It takes one argument: a block with the function at +4 and the parameters at +0x10 (a stack frame, as the
    // script VM keeps them). Copy what the game itself passes in, from one real call.
    let sampler = null;
    sampler = Interceptor.attach(chosen.start, {
      onEnter() {
        if (processEvent.template !== null) {
          return;
        }
        const info = pointerAt(this.context.esp.add(4));
        if (info === null || !readable(info, 0x30)) {
          return;
        }
        const fn = pointerAt(info.add(4));
        if (fn === null || classNameOf(fn) !== 'Function') {
          return;
        }
        const self = this.context.ecx;
        const words = [];
        for (let at = 0; at < 0x30; at += 4) {
          const word = pointerAt(info.add(at));
          words.push(word === null ? ptr(0) : word);
        }
        processEvent.template = { words, self, fn, name: nameOfObject(fn) };
      },
    });
    setTimeout(() => {
      sampler.detach();
      const template = processEvent.template;
      if (template === null) {
        log('events: saw no call to copy. Was the game paused? Please send this log.');
        return;
      }
      const shown = template.words.map((w, i) => {
        const at = hex(i * 4);
        if (w.equals(template.self)) {
          return `+${at} the object`;
        }
        const kind = w.isNull() || !readable(w, 0x40) ? null : classNameOf(w);
        return `+${at} ${w}${kind === null ? '' : ` (${kind})`}`;
      });
      log(`events: a real call (${template.name}) passes: ${shown.join(', ')}`);
      processEvent.target = chosen.start;
      processEvent.slot = chosen.slot === undefined ? null : chosen.slot;
      processEvent.call = new NativeFunction(chosen.start, 'void', ['pointer', 'pointer'], 'thiscall');
      log(`events: script functions are called through ${rel(processEvent.target)}. check() can be tried now.`);
    }, 300);
  }, 1000);
  return `watching ${slots.size} virtual functions of the player for one second; the report follows`;
};

// The start of the function an address is in: functions here begin on a 16-byte boundary after int3 padding,
function functionStart(address) {
  let at = address.and(ptr('0xFFFFFFF0'));
  for (let n = 0; n < 0x400; n++, at = at.sub(16)) {
    if (!readable(at.sub(1), 2)) {
      return null;
    }
    // ... and start by setting up a frame (push ebp; mov ebp, esp), as the functions around here do
    if (at.sub(1).readU8() === 0xCC && at.readU8() === 0x55 && at.add(1).readU8() === 0x8B &&
        at.add(2).readU8() === 0xEC) {
      return at;
    }
  }
  return null;
}

const FUNCTION_SCRIPT = 0x70; // a function's bytecode: data, count, capacity

// Runs a script function on an object, the way the game's own events do: a frame like the one it passes, with this
// function and these parameters. Game thread only.
function callScript(object, fn, parms) {
  const frame = Memory.alloc(0x40);
  processEvent.template.words.forEach((word, i) => {
    frame.add(i * 4).writePointer(word.equals(processEvent.template.self) ? object : word);
  });
  frame.add(4).writePointer(fn);
  frame.add(0x10).writePointer(parms);
  // A word pointing into the copied call's own bytecode points into this function's instead
  const theirs = pointerAt(processEvent.template.fn.add(FUNCTION_SCRIPT));
  const theirLength = u32(processEvent.template.fn.add(FUNCTION_SCRIPT + 4)) || 0;
  const ours = pointerAt(fn.add(FUNCTION_SCRIPT));
  processEvent.template.words.forEach((word, i) => {
    if (theirs !== null && ours !== null && word.compare(theirs) >= 0 && word.compare(theirs.add(theirLength + 1)) < 0) {
      frame.add(i * 4).writePointer(ours.add(word.sub(theirs)));
    }
  });
  processEvent.call(object, frame);
  frame.isNull(); // keeps it alive until the call is done
}

// Calls waiting for the game thread: the game's script must only ever run there.
const onGameThread = [];
const DELTA_FUNCTION = 0x53D850; // the game-thread function the agent hooks on the Steam build
let gameThreadHook = null;

function runOnGameThread(job) {
  if (gameThreadHook === null) {
    gameThreadHook = Interceptor.attach(base.add(DELTA_FUNCTION), {
      onEnter() {
        while (onGameThread.length > 0) {
          const next = onGameThread.shift();
          try {
            next();
          } catch (e) {
            log(`on the game thread: ${e.message}`);
          }
        }
      },
    });
  }
  onGameThread.push(job);
}

globalThis.check = function () {
  if (processEvent.call === null) {
    return 'run events() first, and wait for its report';
  }
  const player = thePlayer();
  if (player === null) {
    return `could not find the one ${PLAYER_CLASS}: load a save first`;
  }
  const found = findFunction(PLAYER_CLASS, 'SetInvincible');
  if (found.error) {
    return found.error;
  }
  const flag = learnAnyClass(PLAYER_CLASS).properties.get('bIsInvincible');
  if (flag === undefined) {
    return 'ShockPlayer has no bIsInvincible property here';
  }
  const parameter = [...found.parameters.values()][0];
  const read = () => ((player.add(flag.offset).readU32() & flag.mask) !== 0);
  log(`check: bIsInvincible at +${hex(flag.offset)} bit ${hex(flag.mask)}, now ${read()}; ` +
    `SetInvincible's parameter at +${hex(parameter.offset)}`);
  const block = Memory.alloc(0x40);
  runOnGameThread(() => {
    for (const value of [1, 0]) {
      for (let at = 0; at < 0x40; at += 4) {
        block.add(at).writeU32(0);
      }
      block.add(parameter.offset).writeU32(value);
      callScript(player, found.function, block);
      log(`check: after SetInvincible(${value === 1}), bIsInvincible is ${read()}`);
    }
    block.isNull();
  });
  return 'queued for the game thread; the result follows';
};

function disassemble(start, count) {
  const lines = [];
  let at = start;
  for (let n = 0; n < count; n++) {
    let instruction;
    try {
      instruction = Instruction.parse(at);
    } catch (e) {
      lines.push(`${rel(at)}: ?? (${e.message})`);
      break;
    }
    lines.push(`${rel(at)}: ${instruction.toString()}`);
    at = instruction.next;
  }
  return lines;
}

globalThis.peek = function () {
  if (processEvent.call === null) {
    return 'run events() first, and wait for its report';
  }
  log(`peek: the start of ${rel(processEvent.target)}`);
  for (const line of disassemble(processEvent.target, 120)) {
    log(`  ${line}`);
  }
  for (const [className, functionName] of [[PLAYER_CLASS, 'SetInvincible'], [PLAYER_CLASS, 'OnKilled'],
    [PLAYER_CLASS, 'TakeSimpleDamage'], [PLAYER_CLASS, 'GetActiveHoldable'], [PLAYER_CLASS, 'Tick'],
    ['HUD', 'PostRender'], [PLAYER_CLASS, 'GetHealth']]) {
    const found = findFunction(className, functionName);
    if (found.error) {
      log(`  ${found.error}`);
      continue;
    }
    log(`  ${found.owner}.${functionName} at ${found.function}: ${hexWords(found.function, 0, 0xC0)}`);
  }
  return 'done';
};

globalThis.die = function () {
  if (processEvent.call === null) {
    return 'run events() first, and wait for its report';
  }
  const player = thePlayer();
  if (player === null) {
    return `could not find the one ${PLAYER_CLASS}: load a save first`;
  }
  const found = findFunction(PLAYER_CLASS, 'OnKilled');
  if (found.error) {
    return found.error;
  }
  const described = [...found.parameters.entries()].map(([name, p]) => `${name} +${hex(p.offset)} ${p.kind}`);
  log(`${found.owner}.OnKilled(${described.join(', ')})`);
  const size = Math.max(found.size, 0x80);
  const block = Memory.alloc(size);
  for (let at = 0; at < size; at += 4) {
    block.add(at).writeU32(0); // no damage set, no damager, no hit: all None
  }
  runOnGameThread(() => {
    const before = player.add(PAWN_HEALTH).readFloat().toFixed(1);
    player.add(PAWN_HEALTH).writeFloat(0);
    callScript(player, found.function, block);
    log(`die: health ${before} -> 0, OnKilled called`);
    block.isNull();
    setTimeout(() => {
      log(`die: a few seconds later, health ${player.add(PAWN_HEALTH).readFloat().toFixed(1)}`);
    }, 5000);
  });
  return 'queued for the game thread; the result follows';
};

globalThis.hurt = function (amount = 10, functionName = 'TakeSimpleDamage', type = 36) {
  if (processEvent.call === null) {
    return 'run events() first, and wait for its report';
  }
  const player = thePlayer();
  if (player === null) {
    return `could not find the one ${PLAYER_CLASS}: load a save first`;
  }
  const found = findFunction(PLAYER_CLASS, functionName);
  if (found.error) {
    return found.error;
  }
  const described = [...found.parameters.entries()].map(([name, p]) => `${name} +${hex(p.offset)} ${p.kind}`);
  log(`${found.owner}.${functionName}(${described.join(', ')})`);
  const wanted = { DamageType: type, DamageAmount: amount, DamageChance: 1, numHealth: amount };
  const block = Memory.alloc(Math.max(found.size, 0x40));
  for (let at = 0; at < Math.max(found.size, 0x40); at += 4) {
    block.add(at).writeU32(0);
  }
  for (const [name, p] of found.parameters) {
    if (!(name in wanted)) {
      continue; // Damager: none
    }
    if (p.kind === 'ByteProperty') {
      block.add(p.offset).writeU8(wanted[name]);
    } else if (p.kind === 'FloatProperty') {
      block.add(p.offset).writeFloat(wanted[name]);
    } else {
      return `${name} is a ${p.kind}; not sure what to put there`;
    }
  }
  runOnGameThread(() => {
    const before = player.add(PAWN_HEALTH).readFloat().toFixed(1);
    callScript(player, found.function, block);
    const after = player.add(PAWN_HEALTH).readFloat().toFixed(1);
    log(`hurt: called ${functionName}(${type}, ${amount}); health ${before} -> ${after}`);
    block.isNull(); // keeps the parameter block alive until the call is done
    setTimeout(() => {
      log(`hurt: a second later, health ${player.add(PAWN_HEALTH).readFloat().toFixed(1)}`);
    }, 1000);
  });
  return 'queued for the game thread; the result follows';
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

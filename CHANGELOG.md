# Changelog

## 0.2.0 (first release)

The first build of the BioShock Remastered APWorld meant for players. It needs Archipelago 0.6.7 or newer, the Steam
version of BioShock Remastered on Windows, and Python 3 with `pip install frida` (the
[setup guide](apworld/bioshock/docs/setup_en.md) explains why).

Every part below has been seen working in the real game. No whole seed has been played start to finish yet, so
expect rough edges, and please report what you find.

### Checks

- 28 story objectives and bosses, 21 Little Sisters (rescued or harvested), 12 Power to the People stations and
  12 Level Complete checks, all on by default.
- 122 audio diaries: filler only by default, or `off`, or `all`. Diaries picked up while the client was
  disconnected are sent when a save is loaded.
- Optional: Gatherer's Garden lines (Shop Checks: 61 first appearances or all 273), plasmids, tonics and weapons
  lying in the levels (World Item Checks: keep their item, or shuffled into the multiworld), the 41 wall safes
  (Safe Checks) and research levels (Research Checks: 11 or 55).

### Items and the game

- Items arrive straight into the game through the client: ADAM, plasmids, tonics, slots, Health and EVE upgrades,
  weapon upgrades, ammo, money and more, with the game's own equip window.
- Power to the People stations open no menu: using one sends its check, and the 12 weapon upgrades come from the
  multiworld and wait until you hold their weapon.
- Garden lines that are checks are renamed and priced after the item they hold, with its icon or the Archipelago
  icon.
- Traps: EVE Drain, Pickpocket and Security Alarm (Trap Chance, off by default).
- DeathLink, both ways: a received death kills Jack the game's own way, and he wakes in a Vita-Chamber.
- Start With Research Camera (on by default).

### Level access

- Access Items (default): a level's checks are sent once you hold its access item; anything found earlier is sent
  when the item arrives.
- Open Rapture (experimental): access items open the bathysphere one chapter at a time, in any order the seed
  allows, and level-change doors go where the seed says.

### Tracking

- Universal Tracker: logic, plus a map tab drawn from the game's own map screen with a pin on every check that has
  a place in a level, and a dot where you stand while the client is connected.

### Known issues

- Open Rapture: the first bathysphere menu in a level can show the vanilla list. Close it and pull the switch again.
- The dollars counter does not flash when the Pickpocket trap takes money; the amount is right.
- Not tried in the real game yet: the Gatherer's Gardens after Neptune's Bounty, sending the goal when Fontaine is
  defeated, and a whole seed start to finish.
- Steam only: the GOG and Epic versions are not supported.

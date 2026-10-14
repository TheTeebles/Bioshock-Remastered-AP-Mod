# BioShock

## Where is the options page?

The [player options page for this game](../player-options) contains all the options you need to configure and export a
config file.

## What does randomization do to this game?

You play BioShock Remastered (PC) from Welcome to Rapture to Frank Fontaine as normal. Story objectives, Little
Sisters and a few other things you do along the way send checks to the multiworld, and items from the multiworld are
delivered straight into your inventory.

With the default **Level Access** setting, every level after Medical Pavilion has an access item (for example
"Arcadia Access"). You can still play the story straight through, but a level's checks are only sent once you hold its
access item. Anything you already collected there is sent the moment the access item arrives, so you never need to go
back for it.

## What counts as a check?

These can hold anything, including the items you and other players need:

- 28 story objectives and bosses, for example defeating Dr. Steinman, photographing the Spider Splicers or completing
  Cohen's masterpiece. All of them are part of finishing the game, so you cannot miss one.
- Rescuing or harvesting each of the 21 Little Sisters
- Using each of the 12 Power to the People weapon upgrade stations. A station opens no upgrade menu: using it sends
  its check and closes it, and the 12 weapon upgrades are items in the multiworld instead. An upgrade for a weapon
  you don't have yet waits until you pick the weapon up.
- Finishing each level for the first time (12 checks)

The 122 Audio Diaries are extras. By default they are checks that only ever hold filler such as ADAM and dollars, or
traps, so nobody ever has to hunt for one. They can also be switched off, or set to "all" if you do want important
items on them. Every other kind of check can be switched off as well.

Shop Checks (off by default) make each gene upgrade a Gatherer's Garden sells a check. They can hold useful items
but never anything needed to progress, since the ADAM to buy them depends on how you play. Only the Neptune's
Bounty Garden is mapped so far (16 checks). Each of its lines is renamed after the multiworld item it holds. Your
own plasmids and tonics keep their usual price; other items cost 80 ADAM if they are someone's progression item, 40
if useful and 20 otherwise. Buying a line sends its check and gives you a bag of chips instead of the gene.

Welcome to Rapture can never be revisited, so its two audio diaries only hold filler whatever you choose.

The Remastered Director's Commentary film reels are not checks. A reel unlocks its film for the whole PC rather than
the save, so a reel found in an earlier playthrough would not be there to pick up.

## Which items can I receive?

- Level access items (progression)
- Plasmids and plasmid upgrades that are normally bought or gifted (Winter Blast, Insect Swarm, Cyclone Trap, Enrage!,
  Target Dummy, Sonic Boom, Hypnotize Big Daddy, Electro Bolt 2/3, Incinerate! 2/3)
- Gene tonics that are normally bought, crafted, researched or gifted (up to 28 tonics)
- Health Upgrades, EVE Upgrades, Plasmid Slots and tonic slots
- Filler: ADAM, dollars, First Aid Kits, EVE Hypos, Auto-Hack Tools, ammo, film and invention components
- Optional traps: EVE Drain, Pickpocket and Security Alarm

Important items are never placed on a filler-only check, so the world only adds as many of them as its other checks
can hold. With the default options that is every access item, plasmid and upgrade plus 15 of the 28 tonics, picked at
random for each seed. With fewer checks switched on, tonics are left out first, then upgrades, then plasmids. Tonics
that are left out can still be bought, invented or researched in the game as usual.

Traps take the place of filler, so they turn up where filler does. With audio diary checks switched off your own pool
has little or no filler, and then little or nothing becomes a trap.

Plasmids, tonics and weapons that you pick up at a fixed spot in the world stay where they are for now. The game hands
those out itself, so shuffling them has to wait until the client can suppress vanilla pickups.

## What is the goal?

Defeat Frank Fontaine at the end of Proving Grounds. With Level Access on, you also need Point Prometheus Access.

## Status

This world is in early development. Seeds can be generated, and the BioShock Client (in the Archipelago Launcher)
connects to a multiworld, receives items, holds and sends checks and reports the goal. On the Steam version, items
have been put into the real game by hand; the client doing that by itself is built and has not been run against the
game yet. Nothing notices most of what you do in the game so far (story objectives, Little Sisters, upgrade
stations, diaries), so for now a seed can only be played end to end with the client's simulated game. See the setup
guide.

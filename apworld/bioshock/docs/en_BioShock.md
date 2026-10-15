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

**Open Rapture** (Level Access: `open_rapture`, not playable yet) goes further: access items open travel itself, one
per chapter. After Medical Pavilion, the bathysphere takes you to Neptune's Bounty, Arcadia or Fort Frolic in any
order, as soon as you hold that chapter's access item. Hephaestus opens once you hold its item and the three before it,
and Olympus Heights and Point Prometheus follow in story order, each behind its own item. Each one appears in the
bathysphere menu as soon as its item arrives. Olympus Heights has no station of its own, so it appears on Apollo
Square's line until you have been there once. A way into a chapter you
can't enter yet (the submarine out of Smuggler's Hideout, the way out of Ryan's office) takes you back to Medical
Pavilion's bathysphere instead.

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

Shop Checks (off by default) make the gene upgrades the Gatherer's Gardens sell checks. They can hold useful items
but never anything needed to progress, since the ADAM to buy them depends on how you play. With First Appearances,
a line is a check only in the first Garden that sells its gene (61 checks) and repeats are sold out; with All,
every line of every level's Garden is a check (273). Each line that is a check is renamed after the multiworld
item it holds. Your
own BioShock items cost what the game charges for them; other items cost 80 ADAM if they are useful and 20
otherwise. Your own items show their own icon (a weapon upgrade shows its weapon) and other items show the
Archipelago icon. Buying a line sends its
check and gives you a bag of chips instead of the gene.

World Item Checks and Safe Checks (both off by default, and not tried in the game yet) make what lies in the levels
checks. World Item Checks covers the 31 plasmids and tonics lying in the levels and the first time you hold each
of the 7 weapons (6 when you start with the Research Camera). With Keep Items every pickup still gives its own item
as well; with Shuffled the plasmid and tonic pickups give a bag of chips instead and their items go into the
multiworld, except Electro Bolt and Incinerate!, which the story needs. Safe Checks makes opening each of the 64
safes and lockboxes a check; their contents stay as they are.

Welcome to Rapture can never be revisited, so its two audio diaries and its Electro Bolt only hold filler whatever
you choose.

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

Plasmids, tonics and weapons that you pick up at a fixed spot in the world stay where they are unless World Item
Checks is set to Shuffled. Then the plasmids and tonics lying in the levels (all but Electro Bolt and Incinerate!)
are items in the multiworld, and all of them are always in the pool. Weapons always stay where they are.

You start with the Research Camera (the `start_with_research_camera` option, on by default), so you can research
enemies from the start. Some later ones, such as spider splicers, are hard to hurt without it. The camera in Neptune's
Bounty is still there and gives film.

## What is the goal?

Defeat Frank Fontaine at the end of Proving Grounds. With Level Access on, you also need Point Prometheus Access.

## Status

This world is in early development. Seeds can be generated, and the BioShock Client (in the Archipelago Launcher)
connects to a multiworld, puts items into the real game by itself (Steam version) and notices every kind of check
there: story objectives, Little Sisters, Power to the People stations, audio diaries, level completion and Gatherer's
Garden purchases. DeathLink works both ways. No whole seed has been played through in the real game yet, and the
GOG and Epic versions are not supported. See the setup guide for exactly what has been tried in the game.

Universal Tracker works without your YAML: it takes the options from the seed.

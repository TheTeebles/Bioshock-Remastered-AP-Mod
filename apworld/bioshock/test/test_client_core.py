"""Tests for the client's decision logic, run against the simulated game. No Archipelago server or game needed."""
import pkgutil
import unittest

from ..client import core as core_module
from ..client import game_data
from ..client.core import REFUSED, UNKNOWN, BridgeCore
from ..client.protocol import CommandResult, CommandStarted
from ..client.simulated import SimulatedGame
from ..data import ITEM_TABLE, PLASMIDS, TONICS, ItemKind
from .client_harness import IDENTITY, Harness, item, loc, not_known_yet, play_level


class TestItemDelivery(unittest.TestCase):
    def test_items_are_delivered_in_order_and_counted(self) -> None:
        h = Harness().connect().attach()
        h.receive("Health Upgrade", "10 ADAM", "$50", "Enrage!")
        h.step(10)
        self.assertEqual(h.game.commands, [
            "GiveItem 1 ShockGame.HealthUpgrade",
            "GiveItem 10 ShockGame.ADAM",
            "GiveItem 50 ShockGame.Credits",
            "GiveItem 1 ShockGame.BerserkRage",
        ])
        self.assertEqual(h.game.inventory["ShockGame.ADAM"], 10)
        self.assertEqual(h.core.delivered, 4)
        self.assertEqual(h.storage["delivered"], 4)
        self.assertFalse(h.core.busy)

    def test_nothing_is_delivered_until_the_game_is_ready(self) -> None:
        h = Harness().connect()
        h.receive("Health Upgrade")
        h.step(4)
        self.assertEqual(h.core.delivered, 0, "no game attached yet")

        h.game.loading = True
        h.attach()
        h.step(4)
        self.assertEqual(h.game.commands, [], "the game is loading")

        h.game.loading = False
        h.step(4)
        self.assertEqual(h.game.commands, ["GiveItem 1 ShockGame.HealthUpgrade"])
        self.assertEqual(h.core.delivered, 1)

    def test_a_game_that_is_not_ready_is_not_polled_in_a_hurry(self) -> None:
        h = Harness().connect().attach()
        h.game.loading = True
        h.receive("Health Upgrade")
        h.step(2)
        self.assertFalse(h.core.busy, "nothing can be given during a loading screen, so there is no rush")
        self.assertIn("the rest when the game is ready for them", "\n".join(h.core.status_lines()))
        h.game.loading = False
        h.step()
        self.assertTrue(h.core.busy)
        h.settle()
        self.assertNotIn("when the game is ready", "\n".join(h.core.status_lines()))

    def test_nothing_is_delivered_while_archipelago_is_disconnected(self) -> None:
        h = Harness().connect().attach()
        h.disconnect()
        h.receive("Health Upgrade")
        h.step(4)
        self.assertEqual(h.game.commands, [])
        h.connect()
        h.step(4)
        self.assertEqual(h.core.delivered, 1)

    def test_delivered_count_survives_a_restart(self) -> None:
        first = Harness().connect().attach()
        first.receive("Health Upgrade", "EVE Upgrade")
        first.step(8)
        self.assertEqual(first.storage["delivered"], 2)

        second = Harness()
        second.storage = first.storage
        second.server_items = list(first.server_items)
        second.connect().attach()
        second.receive("10 ADAM")
        second.step(8)
        self.assertEqual(second.game.commands, ["GiveItem 10 ShockGame.ADAM"], "old items must not be given again")

    def test_progressive_plasmid_copies_climb_levels(self) -> None:
        self.assertEqual(game_data.plasmid_levels("Progressive Electro Bolt"), ("Electro Bolt 2", "Electro Bolt 3"))
        self.assertEqual(game_data.plasmid_levels("Progressive Electro Bolt", world_items_shuffled=True),
                         ("Electro Bolt", "Electro Bolt 2", "Electro Bolt 3"))
        self.assertEqual(game_data.plasmid_levels("Telekinesis"), ("Telekinesis",))

        h = Harness().connect().attach()
        h.receive("Progressive Winter Blast", "Health Upgrade", "Progressive Winter Blast", "Progressive Electro Bolt")
        h.step(12)
        self.assertEqual(h.game.commands, [
            "GiveItem 1 ShockGame.IcicleAssault",
            "GiveItem 1 ShockGame.HealthUpgrade",
            "GiveItem 1 ShockDesignerClasses.IcicleAssaultTwo",
            "GiveItem 1 ShockDesignerClasses.ElectricBoltTwo",  # the first level is found in the game itself
        ])

    def test_surplus_plasmid_copies_are_ignored(self) -> None:
        h = Harness().connect().attach()
        h.receive("Enrage!", "Enrage!")
        h.step(8)
        self.assertEqual(h.game.commands, ["GiveItem 1 ShockGame.BerserkRage"])
        self.assertEqual(h.core.delivered, 2)

    def test_items_without_a_known_class_are_set_aside_with_a_warning(self) -> None:
        h = Harness().connect().attach()
        with not_known_yet("Invention Components", "EVE Hypo"):  # as a client did before it knew their classes
            h.receive("Invention Components", "Health Upgrade", "EVE Hypo")
            h.step(8)
        self.assertEqual(h.game.commands, ["GiveItem 1 ShockGame.HealthUpgrade"], "the queue must not stay blocked")
        self.assertEqual(h.core.delivered, 3)
        self.assertEqual(h.core.skipped, {0: UNKNOWN, 2: UNKNOWN})
        self.assertEqual(h.storage["skipped"], [[0, UNKNOWN], [2, UNKNOWN]])
        self.assertTrue(any("Invention Components" in text for text in h.warnings()))
        self.assertTrue(any("EVE Hypo" in text for text in h.warnings()))

    def test_set_aside_item_is_given_once_the_client_learns_how(self) -> None:
        h = Harness().connect().attach()
        with not_known_yet("EVE Hypo"):
            h.receive("EVE Hypo", "Health Upgrade", "EVE Hypo")
            h.settle()
            self.assertEqual(h.core.skipped, {0: UNKNOWN, 2: UNKNOWN})

        # From here on the client is a newer one that knows the class.
        hypo = "ShockDesignerClasses.BioAmmoHypo"
        h.settle()
        self.assertEqual(h.game.inventory[hypo], 0, "not in the middle of a session")
        h.restart_client()  # the player installs the update and starts the client again
        h.settle()
        self.assertEqual(h.game.inventory[hypo], 2)
        self.assertEqual(h.core.skipped, {})
        self.assertEqual(h.storage["skipped"], [])
        self.assertEqual(h.core.delivered, 3)
        h.restart_client()
        h.settle()
        self.assertEqual(h.game.inventory[hypo], 2, "and never a second time")
        self.assertEqual(h.game.inventory["ShockGame.HealthUpgrade"], 1)

    def test_item_with_an_unknown_id_is_set_aside_too(self) -> None:
        h = Harness().connect().attach()
        h.server_items += [987654, item("Health Upgrade")]
        h.core.on_items(h.server_items)
        h.settle()
        self.assertEqual(h.core.skipped, {0: UNKNOWN})
        self.assertEqual(h.game.commands, ["GiveItem 1 ShockGame.HealthUpgrade"])
        self.assertTrue(any("987654" in text for text in h.warnings()))

    def test_refused_command_is_retried_then_set_aside(self) -> None:
        h = Harness().connect().attach()
        h.game.refuse.add("HealthUpgrade")
        h.receive("Health Upgrade", "10 ADAM")
        h.step(40)
        self.assertEqual(h.game.commands.count("GiveItem 1 ShockGame.HealthUpgrade"), core_module.MAX_ATTEMPTS)
        self.assertEqual(h.game.commands[-1], "GiveItem 10 ShockGame.ADAM", "the queue must not stay blocked")
        self.assertEqual(h.core.delivered, 2)
        self.assertEqual(h.core.skipped, {0: REFUSED})
        self.assertTrue(any("Health Upgrade" in text for text in h.warnings()))

    def test_refused_item_is_only_tried_again_when_asked(self) -> None:
        h = Harness().connect().attach()
        h.game.refuse.add("HealthUpgrade")
        h.receive("Health Upgrade", "10 ADAM")
        h.settle()
        tries = len(h.game.commands)
        h.restart_client()
        h.detach()
        h.attach()
        h.settle()
        self.assertEqual(len(h.game.commands), tries, "restarting does not hammer the game with it again")

        self.assertEqual(h.core.retry_set_aside(), 1)
        h.settle()
        self.assertEqual(h.core.skipped, {0: REFUSED}, "still refused")
        h.game.refuse.clear()
        self.assertEqual(h.core.retry_set_aside(), 1)
        h.settle()
        self.assertEqual(h.game.inventory["ShockGame.HealthUpgrade"], 1)
        self.assertEqual(h.core.skipped, {})
        self.assertEqual(h.core.delivered, 2)
        self.assertEqual(h.core.retry_set_aside(), 0)

    def test_refused_command_that_later_succeeds(self) -> None:
        h = Harness().connect().attach()
        h.game.refuse.add("HealthUpgrade")
        h.receive("Health Upgrade")
        h.step(2)
        h.game.refuse.clear()
        h.step(12)
        self.assertEqual(h.game.inventory["ShockGame.HealthUpgrade"], 1)
        self.assertEqual(h.warnings(), [])

    def test_unanswered_command_is_never_sent_twice(self) -> None:
        h = Harness().connect().attach()
        h.game.paused = True  # the game takes the command but does not run it yet
        h.receive("Health Upgrade", "EVE Upgrade")
        h.step(int(60 / 0.25))  # a whole minute in the pause menu
        self.assertEqual(h.game.commands, ["GiveItem 1 ShockGame.HealthUpgrade"], "one copy, however long it takes")
        self.assertEqual(h.core.delivered, 0)
        self.assertEqual(sum("Still waiting" in text for text in h.warnings()), 1, "said once")

        h.game.paused = False
        h.settle()
        self.assertEqual(h.game.inventory["ShockGame.HealthUpgrade"], 1, "given exactly once")
        self.assertEqual(h.game.inventory["ShockGame.BioAmmoUpgrade"], 1)
        self.assertEqual(h.core.delivered, 2)
        self.assertEqual(h.core.skipped, {})

    def test_game_closing_mid_delivery_restarts_the_item(self) -> None:
        h = Harness().connect().attach()
        h.game.paused = True
        h.receive("Health Upgrade")
        h.step(2)
        h.detach()
        h.step(2)
        self.assertEqual(h.core.delivered, 0)
        h.game = SimulatedGame()  # the game was closed and started again: what was waiting in it is gone
        h.attach()
        h.step(4)
        self.assertEqual(h.core.delivered, 1)
        self.assertEqual(h.game.commands, ["GiveItem 1 ShockGame.HealthUpgrade"])

    def test_game_going_away_in_the_middle_of_a_command_sets_the_item_aside(self) -> None:
        h = Harness().connect().attach()
        h.game.paused = True  # stands for a game that takes the command up and never answers
        h.receive("Health Upgrade", "EVE Upgrade")
        h.step(2)
        flight = h.core._in_flight
        assert flight is not None
        h.core.on_event(CommandStarted(flight.command_id), h.now)  # the game has begun on the command ...
        h.detach()  # ... and is gone before it answered
        h.step(2)
        self.assertEqual(h.core.delivered, 1, "dealt with, for now: not given, and not to be given again by itself")
        self.assertEqual(h.core.skipped, {0: REFUSED})
        self.assertEqual(h.storage["delivered"], 1, "and the server is told, so a restarted client knows too")
        self.assertEqual([text for text in h.warnings() if "went away" in text], [
            "The game went away while it was being given Health Upgrade. In case that is what closed it, the item "
            "is set aside; /retry tries it again."])
        self.assertIn("1 the game refused", " ".join(h.core.status_lines()))

        h.game = SimulatedGame()  # the player starts the game again
        h.attach()
        h.settle()
        self.assertEqual(h.game.commands, ["GiveItem 1 ShockGame.BioAmmoUpgrade"],
                         "the next item, and not the one the game went away over")
        h.restart_client()
        h.settle()
        self.assertEqual(h.game.commands, ["GiveItem 1 ShockGame.BioAmmoUpgrade"], "nor after a restart of the client")

        self.assertEqual(h.core.retry_set_aside(), 1)
        h.settle()
        self.assertEqual(h.game.commands[-1], "GiveItem 1 ShockGame.HealthUpgrade", "only when the player asks")
        self.assertEqual(h.core.skipped, {})
        self.assertEqual(h.core.delivered, 2)

    def test_a_command_that_only_waited_in_the_game_is_simply_given_again(self) -> None:
        h = Harness().connect().attach()
        h.game.paused = True
        h.receive("Health Upgrade")
        h.step(2)
        flight = h.core._in_flight
        assert flight is not None
        h.core.on_event(CommandStarted(flight.command_id + 1), h.now)  # word of some other command: not this one
        h.detach()
        self.assertEqual(h.core.skipped, {}, "the game closed with the command still waiting: nothing to suspect")
        self.assertEqual(h.core.delivered, 0)
        self.assertFalse(any("went away" in text for text in h.warnings()))

    def test_a_command_the_game_answered_is_not_what_closed_it(self) -> None:
        h = Harness().connect().attach()
        h.game.paused = True
        h.receive("Health Upgrade")
        h.step(2)
        flight = h.core._in_flight
        assert flight is not None
        h.core.on_event(CommandStarted(flight.command_id), h.now)
        h.core.on_event(CommandResult(flight.command_id, False), h.now)  # refused: it will be tried again shortly
        h.detach()  # and before that happens, the game closes
        self.assertEqual(h.core.skipped, {}, "the command had come back; the game went away over something else")
        h.game = SimulatedGame()
        h.attach()
        h.settle()
        self.assertEqual(h.game.commands, ["GiveItem 1 ShockGame.HealthUpgrade"])
        self.assertEqual(h.core.delivered, 1)

    def test_each_command_of_an_item_starts_afresh(self) -> None:
        h = Harness().connect().attach()
        h.receive("Ammo Bundle")
        h.game.paused = True
        h.step(2)
        first = h.core._in_flight
        assert first is not None
        first_id = first.command_id
        h.core.on_event(CommandStarted(first_id), h.now)
        h.core.on_event(CommandResult(first_id, True), h.now)  # the first of its commands went through ...
        h.step()
        self.assertEqual(len(h.game.commands), 2, "... and the second is on its way, not yet begun")
        self.assertNotEqual(first.command_id, first_id)
        h.core.on_event(CommandStarted(first_id), h.now)  # late word of the first changes nothing
        h.detach()
        self.assertEqual(h.core.skipped, {})

        h.game = SimulatedGame()
        h.game.paused = True
        h.attach()
        h.step(2)
        again = h.core._in_flight
        assert again is not None
        h.core.on_event(CommandStarted(again.command_id), h.now)
        h.core.on_event(CommandResult(again.command_id, True), h.now)
        h.step()
        second = h.core._in_flight
        assert second is not None
        h.core.on_event(CommandStarted(second.command_id), h.now)  # this time the game is in the second command
        h.detach()
        h.step()
        self.assertEqual(h.core.skipped, {0: REFUSED}, "the item is set aside whichever of its commands it was")
        self.assertTrue(any("went away while it was being given Ammo Bundle" in text for text in h.warnings()))

    def test_a_started_command_that_is_answered_is_delivered_as_usual(self) -> None:
        h = Harness().connect().attach()
        h.game.paused = True
        h.receive("Health Upgrade")
        h.step(2)
        flight = h.core._in_flight
        assert flight is not None
        h.core.on_event(CommandStarted(flight.command_id), h.now)
        h.game.paused = False
        h.settle()
        self.assertEqual(h.core.delivered, 1)
        self.assertEqual(h.core.skipped, {})
        h.detach()
        self.assertEqual(h.core.skipped, {}, "nothing was on its way when the game closed")

    def test_stopping_takes_the_last_answer_and_starts_nothing_new(self) -> None:
        h = Harness().connect().attach()
        h.receive("Health Upgrade", "EVE Upgrade")
        h.step()  # the first command goes out; its answer is waiting in the game
        self.assertTrue(h.core.awaiting_answer)
        h.core.stop_deliveries()
        h.step(5)
        self.assertEqual(h.game.commands, ["GiveItem 1 ShockGame.HealthUpgrade"])
        self.assertFalse(h.core.awaiting_answer)
        self.assertFalse(h.core.busy)
        self.assertEqual(h.core.delivered, 1)
        self.assertEqual(h.storage["delivered"], 1)

    def test_traps_are_agent_actions(self) -> None:
        h = Harness().connect().attach()
        h.receive("EVE Drain Trap", "Pickpocket Trap", "Health Upgrade")
        h.game.unsupported_actions.add("pickpocket")
        h.step(10)
        self.assertEqual(h.game.actions, ["eve_drain", "pickpocket"], "an unsupported action is not retried")
        self.assertEqual(h.core.delivered, 3)
        self.assertEqual(h.core.skipped, {1: REFUSED})
        self.assertTrue(any("Pickpocket Trap" in text for text in h.warnings()))

    def test_resync(self) -> None:
        h = Harness().connect().attach()
        h.receive("Health Upgrade", "EVE Upgrade", "10 ADAM")
        h.step(10)
        self.assertEqual(h.core.resync(2), 2)
        h.step(10)
        self.assertEqual(h.game.inventory["ShockGame.ADAM"], 20)
        self.assertEqual(h.game.inventory["ShockGame.HealthUpgrade"], 1)
        self.assertEqual(h.core.resync(), 3)
        h.step(10)
        self.assertEqual(h.game.inventory["ShockGame.HealthUpgrade"], 2)

    def test_resync_waits_for_the_item_on_its_way(self) -> None:
        h = Harness().connect().attach()
        h.receive("Health Upgrade", "EVE Upgrade")
        h.step()  # Health Upgrade is on its way
        self.assertTrue(h.core.awaiting_answer)
        self.assertEqual(h.core.resync(), 0, "nothing has been confirmed yet, so there is nothing to give again")
        h.settle()
        self.assertEqual(h.game.inventory["ShockGame.HealthUpgrade"], 1)

        h.receive("10 ADAM")
        h.step()  # ADAM is on its way; two items are already confirmed
        self.assertEqual(h.core.resync(1), 1)
        h.settle()
        self.assertEqual(h.game.inventory["ShockGame.ADAM"], 20, "once normally, once for the resync")
        self.assertEqual(h.game.inventory["ShockGame.BioAmmoUpgrade"], 1, "the one before it is left alone")
        self.assertEqual(h.core.delivered, 3)

    def test_resync_while_offline_is_kept(self) -> None:
        h = Harness().connect().attach()
        h.receive("Health Upgrade", "EVE Upgrade")
        h.settle()
        h.disconnect()
        self.assertEqual(h.core.resync(), 2)
        h.step(3)
        self.assertEqual(len(h.game.commands), 2, "nothing is given while Archipelago is disconnected")
        h.connect()  # the server still has the old count of 2
        h.settle()
        self.assertEqual(h.game.inventory["ShockGame.HealthUpgrade"], 2)
        self.assertEqual(h.game.inventory["ShockGame.BioAmmoUpgrade"], 2)
        self.assertEqual(h.storage["delivered"], 2)

    def test_resync_forgets_what_was_set_aside_among_those_items(self) -> None:
        h = Harness().connect().attach()
        with not_known_yet("Invention Components"):
            h.receive("Invention Components", "Health Upgrade", "Invention Components")
            h.settle()
            self.assertEqual(h.core.skipped, {0: UNKNOWN, 2: UNKNOWN})
            h.game.loading = True  # nothing is processed for now, so the state right after the resync can be seen
            h.core.resync(1)
            h.step()
            self.assertEqual(h.core.skipped, {0: UNKNOWN})
            self.assertEqual(h.core.delivered, 2)
            h.game.loading = False
            h.settle()
            self.assertEqual(h.core.skipped, {0: UNKNOWN, 2: UNKNOWN}, "and it is set aside again like the first time")
            self.assertEqual(h.core.delivered, 3)

    def test_new_game_with_earlier_deliveries_suggests_resync(self) -> None:
        h = Harness().connect().attach()
        h.receive("Health Upgrade")
        h.step(4)
        for value in (1555, 1555, 2171):  # the plane crash, also once its actor list has grown
            h.core.on_state(core_module.GameState(ready=True, level_value=value))
        h.perform(h.core.drain())
        hints = [text for text in h.warnings() if "/resync" in text]
        self.assertEqual(len(hints), 1, "said once")
        self.assertIn("1 item(s)", hints[0])

    def test_new_game_is_noticed_from_the_grown_value_too(self) -> None:
        h = Harness().connect().attach()
        h.receive("Health Upgrade")
        h.step(4)
        h.core.on_state(core_module.GameState(ready=True, level_value=2171))
        h.perform(h.core.drain())
        self.assertEqual(len([text for text in h.warnings() if "/resync" in text]), 1)

    def test_new_game_is_noticed_from_the_map_name(self) -> None:
        h = Harness().connect().attach()
        h.receive("Health Upgrade")
        h.step(4)
        h.core.on_state(core_module.GameState(map="1-welcome", level_value=1555))  # the number is not believed
        h.perform(h.core.drain())
        self.assertEqual(h.warnings(), [])
        h.core.on_state(core_module.GameState(map="0-lighthouse", level_value=1030))
        h.perform(h.core.drain())
        self.assertEqual(len([text for text in h.warnings() if "/resync" in text]), 1)

    def test_new_game_with_nothing_delivered_says_nothing(self) -> None:
        h = Harness().connect().attach()
        h.core.on_state(core_module.GameState(ready=True, level_value=1555))
        h.perform(h.core.drain())
        self.assertEqual(h.warnings(), [])

    def test_weapons_upgrades_and_bundles_have_commands_of_their_own(self) -> None:
        h = Harness().connect().attach()
        h.receive("Crossbow", "Crossbow Damage Increase", "Pistol Clip Size", "Ammo Bundle", "Film", "Plasmid Slot",
                  "Combat Tonic Slot", "Armored Shell 2", "Extra Nutrition 3", "First Aid Kit", "Auto-Hack Tool")
        h.settle()
        self.assertEqual(h.game.commands, [
            "GiveWeapon ShockGame.Crossbow",
            "AddWeaponStatUpgrade Crossbow Damage",
            "AddWeaponStatUpgrade Pistol MagazineSize",
            "GiveItem 12 ShockGame.Pistol_Bullet",
            "GiveItem 40 ShockGame.MachineGun_Bullet",
            "GiveItem 8 ShockGame.Shotgun_00Buck",
            "GiveItem 5 ShockGame.Film",
            "GiveItem 1 ShockGame.ActiveGeneticSlotUpgrade",
            "GiveItem 1 ShockGame.WeaponsGeneticSlotUpgrade",
            "GiveItem 1 ShockDesignerClasses.ArmoredBodyTwo",
            "GiveItem 1 ShockDesignerClasses.HealthyConsumerThree",
            "GiveItem 1 ShockDesignerClasses.MedHypo",
            "GiveItem 1 ShockGame.AutoHack",
        ])
        self.assertEqual(h.core.delivered, 11)
        self.assertEqual(h.core.skipped, {})
        self.assertEqual(h.game.inventory["ShockGame.Crossbow"], 1)
        self.assertEqual(h.game.inventory["Crossbow Damage upgrade"], 1)
        self.assertEqual(h.game.inventory["ShockGame.MachineGun_Bullet"], 40)
        self.assertEqual([notice.text for notice in h.notices if "Ammo Bundle" in notice.text],
                         ["Delivered Ammo Bundle."], "one item, however many commands it takes")

    def test_an_item_of_several_commands_counts_once_all_of_them_ran(self) -> None:
        h = Harness().connect().attach()
        h.game.paused = True
        h.receive("Ammo Bundle", "Health Upgrade")
        h.step(3)
        self.assertEqual(h.game.commands, ["GiveItem 12 ShockGame.Pistol_Bullet"], "one at a time, each after the last")
        self.assertEqual(h.core.delivered, 0)
        h.game.paused = False
        h.settle()
        self.assertEqual(len(h.game.commands), 4)
        self.assertEqual(h.core.delivered, 2)

        h = Harness().connect().attach()
        h.game.refuse.add("MachineGun_Bullet")
        h.receive("Ammo Bundle", "Health Upgrade")
        h.settle()
        self.assertEqual(h.game.commands, ["GiveItem 12 ShockGame.Pistol_Bullet"]
                         + ["GiveItem 40 ShockGame.MachineGun_Bullet"] * core_module.MAX_ATTEMPTS
                         + ["GiveItem 1 ShockGame.HealthUpgrade"], "the part the game refuses is not skipped over")
        self.assertEqual(h.core.skipped, {0: REFUSED})
        self.assertEqual(h.game.inventory["ShockGame.Shotgun_00Buck"], 0)

    def test_what_was_on_its_way_to_one_game_does_not_reach_the_next(self) -> None:
        h = Harness().connect().attach()
        h.receive("Health Upgrade", "EVE Upgrade", "10 ADAM")
        h.step()  # the first command is with the game, which has answered; the client has not looked yet
        first = h.game
        # The wrapper lets go of this game for another one: it takes the answer that is waiting, then swaps.
        for event in first.poll_events():
            h.core.on_event(event, h.now)
        self.assertEqual(h.core.delivered, 1)
        h.detach()
        h.game = SimulatedGame()
        h.attach()
        h.settle()
        self.assertEqual(first.commands, ["GiveItem 1 ShockGame.HealthUpgrade"])
        self.assertEqual(h.game.commands, ["GiveItem 1 ShockGame.BioAmmoUpgrade", "GiveItem 10 ShockGame.ADAM"],
                         "each once: nothing started for the old game is sent to the new one as well")
        self.assertEqual(h.core.delivered, 3)

    def test_a_death_meant_for_one_game_does_not_reach_the_next(self) -> None:
        h = Harness(death_link=True).connect().attach()
        h.step()
        h.core.on_deathlink()
        h.core.tick(h.now)  # the kill is decided on ...
        first = h.game
        h.detach()  # ... and the game is gone before it is carried out
        h.game = SimulatedGame()
        h.attach()
        h.settle()
        self.assertEqual((first.deaths, h.game.deaths), (0, 0))

    def test_the_simulated_game_only_takes_the_commands_the_client_uses(self) -> None:
        """Otherwise a command the real game would not know could pass every test."""
        game = SimulatedGame()
        commands = ("GiveItem 2 ShockGame.ADAM", "GiveWeapon ShockGame.Pistol", "AddWeaponStatUpgrade Pistol Damage",
                    "StartSecurityAlarm", "summon ShockAI.SecurityBot",
                    "GiveItem ShockGame.ADAM", "GiveItem two ShockGame.ADAM", "GiveWeapon",
                    "AddWeaponStatUpgrade Pistol", "StartSecurityAlarm now", "summon", "Dance", "")
        for number, command in enumerate(commands):
            game.run_command(number, command)
        self.assertEqual([event.handled for event in game.poll_events()], [True] * 5 + [False] * 8)
        self.assertEqual(game.inventory, {"ShockGame.ADAM": 2, "ShockGame.Pistol": 1, "Pistol Damage upgrade": 1})
        self.assertEqual(game.alarms, 1)
        self.assertEqual(game.summoned, ["ShockAI.SecurityBot"])

    def test_the_alarm_trap_is_a_console_command_and_the_others_are_agent_actions(self) -> None:
        h = Harness().connect().attach()
        h.receive("Security Alarm Trap", "EVE Drain Trap", "Pickpocket Trap")
        h.settle()
        self.assertEqual(h.game.commands, ["StartSecurityAlarm", f"summon {game_data.SECURITY_BOT}"],
                         "the alarm first: a bot summoned while it rings is hostile, one summoned after is friendly")
        self.assertEqual(h.game.alarms, 1)
        self.assertEqual(h.game.summoned, [game_data.SECURITY_BOT])
        self.assertEqual(h.game.actions, ["eve_drain", "pickpocket"])
        self.assertEqual(h.core.delivered, 3)

    def test_an_alarm_without_a_bot_is_still_a_trap(self) -> None:
        """The bot's class is not loaded in every level; the alarm has rung by then, and must not ring twice."""
        h = Harness().connect().attach()
        h.game.refuse.add("summon")
        h.receive("Security Alarm Trap", "Health Upgrade")
        h.settle()
        self.assertEqual(h.game.commands, ["StartSecurityAlarm"]
                         + [f"summon {game_data.SECURITY_BOT}"] * core_module.MAX_ATTEMPTS
                         + ["GiveItem 1 ShockGame.HealthUpgrade"])
        self.assertEqual(h.game.alarms, 1)
        self.assertEqual(h.core.delivered, 2)
        self.assertEqual(h.core.skipped, {})
        texts = [notice.text for notice in h.notices]
        self.assertIn("Delivered Security Alarm Trap.", texts)
        self.assertTrue(any(text.startswith("While delivering Security Alarm Trap, \"summon ") for text in texts))

    def test_a_refused_command_before_the_optional_ones_still_sets_the_item_aside(self) -> None:
        h = Harness().connect().attach()
        h.game.refuse.add("StartSecurityAlarm")
        h.receive("Security Alarm Trap")
        h.settle()
        self.assertEqual(h.game.commands, ["StartSecurityAlarm"] * core_module.MAX_ATTEMPTS)
        self.assertEqual(h.game.summoned, [])
        self.assertEqual(h.core.skipped, {0: REFUSED})

    def test_every_class_the_client_names_is_one_the_running_game_listed(self) -> None:
        """Held against what the game itself printed for `obj list` (Steam build, 2026-10-07)."""
        data = pkgutil.get_data(__package__, "runtime_classes.txt")
        assert data is not None
        # The game does not tell capitals from small letters in a name, and the two lists do not always agree
        # on them ("SpringboardTrap" in the guides, "SpringBoardTrapTwo" in the game).
        listed = {name.lower() for name in data.decode("ascii").split()}
        self.assertEqual(len(listed), 1277)
        self.assertEqual(sum(1 for name in listed if name.startswith("shockdesignerclasses.")), 831,
                         "every class of the designer package, by the game's own count")
        self.assertEqual({name.split(".")[0] for name in listed}, {"shockgame", "shockdesignerclasses"})
        used = game_data.classes_given()
        script = sorted(name for name in used if name.startswith("ShockGame."))
        designer = sorted(name for name in used if name.startswith("ShockDesignerClasses."))
        self.assertEqual(len(script) + len(designer), len(used), "no other package is known to hold items")
        self.assertEqual((len(script), len(designer)), (62, 52))
        for name in used:
            self.assertIn(name.lower(), listed, f"{name}, for {used[name]}")
        # In the designer package: every second and third level, the two hypos the shops sell, and what the
        # U-Invent machines take.
        hypos = ("ShockDesignerClasses.MedHypo", "ShockDesignerClasses.BioAmmoHypo")
        for name in designer:
            self.assertTrue(name.endswith(("Two", "Three", "Component")) or name in hypos, name)
        for name in script:
            self.assertFalse(name.endswith(("Two", "Three")), f"{name}: the game has no higher level in ShockGame")

    def test_the_script_package_on_disk_is_not_the_last_word(self) -> None:
        """The list exported from the ShockGame package with UE Explorer, and where the running game differs."""
        data = pkgutil.get_data(__package__, "shockgame_classes.txt")
        assert data is not None
        exported = set(data.decode("ascii").split())
        self.assertEqual(len(exported), 654)
        running = pkgutil.get_data(__package__, "runtime_classes.txt")
        assert running is not None
        listed = {name.lower() for name in running.decode("ascii").split()}
        used = game_data.classes_given()
        for name in used:
            if name.startswith("ShockGame."):
                self.assertIn(name.split(".", 1)[1], exported, f"{name}, for {used[name]}")
        for weapon, _ in game_data.WEAPON_UPGRADE.values():
            self.assertIn(weapon, exported)
            self.assertIn(f"ShockGame.{weapon}", game_data.WEAPON_CLASS.values())
        # Six higher levels are declared in the script package and are not classes of ShockGame in the running
        # game. Naming them there was a mistake that only the game itself could show (2026-10-07).
        declared_only = sorted(name for name in exported
                               if name.endswith(("Two", "Three")) and f"shockgame.{name.lower()}" not in listed
                               and f"shockdesignerclasses.{name.lower()}" in listed)
        self.assertEqual(declared_only, ["ChargedBurstsTwo", "EyeForDetailTwo", "FastTwitchTwo",
                                         "HealthyConsumerThree", "MeleeMasterTwo", "SecuritySystemsExpertTwo"])
        for name in declared_only:
            self.assertIn(f"ShockDesignerClasses.{name}", used)
            self.assertNotIn(f"ShockGame.{name}", used)

    def test_every_plasmid_and_tonic_has_a_class_of_its_own(self) -> None:
        names = [name for plasmid in PLASMIDS for name, _, _ in plasmid.levels] + [tonic.name for tonic in TONICS]
        self.assertEqual(len(names), 22 + 58)
        for name in names:
            self.assertIn(name, game_data.GIVE_CLASS)
        classes = [game_data.GIVE_CLASS[name] for name in names]
        self.assertEqual(len(set(classes)), len(classes), "two items that give the same thing would be a mix-up")
        for name in ("Armored Shell", "Winter Blast", "Telekinesis", "Electro Bolt", "SportBoost"):
            self.assertTrue(game_data.GIVE_CLASS[name].startswith("ShockGame."), name)
        self.assertEqual(game_data.GIVE_CLASS["Armored Shell"], "ShockGame.ArmoredBody")
        self.assertEqual(game_data.GIVE_CLASS["Armored Shell 2"], "ShockDesignerClasses.ArmoredBodyTwo")
        self.assertEqual(game_data.GIVE_CLASS["Electro Bolt 3"], "ShockDesignerClasses.ElectricBoltThree")
        self.assertEqual(game_data.GIVE_CLASS["Hypnotize Big Daddy 2"], "ShockDesignerClasses.SummonProtectorTwo")
        self.assertEqual(game_data.GIVE_CLASS["Medical Expert 3"], "ShockDesignerClasses.MedicineFriendlyThree")
        self.assertEqual(game_data.GIVE_CLASS["Natural Camouflage"], "ShockGame.ChameleonBlood")
        self.assertEqual(game_data.GIVE_CLASS["Human Inferno"], "ShockGame.SuperHeated")
        self.assertEqual(game_data.GIVE_CLASS["Safecracker 2"], "ShockDesignerClasses.StationExpertTwo")
        # The Research Camera's rewards are no exception. The script package on disk declares these six, but the
        # running game only has them in the designer package: giving ShockGame.FastTwitchTwo gave nothing.
        self.assertEqual({name: game_data.GIVE_CLASS[name] for name in (
            "Static Discharge 2", "Photographer's Eye 2", "SportBoost 2", "Extra Nutrition 3", "Wrench Jockey 2",
            "Security Expert 2")}, {
            "Static Discharge 2": "ShockDesignerClasses.ChargedBurstsTwo",
            "Photographer's Eye 2": "ShockDesignerClasses.EyeForDetailTwo",
            "SportBoost 2": "ShockDesignerClasses.FastTwitchTwo",
            "Extra Nutrition 3": "ShockDesignerClasses.HealthyConsumerThree",
            "Wrench Jockey 2": "ShockDesignerClasses.MeleeMasterTwo",
            "Security Expert 2": "ShockDesignerClasses.SecuritySystemsExpertTwo"})
        self.assertEqual(game_data.GIVE_CLASS["Extra Nutrition 2"], "ShockDesignerClasses.HealthyConsumerTwo")
        for name in names:
            family, level = game_data._split_level(name)
            self.assertEqual(game_data.GIVE_CLASS[name].split(".")[0],
                             "ShockGame" if level == 1 else "ShockDesignerClasses", name)

    def test_there_is_a_way_to_give_every_item(self) -> None:
        self.assertEqual(game_data.undeliverable_items(), [])
        components = game_data.plan_delivery("Invention Components", 1)
        assert components is not None
        self.assertEqual(len(components.commands), 12, "one of each thing a U-Invent takes")
        self.assertEqual(components.commands[0], "GiveItem 1 ShockDesignerClasses.AlcoholComponent")
        self.assertEqual(components.commands[-1], "GiveItem 1 ShockDesignerClasses.SteelScrewComponent")
        self.assertEqual(len(set(components.commands)), 12)
        with not_known_yet("Invention Components", "EVE Hypo"):
            self.assertEqual(game_data.undeliverable_items(), ["EVE Hypo", "Invention Components"])
        for data in ITEM_TABLE:
            if data.kind in (ItemKind.WEAPON, ItemKind.WEAPON_UPGRADE, ItemKind.TONIC, ItemKind.CHARACTER_UPGRADE):
                self.assertEqual(len(game_data.plan_delivery(data.name, 1).commands), 1, data.name)

    def test_every_item_has_a_plan_or_is_known_missing(self) -> None:
        missing = set(game_data.undeliverable_items())
        for data in ITEM_TABLE:
            plan = game_data.plan_delivery(data.name, 1)
            if plan is None:
                self.assertTrue(any(entry.startswith(data.name) for entry in missing), data.name)
            else:
                self.assertTrue(plan.description)


class TestLevelAccess(unittest.TestCase):
    def test_open_levels_send_immediately(self) -> None:
        h = Harness().connect().attach()
        h.game.collect_diary(1)  # Welcome to Rapture
        h.game.collect_diary(3)  # Medical Pavilion
        h.step(2)
        self.assertEqual(sorted(h.sent_checks), [1001, 1003])

    def test_locked_level_is_held_until_its_access_item_arrives(self) -> None:
        h = Harness().connect().attach()
        h.game.collect_diary(42)  # Arcadia
        h.game.collect_diary(20)  # Neptune's Bounty
        h.step(3)
        self.assertEqual(h.sent_checks, [])
        self.assertEqual(h.core.held_locations(), [1020, 1042])
        self.assertEqual(sum("Arcadia Access" in notice.text for notice in h.notices), 1, "told once, not every tick")

        h.receive("Arcadia Access")
        h.step(3)
        self.assertEqual(h.sent_checks, [1042])
        self.assertEqual(h.core.held_locations(), [1020])

    def test_many_held_checks_are_announced_in_one_line(self) -> None:
        h = Harness().connect().attach()
        play_level(h.game, "Arcadia")
        h.step(3)

        def announcements() -> list[str]:
            return [notice.text for notice in h.notices if "will be sent once" in notice.text]

        self.assertEqual(len(announcements()), 3, "not a line for each of the level's 25 checks")
        self.assertIn("25 checks in levels that are still locked", announcements()[0])
        self.assertIn("Arcadia Access", announcements()[0])
        self.assertIn("Neptune's Bounty - Level Complete", announcements()[1], "a few are still named one by one")

        h.restart_client()
        h.step(3)
        self.assertEqual(len(announcements()), 4, "and one line again after a restart, not twenty-seven")
        self.assertIn("27 checks", announcements()[3])
        self.assertIn("Arcadia Access, Neptune's Bounty Access", announcements()[3])

    def test_story_milestones_are_checks_of_their_level(self) -> None:
        h = Harness().connect().attach()
        self.assertEqual(h.game.reach_milestone("Medical Pavilion"), "Melt the Ice to Dental Services")
        self.assertEqual(h.game.reach_milestone("Arcadia"), "Bring Langford the Rosa Gallica")
        h.step(3)
        self.assertEqual(h.sent_checks, [loc("Medical Pavilion - Story: Melt the Ice to Dental Services")])
        self.assertEqual(h.core.held_locations(), [loc("Arcadia - Story: Bring Langford the Rosa Gallica")])
        h.receive("Arcadia Access")
        h.step(3)
        self.assertEqual(h.core.held_locations(), [])

        for _ in range(2):
            h.game.reach_milestone("Medical Pavilion")
        self.assertRaises(ValueError, h.game.reach_milestone, "Medical Pavilion")
        h.core.on_state(core_module.GameState(ready=True, milestones=frozenset({"steinman", "not a real one"})))
        self.assertIn(loc("Medical Pavilion - Story: Defeat Dr. Steinman"), h.core.collected)

    def test_shared_access_items(self) -> None:
        h = Harness().connect().attach()
        h.game.collect_diary(39)  # Smuggler's Hideout
        h.game.collect_diary(99)  # Rapture Central Control
        h.receive("Neptune's Bounty Access")
        h.step(3)
        self.assertEqual(h.sent_checks, [1039])
        h.receive("Hephaestus Access")
        h.step(3)
        self.assertEqual(h.sent_checks, [1039, 1099])

    def test_vanilla_access_sends_everything(self) -> None:
        h = Harness(access_items=False).connect().attach()
        h.game.collect_diary(122)
        h.game.travel("Point Prometheus")
        h.game.deal_with_little_sister()
        h.step(3)
        self.assertIn(1122, h.sent_checks)
        self.assertIn(loc("Point Prometheus - Little Sister 1"), h.sent_checks)

    def test_held_checks_survive_a_restart(self) -> None:
        first = Harness().connect().attach()
        first.game.collect_diary(42)
        first.step(3)

        second = Harness()  # new session; the save being played no longer reports that diary
        second.storage = first.storage
        second.connect().attach()
        second.receive("Arcadia Access")
        second.step(3)
        self.assertEqual(second.sent_checks, [1042])

    def test_only_locations_in_this_seed_are_sent(self) -> None:
        h = Harness(access_items=False)
        h.valid = {1001}
        h.connect().attach()
        h.game.collect_diary(1)
        h.game.collect_diary(2)
        h.game.collect_reel()
        h.step(3)
        self.assertEqual(h.sent_checks, [1001])

    def test_checks_are_not_sent_twice(self) -> None:
        h = Harness().connect().attach()
        h.game.collect_diary(1)
        h.step(6)
        self.assertEqual(h.sent_checks, [1001])
        h.disconnect()
        h.connect()
        h.step(3)
        self.assertEqual(h.sent_checks, [1001], "the server already has it")

    def test_check_lost_in_a_disconnect_is_sent_again(self) -> None:
        h = Harness().connect().attach()
        h.game.collect_diary(1)
        h.core.on_state(h.game.read_state())
        h.core.tick(1.0)
        h.core.drain()  # the packet never reached the server
        h.disconnect()
        h.connect()
        h.step(2)
        self.assertEqual(h.sent_checks, [1001])

    def test_counted_checks_use_per_level_numbers(self) -> None:
        h = Harness(access_items=False).connect().attach()
        h.game.travel("Fort Frolic")
        h.game.deal_with_little_sister()
        h.game.deal_with_little_sister()
        h.game.use_station()
        h.game.collect_reel()
        h.step(3)
        for name in ("Fort Frolic - Little Sister 1", "Fort Frolic - Little Sister 2",
                     "Fort Frolic - Power to the People 1", "Fort Frolic - Director's Commentary Reel"):
            self.assertIn(loc(name), h.sent_checks, name)
        self.assertNotIn(loc("Fort Frolic - Little Sister 3"), h.sent_checks)


class TestStoryProgress(unittest.TestCase):
    def completions(self, h: Harness) -> list[str]:
        return sorted(game_data.LOCATION_NAME[location] for location in h.sent_checks
                      if "Level Complete" in game_data.LOCATION_NAME[location])

    def test_reaching_the_next_level_completes_the_previous_one(self) -> None:
        h = Harness(access_items=False).connect().attach()
        h.step(3)
        self.assertEqual(self.completions(h), [])
        h.game.travel("Medical Pavilion")
        h.step(3)
        self.assertEqual(self.completions(h), ["Welcome to Rapture - Level Complete"])

    def test_arcadia_and_farmers_market_complete_at_fort_frolic(self) -> None:
        h = Harness(access_items=False).connect().attach()
        for level in ("Arcadia", "Farmer's Market", "Arcadia"):
            h.game.travel(level)
            h.step(3)
        self.assertNotIn("Arcadia - Level Complete", self.completions(h))
        self.assertNotIn("Farmer's Market - Level Complete", self.completions(h))
        self.assertIn("Smuggler's Hideout - Level Complete", self.completions(h))
        h.game.travel("Fort Frolic")
        h.step(3)
        self.assertIn("Arcadia - Level Complete", self.completions(h))
        self.assertIn("Farmer's Market - Level Complete", self.completions(h))

    def test_backtracking_does_not_undo_progress(self) -> None:
        h = Harness(access_items=False).connect().attach()
        h.game.travel("Hephaestus")
        h.step(3)
        h.game.travel("Medical Pavilion")
        h.step(3)
        self.assertEqual(h.core.furthest, game_data.STORY_INDEX["Hephaestus"])
        self.assertEqual(h.core.level, "Medical Pavilion")

    def test_level_is_read_from_autosplitter_values(self) -> None:
        core = BridgeCore()
        core.on_agent_attached("test")
        state = core_module.GameState(ready=True, level_value=9317)  # Fort Frolic, freshly loaded
        core.on_state(state)
        self.assertIsNone(core.level, "one sighting is not enough")
        core.on_state(state)
        self.assertEqual(core.level, "Fort Frolic")
        core.on_state(core_module.GameState(ready=True, level_value=12844))  # same level after its actor list grew
        core.on_state(core_module.GameState(ready=True, level_value=12844))
        self.assertEqual(core.level, "Fort Frolic")
        core.on_state(core_module.GameState(ready=False, level_value=0))
        core.on_state(core_module.GameState(ready=True, level_value=31337))
        self.assertEqual(core.furthest, game_data.STORY_INDEX["Fort Frolic"], "unknown values change nothing")

    def test_the_map_name_decides_the_level(self) -> None:
        """The level number is only right in a game played straight through. In a loaded Medical Pavilion save the
        real game showed 1030, which is in nobody's table; the map's name was still "1-medical"."""
        core = BridgeCore()
        core.on_agent_attached("test")
        loaded_save = core_module.GameState(ready=True, map="1-medical", level_value=1030)
        core.on_state(loaded_save)
        self.assertIsNone(core.level, "one sighting is not enough")
        core.on_state(loaded_save)
        self.assertEqual(core.level, "Medical Pavilion")
        self.assertEqual(core.furthest, game_data.STORY_INDEX["Medical Pavilion"])
        # A number that belongs to another level does not win over the name.
        for _ in range(2):
            core.on_state(core_module.GameState(ready=True, map="4-recreation", level_value=7039))
        self.assertEqual(core.level, "Fort Frolic")

    def test_every_level_has_a_map_and_the_lighthouse_and_the_last_fight_have_theirs(self) -> None:
        self.assertEqual(sorted(game_data.MAP_LEVEL.values()), sorted(game_data.STORY_ORDER))
        self.assertEqual(game_data.level_of("0-lighthouse", None), "Welcome to Rapture")
        self.assertEqual(game_data.level_of("7-bossfight", None), "Proving Grounds")
        self.assertEqual(game_data.level_of("7-gauntlet", None), "Proving Grounds")
        self.assertEqual(game_data.level_of("7-science", None), "Point Prometheus")
        self.assertEqual(game_data.level_of("2-subbay", None), "Smuggler's Hideout")

    def test_map_names_are_read_however_the_game_spells_them(self) -> None:
        for spelled in ("1-medical", "1-Medical.bsm", "1-MEDICAL_deu.bsm", r"..\Maps\1-Medical.bsm?Name=Jack",
                        " maps/1-medical ", "1-medical?Game=ShockGame/Thing", "1-Medical.bsm?a=b?c=d\\e"):
            self.assertEqual(game_data.normalize_map(spelled), "1-medical", spelled)
        for nothing in ("", "   ", None, 5, ".bsm"):
            self.assertIsNone(game_data.normalize_map(nothing))

    def test_a_map_the_client_has_no_name_for_falls_back_to_the_level_number(self) -> None:
        core = BridgeCore()
        core.on_agent_attached("test")
        for _ in range(2):
            core.on_state(core_module.GameState(ready=True, map="9-newlevel", level_value=9317))
        self.assertEqual(core.level, "Fort Frolic")
        self.assertIn("in Fort Frolic", "\n".join(core.status_lines()))
        for _ in range(2):
            core.on_state(core_module.GameState(ready=True, map="9-newlevel", level_value=1030))
        self.assertIn("in a map the client has no level for yet (9-newlevel)", "\n".join(core.status_lines()))

    def test_maps_outside_the_story_are_no_level(self) -> None:
        core = BridgeCore()
        core.on_agent_attached("test")
        for outside in ("entry", "museum", "challengeroomdecoy", "autoplay"):
            for _ in range(3):
                core.on_state(core_module.GameState(ready=False, map=outside, level_value=9317))  # the number lies
            self.assertEqual(core.furthest, 0, outside)
            self.assertFalse(game_data.delivers_in(outside))
            self.assertIn(f"outside Rapture's levels ({outside})", "\n".join(core.status_lines()))
        self.assertFalse(game_data.delivers_in("0-lighthouse"), "not before the bathysphere ride either")
        self.assertTrue(game_data.delivers_in("1-welcome"))
        self.assertTrue(game_data.delivers_in("7-bossfight"))
        self.assertTrue(game_data.delivers_in(None), "no name, no opinion")

    def test_level_values_follow_the_engines_growth_rule(self) -> None:
        # The second value the LiveSplit autosplitter lists for each map, next to the value it loads with.
        listed = {
            5538: 7648, 7039: 9712, 8543: 11780, 3205: 4440, 8187: 11290, 5448: 7524, 9317: 12844, 6932: 9564,
            2116: 2942, 8291: 11433, 5804: 8013, 8672: 11957, 4101: 5672, 928: 1309,
        }
        for arrival, second in listed.items():
            self.assertEqual(game_data.grown(arrival), second, arrival)
            self.assertEqual(game_data.LEVEL_FROM_VALUE[arrival], game_data.LEVEL_FROM_VALUE[second])
        self.assertEqual(game_data.LEVEL_FROM_VALUE[8187], "Arcadia")
        self.assertEqual(game_data.LEVEL_FROM_VALUE[game_data.grown(11290)], "Arcadia", "and after growing again")
        self.assertEqual(game_data.LEVEL_FROM_VALUE[2171], "Welcome to Rapture", "the plane crash, grown once")
        self.assertIn(1833, game_data.FONTAINE_ARENA_VALUES)

    def test_level_completion_respects_access(self) -> None:
        h = Harness().connect().attach()
        h.game.travel("Arcadia")
        h.step(3)
        self.assertEqual(self.completions(h), ["Medical Pavilion - Level Complete",
                                               "Welcome to Rapture - Level Complete"])
        h.receive("Neptune's Bounty Access")
        h.step(3)
        self.assertIn("Neptune's Bounty - Level Complete", self.completions(h))
        self.assertIn("Smuggler's Hideout - Level Complete", self.completions(h))


class TestGoal(unittest.TestCase):
    def test_goal_waits_for_point_prometheus_access(self) -> None:
        h = Harness().connect().attach()
        h.game.defeat_fontaine()
        h.step(3)
        self.assertEqual(h.goals, 0)
        h.receive("Point Prometheus Access")
        h.step(3)
        self.assertEqual(h.goals, 1)
        h.step(3)
        self.assertEqual(h.goals, 1)

    def test_goal_is_remembered_across_sessions(self) -> None:
        first = Harness().connect().attach()
        first.game.defeat_fontaine()
        first.step(3)

        second = Harness()
        second.storage = first.storage
        second.connect()
        second.receive("Point Prometheus Access")
        second.step(2)
        self.assertEqual(second.goals, 1)

    def test_goal_in_vanilla_access(self) -> None:
        h = Harness(access_items=False).connect().attach()
        h.game.defeat_fontaine()
        h.step(2)
        self.assertEqual(h.goals, 1)

    def test_goal_from_fontaine_fight_phase(self) -> None:
        core = BridgeCore()
        core.on_connected(IDENTITY, {"level_access_items": {}}, set(), set(), None)
        core.on_agent_attached("test")
        arena = 1309
        core.on_state(core_module.GameState(ready=True, level_value=arena, fontaine_phase=4))
        self.assertFalse(core.goal_reached, "phase 4 without having seen the fight is not a win")
        core.on_state(core_module.GameState(ready=True, level_value=arena, fontaine_phase=3))
        core.on_state(core_module.GameState(ready=True, level_value=5672, fontaine_phase=4))
        self.assertFalse(core.goal_reached, "not in the arena")
        core.on_state(core_module.GameState(ready=True, level_value=arena, fontaine_phase=3))
        core.on_state(core_module.GameState(ready=True, level_value=0, fontaine_phase=4))
        self.assertFalse(core.goal_reached, "a loading screen ends the fight (the player died and reloaded)")
        core.on_state(core_module.GameState(ready=True, level_value=arena, fontaine_phase=3))
        core.on_state(core_module.GameState(ready=True, level_value=arena, fontaine_phase=4))
        self.assertTrue(core.goal_reached)

    def test_goal_in_a_loaded_save_goes_by_the_map_name(self) -> None:
        """After loading a save the level number means nothing, so the fight is recognised by its map."""
        core = BridgeCore()
        core.on_connected(IDENTITY, {"level_access_items": {}}, set(), set(), None)
        core.on_agent_attached("test")
        core.on_state(core_module.GameState(ready=True, map="7-gauntlet", level_value=1309, fontaine_phase=3))
        core.on_state(core_module.GameState(ready=True, map="7-gauntlet", level_value=1309, fontaine_phase=4))
        self.assertFalse(core.goal_reached, "the number says arena, the map says Proving Grounds")
        core.on_state(core_module.GameState(ready=True, map="7-bossfight", level_value=1030, fontaine_phase=3))
        core.on_state(core_module.GameState(ready=True, map="7-gauntlet", level_value=1030, fontaine_phase=4))
        self.assertFalse(core.goal_reached, "the fight was left")
        core.on_state(core_module.GameState(ready=True, map="7-bossfight", level_value=1030, fontaine_phase=3))
        core.on_state(core_module.GameState(ready=True, map=None, level_value=31337, fontaine_phase=3))
        core.on_state(core_module.GameState(ready=True, map="7-bossfight", level_value=77, fontaine_phase=4))
        self.assertTrue(core.goal_reached, "a moment without a map name does not lose the fight")
        self.assertEqual(core.level, "Proving Grounds")

    def test_goal_with_an_agent_that_names_maps_and_passes_no_number(self) -> None:
        def fight(**state: object) -> None:
            core.on_state(core_module.GameState(**state))  # type: ignore[arg-type]

        core = BridgeCore()
        core.on_connected(IDENTITY, {"level_access_items": {}}, set(), set(), None)
        core.on_agent_attached("test")
        fight(ready=True, map="7-bossfight", fontaine_phase=3)
        fight(ready=False, in_level=False, fontaine_phase=4)  # the player died and the save is loading
        self.assertFalse(core.goal_reached, "what is read during a loading screen means nothing")
        fight(ready=True, map="7-bossfight", fontaine_phase=4)
        self.assertFalse(core.goal_reached, "the fight has to be seen again first")

        fight(ready=True, map="7-bossfight", fontaine_phase=3)
        fight(ready=False, fontaine_phase=3)  # paused, or a hitch: no map is named, and nothing says it is over
        fight(ready=False, fontaine_phase=4)
        self.assertTrue(core.goal_reached, "a pause does not lose the fight")

        core = BridgeCore()
        core.on_connected(IDENTITY, {"level_access_items": {}}, set(), set(), None)
        core.on_agent_attached("test")
        fight(ready=True, map="7-bossfight", fontaine_phase=3)
        fight(ready=False, in_level=False, fontaine_phase=3)  # quit to the main menu
        fight(ready=False, fontaine_phase=4)
        self.assertFalse(core.goal_reached)
        self.assertEqual(game_data.in_fontaine_arena(None, None), None)
        self.assertIs(game_data.in_fontaine_arena(None, 0), False)

    def test_goal_survives_the_arena_growing_past_what_the_client_knows(self) -> None:
        for later_value in (1833, 2553, 31337):  # two more enlargements, and a number the client has never seen
            with self.subTest(later_value=later_value):
                core = BridgeCore()
                core.on_connected(IDENTITY, {"level_access_items": {}}, set(), set(), None)
                core.on_agent_attached("test")
                core.on_state(core_module.GameState(ready=True, level_value=1309, fontaine_phase=3))
                core.on_state(core_module.GameState(ready=True, level_value=later_value, fontaine_phase=3))
                core.on_state(core_module.GameState(ready=True, level_value=later_value, fontaine_phase=4))
                self.assertTrue(core.goal_reached)


class TestDeathLink(unittest.TestCase):
    def test_own_death_is_sent(self) -> None:
        h = Harness(death_link=True).connect().attach()
        h.game.die()
        h.step(2)
        self.assertEqual(len(h.deaths_sent), 1)

    def test_death_is_not_sent_when_disabled(self) -> None:
        h = Harness(death_link=False).connect().attach()
        h.game.die()
        h.step(2)
        self.assertEqual(h.deaths_sent, [])

    def test_received_death_kills_without_echoing(self) -> None:
        h = Harness(death_link=True).connect().attach()
        h.core.on_deathlink()
        h.step(3)
        self.assertEqual(h.game.deaths, 1)
        self.assertEqual(h.deaths_sent, [], "the death we caused must not be sent back")
        h.game.die()
        h.step(2)
        self.assertEqual(len(h.deaths_sent), 1, "the player's next death is their own")

    def test_received_death_waits_for_a_loading_screen(self) -> None:
        h = Harness(death_link=True).connect().attach()
        h.game.loading = True
        h.core.on_deathlink()
        h.step(8)
        self.assertEqual(h.game.deaths, 0)
        h.game.loading = False
        h.step(2)
        self.assertEqual(h.game.deaths, 1)
        self.assertEqual(h.deaths_sent, [])

    def test_received_death_does_not_wait_forever(self) -> None:
        h = Harness(death_link=True).connect().attach()
        h.game.loading = True
        h.core.on_deathlink()
        h.step(int(core_module.DEATHLINK_WINDOW / 0.25) + 4)
        h.game.loading = False
        h.step(4)
        self.assertEqual(h.game.deaths, 0, "a death from half a minute ago is not carried out")

    def test_received_death_with_no_game_running_is_dropped(self) -> None:
        h = Harness(death_link=True).connect()
        h.core.on_deathlink()
        h.step(3)
        h.attach()
        h.step(3)
        self.assertEqual(h.game.deaths, 0, "starting the game later must not kill the player")

    def test_deathlink_choice_lasts_for_the_session(self) -> None:
        h = Harness(death_link=True).connect().attach()
        h.core.death_link = False  # what /deathlink does
        h.disconnect()
        h.connect()
        self.assertFalse(h.core.death_link, "a reconnect must not switch it back on")
        h.disconnect()
        h.connect(identity=("other seed", 0, 1))
        self.assertTrue(h.core.death_link, "another seed starts from its own setting")


class TestSavedState(unittest.TestCase):
    def test_switching_slots_forgets_the_old_one(self) -> None:
        h = Harness().connect().attach()
        h.receive("Health Upgrade")
        h.game.collect_diary(42)
        h.step(6)
        self.assertEqual(h.core.delivered, 1)

        h.disconnect()
        h.storage = None
        h.server_items = []
        h.server_checked = set()
        h.sent_checks = []
        h.connect(identity=("other seed", 0, 2))
        h.game.diaries.clear()
        h.receive("Arcadia Access")
        h.step(4)
        self.assertEqual(h.core.delivered, 1, "only the access item of the new slot")
        self.assertEqual(h.sent_checks, [], "the old slot's diary must not leak into the new one")

    def test_reconnecting_never_moves_backwards(self) -> None:
        h = Harness().connect().attach()
        h.receive("Health Upgrade", "EVE Upgrade")
        h.step(8)
        stale = dict(h.storage, delivered=1)
        h.disconnect()
        h.storage = stale  # as if the last save never reached the server
        h.connect()
        h.step(4)
        self.assertEqual(h.core.delivered, 2)
        self.assertEqual(h.game.commands.count("GiveItem 1 ShockGame.BioAmmoUpgrade"), 1)
        self.assertEqual(h.storage["delivered"], 2, "the server copy is brought up to date")

    def test_unusable_saved_data_is_replaced(self) -> None:
        h = Harness()
        h.storage = {"version": 999, "delivered": 50}
        h.connect().attach()
        h.step(2)
        self.assertEqual(h.core.delivered, 0)
        self.assertEqual(h.storage["version"], core_module.SAVE_VERSION)

    def test_newer_slot_data_warns(self) -> None:
        h = Harness()
        h.slot_data["slot_data_version"] = 99
        h.connect()
        h.step(1)
        self.assertTrue(any("newer" in text for text in h.warnings()))

    def test_status_lines(self) -> None:
        h = Harness().connect().attach()
        with not_known_yet("Invention Components"):
            h.receive("Health Upgrade", "Invention Components")
            h.game.collect_diary(42)
            h.step(8)
        text = "\n".join(h.core.status_lines())
        self.assertIn("Items: 1 of 2 given, 1 the client cannot give yet", text)
        self.assertIn("1 held until you receive Arcadia Access", text)

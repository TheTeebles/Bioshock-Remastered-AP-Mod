"""Research Checks: researching enemies with the Research Camera."""
from __future__ import annotations

import unittest
from collections import Counter

from ..client import game_data
from ..data import RESEARCH_AWARDS, RESEARCH_SUBJECTS, TONICS, LocationCategory, Source
from .bases import BioShockTestBase
from .client_harness import Harness, loc

# From the exported ShockPlayer.uc: the AwardItemClass of each research level that gives a tonic.
AWARD_CLASSES = {
    "SportBoost": "FastTwitch", "SportBoost 2": "FastTwitchTwo", "Scrounger": "ThoroughScavenger",
    "Static Discharge 2": "ChargedBurstsTwo", "Extra Nutrition 3": "HealthyConsumerThree",
    "Natural Camouflage": "ChameleonBlood", "Wrench Jockey 2": "MeleeMasterTwo",
    "Photographer's Eye 2": "EyeForDetailTwo", "Security Expert 2": "SecuritySystemsExpertTwo",
}


class TestTable(unittest.TestCase):
    def test_awards_are_the_research_tonics(self) -> None:
        self.assertCountEqual(RESEARCH_AWARDS, [tonic.name for tonic in TONICS if tonic.source == Source.RESEARCH])
        for name, game_class in AWARD_CLASSES.items():
            self.assertEqual(game_data.GIVE_CLASS[name].rsplit(".", 1)[-1], game_class, name)

    def test_locations(self) -> None:
        self.assertEqual(len(game_data.RESEARCH_LOCATION), 55)
        self.assertEqual(len({subject.key for subject in RESEARCH_SUBJECTS}), 11)


class TestOff(BioShockTestBase):
    def test_no_research_checks_by_default(self) -> None:
        self.assertFalse([location for location in self.multiworld.get_locations(self.player)
                          if location.name.startswith("Research:")])
        self.assertEqual(self.world.fill_slot_data()["research_checks"], 0)


class TestFirstLevel(BioShockTestBase):
    options = {"research_checks": "first_level"}

    def test_one_check_per_subject(self) -> None:
        names = sorted(location.name for location in self.multiworld.get_locations(self.player)
                       if location.name.startswith("Research:"))
        self.assertEqual(len(names), 11)
        self.assertTrue(all(name.endswith(" 1") for name in names))

    def test_awards_are_in_the_pool_once(self) -> None:
        pool = Counter(item.name for item in self.multiworld.itempool)
        for name in RESEARCH_AWARDS:
            self.assertEqual(pool[name], 1, name)


class TestAll(BioShockTestBase):
    options = {"research_checks": "all"}

    def test_later_levels_hold_no_progression(self) -> None:
        location = self.multiworld.get_location("Research: Bouncer 4", self.player)
        access = self.world.create_item("Arcadia Access")
        self.assertFalse(location.item_rule(access))
        first = self.multiworld.get_location("Research: Bouncer 1", self.player)
        self.assertTrue(first.item_rule(access))


class TestClient(unittest.TestCase):
    def harness(self) -> Harness:
        h = Harness(access_items=False)
        h.slot_data["research_checks"] = 2
        return h.connect().attach()

    def test_research_levels_send_checks(self) -> None:
        h = self.harness()
        h.step(4)
        self.assertTrue(h.game.research_awards_emptied)
        h.game.photograph("thuggish")
        self.assertEqual(h.game.photograph("thuggish"), "Thuggish Splicer research level 2")
        h.step(4)
        self.assertIn(loc("Research: Thuggish Splicer 1"), h.sent_checks)
        self.assertIn(loc("Research: Thuggish Splicer 2"), h.sent_checks)
        self.assertNotIn(loc("Research: Thuggish Splicer 3"), h.sent_checks)

    def test_a_save_researched_further_sends_every_level_below(self) -> None:
        h = self.harness()
        h.game.research["Rosie"] = 3
        h.step(4)
        for level in (1, 2, 3):
            self.assertIn(loc(f"Research: Rosie {level}"), h.sent_checks)

    def test_a_game_that_cannot_empty_awards_says_so(self) -> None:
        h = Harness(access_items=False)
        h.slot_data["research_checks"] = 1
        h.game.unsupported_actions.add("empty_research_awards")
        h.connect().attach()
        h.step(4)
        self.assertTrue(any("Research still gives" in text for text in h.warnings()), h.warnings())

    def test_off_asks_for_nothing(self) -> None:
        h = Harness(access_items=False).connect().attach()
        h.step(4)
        self.assertFalse(h.game.research_awards_emptied)
        self.assertEqual(h.game.photograph("bouncer"), "Bouncer research level 1")


if __name__ == "__main__":
    unittest.main()

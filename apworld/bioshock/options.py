from dataclasses import dataclass

from Options import (
    Choice, DeathLink, DefaultOnToggle, OptionGroup, PerGameCommonOptions, Range, StartInventoryPool, Toggle,
)


class LevelAccess(Choice):
    """
    How your checks unlock as the multiworld progresses.

    Access Items: every level after Medical Pavilion has an "<Level> Access" item somewhere in the multiworld.
    You can still play the story straight through, but the client only sends a level's checks once you have its
    access item. Anything you collected earlier is sent the moment the access item arrives.
    Smuggler's Hideout shares Neptune's Bounty Access, Rapture Central Control shares Hephaestus Access and
    Proving Grounds shares Point Prometheus Access.

    Vanilla: no access items. Every check is available from the start.
    """
    display_name = "Level Access"
    option_vanilla = 0
    option_access_items = 1
    default = option_access_items


class CollectibleChecks(Choice):
    """Shared by the hidden collectibles: easy to walk past, so by default nothing important is put on them."""
    option_off = 0
    option_filler_only = 1
    option_all = 2
    alias_false = 0  # on/off spellings, for YAMLs written for an on/off switch
    alias_no = 0
    alias_true = 2
    alias_yes = 2
    alias_on = 2
    default = option_filler_only


class StoryChecks(DefaultOnToggle):
    """
    Bosses and main story objectives send checks (28 checks), for example defeating Dr. Steinman, photographing
    the Spider Splicers or completing Cohen's masterpiece. You cannot miss them.
    """
    display_name = "Story Checks"


class AudioDiaryChecks(CollectibleChecks):
    """
    Whether picking up audio diaries sends checks (122 checks). Some diaries are hard to find.

    Off: audio diaries are not checks.
    Filler Only: they are checks, but only ever hold filler such as ADAM and dollars, or traps. Nobody needs one
    to progress.
    All: they can hold anything, including the items you or other players need to progress. The two in Welcome to
    Rapture stay filler only, because that level cannot be revisited.
    """
    display_name = "Audio Diary Checks"


class LittleSisterChecks(DefaultOnToggle):
    """Rescuing or harvesting each Little Sister sends a check (21 checks)."""
    display_name = "Little Sister Checks"


class PowerToThePeopleChecks(DefaultOnToggle):
    """
    Each Power to the People weapon upgrade station is a check (12 checks). Using a station sends its check and
    closes it, with no upgrade menu. The 12 weapon upgrades are items in the multiworld instead; one for a weapon you
    don't have yet waits until you pick the weapon up.
    """
    display_name = "Power to the People Checks"


class ShopChecks(Choice):
    """
    Buying gene upgrades from the Gatherer's Gardens sends checks. The Gardens' plasmids, tonics, slots and upgrades
    then come from the multiworld instead.

    Off: the Gardens sell their own genes, and buying them sends nothing.
    First Appearances: a line is a check only in the first Garden (in story order) that sells its gene (61 checks).
    Lines that repeat an earlier Garden's gene are sold out.
    All: every line of every Garden is a check (273 checks).

    These checks never hold an item anyone needs to progress, because how much ADAM you have to spend depends on
    how you play. Circus of Values and El Ammo Bandito machines are never checks.
    Each line that is a check shows the name of the multiworld item it holds. Your own BioShock items cost what the game
    charges for them; other items cost 80 ADAM (useful) or 20 (anything else).
    """
    display_name = "Shop Checks"
    option_off = 0
    option_first_appearances = 1
    option_all = 2
    alias_false = 0  # on/off spellings, for YAMLs written when this was an on/off switch
    alias_no = 0
    alias_true = 2
    alias_yes = 2
    alias_on = 2
    default = option_off


class LevelCompletionChecks(DefaultOnToggle):
    """Finishing each level for the first time sends a check (12 checks)."""
    display_name = "Level Completion Checks"


class StartWithResearchCamera(DefaultOnToggle):
    """
    You start with the Research Camera instead of finding it in Neptune's Bounty. Research makes you deal more
    damage to the enemies you photograph, and some later enemies (spider splicers) are hard to hurt without it.
    The camera in Neptune's Bounty is still there and gives film.
    """
    display_name = "Start With Research Camera"


class TrapChance(Range):
    """
    Percentage chance that each filler item is replaced with a trap.
    Traps: EVE Drain (EVE drops to zero), Pickpocket (lose half your dollars), Security Alarm (sets off an alarm).
    Your item pool only has filler when you have more checks than important items, so with audio diary checks
    off there is little or nothing to replace.
    """
    display_name = "Trap Chance"
    range_start = 0
    range_end = 100
    default = 0


@dataclass
class BioShockOptions(PerGameCommonOptions):
    level_access: LevelAccess
    story_checks: StoryChecks
    little_sister_checks: LittleSisterChecks
    power_to_the_people_checks: PowerToThePeopleChecks
    level_completion_checks: LevelCompletionChecks
    audio_diary_checks: AudioDiaryChecks
    shop_checks: ShopChecks
    start_with_research_camera: StartWithResearchCamera
    trap_chance: TrapChance
    death_link: DeathLink
    start_inventory_from_pool: StartInventoryPool


option_groups = [
    OptionGroup("Progression", [LevelAccess]),
    OptionGroup("Checks", [StoryChecks, LittleSisterChecks, PowerToThePeopleChecks, LevelCompletionChecks,
                           AudioDiaryChecks, ShopChecks]),
    OptionGroup("Item Pool", [StartWithResearchCamera, TrapChance]),
]

option_presets = {
    "No Collectibles": {
        "audio_diary_checks": AudioDiaryChecks.option_off,
    },
    "Collectible Hunt": {
        "audio_diary_checks": AudioDiaryChecks.option_all,
    },
    "Audio Diaries Only": {
        "level_access": LevelAccess.option_access_items,
        "audio_diary_checks": AudioDiaryChecks.option_all,
        "story_checks": False,
        "little_sister_checks": False,
        "power_to_the_people_checks": False,
        "level_completion_checks": False,
    },
    "Open Rapture": {
        "level_access": LevelAccess.option_vanilla,
    },
}

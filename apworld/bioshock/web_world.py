from BaseClasses import Tutorial
from worlds.AutoWorld import WebWorld

from .data import GAME_NAME
from .options import option_groups, option_presets


class BioShockWebWorld(WebWorld):
    game = GAME_NAME
    theme = "ocean"

    setup_en = Tutorial(
        "Multiworld Setup Guide",
        "A guide to setting up BioShock Remastered for Archipelago.",
        "English",
        "setup_en.md",
        "setup/en",
        ["Teebs"],
    )
    tutorials = [setup_en]

    option_groups = option_groups
    options_presets = option_presets

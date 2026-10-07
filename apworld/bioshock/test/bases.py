"""Shared by all BioShock tests."""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from glob import glob
from typing import Any

from test.bases import WorldTestBase

import Utils
from BaseClasses import MultiWorld

from ..data import GAME_NAME
from ..world import BioShockWorld


class BioShockTestBase(WorldTestBase):
    game = "BioShock"
    world: BioShockWorld


def generate_multiworld(directory: str, players: Mapping[str, Mapping[str, Any]],
                        seed: int) -> tuple[MultiWorld, str]:
    """Generate a multiworld the way ArchipelagoGenerate does, start inventory, exclusions and output included.

    `players` maps a slot name to its BioShock options. Returns the finished multiworld and the path of the zip.
    """
    import Generate
    import Main

    player_files = os.path.join(directory, "players")
    output = os.path.join(directory, "output")
    os.makedirs(player_files)
    for number, (name, options) in enumerate(players.items(), start=1):
        with open(os.path.join(player_files, f"{number}.yaml"), "w", encoding="utf-8") as file:
            json.dump({"name": name, "game": GAME_NAME, GAME_NAME: dict(options)}, file)  # JSON is valid YAML

    argv = sys.argv
    had_output_path = hasattr(Utils.output_path, "cached_path")
    old_output_path = getattr(Utils.output_path, "cached_path", None)
    sys.argv = [argv[0], "--seed", str(seed), "--spoiler", "0",
                "--player_files_path", player_files, "--outputpath", output]
    try:
        multiworld = Main.main(*Generate.main())
    finally:  # generating redirects Archipelago's output folder; put it back for whatever runs next
        sys.argv = argv
        if had_output_path:
            Utils.output_path.cached_path = old_output_path
        elif hasattr(Utils.output_path, "cached_path"):
            del Utils.output_path.cached_path
    archives = glob(os.path.join(output, "*.zip"))
    assert len(archives) == 1, archives
    return multiworld, archives[0]


def generate_seed(directory: str, players: Mapping[str, Mapping[str, Any]], seed: int) -> str:
    """Like generate_multiworld, for callers that only want the zip."""
    return generate_multiworld(directory, players, seed)[1]

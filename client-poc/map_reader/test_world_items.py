"""
Tests for world_items.py on a made-up two-map survey. Run from an Archipelago checkout with the BioShock world in
worlds/bioshock:

    python -m pytest path/to/client-poc/map_reader/test_world_items.py
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from world_items import candidates  # noqa: E402


def spot(cls, actor, items=(), label=None):
    return {"class": cls, "actor": actor, "label": label or actor, "at": [0, 0, 0], "items": list(items)}


# Listed out of story order on purpose: Neptune's Bounty comes after the Medical Pavilion.
FISHERIES = {"map": "2-Fisheries.bsm", "world": [
    spot("ShotgunAnd00BuckPickup", "ShotgunAnd00BuckPickup0", ["ShotgunAnd00Buck"]),
    spot("SecurityCrate_Safe", "SecurityCrate_Safe2", ["AutoHack"]),
    spot("ActivePlasmidPickup", "ActivePlasmidPickup0", ["SecurityBeacon"]),
    spot("GrenadeLauncherAndFragGrenadesPickup", "GrenadeLauncherAndFragGrenadesPickup0",
         ["GrenadeLauncherAndFragGrenades"]),
    spot("Lockbox", "Lockbox0", ["MedHypo"]),
    spot("EngineeringPlasmidPickup", "EngineeringPlasmidPickup0", ["ShorterAlarm"]),
]}
MEDICAL = {"map": "1-Medical.bsm", "world": [
    spot("ShotgunAnd00BuckPickup", "ShotgunAnd00BuckPickup3", ["ShotgunAnd00Buck"]),
    spot("SecurityCrate_Safe", "SecurityCrate_Safe0"),
    spot("PhysicalPlasmidPickup", "PhysicalPlasmidPickup0", ["MedicineFriendly"]),
    spot("LogPickup", "LogPickup0", ["SomeDiary"]),  # not a world item
]}


def by_name(rows):
    return {row["name"]: row for row in rows}


def test_ids_follow_story_order_not_file_order():
    rows, unknown, duplicates = candidates([FISHERIES, MEDICAL])
    names = by_name(rows)
    assert names["Medical Pavilion - Medical Expert"]["id"] == 6011
    assert names["Neptune's Bounty - Shorten Alarms"]["id"] == 6012
    assert names["Neptune's Bounty - Security Bullseye"]["id"] == 6001
    assert names["Medical Pavilion Safe 1"]["id"] == 6101
    assert names["Neptune's Bounty Safe 1"]["id"] == 6102
    assert unknown == [] and duplicates == []


def test_each_weapon_counts_once_where_it_first_lies():
    rows, _, _ = candidates([FISHERIES, MEDICAL])
    weapons = [row for row in rows if row["kind"] == "weapon"]
    assert [(row["name"], row["id"]) for row in weapons] == [
        ("Medical Pavilion - Shotgun", 6051), ("Neptune's Bounty - Grenade Launcher", 6052)]


def test_safes_are_numbered_per_level_and_keep_their_contents():
    rows, _, _ = candidates([FISHERIES, MEDICAL])
    safes = {row["name"]: row["contents"] for row in rows if row["kind"] == "safe"}
    assert safes == {"Medical Pavilion Safe 1": [], "Neptune's Bounty Safe 1": ["AutoHack"],
                     "Neptune's Bounty Safe 2": ["MedHypo"]}
    assert all(row["vanilla"] is None for row in rows if row["kind"] == "safe")


def test_classes_the_tables_do_not_know_are_reported():
    odd = {"map": "2-Fisheries.bsm", "world": [spot("PhysicalPlasmidPickup", "PhysicalPlasmidPickup9", ["NoSuchTonic"]),
                                                spot("FooAndBarPickup", "FooAndBarPickup0")]}
    rows, unknown, _ = candidates([odd])
    assert unknown == ["Foo", "NoSuchTonic"]
    assert {row["name"] for row in rows} == {"Neptune's Bounty - NoSuchTonic", "Neptune's Bounty - Foo"}


def test_too_many_spots_for_the_id_range_is_refused():
    many = {"map": "2-Fisheries.bsm", "world": [spot("SecurityCrate_Safe", f"SecurityCrate_Safe{i}")
                                                for i in range(100)]}
    with pytest.raises(ValueError, match="safe"):
        candidates([many])


def test_maps_that_are_not_levels_are_left_out():
    autoplay = {"map": "Autoplay.bsm", "world": [spot("SecurityCrate_Safe", "SecurityCrate_Safe0")]}
    rows, _, _ = candidates([autoplay, MEDICAL])
    assert [row["map"] for row in rows if row["kind"] == "safe"] == ["1-Medical.bsm"]

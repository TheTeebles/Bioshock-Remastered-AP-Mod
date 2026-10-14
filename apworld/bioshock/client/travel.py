"""
Open Rapture (level_access: open_rapture): where the game may take the player, from the items they hold and how far
the story has gone. The client works this out and hands it to the game agent as one `travel_rules` action whenever it
changes; the agent makes the bathysphere menu offer exactly these stops and rewrites the scripted level changes that
would cross into a chapter that is still locked.

Everything here comes from the map files (client-poc/map_reader/map_survey.py over the Steam install, 2026-10-14):
each bathysphere station's map and the PlayerStart its arrivals use, and the two scripted level changes that cross
from one chapter into the next (Smuggler's Hideout's submarine to Arcadia, and Ryan's office to Olympus Heights).
The other chapter crossings are bathysphere rides already.
"""
from __future__ import annotations

from collections.abc import Collection
from typing import Any, NamedTuple

from ..data import (
    APOLLO_SQUARE, ARCADIA, CHAPTER_OF_LEVEL, CHAPTERS, FARMERS_MARKET, FORT_FROLIC, FREE_CHAPTERS, MEDICAL_PAVILION,
    OLYMPUS_HEIGHTS, POINT_PROMETHEUS, RAPTURE_CENTRAL_CONTROL, SMUGGLERS_HIDEOUT,
)


class Stop(NamedTuple):
    map: str  # the map, as BathysphereManager.Destinations names it (lower case here)
    level: str
    arrival: str  # the PlayerStart a ride there ends at


# The bathysphere stations, one per chapter. Olympus Heights has none: Apollo Square's is chapter 6's.
STOPS: tuple[Stop, ...] = (
    Stop("1-medical", MEDICAL_PAVILION, "Backtrack_MedicalStart"),
    Stop("2-fisheries", "Neptune's Bounty", "PlayerStart"),
    Stop("3-arcadia", ARCADIA, "ArcBathyEndPS"),
    Stop("4-recreation", FORT_FROLIC, "PlayerStart"),
    Stop("5-hephaestus", "Hephaestus", "EngBathyStartPS"),
    Stop("6-slums", APOLLO_SQUARE, "PlayerStart_FromDeck7Bathy"),
    Stop("7-science", POINT_PROMETHEUS, "FromResidential"),
)
HUB = STOPS[0]  # where a crossing into a locked chapter goes instead: Medical Pavilion's station, made for coming back

# Arcadia's station is at the far end of the level, where the story leaves it. A first visit starts where the
# submarine from Smuggler's Hideout docks, as in the story.
FIRST_ARRIVALS: dict[str, str] = {ARCADIA: "FromFisheriesPS"}


class Crossing(NamedTuple):
    """A scripted level change from one chapter into the next."""
    from_map: str
    to_map: str
    chapter: int  # the chapter it enters


CROSSINGS: tuple[Crossing, ...] = (
    Crossing("2-subbay", "3-arcadia", 3),
    Crossing("5-ryan", "6-resi", 6),
)

# Open Rapture's Level Complete: a level is finished when the next level of its chapter has been reached, or, for the
# last level of a chapter, when the quest that closes its story is completed (quest names from the map files'
# ActionCompleteQuest; the client reads completed quests from the game). Medical Pavilion is finished once any later
# chapter has been reached: the bathysphere ride out of it is the end of its story.
CLOSING_QUESTS: dict[str, frozenset[str]] = {
    SMUGGLERS_HIDEOUT: frozenset({"RunToSubmarine"}),
    ARCADIA: frozenset({"DefendMPRAmbush"}),
    FARMERS_MARKET: frozenset({"DefendMPRAmbush"}),  # back in Arcadia, as the story goes
    FORT_FROLIC: frozenset({"ReplaceThreeMorePhotos"}),
    RAPTURE_CENTRAL_CONTROL: frozenset({"KillRyan"}),
    APOLLO_SQUARE: frozenset({"ReturnToBathysphere"}),
}


def _next_in_chapter() -> dict[str, str]:
    following: dict[str, str] = {}
    for chapter in CHAPTERS:
        for before, after in zip(chapter.levels, chapter.levels[1:]):
            following[before] = after
    following.pop(ARCADIA, None)  # Arcadia sends you to Farmer's Market and takes you back: reaching it ends nothing
    return following


NEXT_IN_CHAPTER = _next_in_chapter()


def open_chapters(held: Collection[str]) -> frozenset[int]:
    """The chapters whose access the player holds, as the world's logic counts it (rules.py)."""
    access = {chapter.number: chapter.access_item for chapter in CHAPTERS}
    opened = {number for number, item in access.items() if item is None or item in held}
    if not {5, *FREE_CHAPTERS} <= opened:
        opened.discard(5)  # Ryan's chapter needs the three before it as well
    return frozenset(opened)


def offered_stops(held: Collection[str], visited: Collection[str]) -> list[Stop]:
    """The stations the bathysphere offers. Chapters 6 and 7 are entered the story's way first (Ryan's office leads to
    Olympus Heights, Apollo Square's bathysphere to Point Prometheus); after that their stations are open for
    coming back."""
    opened = open_chapters(held)
    stops = []
    for stop in STOPS:
        chapter = CHAPTER_OF_LEVEL[stop.level].number
        if chapter not in opened:
            continue
        if chapter == 6 and OLYMPUS_HEIGHTS not in visited:
            continue
        if chapter == 7 and APOLLO_SQUARE not in visited:
            continue
        stops.append(stop)
    return stops


def travel_rules(held: Collection[str], visited: Collection[str]) -> dict[str, Any]:
    """What the agent is told: the stops to offer, where each one puts the player, and the scripted crossings into a
    locked chapter, sent to the hub instead."""
    opened = open_chapters(held)
    stops = offered_stops(held, visited)
    return {
        "stops": [{"map": stop.map,
                   "arrival": FIRST_ARRIVALS.get(stop.level, stop.arrival) if stop.level not in visited
                   else stop.arrival}
                  for stop in stops],
        "redirects": [{"from": crossing.from_map, "to": crossing.to_map, "map": HUB.map, "arrival": HUB.arrival}
                      for crossing in CROSSINGS if crossing.chapter not in opened],
    }


def completed_levels(visited: Collection[str], quests: Collection[str]) -> list[str]:
    """Open Rapture's finished levels (see CLOSING_QUESTS). Proving Grounds is the goal, not a Level Complete."""
    done = []
    for chapter in CHAPTERS:
        for level in chapter.levels:
            if level in NEXT_IN_CHAPTER and NEXT_IN_CHAPTER[level] in visited:
                done.append(level)
            elif level in CLOSING_QUESTS and CLOSING_QUESTS[level] & set(quests):
                done.append(level)
            elif level == MEDICAL_PAVILION and any(CHAPTER_OF_LEVEL[v].number > 1 for v in visited):
                done.append(level)
    return done


def blocked_notice(crossing_to: str, held: Collection[str]) -> str | None:
    """What the player is told when a crossing is sent to the hub."""
    for crossing in CROSSINGS:
        if crossing.to_map == crossing_to and crossing.chapter not in open_chapters(held):
            chapter = next(c for c in CHAPTERS if c.number == crossing.chapter)
            return (f"{chapter.levels[0]} is locked until you receive {chapter.access_item}: "
                    f"the way there took you back to {HUB.level}'s bathysphere.")
    return None


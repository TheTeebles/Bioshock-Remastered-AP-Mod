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

from collections.abc import Collection, Sequence
from typing import Any, NamedTuple

from ..data import (
    APOLLO_SQUARE, ARCADIA, CHAPTER_OF_LEVEL, CHAPTERS, FARMERS_MARKET, FORT_FROLIC, FREE_CHAPTERS, MEDICAL_PAVILION,
    OLYMPUS_HEIGHTS, POINT_PROMETHEUS, RAPTURE_CENTRAL_CONTROL, SMUGGLERS_HIDEOUT,
)
from .game_data import MAP_LEVEL


class Stop(NamedTuple):
    map: str  # the map, as BathysphereManager.Destinations names it (lower case here)
    level: str
    arrival: str  # the PlayerStart a ride there ends at
    to: str | None = None  # where the line goes instead of `map`, when it stands in for a level with no station
    title: str | None = None  # the menu's text for it then


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

# Olympus Heights has no station. Until it has been reached, Apollo Square's line stands in for it and goes where the
# story arrives from Ryan's office, so chapter 6 opens from the menu as soon as its item does.
OLYMPUS_STOP = Stop("6-slums", OLYMPUS_HEIGHTS, "PlayerStart_First", to="6-resi", title=OLYMPUS_HEIGHTS)
# What Ryan's office sets on its way to Olympus Heights (5-Ryan ToResidentialScript), which Olympus Heights' opening
# scripts test. Set while the Olympus Heights line is offered, so a ride in finds the story where the door leaves it.
# The ride in client test 2 (2026-10-15) left the player shut in Tenenbaum's safehouse; this flag is the only thing
# from Ryan's office its scripts read, so it is the first suspect.
OLYMPUS_FLAGS: dict[str, str] = {"global_transitionedFromRyan": "1"}

# Arcadia's station is at the far end of the level, where the story leaves it. A first visit starts where the
# submarine from Smuggler's Hideout docks, as in the story.
FIRST_ARRIVALS: dict[str, str] = {ARCADIA: "FromFisheriesPS"}


class Crossing(NamedTuple):
    """A scripted level change from one chapter into the next."""
    from_map: str
    to_map: str
    chapter: int  # the chapter it enters
    # Where it goes when the hub's names do not fit its strings (the agent cannot always make them longer): back into
    # the level it starts from, at the arrival point of the way it leads out. Both names are no longer than the
    # crossing's own ("2-SubBay" for "3-Arcadia", "FromArcadiaPS" for "FromFisheriesPS"; "5-Ryan" for "6-Resi",
    # "FromBulkhead" for "PlayerStart_First"), so they always fit.
    bounce_map: str
    bounce_arrival: str
    arrival: str  # where the story's crossing arrives


CROSSINGS: tuple[Crossing, ...] = (
    Crossing("2-subbay", "3-arcadia", 3, "2-subbay", "FromArcadiaPS", "FromFisheriesPS"),
    Crossing("5-ryan", "6-resi", 6, "5-ryan", "FromBulkhead", "PlayerStart_First"),
)

# Open Rapture's Level Complete: a level is finished when the next level of its chapter has been reached, or, for the
# last level of a chapter, when the quest that closes its story is completed (quest names from the map files'
# ActionCompleteQuest; the client reads completed quests from the game). Medical Pavilion is finished with Steinman's
# key, or once any later chapter has been reached: the bathysphere ride out of it is the end of its story. The key
# matters when the first chapter item is on Medical's own Level Complete: no ride out is open before it is sent.
CLOSING_QUESTS: dict[str, frozenset[str]] = {
    MEDICAL_PAVILION: frozenset({"QuarantineKey"}),
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


def open_chapters(held: Collection[str], order: Sequence[int] = (),
                  finished: Collection[int] = ()) -> frozenset[int]:
    """The chapters the player can reach, as the world's logic counts it (rules.py, regions.py).

    With Chapter Order shuffled, `order` is the seed's order of chapters 2 to 4: each of them, and Ryan's chapter
    after the last, opens only once the one before it is `finished` (finished_chapters) as well. Chapters opened stay
    open, so the bathysphere still goes back to them."""
    access = {chapter.number: chapter.access_item for chapter in CHAPTERS}
    opened = {number for number, item in access.items() if item is None or item in held}
    for before, number in zip(order, (*order[1:], 5)):
        if before not in opened or before not in finished:
            opened.discard(number)
    if not {5, *FREE_CHAPTERS} <= opened:
        opened.discard(5)  # Ryan's chapter needs the three before it as well
    for number in (6, 7):  # and the story goes on from there in order
        if number - 1 not in opened:
            opened.discard(number)
    return frozenset(opened)


def finished_chapters(visited: Collection[str], quests: Collection[str]) -> frozenset[int]:
    """The chapters whose story is done: their last level is finished (completed_levels)."""
    done = set(completed_levels(visited, quests))
    return frozenset(chapter.number for chapter in CHAPTERS if chapter.levels[-1] in done)


def why_locked(number: int, held: Collection[str], order: Sequence[int] = (),
               finished: Collection[int] = ()) -> str:
    """What the player still needs for a chapter that is not open: its item, the chapter before it in the seed's
    order, or both."""
    chapter = next(c for c in CHAPTERS if c.number == number)
    needs = [] if chapter.access_item in held else [f"you receive {chapter.access_item}"]
    before = dict(zip((*order[1:], 5), order)).get(number)
    if before is not None and before not in finished:
        needs.append(f"you finish {next(c for c in CHAPTERS if c.number == before).levels[0]}")
    return " and ".join(needs) or "the chapters before it are open"


def offered_stops(held: Collection[str], visited: Collection[str], order: Sequence[int] = (),
                  finished: Collection[int] = ()) -> list[Stop]:
    """The stations the bathysphere offers: every chapter the player can reach, as soon as its item arrives. Chapter
    6 is offered as Olympus Heights (its first level) until that has been reached, then as Apollo Square's station."""
    opened = open_chapters(held, order, finished)
    stops = []
    for stop in STOPS:
        chapter = CHAPTER_OF_LEVEL[stop.level].number
        if chapter not in opened:
            continue
        if chapter == 6 and OLYMPUS_HEIGHTS not in visited:
            stop = OLYMPUS_STOP
        stops.append(stop)
    return stops


def travel_rules(held: Collection[str], visited: Collection[str], order: Sequence[int] = (),
                 finished: Collection[int] = ()) -> dict[str, Any]:
    """What the agent is told: the stops to offer, where each one puts the player, and the scripted crossings into a
    locked chapter, sent to the hub instead. `order` and `finished`: see open_chapters."""
    opened = open_chapters(held, order, finished)
    stops = offered_stops(held, visited, order, finished)
    return {
        "stops": [{"map": stop.map,
                   "arrival": FIRST_ARRIVALS.get(stop.level, stop.arrival) if stop.level not in visited
                   else stop.arrival,
                   **({"to": stop.to, "title": stop.title} if stop.to else {})}
                  for stop in stops],
        "flags": dict(OLYMPUS_FLAGS) if OLYMPUS_STOP in stops else {},
        "redirects": [{"from": crossing.from_map, "to": crossing.to_map, "map": HUB.map, "arrival": HUB.arrival,
                       "fallback": {"map": crossing.bounce_map, "arrival": crossing.bounce_arrival}}
                      for crossing in CROSSINGS if crossing.chapter not in opened],
        # A save made while a crossing was redirected keeps it so: once its chapter is open it is put back.
        "restores": [{"from": crossing.from_map, "to": crossing.to_map, "arrival": crossing.arrival,
                      "hub": HUB.map, "hubArrival": HUB.arrival}
                     for crossing in CROSSINGS if crossing.chapter in opened],
    }


# For a slot that is not Open Rapture, after rules were sent in this session: undo them.
TRAVEL_RULES_OFF: dict[str, Any] = {"off": True}


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


def locked_way_on(level: str, held: Collection[str], order: Sequence[int] = (),
                  finished: Collection[int] = ()) -> str | None:
    """Said on entering a level whose way on leads into a chapter still locked."""
    for crossing in CROSSINGS:
        if MAP_LEVEL.get(crossing.from_map) == level and crossing.chapter not in open_chapters(held, order, finished):
            chapter = next(c for c in CHAPTERS if c.number == crossing.chapter)
            return (f"The way on to {chapter.levels[0]} is locked until "
                    f"{why_locked(crossing.chapter, held, order, finished)}. "
                    f"Use a bathysphere to go elsewhere meanwhile.")
    return None


def blocked_notice(crossing_to: str, landed: str | None, held: Collection[str], order: Sequence[int] = (),
                   finished: Collection[int] = ()) -> str | None:
    """What the player is told when a crossing into a locked chapter went somewhere else (`landed`, a map)."""
    for crossing in CROSSINGS:
        if crossing.to_map == crossing_to and crossing.chapter not in open_chapters(held, order, finished):
            chapter = next(c for c in CHAPTERS if c.number == crossing.chapter)
            if landed == crossing.bounce_map:
                where = (f"you stay in {MAP_LEVEL.get(landed, landed)}; go back to a bathysphere "
                         f"({HUB.level}'s goes everywhere you can)")
            else:
                where = f"the way there took you back to {HUB.level}'s bathysphere"
            needs = why_locked(crossing.chapter, held, order, finished)
            return f"{chapter.levels[0]} is locked until {needs}: {where}."
    return None

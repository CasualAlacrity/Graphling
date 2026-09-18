from types import SimpleNamespace
from typing import Any, TypeVar

import jellyfish
from pydantic import BaseModel
from rapidfuzz import fuzz, process
from rapidfuzz.utils import default_process

from tools.uexcorp.aliases import canonicalize
from tools.uexcorp.reference_cache import TerminalType

_HasNameAndCode = TypeVar("_HasNameAndCode")

DEFAULT_NEAR_DISTANCE = 25  # gm — used when 'near' is set without an explicit max_distance

# Below this score (but still >= score_cutoff), treat a match as a guess worth confirming
# rather than a certainty — e.g. a voice-transcribed "railing" matching "Railen" at 60,
# one point above the cutoff, with zero margin. Only meaningful to callers using
# match_by_name_or_code_with_score; match_by_name_or_code's plain callers are unaffected.
LOW_CONFIDENCE_MAX = 80

# How close two phonetic encodings must be before the fallback will commit. Measured
# against real mishearings: "railing" -> Railen scores 86 here, "Raelin" -> Railen 100.
PHONETIC_MIN = 85


class OrbitDistance(BaseModel):
    orbit_destination_name: str
    distance: float


def _choices_and_lookup(items: list[_HasNameAndCode]) -> tuple[list[str], list[_HasNameAndCode]]:
    choices = []
    lookup = []
    for item in items:
        choices.append(item.name)
        lookup.append(item)

        code = getattr(item, "code", None)
        if code:
            choices.append(code)
            lookup.append(item)

        # Vehicles have name_full (e.g. "Gatac Railen") alongside the short name
        # ("Railen") — manufacturer-prefixed phrasing is how ships are naturally
        # referred to (including by trade_run_status's own output), so leaving it out
        # of the match pool meant a correctly-typed full name still scored worse than
        # it should have, purely from the unmatched "Gatac " prefix diluting the score.
        name_full = getattr(item, "name_full", None)
        if name_full:
            choices.append(name_full)
            lookup.append(item)

        # Terminals carry a short human nickname ("ARC-L1", "ArcCorp 045", "Everus
        # Harbor") alongside the long official name ("Admin - ARC-L1", "ArcCorp Mining
        # Area 045"). The nickname is what a pilot actually says, and the overlay already
        # matches on it — leaving it out meant the spoken form only ever matched the
        # official name by partial credit. Additive: another way to reach the same item,
        # so it can't restrict any workflow.
        nickname = getattr(item, "nickname", None)
        if nickname:
            choices.append(nickname)
            lookup.append(item)
    return choices, lookup


def match_by_name_or_code(
        query: str, items: list[_HasNameAndCode], score_cutoff: int = 60, scorer: Any = fuzz.WRatio,
) -> _HasNameAndCode | None:
    choices, lookup = _choices_and_lookup(items)
    match = process.extractOne(
        query, choices, score_cutoff=score_cutoff, scorer=scorer, processor=default_process
    )
    return lookup[match[2]] if match else None


def match_by_name_or_code_with_score(
        query: str, items: list[_HasNameAndCode], score_cutoff: int = 60, scorer: Any = fuzz.WRatio,
) -> tuple[_HasNameAndCode, float] | None:
    """Same matching as match_by_name_or_code, but also returns the match score so a
    caller can tell a barely-passing match (near score_cutoff) from a confident one, and
    hedge accordingly instead of silently committing to a guess. Kept separate from
    match_by_name_or_code — that function has 10+ call sites that don't need this.

    scorer defaults to WRatio (rapidfuzz's own default) — good for short queries against
    long official names (e.g. "Orison" against a terminal's full official name), since it
    rewards a query matching a substring/fragment of the choice. Vehicle name matching
    wants the opposite: fuzz.token_sort_ratio, a whole-string comparison — WRatio's
    substring credit is what let unrelated short names like "600i Touring" outscore the
    actually-closest "Railen" for a garbled query like "railing".

    default_process lowercases and strips punctuation before comparing. Without it
    rapidfuzz compares raw strings, so a lowercase query lost a whole character of
    similarity against a capitalised catalog name — "caranite" tied with "Laranite" and
    "Taranite" at 88 against the real "Caranite", and extractOne returned the first of
    them. That was a silent wrong commodity, above the confidence threshold, no hedge."""
    choices, lookup = _choices_and_lookup(items)
    match = process.extractOne(
        query, choices, score_cutoff=score_cutoff, scorer=scorer, processor=default_process
    )
    if not match:
        return None
    return lookup[match[2]], match[1]


def _top_matches(
        query: str, items: list[_HasNameAndCode], score_cutoff: int, scorer: Any,
) -> tuple[list[_HasNameAndCode], float]:
    """Every distinct item tied at the best score, and that score.

    Returning the tie rather than one winner is what lets resolve_or_hedge tell "this is
    the answer" from "several answers fit equally". extractOne silently returns whichever
    tied candidate it saw first, which is fine when names are distinct and dangerous when
    they differ only by a number — "ArcCorp Mining Area 045/048/141" score identically
    against a query with no number in it."""
    choices, lookup = _choices_and_lookup(items)
    results = process.extract(
        query, choices, scorer=scorer, processor=default_process,
        score_cutoff=score_cutoff, limit=None,
    )
    if not results:
        return [], 0.0

    best_score = results[0][1]
    winners = []
    for _choice, score, index in results:
        if score < best_score:
            break
        item = lookup[index]
        # One item contributes several choices (name, code, name_full) — those aren't a tie.
        if item not in winners:
            winners.append(item)
    return winners, best_score


def _phonetic_key(text: str) -> str:
    """Metaphone encoding, per word. Encodes how a name *sounds* rather than how it's
    spelled, which is the right question for speech input: "Railen" and "Raelin" both
    encode to RLN, while "Javelin" — which outscores Railen on letter-distance — is JFLN."""
    parts = []
    for word in text.split():
        parts.append(jellyfish.metaphone(word))
    return " ".join(parts)


def _phonetic_match(query: str, items: list[_HasNameAndCode]) -> _HasNameAndCode | None:
    """Best phonetic match, but only when it's unambiguous.

    Metaphone discards digits, so whole families of catalog names collapse onto one key —
    every "ArcCorp Mining Area NNN" encodes identically, as do "Admin - HUR-L1".."L5" and
    "M50"/"M80". Committing to the top scorer would confidently send a pilot to the wrong
    mining area. So a tie between distinct items is treated as no answer, and the caller
    hedges exactly as it would have before."""
    query_key = _phonetic_key(query)
    if not query_key.strip():
        return None

    choices, lookup = _choices_and_lookup(items)

    best_score = 0.0
    winners = []
    for index, choice in enumerate(choices):
        score = fuzz.token_sort_ratio(query_key, _phonetic_key(choice))
        if score < PHONETIC_MIN:
            continue
        item = lookup[index]
        if score > best_score:
            best_score = score
            winners = [item]
        elif score == best_score and item not in winners:
            winners.append(item)

    if len(winners) != 1:
        return None
    return winners[0]


def resolve_or_hedge(
        query: str, items: list[_HasNameAndCode], label: str, score_cutoff: int = 60, scorer: Any = fuzz.WRatio,
) -> tuple[_HasNameAndCode | None, str | None]:
    """Match + confidence-check + standard error wording, in one place — every tool that
    resolves a pilot-spoken name (ship, location, ...) needs the same three things: fail
    plainly if nothing matches, hedge instead of silently committing if the match is
    low-confidence, and never name the low-confidence guess (naming it just hands the
    model a ready-made string to resubmit as fact instead of actually asking the pilot).

    A missing confidence check on terminal matching (only ship matching had it) let a
    live trace commit to a completely wrong location — this exists so that gap can't
    recur silently at some future call site the way it did here.

    Returns (item, None) on a confident match, or (None, message) otherwise — callers
    should `return` the message directly on failure, same as every other tool error.

    Three layers, each only reached when the previous one didn't settle it:

    1. Alias rewrite for known pilot slang ("connie" -> Constellation). A closed set, so
       a lookup is correct by construction — see tools/uexcorp/aliases.py.
    2. Fuzzy string match, which must land on a single best candidate. A tie is treated as
       not knowing: several names scoring identically means the query didn't contain
       whatever distinguishes them, and picking one would be inventing that detail.
    3. Phonetic fallback, for mishearings string distance can't see. This only ever
       converts a hedge into a match, and abstains on ties for the same reason.
    """
    # Messages always quote what the pilot actually said, never the alias-rewritten form —
    # being told ALICE didn't catch "Constellation" when you said "connie" is baffling.
    spoken = query
    query = canonicalize(query, label)

    winners, score = _top_matches(query, items, score_cutoff, scorer)
    if len(winners) == 1 and score >= LOW_CONFIDENCE_MAX:
        return winners[0], None

    sounds_like = _phonetic_match(query, items)
    if sounds_like is not None:
        return sounds_like, None

    if not winners:
        return None, f"Couldn't find a {label} matching '{spoken}'."

    message = f"Didn't catch which {label} you meant by '{spoken}' clearly enough to be sure — can you say it again?"
    return None, message


def filter_by_match(rows, query, candidates, attr):
    if not query:
        return rows

    match = match_by_name_or_code(query, candidates)
    if not match:
        return rows

    result = []
    for r in rows:
        if getattr(r, attr) == match.name:
            result.append(r)
    return result


def _find_orbit_by_name(cache, orbit_name):
    for o in cache.orbits:
        if o.name == orbit_name:
            return o
    return None


def find_commodity_by_id(cache, commodity_id):
    for c in cache.commodities:
        if c.id == commodity_id:
            return c
    return None


async def filter_by_location(rows, cache, client, star_system=None, orbit=None, terminal=None, moon=None,
                              near=None, max_distance=None):
    """The star_system/orbit/terminal/moon + optional near/distance filter sequence shared by
    every UEX price/rental/yield tool. Terminal filtering and the near/distance concept don't
    apply to everything (e.g. mining locations, which filter four different result lists with
    no terminal or distance axis) — those stay hand-rolled rather than being forced through here.
    """
    rows = filter_by_match(rows, star_system, cache.star_systems, "star_system_name")
    rows = filter_by_match(rows, orbit, cache.orbits, "orbit_name")
    rows = filter_by_match(rows, terminal, cache.terminals, "terminal_name")
    rows = filter_by_match(rows, moon, cache.moons, "moon_name")

    if near:
        effective_max_distance = max_distance if max_distance is not None else DEFAULT_NEAR_DISTANCE
        rows = await filter_by_distance(rows, near, effective_max_distance, cache, client)

    return rows


def resolve_near_anchor(near: str, cache) -> tuple[int, int, str] | None:
    """Resolves a name (orbit, moon, or terminal) to (orbit_id, star_system_id,
    orbit_name) — the anchor point 'near X' radius searches are computed from. Tries an
    orbit match first, then a moon (using its own orbit directly), then falls back to a
    terminal's own orbit. Returns None if nothing resolves.

    Shared by filter_by_distance and best_route's region search (docs/trade-route-
    tracker.md's "Known gaps" — best_route was the one location-taking tool that hadn't
    adopted this resolution order) — this is the exact chain both need, extracted so
    there's one copy of it, not two drifting independently."""
    orbit = match_by_name_or_code(near, cache.orbits)
    if orbit:
        return orbit.id, orbit.id_star_system, orbit.name

    moon = match_by_name_or_code(near, cache.moons)
    if moon:
        return moon.id_orbit, moon.id_star_system, moon.orbit_name

    terminal = match_by_name_or_code(near, cache.terminals)
    if terminal and terminal.orbit_name:
        matched_orbit = _find_orbit_by_name(cache, terminal.orbit_name)
        if matched_orbit:
            return matched_orbit.id, matched_orbit.id_star_system, matched_orbit.name

    return None


async def filter_by_distance(rows, near, max_distance, cache, client):
    anchor = resolve_near_anchor(near, cache)
    if anchor is None:
        return rows
    origin_id, origin_system_id, origin_name = anchor

    distances = await client.get_orbit_distances(origin_id, origin_system_id)

    parsed = []
    for d in distances:
        parsed.append(OrbitDistance.model_validate(d))

    lookup = {}
    for d in parsed:
        lookup[d.orbit_destination_name] = d.distance
    lookup[origin_name] = 0

    result = []
    for r in rows:
        if r.orbit_name in lookup and lookup[r.orbit_name] <= max_distance:
            result.append(r)
    return result


def trade_terminals(cache) -> list:
    """Only the terminals that can actually be an end of a commodity trade route.

    **This is a caller-side filter, for hauling tools only.** It is deliberately NOT
    applied inside terminals_near/terminals_within — those answer "what is in this
    region", and what counts as a valid terminal is the workflow's decision, not theirs.
    A mining tool, or "where can I buy Trawler Scraping Modules", wants a different pool
    entirely, and would be silently broken by a shared restriction.

    826 terminals in the catalog; 161 are COMMODITY. The rest are clothing shops, pizza
    counters, pharmacies, fuel, rentals and refineries — 481 `item` terminals alone. They
    can't buy or sell cargo, so including them in a trade-route search is never right, and
    it caused real wrong answers: "Port Tressler" resolved to *Pizza - Port Tressler*, and
    "Everus" to *Pizza - Everus Harbor*, both confidently.

    COMMODITY_RAW is deliberately excluded rather than overlooked — those are the
    "Refinery Ore Sales" terminals, and UEX returns **zero** commodity routes with any of
    them as origin, so they'd be pure wasted fan-out. Verified against the live API
    2026-09-18.

    This mirrors what the overlay already does (uex_lookup.py builds its terminal dropdown
    from COMMODITY only) — the two paths had diverged, same as the autoload patch had.
    """
    result = []
    for terminal in cache.terminals:
        if terminal.type == TerminalType.COMMODITY:
            result.append(terminal)
    return result


def cargo_capable_vehicles(cache) -> list:
    """Vehicles that can actually haul — the pool a trading workflow should match against.

    **Caller-side filter, like trade_terminals.** Do NOT push this into resolve_or_hedge:
    travel_time, vehicle_rental and vehicle_purchase all legitimately want the full
    catalog, and a fighter's ETA or rental price is a perfectly good question.

    Excludes two groups that can never be a trade run's ship, and whose presence actively
    hurt matching: 47 concept vehicles that aren't flyable in-game at all, and 146 with no
    cargo capacity. Javelin is both — scu=0, is_concept=1 — and it was outscoring Railen
    at 77 for a misheard "Raelin", competing for a slot it could never legitimately fill.
    280 names drop to 112.
    """
    result = []
    for vehicle in cache.vehicles:
        if vehicle.is_concept:
            continue
        if not vehicle.scu or vehicle.scu < 1:
            continue
        result.append(vehicle)
    return result


async def terminals_near(
        near: str, terminals: list, cache, client, max_distance: int = DEFAULT_NEAR_DISTANCE,
) -> list | None:
    """Every terminal within max_distance Gm of a resolved orbit/moon/terminal anchor —
    the "near X" radius mode — sorted closest-first so a caller that wants to cap a
    fan-out search can just slice the front of the list. Returns None if `near` doesn't
    resolve to anything at all (caller should hedge); an empty list is a real "nothing
    in range" answer, distinct from "couldn't even find X"."""
    anchor = resolve_near_anchor(near, cache)
    if anchor is None:
        return None
    origin_id, origin_system_id, origin_name = anchor

    distances = await client.get_orbit_distances(origin_id, origin_system_id)
    parsed = [OrbitDistance.model_validate(d) for d in distances]
    lookup = {d.orbit_destination_name: d.distance for d in parsed}
    lookup[origin_name] = 0

    in_range = [t for t in terminals if t.orbit_name in lookup and lookup[t.orbit_name] <= max_distance]
    return sorted(in_range, key=lambda t: lookup[t.orbit_name])


def _places_in(pool: list) -> dict[str, list]:
    """Trade terminals grouped by the station, city or outpost they sit at.

    A place is neither an orbit nor a terminal, and without this grouping it fell between
    the two: a pilot asking for a route from Orison names somewhere with *three* commodity
    terminals (TDD at Cloudview Center, Orison Municipal Services at Providence Platform,
    and Admin - Seraphim), so exact-terminal matching could only hedge. "A route from
    Orison" plainly means any of them — deciding which is the search's job, not the
    pilot's."""
    places: dict[str, list] = {}
    for terminal in pool:
        for place_name in (terminal.space_station_name, terminal.city_name, terminal.outpost_name):
            if not place_name:
                continue
            if place_name not in places:
                places[place_name] = []
            places[place_name].append(terminal)
    return places


def _exact_name(region: str, names) -> str | None:
    wanted = region.strip().casefold()
    for name in names:
        if name and name.casefold() == wanted:
            return name
    return None


def _fuzzy_name(region: str, names) -> str | None:
    # Whole-string comparison, never WRatio. WRatio's substring credit scored "Stanton" at
    # 90 against the station "Terra Gateway (Stanton)", and matched "GrimHEX" to the orbit
    # "Xi" — short names inflate it badly. You either named the place or you didn't.
    candidates = []
    for name in names:
        if name:
            candidates.append(SimpleNamespace(name=name))

    match = match_by_name_or_code(region, candidates, scorer=fuzz.token_sort_ratio)
    if match is None:
        return None
    return match.name


def terminals_within(region: str, terminals: list, cache) -> list | None:
    """Every terminal from `terminals` inside a named place, orbit or star system — the
    "in X"/"on X"/"within X" containment mode, no radius/distance computation at all.
    Returns None if `region` doesn't resolve to any of them.

    The caller supplies the pool, deliberately. This function answers "what is in this
    region"; which terminals are *valid* is the workflow's decision — hauling wants
    commodity terminals, an item-price lookup wants shops, mining wants something else
    again.

    **Every exact match beats every fuzzy one**, and only then is the narrower scope
    preferred. Ordering by scope alone got this wrong twice: "Stanton" fuzzy-matched the
    station "Ruin Station" (Stanton and Station are one letter apart) and returned a
    single terminal instead of the system's 117; "Pyro" fuzzy-matched the orbit "Pyro I"
    and returned 2 instead of 35. Both are exact names of star systems, and an exact name
    is what the pilot said.

    Within one confidence tier, most-specific wins — "Orison" is a city inside the
    Crusader orbit, and answering with all 23 Crusader terminals when they named one city
    would be ignoring what they said."""
    region = canonicalize(region, "location")
    pool = terminals

    places = _places_in(pool)
    orbit_names = [o.name for o in cache.orbits]
    system_names = [s.name for s in cache.star_systems]

    place = _exact_name(region, places)
    if place:
        return places[place]

    orbit = _exact_name(region, orbit_names)
    if orbit:
        return [t for t in pool if t.orbit_name == orbit]

    system = _exact_name(region, system_names)
    if system:
        return [t for t in pool if t.star_system_name == system]

    place = _fuzzy_name(region, places)
    if place:
        return places[place]

    orbit = _fuzzy_name(region, orbit_names)
    if orbit:
        return [t for t in pool if t.orbit_name == orbit]

    system = _fuzzy_name(region, system_names)
    if system:
        return [t for t in pool if t.star_system_name == system]

    return None

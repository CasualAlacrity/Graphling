"""Covers resolve_or_hedge's three layers — alias rewrite, fuzzy string match, phonetic
fallback — and the guards that stop each one committing to a wrong answer.

Two regressions are pinned here:

  - "caranite" resolved to *Laranite*, confidently and above the hedge threshold, because
    rapidfuzz compares raw strings by default and a lowercase query lost a character of
    similarity against every capitalised candidate. Caranite, Laranite and Taranite all
    tied at 88 and extractOne returned whichever came first.
  - "railing" and "Raelin" are near-homophones of Railen that string distance can't see —
    "Raelin" scored *Javelin* higher than Railen, despite sounding nothing like it.
"""
from types import SimpleNamespace

import pytest
from rapidfuzz import fuzz

from tools.uexcorp.matching import (
    cargo_capable_vehicles,
    resolve_or_hedge,
    terminals_within,
    trade_terminals,
)
from tools.uexcorp.reference_cache import TerminalType


def _item(name, code=None):
    return SimpleNamespace(name=name, code=code)


SHIPS = [
    _item("Railen"),
    _item("Javelin"),
    _item("Paladin"),
    _item("Caterpillar"),
    _item("Mercury Star Runner"),
    _item("Cutlass Black"),
    _item("Reclaimer"),
]

COMMODITIES = [
    _item("Caranite", "CARA"),
    _item("Carinite", "CARI"),
    _item("Laranite", "LARA"),
    _item("Taranite", "TARA"),
    _item("Quantainium", "QUAN"),
]

# Metaphone discards digits, so these all share one phonetic key. The fallback must
# refuse to choose between them rather than sending a pilot to the wrong site.
MINING_AREAS = [
    _item("ArcCorp Mining Area 045"),
    _item("ArcCorp Mining Area 048"),
    _item("ArcCorp Mining Area 141"),
]


def _ship(query):
    return resolve_or_hedge(query, SHIPS, "ship", scorer=fuzz.token_sort_ratio)


@pytest.mark.parametrize("query", ["Railen", "railen", "RAILEN"])
def test_exact_ship_resolves_regardless_of_case(query):
    item, error = _ship(query)
    assert error is None
    assert item.name == "Railen"


@pytest.mark.parametrize("misheard", ["railing", "Raelin"])
def test_mishearings_resolve_phonetically(misheard):
    """Neither reaches the confidence threshold on string distance — "Raelin" actually
    scores Javelin higher — but both encode to Railen's phonetic key."""
    item, error = _ship(misheard)
    assert error is None
    assert item.name == "Railen"


def test_nonsense_does_not_resolve_to_a_ship():
    """The failure mode an LLM resolver showed in testing: "banana" confidently returning
    a real ship. Every layer here must decline."""
    item, error = _ship("banana")
    assert item is None
    assert error is not None


def test_nickname_resolves_through_alias_table():
    item, error = _ship("cat")
    assert error is None
    assert item.name == "Caterpillar"


def test_initialism_resolves_through_alias_table():
    item, error = _ship("MSR")
    assert error is None
    assert item.name == "Mercury Star Runner"


def test_lowercase_query_does_not_resolve_to_a_similar_commodity():
    """The Caranite/Laranite regression."""
    item, error = resolve_or_hedge("caranite", COMMODITIES, "commodity")
    assert error is None
    assert item.name == "Caranite"


def test_near_identical_commodities_stay_distinct():
    caranite, _ = resolve_or_hedge("caranite", COMMODITIES, "commodity")
    carinite, _ = resolve_or_hedge("carinite", COMMODITIES, "commodity")
    assert caranite.name == "Caranite"
    assert carinite.name == "Carinite"


def test_phonetic_fallback_abstains_when_digits_collide():
    """All three areas share a phonetic key. A garbled query must hedge, never pick one —
    committing here would send a pilot to a different mining site."""
    item, error = resolve_or_hedge("arcorp mining area", MINING_AREAS, "location")
    assert item is None
    assert error is not None


def test_exact_numbered_location_still_resolves():
    item, error = resolve_or_hedge("ArcCorp Mining Area 141", MINING_AREAS, "location")
    assert error is None
    assert item.name == "ArcCorp Mining Area 141"


def test_hedge_message_quotes_what_the_pilot_said():
    """Not the alias-rewritten form — being told ALICE didn't catch "Constellation" when
    you said "connie" is baffling."""
    _, error = resolve_or_hedge("connie", SHIPS, "ship", scorer=fuzz.token_sort_ratio)
    assert error is not None
    assert "connie" in error
    assert "Constellation" not in error


# --- region resolution -------------------------------------------------------

def _terminal(name, *, station=None, city=None, outpost=None, orbit=None, system=None):
    return SimpleNamespace(
        name=name, code=None, type=TerminalType.COMMODITY,
        space_station_name=station, city_name=city, outpost_name=outpost,
        orbit_name=orbit, star_system_name=system,
    )


CACHE = SimpleNamespace(
    terminals=[
        _terminal("TDD - Cloudview Center - Orison", city="Orison", orbit="Crusader", system="Stanton"),
        _terminal("Orison Municipal Services", city="Orison", orbit="Crusader", system="Stanton"),
        _terminal("Admin - GrimHEX", station="Green Imperial Housing Exchange",
                  orbit="Crusader", system="Stanton"),
        _terminal("Admin - Ruin Station", station="Ruin Station", orbit="Pyro I", system="Pyro"),
        _terminal("Platinum Bay - Checkmate", station="Checkmate", orbit="Pyro II", system="Pyro"),
        # Not a commodity terminal — must never appear in any region result.
        SimpleNamespace(name="Pizza - Orison", code=None, type=TerminalType.ITEM,
                        space_station_name=None, city_name="Orison",
                        outpost_name=None, orbit_name="Crusader", star_system_name="Stanton"),
    ],
    orbits=[SimpleNamespace(name="Crusader", code=None), SimpleNamespace(name="Pyro I", code=None),
            SimpleNamespace(name="Pyro II", code=None)],
    star_systems=[SimpleNamespace(name="Stanton", code=None), SimpleNamespace(name="Pyro", code=None)],
)


def test_place_name_resolves_to_its_terminals():
    """A pilot naming a city means every trade terminal there, not a hedge over which."""
    found = terminals_within("Orison", trade_terminals(CACHE), CACHE)
    assert sorted(t.name for t in found) == [
        "Orison Municipal Services", "TDD - Cloudview Center - Orison",
    ]


def test_region_helper_does_not_filter_on_its_own():
    """The workflow decides what counts, not the helper. Hauling passes
    trade_terminals(); an item-price or mining lookup needs a different pool, and would
    be silently broken if this filtered for them."""
    hauling = terminals_within("Orison", trade_terminals(CACHE), CACHE)
    everything = terminals_within("Orison", CACHE.terminals, CACHE)

    assert all(t.type == TerminalType.COMMODITY for t in hauling)
    assert any(t.type == TerminalType.ITEM for t in everything)


def test_exact_system_beats_fuzzy_place():
    """"Stanton" fuzzy-matches the station "Ruin Station" — the words are one letter
    apart — which returned a single terminal instead of the whole system."""
    found = terminals_within("Stanton", trade_terminals(CACHE), CACHE)
    assert len(found) == 3
    assert all(t.star_system_name == "Stanton" for t in found)


def test_exact_system_beats_fuzzy_orbit():
    """"Pyro" fuzzy-matches the orbit "Pyro I", which returned one orbit's terminals
    instead of the system's."""
    found = terminals_within("Pyro", trade_terminals(CACHE), CACHE)
    assert len(found) == 2
    assert all(t.star_system_name == "Pyro" for t in found)


def test_orbit_resolves_when_it_is_the_exact_name():
    found = terminals_within("Crusader", trade_terminals(CACHE), CACHE)
    assert len(found) == 3


def test_alias_applies_to_region_lookup():
    """UEX records the station as "Green Imperial Housing Exchange"; nobody says that."""
    found = terminals_within("GrimHEX", trade_terminals(CACHE), CACHE)
    assert [t.name for t in found] == ["Admin - GrimHEX"]


def test_unknown_region_is_unresolved_not_empty():
    assert terminals_within("nowhere at all", trade_terminals(CACHE), CACHE) is None


def test_nickname_is_matchable():
    """UEX gives terminals a short human nickname ("Everus Harbor") alongside the long
    official name ("Admin - Everus Harbor"). The nickname is what pilots say."""
    terminals = [
        SimpleNamespace(name="Admin - Everus Harbor", code=None, nickname="Everus Harbor"),
        SimpleNamespace(name="ArcCorp Mining Area 045", code=None, nickname="ArcCorp 045"),
    ]
    item, error = resolve_or_hedge("Everus Harbor", terminals, "location")
    assert error is None
    assert item.name == "Admin - Everus Harbor"


def test_cargo_capable_pool_excludes_concepts_and_empty_holds():
    """Javelin is scu=0 and is_concept=1 — it can never be a trade run's ship, yet it was
    competing for ship matches. Applied per-workflow, never globally: travel_time and the
    rental/purchase tools legitimately want the full catalog."""
    cache = SimpleNamespace(vehicles=[
        SimpleNamespace(name="Railen", scu=96.0, is_concept=0),
        SimpleNamespace(name="Javelin", scu=0.0, is_concept=1),
        SimpleNamespace(name="Gladius", scu=0.0, is_concept=0),
        SimpleNamespace(name="Odin", scu=6000.0, is_concept=1),
    ])
    assert [v.name for v in cargo_capable_vehicles(cache)] == ["Railen"]

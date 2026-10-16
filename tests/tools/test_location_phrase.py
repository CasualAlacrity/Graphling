"""Covers tools/uexcorp/location_phrase.py: one preposition per kind of place, with real
UEX terminal records (fields copied from the live reference cache, 2026-10-16)."""
import pytest

from tools.uexcorp.location_phrase import location_phrase
from tools.uexcorp.reference_cache import CachedTerminal, TerminalType


def _terminal(name, *, system="Stanton", orbit=None, moon=None, planet=None, nickname=None,
              station=None, outpost=None, city=None, displayname=None):
    return CachedTerminal(
        id=1, name=name, type=TerminalType.COMMODITY, star_system_name=system, orbit_name=orbit,
        moon_name=moon, planet_name=planet, displayname=displayname or name, nickname=nickname,
        space_station_name=station, outpost_name=outpost, city_name=city, is_auto_load=0, is_nqa=0,
    )


@pytest.mark.parametrize(("terminal", "expected"), [
    # Surface: "on" the moon when there is one, else the planet.
    (_terminal("ArcCorp Mining Area 141", orbit="Crusader", moon="Daymar", planet="Crusader",
               outpost="ArcCorp Mining Area 141"), "ArcCorp Mining Area 141, on Daymar"),
    (_terminal("HDMS-Edmond", orbit="Hurston", planet="Hurston", outpost="HDMS-Edmond"), "HDMS-Edmond, on Hurston"),
    # Cities by nickname: Area 18 has several trade terminals with different prices.
    (_terminal("IO North Tower - Area 18", orbit="ArcCorp", planet="ArcCorp", city="Area 18",
               nickname="Area 18 IO Tower"), "Area 18 IO Tower, on ArcCorp"),
    # Levski has no planet or moon, only the orbit it's catalogued under.
    (_terminal("Levski", system="Nyx", orbit="Delamar", city="Levski", nickname="Levski"), "Levski, on Delamar"),
    # Station orbiting a planet or moon: "over".
    (_terminal("Admin - Baijini Point", orbit="ArcCorp", planet="ArcCorp", station="Baijini Point"),
     "Baijini Point, over ArcCorp"),
    # Said the way pilots say it, not UEX's official "Green Imperial Housing Exchange".
    (_terminal("Admin - GrimHEX", orbit="Crusader", moon="Yela", planet="Crusader",
               station="Green Imperial Housing Exchange"), "GrimHEX, over Yela"),
    # Lagrange stations: "at <body>'s L<n>", Stanton's by their code.
    (_terminal("Admin - ARC-L1", orbit="ArcCorp Lagrange Point 1", planet="ArcCorp",
               station="ARC-L1 Wide Forest Station"), "ARC-L1, at ArcCorp's L1"),
    (_terminal("Checkmate", system="Pyro", orbit="Monox Lagrange Point 4", planet="Monox",
               station="Checkmate Station"), "Checkmate Station, at Monox's L4"),
    (_terminal("Gaslight", system="Pyro", orbit="Pyro V Lagrange Point 2", planet="Pyro V", station="Gaslight"),
     "Gaslight, at Pyro V's L2"),
    # Gateways and free-floating stations: "in <system>".
    (_terminal("Admin - Pyro Gateway (Stanton)", orbit="Pyro Gateway (Stanton system)",
               station="Pyro Gateway (Stanton)"), "Pyro Gateway, in Stanton"),
    (_terminal("People's Service Station Alpha", system="Nyx", orbit="People's Service Station Alpha",
               station="People's Service Station Alpha"), "People's Service Station Alpha, in Nyx"),
])
def test_each_kind_of_place_gets_its_own_preposition(terminal, expected):
    assert location_phrase(terminal, terminal.name) == expected


def test_never_says_near():
    lagrange = _terminal("Admin - HUR-L2", orbit="Hurston Lagrange Point 2", planet="Hurston",
                         station="HUR-L2 Faithful Dream Station")
    assert "near" not in location_phrase(lagrange, lagrange.name)


def test_a_terminal_missing_from_the_cache_keeps_the_price_rows_name():
    assert location_phrase(None, "Admin - Somewhere New") == "Admin - Somewhere New"

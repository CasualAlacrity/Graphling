"""How ALICE names a terminal's location out loud: one fixed preposition per kind of place,
so the same station never comes out three different ways (agreed with Jeff 2026-10-16).

    surface (outpost, city)      "ArcCorp Mining Area 141, on Daymar"
    station orbiting a body      "Baijini Point, over ArcCorp"
    Lagrange-point station       "ARC-L1, at ArcCorp's L1"  /  "Checkmate Station, at Monox's L4"
    gateway station              "Pyro Gateway, in Stanton"
    free-floating station        "People's Service Station Alpha, in Nyx"

"near" is deliberately never used: it read as either "orbiting" or "somewhere around",
and Lagrange points are millions of km from the planet they're named after.

Built from the reference cache's terminal record, not the price row: only the terminal
says whether it's a station, city or outpost. The price tools hand the result to the
model as `location`, and their descriptions tell it to say that phrase as given.
"""
import re

from tools.uexcorp.reference_cache import CachedTerminal

# Appended to every price tool's description, so the model says the phrase rather than
# assembling its own from star_system_name/orbit_name/planet_name (which is what produced
# "in orbit around Wala, on ArcCorp" in one answer and "on ArcCorp, in Stanton" the next).
LOCATION_GUIDANCE = (
    " Every terminal comes with `location`, its name and place as ALICE says it (e.g. "
    "'Baijini Point, over ArcCorp' or 'ArcCorp Mining Area 141, on Daymar'). When you name a "
    "terminal aloud, say its location exactly as given and continue the sentence from there: "
    "'Baijini Point, over ArcCorp, is selling Laranite for 3.15 aUEC per SCU.' Don't add the "
    "system, orbit or planet yourself."
)

# Stations whose official name nobody says. UEX's nicknames can't be used instead: they
# vary per terminal at the same station ("Dumper's GrimHEX", "Skutters Grim").
SPOKEN_STATION_NAMES = {
    "Green Imperial Housing Exchange": "GrimHEX",
}

# "ArcCorp Lagrange Point 1", "Pyro VI Lagrange Point 5"
_LAGRANGE_ORBIT = re.compile(r"^(?P<body>.+) Lagrange Point (?P<point>\d+)$")
# Stanton's Lagrange stations lead with their code: "ARC-L1 Wide Forest Station".
_LAGRANGE_CODE = re.compile(r"^(?P<code>[A-Z]{3}-L\d+)\b")
# "Pyro Gateway (Stanton)": the system in brackets is said separately, as "in Stanton".
_TRAILING_BRACKETS = re.compile(r"\s*\([^)]*\)$")


def _surface_body(terminal: CachedTerminal) -> str | None:
    # The moon when there is one: "on Daymar" says more than "on Crusader". Levski has
    # neither, only the orbit it's catalogued under (Delamar).
    return terminal.moon_name or terminal.planet_name or terminal.orbit_name


def _station_phrase(terminal: CachedTerminal) -> str:
    station = SPOKEN_STATION_NAMES.get(terminal.space_station_name, terminal.space_station_name)
    lagrange = _LAGRANGE_ORBIT.match(terminal.orbit_name or "")
    if lagrange is not None:
        code = _LAGRANGE_CODE.match(station)
        name = code.group("code") if code is not None else station
        return f"{name}, at {lagrange.group('body')}'s L{lagrange.group('point')}"

    body = terminal.moon_name or terminal.planet_name
    if body is not None:
        return f"{station}, over {body}"

    # No body: a gateway at the edge of the system, or a station floating free (Nyx's
    # People's Service Stations).
    name = _TRAILING_BRACKETS.sub("", station)
    if terminal.star_system_name:
        return f"{name}, in {terminal.star_system_name}"
    return name


def location_phrase(terminal: CachedTerminal | None, fallback_name: str) -> str:
    """The spoken name-and-place for a terminal. `fallback_name` (the price row's own
    terminal name) is used as-is when the terminal isn't in the reference cache."""
    if terminal is None:
        return fallback_name

    if terminal.space_station_name:
        return _station_phrase(terminal)

    if terminal.city_name or terminal.outpost_name:
        # Cities by nickname: Area 18 alone has three trade terminals with different
        # prices ("TDD Area 18", "Area 18 IO Tower", "Dumper's Area 18"), and the pilot has
        # to land at the right one.
        if terminal.city_name:
            name = terminal.nickname or terminal.city_name
        else:
            name = terminal.outpost_name
        body = _surface_body(terminal)
        if body is not None and body != name:
            return f"{name}, on {body}"
        return name

    return terminal.displayname or fallback_name


def terminals_by_id(terminals: list[CachedTerminal]) -> dict[int, CachedTerminal]:
    lookup: dict[int, CachedTerminal] = {}
    for terminal in terminals:
        lookup[terminal.id] = terminal
    return lookup

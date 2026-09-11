"""
Hands a resolved route from a search/ranking tool (best_route, trade_advisor) to
start_trade_run without re-resolving or re-fetching it — the route, its computed SCU,
and the ship it was ranked for are exactly what create_run_from_route needs, and
re-deriving any of them from a name a second time only risks landing on a slightly
different answer than the one the pilot actually heard.

Simple in-memory, single-process state, same shape as voice/timer_tool.py's _timers
dict — this only ever runs inside the one process serving one pilot. Losing an entry on
restart just means re-running the search; no DB persistence needed for something this
short-lived and low-stakes.
"""
import uuid

from tools.uexcorp.trade_data import UEXTradeRoute

_cache: dict[str, tuple[UEXTradeRoute, int, str]] = {}


def stash(route: UEXTradeRoute, scu: int, vehicle_name: str) -> str:
    """Stores a found route + the SCU it was scored at + the ship it was found for,
    keyed by a short opaque token. Returns the token to hand back to the model."""
    token = uuid.uuid4().hex[:8]
    _cache[token] = (route, scu, vehicle_name)
    return token


def get(token: str) -> tuple[UEXTradeRoute, int, str] | None:
    """Looks up a previously stashed route. Not popped on read — a failed commit
    shouldn't force the pilot to search again for the same route."""
    return _cache.get(token)

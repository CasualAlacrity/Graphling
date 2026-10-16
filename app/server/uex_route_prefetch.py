"""Keeps every trade terminal's routes cached, so best_route searches all of them.

Before this, a search could only afford MAX_LIVE_ROUTE_FETCHES (8) live UEX calls, so a
first search of Stanton saw 8 of its 117 trade terminals and coverage only grew as
pilots searched. UEX allows 7,000 calls a day; refreshing all 161 trade terminals
hourly costs ~3,900 of them, and searches then find everything already cached.

Each run skips terminals fetched recently (a restart or redeploy doesn't re-spend a
whole round) and stops once today's remaining calls drop to settings.uex_prefetch_reserve,
leaving those for pilots' own lookups (server/uex_budget.py).
"""
import asyncio
import logging
from datetime import UTC, datetime, timedelta

from db.session import SessionLocal
from server import uex_budget, uex_cache_service
from server.config import settings
from tools.uexcorp.client import UexCallBudgetExhausted
from tools.uexcorp.price_cache import route_origin_fetched_at
from tools.uexcorp.reference_cache import TerminalType

# Same as overlay/uex_lookup.py's ROUTE_FANOUT_CONCURRENCY: a whole round goes out in
# under a minute without bursting 161 requests at UEX at once.
PREFETCH_CONCURRENCY = 5

log = logging.getLogger(__name__)


def trade_terminals(cache) -> list:
    """The same pool as tools/uexcorp/matching.trade_terminals (COMMODITY terminals only;
    see its docstring for why), repeated rather than imported: matching pulls in the name
    matchers (jellyfish, rapidfuzz), which the server image doesn't install, and importing
    it crash-looped the server on 2026-10-16."""
    result = []
    for terminal in cache.terminals:
        if terminal.type == TerminalType.COMMODITY:
            result.append(terminal)
    return result


def _recent_enough() -> timedelta:
    # Three quarters of an interval: hourly runs then refresh anything over 45 minutes
    # old, which is every terminal the previous run fetched, but not ones a pilot's search
    # or a restart-triggered run fetched a few minutes ago.
    return timedelta(seconds=settings.uex_route_prefetch_interval_s * 0.75)


async def prefetch_once() -> int:
    """One round. Returns how many terminals were fetched."""
    cache = await uex_cache_service.get_reference_cache()
    async with SessionLocal() as session:
        fetched_at = await route_origin_fetched_at(session)

    now = datetime.now(UTC)
    due: list[int] = []
    for terminal in trade_terminals(cache):
        last = fetched_at.get(terminal.id)
        if last is None or now - last >= _recent_enough():
            due.append(terminal.id)

    semaphore = asyncio.Semaphore(PREFETCH_CONCURRENCY)
    fetched = 0
    stopped = False

    async def fetch_one(terminal_id: int) -> None:
        nonlocal fetched, stopped
        async with semaphore:
            if stopped:
                return
            if await uex_budget.remaining_today() <= settings.uex_prefetch_reserve:
                stopped = True
                return
            try:
                await uex_cache_service.fetch_routes_from_terminal(terminal_id)
            except UexCallBudgetExhausted:
                stopped = True
                return
            except Exception:
                # One terminal failing (a UEX 5xx, a timeout) shouldn't end the round;
                # it's due again next time.
                log.exception("route prefetch: terminal %s failed", terminal_id)
                return
            fetched += 1

    await asyncio.gather(*(fetch_one(terminal_id) for terminal_id in due))

    if stopped:
        log.warning(
            "route prefetch: stopped after %d of %d terminals, today's UEX calls are down to the pilot reserve",
            fetched, len(due),
        )
    else:
        log.info("route prefetch: refreshed %d of %d due terminals", fetched, len(due))
    return fetched


async def prefetch_forever() -> None:
    """Runs a round right away, then every interval. A failed round is logged and the
    loop carries on, like llm_gateway_service.probe_forever."""
    if settings.uex_route_prefetch_interval_s <= 0:
        return
    while True:
        try:
            await prefetch_once()
        except Exception:
            log.exception("route prefetch: round failed")
        await asyncio.sleep(settings.uex_route_prefetch_interval_s)

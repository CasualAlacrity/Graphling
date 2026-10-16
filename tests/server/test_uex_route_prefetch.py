"""Covers server/uex_route_prefetch.py: which terminals a round refreshes, and that it
stops at the pilot reserve rather than spending UEX calls pilots need."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from server import uex_route_prefetch
from server.config import settings
from tools.uexcorp.client import UexCallBudgetExhausted


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _terminal(terminal_id, kind="commodity"):
    return SimpleNamespace(id=terminal_id, type=kind)


@pytest.fixture
def world(monkeypatch):
    """Four trade terminals plus a pizza counter; terminal 2 was fetched 10 minutes ago,
    terminal 3 two hours ago, 1 and 4 never."""
    state = SimpleNamespace(remaining=5000, fetched=[], refuse=False)
    now = datetime.now(UTC)
    cache = SimpleNamespace(terminals=[_terminal(1), _terminal(2), _terminal(3), _terminal(4), _terminal(9, "item")])

    async def get_reference_cache():
        return cache

    async def route_origin_fetched_at(session):
        return {2: now - timedelta(minutes=10), 3: now - timedelta(hours=2)}

    async def remaining_today():
        return state.remaining

    async def fetch_routes_from_terminal(terminal_id):
        if state.refuse:
            raise UexCallBudgetExhausted("spent")
        state.fetched.append(terminal_id)
        state.remaining -= 1
        return []

    def trade_terminals(reference_cache):
        result = []
        for terminal in reference_cache.terminals:
            if terminal.type == "commodity":
                result.append(terminal)
        return result

    monkeypatch.setattr(uex_route_prefetch, "trade_terminals", trade_terminals)
    monkeypatch.setattr(uex_route_prefetch, "SessionLocal", _Session)
    monkeypatch.setattr(uex_route_prefetch, "route_origin_fetched_at", route_origin_fetched_at)
    monkeypatch.setattr(uex_route_prefetch.uex_cache_service, "get_reference_cache", get_reference_cache)
    monkeypatch.setattr(uex_route_prefetch.uex_cache_service, "fetch_routes_from_terminal", fetch_routes_from_terminal)
    monkeypatch.setattr(uex_route_prefetch.uex_budget, "remaining_today", remaining_today)
    monkeypatch.setattr(settings, "uex_route_prefetch_interval_s", 3600)
    monkeypatch.setattr(settings, "uex_prefetch_reserve", 1500)
    return state


async def test_a_round_refreshes_trade_terminals_that_are_due(world):
    fetched = await uex_route_prefetch.prefetch_once()

    assert fetched == 3
    assert sorted(world.fetched) == [1, 3, 4]


async def test_a_round_leaves_the_pilot_reserve_alone(world):
    world.remaining = 1501

    await uex_route_prefetch.prefetch_once()

    assert len(world.fetched) == 1


async def test_a_round_stops_when_uex_refuses(world):
    world.refuse = True

    assert await uex_route_prefetch.prefetch_once() == 0

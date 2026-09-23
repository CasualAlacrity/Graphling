"""Covers resolve_leg/resolve_run's ambiguity handling — specifically that
AmbiguousLegError/AmbiguousRunError carry a real, spoken message, not the empty-by-
default Exception message a subclass gets when it never calls super().__init__().

Regression this guards: neither exception called super().__init__(), so every tool that
caught one and did `return str(error)` (5 for AmbiguousLegError, 1 for AmbiguousRunError)
handed the persona a raw Python repr of the candidate objects — verified live,
2026-09-23, e.g. "[<db.models.TradeLeg object at 0x100a41a60>, ...]". Any pilot with 2+
concurrent runs hit this on the very first ambiguous thing they said.
"""
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from db.models import CargoTransferType, LegType, TradeLeg, TradeRun
from tools.trade_run import resolver
from tools.trade_run.resolver import AmbiguousLegError, AmbiguousRunError
from tools.uexcorp.client import UEXCorpClient


def _leg(leg_type, terminal_name, commodity_name, **overrides):
    fields = dict(
        id=uuid.uuid4(), leg_type=leg_type, terminal_id=1, terminal_name=terminal_name,
        commodity_name=commodity_name, quantity_scu=40, price_per_unit=10,
        cargo_transfer_type=CargoTransferType.MANUAL, cargo_transfer_fee=0,
        created_at=datetime.now(UTC), started_at=datetime.now(UTC),
        reached_at=None, transaction_completed_at=None, transferred_at=None, finalized_at=None,
    )
    fields.update(overrides)
    return TradeLeg(**fields)


def _run(ship, legs):
    return TradeRun(
        id=uuid.uuid4(), ship=ship, usable_container_sizes="1,2,4,8,16,24,32",
        created_at=datetime.now(UTC), finalized_at=None, legs=legs,
    )


@pytest.fixture(autouse=True)
def fake_uex_cache(monkeypatch):
    # resolve_leg only needs cache.commodities/terminals when a commodity/terminal hint
    # was actually passed — every test here uses leg_type alone, so empty pools are fine.
    async def fake_get_uex_cache(self):
        return SimpleNamespace(commodities=[], terminals=[])

    monkeypatch.setattr(UEXCorpClient, "get_uex_cache", fake_get_uex_cache)


async def test_resolve_leg_returns_the_only_match(monkeypatch):
    run = _run("Railen", [_leg(LegType.ACQUISITION, "Seraphim", "Copper")])
    monkeypatch.setattr(resolver.trade_run_store, "get_in_progress_runs", lambda: _async([run]))

    leg = await resolver.resolve_leg(leg_type=LegType.ACQUISITION)

    assert leg.terminal_name == "Seraphim"


async def test_resolve_leg_raises_plain_value_error_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(resolver.trade_run_store, "get_in_progress_runs", lambda: _async([]))

    with pytest.raises(ValueError):
        await resolver.resolve_leg(leg_type=LegType.ACQUISITION)


async def test_resolve_leg_ambiguity_message_names_ship_commodity_and_terminal(monkeypatch):
    run_a = _run("Railen", [_leg(LegType.ACQUISITION, "Seraphim", "Copper")])
    run_b = _run("Caterpillar", [_leg(LegType.ACQUISITION, "Baijini Point", "Laranite")])
    monkeypatch.setattr(resolver.trade_run_store, "get_in_progress_runs", lambda: _async([run_a, run_b]))

    with pytest.raises(AmbiguousLegError) as excinfo:
        await resolver.resolve_leg(leg_type=LegType.ACQUISITION)

    message = str(excinfo.value)
    assert "object at 0x" not in message  # the actual regression
    assert "Railen" in message and "Copper" in message and "Seraphim" in message
    assert "Caterpillar" in message and "Laranite" in message and "Baijini Point" in message
    assert message.endswith("Which one?")


async def test_resolve_run_returns_the_only_match(monkeypatch):
    run = _run("Railen", [_leg(LegType.ACQUISITION, "Seraphim", "Copper")])
    monkeypatch.setattr(resolver.trade_run_store, "get_in_progress_runs", lambda: _async([run]))

    result = await resolver.resolve_run()

    assert result is run


async def test_resolve_run_raises_plain_value_error_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(resolver.trade_run_store, "get_in_progress_runs", lambda: _async([]))

    with pytest.raises(ValueError):
        await resolver.resolve_run()


async def test_resolve_run_ambiguity_message_names_ship_and_commodity(monkeypatch):
    run_a = _run("Railen", [_leg(LegType.ACQUISITION, "Seraphim", "Copper")])
    run_b = _run("Caterpillar", [_leg(LegType.ACQUISITION, "Baijini Point", "Laranite")])
    monkeypatch.setattr(resolver.trade_run_store, "get_in_progress_runs", lambda: _async([run_a, run_b]))

    with pytest.raises(AmbiguousRunError) as excinfo:
        await resolver.resolve_run()

    message = str(excinfo.value)
    assert "object at 0x" not in message  # the actual regression
    assert "Railen" in message and "Copper" in message
    assert "Caterpillar" in message and "Laranite" in message
    assert message.endswith("Which one?")


async def test_resolve_run_ship_hint_narrows_to_the_matching_run(monkeypatch):
    run_a = _run("Railen", [_leg(LegType.ACQUISITION, "Seraphim", "Copper")])
    run_b = _run("Caterpillar", [_leg(LegType.ACQUISITION, "Baijini Point", "Laranite")])
    monkeypatch.setattr(resolver.trade_run_store, "get_in_progress_runs", lambda: _async([run_a, run_b]))

    result = await resolver.resolve_run(ship="Railen")

    assert result is run_a


def _async(value):
    async def _inner(*args, **kwargs):
        return value
    return _inner()

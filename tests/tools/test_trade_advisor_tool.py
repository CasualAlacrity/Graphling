"""Covers two bugs found auditing every AmbiguousRunError call site (2026-09-23):

1. `ship` was accepted as an arg (its own description promises it disambiguates between
   concurrent runs) but never actually passed to resolve_run — a complete no-op.
2. AmbiguousRunError and "no active run at all" were caught together and both produced
   "start one first", which is actively wrong when the real problem is too many runs,
   not zero.
"""
from db.models import TradeRun
from tools.starcitizenwiki.client import StarCitizenWikiClient
from tools.trade_run import resolver
from tools.trade_run.resolver import AmbiguousRunError
from tools.trade_run.trade_advisor_tool import TradeAdvisorTool
from tools.uexcorp.client import UEXCorpClient


def _run(ship):
    return TradeRun(ship=ship, usable_container_sizes="", legs=[])


def _tool():
    return TradeAdvisorTool(
        uex_client=UEXCorpClient(api_key="test", bearer_token="test"),
        scw_client=StarCitizenWikiClient(),
    )


async def test_ship_arg_is_actually_passed_to_resolve_run(monkeypatch):
    calls = []

    async def fake_resolve_run(ship=None):
        calls.append(ship)
        raise AmbiguousRunError([_run("Railen"), _run("Caterpillar")])

    monkeypatch.setattr(resolver, "resolve_run", fake_resolve_run)

    await _tool()._arun(ship="Railen")

    assert calls == ["Railen"]


async def test_ambiguous_runs_surface_the_real_clarifying_message(monkeypatch):
    async def fake_resolve_run(ship=None):
        raise AmbiguousRunError([_run("Railen"), _run("Caterpillar")])

    monkeypatch.setattr(resolver, "resolve_run", fake_resolve_run)

    result = await _tool()._arun()

    assert "Which one?" in result
    assert "start one first" not in result


async def test_genuinely_no_active_run_still_says_start_one_first(monkeypatch):
    async def fake_resolve_run(ship=None):
        raise ValueError("No active trade runs")

    monkeypatch.setattr(resolver, "resolve_run", fake_resolve_run)

    result = await _tool()._arun()

    assert "start one first" in result

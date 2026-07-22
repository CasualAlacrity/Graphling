import asyncio
import os
from datetime import UTC, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import qasync
from doubles import make_fake_cache, make_route, make_trade_run
from PySide6.QtWidgets import QApplication

from db import trade_run_store
from overlay import theme


@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the whole test session — Qt doesn't support more than one
    live instance per process, and fonts only need registering once."""
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    theme.load_fonts()
    return app


@pytest.fixture
def qasync_loop(qapp):
    """A fresh qasync event loop per test, matching how overlay_app.py drives the real
    app — needed because FilterPanel/ResultsPanel use @asyncSlot(), which requires the
    running loop to be a qasync.QEventLoop, not a plain asyncio one."""
    loop = qasync.QEventLoop(qapp)
    asyncio.set_event_loop(loop)
    with loop:
        yield loop


@pytest.fixture
def fake_cache():
    return make_fake_cache()


@pytest.fixture
def wired_panels(qasync_loop, monkeypatch, fake_cache):
    """A FilterPanel + ResultsPanel pair, wired together like overlay_app.py does, with
    every UEX-backed lookup replaced by a fake so no real Postgres/UEX access happens.
    Each test gets a fresh pair — deliberately not session-scoped, so tests can't leak
    state (e.g. a typed-in field value) into each other."""
    from overlay import filter_panel as filter_panel_module
    from overlay import results_panel as results_panel_module
    from overlay import uex_lookup

    call_log = []

    async def fake_commodity_ids_at(terminal_id, side):
        call_log.append(("commodity_ids_at", terminal_id, side))
        await asyncio.sleep(0.01)
        return {c.id for c in fake_cache.commodities}

    async def fake_terminal_ids_for(commodity_id, side):
        call_log.append(("terminal_ids_for", commodity_id, side))
        await asyncio.sleep(0.01)
        return {t.id for t in fake_cache.terminals}

    async def fake_search_routes(**kwargs):
        call_log.append(("search_routes", kwargs))
        await asyncio.sleep(0.01)
        return [make_route()]

    async def fake_commodity_volatility(commodity_id):
        call_log.append(("commodity_volatility", commodity_id))
        await asyncio.sleep(0.01)
        return {1: {"buy_cv": 0.02}, 2: {"sell_cv": 0.05}}

    monkeypatch.setattr(uex_lookup, "uex_cache", fake_cache)
    monkeypatch.setattr(uex_lookup, "commodity_names", [c.name for c in fake_cache.commodities])
    monkeypatch.setattr(uex_lookup, "terminal_names", [t.nickname for t in fake_cache.terminals])
    monkeypatch.setattr(uex_lookup, "ship_names", [v.name_full for v in fake_cache.vehicles])
    monkeypatch.setattr(uex_lookup, "SOURCE_INVENTORY_LEVELS", [])
    monkeypatch.setattr(uex_lookup, "DESTINATION_INVENTORY_LEVELS", [])

    def find_terminal(name):
        return next((t for t in fake_cache.terminals if t.nickname == name), None)

    def find_commodity(name):
        return next((c for c in fake_cache.commodities if c.name == name), None)

    def find_vehicle(name):
        return next((v for v in fake_cache.vehicles if v.name_full == name), None)

    monkeypatch.setattr(filter_panel_module, "find_terminal", find_terminal)
    monkeypatch.setattr(filter_panel_module, "find_commodity", find_commodity)
    monkeypatch.setattr(filter_panel_module, "find_vehicle", find_vehicle)
    monkeypatch.setattr(filter_panel_module, "inventory_code_for", lambda status_name, status_type: None)
    monkeypatch.setattr(filter_panel_module, "is_space_terminal", lambda terminal: False)
    monkeypatch.setattr(filter_panel_module, "terminal_breadcrumb", lambda terminal: "")
    monkeypatch.setattr(filter_panel_module, "commodity_ids_at", fake_commodity_ids_at)
    monkeypatch.setattr(filter_panel_module, "terminal_ids_for", fake_terminal_ids_for)
    monkeypatch.setattr(filter_panel_module, "search_routes", fake_search_routes)

    monkeypatch.setattr(results_panel_module, "commodity_code_for", lambda commodity_id, fallback: fallback)
    monkeypatch.setattr(
        results_panel_module, "route_breadcrumb",
        lambda system, planet, terminal_id, fallback: "/".join(p for p in (system, planet, fallback) if p),
    )
    monkeypatch.setattr(results_panel_module, "commodity_volatility", fake_commodity_volatility)

    filter_panel = filter_panel_module.FilterPanel()
    results_panel = results_panel_module.ResultsPanel()
    filter_panel.routes_found.connect(results_panel.set_routes)
    filter_panel.search_rejected.connect(results_panel.show_message)

    return filter_panel, results_panel, call_log


@pytest.fixture
def wired_canvas(wired_panels, monkeypatch):
    """An OverlayCanvas with FilterPanel/ResultsPanel from wired_panels plus fresh
    TradeRunsPanel/TradeLedgerPanel, and trade_run_store's DB-touching functions
    replaced by an in-memory store — the pure functions (current_step_title,
    run_investment, etc.) are left real, so rendering reflects real logic operating on
    fake data. A single patch on the shared `db.trade_run_store` module object covers
    both overlay_canvas.py and trade_runs_panel.py, since both do `from db import
    trade_run_store` (module access), not `from db.trade_run_store import name`."""
    from overlay import overlay_canvas as overlay_canvas_module
    from overlay import trade_runs_panel as trade_runs_panel_module

    filter_panel, results_panel, call_log = wired_panels
    store_state = {"in_progress": [], "finalized": []}

    async def fake_create_run_from_route(route, quantity_scu, ship):
        call_log.append(("create_run_from_route", route, quantity_scu, ship))
        run = make_trade_run(ship=ship)
        # Mirrors the real store: the acquisition leg starts traveling the instant the
        # run is committed to, no separate depart step.
        run.legs[0].started_at = datetime.now(UTC)
        store_state["in_progress"].append(run)
        return run

    async def fake_get_in_progress_runs():
        call_log.append(("get_in_progress_runs",))
        return list(store_state["in_progress"])

    async def fake_get_finalized_runs(limit=50):
        call_log.append(("get_finalized_runs",))
        return list(store_state["finalized"])

    def _find_leg(leg_id):
        for run in store_state["in_progress"]:
            for leg in run.legs:
                if leg.id == leg_id:
                    return leg
        return None

    async def fake_advance_leg(leg_id):
        call_log.append(("advance_leg", leg_id))
        leg = _find_leg(leg_id)
        if leg is None:
            raise ValueError(f"No trade leg with id {leg_id}")
        field = trade_run_store.next_unset_field(leg)
        if field is None:
            raise ValueError(f"Trade leg {leg_id} is already finalized")
        setattr(leg, field, datetime.now(UTC))
        return leg

    async def fake_record_purchase(leg_id, quantity_scu, price_per_unit, cargo_transfer_type, cargo_transfer_fee):
        call_log.append(
            ("record_purchase", leg_id, quantity_scu, price_per_unit, cargo_transfer_type, cargo_transfer_fee)
        )
        leg = _find_leg(leg_id)
        if leg is None:
            raise ValueError(f"No trade leg with id {leg_id}")
        leg.quantity_scu = quantity_scu
        leg.price_per_unit = price_per_unit
        leg.cargo_transfer_type = cargo_transfer_type
        leg.cargo_transfer_fee = cargo_transfer_fee
        leg.transaction_completed_at = datetime.now(UTC)
        return leg

    async def fake_record_sale(leg_id, quantity_scu, price_per_unit, cargo_transfer_type, cargo_transfer_fee):
        call_log.append(("record_sale", leg_id, quantity_scu, price_per_unit, cargo_transfer_type, cargo_transfer_fee))
        leg = _find_leg(leg_id)
        if leg is None:
            raise ValueError(f"No trade leg with id {leg_id}")
        leg.quantity_scu = quantity_scu
        leg.price_per_unit = price_per_unit
        leg.cargo_transfer_type = cargo_transfer_type
        leg.cargo_transfer_fee = cargo_transfer_fee
        now = datetime.now(UTC)
        leg.transaction_completed_at = now
        leg.transferred_at = now
        return leg

    async def fake_finalize_run(run_id):
        call_log.append(("finalize_run", run_id))
        for run in store_state["in_progress"]:
            if run.id == run_id:
                if any(trade_run_store.next_unset_field(leg) is not None for leg in run.legs):
                    raise ValueError(f"Trade run {run_id} still has unfinished legs")
                run.finalized_at = datetime.now(UTC)
                store_state["in_progress"].remove(run)
                store_state["finalized"].append(run)
                return run
        raise ValueError(f"No trade run with id {run_id}")

    async def fake_delete_run(run_id):
        call_log.append(("delete_run", run_id))
        store_state["in_progress"] = [run for run in store_state["in_progress"] if run.id != run_id]

    monkeypatch.setattr(trade_run_store, "create_run_from_route", fake_create_run_from_route)
    monkeypatch.setattr(trade_run_store, "get_in_progress_runs", fake_get_in_progress_runs)
    monkeypatch.setattr(trade_run_store, "get_finalized_runs", fake_get_finalized_runs)
    monkeypatch.setattr(trade_run_store, "advance_leg", fake_advance_leg)
    monkeypatch.setattr(trade_run_store, "record_purchase", fake_record_purchase)
    monkeypatch.setattr(trade_run_store, "record_sale", fake_record_sale)
    monkeypatch.setattr(trade_run_store, "finalize_run", fake_finalize_run)
    monkeypatch.setattr(trade_run_store, "delete_run", fake_delete_run)

    trade_runs_panel = trade_runs_panel_module.TradeRunsPanel()
    trade_ledger_panel = trade_runs_panel_module.TradeLedgerPanel()
    canvas = overlay_canvas_module.OverlayCanvas(filter_panel, results_panel, trade_runs_panel, trade_ledger_panel)

    return canvas, store_state, call_log

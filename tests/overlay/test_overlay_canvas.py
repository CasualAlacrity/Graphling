"""Covers OverlayCanvas: creating a run from a route search result and switching to the
In Progress tab, tab-change-triggered refresh, the reparented-panels bracket fix
(HudWindow.isWindow()), and Mark Done flowing through to a re-render."""

from doubles import make_route


def test_add_route_requested_creates_run_and_switches_tab(qasync_loop, wired_canvas):
    canvas, store_state, call_log = wired_canvas
    route = make_route()

    async def scenario():
        await canvas._on_add_route_requested(route)

    qasync_loop.run_until_complete(scenario())

    assert len(store_state["in_progress"]) == 1
    assert canvas.tabs.currentWidget() is canvas.trade_runs_panel
    assert canvas.trade_runs_panel.row_count() == 1


def test_add_route_requested_failure_surfaces_message(qasync_loop, wired_canvas, monkeypatch):
    canvas, store_state, _call_log = wired_canvas
    route = make_route()

    async def failing_create_run_from_route(route, quantity_scu, ship):
        raise ConnectionError("Postgres unreachable")

    from db import trade_run_store
    monkeypatch.setattr(trade_run_store, "create_run_from_route", failing_create_run_from_route)

    async def scenario():
        await canvas._on_add_route_requested(route)

    qasync_loop.run_until_complete(scenario())

    assert store_state["in_progress"] == []
    assert canvas.tabs.currentWidget() is canvas.trade_runs_panel
    assert canvas.trade_runs_panel.row_count() == 1
    assert "Couldn't create run" in canvas.trade_runs_panel.row_text()


def test_switching_to_in_progress_tab_refreshes_it(qasync_loop, wired_canvas):
    canvas, store_state, call_log = wired_canvas
    from doubles import make_trade_run
    store_state["in_progress"].append(make_trade_run())

    async def scenario():
        index = canvas.tabs.indexOf(canvas.trade_runs_panel)
        await canvas._on_tab_changed(index)

    qasync_loop.run_until_complete(scenario())

    assert any(entry[0] == "get_in_progress_runs" for entry in call_log)
    assert canvas.trade_runs_panel.row_count() == 1


def test_switching_to_ledger_tab_refreshes_it(qasync_loop, wired_canvas):
    canvas, store_state, call_log = wired_canvas
    from datetime import UTC, datetime

    from doubles import make_trade_run
    store_state["finalized"].append(make_trade_run(finalized_at=datetime.now(UTC)))

    async def scenario():
        index = canvas.tabs.indexOf(canvas.trade_ledger_panel)
        await canvas._on_tab_changed(index)

    qasync_loop.run_until_complete(scenario())

    assert any(entry[0] == "get_finalized_runs" for entry in call_log)
    assert canvas.trade_ledger_panel.run_count() == 1


def test_reparented_panels_are_no_longer_top_level_windows(wired_canvas):
    canvas, _store_state, _call_log = wired_canvas

    assert canvas.filter_panel.isWindow() is False
    assert canvas.results_panel.isWindow() is False
    assert canvas.trade_runs_panel.isWindow() is False
    assert canvas.trade_ledger_panel.isWindow() is False


def test_advance_updates_leg_and_rerenders(qasync_loop, wired_canvas):
    canvas, store_state, _call_log = wired_canvas
    from doubles import make_trade_leg, make_trade_run

    from db.models import LegType

    leg = make_trade_leg(LegType.ACQUISITION)
    run = make_trade_run(legs=[leg])
    store_state["in_progress"].append(run)

    async def scenario():
        await canvas.trade_runs_panel.refresh()
        await canvas.trade_runs_panel._on_advance(leg.id)

    qasync_loop.run_until_complete(scenario())

    assert leg.started_at is not None
    assert canvas.trade_runs_panel.row_count() == 1


def test_record_purchase_updates_leg_and_rerenders(qasync_loop, wired_canvas):
    canvas, store_state, _call_log = wired_canvas
    from doubles import make_trade_leg, make_trade_run

    from db.models import CargoTransferType, LegType

    now_field_leg = make_trade_leg(LegType.ACQUISITION, started_at=None, reached_at=None)
    run = make_trade_run(legs=[now_field_leg])
    store_state["in_progress"].append(run)

    async def scenario():
        await canvas.trade_runs_panel.refresh()
        await canvas.trade_runs_panel._on_record_purchase(
            now_field_leg.id, 40, 12, CargoTransferType.AUTOLOAD, 5000
        )

    qasync_loop.run_until_complete(scenario())

    assert now_field_leg.quantity_scu == 40
    assert now_field_leg.price_per_unit == 12
    assert now_field_leg.transaction_completed_at is not None
    assert canvas.trade_runs_panel.row_count() == 1


def test_toggling_leg_expand_does_not_touch_the_store(qasync_loop, wired_canvas):
    canvas, store_state, call_log = wired_canvas
    from doubles import make_trade_run

    run = make_trade_run()
    store_state["in_progress"].append(run)

    async def scenario():
        await canvas.trade_runs_panel.refresh()

    qasync_loop.run_until_complete(scenario())
    calls_before = len(call_log)

    leg = run.legs[0]
    currently_expanded = canvas.trade_runs_panel._leg_expanded.get(leg.id, True)
    canvas.trade_runs_panel._toggle_leg(leg.id, currently_expanded)

    assert len(call_log) == calls_before


def test_draft_purchase_survives_a_refresh(qasync_loop, wired_canvas):
    # Simulates tabbing away mid-edit and back — overlay_canvas.py's _on_tab_changed
    # refreshes on every visit to the In Progress tab, not just after a mutating action.
    canvas, store_state, _call_log = wired_canvas
    from doubles import make_trade_leg, make_trade_run

    from db.models import CargoTransferType, LegType

    leg = make_trade_leg(LegType.ACQUISITION, started_at=None, reached_at=None)
    run = make_trade_run(legs=[leg])
    store_state["in_progress"].append(run)

    async def first_render():
        await canvas.trade_runs_panel.refresh()

    qasync_loop.run_until_complete(first_render())

    draft = {
        "quantity_scu": 99, "price_per_unit": 42,
        "cargo_transfer_type": CargoTransferType.AUTOLOAD, "cargo_transfer_fee": 1000,
    }
    canvas.trade_runs_panel._on_draft_changed(leg.id, run.id, draft)

    async def second_render():
        await canvas.trade_runs_panel.refresh()

    qasync_loop.run_until_complete(second_render())

    assert canvas.trade_runs_panel._draft_purchase.get(leg.id) == draft


def test_finalize_run_switches_to_ledger_tab(qasync_loop, wired_canvas):
    canvas, store_state, _call_log = wired_canvas
    from datetime import UTC, datetime

    from doubles import make_trade_leg, make_trade_run

    from db.models import LegType

    now = datetime.now(UTC)

    def _finished(leg_type):
        return make_trade_leg(
            leg_type, started_at=now, reached_at=now, transaction_completed_at=now,
            transferred_at=now, finalized_at=now,
        )

    run = make_trade_run(legs=[_finished(LegType.ACQUISITION), _finished(LegType.SALE)])
    store_state["in_progress"].append(run)

    async def scenario():
        await canvas.trade_runs_panel.refresh()
        await canvas.trade_runs_panel._on_finalize(run.id)

    qasync_loop.run_until_complete(scenario())

    assert canvas.tabs.currentWidget() is canvas.trade_ledger_panel
    assert canvas.trade_ledger_panel.run_count() == 1


def test_draft_change_updates_run_header_live(qasync_loop, wired_canvas):
    canvas, store_state, _call_log = wired_canvas
    from doubles import make_trade_leg, make_trade_run

    from db.models import CargoTransferType, LegType

    sale_leg = make_trade_leg(LegType.SALE, quantity_scu=10, price_per_unit=20)
    run = make_trade_run(legs=[sale_leg])
    store_state["in_progress"].append(run)

    async def scenario():
        await canvas.trade_runs_panel.refresh()

    qasync_loop.run_until_complete(scenario())

    _investment_label, profit_label = canvas.trade_runs_panel._run_header_labels[run.id]
    draft = {
        "quantity_scu": 10, "price_per_unit": 999,
        "cargo_transfer_type": CargoTransferType.MANUAL, "cargo_transfer_fee": 0,
    }
    canvas.trade_runs_panel._on_draft_changed(sale_leg.id, run.id, draft)

    assert profit_label.text() == "+9,990 aUEC"

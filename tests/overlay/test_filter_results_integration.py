"""Exercises FilterPanel/ResultsPanel through their real @asyncSlot() methods on a
qasync event loop — the thing that broke when the overlay ran async work via
asyncio.run()-per-call before it moved to qasync (see overlay_app.py)."""
import asyncio

import requests


def test_refresh_filters_cancels_stale_task(qasync_loop, wired_panels):
    filter_panel, _results_panel, _call_log = wired_panels

    async def scenario():
        filter_panel.source_terminal_input.setText("Seraphim Station")
        filter_panel.refresh_filters()
        await asyncio.sleep(0.005)  # let the first task start, but not finish
        filter_panel.refresh_filters()
        await asyncio.sleep(0.1)  # let the second one finish

    qasync_loop.run_until_complete(scenario())

    assert filter_panel._refresh_task is None


def test_search_button_shows_loading_state_and_populates_results(qasync_loop, wired_panels):
    filter_panel, results_panel, _call_log = wired_panels

    async def scenario():
        filter_panel.commodity_input.setText("Agricium")
        assert filter_panel.search_button.isEnabled()

        filter_panel._on_search_clicked()
        await asyncio.sleep(0.005)
        assert not filter_panel.search_button.isEnabled()
        assert filter_panel.search_button.text() == "SEARCHING…"

        await asyncio.sleep(0.1)
        assert filter_panel.search_button.isEnabled()
        assert filter_panel.search_button.text() == "Search"

        await asyncio.sleep(0.1)  # let results_panel's volatility fetch settle
        assert results_panel.results_list.count() == 1

    qasync_loop.run_until_complete(scenario())


def test_sort_toggle_switches_active_key(qasync_loop, wired_panels):
    filter_panel, results_panel, _call_log = wired_panels

    async def scenario():
        filter_panel.commodity_input.setText("Agricium")
        filter_panel._on_search_clicked()
        await asyncio.sleep(0.1)

        results_panel.sort_toggle.setChecked(True)
        results_panel._on_sort_toggled(True)

        route = results_panel.last_routes[0]
        assert results_panel._active_sort_key(route) == route.price_margin
        assert results_panel.sort_margin_label.property("active") is True
        assert results_panel.sort_profit_label.property("active") is False

    qasync_loop.run_until_complete(scenario())


def test_destination_only_search_reaches_search_routes(qasync_loop, wired_panels):
    # UEX's commodities_routes endpoint 400s on a destination alone, but search_routes()
    # now covers that case via a cached fan-out — the filter panel shouldn't reject it.
    filter_panel, _results_panel, call_log = wired_panels

    async def scenario():
        filter_panel.destination_terminal_input.setText("Seraphim Station")
        await asyncio.sleep(0.05)  # let the field-edit-triggered refresh settle first

        filter_panel._on_search_clicked()
        await asyncio.sleep(0.05)

    qasync_loop.run_until_complete(scenario())

    assert any(entry[0] == "search_routes" for entry in call_log)


def test_search_with_nothing_set_is_rejected_client_side(qasync_loop, wired_panels):
    filter_panel, _results_panel, call_log = wired_panels
    rejection_messages = []
    filter_panel.search_rejected.connect(lambda msg: rejection_messages.append(msg))

    async def scenario():
        filter_panel._on_search_clicked()
        await asyncio.sleep(0.05)

    qasync_loop.run_until_complete(scenario())

    assert rejection_messages and "Select a commodity" in rejection_messages[-1]
    assert not any(entry[0] == "search_routes" for entry in call_log)
    assert filter_panel.search_button.isEnabled()


def test_api_failure_is_caught_and_surfaced(qasync_loop, wired_panels, monkeypatch):
    filter_panel, _results_panel, _call_log = wired_panels
    rejection_messages = []
    filter_panel.search_rejected.connect(lambda msg: rejection_messages.append(msg))

    async def failing_search_routes(**kwargs):
        raise requests.exceptions.HTTPError("400 Client Error: Bad Request")

    import overlay.filter_panel as filter_panel_module
    monkeypatch.setattr(filter_panel_module, "search_routes", failing_search_routes)

    async def scenario():
        filter_panel.source_terminal_input.setText("Baijini Point")
        await asyncio.sleep(0.05)
        filter_panel._on_search_clicked()
        await asyncio.sleep(0.05)

    qasync_loop.run_until_complete(scenario())

    assert rejection_messages and "Search failed" in rejection_messages[-1]
    assert filter_panel.search_button.isEnabled()
    assert filter_panel.search_button.text() == "Search"

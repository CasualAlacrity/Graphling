"""Covers ResultsPanel's rendering: row count, sort ordering, the breadcrumb/commodity-
code display (nicknames/codes instead of raw full names — the horizontal-space fix),
and volatility color/label mapping (the 0-sentinel-treated-as-stable bug)."""
import pytest
from doubles import make_route
from PySide6.QtWidgets import QLabel

from overlay import theme


@pytest.fixture
def results_panel(qapp, monkeypatch):
    import overlay.results_panel as results_panel_module

    monkeypatch.setattr(
        results_panel_module, "commodity_code_for",
        lambda commodity_id, fallback: {10: "AGRI", 20: "LRNT"}.get(commodity_id, fallback),
    )
    monkeypatch.setattr(
        results_panel_module, "route_breadcrumb",
        lambda system, planet, terminal_id, fallback: "/".join(
            part for part in ({"Stanton": "ST", "Pyro": "PYR"}.get(system, system), planet, fallback) if part
        ),
    )
    return results_panel_module.ResultsPanel()


def test_render_routes_produces_one_row_per_route(results_panel):
    routes = [make_route(commodity_id=10), make_route(commodity_id=20, origin_id=3, destination_id=1)]
    results_panel.last_routes = routes
    results_panel.render_routes(routes)
    assert results_panel.results_list.count() == 2


def test_empty_routes_shows_placeholder_message(results_panel):
    results_panel.render_routes([])
    assert results_panel.results_list.count() == 1
    assert results_panel.results_list.item(0).text() == "No routes found for these filters."


def test_sort_by_margin_orders_highest_margin_first(results_panel, monkeypatch):
    low_margin = make_route(commodity_id=10, price_margin=5.0)
    high_margin = make_route(commodity_id=20, price_margin=25.0)
    results_panel.last_routes = [low_margin, high_margin]

    rendered_calls = []
    original_render_routes = type(results_panel).render_routes

    def spy_render(self, routes):
        rendered_calls.append(list(routes))
        return original_render_routes(self, routes)

    monkeypatch.setattr(type(results_panel), "render_routes", spy_render)

    results_panel.sort_by_margin()

    assert rendered_calls[-1] == [high_margin, low_margin]


def test_reachable_scu_for_accounts_for_container_packing(results_panel):
    # 180 SCU ship with only 16/32 SCU containers loadable at both ends caps at 176,
    # not 180 — the concrete example from the decided design.
    route = make_route(
        scu_origin=1000, scu_destination=1000,
        container_sizes_origin=[16, 32], container_sizes_destination=[16, 32],
    )
    results_panel.cargo_scu = 180
    assert results_panel.reachable_scu_for(route) == 176


def test_reachable_scu_for_falls_back_to_capacity_without_container_data(results_panel):
    route = make_route(
        scu_origin=1000, scu_destination=1000,
        container_sizes_origin=[], container_sizes_destination=[],
    )
    results_panel.cargo_scu = 180
    assert results_panel.reachable_scu_for(route) == 180


def test_reachable_scu_for_only_uses_sizes_usable_at_both_ends(results_panel):
    # Origin only has 32s, destination only unloads up to 16 -> usable set is just [16].
    route = make_route(
        scu_origin=1000, scu_destination=1000,
        container_sizes_origin=[16, 32], container_sizes_destination=[1, 2, 4, 8, 16],
    )
    results_panel.cargo_scu = 180
    assert results_panel.reachable_scu_for(route) == 176  # 11 x 16 = 176, still not 180


def test_estimated_profit_for_uses_container_aware_scu(results_panel):
    route = make_route(
        price_origin=10.0, price_destination=20.0,
        scu_origin=1000, scu_destination=1000,
        container_sizes_origin=[16, 32], container_sizes_destination=[16, 32],
    )
    results_panel.cargo_scu = 180
    assert results_panel.estimated_profit_for(route) == 10.0 * 176


def test_breadcrumb_and_commodity_code_used_instead_of_raw_names(results_panel):
    route = make_route(
        commodity_id=10, commodity_name="Agricium",
        origin_name="Orison Terminal Delivery Depot", origin_system="Stanton",
    )
    results_panel.last_routes = [route]
    results_panel.render_routes([route])

    row_widget = results_panel.results_list.itemWidget(results_panel.results_list.item(0))
    commodity_label = row_widget.findChild(QLabel, "routeCommodity")
    assert "AGRI" in commodity_label.text()
    assert "Agricium" not in commodity_label.text()
    assert commodity_label.toolTip() == "Agricium"

    origin_label = row_widget.findChild(QLabel, "routeTerminalName")
    assert origin_label.text() == "ST/Crusader/Orison Terminal Delivery Depot"


def test_unknown_volatility_renders_as_unknown_not_stable(results_panel):
    # UEX returns 0 (not null) for volatility when there's not enough price history —
    # uex_lookup.commodity_volatility() already normalizes that to None before it ever
    # reaches here, so None must map to "Unknown", not fall through to "Stable".
    color, label = results_panel._volatility_color_and_label(None)
    assert label == "Unknown"
    assert color == theme.TEXT_DISABLED


def test_volatility_cv_for_looks_up_by_terminal_and_side(results_panel):
    route = make_route(commodity_id=10, origin_id=1, destination_id=2)
    results_panel._volatility_by_commodity = {10: {1: {"buy_cv": 0.02}, 2: {"sell_cv": 0.05}}}
    assert results_panel._volatility_cv_for(route, "buy") == 0.02
    assert results_panel._volatility_cv_for(route, "sell") == 0.05

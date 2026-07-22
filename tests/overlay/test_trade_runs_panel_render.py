"""Covers TradeRunsPanel/TradeLedgerPanel's rendering rules: which milestone dialog gets
embedded for a leg's current step, Finalize only enables once every leg is done, Abandon
only shows before anything's actually been bought, and the ledger's profit coloring."""
from datetime import UTC, datetime

import pytest
from doubles import make_trade_leg, make_trade_run
from PySide6.QtWidgets import QLabel, QPushButton

from db.models import CargoTransferType, LegType
from overlay.trade_run_widgets import (
    BuyCargoWidget,
    ConfirmLoadedWidget,
    ConfirmUnloadedWidget,
    SellCargoWidget,
    TravelWidget,
)


@pytest.fixture
def trade_runs_panel(qapp, fake_cache, monkeypatch):
    from overlay import uex_lookup
    from overlay.trade_runs_panel import TradeRunsPanel

    # TravelWidget resolves the leg's terminal via the live reference cache — populate it
    # with the same fixture data make_trade_leg's default terminal_id (1) matches.
    monkeypatch.setattr(uex_lookup, "uex_cache", fake_cache)
    return TradeRunsPanel()


@pytest.fixture
def trade_ledger_panel(qapp, fake_cache, monkeypatch):
    from overlay import uex_lookup
    from overlay.trade_runs_panel import TradeLedgerPanel

    # _build_ledger_row resolves each leg's commodity code via the live reference cache.
    monkeypatch.setattr(uex_lookup, "uex_cache", fake_cache)
    return TradeLedgerPanel()


def _mark_done_buttons(card):
    return [b for b in card.findChildren(QPushButton) if b.objectName() == "markDoneButton"]


def test_fresh_acquisition_leg_shows_travel_widget(trade_runs_panel):
    # started_at is always set by the time a real leg exists (see trade_run_store) —
    # "fresh" means not yet arrived, not "not yet started".
    leg = make_trade_leg(LegType.ACQUISITION, started_at=datetime.now(UTC))
    run = make_trade_run(legs=[leg])
    card = trade_runs_panel._build_run_card(run, 0)
    assert card.findChild(TravelWidget) is not None


def test_departed_acquisition_leg_shows_buy_cargo_widget(trade_runs_panel):
    now = datetime.now(UTC)
    leg = make_trade_leg(LegType.ACQUISITION, started_at=now, reached_at=now)
    run = make_trade_run(legs=[leg])
    card = trade_runs_panel._build_run_card(run, 0)
    assert card.findChild(BuyCargoWidget) is not None


def test_buy_cargo_widget_suggests_container_mix_when_ship_and_sizes_known(trade_runs_panel):
    now = datetime.now(UTC)
    leg = make_trade_leg(LegType.ACQUISITION, started_at=now, reached_at=now)
    run = make_trade_run(legs=[leg], ship="Railen", usable_container_sizes="16,32")
    card = trade_runs_panel._build_run_card(run, 0)

    widget = card.findChild(BuyCargoWidget)
    suggestion = widget.findChild(QLabel, "legDetail")
    # Railen (96 SCU) with only 16/32 SCU containers packs exactly: 3 x 32 = 96 SCU.
    assert "Railen" in suggestion.text()
    assert "96" in suggestion.text()


def test_buy_cargo_widget_has_no_suggestion_without_a_ship(trade_runs_panel):
    now = datetime.now(UTC)
    leg = make_trade_leg(LegType.ACQUISITION, started_at=now, reached_at=now)
    run = make_trade_run(legs=[leg], ship=None, usable_container_sizes="16,32")
    card = trade_runs_panel._build_run_card(run, 0)

    widget = card.findChild(BuyCargoWidget)
    assert widget.findChild(QLabel, "legDetail") is None


def test_bought_acquisition_leg_shows_confirm_loaded_widget(trade_runs_panel):
    now = datetime.now(UTC)
    leg = make_trade_leg(LegType.ACQUISITION, started_at=now, reached_at=now, transaction_completed_at=now)
    run = make_trade_run(legs=[leg])
    card = trade_runs_panel._build_run_card(run, 0)
    assert card.findChild(ConfirmLoadedWidget) is not None


def test_fresh_sale_leg_shows_travel_widget(trade_runs_panel):
    leg = make_trade_leg(LegType.SALE, started_at=datetime.now(UTC))
    run = make_trade_run(legs=[leg])
    card = trade_runs_panel._build_run_card(run, 0)
    assert card.findChild(TravelWidget) is not None


def test_departed_autoload_sale_leg_shows_sell_cargo_widget(trade_runs_panel):
    # Autoload skips the unload step entirely — straight to Sell after arriving.
    now = datetime.now(UTC)
    leg = make_trade_leg(LegType.SALE, CargoTransferType.AUTOLOAD, started_at=now, reached_at=now)
    run = make_trade_run(legs=[leg])
    card = trade_runs_panel._build_run_card(run, 0)
    assert card.findChild(SellCargoWidget) is not None


def test_departed_manual_sale_leg_shows_confirm_unloaded_widget(trade_runs_panel):
    # Manual unload is a real, separate, timed step before the sale can be recorded.
    now = datetime.now(UTC)
    leg = make_trade_leg(LegType.SALE, CargoTransferType.MANUAL, started_at=now, reached_at=now)
    run = make_trade_run(legs=[leg])
    card = trade_runs_panel._build_run_card(run, 0)
    assert card.findChild(ConfirmUnloadedWidget) is not None
    assert card.findChild(SellCargoWidget) is None


def test_unloaded_manual_sale_leg_shows_sell_cargo_widget(trade_runs_panel):
    now = datetime.now(UTC)
    leg = make_trade_leg(
        LegType.SALE, CargoTransferType.MANUAL, started_at=now, reached_at=now, transferred_at=now,
    )
    run = make_trade_run(legs=[leg])
    card = trade_runs_panel._build_run_card(run, 0)
    assert card.findChild(SellCargoWidget) is not None


def test_leg_ready_to_finalize_shows_plain_mark_done_button(trade_runs_panel):
    now = datetime.now(UTC)
    leg = make_trade_leg(
        LegType.ACQUISITION, started_at=now, reached_at=now, transaction_completed_at=now, transferred_at=now
    )
    run = make_trade_run(legs=[leg])
    card = trade_runs_panel._build_run_card(run, 0)
    assert len(_mark_done_buttons(card)) == 1


def test_travel_confirm_click_passes_the_real_leg_id(trade_runs_panel):
    # Regression test: QPushButton.clicked emits a bool. A callback with exactly one
    # parameter — even one with a default, like `lambda lid=leg.id: ...` — receives that
    # bool instead of falling back to its default, silently replacing the captured leg id
    # with False (which then hits the DB as `WHERE trade_leg.id = false`). Must exercise
    # a real .click(), not a direct method call — every other test in this file calls
    # _on_advance directly, which is exactly why this slipped through originally.
    run = make_trade_run(legs=[make_trade_leg(LegType.ACQUISITION)])
    leg = run.legs[0]
    captured = []
    trade_runs_panel._on_advance = lambda leg_id: captured.append(leg_id)

    card = trade_runs_panel._build_run_card(run, 0)
    card.findChild(QPushButton, "confirmButton").click()

    assert captured == [leg.id]


def test_confirm_loaded_click_passes_the_real_leg_id(trade_runs_panel):
    now = datetime.now(UTC)
    leg = make_trade_leg(LegType.ACQUISITION, started_at=now, reached_at=now, transaction_completed_at=now)
    run = make_trade_run(legs=[leg])
    captured = []
    trade_runs_panel._on_advance = lambda leg_id: captured.append(leg_id)

    card = trade_runs_panel._build_run_card(run, 0)
    card.findChild(QPushButton, "confirmButton").click()

    assert captured == [leg.id]


def test_mark_done_click_passes_the_real_leg_id(trade_runs_panel):
    now = datetime.now(UTC)
    leg = make_trade_leg(
        LegType.ACQUISITION, started_at=now, reached_at=now, transaction_completed_at=now, transferred_at=now
    )
    run = make_trade_run(legs=[leg])
    captured = []
    trade_runs_panel._on_advance = lambda leg_id: captured.append(leg_id)

    card = trade_runs_panel._build_run_card(run, 0)
    card.findChild(QPushButton, "markDoneButton").click()

    assert captured == [leg.id]


def test_breadcrumb_shows_travel_plus_acquisition_steps(trade_runs_panel):
    from overlay.trade_run_widgets import _TravelNode

    run = make_trade_run(legs=[make_trade_leg(LegType.ACQUISITION)])
    card = trade_runs_panel._build_run_card(run, 0)

    assert card.findChild(_TravelNode) is not None
    labels = [label.text() for label in card.findChildren(QLabel, "breadcrumbLabel")]
    assert labels == ["Travel", "Buy cargo", "Confirm loaded", "Finalize"]


def test_breadcrumb_shows_unload_step_for_manual_sale(trade_runs_panel):
    now = datetime.now(UTC)
    leg = make_trade_leg(LegType.SALE, CargoTransferType.MANUAL, started_at=now, reached_at=now)
    run = make_trade_run(legs=[leg])
    card = trade_runs_panel._build_run_card(run, 0)

    labels = [label.text() for label in card.findChildren(QLabel, "breadcrumbLabel")]
    assert labels == ["Travel", "Confirm unloaded", "Sell cargo", "Finalize"]


def test_breadcrumb_skips_unload_step_for_autoload_sale(trade_runs_panel):
    run = make_trade_run(legs=[make_trade_leg(LegType.SALE, CargoTransferType.AUTOLOAD)])
    card = trade_runs_panel._build_run_card(run, 0)

    labels = [label.text() for label in card.findChildren(QLabel, "breadcrumbLabel")]
    assert labels == ["Travel", "Sell cargo", "Finalize"]


def test_only_the_current_leg_gets_a_live_dialog(trade_runs_panel):
    # Both legs fresh — acquisitions go before sales, so only the acquisition leg should
    # render a live TravelWidget; the sale leg (not yet reached) shows a read-only recap.
    run = make_trade_run(legs=[make_trade_leg(LegType.ACQUISITION), make_trade_leg(LegType.SALE)])
    card = trade_runs_panel._build_run_card(run, 0)
    assert len(card.findChildren(TravelWidget)) == 1


def test_finalize_button_disabled_until_every_leg_is_finalized(trade_runs_panel):
    run = make_trade_run(legs=[make_trade_leg(LegType.ACQUISITION), make_trade_leg(LegType.SALE)])
    card = trade_runs_panel._build_run_card(run, 0)
    finalize_button = card.findChild(QPushButton, "finalizeRunButton")
    assert finalize_button.isEnabled() is False


def test_finalize_button_enabled_when_every_leg_is_finalized(trade_runs_panel):
    now = datetime.now(UTC)

    def _finished(leg_type):
        return make_trade_leg(
            leg_type, started_at=now, reached_at=now, transaction_completed_at=now,
            transferred_at=now, finalized_at=now,
        )

    run = make_trade_run(legs=[_finished(LegType.ACQUISITION), _finished(LegType.SALE)])
    card = trade_runs_panel._build_run_card(run, 0)
    finalize_button = card.findChild(QPushButton, "finalizeRunButton")
    assert finalize_button.isEnabled() is True


def test_upcoming_finalize_row_shown_while_a_leg_is_unfinished(trade_runs_panel):
    run = make_trade_run(legs=[make_trade_leg(LegType.ACQUISITION), make_trade_leg(LegType.SALE)])
    card = trade_runs_panel._build_run_card(run, 0)
    assert card.findChild(QLabel, "upcomingStepLabel") is not None


def test_upcoming_finalize_row_hidden_once_every_leg_is_finalized(trade_runs_panel):
    now = datetime.now(UTC)

    def _finished(leg_type):
        return make_trade_leg(
            leg_type, started_at=now, reached_at=now, transaction_completed_at=now,
            transferred_at=now, finalized_at=now,
        )

    run = make_trade_run(legs=[_finished(LegType.ACQUISITION), _finished(LegType.SALE)])
    card = trade_runs_panel._build_run_card(run, 0)
    assert card.findChild(QLabel, "upcomingStepLabel") is None


def test_run_header_shows_quantity_pill(trade_runs_panel):
    leg = make_trade_leg(LegType.ACQUISITION, quantity_scu=281)
    run = make_trade_run(legs=[leg, make_trade_leg(LegType.SALE, quantity_scu=281)])
    card = trade_runs_panel._build_run_card(run, 0)

    values = [label.text() for label in card.findChildren(QLabel, "runMoneyValue")]
    assert "281 SCU" in values


def test_help_text_present_on_in_progress_panel(trade_runs_panel):
    assert trade_runs_panel.findChild(QLabel, "panelHelpText") is not None


def test_help_text_absent_on_ledger_panel(trade_ledger_panel):
    assert trade_ledger_panel.findChild(QLabel, "panelHelpText") is None


def test_buy_dialog_restates_the_action_as_plain_language(trade_runs_panel):
    leg = make_trade_leg(
        LegType.ACQUISITION, quantity_scu=281, commodity_name="Medical Supplies", terminal_name="Admin - Terra Gateway",
        started_at=datetime.now(UTC), reached_at=datetime.now(UTC),
    )
    run = make_trade_run(legs=[leg])
    card = trade_runs_panel._build_run_card(run, 0)

    action_line = card.findChild(QLabel, "dialogActionLine")
    assert action_line.text() == "Purchase 281 SCU of Medical Supplies at Admin - Terra Gateway"


def test_sell_dialog_restates_the_action_as_plain_language(trade_runs_panel):
    leg = make_trade_leg(
        LegType.SALE, CargoTransferType.AUTOLOAD,
        quantity_scu=281, commodity_name="Medical Supplies", terminal_name="Rod's Fuel",
        started_at=datetime.now(UTC), reached_at=datetime.now(UTC),
    )
    run = make_trade_run(legs=[leg])
    card = trade_runs_panel._build_run_card(run, 0)

    action_line = card.findChild(QLabel, "dialogActionLine")
    assert action_line.text() == "Sell 281 SCU of Medical Supplies at Rod's Fuel"


def test_abandon_button_shown_before_anything_is_bought(trade_runs_panel):
    run = make_trade_run(legs=[make_trade_leg(LegType.ACQUISITION), make_trade_leg(LegType.SALE)])
    card = trade_runs_panel._build_run_card(run, 0)
    assert card.findChild(QPushButton, "abandonRunButton") is not None


def test_abandon_button_hidden_once_cargo_is_bought(trade_runs_panel):
    now = datetime.now(UTC)
    bought_leg = make_trade_leg(LegType.ACQUISITION, transaction_completed_at=now)
    run = make_trade_run(legs=[bought_leg, make_trade_leg(LegType.SALE)])
    card = trade_runs_panel._build_run_card(run, 0)
    assert card.findChild(QPushButton, "abandonRunButton") is None


def test_ledger_row_marks_profitable_run(trade_ledger_panel):
    now = datetime.now(UTC)
    acquisition = make_trade_leg(
        LegType.ACQUISITION, quantity_scu=10, price_per_unit=5, transaction_completed_at=now
    )
    sale = make_trade_leg(LegType.SALE, quantity_scu=10, price_per_unit=8, transaction_completed_at=now)
    run = make_trade_run(legs=[acquisition, sale], finalized_at=now)

    row = trade_ledger_panel._build_ledger_row(run)
    profit_label = row.findChild(QLabel, "ledgerProfit")
    assert profit_label.property("profitable") is True
    assert "+30" in profit_label.text()


def test_ledger_row_marks_unprofitable_run(trade_ledger_panel):
    now = datetime.now(UTC)
    acquisition = make_trade_leg(
        LegType.ACQUISITION, quantity_scu=10, price_per_unit=8, transaction_completed_at=now
    )
    sale = make_trade_leg(LegType.SALE, quantity_scu=10, price_per_unit=5, transaction_completed_at=now)
    run = make_trade_run(legs=[acquisition, sale], finalized_at=now)

    row = trade_ledger_panel._build_ledger_row(run)
    profit_label = row.findChild(QLabel, "ledgerProfit")
    assert profit_label.property("profitable") is False


def test_ledger_row_shows_vehicle_and_length(trade_ledger_panel):
    created = datetime(2026, 7, 20, 14, 0, tzinfo=UTC)
    finalized = datetime(2026, 7, 20, 14, 8, tzinfo=UTC)
    run = make_trade_run(ship="Freelancer MAX", created_at=created, finalized_at=finalized)

    row = trade_ledger_panel._build_ledger_row(run)
    labels = [label.text() for label in row.findChildren(QLabel)]
    assert "Freelancer MAX" in labels
    assert "8m" in labels


def test_ledger_groups_runs_by_day(trade_ledger_panel):
    day_one = datetime(2026, 7, 20, 14, 0, tzinfo=UTC)
    day_two = datetime(2026, 7, 19, 15, 0, tzinfo=UTC)
    run_a = make_trade_run(finalized_at=day_one)
    run_b = make_trade_run(finalized_at=day_two)

    trade_ledger_panel.render_runs([run_a, run_b])

    assert trade_ledger_panel.run_count() == 2
    day_titles = [label.text() for label in trade_ledger_panel.findChildren(QLabel, "ledgerDayTitle")]
    assert day_titles == ["Monday, July 20, 2026", "Sunday, July 19, 2026"]


def test_ledger_day_total_sums_all_runs_that_day(qapp):
    from overlay.trade_runs_panel import TradeLedgerPanel

    day = datetime(2026, 7, 20, 9, 0, tzinfo=UTC)
    run_a = make_trade_run(legs=[
        make_trade_leg(LegType.ACQUISITION, quantity_scu=10, price_per_unit=5),
        make_trade_leg(LegType.SALE, quantity_scu=10, price_per_unit=8),
    ], finalized_at=day)
    run_b = make_trade_run(legs=[
        make_trade_leg(LegType.ACQUISITION, quantity_scu=20, price_per_unit=3),
        make_trade_leg(LegType.SALE, quantity_scu=20, price_per_unit=4),
    ], finalized_at=day)

    total_row = TradeLedgerPanel._build_day_total_row([run_a, run_b])
    profit_label = total_row.findChild(QLabel, "ledgerProfit")
    assert profit_label.text() == "+50 aUEC"  # (80-50) + (80-60)


def test_toggling_ledger_day_does_not_touch_the_store(trade_ledger_panel):
    run = make_trade_run(finalized_at=datetime.now(UTC))
    trade_ledger_panel.render_runs([run])

    day = run.finalized_at.date()
    currently_expanded = trade_ledger_panel._day_expanded.get(day, True)
    trade_ledger_panel._toggle_day(day, currently_expanded)

    assert trade_ledger_panel.run_count() == 1
    assert trade_ledger_panel._day_expanded[day] is False


def test_clear_rows_hides_removed_widgets_immediately(qapp):
    # Regression test: takeAt() alone only detaches a widget from layout management —
    # it keeps painting at its old position until deleteLater()'s deferred cleanup
    # actually runs. Confirmed by direct repro this caused old ledger rows to visibly
    # ghost behind a newly-collapsed day group. hide() must happen synchronously.
    from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

    from overlay.trade_runs_panel import _clear_rows

    container = QWidget()
    layout = QVBoxLayout(container)
    widget = QLabel(parent=container, text="stale")
    layout.addWidget(widget)
    layout.addStretch()

    _clear_rows(layout)

    # isHidden() reflects an explicit hide() call regardless of the parent window's own
    # shown state (unlike isVisible(), which would be False here either way since
    # container was never shown) — this is what actually distinguishes the fix.
    assert widget.isHidden() is True

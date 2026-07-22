from datetime import UTC, datetime, timedelta

from PySide6.QtCore import QSize, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from qasync import asyncSlot

from db import trade_run_store
from db.models import LegType
from overlay import theme, uex_lookup
from overlay.theme import HudWindow
from overlay.trade_run_widgets import (
    BuyCargoWidget,
    ConfirmLoadedWidget,
    ConfirmUnloadedWidget,
    SellCargoWidget,
    TravelWidget,
    build_leg_breadcrumb,
    build_recap_grid,
)

# Shared column widths so the ledger's header row and every day-group/run/total row
# line up — same reasoning as results_panel.py's SCU_COLUMN_WIDTH etc.
LEDGER_CHEVRON_COLUMN_WIDTH = 20
LEDGER_TIME_COLUMN_WIDTH = 48
LEDGER_VEHICLE_COLUMN_WIDTH = 120
LEDGER_LENGTH_COLUMN_WIDTH = 64
LEDGER_SCU_COLUMN_WIDTH = 70
LEDGER_PROFIT_COLUMN_WIDTH = 110
# Must match theme.py's `QScrollArea#cardScrollArea QScrollBar:vertical { width: ... }`
# — the ledger's column header lives in the panel's own layout, as a sibling of the
# QScrollArea, not inside its viewport. Once the scrollbar appears it eats this many
# pixels from the rows' available width without the (unrelated) header row shrinking
# to match, so columns drift out of alignment. Reserving the same width permanently
# (see _build_ui's setVerticalScrollBarPolicy) and mirroring it in the header's
# trailing spacer keeps both sides consistent whether or not scrolling is needed.
LEDGER_SCROLLBAR_COLUMN_WIDTH = 10


def _build_scroll_list(parent, header_text, column_header=None, help_text=None):
    """Shared scaffold for both panels: a header label over a scrollable, top-packed
    column of cards. Uses a plain QScrollArea + QVBoxLayout rather than QListWidget —
    QListWidget's setItemWidget() mechanism has a confirmed PySide6 bug where a deeply
    nested, id-selector-styled QPushButton (e.g. TravelWidget's green confirm button)
    paints its label vertically mirrored once embedded that way. The exact same card,
    reparented into an ordinary layout instead, paints correctly — a plain QVBoxLayout
    of real child widgets is the right tool here anyway, since there's no need for
    QListWidget's row-recycling/selection machinery for a handful of rich cards.

    column_header, if given, is a widget (e.g. a table-style header row) inserted
    between the panel title and the scrollable area — only the Ledger uses this.
    help_text, if given, is a short explainer inserted the same way — only In Progress
    uses this, for a first-time pilot who hasn't seen the leg/milestone flow before.
    """
    layout = QVBoxLayout(parent)
    layout.setContentsMargins(12, 5, 12, 6)
    layout.setSpacing(2)
    layout.addWidget(QLabel(parent=parent, text=header_text, objectName="panelHeader"))
    if help_text is not None:
        help_label = QLabel(parent=parent, text=help_text, objectName="panelHelpText")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
    if column_header is not None:
        layout.addWidget(column_header)

    scroll_area = QScrollArea(parent=parent, objectName="cardScrollArea")
    scroll_area.setWidgetResizable(True)
    scroll_area.setFrameShape(QFrame.NoFrame)
    # Vertical scrolling is fine here (lists can genuinely outgrow the fixed overlay
    # height) — horizontal never is. Disabling it outright rather than trusting the
    # default "as needed" policy means a too-narrow window clips/wraps content instead
    # of ever growing a sideways scrollbar.
    scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    content = QWidget(objectName="cardScrollContent")
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(0, 0, 0, 0)
    content_layout.setSpacing(6)
    content_layout.addStretch()  # keeps cards packed at the top instead of spread out

    scroll_area.setWidget(content)
    layout.addWidget(scroll_area)
    return scroll_area, content_layout


def _content_widgets(content_layout):
    """Actual rendered rows (cards, or the message wrapper) — skips stretch items, of
    which there can be one (packing cards to the top) or two (centering a message)."""
    widgets = []
    for index in range(content_layout.count()):
        widget = content_layout.itemAt(index).widget()
        if widget is not None:
            widgets.append(widget)
    return widgets


def _row_text(content_layout, index):
    widgets = _content_widgets(content_layout)
    if index >= len(widgets):
        return None
    widget = widgets[index]
    if isinstance(widget, QLabel):
        return widget.text()
    label = widget.findChild(QLabel, "emptyStateLabel")
    return label.text() if label else None


def _clear_rows(content_layout):
    while content_layout.count() > 1:  # leave the trailing stretch in place
        item = content_layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            # takeAt() only detaches the widget from layout management — it keeps
            # painting at its last position until deleteLater()'s deferred cleanup
            # actually runs, which can visibly overlap with newly-inserted replacement
            # widgets during the same re-render (confirmed by direct repro: collapsing
            # a day group left its old rows ghosted behind the next group). hide() now,
            # delete later.
            widget.hide()
            widget.deleteLater()


def _show_message_row(content_layout, text, subtitle=None):
    _clear_rows(content_layout)

    message = QWidget()
    message_layout = QVBoxLayout(message)
    message_layout.setContentsMargins(0, 0, 0, 0)
    message_layout.setSpacing(4)
    message_layout.addWidget(
        QLabel(text=text, objectName="emptyStateLabel", alignment=Qt.AlignmentFlag.AlignCenter)
    )
    if subtitle:
        message_layout.addWidget(
            QLabel(text=subtitle, objectName="emptyStateSubtitle", alignment=Qt.AlignmentFlag.AlignCenter)
        )

    # Stretch on both sides (the trailing one is already there) centers the message in
    # the available space instead of it sitting pinned to the top of a mostly-empty box.
    content_layout.insertWidget(0, message)
    content_layout.insertStretch(0)


def _format_age(created_at):
    total_minutes = int((datetime.now(UTC) - created_at).total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours > 0:
        return f"{hours}h {minutes}m ago"
    return f"{minutes}m ago"


def _format_duration(delta):
    total_seconds = max(0, int(delta.total_seconds()))
    if total_seconds < 60:
        return f"{total_seconds}s"
    total_minutes = total_seconds // 60
    if total_minutes < 60:
        return f"{total_minutes}m"
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h {minutes}m"


def _format_day_age(day):
    delta_days = (datetime.now(UTC).date() - day).days
    if delta_days <= 0:
        return "Today"
    if delta_days == 1:
        return "Yesterday"
    return f"{delta_days} days ago"


def _group_runs_by_day(runs):
    # Relies on runs already being ordered by finalized_at descending (get_finalized_runs'
    # own ordering) — same-day runs are then guaranteed adjacent, so a single linear pass
    # can bucket them without needing a dict keyed by date.
    groups = []
    for run in runs:
        day = run.finalized_at.date()
        if groups and groups[-1][0] == day:
            groups[-1][1].append(run)
        else:
            groups.append((day, [run]))
    return groups


def _ordered_legs(run):
    acquisitions = sorted(
        (leg for leg in run.legs if leg.leg_type == LegType.ACQUISITION), key=lambda leg: leg.created_at
    )
    sales = sorted((leg for leg in run.legs if leg.leg_type == LegType.SALE), key=lambda leg: leg.created_at)
    return acquisitions + sales


def _current_leg(run):
    return next((leg for leg in _ordered_legs(run) if leg.finalized_at is None), None)


def _can_abandon(run):
    # Nothing's actually been bought yet — deleting the run loses no real progress.
    return not any(
        leg.leg_type == LegType.ACQUISITION and leg.transaction_completed_at is not None for leg in run.legs
    )


def _is_ready_to_finalize(run):
    return all(leg.finalized_at is not None for leg in run.legs)


def _leg_values(leg, draft):
    if draft is not None:
        return draft["quantity_scu"], draft["price_per_unit"], draft["cargo_transfer_fee"]
    return leg.quantity_scu, leg.price_per_unit, leg.cargo_transfer_fee


def _projected_totals(run, draft_by_leg_id):
    # "Actual where known, planned estimate where not" falls out for free here — quantity_scu/
    # price_per_unit already get overwritten with the real values once record_purchase/
    # record_sale runs, and start out as the searched estimate until then. A live draft
    # (the leg currently being edited, not yet submitted) overrides both.
    investment = 0
    revenue = 0
    fees = 0
    for leg in run.legs:
        quantity, price, fee = _leg_values(leg, draft_by_leg_id.get(leg.id))
        fees += fee
        if leg.leg_type == LegType.ACQUISITION:
            investment += quantity * price
        else:
            revenue += quantity * price
    return investment, revenue - investment - fees


class TradeRunsPanel(HudWindow):
    run_finalized = Signal(object)
    runs_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(theme.STYLESHEET)
        self._last_runs = []
        # Only ever hold entries the pilot manually toggled — anything not in these dicts
        # follows the live "smart default" computed at render time (current leg expanded,
        # first run in the list expanded), so the next actionable step keeps auto-opening
        # instead of freezing at whatever was true the first time it was ever seen.
        self._run_expanded = {}
        self._leg_expanded = {}
        # Keyed by leg id — survives a refresh() (which the canvas triggers on every tab
        # switch, not just after a mutating action) so a pilot mid-typing in Buy/Sell Cargo
        # doesn't lose it by tabbing away and back.
        self._draft_purchase = {}
        # (investment_label, profit_label) per run id, so a draft edit can update the header
        # in place instead of rebuilding the whole list on every keystroke.
        self._run_header_labels = {}
        self._build_ui()

    def _build_ui(self):
        self.runs_list, self._runs_content_layout = _build_scroll_list(
            self, "▸ TRADE RUNS IN PROGRESS",
            help_text=(
                "Each run tracks one Acquisition and one Sale leg. Work through a leg's "
                "milestones in order — travel, transact, confirm — then Finalize once "
                "every leg is done to move the run to the Ledger."
            ),
        )

    @asyncSlot()
    async def refresh(self):
        try:
            runs = await trade_run_store.get_in_progress_runs()
        except Exception as exc:
            self.show_message(f"Couldn't load trade runs — {exc}")
            return
        self.render_runs(runs)

    def render_runs(self, runs):
        self._last_runs = runs
        self._run_header_labels = {}
        self.runs_changed.emit(len(runs))
        if not runs:
            _show_message_row(
                self._runs_content_layout,
                "No trade runs in progress.",
                "Search a route and tap + to start tracking one.",
            )
            return

        _clear_rows(self._runs_content_layout)
        for index, run in enumerate(runs):
            card = self._build_run_card(run, index)
            self._runs_content_layout.insertWidget(self._runs_content_layout.count() - 1, card)

    @Slot(str)
    def show_message(self, message):
        _show_message_row(self._runs_content_layout, message)

    def row_count(self):
        return len(_content_widgets(self._runs_content_layout))

    def row_text(self, index=0):
        return _row_text(self._runs_content_layout, index)

    def _build_run_card(self, run, index):
        card = QFrame(objectName="tradeRunCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 9, 10, 9)
        layout.setSpacing(6)

        run_expanded = self._run_expanded.get(run.id, index == 0)

        layout.addWidget(self._build_run_header(run, run_expanded))

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 4, 0, 0)
        body_layout.setSpacing(4)

        current_leg = _current_leg(run)
        for leg in _ordered_legs(run):
            is_current = leg.id == (current_leg.id if current_leg else None)
            body_layout.addWidget(self._build_leg_container(run, leg, is_current))
        if not _is_ready_to_finalize(run):
            # Every leg gets its own row already (current or collapsed "Pending"), but
            # finalization itself had no representation at all until every leg was
            # already done — this greyed row previews that it's still coming.
            body_layout.addWidget(self._build_upcoming_finalize_row())
        body_layout.addWidget(self._build_run_footer(run))
        # Reparent into the card's layout BEFORE touching visibility — setVisible() on a
        # still-parentless QWidget makes Qt treat it as a top-level window for a moment,
        # which corrupts the paint state of deeply-nested children once it's reparented
        # (confirmed by direct repro: this is what caused the confirm button's text to
        # render upside-down inside the list). Also must come after the layout/children
        # are fully built — setVisible() any earlier than that leaves this widget's
        # sizeHint() permanently stale, a separate, already-diagnosed PySide6 gotcha.
        layout.addWidget(body)
        body.setVisible(run_expanded)

        return card

    def _build_run_header(self, run, expanded):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        chevron = QPushButton(parent=row, text="▾" if expanded else "▸", objectName="chevronButton")
        chevron.clicked.connect(lambda checked=False, rid=run.id, cur=expanded: self._toggle_run(rid, cur))
        layout.addWidget(chevron)

        title = QWidget()
        title_layout = QVBoxLayout(title)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(0)
        title_layout.addWidget(QLabel(parent=title, text=run.ship or "Unspecified ship", objectName="runShip"))
        title_layout.addWidget(QLabel(parent=title, text=_format_age(run.created_at), objectName="runAge"))
        layout.addWidget(title)
        layout.addStretch()

        quantity_label = QLabel(
            parent=row, text=f"{trade_run_store.run_acquired_scu(run):,} SCU", objectName="runMoneyValue"
        )
        investment, profit = _projected_totals(run, self._draft_purchase)
        investment_label = QLabel(parent=row, text=f"{investment:,} aUEC", objectName="runMoneyValue")
        profit_label = QLabel(parent=row, objectName="runMoneyValue")
        self._set_profit_label(profit_label, profit)
        pills = (
            ("Quantity", quantity_label), ("Investment", investment_label), ("Projected profit", profit_label),
        )
        for label_title, value_label in pills:
            block = QWidget()
            block_layout = QVBoxLayout(block)
            block_layout.setContentsMargins(0, 0, 0, 0)
            block_layout.setSpacing(0)
            caption = QLabel(parent=block, text=label_title, objectName="runMoneyLabel")
            block_layout.addWidget(caption)
            block_layout.addWidget(value_label)
            layout.addWidget(block)
        self._run_header_labels[run.id] = (investment_label, profit_label)

        if _can_abandon(run):
            abandon_button = QPushButton(parent=row, objectName="abandonRunButton")
            abandon_button.setIcon(theme.load_icon("trash-can", theme.ERROR))
            abandon_button.setIconSize(QSize(14, 14))
            abandon_button.setToolTip("Abandon run")
            abandon_button.clicked.connect(lambda checked=False, rid=run.id: self._on_abandon(rid))
            layout.addWidget(abandon_button)

        return row

    @staticmethod
    def _set_profit_label(label, profit):
        label.setText(f"{profit:+,} aUEC")
        label.setProperty("profitable", profit >= 0)
        label.style().unpolish(label)
        label.style().polish(label)

    def _build_leg_container(self, run, leg, is_current):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        default_expanded = is_current
        expanded = self._leg_expanded.get(leg.id, default_expanded)

        layout.addWidget(self._build_leg_summary_row(leg, is_current, expanded))

        body = QWidget(objectName="legExpandedArea")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(28, 4, 4, 8)

        if is_current:
            body_layout.addWidget(build_leg_breadcrumb(leg))
            body_layout.addWidget(self._build_leg_dialog(run, leg))
        else:
            body_layout.addWidget(build_recap_grid(leg))
        # Reparent before setVisible() — see the note in _build_run_card.
        layout.addWidget(body)
        body.setVisible(expanded)
        return container

    def _build_leg_summary_row(self, leg, is_current, expanded):
        row = QWidget(objectName="legSummaryRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        chevron = QPushButton(parent=row, text="▾" if expanded else "▸", objectName="chevronButton")
        chevron.setProperty("small", True)
        chevron.clicked.connect(lambda checked=False, lid=leg.id, cur=expanded: self._toggle_leg(lid, cur))
        layout.addWidget(chevron)

        badge = QLabel(parent=row, text="BUY" if leg.leg_type == LegType.ACQUISITION else "SELL", objectName="legBadge")
        badge.setProperty("legType", leg.leg_type.value)
        badge.style().unpolish(badge)
        badge.style().polish(badge)
        layout.addWidget(badge)

        detail_widget = QWidget()
        detail_layout = QVBoxLayout(detail_widget)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(1)
        detail_layout.addWidget(QLabel(parent=detail_widget, text=leg.terminal_name, objectName="legTerminal"))
        detail_layout.addWidget(QLabel(
            parent=detail_widget,
            text=f"{leg.quantity_scu} SCU {leg.commodity_name} @ {leg.price_per_unit:,} aUEC",
            objectName="legDetail",
        ))
        layout.addWidget(detail_widget, 1)

        if leg.finalized_at is not None:
            status_text, status_class = "Finalized", "success"
        elif is_current:
            status_text, status_class = "In progress", "current"
        else:
            status_text, status_class = "Pending", "pending"
        status_label = QLabel(parent=row, text=status_text, objectName="legStatus")
        status_label.setProperty("status", status_class)
        layout.addWidget(status_label)

        return row

    def _build_leg_dialog(self, run, leg):
        field = trade_run_store.next_unset_field(leg)

        if field == "reached_at":
            return TravelWidget(leg, lambda checked=False, lid=leg.id: self._on_advance(lid))

        if field == "transaction_completed_at" and leg.leg_type == LegType.ACQUISITION:
            return BuyCargoWidget(
                leg,
                run=run,
                on_change=lambda draft, lid=leg.id, rid=run.id: self._on_draft_changed(lid, rid, draft),
                on_submit=lambda *values, lid=leg.id: self._on_record_purchase(lid, *values),
            )

        if field == "transaction_completed_at":
            return SellCargoWidget(
                leg,
                on_change=lambda draft, lid=leg.id, rid=run.id: self._on_draft_changed(lid, rid, draft),
                on_submit=lambda *values, lid=leg.id: self._on_record_sale(lid, *values),
            )

        if field == "transferred_at" and leg.leg_type == LegType.SALE:
            return ConfirmUnloadedWidget(leg, lambda checked=False, lid=leg.id: self._on_advance(lid))

        if field == "transferred_at":
            return ConfirmLoadedWidget(leg, lambda checked=False, lid=leg.id: self._on_advance(lid))

        # finalized_at (or already fully advanced) — no more fields to capture, but the
        # leg's own recap (what was actually bought/sold, fees, timestamps) still belongs
        # here — otherwise the one step every leg passes through renders as a bare button
        # with no information at all, unlike every other leg state.
        button_row = QWidget()
        button_layout = QVBoxLayout(button_row)
        button_layout.setContentsMargins(0, 8, 0, 0)
        button_layout.setSpacing(8)
        button_layout.addWidget(build_recap_grid(leg))
        mark_done_button = QPushButton(parent=button_row, text="Mark Done", objectName="markDoneButton")
        mark_done_button.clicked.connect(lambda checked=False, lid=leg.id: self._on_advance(lid))
        button_layout.addWidget(mark_done_button)
        return button_row

    @staticmethod
    def _build_upcoming_finalize_row():
        row = QWidget(objectName="upcomingStepRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(28, 2, 4, 2)
        layout.addWidget(QLabel(parent=row, text="Run Summary and Finalization", objectName="upcomingStepLabel"))
        layout.addStretch()
        return row

    def _build_run_footer(self, run):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.addStretch()

        finalize_button = QPushButton(parent=row, text="Finalize Run", objectName="finalizeRunButton")
        finalize_button.setEnabled(_is_ready_to_finalize(run))
        finalize_button.clicked.connect(lambda checked=False, rid=run.id: self._on_finalize(rid))
        layout.addWidget(finalize_button)

        return row

    def _toggle_run(self, run_id, currently_expanded):
        self._run_expanded[run_id] = not currently_expanded
        self.render_runs(self._last_runs)

    def _toggle_leg(self, leg_id, currently_expanded):
        self._leg_expanded[leg_id] = not currently_expanded
        self.render_runs(self._last_runs)

    def _on_draft_changed(self, leg_id, run_id, draft):
        self._draft_purchase[leg_id] = draft
        run = next((r for r in self._last_runs if r.id == run_id), None)
        labels = self._run_header_labels.get(run_id)
        if run is None or labels is None:
            return
        investment, profit = _projected_totals(run, self._draft_purchase)
        investment_label, profit_label = labels
        investment_label.setText(f"{investment:,} aUEC")
        self._set_profit_label(profit_label, profit)

    @asyncSlot(object)
    async def _on_advance(self, leg_id):
        try:
            await trade_run_store.advance_leg(leg_id)
        except Exception as exc:
            self.show_message(f"Couldn't update leg — {exc}")
            return
        await self.refresh()

    @asyncSlot(object, object, object, object, object)
    async def _on_record_purchase(self, leg_id, quantity_scu, price_per_unit, cargo_transfer_type, cargo_transfer_fee):
        try:
            await trade_run_store.record_purchase(
                leg_id, quantity_scu, price_per_unit, cargo_transfer_type, cargo_transfer_fee
            )
        except Exception as exc:
            self.show_message(f"Couldn't record purchase — {exc}")
            return
        self._draft_purchase.pop(leg_id, None)
        await self.refresh()

    @asyncSlot(object, object, object, object, object)
    async def _on_record_sale(self, leg_id, quantity_scu, price_per_unit, cargo_transfer_type, cargo_transfer_fee):
        try:
            await trade_run_store.record_sale(
                leg_id, quantity_scu, price_per_unit, cargo_transfer_type, cargo_transfer_fee
            )
        except Exception as exc:
            self.show_message(f"Couldn't record sale — {exc}")
            return
        self._draft_purchase.pop(leg_id, None)
        await self.refresh()

    @asyncSlot(object)
    async def _on_finalize(self, run_id):
        try:
            await trade_run_store.finalize_run(run_id)
        except Exception as exc:
            self.show_message(f"Couldn't finalize run — {exc}")
            return
        await self.refresh()
        self.run_finalized.emit(run_id)

    @asyncSlot(object)
    async def _on_abandon(self, run_id):
        try:
            await trade_run_store.delete_run(run_id)
        except Exception as exc:
            self.show_message(f"Couldn't remove run — {exc}")
            return
        await self.refresh()


class TradeLedgerPanel(HudWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(theme.STYLESHEET)
        self._last_runs = []
        # Same override-map + live-default pattern as TradeRunsPanel's run/leg expand
        # state — only ever holds days the pilot manually collapsed; anything else
        # defaults to expanded.
        self._day_expanded = {}
        self._build_ui()

    def _build_ui(self):
        self.ledger_list, self._ledger_content_layout = _build_scroll_list(
            self, "▸ LEDGER", column_header=self._build_column_header_row()
        )
        # Always-on (not as-needed) so the scrollbar's width is reserved consistently —
        # otherwise rows narrow by LEDGER_SCROLLBAR_COLUMN_WIDTH only once there's enough
        # content to actually scroll, drifting out of alignment with the header, which
        # doesn't shrink at all since it isn't inside this scroll area.
        self.ledger_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)

    @staticmethod
    def _build_column_header_row():
        row = QFrame(objectName="resultsHeaderRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 2, 10, 6)
        layout.setSpacing(12)

        def header_label(text, alignment=Qt.AlignmentFlag.AlignLeft, width=None):
            label = QLabel(parent=row, text=text, objectName="resultsColumnHeader")
            label.setAlignment(alignment | Qt.AlignmentFlag.AlignVCenter)
            if width is not None:
                label.setFixedWidth(width)
            return label

        layout.addWidget(header_label("", width=LEDGER_CHEVRON_COLUMN_WIDTH), 0)
        layout.addWidget(header_label("TIME", width=LEDGER_TIME_COLUMN_WIDTH), 0)
        layout.addWidget(header_label("COMMODITY / ROUTE"), 1)
        layout.addWidget(header_label("VEHICLE", width=LEDGER_VEHICLE_COLUMN_WIDTH), 0)
        layout.addWidget(header_label("LENGTH", Qt.AlignmentFlag.AlignRight, LEDGER_LENGTH_COLUMN_WIDTH), 0)
        layout.addWidget(header_label("ACQUIRED", Qt.AlignmentFlag.AlignRight, LEDGER_SCU_COLUMN_WIDTH), 0)
        layout.addWidget(header_label("SOLD", Qt.AlignmentFlag.AlignRight, LEDGER_SCU_COLUMN_WIDTH), 0)
        layout.addWidget(header_label("PROFIT", Qt.AlignmentFlag.AlignRight, LEDGER_PROFIT_COLUMN_WIDTH), 0)
        layout.addWidget(header_label("", width=LEDGER_SCROLLBAR_COLUMN_WIDTH), 0)

        return row

    @asyncSlot()
    async def refresh(self):
        try:
            runs = await trade_run_store.get_finalized_runs()
        except Exception as exc:
            self.show_message(f"Couldn't load the ledger — {exc}")
            return
        self.render_runs(runs)

    @Slot(str)
    def show_message(self, message):
        _show_message_row(self._ledger_content_layout, message)

    def render_runs(self, runs):
        self._last_runs = runs
        if not runs:
            _show_message_row(
                self._ledger_content_layout,
                "No finalized trade runs yet.",
                "Runs land here once every leg is finalized.",
            )
            return

        _clear_rows(self._ledger_content_layout)
        for day, day_runs in _group_runs_by_day(runs):
            expanded = self._day_expanded.get(day, True)
            group = self._build_day_group(day, day_runs, expanded)
            self._ledger_content_layout.insertWidget(self._ledger_content_layout.count() - 1, group)

    def row_count(self):
        return len(_content_widgets(self._ledger_content_layout))

    def row_text(self, index=0):
        return _row_text(self._ledger_content_layout, index)

    def run_count(self):
        """Number of actual run rows currently rendered (as opposed to day-group/total
        chrome) — what "how many runs are showing" tests actually mean, independent of
        how many day groups they happen to fall into. Deliberately scoped to widgets
        reachable from _ledger_content_layout's current items, not a global
        findChildren() scan — a just-replaced day group is detached from the layout
        (via takeAt()) but stays a QObject child until its deferred deleteLater() runs,
        so a global scan would double-count it alongside its replacement."""
        count = 0
        for index in range(self._ledger_content_layout.count()):
            day_group = self._ledger_content_layout.itemAt(index).widget()
            if day_group is not None:
                count += len(day_group.findChildren(QFrame, "ledgerRunRow"))
        return count

    def _build_day_group(self, day, day_runs, expanded):
        container = QFrame(objectName="ledgerDayGroup")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_day_header(day, expanded))

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        for run in day_runs:
            body_layout.addWidget(self._build_ledger_row(run))
        body_layout.addWidget(self._build_day_total_row(day_runs))

        # Reparent before setVisible() — see the note in TradeRunsPanel._build_run_card.
        layout.addWidget(body)
        body.setVisible(expanded)

        return container

    def _build_day_header(self, day, expanded):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 6, 0, 4)
        layout.setSpacing(10)

        chevron = QPushButton(parent=row, text="▾" if expanded else "▸", objectName="chevronButton")
        chevron.clicked.connect(lambda checked=False, d=day, cur=expanded: self._toggle_day(d, cur))
        layout.addWidget(chevron)

        layout.addWidget(QLabel(parent=row, text=day.strftime("%A, %B %d, %Y"), objectName="ledgerDayTitle"))
        layout.addWidget(QLabel(parent=row, text=_format_day_age(day), objectName="runAge"))
        layout.addStretch()

        return row

    def _toggle_day(self, day, currently_expanded):
        self._day_expanded[day] = not currently_expanded
        self.render_runs(self._last_runs)

    @staticmethod
    def _build_ledger_row(run):
        row = QFrame(objectName="ledgerRunRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(12)

        spacer = QLabel(parent=row, text="")
        spacer.setFixedWidth(LEDGER_CHEVRON_COLUMN_WIDTH)
        layout.addWidget(spacer, 0)

        time_label = QLabel(parent=row, text=run.finalized_at.strftime("%H:%M"), objectName="ledgerRoute")
        time_label.setFixedWidth(LEDGER_TIME_COLUMN_WIDTH)
        layout.addWidget(time_label, 0)

        acquisition = next((leg for leg in run.legs if leg.leg_type == LegType.ACQUISITION), None)
        sale = next((leg for leg in run.legs if leg.leg_type == LegType.SALE), None)
        commodity_name = (acquisition or sale).commodity_name if (acquisition or sale) else "—"
        origin_name = acquisition.terminal_name if acquisition else "—"
        destination_name = sale.terminal_name if sale else "—"

        description = QWidget()
        description_layout = QHBoxLayout(description)
        description_layout.setContentsMargins(0, 0, 0, 0)
        description_layout.setSpacing(8)
        commodity_code = uex_lookup.commodity_code_for_name(commodity_name)
        badge = QLabel(
            parent=description, text=f"\N{PACKAGE} {commodity_code}", objectName="ledgerCommodityBadge",
        )
        badge.setToolTip(commodity_name)
        description_layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        route_label = QLabel(
            parent=description, text=f"from {origin_name} to {destination_name}", objectName="ledgerRoute",
        )
        route_label.setWordWrap(True)
        description_layout.addWidget(route_label, 1)
        layout.addWidget(description, 1)

        vehicle_label = QLabel(parent=row, text=run.ship or "—", objectName="ledgerRoute")
        vehicle_label.setFixedWidth(LEDGER_VEHICLE_COLUMN_WIDTH)
        vehicle_label.setWordWrap(True)
        layout.addWidget(vehicle_label, 0)

        length_label = QLabel(
            parent=row, text=_format_duration(trade_run_store.run_duration(run)), objectName="ledgerRoute",
        )
        length_label.setFixedWidth(LEDGER_LENGTH_COLUMN_WIDTH)
        length_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(length_label, 0)

        acquired_label = QLabel(
            parent=row, text=f"{trade_run_store.run_acquired_scu(run):,} SCU", objectName="ledgerRoute",
        )
        acquired_label.setFixedWidth(LEDGER_SCU_COLUMN_WIDTH)
        acquired_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(acquired_label, 0)

        sold_label = QLabel(
            parent=row, text=f"{trade_run_store.run_sold_scu(run):,} SCU", objectName="ledgerRoute",
        )
        sold_label.setFixedWidth(LEDGER_SCU_COLUMN_WIDTH)
        sold_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(sold_label, 0)

        profit = trade_run_store.run_profit(run)
        profit_label = QLabel(parent=row, text=f"{profit:+,} aUEC", objectName="ledgerProfit")
        profit_label.setFixedWidth(LEDGER_PROFIT_COLUMN_WIDTH)
        profit_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        profit_label.setProperty("profitable", profit >= 0)
        profit_label.style().unpolish(profit_label)
        profit_label.style().polish(profit_label)
        layout.addWidget(profit_label, 0)

        return row

    @staticmethod
    def _build_day_total_row(day_runs):
        row = QFrame(objectName="ledgerDayTotalRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(12)

        spacer = QLabel(parent=row, text="")
        spacer.setFixedWidth(LEDGER_CHEVRON_COLUMN_WIDTH + LEDGER_TIME_COLUMN_WIDTH)
        layout.addWidget(spacer, 0)

        commodity_count = len({leg.commodity_name for run in day_runs for leg in run.legs})
        location_count = len({leg.terminal_name for run in day_runs for leg in run.legs})
        description = QWidget()
        description_layout = QVBoxLayout(description)
        description_layout.setContentsMargins(0, 0, 0, 0)
        description_layout.setSpacing(1)
        description_layout.addWidget(QLabel(
            parent=description, objectName="ledgerDayTotalLabel",
            text=f"{commodity_count} commodit{'y' if commodity_count == 1 else 'ies'}",
        ))
        description_layout.addWidget(QLabel(
            parent=description, objectName="ledgerDayTotalLabel",
            text=f"{location_count} location{'' if location_count == 1 else 's'}",
        ))
        layout.addWidget(description, 1)

        vehicle_count = len({run.ship for run in day_runs if run.ship})
        vehicle_label = QLabel(
            parent=row, objectName="ledgerDayTotalLabel",
            text=f"{vehicle_count} vehicle{'' if vehicle_count == 1 else 's'}",
        )
        vehicle_label.setFixedWidth(LEDGER_VEHICLE_COLUMN_WIDTH)
        layout.addWidget(vehicle_label, 0)

        total_duration = sum((trade_run_store.run_duration(run) for run in day_runs), timedelta())
        length_label = QLabel(parent=row, text=_format_duration(total_duration), objectName="ledgerDayTotalLabel")
        length_label.setFixedWidth(LEDGER_LENGTH_COLUMN_WIDTH)
        length_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(length_label, 0)

        total_acquired = sum(trade_run_store.run_acquired_scu(run) for run in day_runs)
        acquired_label = QLabel(
            parent=row, text=f"{total_acquired:,} SCU", objectName="ledgerDayTotalLabel",
        )
        acquired_label.setFixedWidth(LEDGER_SCU_COLUMN_WIDTH)
        acquired_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(acquired_label, 0)

        total_sold = sum(trade_run_store.run_sold_scu(run) for run in day_runs)
        sold_label = QLabel(parent=row, text=f"{total_sold:,} SCU", objectName="ledgerDayTotalLabel")
        sold_label.setFixedWidth(LEDGER_SCU_COLUMN_WIDTH)
        sold_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(sold_label, 0)

        total_profit = sum(trade_run_store.run_profit(run) for run in day_runs)
        profit_label = QLabel(parent=row, text=f"{total_profit:+,} aUEC", objectName="ledgerProfit")
        profit_label.setFixedWidth(LEDGER_PROFIT_COLUMN_WIDTH)
        profit_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        profit_label.setProperty("profitable", total_profit >= 0)
        profit_label.style().unpolish(profit_label)
        profit_label.style().polish(profit_label)
        layout.addWidget(profit_label, 0)

        return row

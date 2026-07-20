import asyncio

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from qasync import asyncSlot

from overlay import theme
from overlay.theme import HudWindow
from overlay.uex_lookup import commodity_code_for, commodity_volatility, route_breadcrumb

# CV = volatility_price / price, the docs' "price coefficient of variation" — a raw
# volatility_price_* value is a currency stddev, meaningless without dividing by price.
# Thresholds are from real sampling across 210 live commodity/terminal price rows
# (p25=1.7%, median=3.6%, p75=7.0%, p90=11.2%), not guessed.
VOLATILITY_STABLE_MAX = 0.03
VOLATILITY_MODERATE_MAX = 0.08

# Shared column widths so the header row and every item row line up — QListWidget
# item widgets and the header live in separate layouts, so alignment only holds if
# both sides fix the same non-stretching columns to the same pixel widths.
SCU_COLUMN_WIDTH = 64
ARROW_COLUMN_WIDTH = 48
ACTION_COLUMN_WIDTH = 24


class VolatilityIcon(QWidget):
    """A small sparkline-style glyph — shape communicates volatility bucket at a glance
    (flat/stable vs. jagged/volatile), color confirms it, exact % lives in the tooltip.
    Fixed per-bucket shapes (not computed from the live cv value) so it stays legible at
    this size rather than becoming a precise-but-illegible micro-chart.
    """

    def __init__(self, cv, color, parent=None):
        super().__init__(parent)
        self._cv = cv
        self._color = color
        self.setFixedSize(24, 14)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(self._color))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        w, h, mid = self.width(), self.height(), self.height() / 2
        if self._cv is None:
            points = [(0, mid), (w, mid)]
        elif self._cv < VOLATILITY_STABLE_MAX:
            points = [(0, mid), (w * 0.3, mid), (w * 0.42, mid - 1.2), (w * 0.58, mid + 1.5), (w, mid)]
        elif self._cv < VOLATILITY_MODERATE_MAX:
            points = [
                (0, mid), (w * 0.22, mid - h * 0.4), (w * 0.5, mid + h * 0.42),
                (w * 0.78, mid - h * 0.2), (w, mid),
            ]
        else:
            points = [
                (0, mid), (w * 0.12, mid), (w * 0.2, mid - h * 0.45), (w * 0.29, mid + h * 0.48),
                (w * 0.38, mid - h * 0.15), (w * 0.47, mid + h * 0.42), (w * 0.56, mid - h * 0.2),
                (w * 0.65, mid + h * 0.32), (w * 0.74, mid - h * 0.1), (w, mid),
            ]

        path = QPainterPath()
        path.moveTo(*points[0])
        for point in points[1:]:
            path.lineTo(*point)
        painter.drawPath(path)
        painter.end()


class SortToggle(QWidget):
    """Profit/Margin is a real binary choice (no meaningful "neither" state once Score
    isn't a hidden default anymore), so a two-position switch fits better than a pair of
    exclusive chip buttons. Unchecked = Profit, checked = Margin."""

    toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._checked = False
        self.setFixedSize(36, 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def isChecked(self):
        return self._checked

    def setChecked(self, checked):
        if checked != self._checked:
            self._checked = checked
            self.update()

    def mousePressEvent(self, event):
        self.setChecked(not self._checked)
        self.toggled.emit(self._checked)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        width, height = self.width(), self.height()
        painter.setBrush(QColor(theme.ACCENT if self._checked else theme.BORDER))
        painter.drawRoundedRect(0, 0, width, height, height / 2, height / 2)

        knob_diameter = height - 4
        knob_x = width - knob_diameter - 2 if self._checked else 2
        painter.setBrush(QColor(theme.TEXT_PRIMARY))
        painter.drawEllipse(knob_x, 2, knob_diameter, knob_diameter)
        painter.end()


class ResultsPanel(HudWindow):
    add_route_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(theme.STYLESHEET)

        self.last_routes = []
        self.cargo_scu = 0
        self._volatility_by_commodity = {}
        # Which sort is currently applied — re-render after the async volatility fetch
        # resolves needs to respect whatever the user picked.
        self._active_sort_key = self.estimated_profit_for

        self._build_ui()
        self._wire_signals()

    def _build_ui(self):
        self._main_layout = QVBoxLayout(self)

        results_header = QLabel(parent=self, text="▸ RESULTS", objectName="panelHeader")
        self._main_layout.addWidget(results_header)

        sort_row = QHBoxLayout()
        self.sort_profit_label = QLabel(parent=self, text="PROFIT", objectName="sortToggleLabel")
        self.sort_toggle = SortToggle(parent=self)
        self.sort_margin_label = QLabel(parent=self, text="MARGIN", objectName="sortToggleLabel")
        sort_row.addWidget(self.sort_profit_label)
        sort_row.addWidget(self.sort_toggle)
        sort_row.addWidget(self.sort_margin_label)
        sort_row.addStretch()
        self._update_sort_labels(margin_active=False)

        self._main_layout.addLayout(sort_row)

        self._main_layout.addWidget(self._build_header_row())

        self.results_list = QListWidget(parent=self)
        # Vertical scrolling is expected here (result counts vary) — horizontal never
        # is; disabled outright rather than trusting the default "as needed" policy.
        self.results_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._main_layout.addWidget(self.results_list)

    def _wire_signals(self):
        self.sort_toggle.toggled.connect(self._on_sort_toggled)

    def _on_sort_toggled(self, margin_active):
        self._update_sort_labels(margin_active)
        if margin_active:
            self.sort_by_margin()
        else:
            self.sort_by_profit()

    def _update_sort_labels(self, margin_active):
        self.sort_profit_label.setProperty("active", not margin_active)
        self.sort_margin_label.setProperty("active", margin_active)
        for label in (self.sort_profit_label, self.sort_margin_label):
            label.style().unpolish(label)
            label.style().polish(label)

    @asyncSlot(list, int)
    async def set_routes(self, routes, cargo_scu):
        self.last_routes = routes
        self.cargo_scu = cargo_scu
        # Renders immediately with whatever volatility is already cached (gray "Unknown"
        # dots for anything not yet fetched) rather than making the user wait on a
        # network round trip before seeing results at all. Keeps whichever sort
        # (Profit/Margin) is already selected instead of resetting it every search.
        if self.sort_toggle.isChecked():
            self.sort_by_margin()
        else:
            self.sort_by_profit()

        if await self._fetch_missing_volatility(routes):
            self.render_routes(sorted(self.last_routes, key=self._active_sort_key, reverse=True))

    @Slot(str)
    def show_message(self, message):
        # Doesn't touch last_routes/cargo_scu — a rejected search (e.g. no criteria
        # entered) shouldn't discard whatever the previous successful search found.
        self.results_list.clear()
        self.results_list.addItem(message)

    def estimated_profit_for(self, route):
        reachable_scu = min(route.scu_origin, route.scu_destination)
        if self.cargo_scu > 0:
            reachable_scu = min(reachable_scu, self.cargo_scu)
        return (route.price_destination - route.price_origin) * reachable_scu

    def reachable_scu_for(self, route):
        reachable_scu = min(route.scu_origin, route.scu_destination)
        if self.cargo_scu > 0:
            reachable_scu = min(reachable_scu, self.cargo_scu)
        return reachable_scu

    async def _fetch_missing_volatility(self, routes):
        commodity_ids = {route.commodity_id for route in routes}
        missing = [cid for cid in commodity_ids if cid not in self._volatility_by_commodity]
        if not missing:
            return False

        results = await asyncio.gather(*(commodity_volatility(cid) for cid in missing))
        for commodity_id, result in zip(missing, results, strict=True):
            self._volatility_by_commodity[commodity_id] = result
        return True

    def _volatility_cv_for(self, route, side):
        by_terminal = self._volatility_by_commodity.get(route.commodity_id, {})
        if side == "buy":
            entry = by_terminal.get(route.origin_terminal_id)
            return entry.get("buy_cv") if entry else None
        entry = by_terminal.get(route.destination_terminal_id)
        return entry.get("sell_cv") if entry else None

    @staticmethod
    def _volatility_color_and_label(cv):
        if cv is None:
            return theme.TEXT_DISABLED, "Unknown"
        if cv < VOLATILITY_STABLE_MAX:
            return theme.SUCCESS, "Stable"
        if cv < VOLATILITY_MODERATE_MAX:
            return theme.WARNING, "Moderate"
        return theme.ERROR, "Volatile"

    def _build_terminal_block(self, breadcrumb, side, price_per_scu, cv):
        block = QWidget()
        layout = QHBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        color, label = self._volatility_color_and_label(cv)
        icon = VolatilityIcon(cv, color, parent=block)
        tooltip = f"{label} volatility" if cv is None else f"{label} volatility ({cv * 100:.1f}%)"
        icon.setToolTip(tooltip)
        layout.addWidget(icon)

        text = QWidget()
        text_layout = QVBoxLayout(text)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        # System/Planet/Terminal, e.g. "ST/Crusader/Orison TDD" — system abbreviated to
        # its UEX code, planet and terminal left full (matches how the game names them).
        text_layout.addWidget(QLabel(parent=text, text=breadcrumb, objectName="routeTerminalName"))
        text_layout.addWidget(
            QLabel(parent=text, text=f"{side} {price_per_scu:,.0f} aUEC", objectName="routeTerminalPrice")
        )
        layout.addWidget(text)

        return block

    def _build_profit_block(self, route):
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        profit_label = QLabel(
            parent=block, text=f"+{self.estimated_profit_for(route):,.0f} aUEC", objectName="routeProfit",
        )
        profit_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        margin_label = QLabel(parent=block, text=f"({route.price_margin:.1f}%)", objectName="routeMargin")
        margin_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(profit_label)
        layout.addWidget(margin_label)

        return block

    @staticmethod
    def _build_route_connector(route):
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        arrow_label = QLabel(parent=block, text="→", objectName="routeArrow")
        arrow_label.setAlignment(Qt.AlignCenter)
        distance_label = QLabel(parent=block, text=f"{route.distance:.0f} Gm", objectName="routeDistance")
        distance_label.setAlignment(Qt.AlignCenter)

        layout.addWidget(arrow_label)
        layout.addWidget(distance_label)
        return block

    def _build_route_row(self, route):
        row = QFrame(objectName="routeRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 9, 10, 9)
        layout.setSpacing(12)

        commodity_code = commodity_code_for(route.commodity_id, route.commodity_name)
        commodity_label = QLabel(parent=row, text=f"\N{PACKAGE} {commodity_code}", objectName="routeCommodity")
        commodity_label.setToolTip(route.commodity_name)
        layout.addWidget(commodity_label, 1)

        scu_label = QLabel(parent=row, text=f"{self.reachable_scu_for(route):.0f} SCU", objectName="routeScu")
        scu_label.setFixedWidth(SCU_COLUMN_WIDTH)
        scu_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(scu_label, 0)

        origin_breadcrumb = route_breadcrumb(
            route.origin_star_system_name, route.origin_planet_name,
            route.origin_terminal_id, route.origin_terminal_name,
        )
        layout.addWidget(self._build_terminal_block(
            origin_breadcrumb, "buy", route.price_origin, self._volatility_cv_for(route, "buy"),
        ), 3)

        connector = self._build_route_connector(route)
        connector.setFixedWidth(ARROW_COLUMN_WIDTH)
        layout.addWidget(connector, 0)

        destination_breadcrumb = route_breadcrumb(
            route.destination_star_system_name, route.destination_planet_name,
            route.destination_terminal_id, route.destination_terminal_name,
        )
        layout.addWidget(self._build_terminal_block(
            destination_breadcrumb, "sell", route.price_destination, self._volatility_cv_for(route, "sell"),
        ), 3)

        layout.addWidget(self._build_profit_block(route), 2)

        add_button = QPushButton(parent=row, text="+", objectName="addRouteButton")
        add_button.setFixedSize(ACTION_COLUMN_WIDTH, ACTION_COLUMN_WIDTH)
        add_button.setToolTip("Add to Active Routes")
        add_button.clicked.connect(lambda checked=False, r=route: self.add_route_requested.emit(r))
        layout.addWidget(add_button, 0)

        return row

    def _build_header_row(self):
        row = QFrame(objectName="resultsHeaderRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 2, 10, 6)
        layout.setSpacing(12)

        def header_label(text, alignment=Qt.AlignLeft, width=None):
            label = QLabel(parent=row, text=text, objectName="resultsColumnHeader")
            label.setAlignment(alignment | Qt.AlignVCenter)
            if width is not None:
                label.setFixedWidth(width)
            return label

        layout.addWidget(header_label("COMMODITY"), 1)
        layout.addWidget(header_label("SCU", Qt.AlignRight, SCU_COLUMN_WIDTH), 0)
        layout.addWidget(header_label("ORIGIN — BUY"), 3)
        layout.addWidget(header_label("", width=ARROW_COLUMN_WIDTH), 0)
        layout.addWidget(header_label("DESTINATION — SELL"), 3)
        layout.addWidget(header_label("PROFIT / MARGIN", Qt.AlignRight), 2)
        layout.addWidget(header_label("", width=ACTION_COLUMN_WIDTH), 0)

        return row

    def render_routes(self, routes):
        self.results_list.clear()
        if not routes:
            self.results_list.addItem("No routes found for these filters.")
            return

        for route in routes[:20]:
            row_widget = self._build_route_row(route)
            item = QListWidgetItem(self.results_list)
            item.setSizeHint(row_widget.sizeHint())
            self.results_list.setItemWidget(item, row_widget)

    def sort_by_profit(self):
        self._active_sort_key = self.estimated_profit_for
        self.render_routes(sorted(self.last_routes, key=self._active_sort_key, reverse=True))

    def sort_by_margin(self):
        self._active_sort_key = lambda route: route.price_margin
        self.render_routes(sorted(self.last_routes, key=self._active_sort_key, reverse=True))

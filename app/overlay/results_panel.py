from PySide6.QtCore import Slot
from PySide6.QtWidgets import QButtonGroup, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget

from overlay import theme
from overlay.theme import HudWindow


class ResultsPanel(HudWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(theme.STYLESHEET)

        self.last_routes = []
        self.cargo_scu = 0

        self._build_ui()
        self._wire_signals()

    def _build_ui(self):
        self._main_layout = QVBoxLayout(self)

        results_header = QLabel(parent=self, text="▸ RESULTS", objectName="panelHeader")
        self._main_layout.addWidget(results_header)

        sort_row = QHBoxLayout()
        # Score is the default sort (applied whenever new routes come in) and isn't
        # exposed as its own chip — Profit/Margin are the two explicit alternatives.
        self.sort_profit_button = QPushButton(parent=self, text="PROFIT", objectName="sortChip", checkable=True)
        self.sort_margin_button = QPushButton(parent=self, text="MARGIN", objectName="sortChip", checkable=True)
        sort_row.addWidget(self.sort_profit_button)
        sort_row.addWidget(self.sort_margin_button)
        sort_row.addStretch()

        self._sort_chip_group = QButtonGroup(self)
        self._sort_chip_group.setExclusive(True)
        self._sort_chip_group.addButton(self.sort_profit_button)
        self._sort_chip_group.addButton(self.sort_margin_button)

        self._main_layout.addLayout(sort_row)

        self.results_list = QListWidget(parent=self)
        self._main_layout.addWidget(self.results_list)

    def _wire_signals(self):
        self.sort_profit_button.clicked.connect(self.sort_by_profit)
        self.sort_margin_button.clicked.connect(self.sort_by_margin)

    @Slot(list, int)
    def set_routes(self, routes, cargo_scu):
        self.last_routes = routes
        self.cargo_scu = cargo_scu
        self._sort_chip_group.setExclusive(False)
        self.sort_profit_button.setChecked(False)
        self.sort_margin_button.setChecked(False)
        self._sort_chip_group.setExclusive(True)
        self.sort_by_score()

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

    def render_routes(self, routes):
        self.results_list.clear()
        if not routes:
            self.results_list.addItem("No routes found for these filters.")
            return

        for route in routes[:20]:
            reachable_scu = min(route.scu_origin, route.scu_destination)
            if self.cargo_scu > 0:
                reachable_scu = min(reachable_scu, self.cargo_scu)

            line = (
                f"{route.commodity_name}: {route.origin_terminal_name} -> {route.destination_terminal_name}"
                f"  |  {route.distance:.0f} Gm  |  {route.price_margin:.1f}% margin"
                f"  |  {reachable_scu:.0f} SCU  |  +{self.estimated_profit_for(route):.0f} aUEC"
            )
            self.results_list.addItem(line)

    def sort_by_score(self):
        self.render_routes(sorted(self.last_routes, key=lambda route: route.score, reverse=True))

    def sort_by_profit(self):
        self.render_routes(sorted(self.last_routes, key=self.estimated_profit_for, reverse=True))

    def sort_by_margin(self):
        self.render_routes(sorted(self.last_routes, key=lambda route: route.price_margin, reverse=True))

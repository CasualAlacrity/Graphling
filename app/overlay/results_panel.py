from PySide6.QtCore import Slot
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget


class ResultsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.last_routes = []
        self.cargo_scu = 0

        self._build_ui()
        self._wire_signals()

    def _build_ui(self):
        self._main_layout = QVBoxLayout(self)

        results_header = QLabel(parent=self, text="Results:")
        self._main_layout.addWidget(results_header)

        sort_row = QHBoxLayout()
        self.sort_score_button = QPushButton(parent=self, text="Sort: Score")
        self.sort_profit_button = QPushButton(parent=self, text="Sort: Profit")
        self.sort_margin_button = QPushButton(parent=self, text="Sort: Margin")
        sort_row.addWidget(self.sort_score_button)
        sort_row.addWidget(self.sort_profit_button)
        sort_row.addWidget(self.sort_margin_button)

        self._main_layout.addLayout(sort_row)

        self.results_list = QListWidget(parent=self)
        self._main_layout.addWidget(self.results_list)

    def _wire_signals(self):
        self.sort_score_button.clicked.connect(self.sort_by_score)
        self.sort_profit_button.clicked.connect(self.sort_by_profit)
        self.sort_margin_button.clicked.connect(self.sort_by_margin)

    @Slot(list, int)
    def set_routes(self, routes, cargo_scu):
        self.last_routes = routes
        self.cargo_scu = cargo_scu
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

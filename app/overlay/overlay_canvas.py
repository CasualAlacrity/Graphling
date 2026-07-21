from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget
from qasync import asyncSlot

from db import trade_run_store
from overlay import theme
from overlay.theme import HudWindow


class OverlayCanvas(HudWindow):
    """The single root window — the F3 hotkey shows/hides this, and everything else
    (Route Search, In Progress, Ledger) is parented under it as a tab page, rather than
    each being its own independently-toggled top-level window."""

    def __init__(self, filter_panel, results_panel, trade_runs_panel, trade_ledger_panel, parent=None):
        super().__init__(parent)
        self.setStyleSheet(theme.STYLESHEET)

        self.filter_panel = filter_panel
        self.results_panel = results_panel
        self.trade_runs_panel = trade_runs_panel
        self.trade_ledger_panel = trade_ledger_panel

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)

        self.tabs = QTabWidget(parent=self)
        layout.addWidget(self.tabs)

        route_search_tab = QWidget()
        route_search_layout = QVBoxLayout(route_search_tab)
        route_search_layout.setContentsMargins(0, 6, 0, 0)
        route_search_layout.setSpacing(10)
        route_search_layout.addWidget(filter_panel)
        route_search_layout.addWidget(results_panel, 1)

        self.tabs.addTab(route_search_tab, "Route Search")
        self.tabs.addTab(trade_runs_panel, "In Progress")
        self.tabs.addTab(trade_ledger_panel, "Ledger")

        results_panel.add_route_requested.connect(self._on_add_route_requested)
        trade_runs_panel.run_finalized.connect(self._on_run_finalized)
        trade_runs_panel.runs_changed.connect(self._on_runs_changed)
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _on_runs_changed(self, count):
        index = self.tabs.indexOf(self.trade_runs_panel)
        self.tabs.setTabText(index, f"In Progress ({count})" if count else "In Progress")

    @asyncSlot(object)
    async def _on_add_route_requested(self, route):
        quantity = self.results_panel.reachable_scu_for(route)
        ship = self.filter_panel.ship_input.text() or None

        # Switch tabs first, with signals blocked — otherwise setCurrentWidget's own
        # currentChanged -> _on_tab_changed -> refresh() would race the explicit
        # show_message/refresh below and could silently overwrite it.
        self.tabs.blockSignals(True)
        self.tabs.setCurrentWidget(self.trade_runs_panel)
        self.tabs.blockSignals(False)

        try:
            await trade_run_store.create_run_from_route(route, int(round(quantity)), ship)
        except Exception as exc:
            self.trade_runs_panel.show_message(f"Couldn't create run — {exc}")
        else:
            await self.trade_runs_panel.refresh()

    @asyncSlot(object)
    async def _on_run_finalized(self, run_id):
        self.tabs.blockSignals(True)
        self.tabs.setCurrentWidget(self.trade_ledger_panel)
        self.tabs.blockSignals(False)
        await self.trade_ledger_panel.refresh()

    @asyncSlot(int)
    async def _on_tab_changed(self, index):
        widget = self.tabs.widget(index)
        if widget is self.trade_runs_panel:
            await self.trade_runs_panel.refresh()
        elif widget is self.trade_ledger_panel:
            await self.trade_ledger_panel.refresh()

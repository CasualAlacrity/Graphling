import asyncio
import threading

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import QApplication
from pynput import keyboard

from overlay.filter_panel import FilterPanel
from overlay.results_panel import ResultsPanel
from voice import run as voice_run


class HotkeyBridge(QObject):
    toggle_requested = Signal()


bridge = HotkeyBridge()
hotkeys = keyboard.GlobalHotKeys({'<f3>': lambda: bridge.toggle_requested.emit()})

app = QApplication()

screen_geometry = app.primaryScreen().availableGeometry()
panel_height = int(screen_geometry.height() * 0.85)
filter_width = int(screen_geometry.width() * 0.22)
results_width = int(screen_geometry.width() * 0.30)

filter_panel = FilterPanel()
filter_panel.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
# Fixed, not just an initial resize() — otherwise a child widget whose content grows
# (e.g. a breadcrumb label picking up a long terminal name) drags the window along
# with it, and the two side-by-side windows drift out of alignment.
filter_panel.setFixedSize(filter_width, panel_height)
filter_panel.move(screen_geometry.x(), screen_geometry.y())
filter_panel.setStyleSheet("background-color: #AAAAAA")

results_panel = ResultsPanel()
results_panel.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
results_panel.setFixedSize(results_width, panel_height)
results_panel.move(screen_geometry.x() + filter_width + 10, screen_geometry.y())
results_panel.setStyleSheet("background-color: #AAAAAA")

filter_panel.routes_found.connect(results_panel.set_routes)
filter_panel.search_rejected.connect(results_panel.show_message)

threading.Thread(target=lambda: asyncio.run(voice_run()), daemon=True).start()


def on_toggle_requested():
    visible = not filter_panel.isVisible()
    filter_panel.setVisible(visible)
    results_panel.setVisible(visible)


bridge.toggle_requested.connect(on_toggle_requested)

hotkeys.start()
app.exec()

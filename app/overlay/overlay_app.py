import asyncio
import os
import threading

import qasync
from pynput import keyboard
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication

from overlay import theme, uex_lookup


def main():
    print("Loading reference data from UEX...", flush=True)
    uex_lookup.init()
    print("Reference data loaded. Building overlay windows...", flush=True)

    # Imported after uex_lookup.init() rather than at module top — both panels read
    # uex_lookup's lookup tables (ship_names, commodity_names, ...) at construction
    # time, so they need init() to have already populated them.
    from overlay.filter_panel import FilterPanel
    from overlay.results_panel import ResultsPanel

    class HotkeyBridge(QObject):
        toggle_requested = Signal()

    bridge = HotkeyBridge()
    hotkeys = keyboard.GlobalHotKeys({'<f3>': lambda: bridge.toggle_requested.emit()})

    app = QApplication()
    # Native macOS ("Aqua") widget rendering doesn't fully respect QSS box-model
    # overrides (border/padding) on things like QLineEdit — Fusion is Qt's own
    # cross-platform style, drawn entirely by Qt, so stylesheets apply predictably
    # instead of fighting native chrome.
    app.setStyle("Fusion")
    theme.load_fonts()

    # Ties an asyncio event loop to Qt's own, so the filter/search coroutines
    # (previously asyncio.run() per call — a whole loop spun up and torn down
    # synchronously on the GUI thread, freezing the window for the call's duration)
    # can now run as real, non-blocking tasks instead. Must be set before any
    # @asyncSlot-decorated method can be invoked.
    event_loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(event_loop)

    screen_geometry = app.primaryScreen().availableGeometry()
    panel_width = int(screen_geometry.width() * 0.6)
    top_margin = 48
    bottom_margin = 32
    gap_between_panels = 14

    filter_panel = FilterPanel()
    filter_panel.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
    # Fixed, not just an initial resize() — otherwise a child widget whose content
    # grows (e.g. a breadcrumb label picking up a long terminal name) drags the
    # window without the results panel following, drifting the two out of alignment.
    # Width is set first so the height computed from it (via the now-horizontal
    # field rows) is accurate.
    filter_panel.setFixedWidth(panel_width)
    filter_panel.adjustSize()
    filter_panel.setFixedHeight(filter_panel.height())

    panel_x = screen_geometry.x() + (screen_geometry.width() - panel_width) // 2
    filter_panel.move(panel_x, screen_geometry.y() + top_margin)

    # Filter is now a wide, short bar near the top rather than a tall sidebar, so
    # Results sits below it (same width) filling the rest of the screen, instead of
    # beside it.
    results_panel = ResultsPanel()
    results_panel.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
    results_top = filter_panel.y() + filter_panel.height() + gap_between_panels
    results_height = screen_geometry.y() + screen_geometry.height() - results_top - bottom_margin
    results_panel.setFixedSize(panel_width, results_height)
    results_panel.move(panel_x, results_top)

    filter_panel.routes_found.connect(results_panel.set_routes)
    filter_panel.search_rejected.connect(results_panel.show_message)

    # Voice pulls in the full LangGraph/LLM/TTS stack (graph.py -> llm.py, ElevenLabs,
    # Whisper), which the trade overlay itself doesn't need. On by default to match
    # the existing workflow; set UPLINK_VOICE=0 to run the overlay standalone without
    # that stack configured.
    if os.getenv("UPLINK_VOICE", "1") != "0":
        print("Starting voice module...", flush=True)
        from voice import run as voice_run
        threading.Thread(target=lambda: asyncio.run(voice_run()), daemon=True).start()

    def on_toggle_requested():
        visible = not filter_panel.isVisible()
        filter_panel.setVisible(visible)
        results_panel.setVisible(visible)

    bridge.toggle_requested.connect(on_toggle_requested)

    hotkeys.start()
    print("Ready. Press F3 to toggle the overlay.", flush=True)
    with event_loop:
        event_loop.run_forever()


if __name__ == "__main__":
    main()

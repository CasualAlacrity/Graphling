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

    # Imported after uex_lookup.init() rather than at module top — panels read
    # uex_lookup's lookup tables (ship_names, commodity_names, ...) at construction
    # time, so they need init() to have already populated them.
    from overlay.filter_panel import FilterPanel
    from overlay.overlay_canvas import OverlayCanvas
    from overlay.results_panel import ResultsPanel
    from overlay.trade_runs_panel import TradeLedgerPanel, TradeRunsPanel

    class HotkeyBridge(QObject):
        toggle_requested = Signal()

    bridge = HotkeyBridge()
    overlay_hotkey = os.getenv("OVERLAY_HOTKEY", "<f3>")
    hotkeys = keyboard.GlobalHotKeys({overlay_hotkey: lambda: bridge.toggle_requested.emit()})

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

    # filter_panel/results_panel/trade_runs_panel/trade_ledger_panel are built with no
    # window flags at all — they're plain child widgets now, parented under the
    # canvas's tabs by OverlayCanvas itself. All geometry (size, position, frameless/
    # always-on-top) lives on the canvas, the one actual top-level window; each panel's
    # own width/height is just whatever its tab's layout gives it.
    filter_panel = FilterPanel()
    results_panel = ResultsPanel()
    trade_runs_panel = TradeRunsPanel()
    trade_ledger_panel = TradeLedgerPanel()

    filter_panel.routes_found.connect(results_panel.set_routes)
    filter_panel.search_rejected.connect(results_panel.show_message)

    canvas = OverlayCanvas(filter_panel, results_panel, trade_runs_panel, trade_ledger_panel)
    canvas.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)

    screen_geometry = app.primaryScreen().availableGeometry()
    panel_width = int(screen_geometry.width() * 0.6)
    top_margin = 48
    bottom_margin = 32
    canvas_height = screen_geometry.height() - top_margin - bottom_margin
    canvas.setFixedSize(panel_width, canvas_height)
    canvas_x = screen_geometry.x() + (screen_geometry.width() - panel_width) // 2
    canvas.move(canvas_x, screen_geometry.y() + top_margin)

    # Voice pulls in the full LangGraph/LLM/TTS stack (graph.py -> llm.py, ElevenLabs,
    # Whisper), which the trade overlay itself doesn't need. On by default to match
    # the existing workflow; set UPLINK_VOICE=0 to run the overlay standalone without
    # that stack configured.
    if os.getenv("UPLINK_VOICE", "1") != "0":
        print("Starting voice module...", flush=True)
        from voice import run as voice_run
        threading.Thread(target=lambda: asyncio.run(voice_run()), daemon=True).start()

    def _toggle_canvas():
        showing = not canvas.isVisible()
        canvas.setVisible(showing)
        if showing:
            # Data may have changed while the overlay was hidden (e.g. an AI trade-run
            # tool ran from Chainlit/voice, a separate process) — refresh on reopen
            # rather than showing stale state. Only on open, not close — no point
            # refreshing a tab nobody's about to look at.
            canvas.refresh()

    bridge.toggle_requested.connect(_toggle_canvas)

    hotkeys.start()
    print(f"Ready. Press {overlay_hotkey} to toggle the overlay.", flush=True)
    with event_loop:
        event_loop.run_forever()


if __name__ == "__main__":
    main()

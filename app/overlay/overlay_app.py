import asyncio
import os
import sys
import threading

import httpx
import qasync
from dotenv import load_dotenv
from pynput import keyboard
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication

from api_client import api_url
from overlay import theme, uex_lookup


def main():
    # First thing, before any setting is read: the `alice` entry point starts here, and
    # .env used to be loaded only later, as a side effect of importing voice/graph. The
    # reference-data fetch below ran before that, so it always used the built-in
    # ALICE_API_URL default (localhost) -- invisible on a dev machine, where that
    # default is right, and broken for every pilot (first Windows test, 2026-10-12).
    load_dotenv()
    print("Loading reference data from UEX...", flush=True)
    try:
        uex_lookup.init()
    except httpx.TransportError as exc:
        # The first thing ALICE does is ask her server for reference data, so a wrong
        # ALICE_API_URL or a server that's down shows up here. Say so plainly instead
        # of a traceback.
        _exit_with(
            f"ALICE couldn't reach her server at {api_url()} ({type(exc).__name__}).",
            "Check your internet connection, and that ALICE_API_URL in your .env is "
            "https://api.heyalice.help (an old developer .env may still point at localhost).",
        )
    except httpx.HTTPStatusError as exc:
        _exit_with(
            f"ALICE's server at {api_url()} answered {exc.response.status_code} at startup.",
            "It may be restarting; try again in a minute.",
        )
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

    # "Just ALICE" — voice + overlay always run together as one package now (Chainlit
    # and the old voice-optional/UPLINK_VOICE=0 path are both gone). Voice pulls in the
    # full LangGraph/LLM/TTS stack (graph.py -> llm.py, ElevenLabs, Whisper) on its own
    # daemon thread so the Qt event loop below stays on the main thread.
    print("Starting voice module...", flush=True)
    from voice import run as voice_run
    threading.Thread(target=lambda: asyncio.run(voice_run()), daemon=True).start()

    def _toggle_canvas():
        showing = not canvas.isVisible()
        canvas.setVisible(showing)
        if showing:
            # Data may have changed while the overlay was hidden (e.g. an AI trade-run
            # tool ran on the voice loop's own thread while this window was closed) —
            # refresh on reopen rather than showing stale state. Only on open, not
            # close — no point refreshing a tab nobody's about to look at.
            canvas.refresh()

    bridge.toggle_requested.connect(_toggle_canvas)

    hotkeys.start()
    print(f"Ready. Press {overlay_hotkey} to toggle the overlay.", flush=True)
    with event_loop:
        event_loop.run_forever()


if __name__ == "__main__":
    main()


def _exit_with(problem: str, advice: str) -> None:
    print()
    print(f"[ALICE] {problem}")
    print(f"[ALICE] {advice}")
    sys.exit(1)

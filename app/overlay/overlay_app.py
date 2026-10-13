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
from auth.discord_identity import LoginFailed
from overlay import theme, uex_lookup


class StartupError(Exception):
    """ALICE can't start, with something the pilot can act on. The launcher shows
    problem + advice in its window — there's no console to print to in a packaged
    install, so a bare exit would just look like ALICE vanishing."""

    def __init__(self, problem: str, advice: str | None = None):
        super().__init__(problem)
        self.problem = problem
        self.advice = advice


def main():
    # First thing, before any setting is read: .env used to be loaded only later, as a
    # side effect of importing voice/graph, and the reference-data fetch ran before
    # that — always using the built-in ALICE_API_URL default (localhost). Invisible on a
    # dev machine, broken for every pilot (first Windows test, 2026-10-12).
    load_dotenv()

    app = QApplication(sys.argv)
    app.setApplicationName("ALICE")
    # Native macOS ("Aqua") widget rendering doesn't fully respect QSS box-model
    # overrides (border/padding) on things like QLineEdit — Fusion is Qt's own
    # cross-platform style, drawn entirely by Qt, so stylesheets apply predictably
    # instead of fighting native chrome.
    app.setStyle("Fusion")
    theme.load_fonts()

    # Ties an asyncio event loop to Qt's own, so coroutines (the filter/search panels,
    # the launcher's sign-in and startup) run as real, non-blocking tasks instead of
    # freezing the window. Must be set before any @asyncSlot method can be invoked.
    event_loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(event_loop)

    # The launcher is the front door: sign in, settings, then Start runs everything
    # below (load_reference_data -> build_overlay -> start_voice). Imported here so its
    # stdout capture is installed before anything else prints.
    from launcher.launcher_window import LauncherWindow

    launcher = LauncherWindow()
    launcher.show()
    with event_loop:
        event_loop.run_forever()


def load_reference_data() -> None:
    """The UEX reference cache every overlay panel reads at construction time. Blocking
    (uex_lookup.init runs its own asyncio.run), so the launcher calls this on a worker
    thread. Raises StartupError for the failures a pilot can do something about."""
    print("Loading reference data from UEX...", flush=True)
    try:
        uex_lookup.init()
    except httpx.TransportError as exc:
        # The first thing ALICE does is ask her server for reference data, so a wrong
        # ALICE_API_URL or a server that's down shows up here.
        raise StartupError(
            f"ALICE couldn't reach her server at {api_url()} ({type(exc).__name__}).",
            "Check your internet connection and try again.",
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise StartupError(
            f"ALICE's server at {api_url()} answered {exc.response.status_code} at startup.",
            "It may be restarting; try again in a minute.",
        ) from exc
    except LoginFailed as failed:
        # The launcher signs in before Start, so this is a token that went bad in
        # between (expired, or beta access removed).
        raise StartupError(str(failed)) from failed
    print("Reference data loaded.", flush=True)


class _HotkeyBridge(QObject):
    toggle_requested = Signal()


class Overlay:
    """The overlay canvas plus its global toggle hotkey. Held by the launcher for the
    life of the app — Qt windows and the pynput listener both stop working once the
    last Python reference to them is gone."""

    def __init__(self, app: QApplication):
        # Imported here rather than at module top — panels read uex_lookup's lookup
        # tables (ship_names, commodity_names, ...) at construction time, so they need
        # load_reference_data() to have already populated them.
        from overlay.filter_panel import FilterPanel
        from overlay.overlay_canvas import OverlayCanvas
        from overlay.results_panel import ResultsPanel
        from overlay.trade_runs_panel import TradeLedgerPanel, TradeRunsPanel

        # filter_panel/results_panel/trade_runs_panel/trade_ledger_panel are built with
        # no window flags at all — they're plain child widgets, parented under the
        # canvas's tabs by OverlayCanvas itself. All geometry (size, position,
        # frameless/always-on-top) lives on the canvas, the one actual top-level window.
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
        self.canvas = canvas

        self.hotkey = os.getenv("OVERLAY_HOTKEY", "<f3>")
        self._bridge = _HotkeyBridge()
        self._bridge.toggle_requested.connect(self._toggle)
        self._hotkeys = keyboard.GlobalHotKeys({self.hotkey: self._bridge.toggle_requested.emit})
        self._hotkeys.start()

    def _toggle(self):
        showing = not self.canvas.isVisible()
        self.canvas.setVisible(showing)
        if showing:
            # Data may have changed while the overlay was hidden (e.g. an AI trade-run
            # tool ran on the voice loop's own thread while this window was closed) —
            # refresh on reopen rather than showing stale state. Only on open, not
            # close — no point refreshing a tab nobody's about to look at.
            self.canvas.refresh()


def start_voice(on_fatal) -> None:
    """"Just ALICE" — voice + overlay always run together as one package. Voice pulls in
    the full LangGraph/LLM/TTS stack (graph.py -> llm.py, Whisper) on its own daemon
    thread so the Qt event loop stays on the main thread. If the voice loop dies,
    on_fatal(message) is called from that thread — the launcher turns it into a
    visible error rather than ALICE silently going deaf."""
    from voice import run as voice_run

    def _run():
        try:
            asyncio.run(voice_run())
        except LoginFailed as failed:
            on_fatal(str(failed))
        except Exception as exc:
            on_fatal(f"ALICE's voice stopped: {type(exc).__name__}: {exc}")

    print("Starting voice module...", flush=True)
    threading.Thread(target=_run, daemon=True).start()


if __name__ == "__main__":
    main()

"""ALICE's front door: sign in, pick keys/mic/speech model, open the logs, Start.

Replaces the console window a pilot used to launch from. Styled to match the rest of
ALICE's brand — the wordmark, orange bar and tagline from the Discord login pages
(auth/login_pages.py), and the faint grid with an orange glow from
docs/assets/alice-hero.svg — on the overlay's own theme tokens (overlay/theme.py).

Closing this window quits ALICE: it's the one window that's always there while voice
and the overlay run, so it doubles as the "ALICE is running" indicator.
"""
import asyncio
import os
import sys

from PySide6.QtCore import QPointF, QProcess, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from qasync import asyncSlot

import audio_devices
import bundle
import startup_status
import update_check
from auth import discord_identity
from launcher import log_stream, settings
from launcher.key_capture import KeyCapture, display_name, overlay_name, ptt_name
from overlay import theme
from overlay.overlay_app import Overlay, StartupError, load_reference_data, start_voice
from turn_metrics import alice_data_dir

ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "alice-icon.svg")
DEFAULT_DEVICE_LABEL = "System default"
WHISPER_MODEL_LABELS = {
    "small": "Small — best with names",
    "base": "Base — faster, lighter",
}

GRID_COLOR = "#1B1E20"
# Room for a key/button name inside the launcher's fixed width.
KEY_BUTTON_TEXT_WIDTH = 220

ACRONYM = "Accidental Logistics Intelligence Coordination Engine"


def _acronym_html(acronym: str) -> str:
    words = []
    for word in acronym.upper().split():
        words.append(f'<span style="color:{theme.ACCENT}">{word[0]}</span>{word[1:]}')
    return " ".join(words)

LAUNCHER_STYLESHEET = f"""
QWidget#launcher {{
    background: {theme.BG};
}}
QLabel#wordmark {{
    font-family: {theme.DISPLAY_FONT};
    font-size: 52px;
    font-weight: 700;
    letter-spacing: 6px;
    color: {theme.TEXT_PRIMARY};
}}
QFrame#brandBar {{
    background: {theme.ACCENT};
}}
QLabel#tagline {{
    font-family: {theme.LABEL_FONT};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1px;
    color: {theme.TEXT_SECONDARY};
}}
QFrame#launcherCard {{
    background: {theme.SURFACE};
    border: 1px solid {theme.BORDER};
}}
QLabel#eyebrow {{
    font-family: {theme.LABEL_FONT};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 3px;
    color: {theme.TEXT_SECONDARY};
}}
QLabel#statusDot {{
    border-radius: 4px;
}}
QLabel#account {{
    font-family: {theme.DISPLAY_FONT};
    font-size: 22px;
    font-weight: 600;
    color: {theme.TEXT_PRIMARY};
}}
QLabel#settingLabel {{
    font-family: {theme.LABEL_FONT};
    font-size: 13px;
    color: {theme.TEXT_SECONDARY};
}}
QPushButton#keyButton {{
    font-family: {theme.MONO_FONT};
    min-width: 150px;
}}
QPushButton#keyButton[capturing="true"] {{
    border: 1px solid {theme.ACCENT};
    color: {theme.ACCENT};
}}
QPushButton#startButton {{
    background: {theme.ACCENT};
    color: {theme.BG};
    border: none;
    padding: 12px 22px;
    font-family: {theme.DISPLAY_FONT};
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 2px;
}}
QPushButton#startButton:hover {{
    background: {theme.ACCENT_HOVER};
}}
QPushButton#startButton:pressed {{
    background: {theme.ACCENT_ACTIVE};
}}
QPushButton#startButton:disabled {{
    background: {theme.SURFACE_ALT};
    color: {theme.TEXT_DISABLED};
}}
QPushButton#startButton[running="true"] {{
    background: transparent;
    border: 1px solid {theme.TEXT_SECONDARY};
    color: {theme.TEXT_PRIMARY};
}}
QPushButton#startButton[running="true"][live="true"] {{
    border: 1px solid {theme.SUCCESS};
    color: {theme.SUCCESS};
}}
QPushButton#startButton[running="true"]:hover {{
    background: {theme.ERROR_MUTED};
    border: 1px solid {theme.ERROR};
    color: {theme.ERROR};
}}
QLabel#statusLine {{
    font-family: {theme.MONO_FONT};
    font-size: 11px;
    color: {theme.TEXT_DISABLED};
}}
QLabel#errorText {{
    font-family: {theme.LABEL_FONT};
    font-size: 13px;
    color: {theme.TEXT_PRIMARY};
}}
QLabel#voiceText {{
    font-family: {theme.LABEL_FONT};
    font-size: 13px;
    color: {theme.TEXT_PRIMARY};
}}
QProgressBar#voiceProgress {{
    background: {theme.SURFACE_ALT};
    border: none;
    max-height: 4px;
}}
QProgressBar#voiceProgress::chunk {{
    background: {theme.ACCENT};
}}
QCheckBox {{
    font-family: {theme.LABEL_FONT};
    font-size: 13px;
    color: {theme.TEXT_PRIMARY};
    spacing: 8px;
}}
QFrame#updateBanner {{
    background: {theme.ACCENT_MUTED};
    border: 1px solid {theme.ACCENT};
}}
QLabel#updateText {{
    font-family: {theme.LABEL_FONT};
    font-size: 13px;
    color: {theme.TEXT_PRIMARY};
}}
QLabel#versionLabel {{
    font-family: {theme.MONO_FONT};
    font-size: 11px;
    color: {theme.TEXT_DISABLED};
}}
QFrame#errorCard {{
    background: {theme.ERROR_MUTED};
    border: 1px solid {theme.ERROR};
}}
"""


def app_icon() -> QIcon:
    return QIcon(ICON_PATH)


class _Card(QFrame):
    """A brand card: eyebrow label with a status dot, then content. Same shape as the
    login pages' card."""

    def __init__(self, eyebrow: str, parent=None):
        super().__init__(parent)
        self.setObjectName("launcherCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(10)
        self.dot = QLabel()
        self.dot.setObjectName("statusDot")
        self.dot.setFixedSize(8, 8)
        header.addWidget(self.dot)
        title = QLabel(eyebrow.upper())
        title.setObjectName("eyebrow")
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)
        self.body = layout
        self.set_tone(theme.TEXT_DISABLED)

    def set_tone(self, color: str) -> None:
        self.dot.setStyleSheet(f"background: {color}; border-radius: 4px;")


class LauncherWindow(QWidget):
    voice_failed = Signal(str)
    # startup_status.report() from the voice thread -> the GUI thread.
    voice_stage = Signal(str, object, object)

    def __init__(self, parent=None):
        log_stream.install()
        super().__init__(parent)
        self.setObjectName("launcher")
        self.setWindowTitle("ALICE")
        self.setWindowIcon(app_icon())
        QApplication.instance().setWindowIcon(app_icon())
        self.setStyleSheet(theme.STYLESHEET + LAUNCHER_STYLESHEET)
        self.setFixedWidth(480)

        self._settings = settings.load()
        self._version = bundle.app_version()
        self._session = discord_identity.cached_session()
        self._overlay = None  # held for the app's life once started (see overlay_app.Overlay)
        self._running = False
        self._voice_ready = False
        self._capturing: str | None = None  # "ptt" / "overlay" while a key capture is open
        self._capture = KeyCapture(self)
        self._capture.captured.connect(self._on_key_captured)
        self._capture.captured_joystick.connect(self._on_joystick_captured)
        self._capture.cancelled.connect(self._end_capture)

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 30, 32, 22)
        root.setSpacing(18)
        root.addLayout(self._build_brand())
        root.addWidget(self._build_update_banner())
        root.addWidget(self._build_pilot_card())
        root.addWidget(self._build_settings_card())
        root.addWidget(self._build_voice_card())
        root.addWidget(self._build_error_card())
        root.addLayout(self._build_actions())
        self.status_line = QLabel("")
        self.status_line.setObjectName("statusLine")
        # One line, elided by width rather than wrapping or scrolling — the full history
        # is alice.log, one click away on "Open logs".
        self.status_line.setTextInteractionFlags(Qt.TextSelectableByMouse)
        footer = QHBoxLayout()
        footer.addWidget(self.status_line, 1)
        # Shown so a tester can say which build they're on when reporting a problem.
        version_label = QLabel(f"v{self._version}" if self._version else "")
        version_label.setObjectName("versionLabel")
        footer.addWidget(version_label)
        root.addLayout(footer)

        log_stream.signals.line.connect(self._show_status)
        self.voice_failed.connect(self._on_voice_failed)
        self.voice_stage.connect(self._on_voice_stage)
        startup_status.set_listener(self.voice_stage.emit)
        self._refresh_pilot()
        self._refresh_start()

    # ---- layout ----------------------------------------------------------------

    def _build_update_banner(self) -> QFrame:
        """Hidden unless a newer ALICE has been published. Notify-only by design: the
        button opens the download in the pilot's browser, and ALICE never downloads or
        installs anything herself (update_check.py)."""
        self.update_banner = QFrame()
        self.update_banner.setObjectName("updateBanner")
        row = QHBoxLayout(self.update_banner)
        row.setContentsMargins(14, 10, 10, 10)
        self.update_text = QLabel()
        self.update_text.setObjectName("updateText")
        self.update_text.setWordWrap(True)
        row.addWidget(self.update_text, 1)
        download = QPushButton("Download")
        download.setObjectName("primaryButton")
        download.setCursor(Qt.PointingHandCursor)
        download.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(update_check.STABLE_DOWNLOAD_URL)))
        row.addWidget(download)
        self.update_banner.hide()
        # After the window is up and the event loop is running: never delays opening.
        QTimer.singleShot(0, self._check_for_update)
        return self.update_banner

    @asyncSlot()
    async def _check_for_update(self):
        newer = await update_check.available_update(self._version)
        if newer is None:
            return
        print(f"[ALICE] Update available: {newer} (this is {self._version}).", flush=True)
        self.update_text.setText(
            f"ALICE {newer} is available — you have {self._version}. "
            "Install it over this one; your settings and logs stay."
        )
        self.update_banner.show()
        self.adjustSize()

    def _build_brand(self) -> QVBoxLayout:
        brand = QVBoxLayout()
        brand.setSpacing(0)
        wordmark = QLabel("ALICE")
        wordmark.setObjectName("wordmark")
        brand.addWidget(wordmark)
        bar = QFrame()
        bar.setObjectName("brandBar")
        bar.setFixedSize(52, 4)
        brand.addWidget(bar)
        brand.addSpacing(12)
        # What ALICE stands for, with each initial in the accent color so the acronym
        # reads straight down from the wordmark above it.
        tagline = QLabel(_acronym_html(ACRONYM))
        tagline.setObjectName("tagline")
        tagline.setTextFormat(Qt.RichText)
        brand.addWidget(tagline)
        return brand

    def _build_pilot_card(self) -> QFrame:
        self.pilot_card = _Card("Pilot")
        row = QHBoxLayout()
        self.account_label = QLabel()
        self.account_label.setObjectName("account")
        row.addWidget(self.account_label, 1)
        self.sign_in_button = QPushButton()
        self.sign_in_button.setCursor(Qt.PointingHandCursor)
        self.sign_in_button.clicked.connect(self._on_sign_in)
        row.addWidget(self.sign_in_button)
        self.pilot_card.body.addLayout(row)
        return self.pilot_card

    def _build_settings_card(self) -> QFrame:
        card = _Card("Controls")
        card.set_tone(theme.ACCENT)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)

        self.ptt_button = self._key_button("ptt")
        self.overlay_button = self._key_button("overlay")

        self.mic_combo = self._device_combo("input", self._settings.mic_device)
        self.mic_combo.currentIndexChanged.connect(self._on_mic_changed)
        self.speaker_combo = self._device_combo("output", self._settings.output_device)
        self.speaker_combo.currentIndexChanged.connect(self._on_speaker_changed)

        self.model_combo = QComboBox()
        for model in settings.WHISPER_MODELS:
            self.model_combo.addItem(WHISPER_MODEL_LABELS[model], model)
        saved_model = self.model_combo.findData(self._settings.whisper_model)
        self.model_combo.setCurrentIndex(saved_model if saved_model >= 0 else 0)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)

        self.sounds_check = QCheckBox("Chirp when recording starts and stops")
        self.sounds_check.setChecked(self._settings.ptt_sounds)
        self.sounds_check.setCursor(Qt.PointingHandCursor)
        self.sounds_check.toggled.connect(self._on_sounds_toggled)

        rows = [
            ("Push to talk", self.ptt_button),
            ("Overlay", self.overlay_button),
            ("Microphone", self.mic_combo),
            ("Speaker", self.speaker_combo),
            ("Speech model", self.model_combo),
            ("PTT sounds", self.sounds_check),
        ]
        for index, (label_text, widget) in enumerate(rows):
            label = QLabel(label_text)
            label.setObjectName("settingLabel")
            grid.addWidget(label, index, 0)
            grid.addWidget(widget, index, 1)
        card.body.addLayout(grid)
        self._settings_widgets = [
            self.ptt_button, self.overlay_button, self.mic_combo, self.speaker_combo, self.model_combo,
            self.sounds_check,
        ]
        self._refresh_keys()
        return card

    @staticmethod
    def _device_combo(kind: str, saved: str | None) -> QComboBox:
        combo = QComboBox()
        combo.addItem(DEFAULT_DEVICE_LABEL, None)
        for name in audio_devices.device_names(kind):
            combo.addItem(name, name)
        index = combo.findData(saved)
        combo.setCurrentIndex(index if index >= 0 else 0)
        return combo

    def _build_voice_card(self) -> QFrame:
        """Hidden until Start. Then the one place that says what ALICE is doing — above
        all the first launch's speech-model download, the longest wait a new pilot sees
        — and, finally, that she's ready and which key to press."""
        self.voice_card = _Card("Voice")
        self.voice_text = QLabel()
        self.voice_text.setObjectName("voiceText")
        self.voice_text.setWordWrap(True)
        self.voice_card.body.addWidget(self.voice_text)
        self.voice_progress = QProgressBar()
        self.voice_progress.setObjectName("voiceProgress")
        self.voice_progress.setTextVisible(False)
        self.voice_card.body.addWidget(self.voice_progress)
        self.voice_card.hide()
        return self.voice_card

    def _set_voice_state(self, tone: str, text: str, progress: tuple[int, int] | None = None) -> None:
        """progress: (value, maximum) — (0, 0) is Qt's busy/indeterminate bar; None hides it."""
        self.voice_card.set_tone(tone)
        self.voice_text.setText(text)
        if progress is None:
            self.voice_progress.hide()
        else:
            self.voice_progress.setRange(0, progress[1])
            self.voice_progress.setValue(progress[0])
            self.voice_progress.show()
        self.voice_card.show()
        self.adjustSize()

    def _key_button(self, which: str) -> QPushButton:
        button = QPushButton()
        button.setObjectName("keyButton")
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(lambda: self._begin_capture(which))
        return button

    def _build_error_card(self) -> QFrame:
        self.error_card = QFrame()
        self.error_card.setObjectName("errorCard")
        layout = QVBoxLayout(self.error_card)
        layout.setContentsMargins(16, 12, 16, 12)
        self.error_text = QLabel()
        self.error_text.setObjectName("errorText")
        self.error_text.setWordWrap(True)
        self.error_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.error_text)
        self.error_card.hide()
        return self.error_card

    def _build_actions(self) -> QHBoxLayout:
        actions = QHBoxLayout()
        logs_button = QPushButton("Open logs")
        logs_button.setCursor(Qt.PointingHandCursor)
        logs_button.setToolTip(str(alice_data_dir()))
        logs_button.clicked.connect(self._open_logs)
        actions.addWidget(logs_button)
        actions.addStretch()
        self.start_button = QPushButton("START ALICE")
        self.start_button.setObjectName("startButton")
        self.start_button.setCursor(Qt.PointingHandCursor)
        self.start_button.clicked.connect(self._on_start_or_stop)
        actions.addWidget(self.start_button)
        return actions

    # ---- background ------------------------------------------------------------

    def paintEvent(self, event):
        # The brand backdrop: faint grid, orange glow top-right — same as the login
        # pages and the hero image. Painted rather than a stylesheet image so it scales
        # with the window and needs no extra asset.
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.BG))
        glow = QRadialGradient(QPointF(self.width() * 0.85, self.height() * 0.12), self.width() * 0.8)
        glow.setColorAt(0.0, QColor(242, 101, 12, 50))
        glow.setColorAt(1.0, QColor(242, 101, 12, 0))
        painter.fillRect(self.rect(), glow)
        painter.setPen(QPen(QColor(GRID_COLOR), 1))
        for x in range(0, self.width(), 60):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), 52):
            painter.drawLine(0, y, self.width(), y)
        painter.end()

    # ---- pilot -----------------------------------------------------------------

    def _refresh_pilot(self) -> None:
        if self._session is not None:
            self.account_label.setText(self._session.username)
            self.sign_in_button.setText("Switch account")
            self.pilot_card.set_tone(theme.SUCCESS)
        else:
            self.account_label.setText("Not signed in")
            self.sign_in_button.setText("Sign in with Discord")
            self.pilot_card.set_tone(theme.WARNING)
        self.sign_in_button.setEnabled(not self._running)

    @asyncSlot()
    async def _on_sign_in(self):
        self._clear_error()
        self.sign_in_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.sign_in_button.setText("Waiting for Discord…")
        if self._session is not None:
            discord_identity.sign_out()
            self._session = None
        print("[ALICE] Opening your browser to sign in with Discord...", flush=True)
        try:
            self._session = await discord_identity.login()
            print(f"[ALICE] Signed in as {self._session.username}.", flush=True)
        except discord_identity.LoginFailed as failed:
            self._show_error(str(failed))
        finally:
            self._refresh_pilot()
            self._refresh_start()

    # ---- settings --------------------------------------------------------------

    def _refresh_keys(self) -> None:
        if self._capturing != "ptt":
            self._set_key_text(self.ptt_button, display_name(self._settings.ptt_hotkey))
        if self._capturing != "overlay":
            self._set_key_text(self.overlay_button, display_name(self._settings.overlay_hotkey))
        for which, button in (("ptt", self.ptt_button), ("overlay", self.overlay_button)):
            button.setProperty("capturing", self._capturing == which)
            button.style().unpolish(button)
            button.style().polish(button)

    @staticmethod
    def _set_key_text(button: QPushButton, text: str) -> None:
        # Stick names run long ("Button 12 · VKB-Sim Gladiator NXT EVO R") and the
        # window is fixed-width: cut the end (the device name), full text on hover.
        # ensurePolished first, so the measurement uses the stylesheet's monospace
        # font rather than the default one the button starts with.
        button.ensurePolished()
        button.setText(button.fontMetrics().elidedText(text, Qt.ElideRight, KEY_BUTTON_TEXT_WIDTH))
        button.setToolTip(f"{text}\n\nClick, then press a key or a joystick/HOTAS button. Esc cancels.")

    def _begin_capture(self, which: str) -> None:
        self._capturing = which
        button = self.ptt_button if which == "ptt" else self.overlay_button
        button.setText("Press a key or button…")
        self._refresh_keys()
        self._capture.start()

    def _on_key_captured(self, key) -> None:
        if self._capturing == "ptt":
            self._settings.ptt_hotkey = ptt_name(key)
        elif self._capturing == "overlay":
            self._settings.overlay_hotkey = overlay_name(key)
        settings.save(self._settings)
        self._end_capture()

    def _on_joystick_captured(self, binding: str) -> None:
        # Same string for both: a stick button has no separate PTT/overlay naming the
        # way pynput keys do (ptt_name vs overlay_name).
        if self._capturing == "ptt":
            self._settings.ptt_hotkey = binding
        elif self._capturing == "overlay":
            self._settings.overlay_hotkey = binding
        settings.save(self._settings)
        self._end_capture()

    def _end_capture(self) -> None:
        self._capture.stop()
        self._capturing = None
        self._refresh_keys()

    def _on_mic_changed(self, _index: int) -> None:
        self._settings.mic_device = self.mic_combo.currentData()
        settings.save(self._settings)

    def _on_speaker_changed(self, _index: int) -> None:
        self._settings.output_device = self.speaker_combo.currentData()
        settings.save(self._settings)

    def _on_sounds_toggled(self, checked: bool) -> None:
        self._settings.ptt_sounds = checked
        settings.save(self._settings)

    def _on_model_changed(self, _index: int) -> None:
        self._settings.whisper_model = self.model_combo.currentData()
        settings.save(self._settings)

    def _open_logs(self) -> None:
        folder = alice_data_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # ---- start -----------------------------------------------------------------

    def _refresh_start(self) -> None:
        # Running: the button is Stop. Outlined green once ALICE is listening ("live"),
        # red on hover — the Voice card's dot stays the "she's listening" indicator.
        live = self._running and self._voice_ready
        for name, value in (("running", self._running), ("live", live)):
            self.start_button.setProperty(name, value)
        self.start_button.style().unpolish(self.start_button)
        self.start_button.style().polish(self.start_button)
        if self._running:
            self.start_button.setText("STOP ALICE")
            self.start_button.setEnabled(True)
            self.start_button.setToolTip("Stop ALICE and come back here, e.g. to change a setting.")
        else:
            self.start_button.setText("START ALICE")
            self.start_button.setEnabled(self._session is not None)
            self.start_button.setToolTip("" if self._session else "Sign in with Discord first.")

    def _on_start_or_stop(self) -> None:
        if self._running:
            self._stop()
        else:
            self._on_start()

    def _stop(self) -> None:
        """Back to the launcher by restarting ALICE, not by unwinding voice + overlay in
        place: the voice loop blocks on the PTT key on its own thread, and the keyboard,
        joystick and overlay listeners all live on others — a fresh process is the one
        stop that's guaranteed complete. Costs a second or two; the speech model is
        already downloaded, and a half-finished download resumes next Start."""
        print("[ALICE] Stopping...", flush=True)
        if getattr(sys, "frozen", False):
            program, arguments = sys.executable, sys.argv[1:]
        else:
            # From source, sys.argv[0] is whatever launched us (a console-script shim,
            # a run configuration); running the entry module directly works for all.
            program, arguments = sys.executable, ["-m", "overlay.overlay_app"]
        if not QProcess.startDetached(program, arguments):
            self._show_error("Couldn't restart ALICE. Close this window and open ALICE again.")
            return
        self._capture.stop()
        os._exit(0)

    @asyncSlot()
    async def _on_start(self):
        self._clear_error()
        self._end_capture()
        self._running = True
        for widget in self._settings_widgets:
            widget.setEnabled(False)
        self._refresh_pilot()
        self._refresh_start()
        self._set_voice_state(theme.WARNING, "Connecting to ALICE's server…", (0, 0))

        settings.apply_to_env(self._settings)
        try:
            await asyncio.to_thread(load_reference_data)
            self._overlay = Overlay(QApplication.instance())
        except StartupError as failed:
            self._show_error(failed.problem + (f"\n{failed.advice}" if failed.advice else ""))
            self._reset_after_failure()
            return

        self._set_voice_state(
            theme.WARNING,
            f"The overlay is ready ({display_name(self._settings.overlay_hotkey)}). Starting ALICE's voice…",
            (0, 0),
        )
        start_voice(self.voice_failed.emit)

    def _on_voice_stage(self, stage: str, done: int | None, total: int | None) -> None:
        if stage == startup_status.DOWNLOADING_VOICE:
            done_mb = (done or 0) / 1e6
            if total:
                text = f"{done_mb:.0f} / {total / 1e6:.0f} MB"
                progress = (int(done or 0) // 1024, int(total) // 1024)
            else:
                text = f"{done_mb:.0f} MB"
                progress = (0, 0)
            self._set_voice_state(
                theme.WARNING,
                f"First launch: downloading the speech model ({text}).\n"
                "This only happens once. ALICE can't hear you until it's done.",
                progress,
            )
        elif stage == startup_status.LOADING_VOICE:
            self._set_voice_state(theme.WARNING, "Loading the speech model…", (0, 0))
        elif stage == startup_status.READY:
            self._voice_ready = True
            self._set_voice_state(
                theme.SUCCESS,
                f"Ready. Hold {display_name(self._settings.ptt_hotkey)} to talk to ALICE; "
                f"{display_name(self._settings.overlay_hotkey)} opens the overlay.",
            )
            self._refresh_start()

    def _on_voice_failed(self, message: str) -> None:
        self._show_error(message + "\nPress Restart ALICE to try again.")
        self._set_voice_state(theme.ERROR, "ALICE's voice stopped — see the message below.")
        self._voice_ready = False
        self._refresh_start()
        # Same button, same restart as Stop — just named for what it does here.
        self.start_button.setText("RESTART ALICE")

    def _reset_after_failure(self) -> None:
        self._running = False
        self.voice_card.hide()
        for widget in self._settings_widgets:
            widget.setEnabled(True)
        self._refresh_pilot()
        self._refresh_start()

    # ---- messages --------------------------------------------------------------

    def _show_status(self, line: str) -> None:
        metrics = self.status_line.fontMetrics()
        self.status_line.setText(metrics.elidedText(line, Qt.ElideRight, self.status_line.width()))
        self.status_line.setToolTip(line)

    def _show_error(self, message: str) -> None:
        self.error_text.setText(message)
        self.error_card.show()
        self.adjustSize()

    def _clear_error(self) -> None:
        self.error_card.hide()
        self.adjustSize()

    def closeEvent(self, event):
        self._capture.stop()
        super().closeEvent(event)
        QApplication.instance().quit()
        # The voice loop and pynput listeners are daemon threads, but qasync's loop can
        # outlive quit() while a turn is mid-flight; exit outright so closing the
        # window always means ALICE is gone.
        os._exit(0)

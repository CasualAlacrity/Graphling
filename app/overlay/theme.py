"""Argo Astronautics MFD-style theme — industrial orange / gunmetal / near-black.

Palette matches the tokens supplied for the overlay's style pass; not an official
Cloud Imperium brand kit.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontDatabase, QPainter, QPen
from PySide6.QtWidgets import QWidget

_FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts")
_FONT_FILES = [
    "Rajdhani-Regular.ttf",
    "Rajdhani-Medium.ttf",
    "Rajdhani-SemiBold.ttf",
    "Rajdhani-Bold.ttf",
    "JetBrainsMono[wght].ttf",
]


def load_fonts():
    """Registers the bundled Rajdhani/JetBrains Mono font files with Qt.

    Must run after QApplication() exists but before any widget using theme.STYLESHEET
    is constructed. Qt can't fetch web fonts at runtime, so these are vendored
    (both OFL-licensed) rather than referenced by URL like the CSS prototype did.
    """
    for filename in _FONT_FILES:
        QFontDatabase.addApplicationFont(os.path.join(_FONTS_DIR, filename))

BG = "#0D0F10"
SURFACE = "#17191B"
SURFACE_ALT = "#1F2224"
BORDER = "#33373A"
BORDER_SUBTLE = "#24272A"

TEXT_PRIMARY = "#EDEAE4"
TEXT_SECONDARY = "#9AA0A6"
TEXT_DISABLED = "#5C6266"

ACCENT = "#F2650C"
ACCENT_HOVER = "#FF7A1F"
ACCENT_ACTIVE = "#C94F00"
ACCENT_MUTED = "rgba(242, 101, 12, 40)"
FOCUS_RING = "#FF9142"

SUCCESS = "#4CAF6D"
WARNING = "#E8B23A"
ERROR = "#E5484D"
INFO = "#4A90D9"

# Bundled (see load_fonts()) with system fallbacks in case loading ever fails.
DISPLAY_FONT = '"Rajdhani", "Helvetica Neue", Arial, sans-serif'
MONO_FONT = '"JetBrains Mono", "Menlo", "Consolas", "DejaVu Sans Mono", monospace'
LABEL_FONT = '"Helvetica Neue", "Segoe UI", Arial, sans-serif'

# QSS has no letter-spacing/text-transform — group titles are written pre-uppercased
# in the widgets that use them, and monospace carries the "instrument placard" feel
# QSS itself can't produce.
STYLESHEET = f"""
FilterPanel, ResultsPanel {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
}}

QLabel {{
    color: {TEXT_SECONDARY};
    font-family: {LABEL_FONT};
    font-size: 11px;
    background: transparent;
}}
QLabel#panelHeader {{
    font-family: {DISPLAY_FONT};
    font-size: 14px;
    font-weight: 600;
    color: {ACCENT};
    padding: 3px 2px;
    border-bottom: 1px solid {BORDER};
}}
QLabel#fieldLabel {{
    font-size: 10.5px;
    min-width: 90px;
    max-width: 90px;
}}

QFrame#sectionRow {{
    background-color: transparent;
    border: none;
    border-bottom: 1px solid {BORDER_SUBTLE};
    padding-bottom: 3px;
    margin-bottom: 3px;
}}
QLabel#sectionLabel {{
    color: {TEXT_SECONDARY};
    font-family: {DISPLAY_FONT};
    font-size: 14px;
    font-weight: 600;
    min-width: 128px;
    max-width: 128px;
}}

QLineEdit, QComboBox, QSpinBox {{
    background-color: {SURFACE_ALT};
    border: 1px solid {BORDER};
    color: {TEXT_PRIMARY};
    font-family: {MONO_FONT};
    font-size: 12px;
    min-height: 18px;
    padding: 5px 7px;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border: 1px solid {FOCUS_RING};
}}
QLineEdit:disabled {{
    color: {TEXT_DISABLED};
}}
QComboBox QAbstractItemView {{
    background-color: {SURFACE_ALT};
    border: 1px solid {BORDER};
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT};
    selection-color: {BG};
}}

QCheckBox {{
    color: {TEXT_SECONDARY};
    font-family: {DISPLAY_FONT};
    font-size: 13px;
    font-weight: 600;
    spacing: 8px;
}}
QCheckBox:checked {{
    color: {TEXT_PRIMARY};
}}
QCheckBox::indicator {{
    width: 13px;
    height: 13px;
    border: 1px solid {BORDER};
    background-color: {SURFACE_ALT};
}}
QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border: 1px solid {ACCENT};
}}

QPushButton {{
    font-family: {DISPLAY_FONT};
    font-size: 14px;
    font-weight: 600;
    padding: 4px 16px;
    border: 1px solid {BORDER};
    background-color: transparent;
    color: {TEXT_SECONDARY};
}}
QPushButton:hover {{
    color: {TEXT_PRIMARY};
    border-color: {FOCUS_RING};
}}
QPushButton#primaryButton {{
    background-color: {ACCENT};
    border: 1px solid {ACCENT};
    color: {BG};
}}
QPushButton#primaryButton:hover {{
    background-color: {ACCENT_HOVER};
    border-color: {ACCENT_HOVER};
}}
QPushButton#primaryButton:pressed {{
    background-color: {ACCENT_ACTIVE};
    border-color: {ACCENT_ACTIVE};
}}

QPushButton#sortChip {{
    font-family: {MONO_FONT};
    font-size: 10px;
    font-weight: bold;
    padding: 5px 12px;
    border: 1px solid {BORDER};
    background-color: {SURFACE_ALT};
    color: {TEXT_SECONDARY};
}}
QPushButton#sortChip:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
    color: {BG};
}}

QListWidget {{
    background-color: {SURFACE_ALT};
    border: 1px solid {BORDER_SUBTLE};
    color: {TEXT_PRIMARY};
    font-family: {MONO_FONT};
    font-size: 11px;
    outline: none;
}}
QListWidget::item {{
    padding: 6px 8px;
    border-bottom: 1px solid {BORDER_SUBTLE};
}}
QListWidget::item:selected {{
    background-color: {ACCENT_MUTED};
    color: {TEXT_PRIMARY};
}}
"""

_BRACKET_LENGTH = 14
_BRACKET_WIDTH = 2
_BRACKET_INSET = 1


class HudWindow(QWidget):
    """A top-level window with MFD-style corner brackets painted over its frame.

    QSS alone can't draw the bracket accents (no ::before/::after) — this adds them
    via a small paintEvent override instead of hand-rolling the whole panel chrome.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def paintEvent(self, event):
        super().paintEvent(event)

        painter = QPainter(self)
        pen = QPen(QColor(ACCENT))
        pen.setWidth(_BRACKET_WIDTH)
        painter.setPen(pen)

        width, height = self.width(), self.height()
        length, inset = _BRACKET_LENGTH, _BRACKET_INSET

        corners = [
            ((inset, inset), (1, 0), (0, 1)),  # top-left
            ((width - inset, inset), (-1, 0), (0, 1)),  # top-right
            ((inset, height - inset), (1, 0), (0, -1)),  # bottom-left
            ((width - inset, height - inset), (-1, 0), (0, -1)),  # bottom-right
        ]
        for (x, y), (dx, _), (_, dy) in corners:
            painter.drawLine(x, y, x + dx * length, y)
            painter.drawLine(x, y, x, y + dy * length)

        painter.end()

"""Argo Astronautics MFD-style theme — industrial orange / gunmetal / near-black.

Palette matches the tokens supplied for the overlay's style pass; not an official
Cloud Imperium brand kit.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontDatabase, QIcon, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

_FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts")
_ICONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icons")
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


def load_icon(name, color, size=20):
    """Renders a bundled SVG icon (assets/icons/<name>.svg, using `currentColor` for
    its fill) into a QIcon tinted with the given hex color. Self-authored icons, not
    a redistributed set — Qt's QSS `color` property doesn't tint icons the way it
    does text, so the color has to be baked into the rendered pixmap itself."""
    svg_path = os.path.join(_ICONS_DIR, f"{name}.svg")
    with open(svg_path, encoding="utf-8") as svg_file:
        svg_text = svg_file.read().replace("currentColor", color)

    renderer = QSvgRenderer(svg_text.encode("utf-8"))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)

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
ERROR_MUTED = "rgba(229, 72, 77, 40)"
INFO = "#4A90D9"

# Bundled (see load_fonts()) with system fallbacks in case loading ever fails.
DISPLAY_FONT = '"Rajdhani", "Helvetica Neue", Arial, sans-serif'
MONO_FONT = '"JetBrains Mono", "Menlo", "Consolas", "DejaVu Sans Mono", monospace'
LABEL_FONT = '"Helvetica Neue", "Segoe UI", Arial, sans-serif'

# QSS has no letter-spacing/text-transform — group titles are written pre-uppercased
# in the widgets that use them, and monospace carries the "instrument placard" feel
# QSS itself can't produce.
STYLESHEET = f"""
FilterPanel, ResultsPanel, TradeRunsPanel, TradeLedgerPanel, OverlayCanvas {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
}}

QTabWidget::pane {{
    border: none;
}}
QTabBar::tab {{
    background-color: {SURFACE_ALT};
    color: {TEXT_SECONDARY};
    font-family: {DISPLAY_FONT};
    font-size: 13px;
    font-weight: 600;
    border: 1px solid {BORDER};
    border-bottom: none;
    padding: 6px 18px;
    margin-right: 2px;
}}
QTabBar::tab:hover {{
    color: {TEXT_PRIMARY};
}}
QTabBar::tab:selected {{
    background-color: {SURFACE};
    color: {ACCENT};
    border-color: {ACCENT};
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
    color: {ACCENT};
    font-family: {DISPLAY_FONT};
    font-size: 12px;
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

QPushButton#filterIconToggle {{
    border: 1px solid {BORDER};
    background: transparent;
    padding: 0px;
    min-width: 30px;
    max-width: 30px;
    min-height: 30px;
    max-height: 30px;
}}
QPushButton#filterIconToggle:hover {{
    border-color: {FOCUS_RING};
}}
QPushButton#filterIconToggle:checked {{
    border-color: {ACCENT};
    background-color: {ACCENT_MUTED};
}}
QLabel#filterOptionsCaption {{
    color: {TEXT_DISABLED};
    font-family: {MONO_FONT};
    font-size: 10.5px;
    padding-left: 4px;
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

QLabel#sortToggleLabel {{
    font-family: {MONO_FONT};
    font-size: 10px;
    font-weight: bold;
    color: {TEXT_DISABLED};
}}
QLabel#sortToggleLabel[active="true"] {{
    color: {TEXT_PRIMARY};
}}

QFrame#resultsHeaderRow {{
    background-color: {SURFACE_ALT};
    border: none;
    border-bottom: 1px solid {BORDER};
}}
QLabel#resultsColumnHeader {{
    color: {TEXT_SECONDARY};
    font-family: {MONO_FONT};
    font-size: 10px;
    font-weight: 600;
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
    padding: 0px;
    border-bottom: 1px solid {BORDER_SUBTLE};
}}
QListWidget::item:hover {{
    background-color: {SURFACE};
    border-left: 3px solid {ACCENT};
}}
QListWidget::item:selected {{
    background-color: {ACCENT_MUTED};
    color: {TEXT_PRIMARY};
}}

QFrame#routeRow {{
    background: transparent;
    border: none;
}}
QLabel#routeCommodity {{
    color: {TEXT_PRIMARY};
    font-family: {DISPLAY_FONT};
    font-size: 13px;
    font-weight: 600;
}}
QLabel#routeScu {{
    color: {TEXT_PRIMARY};
    font-family: {MONO_FONT};
    font-size: 13px;
    font-weight: 600;
}}
QLabel#routeTerminalName {{
    color: {TEXT_PRIMARY};
    font-family: {DISPLAY_FONT};
    font-size: 12px;
    font-weight: 600;
}}
QLabel#routeTerminalPrice {{
    color: {TEXT_SECONDARY};
    font-family: {MONO_FONT};
    font-size: 10.5px;
}}
QLabel#routeArrow {{
    color: {TEXT_DISABLED};
    font-size: 14px;
}}
QLabel#routeDistance {{
    color: {TEXT_DISABLED};
    font-family: {MONO_FONT};
    font-size: 9.5px;
}}
QLabel#routeProfit {{
    color: {TEXT_PRIMARY};
    font-family: {MONO_FONT};
    font-size: 13px;
    font-weight: 700;
}}
QLabel#routeMargin {{
    color: {TEXT_SECONDARY};
    font-family: {MONO_FONT};
    font-size: 10.5px;
}}
QPushButton#addRouteButton {{
    border-radius: 12px;
    border: 1px solid {BORDER};
    background: transparent;
    color: {TEXT_SECONDARY};
    font-family: {DISPLAY_FONT};
    font-size: 14px;
    font-weight: 700;
    padding: 0px;
}}
QPushButton#addRouteButton:hover {{
    background-color: {ACCENT};
    border-color: {ACCENT};
    color: {BG};
}}
QPushButton#addRouteButton:pressed {{
    background-color: {ACCENT_ACTIVE};
    border-color: {ACCENT_ACTIVE};
}}

QScrollArea#cardScrollArea {{
    background-color: {SURFACE_ALT};
    border: 1px solid {BORDER_SUBTLE};
}}
QScrollArea#cardScrollArea > QWidget > QWidget {{
    background-color: transparent;
}}
QWidget#cardScrollContent {{
    background-color: transparent;
}}
QLabel#emptyStateLabel {{
    color: {TEXT_SECONDARY};
    font-family: {DISPLAY_FONT};
    font-size: 14px;
    font-weight: 600;
}}
QLabel#emptyStateSubtitle {{
    color: {TEXT_DISABLED};
    font-family: {MONO_FONT};
    font-size: 10.5px;
}}
QScrollArea#cardScrollArea QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0px;
}}
QScrollArea#cardScrollArea QScrollBar::handle:vertical {{
    background: {BORDER};
    min-height: 24px;
}}
QScrollArea#cardScrollArea QScrollBar::handle:vertical:hover {{
    background: {ACCENT};
}}
QScrollArea#cardScrollArea QScrollBar::add-line:vertical,
QScrollArea#cardScrollArea QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollArea#cardScrollArea QScrollBar::add-page:vertical,
QScrollArea#cardScrollArea QScrollBar::sub-page:vertical {{
    background: transparent;
}}

QFrame#tradeRunCard {{
    background: transparent;
    border: none;
    border-bottom: 1px solid {BORDER_SUBTLE};
}}
QLabel#runShip {{
    color: {TEXT_PRIMARY};
    font-family: {DISPLAY_FONT};
    font-size: 13px;
    font-weight: 600;
    border-left: 2px solid {ACCENT};
    padding-left: 8px;
}}
QLabel#runAge {{
    color: {TEXT_DISABLED};
    font-family: {MONO_FONT};
    font-size: 10px;
}}
QLabel#legBadge {{
    font-family: {DISPLAY_FONT};
    font-size: 10px;
    font-weight: 700;
    padding: 1px 6px;
    border: 1px solid {BORDER};
}}
QLabel#legBadge[legType="acquisition"] {{
    color: {INFO};
    border-color: {INFO};
}}
QLabel#legBadge[legType="sale"] {{
    color: {SUCCESS};
    border-color: {SUCCESS};
}}
QLabel#legTerminal {{
    color: {TEXT_PRIMARY};
    font-family: {DISPLAY_FONT};
    font-size: 12px;
    font-weight: 600;
}}
QLabel#legDetail {{
    color: {TEXT_SECONDARY};
    font-family: {MONO_FONT};
    font-size: 10.5px;
}}
QPushButton#markDoneButton {{
    background-color: {SUCCESS};
    border: 1px solid {SUCCESS};
    color: {BG};
    font-family: {DISPLAY_FONT};
    font-size: 13px;
    font-weight: 600;
    padding: 6px;
}}
QPushButton#markDoneButton:hover {{
    background-color: {ACCENT_HOVER};
    border-color: {ACCENT_HOVER};
}}
QPushButton#finalizeRunButton {{
    background-color: {SUCCESS};
    border: 1px solid {SUCCESS};
    color: {BG};
}}
QPushButton#finalizeRunButton:disabled {{
    background-color: transparent;
    border: 1px solid {BORDER};
    color: {TEXT_DISABLED};
}}
QPushButton#abandonRunButton {{
    border: 1px solid {ERROR};
    background: transparent;
    padding: 0px;
    min-width: 24px;
    max-width: 24px;
    min-height: 24px;
    max-height: 24px;
}}
QPushButton#abandonRunButton:hover {{
    background-color: {ERROR_MUTED};
    border-color: {ERROR};
}}
QLabel#ledgerCommodityBadge {{
    color: {TEXT_PRIMARY};
    font-family: {DISPLAY_FONT};
    font-size: 12px;
    font-weight: 600;
}}
QLabel#ledgerRoute {{
    color: {TEXT_SECONDARY};
    font-family: {MONO_FONT};
    font-size: 10.5px;
}}
QLabel#ledgerProfit {{
    color: {TEXT_PRIMARY};
    font-family: {MONO_FONT};
    font-size: 13px;
    font-weight: 700;
}}
QLabel#ledgerProfit[profitable="true"] {{
    color: {SUCCESS};
}}
QLabel#ledgerProfit[profitable="false"] {{
    color: {ERROR};
}}

QFrame#ledgerDayGroup {{
    background: transparent;
    border: none;
    border-bottom: 1px solid {BORDER_SUBTLE};
}}
QLabel#ledgerDayTitle {{
    color: {TEXT_PRIMARY};
    font-family: {DISPLAY_FONT};
    font-size: 13px;
    font-weight: 600;
    border-left: 2px solid {ACCENT};
    padding-left: 8px;
}}
QFrame#ledgerRunRow {{
    background: transparent;
    border: none;
}}
QFrame#ledgerRunRow:hover {{
    background-color: {SURFACE};
}}
QFrame#ledgerDayTotalRow {{
    background-color: {SURFACE_ALT};
    border: none;
    border-top: 1px solid {BORDER_SUBTLE};
}}
QLabel#ledgerDayTotalLabel {{
    color: {TEXT_DISABLED};
    font-family: {MONO_FONT};
    font-size: 10px;
}}

QPushButton#chevronButton {{
    background: transparent;
    border: none;
    color: {TEXT_SECONDARY};
    font-size: 14px;
    font-weight: 700;
    padding: 0px 4px;
}}
QPushButton#chevronButton:hover {{
    color: {ACCENT};
}}
QPushButton#chevronButton[small="true"] {{
    color: {TEXT_DISABLED};
    font-size: 10px;
    font-weight: 400;
}}
QPushButton#chevronButton[small="true"]:hover {{
    color: {ACCENT};
}}

QWidget#legSummaryRow {{
    background: transparent;
}}
QWidget#legSummaryRow:hover {{
    background-color: {SURFACE_ALT};
}}
QLabel#legStatus {{
    font-family: {MONO_FONT};
    font-size: 10px;
    padding: 2px 7px;
    border: 1px solid {BORDER};
    color: {TEXT_DISABLED};
}}
QLabel#legStatus[status="success"] {{
    color: {SUCCESS};
    border-color: {SUCCESS};
}}
QLabel#legStatus[status="current"] {{
    color: {ACCENT};
    border-color: {ACCENT};
}}

QWidget#legExpandedArea {{
    border-left: 1px solid {BORDER_SUBTLE};
}}

QLabel#runMoneyLabel {{
    color: {TEXT_DISABLED};
    font-family: {MONO_FONT};
    font-size: 9.5px;
}}
QLabel#runMoneyValue {{
    color: {TEXT_PRIMARY};
    font-family: {MONO_FONT};
    font-size: 13px;
    font-weight: 700;
}}
QLabel#runMoneyValue[profitable="true"] {{
    color: {SUCCESS};
}}
QLabel#runMoneyValue[profitable="false"] {{
    color: {ERROR};
}}

QLabel#dialogTitle {{
    color: {ACCENT};
    font-family: {DISPLAY_FONT};
    font-size: 14px;
    font-weight: 600;
}}
QLabel#dialogFieldLabel {{
    color: {TEXT_SECONDARY};
    font-family: {LABEL_FONT};
    font-size: 10px;
    font-weight: 600;
}}
QLabel#dialogTotal {{
    color: {TEXT_PRIMARY};
    font-family: {MONO_FONT};
    font-size: 13px;
    font-weight: 700;
}}
QPushButton#confirmButton {{
    background-color: {SUCCESS};
    border: 1px solid {SUCCESS};
    color: {BG};
    font-family: {DISPLAY_FONT};
    font-size: 13px;
    font-weight: 600;
    padding: 6px;
}}
QPushButton#confirmButton[warning="true"] {{
    background-color: {WARNING};
    border-color: {WARNING};
}}
QPushButton#copyButton {{
    background: transparent;
    border: 1px solid {BORDER};
    color: {TEXT_SECONDARY};
    font-family: {DISPLAY_FONT};
    font-size: 12px;
    font-weight: 600;
    padding: 5px 10px;
}}
QPushButton#copyButton:hover {{
    border-color: {FOCUS_RING};
    color: {TEXT_PRIMARY};
}}
QPushButton#copyButton[copied="true"] {{
    border-color: {SUCCESS};
    color: {SUCCESS};
}}

QLabel#recapLabel {{
    color: {TEXT_DISABLED};
    font-family: {MONO_FONT};
    font-size: 9.5px;
}}
QLabel#recapValue {{
    color: {TEXT_SECONDARY};
    font-family: {MONO_FONT};
    font-size: 11px;
}}

QLabel#breadcrumbDot {{
    border-radius: 11px;
    border: 2px solid {BORDER};
    background: transparent;
    color: {TEXT_DISABLED};
    font-family: {MONO_FONT};
    font-size: 10px;
    font-weight: 700;
}}
QLabel#breadcrumbDot[done="true"] {{
    border-color: {SUCCESS};
    background-color: {SUCCESS};
    color: {BG};
}}
QLabel#breadcrumbDot[current="true"] {{
    border-color: {ACCENT};
    background-color: {ACCENT};
    color: {BG};
}}
QLabel#breadcrumbLabel {{
    color: {TEXT_DISABLED};
    font-family: {DISPLAY_FONT};
    font-size: 10.5px;
    font-weight: 600;
}}
QLabel#breadcrumbLabel[current="true"] {{
    color: {ACCENT};
}}
QFrame#breadcrumbRail {{
    background-color: {BORDER};
    border: none;
}}
QFrame#breadcrumbRail[done="true"] {{
    background-color: {SUCCESS};
}}
"""

_BRACKET_LENGTH = 14
_BRACKET_WIDTH = 2
_BRACKET_INSET = 1


class HudWindow(QWidget):
    """A top-level window with MFD-style corner brackets painted over its frame.

    QSS alone can't draw the bracket accents (no ::before/::after) — this adds them
    via a small paintEvent override instead of hand-rolling the whole panel chrome.
    Brackets only draw while this widget actually IS the top-level window (isWindow());
    once reparented into a layout (e.g. as a tab page inside OverlayCanvas) they'd
    otherwise stack a frame-within-a-frame on top of the parent's own brackets.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def paintEvent(self, event):
        super().paintEvent(event)

        if not self.isWindow():
            return

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

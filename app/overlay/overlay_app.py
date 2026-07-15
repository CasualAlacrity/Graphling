import asyncio
import os
import threading

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QGroupBox,
    QLineEdit,
    QCompleter,
    QSpinBox,
    QComboBox,
    QPushButton,
)
from pynput import keyboard

from tools.uexcorp.client import UEXCorpClient
from voice import run as voice_run


class HotkeyBridge(QObject):
    toggle_requested = Signal()


bridge = HotkeyBridge()
hotkeys = keyboard.GlobalHotKeys({'<f3>': lambda: bridge.toggle_requested.emit()})

app = QApplication()

uex_client = UEXCorpClient(
    api_key=os.getenv("UEXCORP_API_KEY"),
    bearer_token=os.getenv("UEXCORP_BEARER_TOKEN"),
)
uex_cache = asyncio.run(uex_client.get_uex_cache())
ship_names = [v.name for v in uex_cache.vehicles]
commodity_names = [c.name for c in uex_cache.commodities]
terminal_names = [t.name for t in uex_cache.terminals]


def find_terminal(name):
    for terminal in uex_cache.terminals:
        if terminal.name == name:
            return terminal
    return None


def terminal_breadcrumb(terminal):
    if terminal is None:
        return ""
    parts = [terminal.star_system_name, terminal.orbit_name, terminal.moon_name]
    return ">".join(part for part in parts if part)


# "buy" = stock available to purchase at a terminal; "sell" = how saturated a
# terminal's demand already is. Same tiers, different top label.
SOURCE_INVENTORY_LEVELS = [
    status.name for status in uex_cache.commodity_statuses if status.type == "buy"
]

DESTINATION_INVENTORY_LEVELS = [
    status.name for status in uex_cache.commodity_statuses if status.type == "sell"
]

filter_widget = QWidget()
filter_widget.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
filter_widget.resize(800, 600)
filter_widget.setStyleSheet("background-color: #AAAAAA")

verticalLayout = QVBoxLayout(filter_widget)

filter_header = QLabel(parent=filter_widget, text="Filters:")
verticalLayout.addWidget(filter_header)

# --- Ship & Cargo ---
ship_cargo_group = QGroupBox(parent=filter_widget, title="Ship and Cargo")
ship_cargo_layout = QVBoxLayout(ship_cargo_group)

ship_label = QLabel(parent=ship_cargo_group, text="Ship")
ship_input = QLineEdit(parent=ship_cargo_group, placeholderText="Enter a ship name")
ship_completer = QCompleter(
    ship_names,
    parent=ship_input,
    caseSensitivity=Qt.CaseInsensitive,
    maxVisibleItems=10,
)
ship_input.setCompleter(ship_completer)
ship_cargo_layout.addWidget(ship_label)
ship_cargo_layout.addWidget(ship_input)

cargo_label = QLabel(parent=ship_cargo_group, text="Cargo")
cargo_input = QSpinBox(parent=ship_cargo_group, minimum=0, maximum=3000, suffix=" units")
ship_cargo_layout.addWidget(cargo_label)
ship_cargo_layout.addWidget(cargo_input)

commodity_label = QLabel(parent=ship_cargo_group, text="Commodity")
commodity_input = QLineEdit(parent=ship_cargo_group, placeholderText="Search commodity...")
commodity_completer = QCompleter(
    commodity_names,
    parent=commodity_input,
    caseSensitivity=Qt.CaseInsensitive,
    maxVisibleItems=10,
)
commodity_input.setCompleter(commodity_completer)
ship_cargo_layout.addWidget(commodity_label)
ship_cargo_layout.addWidget(commodity_input)

verticalLayout.addWidget(ship_cargo_group)

# --- Source ---
source_group = QGroupBox(parent=filter_widget, title="Source")
source_layout = QVBoxLayout(source_group)

source_terminal_label = QLabel(parent=source_group, text="Source Terminal")
source_terminal_input = QLineEdit(parent=source_group, placeholderText="Search terminal...")
source_terminal_completer = QCompleter(
    terminal_names,
    parent=source_terminal_input,
    caseSensitivity=Qt.CaseInsensitive,
    maxVisibleItems=10,
)
source_terminal_input.setCompleter(source_terminal_completer)
source_layout.addWidget(source_terminal_label)
source_layout.addWidget(source_terminal_input)

source_terminal_breadcrumb = QLabel(parent=source_group)
source_layout.addWidget(source_terminal_breadcrumb)


def on_source_terminal_selected(name):
    source_terminal_breadcrumb.setText(terminal_breadcrumb(find_terminal(name)))


source_terminal_completer.activated[str].connect(on_source_terminal_selected)
source_terminal_input.textEdited.connect(lambda _: source_terminal_breadcrumb.clear())

min_source_inventory_label = QLabel(parent=source_group, text="Min Source Inventory")
min_source_inventory_input = QComboBox(parent=source_group)
min_source_inventory_input.addItems(["Select..."] + SOURCE_INVENTORY_LEVELS)
source_layout.addWidget(min_source_inventory_label)
source_layout.addWidget(min_source_inventory_input)

verticalLayout.addWidget(source_group)

# --- Destination ---
destination_group = QGroupBox(parent=filter_widget, title="Destination")
destination_layout = QVBoxLayout(destination_group)

destination_terminal_label = QLabel(parent=destination_group, text="Destination Terminal")
destination_terminal_input = QLineEdit(parent=destination_group, placeholderText="Search terminal...")
destination_terminal_completer = QCompleter(
    terminal_names,
    parent=destination_terminal_input,
    caseSensitivity=Qt.CaseInsensitive,
    maxVisibleItems=10,
)
destination_terminal_input.setCompleter(destination_terminal_completer)
destination_layout.addWidget(destination_terminal_label)
destination_layout.addWidget(destination_terminal_input)

destination_terminal_breadcrumb = QLabel(parent=destination_group)
destination_layout.addWidget(destination_terminal_breadcrumb)


def on_destination_terminal_selected(name):
    destination_terminal_breadcrumb.setText(terminal_breadcrumb(find_terminal(name)))


destination_terminal_completer.activated[str].connect(on_destination_terminal_selected)
destination_terminal_input.textEdited.connect(lambda _: destination_terminal_breadcrumb.clear())

max_destination_inventory_label = QLabel(parent=destination_group, text="Max Destination Inventory")
max_destination_inventory_input = QComboBox(parent=destination_group)
max_destination_inventory_input.addItems(["Select..."] + DESTINATION_INVENTORY_LEVELS)
destination_layout.addWidget(max_destination_inventory_label)
destination_layout.addWidget(max_destination_inventory_input)

verticalLayout.addWidget(destination_group)

# --- Buttons ---
button_row = QHBoxLayout()
button_row.addStretch()

cancel_button = QPushButton(parent=filter_widget, text="Cancel")
apply_button = QPushButton(parent=filter_widget, text="Apply")
button_row.addWidget(cancel_button)
button_row.addWidget(apply_button)

verticalLayout.addLayout(button_row)

threading.Thread(target=lambda: asyncio.run(voice_run()), daemon=True).start()


def on_toggle_requested():
    filter_widget.setVisible(not filter_widget.isVisible())


bridge.toggle_requested.connect(on_toggle_requested)

hotkeys.start()
app.exec()

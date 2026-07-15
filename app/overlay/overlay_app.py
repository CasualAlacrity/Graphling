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
    QListWidget,
    QCheckBox,
)
from pynput import keyboard

from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.reference_cache import TerminalType
from tools.uexcorp.trade_data import UEXTradeRoute
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
ship_names = [v.name_full for v in uex_cache.vehicles]
commodity_names = [c.name for c in uex_cache.commodities if c.is_buyable == 1]
terminal_names = [t.name for t in uex_cache.terminals if t.type == TerminalType.COMMODITY]


def find_terminal(name):
    for terminal in uex_cache.terminals:
        if terminal.name == name:
            return terminal
    return None


def find_commodity(name):
    for commodity in uex_cache.commodities:
        if commodity.name == name:
            return commodity
    return None


def find_vehicle(name):
    for vehicle in uex_cache.vehicles:
        if vehicle.name_full == name:
            return vehicle
    return None


def inventory_code_for(status_name, status_type):
    if not status_name or status_name == "Select...":
        return None
    for status in uex_cache.commodity_statuses:
        if status.type == status_type and status.name == status_name:
            return status.code
    return None


def terminal_breadcrumb(terminal):
    if terminal is None:
        return ""
    parts = [terminal.star_system_name, terminal.orbit_name, terminal.moon_name, terminal.name]
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
    filterMode=Qt.MatchFlag.MatchContains,
    maxVisibleItems=10,

)
ship_input.setCompleter(ship_completer)
ship_cargo_layout.addWidget(ship_label)
ship_cargo_layout.addWidget(ship_input)

cargo_label = QLabel(parent=ship_cargo_group, text="Cargo Volume")
cargo_input = QSpinBox(parent=ship_cargo_group, minimum=0, maximum=3000, suffix=" SCU")
ship_cargo_layout.addWidget(cargo_label)
ship_cargo_layout.addWidget(cargo_input)


def on_ship_selected(name):
    vehicle = find_vehicle(name)
    if vehicle is not None:
        cargo_input.setValue(int(vehicle.scu))


ship_completer.activated[str].connect(on_ship_selected)

verticalLayout.addWidget(ship_cargo_group)

# --- Commodity ---
commodity_group = QGroupBox(parent=filter_widget, title="Commodity")
commodity_layout = QVBoxLayout(commodity_group)

commodity_label = QLabel(parent=commodity_group, text="Commodity")
commodity_input = QLineEdit(parent=commodity_group, placeholderText="Search commodity...")
commodity_completer = QCompleter(
    commodity_names,
    parent=commodity_input,
    caseSensitivity=Qt.CaseInsensitive,
    maxVisibleItems=10,
)
commodity_input.setCompleter(commodity_completer)
commodity_layout.addWidget(commodity_label)
commodity_layout.addWidget(commodity_input)

verticalLayout.addWidget(commodity_group)

# --- Source ---
source_group = QGroupBox(parent=filter_widget, title="Source")
source_layout = QVBoxLayout(source_group)

source_terminal_label = QLabel(parent=source_group, text="Source Terminal")
source_terminal_input = QLineEdit(parent=source_group, placeholderText="Search terminal...")
source_terminal_completer = QCompleter(
    terminal_names,
    parent=source_terminal_input,
    caseSensitivity=Qt.CaseInsensitive,
    filterMode=Qt.MatchFlag.MatchContains,
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
    filterMode=Qt.MatchFlag.MatchContains,
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

# --- Options ---
options_row = QHBoxLayout()
space_only_checkbox = QCheckBox(parent=filter_widget, text="Space only")
autoload_checkbox = QCheckBox(parent=filter_widget, text="Has autoload")
options_row.addWidget(space_only_checkbox)
options_row.addWidget(autoload_checkbox)
options_row.addStretch()

verticalLayout.addLayout(options_row)

# --- Buttons ---
button_row = QHBoxLayout()
button_row.addStretch()

reset_button = QPushButton(parent=filter_widget, text="Reset")
search_button = QPushButton(parent=filter_widget, text="Search")
button_row.addWidget(reset_button)
button_row.addWidget(search_button)

verticalLayout.addLayout(button_row)

# --- Sort ---
sort_row = QHBoxLayout()
sort_score_button = QPushButton(parent=filter_widget, text="Sort: Score")
sort_profit_button = QPushButton(parent=filter_widget, text="Sort: Profit")
sort_margin_button = QPushButton(parent=filter_widget, text="Sort: Margin")
sort_row.addWidget(sort_score_button)
sort_row.addWidget(sort_profit_button)
sort_row.addWidget(sort_margin_button)

verticalLayout.addLayout(sort_row)

results_list = QListWidget(parent=filter_widget)
verticalLayout.addWidget(results_list)

last_routes = []


def estimated_profit_for(route):
    cargo_scu = cargo_input.value()
    reachable_scu = min(route.scu_origin, route.scu_destination)
    if cargo_scu > 0:
        reachable_scu = min(reachable_scu, cargo_scu)
    return (route.price_destination - route.price_origin) * reachable_scu


def render_routes(routes):
    results_list.clear()
    if not routes:
        results_list.addItem("No routes found for these filters.")
        return

    for route in routes[:20]:
        cargo_scu = cargo_input.value()
        reachable_scu = min(route.scu_origin, route.scu_destination)
        if cargo_scu > 0:
            reachable_scu = min(reachable_scu, cargo_scu)

        line = (
            f"{route.commodity_name}: {route.origin_terminal_name} -> {route.destination_terminal_name}"
            f"  |  {route.distance:.0f} Gm  |  {route.price_margin:.1f}% margin"
            f"  |  {reachable_scu:.0f} SCU  |  +{estimated_profit_for(route):.0f} aUEC"
        )
        results_list.addItem(line)


def sort_by_score():
    render_routes(sorted(last_routes, key=lambda route: route.score, reverse=True))


def sort_by_profit():
    render_routes(sorted(last_routes, key=estimated_profit_for, reverse=True))


def sort_by_margin():
    render_routes(sorted(last_routes, key=lambda route: route.price_margin, reverse=True))


def on_search_clicked():
    global last_routes

    commodity = find_commodity(commodity_input.text())
    source_terminal = find_terminal(source_terminal_input.text())
    destination_terminal = find_terminal(destination_terminal_input.text())

    if commodity is None and source_terminal is None and destination_terminal is None:
        results_list.clear()
        results_list.addItem("Select a commodity, source terminal, or destination terminal to search.")
        return

    raw_routes = asyncio.run(uex_client.get_commodity_routes(
        commodity_id=commodity.id if commodity else None,
        origin_terminal_id=source_terminal.id if source_terminal else None,
        destination_terminal_id=destination_terminal.id if destination_terminal else None,
    ))
    routes = [UEXTradeRoute.model_validate(row) for row in raw_routes]

    min_source_code = inventory_code_for(min_source_inventory_input.currentText(), "buy")
    max_destination_code = inventory_code_for(max_destination_inventory_input.currentText(), "sell")
    space_only = space_only_checkbox.isChecked()
    require_autoload = autoload_checkbox.isChecked()

    last_routes = [
        route for route in routes
        if (min_source_code is None or route.status_origin >= min_source_code)
        and (max_destination_code is None or route.status_destination <= max_destination_code)
        and (not space_only or (route.is_on_ground_origin == 0 and route.is_on_ground_destination == 0))
        and (not require_autoload or (route.has_loading_dock_origin == 1 and route.has_loading_dock_destination == 1))
    ]

    sort_by_score()


search_button.clicked.connect(on_search_clicked)
sort_score_button.clicked.connect(sort_by_score)
sort_profit_button.clicked.connect(sort_by_profit)
sort_margin_button.clicked.connect(sort_by_margin)

threading.Thread(target=lambda: asyncio.run(voice_run()), daemon=True).start()


def on_toggle_requested():
    filter_widget.setVisible(not filter_widget.isVisible())


bridge.toggle_requested.connect(on_toggle_requested)

hotkeys.start()
app.exec()

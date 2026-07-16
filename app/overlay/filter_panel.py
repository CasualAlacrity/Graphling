import asyncio

from PySide6.QtCore import QEvent, QObject, Signal, Qt
from PySide6.QtWidgets import (
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
    QCheckBox,
)

from db.session import engine
from overlay import theme
from overlay.theme import HudWindow
from overlay.uex_lookup import (
    commodity_names,
    commodity_ids_at,
    DESTINATION_INVENTORY_LEVELS,
    find_commodity,
    find_terminal,
    find_vehicle,
    inventory_code_for,
    is_space_terminal,
    search_routes,
    ship_names,
    SOURCE_INVENTORY_LEVELS,
    terminal_breadcrumb,
    terminal_ids_for,
    terminal_names,
    uex_cache,
)


class CompleterFocusFilter(QObject):
    """Shows a field's full completer popup on focus, instead of waiting for a keystroke."""

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FocusIn:
            completer = watched.completer()
            if completer is not None:
                completer.complete()
        return False


class FilterPanel(HudWindow):
    routes_found = Signal(list, int)  # filtered routes, cargo SCU at time of search
    search_rejected = Signal(str)  # no commodity/source/destination selected at all

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(theme.STYLESHEET)

        self.current_commodity_candidates = list(commodity_names)
        self.current_source_candidates = list(terminal_names)
        self.current_destination_candidates = list(terminal_names)
        # Unfiltered-by-space-only versions, used only to tell "not reachable at all"
        # (red) apart from "reachable but not Space Only" (orange) once narrowed.
        self.current_source_reachable = list(terminal_names)
        self.current_destination_reachable = list(terminal_names)

        self._build_ui()
        self._wire_signals()

    def _build_ui(self):
        self._main_layout = QVBoxLayout(self)

        filter_header = QLabel(parent=self, text="▸ FILTERS", objectName="panelHeader")
        self._main_layout.addWidget(filter_header)

        # --- Ship & Cargo ---
        ship_cargo_group = QGroupBox(parent=self, title="SHIP AND CARGO")
        ship_cargo_layout = QVBoxLayout(ship_cargo_group)

        ship_label = QLabel(parent=ship_cargo_group, text="Ship")
        self.ship_input = QLineEdit(parent=ship_cargo_group, placeholderText="Enter a ship name")
        self.ship_completer = QCompleter(
            ship_names,
            parent=self.ship_input,
            caseSensitivity=Qt.CaseInsensitive,
            filterMode=Qt.MatchFlag.MatchContains,
            maxVisibleItems=10,
        )
        self.ship_input.setCompleter(self.ship_completer)
        self.ship_input.setClearButtonEnabled(True)
        self.ship_input.installEventFilter(CompleterFocusFilter(parent=self.ship_input))
        ship_cargo_layout.addWidget(ship_label)
        ship_cargo_layout.addWidget(self.ship_input)

        cargo_label = QLabel(parent=ship_cargo_group, text="Cargo Volume")
        self.cargo_input = QSpinBox(parent=ship_cargo_group, minimum=0, maximum=3000, suffix=" SCU")
        ship_cargo_layout.addWidget(cargo_label)
        ship_cargo_layout.addWidget(self.cargo_input)

        self._main_layout.addWidget(ship_cargo_group)

        # --- Commodity ---
        commodity_group = QGroupBox(parent=self, title="COMMODITY")
        commodity_layout = QVBoxLayout(commodity_group)

        commodity_label = QLabel(parent=commodity_group, text="Commodity")
        self.commodity_input = QLineEdit(parent=commodity_group, placeholderText="Search commodity...")
        self.commodity_completer = QCompleter(
            commodity_names,
            parent=self.commodity_input,
            caseSensitivity=Qt.CaseInsensitive,
            maxVisibleItems=10,
        )
        self.commodity_input.setCompleter(self.commodity_completer)
        self.commodity_input.setClearButtonEnabled(True)
        self.commodity_input.installEventFilter(CompleterFocusFilter(parent=self.commodity_input))
        commodity_layout.addWidget(commodity_label)
        commodity_layout.addWidget(self.commodity_input)

        self._main_layout.addWidget(commodity_group)

        # --- Source ---
        source_group = QGroupBox(parent=self, title="SOURCE")
        source_layout = QVBoxLayout(source_group)

        source_terminal_label = QLabel(parent=source_group, text="Source Terminal")
        self.source_terminal_input = QLineEdit(parent=source_group, placeholderText="Search terminal...")
        self.source_terminal_completer = QCompleter(
            terminal_names,
            parent=self.source_terminal_input,
            caseSensitivity=Qt.CaseInsensitive,
            filterMode=Qt.MatchFlag.MatchContains,
            maxVisibleItems=10,
        )
        self.source_terminal_input.setCompleter(self.source_terminal_completer)
        self.source_terminal_input.setClearButtonEnabled(True)
        self.source_terminal_input.installEventFilter(CompleterFocusFilter(parent=self.source_terminal_input))
        source_layout.addWidget(source_terminal_label)
        source_layout.addWidget(self.source_terminal_input)

        self.source_terminal_breadcrumb = QLabel(parent=source_group, wordWrap=True, objectName="breadcrumb")
        source_layout.addWidget(self.source_terminal_breadcrumb)

        min_source_inventory_label = QLabel(parent=source_group, text="Min Source Inventory")
        self.min_source_inventory_input = QComboBox(parent=source_group)
        self.min_source_inventory_input.addItems(["Select..."] + SOURCE_INVENTORY_LEVELS)
        source_layout.addWidget(min_source_inventory_label)
        source_layout.addWidget(self.min_source_inventory_input)

        self._main_layout.addWidget(source_group)

        # --- Destination ---
        destination_group = QGroupBox(parent=self, title="DESTINATION")
        destination_layout = QVBoxLayout(destination_group)

        destination_terminal_label = QLabel(parent=destination_group, text="Destination Terminal")
        self.destination_terminal_input = QLineEdit(parent=destination_group, placeholderText="Search terminal...")
        self.destination_terminal_completer = QCompleter(
            terminal_names,
            parent=self.destination_terminal_input,
            caseSensitivity=Qt.CaseInsensitive,
            filterMode=Qt.MatchFlag.MatchContains,
            maxVisibleItems=10,
        )
        self.destination_terminal_input.setCompleter(self.destination_terminal_completer)
        self.destination_terminal_input.setClearButtonEnabled(True)
        self.destination_terminal_input.installEventFilter(
            CompleterFocusFilter(parent=self.destination_terminal_input)
        )
        destination_layout.addWidget(destination_terminal_label)
        destination_layout.addWidget(self.destination_terminal_input)

        self.destination_terminal_breadcrumb = QLabel(
            parent=destination_group, wordWrap=True, objectName="breadcrumb"
        )
        destination_layout.addWidget(self.destination_terminal_breadcrumb)

        max_destination_inventory_label = QLabel(parent=destination_group, text="Max Destination Inventory")
        self.max_destination_inventory_input = QComboBox(parent=destination_group)
        self.max_destination_inventory_input.addItems(["Select..."] + DESTINATION_INVENTORY_LEVELS)
        destination_layout.addWidget(max_destination_inventory_label)
        destination_layout.addWidget(self.max_destination_inventory_input)

        self._main_layout.addWidget(destination_group)

        # --- Options ---
        options_row = QHBoxLayout()
        self.space_only_checkbox = QCheckBox(parent=self, text="Space only")
        self.autoload_checkbox = QCheckBox(parent=self, text="Has autoload")
        options_row.addWidget(self.space_only_checkbox)
        options_row.addWidget(self.autoload_checkbox)
        options_row.addStretch()

        self._main_layout.addLayout(options_row)

        # --- Buttons ---
        button_row = QHBoxLayout()
        button_row.addStretch()

        self.reset_button = QPushButton(parent=self, text="Reset")
        self.search_button = QPushButton(parent=self, text="Search", objectName="primaryButton")
        button_row.addWidget(self.reset_button)
        button_row.addWidget(self.search_button)

        self._main_layout.addLayout(button_row)

    def _wire_signals(self):
        self.ship_completer.activated[str].connect(self._on_ship_selected)

        self.source_terminal_completer.activated[str].connect(self._on_source_terminal_selected)
        self.source_terminal_input.textChanged.connect(lambda _: self.source_terminal_breadcrumb.clear())

        self.destination_terminal_completer.activated[str].connect(self._on_destination_terminal_selected)
        self.destination_terminal_input.textChanged.connect(lambda _: self.destination_terminal_breadcrumb.clear())

        self.commodity_completer.activated[str].connect(lambda _: self.refresh_filters())
        self.source_terminal_completer.activated[str].connect(lambda _: self.refresh_filters())
        self.destination_terminal_completer.activated[str].connect(lambda _: self.refresh_filters())
        self.space_only_checkbox.toggled.connect(lambda _: self.refresh_filters())

        self.commodity_input.textChanged.connect(
            lambda _: self._on_field_edited(self.commodity_input, self.current_commodity_candidates)
        )
        self.source_terminal_input.textChanged.connect(
            lambda _: self._on_field_edited(
                self.source_terminal_input, self.current_source_candidates, self.current_source_reachable
            )
        )
        self.destination_terminal_input.textChanged.connect(
            lambda _: self._on_field_edited(
                self.destination_terminal_input,
                self.current_destination_candidates,
                self.current_destination_reachable,
            )
        )

        self.search_button.clicked.connect(self._on_search_clicked)

    def _on_ship_selected(self, name):
        vehicle = find_vehicle(name)
        if vehicle is not None:
            self.cargo_input.setValue(int(vehicle.scu))

    def _on_source_terminal_selected(self, name):
        self.source_terminal_breadcrumb.setText(terminal_breadcrumb(find_terminal(name)))

    def _on_destination_terminal_selected(self, name):
        self.destination_terminal_breadcrumb.setText(terminal_breadcrumb(find_terminal(name)))

    @staticmethod
    def _apply_validation_style(field, valid_names, reachable_names=None):
        text = field.text()

        if not text or text in valid_names:
            style, tooltip = "", ""
        elif reachable_names is not None and text in reachable_names:
            # A real, reachable terminal — just not one that satisfies Space Only.
            style = f"border: 2px solid {theme.WARNING};"
            tooltip = "This destination isn't Space Only"
        else:
            style, tooltip = f"border: 2px solid {theme.ERROR};", ""

        field.setStyleSheet(style)
        field.setToolTip(tooltip)

    # --- Smart filtering: each of Commodity/Source/Destination narrows the other
    # two's completer suggestions, based on the last *selected* (not typed) value.

    async def _refresh_filters_async(self):
        commodity = find_commodity(self.commodity_input.text())
        source = find_terminal(self.source_terminal_input.text())
        destination = find_terminal(self.destination_terminal_input.text())

        # Commodity candidates: narrowed by whichever of source/destination are set.
        if source is not None and destination is not None:
            source_ids, dest_ids = await asyncio.gather(
                commodity_ids_at(source.id, "buy"), commodity_ids_at(destination.id, "sell")
            )
            commodity_ids = source_ids & dest_ids
            commodity_candidates = [c.name for c in uex_cache.commodities if c.id in commodity_ids]
        elif source is not None:
            commodity_ids = await commodity_ids_at(source.id, "buy")
            commodity_candidates = [c.name for c in uex_cache.commodities if c.id in commodity_ids]
        elif destination is not None:
            commodity_ids = await commodity_ids_at(destination.id, "sell")
            commodity_candidates = [c.name for c in uex_cache.commodities if c.id in commodity_ids]
        else:
            commodity_candidates = list(commodity_names)

        # Source candidates: direct from commodity, or fanned out from destination
        # (every terminal that sells anything the destination will buy) — fetched
        # concurrently, since a terminal can carry dozens of commodities.
        if commodity is not None:
            terminal_ids = await terminal_ids_for(commodity.id, "buy")
            source_reachable = [t.nickname for t in uex_cache.terminals if t.id in terminal_ids]
        elif destination is not None:
            reachable_commodity_ids = await commodity_ids_at(destination.id, "sell")
            results = await asyncio.gather(*(terminal_ids_for(cid, "buy") for cid in reachable_commodity_ids))
            terminal_ids = set().union(*results)
            terminal_ids.discard(destination.id)
            source_reachable = [t.nickname for t in uex_cache.terminals if t.id in terminal_ids]
        else:
            source_reachable = list(terminal_names)

        # Destination candidates: direct from commodity, or fanned out from source
        # (every terminal that buys anything the source sells), same concurrency.
        if commodity is not None:
            terminal_ids = await terminal_ids_for(commodity.id, "sell")
            destination_reachable = [t.nickname for t in uex_cache.terminals if t.id in terminal_ids]
        elif source is not None:
            available_commodity_ids = await commodity_ids_at(source.id, "buy")
            results = await asyncio.gather(*(terminal_ids_for(cid, "sell") for cid in available_commodity_ids))
            terminal_ids = set().union(*results)
            terminal_ids.discard(source.id)
            destination_reachable = [t.nickname for t in uex_cache.terminals if t.id in terminal_ids]
        else:
            destination_reachable = list(terminal_names)

        # Space Only only narrows a field once something actually drives its reachable
        # set (commodity, or the other terminal) — with nothing set yet there's no
        # "missing location" to narrow against, so the completer stays unfiltered.
        space_only = self.space_only_checkbox.isChecked()
        # A field that already holds a resolved value is pinned, not still being
        # searched — narrowing (and thus space-only filtering) only makes sense for
        # whichever field is still open, same principle as search_routes' pinning.
        source_narrowed = source is None and (commodity is not None or destination is not None)
        destination_narrowed = destination is None and (commodity is not None or source is not None)

        if space_only and source_narrowed:
            source_candidates = [n for n in source_reachable if is_space_terminal(find_terminal(n))]
        else:
            source_candidates = source_reachable

        if space_only and destination_narrowed:
            destination_candidates = [n for n in destination_reachable if is_space_terminal(find_terminal(n))]
        else:
            destination_candidates = destination_reachable

        # This coroutine runs inside its own throwaway asyncio.run() loop (Qt's event
        # loop isn't asyncio), so any pooled connections left open here would be bound
        # to a loop that's about to close — the next call's fresh loop can't reuse them.
        # Disposing before returning keeps every call self-contained.
        await engine.dispose()

        return commodity_candidates, source_candidates, destination_candidates, source_reachable, destination_reachable

    def refresh_filters(self):
        (
            self.current_commodity_candidates,
            self.current_source_candidates,
            self.current_destination_candidates,
            self.current_source_reachable,
            self.current_destination_reachable,
        ) = asyncio.run(self._refresh_filters_async())

        self.commodity_completer.model().setStringList(self.current_commodity_candidates)
        self.source_terminal_completer.model().setStringList(self.current_source_candidates)
        self.destination_terminal_completer.model().setStringList(self.current_destination_candidates)

        self._apply_validation_style(self.commodity_input, self.current_commodity_candidates)
        self._apply_validation_style(
            self.source_terminal_input, self.current_source_candidates, self.current_source_reachable
        )
        self._apply_validation_style(
            self.destination_terminal_input, self.current_destination_candidates, self.current_destination_reachable
        )

    def _on_field_edited(self, field, valid_names, reachable_names=None):
        # Clearing a field that was driving narrowing (e.g. Source) needs the other
        # fields' candidate lists relaxed back — only a full refresh does that, so it's
        # worth the one extra fetch specifically on "cleared to empty," not every keystroke.
        if field.text() == "":
            self.refresh_filters()
        else:
            self._apply_validation_style(field, valid_names, reachable_names)

    def _on_search_clicked(self):
        commodity = find_commodity(self.commodity_input.text())
        source_terminal = find_terminal(self.source_terminal_input.text())
        destination_terminal = find_terminal(self.destination_terminal_input.text())

        if commodity is None and source_terminal is None and destination_terminal is None:
            self.search_rejected.emit("Select a commodity, source terminal, or destination terminal to search.")
            return

        min_source_code = inventory_code_for(self.min_source_inventory_input.currentText(), "buy")
        max_destination_code = inventory_code_for(self.max_destination_inventory_input.currentText(), "sell")
        space_only = self.space_only_checkbox.isChecked()
        require_autoload = self.autoload_checkbox.isChecked()

        routes = asyncio.run(search_routes(
            commodity_id=commodity.id if commodity else None,
            source_terminal_id=source_terminal.id if source_terminal else None,
            destination_terminal_id=destination_terminal.id if destination_terminal else None,
            min_source_code=min_source_code,
            max_destination_code=max_destination_code,
            space_only=space_only,
            require_autoload=require_autoload,
        ))

        self.routes_found.emit(routes, self.cargo_input.value())

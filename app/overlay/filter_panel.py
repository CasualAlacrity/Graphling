import asyncio

from qasync import asyncSlot
from PySide6.QtCore import QEvent, QObject, Signal, Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QCompleter,
    QSpinBox,
    QComboBox,
    QPushButton,
    QCheckBox,
)

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
        # Only one filter refresh should ever be in flight — a newer trigger (another
        # completer selection, a checkbox toggle) cancels whatever's still running
        # instead of letting two overlapping fetches race to overwrite the candidates.
        self._refresh_task = None

        self._build_ui()
        self._wire_signals()

    @staticmethod
    def _field_column(label_text, widget):
        """A label beside its one widget — one item in a section row's horizontal group
        of fields. Inline (not stacked above) to keep the whole panel short; terminal
        breadcrumbs live in the field's tooltip instead of their own line for the same
        reason."""
        column = QWidget()
        layout = QHBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(parent=column, text=label_text, objectName="fieldLabel"))
        layout.addWidget(widget, 1)
        return column

    @staticmethod
    def _section_row(label_text, *field_columns):
        """A section label sitting beside a horizontal row of its fields, instead of
        QGroupBox's title-above-content — matches the wide-panel prototype's layout."""
        row = QFrame(objectName="sectionRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 1, 0, 1)

        row_layout.addWidget(QLabel(parent=row, text=label_text, objectName="sectionLabel"))

        fields = QWidget(parent=row)
        fields_layout = QHBoxLayout(fields)
        fields_layout.setContentsMargins(0, 0, 0, 0)
        for column in field_columns:
            column.setParent(fields)
            fields_layout.addWidget(column)
        row_layout.addWidget(fields, 1)

        return row

    def _build_ui(self):
        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(12, 5, 12, 6)
        self._main_layout.setSpacing(2)

        filter_header = QLabel(parent=self, text="▸ FILTERS", objectName="panelHeader")
        self._main_layout.addWidget(filter_header)

        # --- Ship, Cargo & Commodity ---
        self.ship_input = QLineEdit(placeholderText="Enter a ship name")
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

        self.cargo_input = QSpinBox(minimum=0, maximum=3000, suffix=" SCU")

        self.commodity_input = QLineEdit(placeholderText="Search commodity...")
        self.commodity_completer = QCompleter(
            commodity_names,
            parent=self.commodity_input,
            caseSensitivity=Qt.CaseInsensitive,
            maxVisibleItems=10,
        )
        self.commodity_input.setCompleter(self.commodity_completer)
        self.commodity_input.setClearButtonEnabled(True)
        self.commodity_input.installEventFilter(CompleterFocusFilter(parent=self.commodity_input))

        self._main_layout.addWidget(self._section_row(
            "SHIP AND CARGO",
            self._field_column("Ship", self.ship_input),
            self._field_column("Cargo Volume", self.cargo_input),
            self._field_column("Commodity", self.commodity_input),
        ))

        # --- Source ---
        self.source_terminal_input = QLineEdit(placeholderText="Search terminal...")
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

        self.min_source_inventory_input = QComboBox()
        self.min_source_inventory_input.addItems(["Select..."] + SOURCE_INVENTORY_LEVELS)

        self._main_layout.addWidget(self._section_row(
            "SOURCE",
            self._field_column("Terminal", self.source_terminal_input),
            self._field_column("Min Inventory", self.min_source_inventory_input),
        ))

        # --- Destination ---
        self.destination_terminal_input = QLineEdit(placeholderText="Search terminal...")
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

        self.max_destination_inventory_input = QComboBox()
        self.max_destination_inventory_input.addItems(["Select..."] + DESTINATION_INVENTORY_LEVELS)

        self._main_layout.addWidget(self._section_row(
            "DESTINATION",
            self._field_column("Terminal", self.destination_terminal_input),
            self._field_column("Max Inventory", self.max_destination_inventory_input),
        ))

        # --- Options ---
        options_row = QHBoxLayout()
        self.space_only_checkbox = QCheckBox(parent=self, text="🚀 Space only")
        self.autoload_checkbox = QCheckBox(parent=self, text="📦 Has autoload")
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

        self.commodity_completer.activated[str].connect(lambda _: self.refresh_filters())
        self.source_terminal_completer.activated[str].connect(lambda _: self.refresh_filters())
        self.destination_terminal_completer.activated[str].connect(lambda _: self.refresh_filters())
        self.space_only_checkbox.toggled.connect(lambda _: self.refresh_filters())

        self.commodity_input.textChanged.connect(
            lambda _: self._on_field_edited(self.commodity_input, self.current_commodity_candidates)
        )
        self.source_terminal_input.textChanged.connect(
            lambda _: self._on_field_edited(
                self.source_terminal_input,
                self.current_source_candidates,
                self.current_source_reachable,
                self._terminal_breadcrumb_for,
            )
        )
        self.destination_terminal_input.textChanged.connect(
            lambda _: self._on_field_edited(
                self.destination_terminal_input,
                self.current_destination_candidates,
                self.current_destination_reachable,
                self._terminal_breadcrumb_for,
            )
        )

        self.search_button.clicked.connect(self._on_search_clicked)

    def _on_ship_selected(self, name):
        vehicle = find_vehicle(name)
        if vehicle is not None:
            self.cargo_input.setValue(int(vehicle.scu))

    @staticmethod
    def _apply_validation_style(field, valid_names, reachable_names=None, breadcrumb_for=None):
        text = field.text()

        if not text:
            style, tooltip = "", ""
        elif text in valid_names:
            # A resolved selection — the terminal breadcrumb (if any) lives in the
            # tooltip instead of its own line, to keep the panel short.
            style = ""
            tooltip = breadcrumb_for(text) if breadcrumb_for else ""
        elif reachable_names is not None and text in reachable_names:
            # A real, reachable terminal — just not one that satisfies Space Only.
            style = f"border: 2px solid {theme.WARNING};"
            warning = "This destination isn't Space Only"
            breadcrumb = breadcrumb_for(text) if breadcrumb_for else ""
            tooltip = f"{breadcrumb}\n{warning}" if breadcrumb else warning
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

        return commodity_candidates, source_candidates, destination_candidates, source_reachable, destination_reachable

    @asyncSlot()
    async def refresh_filters(self):
        if self._refresh_task is not None:
            self._refresh_task.cancel()

        task = asyncio.ensure_future(self._refresh_filters_async())
        self._refresh_task = task
        try:
            (
                self.current_commodity_candidates,
                self.current_source_candidates,
                self.current_destination_candidates,
                self.current_source_reachable,
                self.current_destination_reachable,
            ) = await task
        except asyncio.CancelledError:
            # A newer selection superseded this one — its own refresh_filters call
            # will apply the up-to-date candidates, nothing to do here.
            return
        finally:
            if self._refresh_task is task:
                self._refresh_task = None

        self.commodity_completer.model().setStringList(self.current_commodity_candidates)
        self.source_terminal_completer.model().setStringList(self.current_source_candidates)
        self.destination_terminal_completer.model().setStringList(self.current_destination_candidates)

        self._apply_validation_style(self.commodity_input, self.current_commodity_candidates)
        self._apply_validation_style(
            self.source_terminal_input,
            self.current_source_candidates,
            self.current_source_reachable,
            self._terminal_breadcrumb_for,
        )
        self._apply_validation_style(
            self.destination_terminal_input,
            self.current_destination_candidates,
            self.current_destination_reachable,
            self._terminal_breadcrumb_for,
        )

    @staticmethod
    def _terminal_breadcrumb_for(name):
        return terminal_breadcrumb(find_terminal(name))

    def _on_field_edited(self, field, valid_names, reachable_names=None, breadcrumb_for=None):
        # Clearing a field that was driving narrowing (e.g. Source) needs the other
        # fields' candidate lists relaxed back — only a full refresh does that, so it's
        # worth the one extra fetch specifically on "cleared to empty," not every keystroke.
        if field.text() == "":
            self.refresh_filters()
        else:
            self._apply_validation_style(field, valid_names, reachable_names, breadcrumb_for)

    @asyncSlot()
    async def _on_search_clicked(self):
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

        self.search_button.setEnabled(False)
        self.search_button.setText("SEARCHING…")
        try:
            routes = await search_routes(
                commodity_id=commodity.id if commodity else None,
                source_terminal_id=source_terminal.id if source_terminal else None,
                destination_terminal_id=destination_terminal.id if destination_terminal else None,
                min_source_code=min_source_code,
                max_destination_code=max_destination_code,
                space_only=space_only,
                require_autoload=require_autoload,
            )
        finally:
            self.search_button.setEnabled(True)
            self.search_button.setText("Search")

        self.routes_found.emit(routes, self.cargo_input.value())

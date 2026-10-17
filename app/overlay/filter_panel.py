import asyncio

import requests
from PySide6.QtCore import QEvent, QObject, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from qasync import asyncSlot

from overlay import theme, uex_lookup
from overlay.theme import HudWindow
from overlay.uex_lookup import (
    commodity_ids_at,
    find_commodity,
    find_location,
    find_terminal,
    find_vehicle,
    inventory_code_for,
    is_space_terminal,
    location_names,
    search_routes,
    terminal_breadcrumb,
    terminal_ids_for,
)


FILTER_LABEL_COLUMN_WIDTH = 128


class CompleterFocusFilter(QObject):
    """Shows a field's full completer popup on focus, instead of waiting for a keystroke."""

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FocusIn:
            completer = watched.completer()
            if completer is not None:
                completer.complete()
        return False


class BreadcrumbLineEdit(QLineEdit):
    """A QLineEdit that can show a dim, right-aligned breadcrumb ("ArcCorp › Wala")
    inside the field once its text resolves to a terminal. The text margin keeps typing
    from running underneath it; the clear button stays at the far right."""

    CLEAR_BUTTON_ALLOWANCE = 26

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._breadcrumb = QLabel(self, objectName="inputBreadcrumb")
        self._breadcrumb.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._breadcrumb.hide()

    def set_breadcrumb(self, text):
        if not text:
            self._breadcrumb.hide()
            self.setTextMargins(0, 0, 0, 0)
            return
        # Drop the star system (the field is already a terminal) and elide to at most
        # half the field so a long chain never hides what the pilot typed.
        parts = text.split(">")
        shown = " › ".join(parts[1:] if len(parts) > 2 else parts)
        metrics = QFontMetrics(self._breadcrumb.font())
        self._breadcrumb.setText(metrics.elidedText(shown, Qt.ElideRight, max(60, self.width() // 2)))
        self._breadcrumb.adjustSize()
        self.setTextMargins(0, 0, self._breadcrumb.width() + self.CLEAR_BUTTON_ALLOWANCE, 0)
        self._place_breadcrumb()
        self._breadcrumb.show()

    def _place_breadcrumb(self):
        x = self.width() - self._breadcrumb.width() - self.CLEAR_BUTTON_ALLOWANCE
        y = (self.height() - self._breadcrumb.height()) // 2
        self._breadcrumb.move(max(0, x), y)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._breadcrumb.isVisible():
            self._place_breadcrumb()


class FilterPanel(HudWindow):
    routes_found = Signal(list, int, str)  # filtered routes, cargo SCU, ship name at time of search
    search_rejected = Signal(str)  # no commodity/source/destination selected at all

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(theme.STYLESHEET)

        self.current_commodity_candidates = list(uex_lookup.commodity_names)
        # Star systems, planets, moons and orbits are accepted wherever a terminal is — a
        # region stands for every terminal inside it. Appended after any narrowing, since
        # a region is never "unreachable" the way a single terminal can be.
        self._location_names = location_names()
        self.current_source_candidates = list(uex_lookup.terminal_names) + self._location_names
        self.current_destination_candidates = list(uex_lookup.terminal_names) + self._location_names
        # Unfiltered-by-space-only versions, used only to tell "not reachable at all"
        # (red) apart from "reachable but not Space Only" (orange) once narrowed.
        self.current_source_reachable = list(self.current_source_candidates)
        self.current_destination_reachable = list(self.current_destination_candidates)
        # Only one filter refresh should ever be in flight — a newer trigger (another
        # completer selection, a checkbox toggle) cancels whatever's still running
        # instead of letting two overlapping fetches race to overwrite the candidates.
        self._refresh_task = None

        self._build_ui()
        self._wire_signals()

    @staticmethod
    def _field_column(label_text, widget):
        """A field with its label stacked above it — one item in a card's row of fields."""
        column = QWidget()
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(QLabel(parent=column, text=label_text, objectName="fieldLabel"))
        layout.addWidget(widget)
        return column

    @staticmethod
    def _card(label_text, icon_name, edge, *field_columns):
        """A Cargo Manifest card: icon + label on the left, its fields on the right. The
        orange edge ("accent") marks the two ends of the route; the neutral one is the
        ship and cargo."""
        card = QFrame(objectName="filterCard")
        card.setProperty("edge", edge)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(14)

        label = QWidget(parent=card)
        label.setFixedWidth(FILTER_LABEL_COLUMN_WIDTH)
        label_layout = QHBoxLayout(label)
        label_layout.setContentsMargins(0, 0, 0, 0)
        label_layout.setSpacing(8)
        icon = QLabel(parent=label)
        icon.setPixmap(theme.load_icon(icon_name, theme.TEXT_SECONDARY, size=16).pixmap(QSize(16, 16)))
        label_layout.addWidget(icon)
        label_layout.addWidget(QLabel(parent=label, text=label_text, objectName="sectionLabel"))
        label_layout.addStretch()
        layout.addWidget(label, 0, Qt.AlignTop)

        fields = QWidget(parent=card)
        fields_layout = QHBoxLayout(fields)
        fields_layout.setContentsMargins(0, 0, 0, 0)
        fields_layout.setSpacing(14)
        for column in field_columns:
            column.setParent(fields)
            fields_layout.addWidget(column)
        layout.addWidget(fields, 1)

        return card

    @staticmethod
    def _route_connector():
        """The ROUTE marker between Source and Destination: the route reads top to bottom."""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(14 + FILTER_LABEL_COLUMN_WIDTH, 0, 0, 0)
        layout.setSpacing(8)
        circle = QLabel(parent=row, objectName="routeConnectorCircle")
        circle.setFixedSize(20, 20)
        circle.setAlignment(Qt.AlignCenter)
        circle.setPixmap(theme.load_icon("arrow-down", theme.ACCENT, size=12).pixmap(QSize(12, 12)))
        layout.addWidget(circle)
        layout.addWidget(QLabel(parent=row, text="ROUTE", objectName="routeConnectorText"))
        layout.addStretch()
        return row

    def _build_ui(self):
        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(12, 5, 12, 6)
        self._main_layout.setSpacing(8)

        # --- Ship, Cargo & Commodity ---
        self.ship_input = QLineEdit(placeholderText="Search ship...")
        self.ship_completer = QCompleter(
            uex_lookup.ship_names,
            parent=self.ship_input,
            caseSensitivity=Qt.CaseInsensitive,
            filterMode=Qt.MatchFlag.MatchContains,
            maxVisibleItems=10,
        )
        self.ship_input.setCompleter(self.ship_completer)
        self.ship_input.setClearButtonEnabled(True)
        # self.ship_input.installEventFilter(CompleterFocusFilter(parent=self.ship_input))

        self.cargo_input = QSpinBox(minimum=0, maximum=3000, suffix=" SCU")

        self.commodity_input = QLineEdit(placeholderText="Search commodity...")
        self.commodity_completer = QCompleter(
            uex_lookup.commodity_names,
            parent=self.commodity_input,
            caseSensitivity=Qt.CaseInsensitive,
            maxVisibleItems=10,
        )
        self.commodity_input.setCompleter(self.commodity_completer)
        self.commodity_input.setClearButtonEnabled(True)
        # self.commodity_input.installEventFilter(CompleterFocusFilter(parent=self.commodity_input))

        self._main_layout.addWidget(self._card(
            "Ship & Cargo", "cargo", "neutral",
            self._field_column("Ship", self.ship_input),
            self._field_column("Cargo", self.cargo_input),
            self._field_column("Commodity", self.commodity_input),
        ))

        # --- Source ---
        self.source_terminal_input = BreadcrumbLineEdit(placeholderText="Terminal, system or planet...")
        self.source_terminal_completer = QCompleter(
            self.current_source_candidates,
            parent=self.source_terminal_input,
            caseSensitivity=Qt.CaseInsensitive,
            filterMode=Qt.MatchFlag.MatchContains,
            maxVisibleItems=10,
        )
        self.source_terminal_input.setCompleter(self.source_terminal_completer)
        self.source_terminal_input.setClearButtonEnabled(True)
        # self.source_terminal_input.installEventFilter(CompleterFocusFilter(parent=self.source_terminal_input))

        self.min_source_inventory_input = QComboBox()
        self.min_source_inventory_input.addItems(["Select..."] + uex_lookup.SOURCE_INVENTORY_LEVELS)

        self._main_layout.addWidget(self._card(
            "Source", "pin", "accent",
            self._field_column("Terminal", self.source_terminal_input),
            self._field_column("Min inventory", self.min_source_inventory_input),
        ))
        self._main_layout.addWidget(self._route_connector())

        # --- Destination ---
        self.destination_terminal_input = BreadcrumbLineEdit(placeholderText="Terminal, system or planet...")
        self.destination_terminal_completer = QCompleter(
            self.current_destination_candidates,
            parent=self.destination_terminal_input,
            caseSensitivity=Qt.CaseInsensitive,
            filterMode=Qt.MatchFlag.MatchContains,
            maxVisibleItems=10,
        )
        self.destination_terminal_input.setCompleter(self.destination_terminal_completer)
        self.destination_terminal_input.setClearButtonEnabled(True)
        # self.destination_terminal_input.installEventFilter(
        #     CompleterFocusFilter(parent=self.destination_terminal_input)
        # )

        self.max_destination_inventory_input = QComboBox()
        self.max_destination_inventory_input.addItems(["Select..."] + uex_lookup.DESTINATION_INVENTORY_LEVELS)

        self._main_layout.addWidget(self._card(
            "Destination", "pin", "accent",
            self._field_column("Terminal", self.destination_terminal_input),
            self._field_column("Max inventory", self.max_destination_inventory_input),
        ))

        # --- Options ---
        options_row = QHBoxLayout()
        self.space_only_filter_toggle = QPushButton(parent=self, objectName="filterIconToggle")
        self.space_only_filter_toggle.setCheckable(True)
        self.space_only_filter_toggle.setIcon(theme.load_icon("station", theme.TEXT_DISABLED))
        self.space_only_filter_toggle.setIconSize(QSize(16, 16))
        self.space_only_filter_toggle.setToolTip("Space stations only")

        self.autoload_filter_toggle = QPushButton(parent=self, objectName="filterIconToggle")
        self.autoload_filter_toggle.setCheckable(True)
        self.autoload_filter_toggle.setIcon(theme.load_icon("loader", theme.TEXT_DISABLED))
        self.autoload_filter_toggle.setIconSize(QSize(16, 16))
        self.autoload_filter_toggle.setToolTip("Autoload-capable terminals only")

        self.nqa_filter_toggle = QPushButton(parent=self, objectName="filterIconToggle")
        self.nqa_filter_toggle.setCheckable(True)
        self.nqa_filter_toggle.setIcon(theme.load_icon("mask", theme.TEXT_DISABLED))
        self.nqa_filter_toggle.setIconSize(QSize(16, 16))
        self.nqa_filter_toggle.setToolTip("No-questions-asked terminals only (sells hot cargo)")

        options_row.addWidget(self.space_only_filter_toggle)
        options_row.addWidget(self.autoload_filter_toggle)
        options_row.addWidget(self.nqa_filter_toggle)
        options_row.addStretch()

        self.reset_button = QPushButton(parent=self, text="Reset")
        self.search_button = QPushButton(parent=self, text="Search", objectName="primaryButton")
        options_row.addWidget(self.reset_button)
        options_row.addWidget(self.search_button)

        self._main_layout.addLayout(options_row)

    def _wire_signals(self):
        self.ship_completer.activated[str].connect(self._on_ship_selected)

        self.commodity_completer.activated[str].connect(lambda _: self.refresh_filters())
        self.source_terminal_completer.activated[str].connect(lambda _: self.refresh_filters())
        self.destination_terminal_completer.activated[str].connect(lambda _: self.refresh_filters())
        self.space_only_filter_toggle.toggled.connect(
            lambda checked: self._on_toggle_changed(self.space_only_filter_toggle, "station", checked)
        )
        self.space_only_filter_toggle.toggled.connect(lambda _: self.refresh_filters())
        self.autoload_filter_toggle.toggled.connect(
            lambda checked: self._on_toggle_changed(self.autoload_filter_toggle, "loader", checked)
        )
        self.nqa_filter_toggle.toggled.connect(
            lambda checked: self._on_toggle_changed(self.nqa_filter_toggle, "mask", checked)
        )

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

    def _on_toggle_changed(self, button, icon_name, checked):
        # QSS can't retint a QIcon the way it recolors text, so the accent/disabled swap
        # has to happen here rather than via a :checked selector.
        color = theme.ACCENT if checked else theme.TEXT_DISABLED
        button.setIcon(theme.load_icon(icon_name, color))

    def _on_ship_selected(self, name):
        vehicle = find_vehicle(name)
        if vehicle is not None:
            self.cargo_input.setValue(int(vehicle.scu))

    @staticmethod
    def _apply_validation_style(field, valid_names, reachable_names=None, breadcrumb_for=None):
        text = field.text()
        breadcrumb = ""

        if not text:
            style, tooltip = "", ""
        elif text in valid_names:
            style = ""
            breadcrumb = breadcrumb_for(text) if breadcrumb_for else ""
            tooltip = breadcrumb
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
        if hasattr(field, "set_breadcrumb"):
            field.set_breadcrumb(breadcrumb)

    # --- Smart filtering: each of Commodity/Source/Destination narrows the other
    # two's completer suggestions, based on the last *selected* (not typed) value.

    @staticmethod
    def _scope_ids(text):
        """Terminal ids a Source/Destination field stands for — the one terminal it names,
        or every terminal in the system/planet/moon/orbit it names. None if unresolved."""
        terminal = find_terminal(text)
        if terminal is not None:
            return {terminal.id}
        location = find_location(text)
        return set(location.terminal_ids) if location is not None else None

    @staticmethod
    async def _commodity_ids_at_any(terminal_ids, side):
        results = await asyncio.gather(*(commodity_ids_at(terminal_id, side) for terminal_id in terminal_ids))
        return set().union(*results)

    async def _refresh_filters_async(self):
        commodity = find_commodity(self.commodity_input.text())
        source_ids = self._scope_ids(self.source_terminal_input.text())
        destination_ids = self._scope_ids(self.destination_terminal_input.text())

        # Commodity candidates: narrowed by whichever of source/destination are set.
        if source_ids is not None and destination_ids is not None:
            bought_at_source, sold_at_destination = await asyncio.gather(
                self._commodity_ids_at_any(source_ids, "buy"), self._commodity_ids_at_any(destination_ids, "sell")
            )
            commodity_ids = bought_at_source & sold_at_destination
            commodity_candidates = [c.name for c in uex_lookup.uex_cache.commodities if c.id in commodity_ids]
        elif source_ids is not None:
            commodity_ids = await self._commodity_ids_at_any(source_ids, "buy")
            commodity_candidates = [c.name for c in uex_lookup.uex_cache.commodities if c.id in commodity_ids]
        elif destination_ids is not None:
            commodity_ids = await self._commodity_ids_at_any(destination_ids, "sell")
            commodity_candidates = [c.name for c in uex_lookup.uex_cache.commodities if c.id in commodity_ids]
        else:
            commodity_candidates = list(uex_lookup.commodity_names)

        # Source candidates: direct from commodity, or fanned out from destination
        # (every terminal that sells anything the destination will buy) — fetched
        # concurrently, since a terminal can carry dozens of commodities.
        if commodity is not None:
            terminal_ids = await terminal_ids_for(commodity.id, "buy")
            source_reachable = [t.nickname for t in uex_lookup.uex_cache.terminals if t.id in terminal_ids]
        elif destination_ids is not None:
            reachable_commodity_ids = await self._commodity_ids_at_any(destination_ids, "sell")
            results = await asyncio.gather(*(terminal_ids_for(cid, "buy") for cid in reachable_commodity_ids))
            terminal_ids = set().union(*results)
            if len(destination_ids) == 1:  # a lone terminal can't be its own source; a region can
                terminal_ids -= destination_ids
            source_reachable = [t.nickname for t in uex_lookup.uex_cache.terminals if t.id in terminal_ids]
        else:
            source_reachable = list(uex_lookup.terminal_names)

        # Destination candidates: direct from commodity, or fanned out from source
        # (every terminal that buys anything the source sells), same concurrency.
        if commodity is not None:
            terminal_ids = await terminal_ids_for(commodity.id, "sell")
            destination_reachable = [t.nickname for t in uex_lookup.uex_cache.terminals if t.id in terminal_ids]
        elif source_ids is not None:
            available_commodity_ids = await self._commodity_ids_at_any(source_ids, "buy")
            results = await asyncio.gather(*(terminal_ids_for(cid, "sell") for cid in available_commodity_ids))
            terminal_ids = set().union(*results)
            if len(source_ids) == 1:
                terminal_ids -= source_ids
            destination_reachable = [t.nickname for t in uex_lookup.uex_cache.terminals if t.id in terminal_ids]
        else:
            destination_reachable = list(uex_lookup.terminal_names)

        # Space Only only narrows a field once something actually drives its reachable
        # set (commodity, or the other terminal) — with nothing set yet there's no
        # "missing location" to narrow against, so the completer stays unfiltered.
        space_only = self.space_only_filter_toggle.isChecked()
        # A field that already holds a resolved value is pinned, not still being
        # searched — narrowing (and thus space-only filtering) only makes sense for
        # whichever field is still open, same principle as search_routes' pinning.
        source_narrowed = source_ids is None and (commodity is not None or destination_ids is not None)
        destination_narrowed = destination_ids is None and (commodity is not None or source_ids is not None)

        if space_only and source_narrowed:
            source_candidates = [n for n in source_reachable if is_space_terminal(find_terminal(n))]
        else:
            source_candidates = source_reachable

        if space_only and destination_narrowed:
            destination_candidates = [n for n in destination_reachable if is_space_terminal(find_terminal(n))]
        else:
            destination_candidates = destination_reachable

        # Regions go on last: they're a way to name several terminals at once, so
        # narrowing by what a single terminal can reach doesn't apply to them.
        source_candidates = source_candidates + self._location_names
        destination_candidates = destination_candidates + self._location_names
        source_reachable = source_reachable + self._location_names
        destination_reachable = destination_reachable + self._location_names

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
        terminal = find_terminal(name)
        if terminal is not None:
            return terminal_breadcrumb(terminal)
        location = find_location(name)
        if location is not None:
            return f"{len(location.terminal_ids)} terminals"
        return ""

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
        # A system/planet/moon/orbit instead of a terminal: search every terminal in it.
        source_location = None if source_terminal else find_location(self.source_terminal_input.text())
        destination_location = None if destination_terminal else find_location(self.destination_terminal_input.text())

        if (commodity is None and source_terminal is None and destination_terminal is None
                and source_location is None and destination_location is None):
            # UEX's own API requires a commodity or a source terminal for a direct query
            # (verified live — destination alone 400s), but search_routes() now works
            # around that with a cached per-commodity fan-out, so destination-only is a
            # valid search from here — only reject when nothing at all is set.
            self.search_rejected.emit("Select a commodity, source, or destination to search.")
            return

        min_source_code = inventory_code_for(self.min_source_inventory_input.currentText(), "buy")
        max_destination_code = inventory_code_for(self.max_destination_inventory_input.currentText(), "sell")
        space_only = self.space_only_filter_toggle.isChecked()
        require_autoload = self.autoload_filter_toggle.isChecked()
        nqa_only = self.nqa_filter_toggle.isChecked()

        self.search_button.setEnabled(False)
        self.search_button.setText("SEARCHING…")
        try:
            routes = await search_routes(
                commodity_id=commodity.id if commodity else None,
                source_terminal_id=source_terminal.id if source_terminal else None,
                destination_terminal_id=destination_terminal.id if destination_terminal else None,
                source_scope_ids=set(source_location.terminal_ids) if source_location else None,
                destination_scope_ids=set(destination_location.terminal_ids) if destination_location else None,
                min_source_code=min_source_code,
                max_destination_code=max_destination_code,
                space_only=space_only,
                require_autoload=require_autoload,
                nqa_only=nqa_only,
            )
        except requests.exceptions.RequestException as exc:
            self.search_rejected.emit(f"Search failed — {exc}")
            return
        finally:
            self.search_button.setEnabled(True)
            self.search_button.setText("Search")

        self.routes_found.emit(routes, self.cargo_input.value(), self.ship_input.text())

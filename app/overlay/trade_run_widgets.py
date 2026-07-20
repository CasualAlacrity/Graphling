from PySide6.QtCore import QLocale, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from db.models import CargoTransferType
from overlay import uex_lookup
from overlay.results_panel import SortToggle

# aUEC has no fractional units, so every quantity/price field here is an integer —
# fixed to a US-style locale (not just the default constructor locale) so thousands
# always group with commas regardless of the host machine's system locale, which
# otherwise can silently switch the group/decimal separators (e.g. "1234,99").
_NUMBER_LOCALE = QLocale(QLocale.Language.English, QLocale.Country.UnitedStates)


def _integer_spinbox(minimum, maximum, suffix):
    spin_box = QSpinBox(minimum=minimum, maximum=maximum, suffix=suffix)
    spin_box.setLocale(_NUMBER_LOCALE)
    spin_box.setGroupSeparatorShown(True)
    return spin_box


class TravelWidget(QWidget):
    """Reused for both the depart (started_at) and arrive (reached_at) milestones — neither
    has data to capture, just a place to confirm against. The destination name/copy button
    matter at both points (set nav before departing, double-check on arrival), so both stay
    visible either way; only the confirm button's label depends on which field is next."""

    def __init__(self, leg, next_field, on_confirm, parent=None):
        super().__init__(parent)
        self._on_confirm = on_confirm

        terminal = uex_lookup.find_terminal_by_id(leg.terminal_id)
        place = uex_lookup.terminal_place_name(terminal)
        place_name, place_kind = place if place else (leg.terminal_name, "Terminal")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(QLabel(parent=self, text=f"Travel to {place_name}", objectName="dialogTitle"))
        if terminal is not None and terminal.planet_name:
            subtitle = f"{place_kind} on {terminal.planet_name} — {leg.terminal_name} terminal"
        else:
            subtitle = leg.terminal_name

        # Copy sits beside the subtitle it acts on, sized to its own content — a small
        # utility action, not a second full-width bar competing with the confirm button
        # for the same visual weight.
        subtitle_row = QHBoxLayout()
        subtitle_row.addWidget(QLabel(parent=self, text=subtitle, objectName="legDetail"), 1)
        copy_button = QPushButton(parent=self, text=f'Copy "{place_name}"', objectName="copyButton")
        copy_button.clicked.connect(lambda: self._copy_to_clipboard(place_name, copy_button))
        subtitle_row.addWidget(copy_button)
        layout.addLayout(subtitle_row)

        confirm_text = f"Departing for {place_name}" if next_field == "started_at" else f"I'm at {place_name}"
        confirm_button = QPushButton(parent=self, text=confirm_text, objectName="confirmButton")
        confirm_button.clicked.connect(self._on_confirm)
        layout.addWidget(confirm_button)

    @staticmethod
    def _copy_to_clipboard(text, button):
        QApplication.clipboard().setText(text)
        original_text = button.text()

        def _reset():
            button.setText(original_text)
            button.setProperty("copied", False)
            button.style().unpolish(button)
            button.style().polish(button)

        button.setText("Copied")
        button.setProperty("copied", True)
        button.style().unpolish(button)
        button.style().polish(button)
        QTimer.singleShot(1400, _reset)


class _TransactionWidget(QWidget):
    """Shared shape for Buy Cargo / Sell Cargo — quantity, price, a Manual/Autoload toggle
    with a conditional fee field, a live total, and a confirm button. Subclasses only differ
    in labels/copy and what "confirm" produces."""

    DIALOG_TITLE = ""
    QUANTITY_LABEL = ""
    PRICE_LABEL = ""
    FEE_LABEL = ""

    def __init__(self, leg, on_change, on_submit, parent=None):
        super().__init__(parent)
        self._leg = leg
        self._on_change = on_change
        self._on_submit = on_submit
        self._build_ui()
        self._wire_signals()
        self._recompute_total()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(QLabel(parent=self, text=self.DIALOG_TITLE, objectName="dialogTitle"))

        field_row = QHBoxLayout()
        self.quantity_input = _integer_spinbox(0, 100_000, " SCU")
        self.quantity_input.setValue(self._leg.quantity_scu)
        self.price_input = _integer_spinbox(0, 1_000_000, " aUEC")
        self.price_input.setValue(self._leg.price_per_unit)
        field_row.addWidget(self._field_column(self.QUANTITY_LABEL, self.quantity_input))
        field_row.addWidget(self._field_column(self.PRICE_LABEL, self.price_input))
        layout.addLayout(field_row)

        toggle_row = QHBoxLayout()
        self.manual_label = QLabel(parent=self, text="MANUAL", objectName="sortToggleLabel")
        self.toggle = SortToggle(parent=self)
        self.toggle.setChecked(self._leg.cargo_transfer_type == CargoTransferType.AUTOLOAD)
        self.auto_label = QLabel(parent=self, text="AUTOLOAD", objectName="sortToggleLabel")
        toggle_row.addWidget(self.manual_label)
        toggle_row.addWidget(self.toggle)
        toggle_row.addWidget(self.auto_label)
        toggle_row.addStretch()
        layout.addLayout(toggle_row)

        self.fee_input = _integer_spinbox(0, 1_000_000, " aUEC")
        self.fee_input.setValue(self._leg.cargo_transfer_fee)
        self.fee_row = self._field_column(self.FEE_LABEL, self.fee_input)
        layout.addWidget(self.fee_row)

        self.total_label = QLabel(parent=self, objectName="dialogTotal")
        layout.addWidget(self.total_label)

        self.confirm_button = QPushButton(parent=self, objectName="confirmButton")
        layout.addWidget(self.confirm_button)

        self._update_toggle_labels()
        self._update_fee_visibility()

    @staticmethod
    def _field_column(label_text, widget):
        column = QWidget()
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(QLabel(parent=column, text=label_text, objectName="dialogFieldLabel"))
        layout.addWidget(widget)
        return column

    def _wire_signals(self):
        self.quantity_input.valueChanged.connect(self._on_field_changed)
        self.price_input.valueChanged.connect(self._on_field_changed)
        self.fee_input.valueChanged.connect(self._on_field_changed)
        self.toggle.toggled.connect(self._on_toggle_changed)
        self.confirm_button.clicked.connect(self._on_confirm_clicked)

    def _on_toggle_changed(self, _checked):
        self._update_toggle_labels()
        self._update_fee_visibility()
        self._on_field_changed()

    def _update_toggle_labels(self):
        is_auto = self.toggle.isChecked()
        self.manual_label.setProperty("active", not is_auto)
        self.auto_label.setProperty("active", is_auto)
        for label in (self.manual_label, self.auto_label):
            label.style().unpolish(label)
            label.style().polish(label)

    def _update_fee_visibility(self):
        is_auto = self.toggle.isChecked()
        self.fee_row.setVisible(is_auto)
        if not is_auto:
            self.fee_input.setValue(0)

    def _current_values(self):
        return {
            "quantity_scu": self.quantity_input.value(),
            "price_per_unit": self.price_input.value(),
            "cargo_transfer_type": CargoTransferType.AUTOLOAD if self.toggle.isChecked() else CargoTransferType.MANUAL,
            "cargo_transfer_fee": self.fee_input.value(),
        }

    def _recompute_total(self):
        total = self.quantity_input.value() * self.price_input.value()
        self.total_label.setText(f"total {total:,} aUEC")

    def _on_field_changed(self, *_args):
        self._recompute_total()
        self._on_change(self._current_values())

    def _on_confirm_clicked(self):
        values = self._current_values()
        self._on_submit(
            values["quantity_scu"], values["price_per_unit"],
            values["cargo_transfer_type"], values["cargo_transfer_fee"],
        )


class BuyCargoWidget(_TransactionWidget):
    DIALOG_TITLE = "Buy cargo"
    QUANTITY_LABEL = "Quantity purchased"
    PRICE_LABEL = "Price paid"
    FEE_LABEL = "Autoload fee"

    def _build_ui(self):
        super()._build_ui()
        self.confirm_button.setText("Confirm purchase")


class SellCargoWidget(_TransactionWidget):
    DIALOG_TITLE = "Sell cargo"
    QUANTITY_LABEL = "Quantity sold"
    PRICE_LABEL = "Price received"
    FEE_LABEL = "Unload fee"

    def _build_ui(self):
        super()._build_ui()
        self._planned_quantity = self._leg.quantity_scu
        self._update_confirm_label()

    def _wire_signals(self):
        super()._wire_signals()
        self.quantity_input.valueChanged.connect(self._update_confirm_label)

    def _update_confirm_label(self, *_args):
        quantity = self.quantity_input.value()
        if quantity <= 0:
            self.confirm_button.setText("Confirm — nothing sold")
            self.confirm_button.setProperty("warning", True)
        elif quantity < self._planned_quantity:
            self.confirm_button.setText("Confirm partial sale")
            self.confirm_button.setProperty("warning", True)
        else:
            self.confirm_button.setText("Confirm sale")
            self.confirm_button.setProperty("warning", False)
        self.confirm_button.style().unpolish(self.confirm_button)
        self.confirm_button.style().polish(self.confirm_button)


class ConfirmLoadedWidget(QWidget):
    """Buy-side only — quantity/type/fee were already captured in Buy Cargo, this just
    confirms the physical load finished. No equivalent widget exists for the sale side."""

    def __init__(self, leg, on_confirm, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(QLabel(parent=self, text="Confirm loaded", objectName="dialogTitle"))
        if leg.cargo_transfer_type == CargoTransferType.AUTOLOAD:
            recap_text = f"Autoload — {leg.cargo_transfer_fee:,} aUEC fee recorded"
        else:
            recap_text = "Manual — no fee recorded"
        layout.addWidget(QLabel(parent=self, text=recap_text, objectName="legDetail"))

        confirm_button = QPushButton(parent=self, text="Cargo's Loaded", objectName="confirmButton")
        confirm_button.clicked.connect(on_confirm)
        layout.addWidget(confirm_button)


def _format_timestamp(value):
    return value.strftime("%H:%M") if value else "—"


def _transfer_recap(leg):
    if leg.transaction_completed_at is None:
        return "—"
    if leg.cargo_transfer_type == CargoTransferType.AUTOLOAD:
        return f"Autoload, {leg.cargo_transfer_fee:,} aUEC fee"
    return "Manual, no fee"


def build_recap_grid(leg):
    """Read-only summary for a leg that isn't currently actionable (finalized, or a future
    leg not yet reached) — answers "what did I buy this for" without touching the DB."""
    widget = QWidget()
    layout = QGridLayout(widget)
    layout.setContentsMargins(0, 4, 0, 4)
    layout.setHorizontalSpacing(20)
    layout.setVerticalSpacing(6)

    transaction_text = (
        f"{leg.quantity_scu} SCU @ {leg.price_per_unit:,} aUEC" if leg.transaction_completed_at else "—"
    )
    entries = [
        ("Departed", _format_timestamp(leg.started_at)),
        ("Arrived", _format_timestamp(leg.reached_at)),
        ("Transaction", transaction_text),
        ("Transfer", _transfer_recap(leg)),
        ("Finalized", _format_timestamp(leg.finalized_at)),
    ]
    for index, (label_text, value_text) in enumerate(entries):
        row, column = divmod(index, 2)
        cell = QWidget()
        cell_layout = QVBoxLayout(cell)
        cell_layout.setContentsMargins(0, 0, 0, 0)
        cell_layout.setSpacing(1)
        cell_layout.addWidget(QLabel(parent=cell, text=label_text, objectName="recapLabel"))
        cell_layout.addWidget(QLabel(parent=cell, text=value_text, objectName="recapValue"))
        layout.addWidget(cell, row, column)

    return widget

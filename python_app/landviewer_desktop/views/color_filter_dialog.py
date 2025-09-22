"""Dialog that mirrors the React colour filter editor."""
from __future__ import annotations

from typing import List, Sequence, Tuple

from PySide6.QtCore import Qt, Signal, QRegularExpression
from PySide6.QtGui import QColor, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QColorDialog,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from landviewer_desktop.state import ColorFilterSetting


def _normalise_hex(value: str) -> str:
    """Return ``value`` formatted as ``#RRGGBB`` or raise ``ValueError``."""

    colour = value.strip().upper()
    if not colour:
        raise ValueError("Colour cannot be empty.")
    if not colour.startswith("#"):
        colour = "#" + colour
    if len(colour) != 7:
        raise ValueError("Colour must follow the #RRGGBB format.")
    valid_chars = set("0123456789ABCDEF")
    if any(char not in valid_chars for char in colour[1:]):
        raise ValueError("Colour must follow the #RRGGBB format.")
    return colour


class _ColorFilterRow(QWidget):
    """Single row containing controls for a colour filter."""

    remove_requested = Signal(object)

    def __init__(self, filter_setting: ColorFilterSetting, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._filter = ColorFilterSetting(filter_setting.color, filter_setting.tolerance)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self._colour_button = QPushButton()
        self._colour_button.setFixedSize(32, 32)
        self._colour_button.clicked.connect(self._choose_colour)
        layout.addWidget(self._colour_button)

        controls_column = QVBoxLayout()
        controls_column.setContentsMargins(0, 0, 0, 0)
        controls_column.setSpacing(6)

        hex_row = QHBoxLayout()
        hex_row.setContentsMargins(0, 0, 0, 0)
        hex_row.setSpacing(6)
        hex_label = QLabel("HEX")
        hex_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hex_row.addWidget(hex_label)

        self._hex_input = QLineEdit(self._filter.color)
        self._hex_input.setMaxLength(7)
        regex = QRegularExpression(r"^#?[0-9A-Fa-f]{0,6}$")
        self._hex_input.setValidator(QRegularExpressionValidator(regex, self._hex_input))
        self._hex_input.textEdited.connect(self._preview_from_text)
        self._hex_input.editingFinished.connect(self._sync_from_text)
        hex_row.addWidget(self._hex_input, stretch=1)
        controls_column.addLayout(hex_row)

        tolerance_row = QHBoxLayout()
        tolerance_row.setContentsMargins(0, 0, 0, 0)
        tolerance_row.setSpacing(6)
        tolerance_label = QLabel("Tolerance")
        tolerance_row.addWidget(tolerance_label)

        self._tolerance_slider = QSlider(Qt.Orientation.Horizontal)
        self._tolerance_slider.setRange(0, 100)
        self._tolerance_slider.setValue(max(0, min(self._filter.tolerance, 100)))
        self._tolerance_slider.valueChanged.connect(self._update_tolerance_display)
        tolerance_row.addWidget(self._tolerance_slider, stretch=1)

        self._tolerance_value = QLabel()
        self._tolerance_value.setMinimumWidth(24)
        self._tolerance_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        tolerance_row.addWidget(self._tolerance_value)
        controls_column.addLayout(tolerance_row)

        layout.addLayout(controls_column, stretch=1)

        self._remove_button = QPushButton("Remove")
        self._remove_button.clicked.connect(lambda: self.remove_requested.emit(self))
        layout.addWidget(self._remove_button)

        self._update_colour_button()
        self._update_tolerance_display(self._tolerance_slider.value())

    # ------------------------------------------------------------------
    def _choose_colour(self) -> None:
        colour = QColor(self._filter.color)
        chosen = QColorDialog.getColor(colour, self, "Select colour")
        if not chosen.isValid():
            return
        self._set_colour(chosen.name(QColor.NameFormat.HexRgb))

    def _set_colour(self, colour: str, *, update_field: bool = True) -> None:
        normalised = _normalise_hex(colour)
        self._filter.color = normalised
        if update_field:
            self._hex_input.blockSignals(True)
            self._hex_input.setText(normalised)
            self._hex_input.blockSignals(False)
        self._update_colour_button()

    def _update_colour_button(self) -> None:
        self._colour_button.setStyleSheet(
            f"background-color: {self._filter.color}; border: 1px solid #1f2937; border-radius: 6px;"
        )

    def _preview_from_text(self, text: str) -> None:
        candidate = text.strip()
        if not candidate:
            return
        if not candidate.startswith("#"):
            candidate = "#" + candidate
        if len(candidate) == 7:
            try:
                normalised = _normalise_hex(candidate)
            except ValueError:
                return
            self._update_colour_button_from_preview(normalised)

    def _update_colour_button_from_preview(self, colour: str) -> None:
        self._colour_button.setStyleSheet(
            f"background-color: {colour}; border: 1px solid #1f2937; border-radius: 6px;"
        )

    def _sync_from_text(self) -> None:
            try:
                self._set_colour(self._hex_input.text(), update_field=True)
            except ValueError:
                # Revert to the last valid value if parsing failed.
                self._hex_input.blockSignals(True)
                self._hex_input.setText(self._filter.color)
                self._hex_input.blockSignals(False)
                self._update_colour_button()

    def _update_tolerance_display(self, value: int) -> None:
        clamped = max(0, min(int(value), 100))
        self._filter.tolerance = clamped
        self._tolerance_value.setText(str(clamped))

    # ------------------------------------------------------------------
    def to_filter(self) -> ColorFilterSetting:
        colour = _normalise_hex(self._hex_input.text())
        tolerance = max(0, min(self._tolerance_slider.value(), 100))
        return ColorFilterSetting(colour, tolerance)


class ColorFilterDialog(QDialog):
    """Modal dialog for editing keep/remove colour filters."""

    def __init__(
        self,
        keep_filters: Sequence[ColorFilterSetting],
        remove_filters: Sequence[ColorFilterSetting],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Advanced colour filters")
        self.setModal(True)
        self.resize(560, 540)

        self._keep_rows: List[_ColorFilterRow] = []
        self._remove_rows: List[_ColorFilterRow] = []
        self._keep_list_layout: QVBoxLayout | None = None
        self._remove_list_layout: QVBoxLayout | None = None
        self._result_keep: List[ColorFilterSetting] = []
        self._result_remove: List[ColorFilterSetting] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        intro = QLabel(
            "Select colours to keep or remove so only the cadastral lines remain. "
            "Keep filters recolour matches, while remove filters hide unwanted hues."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._keep_container = self._build_section(
            "Colours to keep", "Add colour to keep", self._keep_rows, keep_filters
        )
        layout.addWidget(self._keep_container)

        self._remove_container = self._build_section(
            "Colours to remove", "Add colour to remove", self._remove_rows, remove_filters
        )
        layout.addWidget(self._remove_container)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(12)
        button_row.addStretch(1)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(cancel_button)
        apply_button = QPushButton("Apply filters")
        apply_button.clicked.connect(self.accept)
        button_row.addWidget(apply_button)
        layout.addLayout(button_row)

        self._populate_initial_filters(self._keep_rows, keep_filters)
        self._populate_initial_filters(self._remove_rows, remove_filters)

    # ------------------------------------------------------------------
    def _build_section(
        self,
        title: str,
        button_text: str,
        rows: List[_ColorFilterRow],
        initial_filters: Sequence[ColorFilterSetting],
    ) -> QWidget:
        container = QWidget()
        section_layout = QVBoxLayout(container)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        title_label = QLabel(title)
        title_label.setObjectName("colorFilterSectionTitle")
        header.addWidget(title_label)
        header.addStretch(1)
        add_button = QPushButton(button_text)
        header.addWidget(add_button)
        section_layout.addLayout(header)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        list_widget = QWidget()
        list_layout = QVBoxLayout(list_widget)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(8)
        list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll_area.setWidget(list_widget)
        section_layout.addWidget(scroll_area, stretch=1)

        if rows is self._keep_rows:
            self._keep_list_layout = list_layout
        else:
            self._remove_list_layout = list_layout

        add_button.clicked.connect(lambda: self._add_row(rows, list_layout))

        if not initial_filters:
            # Provide a default row to encourage exploration.
            self._add_row(rows, list_layout)

        return container

    def _populate_initial_filters(
        self,
        rows: List[_ColorFilterRow],
        filters: Sequence[ColorFilterSetting],
    ) -> None:
        layout = self._keep_list_layout if rows is self._keep_rows else self._remove_list_layout
        if layout is None:
            return
        if filters:
            for filter_setting in filters:
                row = _ColorFilterRow(filter_setting, self)
                row.remove_requested.connect(lambda widget, r=row: self._remove_row(rows, layout, r))
                rows.append(row)
                layout.addWidget(row)

    def _add_row(self, rows: List[_ColorFilterRow], layout: QVBoxLayout) -> None:
        row = _ColorFilterRow(ColorFilterSetting(), self)
        row.remove_requested.connect(lambda widget, r=row: self._remove_row(rows, layout, r))
        rows.append(row)
        layout.addWidget(row)

    def _remove_row(
        self,
        rows: List[_ColorFilterRow],
        layout: QVBoxLayout,
        row: _ColorFilterRow,
    ) -> None:
        if row not in rows:
            return
        rows.remove(row)
        layout.removeWidget(row)
        row.setParent(None)
        row.deleteLater()

    # ------------------------------------------------------------------
    def accept(self) -> None:  # type: ignore[override]
        try:
            keep_filters = [row.to_filter() for row in self._keep_rows]
            remove_filters = [row.to_filter() for row in self._remove_rows]
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid colour", str(exc))
            return

        self._result_keep = keep_filters
        self._result_remove = remove_filters
        super().accept()

    # ------------------------------------------------------------------
    def filters(self) -> Tuple[List[ColorFilterSetting], List[ColorFilterSetting]]:
        """Return the keep/remove filters selected by the user."""

        return list(self._result_keep), list(self._result_remove)

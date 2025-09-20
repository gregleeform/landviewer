"""Upload screen implementation."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from landviewer_desktop.services import image_io
from landviewer_desktop.state import AppState
from landviewer_desktop.widgets.image_slot import ImageSlotWidget


class UploadView(QWidget):
    """Allows the user to pick the cadastral map and field photo."""

    proceed_requested = Signal()

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = state

        self._intro_label = QLabel(
            "Select the cadastral map and the field photograph to begin."
        )
        self._intro_label.setWordWrap(True)
        self._intro_label.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self._cadastral_slot = ImageSlotWidget(
            "Cadastral map",
            "Upload the cadastral (parcel) overlay image.",
        )
        self._photo_slot = ImageSlotWidget(
            "Field photo",
            "Upload the field photo that will receive the overlay.",
        )

        self._start_button = QPushButton("Start cropping")
        self._start_button.setEnabled(False)

        self._cadastral_slot.select_requested.connect(self._choose_cadastral)
        self._cadastral_slot.clear_requested.connect(self._clear_cadastral)
        self._photo_slot.select_requested.connect(self._choose_photo)
        self._photo_slot.clear_requested.connect(self._clear_photo)
        self._start_button.clicked.connect(self.proceed_requested.emit)

        layout = QVBoxLayout(self)
        layout.addWidget(self._intro_label)

        grid = QGridLayout()
        grid.addWidget(self._cadastral_slot, 0, 0)
        grid.addWidget(self._photo_slot, 0, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(12)
        layout.addLayout(grid)
        layout.addStretch(1)
        layout.addWidget(self._start_button)

        self.setLayout(layout)

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """Synchronise the widget state with ``AppState``."""
        cadastral = self._state.cadastral
        if cadastral.path and cadastral.image:
            if cadastral.cropped_image:
                display_image = cadastral.cropped_image
            else:
                display_image = image_io.rotate_image(
                    cadastral.image, cadastral.rotation
                )
            pixmap = image_io.image_to_qpixmap(display_image)
            filename = cadastral.path.name
            if cadastral.crop_rect:
                filename = f"{filename} (cropped)"
            self._cadastral_slot.set_preview(pixmap, filename)
        else:
            self._cadastral_slot.clear_preview()

        if self._state.photo.path and self._state.photo.image:
            pixmap = image_io.image_to_qpixmap(self._state.photo.image)
            self._photo_slot.set_preview(pixmap, self._state.photo.path.name)
        else:
            self._photo_slot.clear_preview()

        self._update_start_button()

    # ------------------------------------------------------------------
    def _choose_cadastral(self) -> None:
        self._handle_file_selection(
            "Select cadastral map",
            self._state.cadastral,
            self._cadastral_slot,
            allow_resize=False,
        )

    def _choose_photo(self) -> None:
        self._handle_file_selection(
            "Select field photo",
            self._state.photo,
            self._photo_slot,
            allow_resize=True,
        )

    def _clear_cadastral(self) -> None:
        self._state.cadastral.clear()
        self._cadastral_slot.clear_preview()
        self._update_start_button()

    def _clear_photo(self) -> None:
        self._state.photo.clear()
        self._photo_slot.clear_preview()
        self._update_start_button()

    # ------------------------------------------------------------------
    def _handle_file_selection(
        self,
        dialog_title: str,
        selection,
        slot_widget: ImageSlotWidget,
        *,
        allow_resize: bool,
    ) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            dialog_title,
            str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.webp *.tif *.tiff)",
        )
        if not file_path:
            return

        path = Path(file_path)
        try:
            image = image_io.load_image(path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self,
                "Failed to load image",
                f"{path.name} could not be opened as an image.\n\n{exc}",
            )
            return

        resized = False
        if allow_resize and image_io.should_suggest_resize(image):
            choice = QMessageBox.question(
                self,
                "Large image detected",
                "The selected photo is very large and may impact performance.\n"
                "Would you like to work with a resized copy instead?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if choice == QMessageBox.StandardButton.Yes:
                image = image_io.resized_copy(image)
                resized = True

        selection.path = path
        selection.image = image
        selection.resized_for_performance = resized
        selection.cropped_image = None
        selection.crop_rect = None
        selection.rotation = 0.0

        pixmap = image_io.image_to_qpixmap(image)
        slot_widget.set_preview(pixmap, path.name)
        self._update_start_button()

    def _update_start_button(self) -> None:
        ready = bool(self._state.cadastral.image and self._state.photo.image)
        self._start_button.setEnabled(ready)

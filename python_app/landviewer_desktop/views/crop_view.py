"""Interactive cropping workspace for the cadastral image."""
from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from PIL import Image

from landviewer_desktop.services import image_io
from landviewer_desktop.state import AppState


class CropGraphicsView(QGraphicsView):
    """QGraphicsView subclass that captures drag rectangles as crop selections."""

    selection_changed = Signal(QRectF)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setBackgroundBrush(QColor("#111827"))
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setMouseTracking(True)

        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._selection_item: QGraphicsRectItem | None = None
        self._current_selection = QRectF()
        self._drag_origin = QPointF()
        self._dragging = False

    # ------------------------------------------------------------------
    def set_pixmap(self, pixmap: Optional[QPixmap]) -> None:
        """Load the cadastral pixmap into the scene."""

        self._scene.clear()
        self._pixmap_item = None
        self._selection_item = None
        self._current_selection = QRectF()
        self.selection_changed.emit(QRectF())

        if pixmap and not pixmap.isNull():
            self._pixmap_item = self._scene.addPixmap(pixmap)
            self._pixmap_item.setZValue(0)
            self._scene.setSceneRect(self._pixmap_item.boundingRect())

            self._selection_item = QGraphicsRectItem()
            pen = QPen(QColor("#38bdf8"))
            pen.setWidth(2)
            self._selection_item.setPen(pen)
            selection_fill = QColor(56, 189, 248, 50)
            self._selection_item.setBrush(selection_fill)
            self._selection_item.setZValue(1)
            self._selection_item.setVisible(False)
            self._scene.addItem(self._selection_item)

            self._refit_view()

    def clear_selection(self) -> None:
        """Hide the active selection rectangle."""

        self._current_selection = QRectF()
        if self._selection_item:
            self._selection_item.setVisible(False)
        self.selection_changed.emit(QRectF())

    def set_selection_rect(self, rect: Optional[QRectF]) -> None:
        """Restore an existing selection rectangle."""

        if not self._pixmap_item or not self._selection_item:
            return

        if rect is None or rect.isNull() or rect.width() <= 0 or rect.height() <= 0:
            self.clear_selection()
            return

        bounds = self._pixmap_item.boundingRect()
        bounded = rect.intersected(bounds).normalized()
        if bounded.isEmpty():
            self.clear_selection()
            return

        self._selection_item.setRect(bounded)
        self._selection_item.setVisible(True)
        self._current_selection = bounded
        self.selection_changed.emit(bounded)

    def selection_rect(self) -> Optional[QRectF]:
        """Return the current selection or ``None`` if nothing is active."""

        if self._current_selection.isNull() or self._current_selection.width() <= 0:
            return None
        if self._current_selection.height() <= 0:
            return None
        return QRectF(self._current_selection)

    # ------------------------------------------------------------------
    def mousePressEvent(self, event):  # type: ignore[override]
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._pixmap_item is not None
        ):
            self._dragging = True
            self._drag_origin = self.mapToScene(event.position().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # type: ignore[override]
        if self._dragging and self._pixmap_item is not None and self._selection_item:
            current = self.mapToScene(event.position().toPoint())
            self._update_selection_from_points(self._drag_origin, current)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            if self._pixmap_item is not None and self._selection_item:
                current = self.mapToScene(event.position().toPoint())
                self._update_selection_from_points(self._drag_origin, current)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):  # type: ignore[override]
        if event.key() == Qt.Key.Key_Escape:
            self.clear_selection()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):  # type: ignore[override]
        super().resizeEvent(event)
        self._refit_view()

    # ------------------------------------------------------------------
    def _update_selection_from_points(
        self, start: QPointF, end: QPointF, *, emit_signal: bool = True
    ) -> None:
        if not self._pixmap_item or not self._selection_item:
            return

        bounds = self._pixmap_item.boundingRect()

        x1 = min(max(start.x(), bounds.left()), bounds.right())
        y1 = min(max(start.y(), bounds.top()), bounds.bottom())
        x2 = min(max(end.x(), bounds.left()), bounds.right())
        y2 = min(max(end.y(), bounds.top()), bounds.bottom())

        rect = QRectF(QPointF(x1, y1), QPointF(x2, y2)).normalized()

        if rect.width() < 1 or rect.height() < 1:
            self._selection_item.setVisible(False)
            self._current_selection = QRectF()
            if emit_signal:
                self.selection_changed.emit(QRectF())
            return

        self._selection_item.setRect(rect)
        self._selection_item.setVisible(True)
        self._current_selection = rect
        if emit_signal:
            self.selection_changed.emit(rect)

    def _refit_view(self) -> None:
        if self._pixmap_item:
            self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)


class CropView(QWidget):
    """Interactive crop workflow mirroring the web application's behaviour."""

    back_requested = Signal()
    proceed_requested = Signal()

    _INSTRUCTION_TEMPLATE = (
        "Drag a rectangle over the cadastral map to focus on the parcel area. "
        "Selections must be at least {min_size} × {min_size} pixels."
    )

    MIN_CROP_PIXELS = 128

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = state
        self._current_rotated_image: Optional[Image.Image] = None

        self._instruction_label = QLabel(
            self._INSTRUCTION_TEMPLATE.format(min_size=self.MIN_CROP_PIXELS)
        )
        self._instruction_label.setWordWrap(True)

        self._selection_label = QLabel(
            f"Draw a selection (min {self.MIN_CROP_PIXELS} × {self.MIN_CROP_PIXELS}px)."
        )
        self._selection_label.setObjectName("cropSelectionLabel")

        self._view = CropGraphicsView()
        self._view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._view.selection_changed.connect(self._handle_selection_changed)

        self._back_button = QPushButton("Back to upload")
        self._reset_button = QPushButton("Clear selection")
        self._confirm_button = QPushButton("Use selection")
        self._confirm_button.setEnabled(False)

        self._rotation_slider = QSlider(Qt.Orientation.Horizontal)
        self._rotation_slider.setRange(-180, 180)
        self._rotation_slider.setSingleStep(1)
        self._rotation_slider.setPageStep(10)
        self._rotation_slider.setTickInterval(15)
        self._rotation_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._rotation_slider.setEnabled(False)
        self._rotation_slider.setFixedWidth(260)
        self._rotation_value_label = QLabel("+0°")
        self._rotation_value_label.setObjectName("cropRotationValueLabel")

        self._back_button.clicked.connect(self.back_requested.emit)
        self._reset_button.clicked.connect(self._view.clear_selection)
        self._confirm_button.clicked.connect(self._commit_crop)
        self._rotation_slider.valueChanged.connect(self._rotation_slider_changed)

        layout = QVBoxLayout(self)
        layout.addWidget(self._instruction_label)
        layout.addWidget(self._view, stretch=1)
        layout.addSpacing(8)
        rotation_row = QHBoxLayout()
        rotation_caption = QLabel("Rotation")
        rotation_row.addWidget(rotation_caption)
        rotation_row.addWidget(self._rotation_slider)
        rotation_row.addWidget(self._rotation_value_label)
        rotation_row.addStretch(1)
        layout.addLayout(rotation_row)
        layout.addSpacing(8)
        layout.addWidget(self._selection_label)

        button_row = QHBoxLayout()
        button_row.addWidget(self._back_button)
        button_row.addWidget(self._reset_button)
        button_row.addStretch(1)
        button_row.addWidget(self._confirm_button)
        layout.addLayout(button_row)

        self.setLayout(layout)

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """Load the latest cadastral image and selection from state."""

        cadastral = self._state.cadastral
        image = cadastral.image

        if image is None:
            self._view.set_pixmap(None)
            self._view.setEnabled(False)
            self._confirm_button.setEnabled(False)
            self._current_rotated_image = None
            self._selection_label.setText(
                "Upload a cadastral map on the previous screen to crop it."
            )
            self._rotation_slider.blockSignals(True)
            self._rotation_slider.setValue(0)
            self._rotation_slider.blockSignals(False)
            self._rotation_slider.setEnabled(False)
            self._rotation_value_label.setText("+0°")
            return

        rotation_degrees = float(cadastral.rotation)
        rotated_image = image_io.rotate_image(image, rotation_degrees)
        self._current_rotated_image = rotated_image
        pixmap = image_io.image_to_qpixmap(rotated_image)
        self._view.setEnabled(True)
        self._view.set_pixmap(pixmap)
        slider_value = int(round(rotation_degrees))
        self._rotation_slider.blockSignals(True)
        self._rotation_slider.setValue(slider_value)
        self._rotation_slider.blockSignals(False)
        self._rotation_slider.setEnabled(True)
        self._rotation_value_label.setText(f"{slider_value:+d}°")

        if cadastral.crop_rect:
            left, top, right, bottom = cadastral.crop_rect
            rect = QRectF(left, top, right - left, bottom - top)
            self._view.set_selection_rect(rect)
        else:
            self._view.clear_selection()

        current_rect = self._view.selection_rect()
        self._handle_selection_changed(current_rect or QRectF())

    # ------------------------------------------------------------------
    def _handle_selection_changed(self, rect: QRectF) -> None:
        width = int(rect.width())
        height = int(rect.height())

        if width <= 0 or height <= 0:
            self._selection_label.setText(
                f"Draw a selection (min {self.MIN_CROP_PIXELS} × {self.MIN_CROP_PIXELS}px)."
            )
            self._confirm_button.setEnabled(False)
            return

        if width < self.MIN_CROP_PIXELS or height < self.MIN_CROP_PIXELS:
            self._selection_label.setText(
                f"Selection: {width} × {height}px — expand to at least "
                f"{self.MIN_CROP_PIXELS} × {self.MIN_CROP_PIXELS}px."
            )
            self._confirm_button.setEnabled(False)
            return

        self._selection_label.setText(f"Selection: {width} × {height}px")
        self._confirm_button.setEnabled(True)

    def _rotation_slider_changed(self, value: int) -> None:
        self._rotation_value_label.setText(f"{value:+d}°")
        selection = self._state.cadastral
        if selection.image is None:
            return

        degrees = float(value)
        if math.isclose(selection.rotation, degrees, abs_tol=0.01):
            return

        selection.rotation = degrees
        selection.cropped_image = None
        selection.crop_rect = None
        self._current_rotated_image = None
        self._view.clear_selection()
        self._state.overlay.clear_alignment()
        self.refresh()

    def _commit_crop(self) -> None:
        """Persist the selected crop to ``AppState`` and advance to the editor."""

        rect = self._view.selection_rect()
        base_image = self._state.cadastral.image

        if rect is None or base_image is None:
            return

        left = max(0, math.floor(rect.left()))
        top = max(0, math.floor(rect.top()))
        rotation = self._state.cadastral.rotation
        rotated_image = self._current_rotated_image or image_io.rotate_image(
            base_image, rotation
        )
        max_width, max_height = rotated_image.size
        right = min(max_width, math.ceil(rect.right()))
        bottom = min(max_height, math.ceil(rect.bottom()))

        width = right - left
        height = bottom - top
        if width < self.MIN_CROP_PIXELS or height < self.MIN_CROP_PIXELS:
            return

        cropped = rotated_image.crop((left, top, right, bottom))
        self._state.cadastral.cropped_image = cropped
        self._state.cadastral.crop_rect = (left, top, right, bottom)
        self._state.overlay.clear_alignment()

        self.proceed_requested.emit()

"""Editor view implementing manual overlay alignment for the prototype."""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np
from PIL import Image
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSlider,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from landviewer_desktop.services import image_io
from landviewer_desktop.state import AppState


class OverlayHandle(QObject, QGraphicsEllipseItem):
    """Draggable handle that clamps to the photo bounds."""

    moved = Signal(int, QPointF)

    def __init__(self, index: int, bounds: QRectF, parent: Optional[QGraphicsItem] = None) -> None:
        QObject.__init__(self)
        QGraphicsEllipseItem.__init__(self, parent)
        self._index = index
        self._bounds = bounds

        radius = 9.0
        self.setRect(-radius, -radius, radius * 2, radius * 2)
        self.setBrush(QColor("#38bdf8"))
        self.setPen(QPen(QColor("#0f172a"), 1.5))
        self.setZValue(3)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)

    # ------------------------------------------------------------------
    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):  # type: ignore[override]
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            pos = value
            if isinstance(pos, QPointF):
                x = min(max(pos.x(), self._bounds.left()), self._bounds.right())
                y = min(max(pos.y(), self._bounds.top()), self._bounds.bottom())
                return QPointF(x, y)
        elif change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.moved.emit(self._index, self.pos())
        return QGraphicsEllipseItem.itemChange(self, change, value)

    def mousePressEvent(self, event):  # type: ignore[override]
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        QGraphicsEllipseItem.mousePressEvent(self, event)

    def mouseReleaseEvent(self, event):  # type: ignore[override]
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        QGraphicsEllipseItem.mouseReleaseEvent(self, event)


class EditorGraphicsView(QGraphicsView):
    """Renders the field photo with the warped cadastral overlay."""

    points_changed = Signal(list)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setBackgroundBrush(QColor("#0b1120"))
        self.setDragMode(QGraphicsView.DragMode.NoDrag)

        self._photo_item: Optional[QGraphicsPixmapItem] = None
        self._overlay_item: Optional[QGraphicsPixmapItem] = None
        self._polygon_item: Optional[QGraphicsPathItem] = None
        self._handles: List[OverlayHandle] = []
        self._overlay_image: Optional[Image.Image] = None
        self._overlay_array: Optional[np.ndarray] = None
        self._src_points: Optional[np.ndarray] = None
        self._manual_points: List[QPointF] = []
        self._default_points: Optional[List[QPointF]] = None
        self._overlay_visible: bool = True
        self._overlay_opacity: float = 0.65
        self._suppress_point_updates = False

    # ------------------------------------------------------------------
    def clear(self) -> None:
        """Remove all scene items."""

        self._scene.clear()
        self._photo_item = None
        self._overlay_item = None
        self._polygon_item = None
        self._handles = []
        self._overlay_image = None
        self._overlay_array = None
        self._src_points = None
        self._manual_points = []
        self._default_points = None

    def load_images(
        self,
        photo_pixmap,
        overlay_image: Optional[Image.Image],
        manual_points: Optional[Sequence[Tuple[float, float]] | Sequence[QPointF]] = None,
    ) -> None:
        """Populate the scene with the provided images and restore handles."""

        self.clear()

        if photo_pixmap is None or photo_pixmap.isNull():
            return

        self._photo_item = self._scene.addPixmap(photo_pixmap)
        self._photo_item.setZValue(0)
        self._scene.setSceneRect(self._photo_item.boundingRect())

        if overlay_image is None:
            self._refit_view()
            return

        self._overlay_image = overlay_image
        self._overlay_array = np.array(overlay_image.convert("RGBA"))
        height, width = self._overlay_array.shape[:2]
        self._src_points = np.array(
            [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
            dtype=np.float32,
        )

        self._overlay_item = QGraphicsPixmapItem()
        self._overlay_item.setZValue(1)
        self._overlay_item.setVisible(False)
        self._scene.addItem(self._overlay_item)

        pen = QPen(QColor("#38bdf8"))
        pen.setWidth(2)
        pen.setCosmetic(True)
        self._polygon_item = QGraphicsPathItem()
        self._polygon_item.setPen(pen)
        self._polygon_item.setZValue(2)
        self._polygon_item.setVisible(False)
        self._scene.addItem(self._polygon_item)

        emit_points_after_load = True
        if manual_points and len(manual_points) == 4:
            points = [self._to_point(point) for point in manual_points]
            emit_points_after_load = False
        else:
            points = self._compute_default_points()
        if not points:
            self._refit_view()
            return

        bounds = self._photo_item.boundingRect()
        self._handles = []
        for index, point in enumerate(points):
            handle = OverlayHandle(index, bounds)
            handle.setPos(point)
            handle.moved.connect(self._handle_point_moved)
            self._scene.addItem(handle)
            self._handles.append(handle)

        self._manual_points = [QPointF(handle.pos()) for handle in self._handles]
        self._update_polygon()
        self._update_overlay_pixmap()

        if emit_points_after_load:
            self._emit_points()

        self._refit_view()

    def manual_points(self) -> List[QPointF]:
        """Return the currently tracked manual points."""

        return [QPointF(point) for point in self._manual_points]

    def reset_manual_points(self) -> None:
        """Restore handles to their default location."""

        if not self._handles:
            return

        defaults = self._compute_default_points()
        if not defaults:
            return

        self._set_points(defaults, emit=True)

    def set_overlay_settings(self, visible: bool, opacity: float) -> None:
        """Control overlay visibility and opacity."""

        self._overlay_visible = visible
        self._overlay_opacity = max(0.0, min(opacity, 1.0))
        self._update_overlay_pixmap()

    # ------------------------------------------------------------------
    def resizeEvent(self, event):  # type: ignore[override]
        super().resizeEvent(event)
        self._refit_view()

    # ------------------------------------------------------------------
    def _compute_default_points(self) -> Optional[List[QPointF]]:
        if not self._photo_item or self._overlay_image is None:
            return None

        photo_rect = self._photo_item.boundingRect()
        if photo_rect.width() <= 0 or photo_rect.height() <= 0:
            return None

        overlay_width, overlay_height = self._overlay_image.size
        if overlay_width <= 0 or overlay_height <= 0:
            return None

        scale = min(
            photo_rect.width() / overlay_width,
            photo_rect.height() / overlay_height,
        )
        scale *= 0.65
        scaled_width = overlay_width * scale
        scaled_height = overlay_height * scale

        left = photo_rect.left() + (photo_rect.width() - scaled_width) / 2
        top = photo_rect.top() + (photo_rect.height() - scaled_height) / 2
        right = left + scaled_width
        bottom = top + scaled_height

        points = [
            QPointF(left, top),
            QPointF(right, top),
            QPointF(right, bottom),
            QPointF(left, bottom),
        ]
        self._default_points = points
        return points

    def _emit_points(self) -> None:
        if len(self._manual_points) != 4:
            return
        serialised = [(point.x(), point.y()) for point in self._manual_points]
        self.points_changed.emit(serialised)

    def _handle_point_moved(self, index: int, position: QPointF) -> None:
        if self._suppress_point_updates:
            return
        if index < 0 or index >= len(self._manual_points):
            return

        self._manual_points[index] = QPointF(position)
        self._update_polygon()
        self._update_overlay_pixmap()
        self._emit_points()

    def _has_valid_polygon(self) -> bool:
        if len(self._manual_points) != 4:
            return False
        area = 0.0
        coords = [(point.x(), point.y()) for point in self._manual_points]
        for i in range(4):
            x1, y1 = coords[i]
            x2, y2 = coords[(i + 1) % 4]
            area += x1 * y2 - x2 * y1
        return abs(area) >= 1.0

    def _refit_view(self) -> None:
        if self._photo_item:
            self.fitInView(self._photo_item, Qt.AspectRatioMode.KeepAspectRatio)

    def _set_points(self, points: Sequence[QPointF], *, emit: bool) -> None:
        if not self._handles or len(points) != len(self._handles):
            return

        self._suppress_point_updates = True
        for handle, point in zip(self._handles, points):
            handle.setPos(point)
        self._suppress_point_updates = False

        self._manual_points = [QPointF(handle.pos()) for handle in self._handles]
        self._update_polygon()
        self._update_overlay_pixmap()

        if emit:
            self._emit_points()

    def _to_point(self, value) -> QPointF:
        if isinstance(value, QPointF):
            return QPointF(value)
        x, y = value  # type: ignore[misc]
        return QPointF(float(x), float(y))

    def _update_overlay_pixmap(self) -> None:
        if not self._overlay_item or self._overlay_array is None or self._src_points is None:
            return

        if not self._overlay_visible or not self._has_valid_polygon():
            self._overlay_item.setVisible(False)
            return

        if not self._photo_item:
            self._overlay_item.setVisible(False)
            return

        photo_rect = self._photo_item.boundingRect()
        width = int(round(photo_rect.width()))
        height = int(round(photo_rect.height()))
        if width <= 0 or height <= 0:
            self._overlay_item.setVisible(False)
            return

        dst = np.array([(point.x(), point.y()) for point in self._manual_points], dtype=np.float32)
        try:
            matrix = cv2.getPerspectiveTransform(self._src_points, dst)
            warped = cv2.warpPerspective(
                self._overlay_array,
                matrix,
                (width, height),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=(0, 0, 0, 0),
            )
        except cv2.error:
            self._overlay_item.setVisible(False)
            return

        result = warped
        if self._overlay_opacity < 1.0:
            alpha = result[..., 3].astype(np.float32)
            alpha *= self._overlay_opacity
            result = result.copy()
            result[..., 3] = np.clip(alpha, 0, 255).astype(np.uint8)

        pixmap = image_io.image_to_qpixmap(Image.fromarray(result, mode="RGBA"))
        self._overlay_item.setPixmap(pixmap)
        self._overlay_item.setOffset(0, 0)
        self._overlay_item.setVisible(True)

    def _update_polygon(self) -> None:
        if not self._polygon_item:
            return

        if len(self._manual_points) < 2:
            self._polygon_item.setVisible(False)
            self._polygon_item.setPath(QPainterPath())
            return

        path = QPainterPath(self._manual_points[0])
        for point in self._manual_points[1:]:
            path.lineTo(point)
        path.closeSubpath()
        self._polygon_item.setPath(path)
        self._polygon_item.setVisible(True)


class _OverlayPreviewCanvas(QWidget):
    """Static preview that renders the cadastral image with corner markers."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._pixmap = None
        self._image_size: Optional[Tuple[int, int]] = None
        self.setMinimumSize(220, 220)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def set_image(self, image: Optional[Image.Image]) -> None:
        if image is None:
            self._pixmap = None
            self._image_size = None
        else:
            self._pixmap = image_io.image_to_qpixmap(image)
            self._image_size = image.size
        self.update()

    # ------------------------------------------------------------------
    def paintEvent(self, event):  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        painter.fillRect(rect, QColor("#0f172a"))

        border_pen = QPen(QColor("#1f2937"))
        border_pen.setWidth(1)
        painter.setPen(border_pen)
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

        if not self._pixmap or not self._image_size:
            painter.setPen(QColor("#94a3b8"))
            painter.drawText(
                rect,
                Qt.AlignmentFlag.AlignCenter,
                "Overlay preview\nAwaiting crop",
            )
            return

        image_width, image_height = self._image_size
        if image_width <= 0 or image_height <= 0:
            return

        target = rect.adjusted(12, 12, -12, -12)
        if target.width() <= 0 or target.height() <= 0:
            return

        scale = min(target.width() / image_width, target.height() / image_height)
        scaled_width = image_width * scale
        scaled_height = image_height * scale
        left = target.left() + (target.width() - scaled_width) / 2.0
        top = target.top() + (target.height() - scaled_height) / 2.0

        dest_rect = QRectF(left, top, scaled_width, scaled_height)
        painter.drawPixmap(dest_rect, self._pixmap, QRectF(0, 0, image_width, image_height))

        corner_labels = ("1", "2", "3", "4")
        corners = (
            QPointF(dest_rect.left(), dest_rect.top()),
            QPointF(dest_rect.right(), dest_rect.top()),
            QPointF(dest_rect.right(), dest_rect.bottom()),
            QPointF(dest_rect.left(), dest_rect.bottom()),
        )

        handle_brush = QColor("#38bdf8")
        handle_pen = QPen(QColor("#0f172a"), 1.5)

        for label, point in zip(corner_labels, corners):
            marker_rect = QRectF(point.x() - 9.0, point.y() - 9.0, 18.0, 18.0)
            painter.setBrush(handle_brush)
            painter.setPen(handle_pen)
            painter.drawEllipse(marker_rect)
            painter.setPen(QPen(QColor("#0f172a")))
            painter.drawText(marker_rect, Qt.AlignmentFlag.AlignCenter, label)


class OverlayPreviewPanel(QWidget):
    """Container displaying the cadastral preview alongside guidance text."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._canvas = _OverlayPreviewCanvas()

        title = QLabel("Cadastral overlay preview")
        title.setObjectName("overlayPreviewTitle")

        helper = QLabel(
            "Corner markers show the manual pin order. Align handle 1 with the same "
            "corner on the field photo and continue clockwise."
        )
        helper.setWordWrap(True)
        helper.setObjectName("overlayPreviewHelper")

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(self._canvas, stretch=1)
        layout.addSpacing(8)
        layout.addWidget(helper)
        layout.addStretch(1)

        self.setLayout(layout)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setMinimumWidth(260)

    def set_overlay_image(self, image: Optional[Image.Image]) -> None:
        self._canvas.set_image(image)


class EditorView(QWidget):
    """Top-level widget that exposes manual overlay alignment controls."""

    restart_requested = Signal()

    def __init__(self, state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._state = state

        self._instruction_label = QLabel(
            "Manual pinning mode — drag the four handles to line up the cadastral overlay "
            "with the field photo. Adjust the overlay opacity to inspect alignment."
        )
        self._instruction_label.setWordWrap(True)

        self._view = EditorGraphicsView()
        self._view.points_changed.connect(self._handle_points_changed)

        self._preview_panel = OverlayPreviewPanel()

        self._image_info_label = QLabel("")
        self._image_info_label.setObjectName("editorInfoLabel")

        self._manual_radio = QRadioButton("Manual pinning")
        self._manual_radio.setChecked(True)
        self._manual_radio.setEnabled(False)
        self._auto_radio = QRadioButton("Automatic pinning (coming soon)")
        self._auto_radio.setEnabled(False)

        self._overlay_checkbox = QCheckBox("Show cadastral overlay")
        self._overlay_checkbox.toggled.connect(self._handle_overlay_visibility)

        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(0, 100)
        self._opacity_slider.setPageStep(5)
        self._opacity_slider.setValue(65)
        self._opacity_slider.valueChanged.connect(self._handle_opacity_changed)

        self._opacity_value_label = QLabel("65%")
        self._opacity_value_label.setObjectName("overlayOpacityValue")
        self._opacity_value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self._reset_pins_button = QPushButton("Reset pins")
        self._reset_pins_button.clicked.connect(self._view.reset_manual_points)

        self._restart_button = QPushButton("Start over")
        self._restart_button.clicked.connect(self.restart_requested.emit)

        layout = QVBoxLayout(self)
        layout.addWidget(self._instruction_label)

        view_row = QHBoxLayout()
        view_row.setContentsMargins(0, 0, 0, 0)
        view_row.addWidget(self._view, stretch=1)
        view_row.addSpacing(12)
        view_row.addWidget(self._preview_panel)
        layout.addLayout(view_row, stretch=1)
        layout.addSpacing(8)
        layout.addWidget(self._image_info_label)

        mode_row = QHBoxLayout()
        mode_row.addWidget(self._manual_radio)
        mode_row.addWidget(self._auto_radio)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        overlay_row = QHBoxLayout()
        overlay_row.addWidget(self._overlay_checkbox)
        overlay_row.addSpacing(12)
        overlay_row.addWidget(QLabel("Opacity"))
        overlay_row.addWidget(self._opacity_slider, stretch=1)
        overlay_row.addWidget(self._opacity_value_label)
        layout.addLayout(overlay_row)

        button_row = QHBoxLayout()
        button_row.addWidget(self._reset_pins_button)
        button_row.addStretch(1)
        button_row.addWidget(self._restart_button)
        layout.addLayout(button_row)

        self.setLayout(layout)

        self._set_controls_enabled(False)

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """Synchronise the editor with the latest application state."""

        photo = self._state.photo.image
        overlay = self._state.cadastral.cropped_image

        self._preview_panel.set_overlay_image(overlay)

        if photo is None or overlay is None:
            self._view.clear()
            self._view.setEnabled(False)
            self._set_controls_enabled(False)
            self._image_info_label.setText(
                "Upload a cadastral map and field photo, then crop the overlay to begin alignment."
            )
            return

        photo_pixmap = image_io.image_to_qpixmap(photo)
        manual_points = self._state.overlay.manual_points
        if manual_points:
            point_sequence: Optional[Sequence[Tuple[float, float]]] = manual_points
        else:
            point_sequence = None

        self._view.load_images(photo_pixmap, overlay, point_sequence)
        self._view.setEnabled(True)

        if self._state.overlay.manual_points is None:
            current_points = self._view.manual_points()
            if current_points:
                self._state.overlay.manual_points = tuple(
                    (point.x(), point.y()) for point in current_points
                )

        opacity = float(self._state.overlay.opacity)
        self._overlay_checkbox.blockSignals(True)
        self._overlay_checkbox.setChecked(self._state.overlay.show_overlay)
        self._overlay_checkbox.blockSignals(False)

        self._opacity_slider.blockSignals(True)
        self._opacity_slider.setValue(int(round(opacity * 100)))
        self._opacity_slider.blockSignals(False)
        self._update_opacity_label(opacity)

        self._view.set_overlay_settings(self._state.overlay.show_overlay, opacity)
        self._set_controls_enabled(True)

        self._image_info_label.setText(
            self._build_info_text(photo.size, overlay.size, self._state.photo.resized_for_performance)
        )

    # ------------------------------------------------------------------
    def _build_info_text(
        self,
        photo_size: Tuple[int, int],
        overlay_size: Tuple[int, int],
        resized: bool,
    ) -> str:
        photo_w, photo_h = photo_size
        overlay_w, overlay_h = overlay_size
        parts = [
            f"Photo: {photo_w} × {photo_h}px",
            f"Cadastral overlay: {overlay_w} × {overlay_h}px",
        ]
        if resized:
            parts.append("Working with resized photo")
        return " — ".join(parts)

    def _handle_opacity_changed(self, value: int) -> None:
        opacity = max(0.0, min(value / 100.0, 1.0))
        self._state.overlay.opacity = opacity
        self._view.set_overlay_settings(self._state.overlay.show_overlay, opacity)
        self._update_opacity_label(opacity)

    def _handle_overlay_visibility(self, checked: bool) -> None:
        self._state.overlay.show_overlay = checked
        self._view.set_overlay_settings(checked, self._state.overlay.opacity)

    def _handle_points_changed(self, points: list) -> None:
        if len(points) != 4:
            self._state.overlay.manual_points = None
            return
        self._state.overlay.manual_points = tuple((float(x), float(y)) for x, y in points)

    def _set_controls_enabled(self, enabled: bool) -> None:
        self._overlay_checkbox.setEnabled(enabled)
        self._opacity_slider.setEnabled(enabled)
        self._reset_pins_button.setEnabled(enabled)
        self._restart_button.setEnabled(True)
        self._manual_radio.setEnabled(False)
        self._auto_radio.setEnabled(False)

    def _update_opacity_label(self, opacity: float) -> None:
        percentage = int(round(opacity * 100))
        self._opacity_value_label.setText(f"{percentage}%")

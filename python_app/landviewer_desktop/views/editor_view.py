"""Editor view implementing manual overlay alignment for the prototype."""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np
from PIL import Image
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal, QThread
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsSimpleTextItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from landviewer_desktop.services import image_io, color_filters, line_uniformity
from landviewer_desktop.state import AppState, ColorFilterSetting
from landviewer_desktop.views.color_filter_dialog import ColorFilterDialog


class OverlayHandle(QObject, QGraphicsEllipseItem):
    """Draggable handle that optionally clamps to a bounding rectangle."""

    moved = Signal(int, QPointF)

    def __init__(
        self,
        index: int,
        bounds: Optional[QRectF],
        parent: Optional[QGraphicsItem] = None,
        *,
        radius: float = 9.0,
        fill_color: str = "#38bdf8",
        pen_color: str = "#0f172a",
        pen_width: float = 1.5,
    ) -> None:
        QObject.__init__(self)
        QGraphicsEllipseItem.__init__(self, parent)
        self._index = index
        self._bounds: Optional[QRectF] = QRectF(bounds) if bounds is not None else None

        self.setRect(-radius, -radius, radius * 2, radius * 2)
        self.setBrush(QColor(fill_color))
        pen = QPen(QColor(pen_color), pen_width)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setZValue(3)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)

    # ------------------------------------------------------------------
    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):  # type: ignore[override]
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            pos = value
            if isinstance(pos, QPointF) and self._bounds is not None:
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

    def update_bounds(self, bounds: Optional[QRectF]) -> None:
        """Change the clamp rectangle; ``None`` disables clamping."""

        self._bounds = QRectF(bounds) if bounds is not None else None


class _ColorFilterWorker(QObject):
    """Background worker that applies colour filters to the overlay image."""

    finished = Signal(int, object)
    failed = Signal(int, str)

    def __init__(
        self,
        token: int,
        image: Image.Image,
        keep_filters: Sequence[ColorFilterSetting],
        remove_filters: Sequence[ColorFilterSetting],
    ) -> None:
        super().__init__()
        self._token = token
        self._image = image.copy()
        self._keep = tuple(ColorFilterSetting(f.color, f.tolerance) for f in keep_filters)
        self._remove = tuple(ColorFilterSetting(f.color, f.tolerance) for f in remove_filters)

    def process(self) -> None:
        try:
            result = color_filters.apply_color_filters(self._image, self._keep, self._remove)
        except Exception as exc:  # pragma: no cover - defensive guard
            self.failed.emit(self._token, str(exc))
        else:
            self.finished.emit(self._token, result)


class EditorGraphicsView(QGraphicsView):
    """Renders the field photo with the warped cadastral overlay."""

    points_changed = Signal(list)
    photo_clicked = Signal(QPointF)
    auto_handles_changed = Signal(list)

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
        self._line_balance_strength: float = 0.5
        self._overlay_suppressed: bool = False
        self._suppress_point_updates = False
        self._handles_visible = True
        self._auto_click_enabled = False
        self._auto_markers: List[QGraphicsEllipseItem] = []
        self._handle_bounds: Optional[QRectF] = None
        self._handles_constrained = True
        self._auto_handles: List[OverlayHandle] = []
        self._auto_points: List[QPointF] = []
        self._auto_handles_visible = False
        self._suppress_auto_updates = False
        self._line_sketch: Optional[line_uniformity.LineSketch] = None
        self._line_cached_image: Optional[np.ndarray] = None
        self._line_cached_strength: Optional[float] = None

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
        self._auto_markers = []
        self._handles_visible = True
        self._auto_click_enabled = False
        self._handle_bounds = None
        self._handles_constrained = True
        self._clear_auto_handles()
        self._auto_points = []
        self._auto_handles_visible = False
        self._suppress_auto_updates = False
        self._overlay_suppressed = False
        self._line_sketch = None
        self._line_cached_image = None
        self._line_cached_strength = None

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

        self.update_overlay_image(overlay_image)

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
        self._handle_bounds = QRectF(bounds)
        constrain_handles = all(bounds.contains(point) for point in points)
        self._handles = []
        for index, point in enumerate(points):
            handle = OverlayHandle(index, bounds if constrain_handles else None)
            handle.setPos(point)
            handle.moved.connect(self._handle_point_moved)
            self._scene.addItem(handle)
            self._handles.append(handle)
        for handle in self._handles:
            handle.setVisible(self._handles_visible)

        self._handles_constrained = constrain_handles
        self._update_handle_bounds()

        self._manual_points = [QPointF(handle.pos()) for handle in self._handles]
        self._update_polygon()
        self._update_overlay_pixmap()

        if emit_points_after_load:
            self._emit_points()

        self._refit_view()

    def update_overlay_image(self, overlay_image: Optional[Image.Image]) -> None:
        """Update the cached overlay image and refresh the warped preview."""

        self._overlay_image = overlay_image
        if overlay_image is None:
            self._overlay_array = None
            self._src_points = None
            self._update_overlay_pixmap()
            return

        self._overlay_array = np.array(overlay_image.convert("RGBA"))
        height, width = self._overlay_array.shape[:2]
        self._src_points = np.array(
            [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
            dtype=np.float32,
        )
        try:
            self._line_sketch = line_uniformity.prepare_sketch(self._overlay_array)
        except cv2.error:
            self._line_sketch = None
        self._line_cached_image = None
        self._line_cached_strength = None
        self._update_overlay_pixmap()

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

        self._set_handle_constraints(True)
        self._set_points(defaults, emit=True)

    def set_overlay_settings(self, visible: bool, opacity: float) -> None:
        """Control overlay visibility and opacity."""

        self._overlay_visible = visible
        self._overlay_opacity = max(0.0, min(opacity, 1.0))
        self._update_overlay_pixmap()

    def set_line_balance_strength(self, strength: float) -> None:
        """Adjust the uniform line thickness enhancement strength."""

        clamped = max(0.0, min(strength, 1.0))
        if abs(self._line_balance_strength - clamped) < 1e-3:
            return
        self._line_balance_strength = clamped
        self._line_cached_strength = None
        self._update_overlay_pixmap()

    def set_overlay_suppressed(self, suppressed: bool) -> None:
        """Temporarily hide the overlay regardless of visibility settings."""

        if self._overlay_suppressed != suppressed:
            self._overlay_suppressed = suppressed
            self._update_overlay_pixmap()

    def set_handles_visible(self, visible: bool) -> None:
        """Show or hide the draggable handles."""

        self._handles_visible = visible
        for handle in self._handles:
            handle.setVisible(visible)
        self._update_polygon()

    def set_manual_points(
        self,
        points: Sequence[Tuple[float, float]] | Sequence[QPointF],
        *,
        allow_outside: bool = False,
    ) -> None:
        """Move handles to the supplied coordinates and emit updates."""

        if not self._handles or len(points) != len(self._handles):
            return

        qpoints = [self._to_point(point) for point in points]
        if allow_outside:
            self._set_handle_constraints(False)
        elif self._handle_bounds and all(self._handle_bounds.contains(p) for p in qpoints):
            self._set_handle_constraints(True)
        self._set_points(qpoints, emit=True)

    def set_auto_adjust_points(
        self, points: Sequence[Tuple[float, float]] | Sequence[QPointF]
    ) -> None:
        """Show draggable auto-pin handles for fine-tuning destination points."""

        qpoints = [self._to_point(point) for point in points]
        if not qpoints:
            self.clear_auto_adjustment()
            return

        if len(self._auto_handles) != len(qpoints):
            self._clear_auto_handles()
            for index in range(len(qpoints)):
                handle = OverlayHandle(
                    index,
                    None,
                    radius=8.0,
                    fill_color="#f97316",
                    pen_color="#0f172a",
                    pen_width=1.4,
                )
                handle.setZValue(3.5)
                handle.moved.connect(self._handle_auto_handle_moved)
                label = QGraphicsSimpleTextItem(str(index + 1))
                label.setBrush(QColor("#0f172a"))
                label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
                label.setParentItem(handle)
                label.setPos(-4.0, -6.0)
                self._scene.addItem(handle)
                self._auto_handles.append(handle)

        self._suppress_auto_updates = True
        for handle, point in zip(self._auto_handles, qpoints):
            handle.setPos(point)
            handle.setVisible(True)
        self._suppress_auto_updates = False

        self._auto_points = [QPointF(handle.pos()) for handle in self._auto_handles]
        self._auto_handles_visible = True

    def clear_auto_adjustment(self) -> None:
        """Remove auto-pin fine-tune handles from the scene."""

        self._clear_auto_handles()
        self._auto_points = []
        self._auto_handles_visible = False

    def auto_adjust_points(self) -> List[QPointF]:
        """Return the current auto-pin destination points."""

        return [QPointF(point) for point in self._auto_points]

    def set_auto_click_enabled(self, enabled: bool) -> None:
        """Enable or disable capture of photo clicks for auto pinning."""

        self._auto_click_enabled = enabled

    def set_auto_cursor(self, enabled: bool) -> None:
        """Toggle a crosshair cursor when awaiting destination clicks."""

        if enabled:
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.viewport().unsetCursor()

    def set_auto_markers(
        self, points: Sequence[Tuple[float, float]] | Sequence[QPointF]
    ) -> None:
        """Display numbered markers for auto pin destination points."""

        self._clear_auto_markers()
        if not self._photo_item:
            return

        for index, value in enumerate(points, start=1):
            point = self._to_point(value)
            radius = 7.0
            marker = QGraphicsEllipseItem(-radius, -radius, radius * 2, radius * 2)
            marker.setBrush(QColor("#f97316"))
            marker.setPen(QPen(QColor("#0f172a"), 1.2))
            marker.setZValue(2.5)
            marker.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
            marker.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            marker.setPos(point)
            self._scene.addItem(marker)
            self._auto_markers.append(marker)

            label = QGraphicsSimpleTextItem(str(index))
            label.setBrush(QColor("#0f172a"))
            label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
            label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            label.setParentItem(marker)
            label.setPos(-4.0, -6.0)

    # ------------------------------------------------------------------
    def mousePressEvent(self, event):  # type: ignore[override]
        if (
            self._auto_click_enabled
            and event.button() == Qt.MouseButton.LeftButton
            and self._photo_item is not None
        ):
            scene_pos = self.mapToScene(event.position().toPoint())
            item_pos = self._photo_item.mapFromScene(scene_pos)
            rect = self._photo_item.boundingRect()
            if rect.contains(item_pos):
                clamped_x = min(max(item_pos.x(), rect.left()), rect.right())
                clamped_y = min(max(item_pos.y(), rect.top()), rect.bottom())
                clamped_scene = self._photo_item.mapToScene(QPointF(clamped_x, clamped_y))
                self.photo_clicked.emit(clamped_scene)
                event.accept()
                return
        super().mousePressEvent(event)

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

    def _set_handle_constraints(self, constrained: bool) -> None:
        self._handles_constrained = constrained
        self._update_handle_bounds()

    def _update_handle_bounds(self) -> None:
        rect = self._handle_bounds if self._handles_constrained else None
        for handle in self._handles:
            handle.update_bounds(rect)

    def _handle_point_moved(self, index: int, position: QPointF) -> None:
        if self._suppress_point_updates:
            return
        if index < 0 or index >= len(self._manual_points):
            return

        self._manual_points[index] = QPointF(position)
        self._update_polygon()
        self._update_overlay_pixmap()
        self._emit_points()

    def _handle_auto_handle_moved(self, index: int, position: QPointF) -> None:
        if self._suppress_auto_updates:
            return
        if index < 0 or index >= len(self._auto_points):
            return

        self._auto_points[index] = QPointF(position)
        serialised = [(point.x(), point.y()) for point in self._auto_points]
        self.auto_handles_changed.emit(serialised)

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

    def _clear_auto_markers(self) -> None:
        for marker in self._auto_markers:
            self._scene.removeItem(marker)
        self._auto_markers = []

    def _clear_auto_handles(self) -> None:
        for handle in self._auto_handles:
            self._scene.removeItem(handle)
        self._auto_handles = []

    def _update_overlay_pixmap(self) -> None:
        if not self._overlay_item:
            return

        if self._overlay_array is None or self._src_points is None:
            self._overlay_item.setVisible(False)
            return

        if self._overlay_suppressed:
            self._overlay_item.setVisible(False)
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
                self._overlay_for_warp(),
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

        if not self._handles_visible:
            self._polygon_item.setVisible(False)
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

    def _overlay_for_warp(self) -> np.ndarray:
        if self._overlay_array is None:
            raise RuntimeError("overlay not loaded")

        if self._line_balance_strength <= 0.0 or self._line_sketch is None:
            return self._overlay_array

        strength = float(self._line_balance_strength)
        if (
            self._line_cached_image is not None
            and self._line_cached_strength is not None
            and abs(self._line_cached_strength - strength) < 1e-4
        ):
            return self._line_cached_image

        try:
            rendered = line_uniformity.render_uniform_overlay(
                self._overlay_array,
                self._line_sketch,
                strength,
            )
        except cv2.error:
            rendered = self._overlay_array
        else:
            rendered = np.ascontiguousarray(rendered)

        self._line_cached_image = rendered
        self._line_cached_strength = strength
        return rendered


class _OverlayPreviewCanvas(QWidget):
    """Static preview that renders the cadastral image with corner markers."""

    point_clicked = Signal(QPointF)
    auto_point_moved = Signal(int, QPointF)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._pixmap = None
        self._image_size: Optional[Tuple[int, int]] = None
        self._auto_points: List[QPointF] = []
        self._highlight_border = False
        self._show_corner_guides = True
        self._auto_editable = False
        self._drag_index: Optional[int] = None
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

    def set_auto_points(
        self,
        points: Sequence[QPointF],
        *,
        preserve_drag_index: bool = False,
    ) -> None:
        if preserve_drag_index:
            drag_index = self._drag_index
        else:
            drag_index = None

        self._auto_points = [QPointF(point) for point in points]

        if preserve_drag_index:
            if drag_index is not None and drag_index >= len(self._auto_points):
                drag_index = None
            self._drag_index = drag_index
        else:
            self._drag_index = None

        self.update()

    def set_highlighted(self, highlighted: bool) -> None:
        if self._highlight_border != highlighted:
            self._highlight_border = highlighted
            self.update()

    def set_corner_guides_visible(self, visible: bool) -> None:
        if self._show_corner_guides != visible:
            self._show_corner_guides = visible
            self.update()

    def set_auto_editable(self, editable: bool) -> None:
        if self._auto_editable != editable:
            self._auto_editable = editable
            if not editable:
                self._drag_index = None

    # ------------------------------------------------------------------
    def paintEvent(self, event):  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        painter.fillRect(rect, QColor("#0f172a"))

        border_color = "#38bdf8" if self._highlight_border else "#1f2937"
        border_pen = QPen(QColor(border_color))
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

        dest_rect = self._target_rect()
        if dest_rect is None:
            return
        painter.drawPixmap(dest_rect, self._pixmap, QRectF(0, 0, image_width, image_height))

        if self._show_corner_guides:
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

        if self._auto_points:
            marker_color = QColor("#f97316")
            pen = QPen(QColor("#0f172a"), 1.2)
            for index, point in enumerate(self._auto_points, start=1):
                px = dest_rect.left() + (point.x() / image_width) * dest_rect.width()
                py = dest_rect.top() + (point.y() / image_height) * dest_rect.height()
                marker_rect = QRectF(px - 7.0, py - 7.0, 14.0, 14.0)
                painter.setBrush(marker_color)
                painter.setPen(pen)
                painter.drawEllipse(marker_rect)
                painter.setPen(QPen(QColor("#0f172a")))
                painter.drawText(marker_rect, Qt.AlignmentFlag.AlignCenter, str(index))

    def mousePressEvent(self, event):  # type: ignore[override]
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        if not self._pixmap or not self._image_size:
            super().mousePressEvent(event)
            return

        target = self._target_rect()
        if target is None:
            super().mousePressEvent(event)
            return

        pos = event.position()
        if not target.contains(pos):
            super().mousePressEvent(event)
            return

        if self._auto_editable and self._auto_points:
            index = self._hit_test_auto_point(pos, target)
            if index is not None:
                self._drag_index = index
                self._update_drag_position(pos, emit=True)
                event.accept()
                return
            super().mousePressEvent(event)
            return

        if target.width() <= 0 or target.height() <= 0:
            super().mousePressEvent(event)
            return

        relative_x = (pos.x() - target.left()) / target.width()
        relative_y = (pos.y() - target.top()) / target.height()
        image_x = relative_x * self._image_size[0]
        image_y = relative_y * self._image_size[1]
        self.point_clicked.emit(QPointF(image_x, image_y))
        event.accept()

    def mouseMoveEvent(self, event):  # type: ignore[override]
        if (
            self._auto_editable
            and self._drag_index is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            self._update_drag_position(event.position(), emit=True)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # type: ignore[override]
        if self._auto_editable and self._drag_index is not None:
            self._update_drag_position(event.position(), emit=True)
            self._drag_index = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _hit_test_auto_point(self, pos: QPointF, target: QRectF) -> Optional[int]:
        if not self._image_size:
            return None

        image_width, image_height = self._image_size
        radius = 12.0
        for index, point in enumerate(self._auto_points):
            px = target.left() + (point.x() / image_width) * target.width()
            py = target.top() + (point.y() / image_height) * target.height()
            dx = pos.x() - px
            dy = pos.y() - py
            if dx * dx + dy * dy <= radius * radius:
                return index
        return None

    def _update_drag_position(self, pos: QPointF, *, emit: bool) -> None:
        if self._drag_index is None or not self._image_size:
            return

        target = self._target_rect()
        if target is None or target.width() <= 0 or target.height() <= 0:
            return

        clamped_x = min(max(pos.x(), target.left()), target.right())
        clamped_y = min(max(pos.y(), target.top()), target.bottom())
        relative_x = (clamped_x - target.left()) / target.width()
        relative_y = (clamped_y - target.top()) / target.height()
        image_x = relative_x * self._image_size[0]
        image_y = relative_y * self._image_size[1]

        point = QPointF(image_x, image_y)
        if 0 <= self._drag_index < len(self._auto_points):
            self._auto_points[self._drag_index] = point
            self.update()
            if emit:
                self.auto_point_moved.emit(self._drag_index, point)

    def _target_rect(self) -> Optional[QRectF]:
        if not self._image_size:
            return None

        rect = self.rect()
        target = rect.adjusted(12, 12, -12, -12)
        if target.width() <= 0 or target.height() <= 0:
            return None

        image_width, image_height = self._image_size
        if image_width <= 0 or image_height <= 0:
            return None

        scale = min(target.width() / image_width, target.height() / image_height)
        scaled_width = image_width * scale
        scaled_height = image_height * scale
        left = target.left() + (target.width() - scaled_width) / 2.0
        top = target.top() + (target.height() - scaled_height) / 2.0
        return QRectF(left, top, scaled_width, scaled_height)


class OverlayPreviewPanel(QWidget):
    """Container displaying the cadastral preview alongside guidance text."""

    point_clicked = Signal(QPointF)
    auto_point_moved = Signal(int, QPointF)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._canvas = _OverlayPreviewCanvas()
        self._canvas.point_clicked.connect(self.point_clicked)
        self._canvas.auto_point_moved.connect(self.auto_point_moved)

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

    def set_auto_points(
        self,
        points: Sequence[QPointF],
        *,
        preserve_drag: bool = False,
    ) -> None:
        self._canvas.set_auto_points(points, preserve_drag_index=preserve_drag)

    def set_highlighted(self, highlighted: bool) -> None:
        self._canvas.set_highlighted(highlighted)

    def set_corner_guides_visible(self, visible: bool) -> None:
        self._canvas.set_corner_guides_visible(visible)

    def set_auto_editable(self, editable: bool) -> None:
        self._canvas.set_auto_editable(editable)


class EditorView(QWidget):
    """Top-level widget that exposes manual overlay alignment controls."""

    restart_requested = Signal()

    _MANUAL_INSTRUCTION = (
        "Manual pinning mode — drag the four handles to line up the cadastral overlay "
        "with the field photo. Adjust the overlay opacity to inspect alignment."
    )
    _AUTO_CORNER_NAMES = (
        "corner 1 (top-left)",
        "corner 2 (top-right)",
        "corner 3 (bottom-right)",
        "corner 4 (bottom-left)",
    )

    def __init__(self, state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._state = state

        self._instruction_label = QLabel(self._MANUAL_INSTRUCTION)
        self._instruction_label.setWordWrap(True)

        self._view = EditorGraphicsView()
        self._view.points_changed.connect(self._handle_points_changed)
        self._view.photo_clicked.connect(self._handle_photo_clicked)
        self._view.auto_handles_changed.connect(self._handle_auto_dest_points_adjusted)

        self._preview_panel = OverlayPreviewPanel()
        self._preview_panel.point_clicked.connect(self._handle_preview_point_clicked)
        self._preview_panel.auto_point_moved.connect(self._handle_auto_source_point_adjusted)

        self._image_info_label = QLabel("")
        self._image_info_label.setObjectName("editorInfoLabel")

        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)

        self._manual_toggle = QPushButton("Manual pinning")
        self._manual_toggle.setCheckable(True)
        self._manual_toggle.setChecked(True)
        self._manual_toggle.toggled.connect(self._handle_manual_mode_selected)
        self._mode_group.addButton(self._manual_toggle)

        self._auto_toggle = QPushButton("Automatic pinning")
        self._auto_toggle.setCheckable(True)
        self._auto_toggle.toggled.connect(self._handle_auto_mode_selected)
        self._mode_group.addButton(self._auto_toggle)

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

        self._line_balance_slider = QSlider(Qt.Orientation.Horizontal)
        self._line_balance_slider.setRange(0, 100)
        self._line_balance_slider.setPageStep(5)
        self._line_balance_slider.setValue(50)
        self._line_balance_slider.valueChanged.connect(self._handle_line_balance_changed)

        self._line_balance_value_label = QLabel("50%")
        self._line_balance_value_label.setObjectName("overlayLineBalanceValue")
        self._line_balance_value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self._reset_pins_button = QPushButton("Reset pins")
        self._reset_pins_button.clicked.connect(self._handle_reset_pins)

        self._color_filter_button = QPushButton("Color filters…")
        self._color_filter_button.setEnabled(False)
        self._color_filter_button.clicked.connect(self._show_color_filter_dialog)

        self._status_label = QLabel("")
        self._status_label.setObjectName("editorStatusLabel")
        self._status_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._status_label.setMinimumWidth(160)
        self._status_label.hide()

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
        mode_row.addWidget(self._manual_toggle)
        mode_row.addWidget(self._auto_toggle)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        overlay_row = QHBoxLayout()
        overlay_row.addWidget(self._overlay_checkbox)
        overlay_row.addSpacing(12)
        overlay_row.addWidget(QLabel("Opacity"))
        overlay_row.addWidget(self._opacity_slider, stretch=1)
        overlay_row.addWidget(self._opacity_value_label)
        layout.addLayout(overlay_row)

        balance_row = QHBoxLayout()
        balance_row.addSpacing(12)
        balance_row.addWidget(QLabel("Line uniformity"))
        balance_row.addWidget(self._line_balance_slider, stretch=1)
        balance_row.addWidget(self._line_balance_value_label)
        layout.addLayout(balance_row)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        button_row.addWidget(self._color_filter_button)
        button_row.addWidget(self._reset_pins_button)
        button_row.addStretch(1)
        button_row.addWidget(self._status_label)
        button_row.addWidget(self._restart_button)
        layout.addLayout(button_row)

        self.setLayout(layout)

        self._set_controls_enabled(False)
        self._controls_enabled = False
        self._processing_colors = False
        self._base_overlay_image: Optional[Image.Image] = None
        self._current_overlay_image: Optional[Image.Image] = None
        self._auto_active = False
        self._auto_step = 0
        self._auto_source_points: List[QPointF] = []
        self._auto_dest_points: List[QPointF] = []
        self._current_mode = "manual"
        self._auto_finished = False
        self._color_processing_token = 0
        self._latest_color_token = 0
        self._color_threads: dict[int, QThread] = {}
        self._color_workers: dict[int, _ColorFilterWorker] = {}

        self.destroyed.connect(self._shutdown_color_threads)

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """Synchronise the editor with the latest application state."""

        photo = self._state.photo.image
        base_overlay = self._state.cadastral.cropped_image
        filtered_overlay = self._state.overlay.filtered_overlay

        self._processing_colors = False
        self._status_label.hide()
        self._status_label.clear()
        self._preview_panel.set_auto_points([])
        self._preview_panel.set_highlighted(False)
        self._view.set_auto_markers([])
        self._view.set_auto_click_enabled(False)
        self._view.set_auto_cursor(False)
        self._base_overlay_image = base_overlay
        self._color_processing_token += 1
        self._latest_color_token = self._color_processing_token

        if photo is None or base_overlay is None:
            self._state.overlay.filtered_overlay = None
            self._current_overlay_image = None
            self._preview_panel.set_overlay_image(base_overlay)
            self._cancel_auto_mode(silent=True)
            self._view.clear()
            self._view.setEnabled(False)
            self._set_controls_enabled(False)
            self._image_info_label.setText(
                "Upload a cadastral map and field photo, then crop the overlay to begin alignment."
            )
            line_strength = float(self._state.overlay.line_balance_strength)
            self._line_balance_slider.blockSignals(True)
            self._line_balance_slider.setValue(int(round(line_strength * 100)))
            self._line_balance_slider.blockSignals(False)
            self._update_line_balance_label(line_strength)
            self._view.set_line_balance_strength(line_strength)
            self._manual_toggle.blockSignals(True)
            self._auto_toggle.blockSignals(True)
            self._manual_toggle.setChecked(True)
            self._auto_toggle.setChecked(False)
            self._manual_toggle.blockSignals(False)
            self._auto_toggle.blockSignals(False)
            self._current_mode = "manual"
            self._instruction_label.setText(self._MANUAL_INSTRUCTION)
            self._update_color_filter_button_state()
            return

        self._cancel_auto_mode(silent=True)
        photo_pixmap = image_io.image_to_qpixmap(photo)
        manual_points = self._state.overlay.manual_points
        if manual_points and len(manual_points) == 4:
            point_sequence: Optional[Sequence[Tuple[float, float]]] = manual_points
        else:
            point_sequence = None

        display_overlay = filtered_overlay or base_overlay
        self._view.load_images(photo_pixmap, display_overlay, point_sequence)
        self._view.setEnabled(True)
        self._set_display_overlay(display_overlay)

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

        line_strength = float(self._state.overlay.line_balance_strength)
        self._line_balance_slider.blockSignals(True)
        self._line_balance_slider.setValue(int(round(line_strength * 100)))
        self._line_balance_slider.blockSignals(False)
        self._update_line_balance_label(line_strength)

        self._view.set_line_balance_strength(line_strength)
        self._view.set_overlay_settings(self._state.overlay.show_overlay, opacity)
        self._set_controls_enabled(True)

        self._manual_toggle.blockSignals(True)
        self._auto_toggle.blockSignals(True)
        self._manual_toggle.setChecked(True)
        self._auto_toggle.setChecked(False)
        self._manual_toggle.blockSignals(False)
        self._auto_toggle.blockSignals(False)
        self._current_mode = "manual"
        self._update_instruction_text()

        self._image_info_label.setText(
            self._build_info_text(
                photo.size,
                base_overlay.size,
                self._state.photo.resized_for_performance,
            )
        )

        has_filters = bool(
            self._state.overlay.color_filters_keep
            or self._state.overlay.color_filters_remove
        )
        if has_filters and filtered_overlay is None:
            self._apply_color_filters()
        else:
            self._update_color_filter_button_state()

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

    def _handle_line_balance_changed(self, value: int) -> None:
        strength = max(0.0, min(value / 100.0, 1.0))
        self._state.overlay.line_balance_strength = strength
        self._view.set_line_balance_strength(strength)
        self._update_line_balance_label(strength)

    def _handle_opacity_changed(self, value: int) -> None:
        opacity = max(0.0, min(value / 100.0, 1.0))
        self._state.overlay.opacity = opacity
        self._view.set_overlay_settings(self._state.overlay.show_overlay, opacity)
        self._update_opacity_label(opacity)

    def _handle_overlay_visibility(self, checked: bool) -> None:
        self._state.overlay.show_overlay = checked
        self._view.set_overlay_settings(checked, self._state.overlay.opacity)

    def _show_color_filter_dialog(self) -> None:
        if self._base_overlay_image is None:
            return

        dialog = ColorFilterDialog(
            self._state.overlay.color_filters_keep,
            self._state.overlay.color_filters_remove,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        keep_filters, remove_filters = dialog.filters()
        if (
            keep_filters == self._state.overlay.color_filters_keep
            and remove_filters == self._state.overlay.color_filters_remove
        ):
            self._update_color_filter_button_state()
            return

        self._state.overlay.color_filters_keep = keep_filters
        self._state.overlay.color_filters_remove = remove_filters
        self._apply_color_filters()

    def _apply_color_filters(self) -> None:
        overlay = self._base_overlay_image
        if overlay is None:
            return

        keep_filters = list(self._state.overlay.color_filters_keep)
        remove_filters = list(self._state.overlay.color_filters_remove)

        if not keep_filters and not remove_filters:
            self._state.overlay.filtered_overlay = None
            self._processing_colors = False
            self._status_label.hide()
            self._status_label.clear()
            self._set_display_overlay(overlay)
            self._update_color_filter_button_state()
            return

        self._processing_colors = True
        self._status_label.setText("Processing colours…")
        self._status_label.show()
        self._update_color_filter_button_state()

        self._color_processing_token += 1
        token = self._color_processing_token
        self._latest_color_token = token

        worker = _ColorFilterWorker(token, overlay, keep_filters, remove_filters)
        self._color_workers[token] = worker
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.process)
        worker.finished.connect(self._handle_color_processing_finished)
        worker.failed.connect(self._handle_color_processing_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        worker.finished.connect(lambda *_, token=token: self._color_workers.pop(token, None))
        worker.failed.connect(lambda *_, token=token: self._color_workers.pop(token, None))
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda token=token: self._color_threads.pop(token, None))
        self._color_threads[token] = thread
        thread.start()

    def _handle_color_processing_finished(self, token: int, image_obj: object) -> None:
        if token != self._latest_color_token:
            return

        self._processing_colors = False
        self._status_label.hide()
        self._status_label.clear()

        if not isinstance(image_obj, Image.Image):
            self._update_color_filter_button_state()
            return

        self._state.overlay.filtered_overlay = image_obj
        self._set_display_overlay(image_obj)
        self._update_color_filter_button_state()

    def _handle_color_processing_failed(self, token: int, message: str) -> None:
        if token != self._latest_color_token:
            return

        self._processing_colors = False
        self._status_label.hide()
        self._status_label.clear()
        self._state.overlay.filtered_overlay = None

        if self._base_overlay_image is not None:
            self._set_display_overlay(self._base_overlay_image)

        if message:
            detail_text = f"Unable to process the overlay colours.\n\n{message}"
        else:
            detail_text = "Unable to process the overlay colours."

        QMessageBox.critical(self, "Colour filtering failed", detail_text)

        self._update_color_filter_button_state()

    def _handle_points_changed(self, points: list) -> None:
        if len(points) != 4:
            self._state.overlay.manual_points = None
            return
        self._state.overlay.manual_points = tuple((float(x), float(y)) for x, y in points)

    def _handle_manual_mode_selected(self, checked: bool) -> None:
        if not checked:
            return
        if self._current_mode == "manual":
            self._update_instruction_text()
            return
        self._current_mode = "manual"
        self._auto_finished = False
        self._cancel_auto_mode()
        self._auto_toggle.blockSignals(True)
        self._auto_toggle.setChecked(False)
        self._auto_toggle.blockSignals(False)

    def _handle_auto_mode_selected(self, checked: bool) -> None:
        if not checked:
            return
        if not self._view.isEnabled() or self._current_overlay_image is None:
            self._auto_toggle.blockSignals(True)
            self._auto_toggle.setChecked(False)
            self._auto_toggle.blockSignals(False)
            QMessageBox.warning(
                self,
                "Automatic pinning unavailable",
                "Load and crop both images before using automatic pinning.",
            )
            return
        if self._current_mode == "auto":
            self._update_instruction_text()
            return
        self._current_mode = "auto"
        self._start_auto_mode()

    def _start_auto_mode(self) -> None:
        self._auto_active = True
        self._auto_finished = False
        self._auto_step = 0
        self._auto_source_points = []
        self._auto_dest_points = []
        self._view.set_handles_visible(False)
        self._view.set_overlay_suppressed(True)
        self._view.set_auto_markers([])
        self._view.clear_auto_adjustment()
        self._preview_panel.set_auto_points([])
        self._preview_panel.set_corner_guides_visible(False)
        self._preview_panel.set_auto_editable(False)
        self._update_auto_focus()
        self._update_instruction_text()

    def _cancel_auto_mode(self, *, silent: bool = False) -> None:
        self._auto_active = False
        self._auto_finished = False
        self._auto_step = 0
        self._auto_source_points.clear()
        self._auto_dest_points.clear()
        self._preview_panel.set_auto_points([])
        self._preview_panel.set_highlighted(False)
        self._preview_panel.set_corner_guides_visible(True)
        self._preview_panel.set_auto_editable(False)
        self._view.set_auto_markers([])
        self._view.set_auto_click_enabled(False)
        self._view.set_auto_cursor(False)
        self._view.clear_auto_adjustment()
        self._view.set_handles_visible(True)
        self._view.set_overlay_suppressed(False)
        if not silent:
            self._update_instruction_text()

    def _auto_instruction_for_step(self, step: int) -> str:
        if step >= 8:
            return (
                "Auto pinning — points captured. Drag the red pins to refine the "
                "alignment or click Reset pins to try again."
            )
        index = step // 2
        corner = self._AUTO_CORNER_NAMES[index]
        if step % 2 == 0:
            return f"Auto pinning — Step {index + 1}: Click {corner} on the cadastral preview."
        return f"Auto pinning — Step {index + 1}: Click the matching {corner} on the field photo."

    def _update_instruction_text(self, *, completed: bool = False) -> None:
        if completed:
            self._auto_finished = True
            self._instruction_label.setText(
                "Auto pinning complete — drag the red pins on the photo or preview "
                "to fine-tune alignment."
            )
            return
        if self._auto_active:
            self._instruction_label.setText(self._auto_instruction_for_step(self._auto_step))
        elif self._current_mode == "auto":
            if self._auto_finished:
                self._instruction_label.setText(
                    "Auto pinning complete — drag the red pins on the photo or preview "
                    "to fine-tune alignment."
                )
            else:
                self._instruction_label.setText(
                    "Auto pinning — click Reset pins to begin placing corners."
                )
        else:
            self._instruction_label.setText(self._MANUAL_INSTRUCTION)

    def _update_auto_focus(self) -> None:
        if not self._auto_active or self._auto_step >= 8:
            self._preview_panel.set_highlighted(False)
            self._view.set_auto_click_enabled(False)
            self._view.set_auto_cursor(False)
            return
        waiting_for_source = self._auto_step % 2 == 0
        self._preview_panel.set_highlighted(waiting_for_source)
        self._view.set_auto_click_enabled(not waiting_for_source)
        self._view.set_auto_cursor(not waiting_for_source)

    def _handle_preview_point_clicked(self, point: QPointF) -> None:
        if (
            not self._auto_active
            or self._auto_step >= 8
            or self._auto_step % 2 == 1
            or len(self._auto_source_points) >= 4
        ):
            return
        self._auto_source_points.append(QPointF(point))
        self._preview_panel.set_auto_points(self._auto_source_points)
        self._auto_step += 1
        self._update_auto_focus()
        self._update_instruction_text()

    def _handle_photo_clicked(self, point: QPointF) -> None:
        if (
            not self._auto_active
            or self._auto_step >= 8
            or self._auto_step % 2 == 0
            or len(self._auto_dest_points) >= 4
        ):
            return
        self._auto_dest_points.append(QPointF(point))
        self._view.set_auto_markers(self._auto_dest_points)
        self._auto_step += 1
        if self._auto_step >= 8:
            self._update_auto_focus()
            self._complete_auto_alignment()
        else:
            self._update_auto_focus()
            self._update_instruction_text()

    def _handle_reset_pins(self) -> None:
        if self._current_mode == "auto":
            self._start_auto_mode()
        else:
            self._view.reset_manual_points()

    def _complete_auto_alignment(self) -> None:
        overlay = self._current_overlay_image
        if overlay is None or len(self._auto_source_points) != 4 or len(self._auto_dest_points) != 4:
            self._cancel_auto_mode(silent=True)
            self._manual_toggle.blockSignals(True)
            self._auto_toggle.blockSignals(True)
            self._manual_toggle.setChecked(True)
            self._auto_toggle.setChecked(False)
            self._manual_toggle.blockSignals(False)
            self._auto_toggle.blockSignals(False)
            self._current_mode = "manual"
            self._update_instruction_text()
            return

        src = np.array([(point.x(), point.y()) for point in self._auto_source_points], dtype=np.float32)
        dst = np.array([(point.x(), point.y()) for point in self._auto_dest_points], dtype=np.float32)

        try:
            matrix = cv2.getPerspectiveTransform(src, dst)
        except cv2.error:
            QMessageBox.warning(
                self,
                "Auto alignment failed",
                "Could not compute the perspective transform. Please try again.",
            )
            self._cancel_auto_mode(silent=True)
            self._manual_toggle.blockSignals(True)
            self._auto_toggle.blockSignals(True)
            self._manual_toggle.setChecked(True)
            self._auto_toggle.setChecked(False)
            self._manual_toggle.blockSignals(False)
            self._auto_toggle.blockSignals(False)
            self._current_mode = "manual"
            self._update_instruction_text()
            return

        width, height = overlay.size
        if width <= 1 or height <= 1:
            self._cancel_auto_mode(silent=True)
            self._manual_toggle.blockSignals(True)
            self._auto_toggle.blockSignals(True)
            self._manual_toggle.setChecked(True)
            self._auto_toggle.setChecked(False)
            self._manual_toggle.blockSignals(False)
            self._auto_toggle.blockSignals(False)
            self._current_mode = "manual"
            self._update_instruction_text()
            return

        corners = np.array(
            [
                [[0.0, 0.0]],
                [[width - 1.0, 0.0]],
                [[width - 1.0, height - 1.0]],
                [[0.0, height - 1.0]],
            ],
            dtype=np.float32,
        )
        try:
            mapped = cv2.perspectiveTransform(corners, matrix)
        except cv2.error:
            QMessageBox.warning(
                self,
                "Auto alignment failed",
                "Unable to project the overlay corners. Please retry.",
            )
            self._cancel_auto_mode(silent=True)
            self._manual_toggle.blockSignals(True)
            self._auto_toggle.blockSignals(True)
            self._manual_toggle.setChecked(True)
            self._auto_toggle.setChecked(False)
            self._manual_toggle.blockSignals(False)
            self._auto_toggle.blockSignals(False)
            self._current_mode = "manual"
            self._update_instruction_text()
            return

        flattened = mapped.reshape(-1, 2)
        if not np.isfinite(flattened).all():
            QMessageBox.warning(
                self,
                "Auto alignment failed",
                "The calculated transform produced invalid coordinates.",
            )
            self._cancel_auto_mode(silent=True)
            self._manual_toggle.blockSignals(True)
            self._auto_toggle.blockSignals(True)
            self._manual_toggle.setChecked(True)
            self._auto_toggle.setChecked(False)
            self._manual_toggle.blockSignals(False)
            self._auto_toggle.blockSignals(False)
            self._current_mode = "manual"
            self._update_instruction_text()
            return

        manual_points = [QPointF(float(x), float(y)) for x, y in flattened]
        self._view.set_manual_points(manual_points, allow_outside=True)
        self._view.set_overlay_suppressed(False)
        self._view.set_handles_visible(False)
        self._view.set_auto_markers([])
        self._view.clear_auto_adjustment()
        self._view.set_auto_click_enabled(False)
        self._view.set_auto_cursor(False)
        self._view.set_auto_adjust_points(self._auto_dest_points)

        self._preview_panel.set_auto_points(self._auto_source_points)
        self._preview_panel.set_highlighted(False)
        self._preview_panel.set_corner_guides_visible(False)
        self._preview_panel.set_auto_editable(True)

        self._auto_active = False
        self._auto_finished = True
        self._auto_toggle.blockSignals(True)
        self._manual_toggle.blockSignals(True)
        self._auto_toggle.setChecked(True)
        self._manual_toggle.setChecked(False)
        self._auto_toggle.blockSignals(False)
        self._manual_toggle.blockSignals(False)
        self._current_mode = "auto"
        self._update_instruction_text(completed=True)

    def _handle_auto_dest_points_adjusted(self, points: list) -> None:
        if not self._auto_finished or len(points) != 4:
            return

        updated: List[QPointF] = []
        for value in points:
            try:
                x, y = value
            except (TypeError, ValueError):
                return
            updated.append(QPointF(float(x), float(y)))

        self._auto_dest_points = updated
        self._update_auto_alignment_from_points()
        self._update_instruction_text(completed=True)

    def _handle_auto_source_point_adjusted(self, index: int, point: QPointF) -> None:
        if not self._auto_finished:
            return
        if index < 0 or index >= len(self._auto_source_points):
            return

        self._auto_source_points[index] = QPointF(point)
        self._preview_panel.set_auto_points(
            self._auto_source_points,
            preserve_drag=True,
        )
        self._update_auto_alignment_from_points()
        self._update_instruction_text(completed=True)

    def _update_auto_alignment_from_points(self) -> None:
        overlay = self._current_overlay_image
        if overlay is None or len(self._auto_source_points) != 4 or len(self._auto_dest_points) != 4:
            return

        src = np.array([(point.x(), point.y()) for point in self._auto_source_points], dtype=np.float32)
        dst = np.array([(point.x(), point.y()) for point in self._auto_dest_points], dtype=np.float32)

        try:
            matrix = cv2.getPerspectiveTransform(src, dst)
        except cv2.error:
            return

        width, height = overlay.size
        if width <= 1 or height <= 1:
            return

        corners = np.array(
            [
                [[0.0, 0.0]],
                [[width - 1.0, 0.0]],
                [[width - 1.0, height - 1.0]],
                [[0.0, height - 1.0]],
            ],
            dtype=np.float32,
        )
        try:
            mapped = cv2.perspectiveTransform(corners, matrix)
        except cv2.error:
            return

        flattened = mapped.reshape(-1, 2)
        if not np.isfinite(flattened).all():
            return

        manual_points = [QPointF(float(x), float(y)) for x, y in flattened]
        self._view.set_manual_points(manual_points, allow_outside=True)
        self._view.set_overlay_suppressed(False)
        self._view.set_handles_visible(False)
        self._view.set_auto_adjust_points(self._auto_dest_points)

    def _set_controls_enabled(self, enabled: bool) -> None:
        self._controls_enabled = enabled
        self._overlay_checkbox.setEnabled(enabled)
        self._opacity_slider.setEnabled(enabled)
        self._line_balance_slider.setEnabled(enabled)
        self._reset_pins_button.setEnabled(enabled)
        self._restart_button.setEnabled(True)
        self._manual_toggle.setEnabled(enabled)
        self._auto_toggle.setEnabled(enabled)
        self._update_color_filter_button_state()

    def _update_color_filter_button_state(self) -> None:
        ready = (
            self._controls_enabled
            and self._base_overlay_image is not None
            and not self._processing_colors
        )
        self._color_filter_button.setEnabled(ready)

    def _set_display_overlay(self, image: Optional[Image.Image]) -> None:
        self._current_overlay_image = image
        self._view.update_overlay_image(image)
        self._preview_panel.set_overlay_image(self._base_overlay_image)

    def _update_opacity_label(self, opacity: float) -> None:
        percentage = int(round(opacity * 100))
        self._opacity_value_label.setText(f"{percentage}%")

    def _update_line_balance_label(self, strength: float) -> None:
        if strength <= 0.001:
            self._line_balance_value_label.setText("Off")
        else:
            percentage = int(round(strength * 100))
            self._line_balance_value_label.setText(f"{percentage}%")

    def _shutdown_color_threads(self) -> None:
        """Ensure background colour filter threads exit before destruction."""

        for token, thread in list(self._color_threads.items()):
            if thread.isRunning():
                thread.quit()
                thread.wait(5000)
            self._color_threads.pop(token, None)
        self._color_workers.clear()

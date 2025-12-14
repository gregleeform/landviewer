"""Editor view implementing manual overlay alignment for the prototype."""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple
from dataclasses import replace

import math

import cv2
import numpy as np
from PIL import Image
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal, QThread
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFontComboBox,
    QFormLayout,
    QGraphicsDropShadowEffect,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsSimpleTextItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QGraphicsTextItem,
    QHBoxLayout,
    QLineEdit,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from landviewer_desktop.services import image_io, color_filters
from landviewer_desktop.state import (
    AnnotationItem,
    AnnotationSettings,
    AnnotationPath,
    AnnotationText,
    AppState,
    ColorFilterSetting,
)
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
        pen_color: str = "#1f2933",
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
        scene = self.scene()
        if scene is not None:
            scene.clearSelection()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        QGraphicsEllipseItem.mousePressEvent(self, event)

    def mouseReleaseEvent(self, event):  # type: ignore[override]
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        QGraphicsEllipseItem.mouseReleaseEvent(self, event)

    def update_bounds(self, bounds: Optional[QRectF]) -> None:
        """Change the clamp rectangle; ``None`` disables clamping."""

        self._bounds = QRectF(bounds) if bounds is not None else None


class AnnotationVertexHandle(QObject, QGraphicsEllipseItem):
    """Draggable vertex handle for annotation paths."""

    moved = Signal(int, QPointF)

    def __init__(
        self,
        index: int,
        parent: Optional[QGraphicsItem] = None,
        *,
        radius: float = 6.5,
        fill_color: str = "#f97316",
        pen_color: str = "#1f2933",
    ) -> None:
        QObject.__init__(self)
        QGraphicsEllipseItem.__init__(self, parent)
        self._index = index
        self.setRect(-radius, -radius, radius * 2, radius * 2)
        self.setBrush(QColor(fill_color))
        pen = QPen(QColor(pen_color), 1.0)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setZValue(5)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):  # type: ignore[override]
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.moved.emit(self._index, self.pos())
        return QGraphicsEllipseItem.itemChange(self, change, value)


class AnnotationPathItem(QObject, QGraphicsPathItem):
    """Editable polyline/polygon annotation with draggable vertices."""

    changed = Signal()

    def __init__(
        self,
        points: List[QPointF],
        *,
        closed: bool,
        fill_color: Optional[str],
        fill_alpha: float,
        stroke_color: str,
        stroke_width: float,
        stroke_pattern: str,
        start_marker: str,
        end_marker: str,
        outline_color: str,
        outline_width: float,
        shadow_enabled: bool,
        shadow_blur: float,
    ) -> None:
        QObject.__init__(self)
        QGraphicsPathItem.__init__(self)
        self._closed = closed
        self._fill_color = fill_color
        self._fill_alpha = max(0.0, min(fill_alpha, 1.0))
        self._stroke_color = stroke_color
        self._stroke_width = stroke_width
        self._stroke_pattern = stroke_pattern
        self._start_marker = start_marker
        self._end_marker = end_marker
        self._outline_color = outline_color
        self._outline_width = outline_width
        self._shadow_enabled = shadow_enabled
        self._shadow_blur = shadow_blur
        self._handles: List[AnnotationVertexHandle] = []
        self._points: List[QPointF] = []
        self._origin = QPointF(0, 0)
        self._edit_mode = False
        self._shadow_effect = QGraphicsDropShadowEffect()
        self._shadow_effect.setBlurRadius(max(0.0, self._shadow_blur))
        self._shadow_effect.setOffset(self._shadow_blur * 0.12, self._shadow_blur * 0.12)
        self._shadow_effect.setColor(QColor(0, 0, 0, 90))
        self.setGraphicsEffect(self._shadow_effect if self._shadow_enabled and self._shadow_blur > 0 else None)

        self.setZValue(4)
        self.setAcceptHoverEvents(True)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._apply_points(points)

    def _apply_points(self, scene_points: List[QPointF]) -> None:
        if not scene_points:
            return
        self._origin = scene_points[0]
        self.setPos(self._origin)
        self._points = [point - self._origin for point in scene_points]
        self._rebuild_path()
        self._rebuild_handles()
        self._update_handles_visibility()
        self.changed.emit()

    def _rebuild_path(self) -> None:
        path = QPainterPath()
        if not self._points:
            self.setPath(path)
            return
        path.moveTo(self._points[0])
        for point in self._points[1:]:
            path.lineTo(point)
        if self._closed:
            path.closeSubpath()
        self.setPath(path)

    def _rebuild_handles(self) -> None:
        for handle in self._handles:
            self.scene().removeItem(handle)  # type: ignore[union-attr]
        self._handles = []
        for index, point in enumerate(self._points):
            handle = AnnotationVertexHandle(index, parent=self)
            handle.setPos(point)
            handle.moved.connect(self._handle_vertex_moved)
            self._handles.append(handle)
        self._update_handles_visibility()

    def _update_handles_visibility(self) -> None:
        visible = self._edit_mode
        for handle in self._handles:
            handle.setVisible(visible)

    def _handle_vertex_moved(self, index: int, pos: QPointF) -> None:
        if index < 0 or index >= len(self._points):
            return
        self._points[index] = QPointF(pos)
        self._rebuild_path()
        self.changed.emit()

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):  # type: ignore[override]
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.changed.emit()
        return QGraphicsPathItem.itemChange(self, change, value)

    def set_edit_mode(self, enabled: bool) -> None:
        self._edit_mode = enabled
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, not enabled)
        self._update_handles_visibility()
        self.changed.emit()

    def set_style(
        self,
        *,
        fill_color: Optional[str] = None,
        fill_alpha: Optional[float] = None,
        stroke_color: Optional[str] = None,
        stroke_width: Optional[float] = None,
        outline_color: Optional[str] = None,
        outline_width: Optional[float] = None,
        shadow_enabled: Optional[bool] = None,
        shadow_blur: Optional[float] = None,
        stroke_pattern: Optional[str] = None,
        start_marker: Optional[str] = None,
        end_marker: Optional[str] = None,
    ) -> None:
        if fill_color is not None:
            self._fill_color = fill_color
        if fill_alpha is not None:
            self._fill_alpha = max(0.0, min(fill_alpha, 1.0))
        if stroke_color is not None:
            self._stroke_color = stroke_color
        if stroke_width is not None:
            self._stroke_width = stroke_width
        if stroke_pattern is not None:
            self._stroke_pattern = stroke_pattern
        if start_marker is not None:
            self._start_marker = start_marker
        if end_marker is not None:
            self._end_marker = end_marker
        if outline_color is not None:
            self._outline_color = outline_color
        if outline_width is not None:
            self._outline_width = outline_width
        if shadow_enabled is not None:
            self._shadow_enabled = shadow_enabled
        if shadow_blur is not None:
            self._shadow_blur = shadow_blur
        if self._shadow_enabled and self._shadow_blur > 0:
            if self.graphicsEffect() is None:
                self.setGraphicsEffect(self._shadow_effect)
            self._shadow_effect.setEnabled(True)
            self._shadow_effect.setBlurRadius(max(0.0, self._shadow_blur))
            self._shadow_effect.setOffset(self._shadow_blur * 0.12, self._shadow_blur * 0.12)
        else:
            self.setGraphicsEffect(None)
        self.update()
        self.changed.emit()

    def scene_points(self) -> List[QPointF]:
        return [self.mapToScene(point) for point in self._points]

    def paint(self, painter: QPainter, option, widget=None):  # type: ignore[override]
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        style_map = {
            "solid": Qt.PenStyle.SolidLine,
            "dashed": Qt.PenStyle.DashLine,
            "dotted": Qt.PenStyle.DotLine,
            "dash-dot": Qt.PenStyle.DashDotLine,
        }
        pen_style = style_map.get(self._stroke_pattern, Qt.PenStyle.SolidLine)

        if self._shadow_enabled and self._shadow_blur > 0:
            shadow_pen = QPen(QColor(0, 0, 0, 80), max(1.0, self._shadow_blur / 4))
            shadow_pen.setCosmetic(True)
            shadow_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            shadow_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(shadow_pen)
            painter.translate(self._shadow_blur * 0.1, self._shadow_blur * 0.1)
            painter.drawPath(self.path())
            painter.translate(-self._shadow_blur * 0.1, -self._shadow_blur * 0.1)

        if self._outline_width > 0:
            outline_pen = QPen(QColor(self._outline_color), self._stroke_width + (self._outline_width * 2))
            outline_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            outline_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            outline_pen.setCosmetic(True)
            painter.setPen(outline_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(self.path())

        if self._stroke_width > 0:
            stroke_pen = QPen(QColor(self._stroke_color), self._stroke_width)
            stroke_pen.setCosmetic(True)
            stroke_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            stroke_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            stroke_pen.setStyle(pen_style)
            painter.setPen(stroke_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(self.path())

            self._draw_markers(painter)

        if self._closed and self._fill_color and self._fill_alpha > 0:
            painter.setPen(Qt.PenStyle.NoPen)
            color = QColor(self._fill_color)
            color.setAlphaF(max(0.0, min(self._fill_alpha, 1.0)))
            painter.setBrush(color)
            painter.drawPath(self.path())

        if self.isSelected():
            pen = QPen(QColor("#9ca3af"))
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            rect = self.boundingRect()
            painter.drawRect(rect)
            handle_size = 5
            for corner in (
                rect.topLeft(),
                rect.topRight(),
                rect.bottomLeft(),
                rect.bottomRight(),
            ):
                handle_rect = QRectF(
                    corner.x() - handle_size / 2,
                    corner.y() - handle_size / 2,
                    handle_size,
                    handle_size,
                )
                painter.fillRect(handle_rect, QColor("#9ca3af"))

        painter.restore()

    def _draw_markers(self, painter: QPainter) -> None:
        points = self.scene_points()
        if len(points) < 2:
            return

        def _direction(a: QPointF, b: QPointF) -> QPointF:
            delta = b - a
            length = (delta.x() ** 2 + delta.y() ** 2) ** 0.5 or 1.0
            return QPointF(delta.x() / length, delta.y() / length)

        pen = painter.pen()
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

        def _draw_arrow(at: QPointF, direction: QPointF) -> None:
            size = max(6.0, pen.widthF() * 2.2)
            ortho = QPointF(-direction.y(), direction.x())
            tail = at - direction * size
            left = tail + ortho * (size * 0.6)
            right = tail - ortho * (size * 0.6)
            painter.drawPolygon(at, left, right)

        def _draw_circle(at: QPointF) -> None:
            radius = max(3.0, pen.widthF() * 1.4)
            painter.drawEllipse(at, radius, radius)

        painter.save()
        painter.setPen(pen)
        painter.setBrush(QBrush(pen.color()))

        start_dir = _direction(points[1], points[0])
        end_dir = _direction(points[-2], points[-1])

        if self._start_marker == "arrow":
            _draw_arrow(points[0], start_dir)
        elif self._start_marker == "circle":
            _draw_circle(points[0])

        if self._end_marker == "arrow":
            _draw_arrow(points[-1], end_dir)
        elif self._end_marker == "circle":
            _draw_circle(points[-1])

        painter.restore()


class AnnotationTextItem(QGraphicsTextItem):
    """Text annotation that supports inline edits and styling."""

    changed = Signal()

    def __init__(
        self,
        text: str,
        *,
        position: QPointF,
        fill_color: Optional[str],
        fill_alpha: float,
        stroke_color: str,
        stroke_width: float,
        outline_color: str,
        outline_width: float,
        shadow_enabled: bool,
        shadow_blur: float,
        font_family: str,
        font_size: int,
    ) -> None:
        QGraphicsTextItem.__init__(self, text)
        self._stroke_color = stroke_color
        self._stroke_width = stroke_width
        self._outline_color = outline_color
        self._outline_width = outline_width
        self._shadow_enabled = shadow_enabled
        self._shadow_blur = shadow_blur
        self._fill_color = fill_color
        self._fill_alpha = max(0.0, min(fill_alpha, 1.0))
        self._font_family = font_family
        self._font_size = font_size
        self._text_path = QPainterPath()
        self._shadow_effect = QGraphicsDropShadowEffect()
        self._shadow_effect.setBlurRadius(max(0.0, self._shadow_blur))
        self._shadow_effect.setOffset(self._shadow_blur * 0.12, self._shadow_blur * 0.12)
        self._shadow_effect.setColor(QColor(0, 0, 0, 90))

        color = QColor(fill_color or "#000000")
        if fill_color is None:
            color.setAlpha(0)
        else:
            color.setAlphaF(max(0.0, min(self._fill_alpha, 1.0)))
        self.setDefaultTextColor(color)
        self.setPos(position)
        self.setZValue(4.5)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsFocusable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self._apply_font()
        self._rebuild_path()
        self.setGraphicsEffect(self._shadow_effect if self._shadow_enabled and self._shadow_blur > 0 else None)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):  # type: ignore[override]
        if change in (
            QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged,
            QGraphicsItem.GraphicsItemChange.ItemPositionChange,
            QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged,
        ):
            self.changed.emit()
        return QGraphicsTextItem.itemChange(self, change, value)

    def _apply_font(self) -> None:
        font = QFont(self._font_family, self._font_size)
        self.setFont(font)

    def _rebuild_path(self) -> None:
        font = self.font()
        path = QPainterPath()
        path.addText(QPointF(0, 0), font, self.toPlainText())
        bounds = path.boundingRect()
        path.translate(-bounds.left(), -bounds.top())
        self._text_path = path
        self.setTransformOriginPoint(path.boundingRect().center())

    def set_style(
        self,
        *,
        fill_color: Optional[str] = None,
        fill_alpha: Optional[float] = None,
        stroke_color: Optional[str] = None,
        stroke_width: Optional[float] = None,
        outline_color: Optional[str] = None,
        outline_width: Optional[float] = None,
        shadow_enabled: Optional[bool] = None,
        shadow_blur: Optional[float] = None,
        font_family: Optional[str] = None,
        font_size: Optional[int] = None,
    ) -> None:
        if fill_color is not None:
            self._fill_color = fill_color
        if fill_alpha is not None:
            self._fill_alpha = max(0.0, min(fill_alpha, 1.0))
        if fill_color is not None or fill_alpha is not None:
            color = QColor(self._fill_color or "#000000")
            if self._fill_color is None:
                color.setAlpha(0)
            else:
                color.setAlphaF(max(0.0, min(self._fill_alpha, 1.0)))
            self.setDefaultTextColor(color)
        if stroke_color is not None:
            self._stroke_color = stroke_color
        if stroke_width is not None:
            self._stroke_width = stroke_width
        if outline_color is not None:
            self._outline_color = outline_color
        if outline_width is not None:
            self._outline_width = outline_width
        if shadow_enabled is not None:
            self._shadow_enabled = shadow_enabled
        if shadow_blur is not None:
            self._shadow_blur = shadow_blur
        if font_family is not None:
            self._font_family = font_family
        if font_size is not None:
            self._font_size = font_size
        self._apply_font()
        self._rebuild_path()
        if self._shadow_enabled and self._shadow_blur > 0:
            if self.graphicsEffect() is None:
                self.setGraphicsEffect(self._shadow_effect)
            self._shadow_effect.setEnabled(True)
            self._shadow_effect.setBlurRadius(max(0.0, self._shadow_blur))
            self._shadow_effect.setOffset(self._shadow_blur * 0.12, self._shadow_blur * 0.12)
        else:
            self.setGraphicsEffect(None)
        self.update()
        self.changed.emit()

    def set_text(self, text: str) -> None:
        self.setPlainText(text)
        self._rebuild_path()
        self.changed.emit()

    def scene_position(self) -> QPointF:
        return self.scenePos()

    def paint(self, painter: QPainter, option, widget=None):  # type: ignore[override]
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        path = self._text_path

        if self._shadow_enabled and self._shadow_blur > 0:
            shadow_pen = QPen(QColor(0, 0, 0, 90), max(1.0, self._shadow_blur / 4))
            shadow_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.translate(self._shadow_blur * 0.12, self._shadow_blur * 0.12)
            painter.setPen(shadow_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
            painter.translate(-self._shadow_blur * 0.12, -self._shadow_blur * 0.12)

        if self._outline_width > 0:
            outline_pen = QPen(QColor(self._outline_color), self._stroke_width + (self._outline_width * 2))
            outline_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            outline_pen.setCosmetic(True)
            painter.setPen(outline_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        if self._stroke_width > 0:
            stroke_pen = QPen(QColor(self._stroke_color), self._stroke_width)
            stroke_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            stroke_pen.setCosmetic(True)
            painter.setPen(stroke_pen)
            color = QColor(self._fill_color or "#000000")
            if self._fill_color is None:
                color.setAlpha(0)
            else:
                color.setAlphaF(max(0.0, min(self._fill_alpha, 1.0)))
            painter.setBrush(color)
            painter.drawPath(path)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            color = QColor(self._fill_color or "#000000")
            if self._fill_color is None:
                color.setAlpha(0)
            else:
                color.setAlphaF(max(0.0, min(self._fill_alpha, 1.0)))
            painter.setBrush(color)
            painter.drawPath(path)

        if self.isSelected():
            pen = QPen(QColor("#9ca3af"))
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            rect = self.boundingRect()
            painter.drawRect(rect)
            handle_size = 5
            for corner in (
                rect.topLeft(),
                rect.topRight(),
                rect.bottomLeft(),
                rect.bottomRight(),
            ):
                handle_rect = QRectF(
                    corner.x() - handle_size / 2,
                    corner.y() - handle_size / 2,
                    handle_size,
                    handle_size,
                )
                painter.fillRect(handle_rect, QColor("#9ca3af"))

        painter.restore()

    def mouseDoubleClickEvent(self, event):  # type: ignore[override]
        super().mouseDoubleClickEvent(event)

    def hoverEnterEvent(self, event):  # type: ignore[override]
        if self.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):  # type: ignore[override]
        self.unsetCursor()
        super().hoverLeaveEvent(event)

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


class _AnnotationStyleDialog(QDialog):
    """Dialog that exposes annotation styling controls on demand."""

    def __init__(
        self,
        parent: QWidget,
        settings: AnnotationSettings,
        *,
        allow_font: bool,
        allow_fill: bool = True,
        allow_stroke: bool = True,
        allow_outline: bool = True,
        allow_text_value: bool = False,
        text_value: str = "",
        live_apply=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Annotation options")
        self._settings = settings
        self._allow_font = allow_font
        self._allow_fill = allow_fill
        self._allow_stroke = allow_stroke
        self._allow_outline = allow_outline
        self._allow_text_value = allow_text_value
        self._live_apply = live_apply

        self._fill_button = QPushButton()
        self._fill_button.setObjectName("annotationDialogFillColor")
        self._fill_clear = QPushButton("No fill")
        self._fill_clear.clicked.connect(self._clear_fill)
        self._stroke_button = QPushButton()
        self._stroke_button.setObjectName("annotationDialogStrokeColor")
        self._outline_button = QPushButton()
        self._outline_button.setObjectName("annotationDialogOutlineColor")
        self._pattern_combo = QComboBox()
        for label, value in (
            ("Solid", "solid"),
            ("Dashed", "dashed"),
            ("Dotted", "dotted"),
            ("Dash-dot", "dash-dot"),
        ):
            self._pattern_combo.addItem(label, value)
        self._pattern_combo.setCurrentIndex(max(0, self._pattern_combo.findData(settings.stroke_pattern)))
        self._pattern_combo.currentIndexChanged.connect(self._emit_live_update)

        self._start_marker_combo = QComboBox()
        self._end_marker_combo = QComboBox()
        for combo in (self._start_marker_combo, self._end_marker_combo):
            combo.addItem("None", "none")
            combo.addItem("Arrow", "arrow")
            combo.addItem("Circle", "circle")
            combo.currentIndexChanged.connect(self._emit_live_update)
        self._start_marker_combo.setCurrentIndex(max(0, self._start_marker_combo.findData(settings.start_marker)))
        self._end_marker_combo.setCurrentIndex(max(0, self._end_marker_combo.findData(settings.end_marker)))

        for button, name in (
            (self._fill_button, "fill"),
            (self._stroke_button, "stroke"),
            (self._outline_button, "outline"),
        ):
            button.setMinimumWidth(76)
            button.clicked.connect(lambda _, b=button, n=name: self._choose_color(b, n))

        self._stroke_width_spin = QDoubleSpinBox()
        self._stroke_width_spin.setRange(0.0, 12.0)
        self._stroke_width_spin.setSingleStep(0.2)
        self._stroke_width_spin.setValue(settings.stroke_width)
        self._stroke_width_spin.valueChanged.connect(self._emit_live_update)

        self._outline_width_spin = QDoubleSpinBox()
        self._outline_width_spin.setRange(0.0, 12.0)
        self._outline_width_spin.setSingleStep(0.2)
        self._outline_width_spin.setValue(settings.outline_width)
        self._outline_width_spin.valueChanged.connect(self._emit_live_update)

        self._fill_alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self._fill_alpha_slider.setRange(0, 100)
        self._fill_alpha_slider.setValue(int(round(settings.fill_alpha * 100)))
        self._fill_alpha_slider.valueChanged.connect(self._emit_live_update)
        self._fill_alpha_label = QLabel(f"{int(round(settings.fill_alpha * 100))}%")

        self._shadow_checkbox = QCheckBox("Drop shadow")
        self._shadow_checkbox.setChecked(settings.shadow_enabled)
        self._shadow_checkbox.toggled.connect(self._emit_live_update)
        self._shadow_blur_spin = QDoubleSpinBox()
        self._shadow_blur_spin.setRange(0.0, 40.0)
        self._shadow_blur_spin.setSingleStep(0.5)
        self._shadow_blur_spin.setValue(settings.shadow_blur)
        self._shadow_blur_spin.setEnabled(settings.shadow_enabled)
        self._shadow_blur_spin.valueChanged.connect(self._emit_live_update)
        self._shadow_checkbox.toggled.connect(self._shadow_blur_spin.setEnabled)

        self._font_combo = QFontComboBox()
        self._font_combo.setEditable(False)
        self._font_combo.setCurrentFont(QFont(settings.font_family))
        self._font_combo.currentFontChanged.connect(self._emit_live_update)
        self._font_size_spin = QSpinBox()
        self._font_size_spin.setRange(8, 96)
        self._font_size_spin.setValue(settings.font_size)
        self._font_size_spin.valueChanged.connect(self._emit_live_update)

        self._text_edit = QLineEdit(text_value)
        self._text_edit.textEdited.connect(self._emit_live_update)

        form = QFormLayout()

        if allow_text_value:
            form.addRow("Text", self._text_edit)

        if allow_fill:
            fill_row = QHBoxLayout()
            fill_row.addWidget(self._fill_button)
            fill_row.addWidget(self._fill_clear)
            fill_row.addStretch(1)
            form.addRow("Fill colour", fill_row)

            alpha_row = QHBoxLayout()
            alpha_row.addWidget(self._fill_alpha_slider)
            alpha_row.addWidget(self._fill_alpha_label)
            form.addRow("Fill opacity", alpha_row)

        if allow_stroke:
            form.addRow("Stroke colour", self._stroke_button)
            form.addRow("Stroke width", self._stroke_width_spin)
            form.addRow("Line style", self._pattern_combo)
            form.addRow("Start marker", self._start_marker_combo)
            form.addRow("End marker", self._end_marker_combo)

        if allow_outline:
            form.addRow("Outline colour", self._outline_button)
            form.addRow("Outline width", self._outline_width_spin)

        form.addRow(self._shadow_checkbox, self._shadow_blur_spin)

        if allow_font:
            form.addRow("Font", self._font_combo)
            form.addRow("Font size", self._font_size_spin)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.setLayout(layout)

        self._apply_color_style(self._fill_button, settings.fill_color)
        self._apply_color_style(self._stroke_button, settings.stroke_color)
        self._apply_color_style(self._outline_button, settings.outline_color)

    def _apply_color_style(self, button: QPushButton, color: Optional[str]) -> None:
        normalized, rgb = EditorGraphicsView._normalise_color(color)
        text_label = "None" if color is None else normalized
        brightness = (rgb[0] * 299 + rgb[1] * 587 + rgb[2] * 114) / 1000
        text_color = "#111827" if brightness > 160 else "#f8fafc"
        button.setProperty("colorValue", color)
        button.setText(text_label)
        button.setStyleSheet(
            f"QPushButton {{ background-color: {normalized}; color: {text_color}; border: 1px solid #9ca3af; padding: 4px 12px; border-radius: 4px; }}"
        )

    def _choose_color(self, button: QPushButton, name: str) -> None:
        initial_color = button.property("colorValue")
        initial = QColor(initial_color or "#ffffff")
        color = QColorDialog.getColor(initial, self, f"Select {name} colour")
        if not color.isValid():
            return
        self._apply_color_style(button, color.name())
        self._emit_live_update()

    def _clear_fill(self) -> None:
        self._apply_color_style(self._fill_button, None)
        self._fill_alpha_slider.blockSignals(True)
        self._fill_alpha_slider.setValue(0)
        self._fill_alpha_label.setText("0%")
        self._fill_alpha_slider.blockSignals(False)
        self._emit_live_update()

    def _emit_live_update(self) -> None:
        if self._allow_fill:
            self._fill_alpha_label.setText(f"{self._fill_alpha_slider.value()}%")
        if callable(self._live_apply):
            self._live_apply(self.result_settings(), self.text_value())

    def text_value(self) -> Optional[str]:
        if not self._allow_text_value:
            return None
        return self._text_edit.text()

    def result_settings(self) -> AnnotationSettings:
        base = self._settings
        return replace(
            base,
            fill_color=self._fill_button.property("colorValue") if self._allow_fill else base.fill_color,
            fill_alpha=(self._fill_alpha_slider.value() / 100.0) if self._allow_fill else base.fill_alpha,
            stroke_color=self._stroke_button.property("colorValue") if self._allow_stroke else base.stroke_color,
            stroke_width=float(self._stroke_width_spin.value()) if self._allow_stroke else base.stroke_width,
            stroke_pattern=self._pattern_combo.currentData() if self._allow_stroke else base.stroke_pattern,
            start_marker=self._start_marker_combo.currentData() if self._allow_stroke else base.start_marker,
            end_marker=self._end_marker_combo.currentData() if self._allow_stroke else base.end_marker,
            outline_color=self._outline_button.property("colorValue") if self._allow_outline else base.outline_color,
            outline_width=float(self._outline_width_spin.value()) if self._allow_outline else base.outline_width,
            shadow_enabled=self._shadow_checkbox.isChecked(),
            shadow_blur=float(self._shadow_blur_spin.value()),
            font_family=self._font_combo.currentFont().family() if self._allow_font else base.font_family,
            font_size=int(self._font_size_spin.value()) if self._allow_font else base.font_size,
        )


class EditorGraphicsView(QGraphicsView):
    """Renders the field photo with the warped cadastral overlay."""

    points_changed = Signal(list)
    photo_clicked = Signal(QPointF)
    auto_handles_changed = Signal(list)
    annotations_changed = Signal(list)
    selection_changed = Signal()
    annotation_double_clicked = Signal(object)
    annotation_committed = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setBackgroundBrush(QColor("#f5f7fa"))
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setMouseTracking(True)
        self._scene.selectionChanged.connect(self._emit_selection_changed)

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
        self._line_thickness: float = 0.0
        self._edge_smoothing: float = 0.0
        self._outline_thickness: float = 1.0
        self._outline_color_hex: str = "#FFFFFF"
        self._outline_color_rgb: Tuple[int, int, int] = (255, 255, 255)
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
        self._annotation_items: List[QGraphicsItem] = []
        self._annotation_mode: str = "select"
        self._annotation_settings: Optional[AnnotationSettings] = None
        self._pending_path: List[QPointF] = []
        self._pending_path_item: Optional[AnnotationPathItem] = None
        self._emit_points_after_load = False
        self._effect_cached_image: Optional[np.ndarray] = None
        self._effect_cache_key: Optional[
            Tuple[float, float, float, Tuple[int, int, int]]
        ] = None
        self._auto_cursor_locked = False
        self._refresh_annotation_cursor()

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
        self._annotation_items = []
        self._annotation_mode = "select"
        self._annotation_settings = None
        self._pending_path = []
        self._pending_path_item = None
        self._overlay_suppressed = False
        self._edge_smoothing = 0.0
        self._outline_thickness = 1.0
        self._outline_color_hex = "#FFFFFF"
        self._outline_color_rgb = (255, 255, 255)
        self._effect_cached_image = None
        self._effect_cache_key = None
        self._auto_cursor_locked = False
        self._refresh_annotation_cursor()

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

        self._emit_points_after_load = True
        if manual_points and len(manual_points) == 4:
            points = [self._to_point(point) for point in manual_points]
            self._emit_points_after_load = False
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

    # ------------------------------------------------------------------
    def set_annotation_settings(self, settings: AnnotationSettings, *, restyle_existing: bool = False) -> None:
        """Store toolbar presets and optionally restyle existing annotations."""

        self._annotation_settings = settings
        if not restyle_existing:
            return

        for item in list(self._annotation_items):
            if isinstance(item, AnnotationTextItem):
                item.set_style(
                    fill_color=settings.fill_color,
                    fill_alpha=settings.fill_alpha,
                    stroke_color=settings.stroke_color,
                    stroke_width=0.0,
                    outline_color=settings.outline_color,
                    outline_width=settings.outline_width,
                    shadow_enabled=settings.shadow_enabled,
                    shadow_blur=settings.shadow_blur,
                    font_family=settings.font_family,
                    font_size=settings.font_size,
                )
            elif isinstance(item, AnnotationPathItem):
                item.set_style(
                    fill_color=settings.fill_color,
                    fill_alpha=settings.fill_alpha,
                    stroke_color=settings.stroke_color,
                    stroke_width=settings.stroke_width,
                    stroke_pattern=settings.stroke_pattern,
                    start_marker=settings.start_marker,
                    end_marker=settings.end_marker,
                    outline_color=settings.outline_color,
                    outline_width=settings.outline_width,
                    shadow_enabled=settings.shadow_enabled,
                    shadow_blur=settings.shadow_blur,
                )

    # ------------------------------------------------------------------
    def set_annotation_mode(self, mode: str) -> None:
        self._annotation_mode = mode
        if mode != "line" and mode != "polygon":
            self._cancel_path()
        self._refresh_annotation_cursor()

    # ------------------------------------------------------------------
    def _refresh_annotation_cursor(self) -> None:
        if self._auto_cursor_locked:
            return
        if self._annotation_mode == "text":
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        elif self._annotation_mode in {"line", "polygon"}:
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)

    # ------------------------------------------------------------------
    def load_annotations(self, annotations: Sequence[AnnotationItem]) -> None:
        self._clear_annotations()
        if not annotations:
            return
        for entry in annotations:
            kind = getattr(entry, "kind", None)
            if kind == "text":
                self._add_text_annotation(
                    QPointF(*entry.position),
                    preset=False,
                    existing=entry,
                )
            elif kind == "path" and getattr(entry, "points", None):
                points = [QPointF(x, y) for x, y in entry.points]
                self._finalize_path(points, closed=getattr(entry, "closed", False), preset=False, existing=entry)

    # ------------------------------------------------------------------
    def _clear_annotations(self) -> None:
        for item in self._annotation_items:
            self._scene.removeItem(item)
        self._annotation_items = []
        self._pending_path = []
        if self._pending_path_item:
            self._scene.removeItem(self._pending_path_item)
        self._pending_path_item = None

    def _emit_selection_changed(self) -> None:
        self.selection_changed.emit()

    # ------------------------------------------------------------------
    def _emit_annotations(self) -> None:
        serialised: List[AnnotationItem] = []
        for item in self._annotation_items:
            if isinstance(item, AnnotationTextItem):
                serialised.append(
                    AnnotationText(
                        text=item.toPlainText(),
                        position=(item.scene_position().x(), item.scene_position().y()),
                        z=item.zValue(),
                        fill_color=item._fill_color,
                        fill_alpha=item._fill_alpha,
                        stroke_color=item._stroke_color,
                        stroke_width=item._stroke_width,
                        outline_color=item._outline_color,
                        outline_width=item._outline_width,
                        shadow_enabled=item._shadow_enabled,
                        shadow_blur=item._shadow_blur,
                        font_family=item._font_family,
                        font_size=item._font_size,
                    )
                )
            elif isinstance(item, AnnotationPathItem):
                points = [(point.x(), point.y()) for point in item.scene_points()]
                serialised.append(
                    AnnotationPath(
                        points=points,
                        closed=item._closed,
                        z=item.zValue(),
                        fill_color=item._fill_color,
                        fill_alpha=item._fill_alpha,
                        stroke_color=item._stroke_color,
                        stroke_width=item._stroke_width,
                        stroke_pattern=item._stroke_pattern,
                        start_marker=item._start_marker,
                        end_marker=item._end_marker,
                        outline_color=item._outline_color,
                        outline_width=item._outline_width,
                        shadow_enabled=item._shadow_enabled,
                        shadow_blur=item._shadow_blur,
                    )
                )
        self.annotations_changed.emit(serialised)

    # ------------------------------------------------------------------
    def selected_text_item(self) -> Optional[AnnotationTextItem]:
        for item in self._scene.selectedItems():
            if isinstance(item, AnnotationTextItem):
                return item
        return None

    def selected_annotation(self) -> Optional[QGraphicsItem]:
        for item in self._scene.selectedItems():
            if isinstance(item, (AnnotationTextItem, AnnotationPathItem)):
                return item
        return None

    # ------------------------------------------------------------------
    def duplicate_selected_annotation(self) -> None:
        item = self.selected_annotation()
        if item is None:
            return

        offset = QPointF(12, 12)
        duplicated: Optional[QGraphicsItem] = None

        if isinstance(item, AnnotationTextItem):
            duplicated = AnnotationTextItem(
                item.toPlainText(),
                position=item.scene_position() + offset,
                fill_color=item._fill_color,
                fill_alpha=item._fill_alpha,
                stroke_color=item._stroke_color,
                stroke_width=item._stroke_width,
                outline_color=item._outline_color,
                outline_width=item._outline_width,
                shadow_enabled=item._shadow_enabled,
                shadow_blur=item._shadow_blur,
                font_family=item._font_family,
                font_size=item._font_size,
            )
        elif isinstance(item, AnnotationPathItem):
            points = [point + offset for point in item.scene_points()]
            duplicated = AnnotationPathItem(
                points,
                closed=item._closed,
                fill_color=item._fill_color,
                fill_alpha=item._fill_alpha,
                stroke_color=item._stroke_color,
                stroke_width=item._stroke_width,
                stroke_pattern=item._stroke_pattern,
                start_marker=item._start_marker,
                end_marker=item._end_marker,
                outline_color=item._outline_color,
                outline_width=item._outline_width,
                shadow_enabled=item._shadow_enabled,
                shadow_blur=item._shadow_blur,
            )

        if duplicated is None:
            return

        duplicated.setZValue(item.zValue() + 0.01)
        duplicated.changed.connect(self._emit_annotations)  # type: ignore[attr-defined]
        self._scene.addItem(duplicated)
        self._annotation_items.append(duplicated)
        self._scene.clearSelection()
        duplicated.setSelected(True)
        self._emit_annotations()
        self.annotation_committed.emit()

    # ------------------------------------------------------------------
    def has_selected_text(self) -> bool:
        return self.selected_text_item() is not None

    # ------------------------------------------------------------------
    def edit_selected_text(self) -> None:
        item = self.selected_text_item()
        if item is None:
            return
        self.annotation_double_clicked.emit(item)

    # ------------------------------------------------------------------
    def _current_annotation_settings(self) -> AnnotationSettings:
        return self._annotation_settings or AnnotationSettings()

    # ------------------------------------------------------------------
    def _add_text_annotation(
        self,
        pos: QPointF,
        *,
        preset: bool = True,
        existing: Optional[AnnotationText] = None,
    ) -> None:
        settings = self._current_annotation_settings()
        base_text = existing.text if existing else "새 텍스트"
        item = AnnotationTextItem(
            base_text,
            position=pos,
            fill_color=settings.fill_color,
            fill_alpha=settings.fill_alpha,
            stroke_color=settings.stroke_color,
            stroke_width=existing.stroke_width if existing else 0.0,
            outline_color=settings.outline_color,
            outline_width=settings.outline_width,
            shadow_enabled=settings.shadow_enabled,
            shadow_blur=settings.shadow_blur,
            font_family=settings.font_family,
            font_size=settings.font_size,
        )
        if existing:
            item.set_style(
                fill_color=existing.fill_color,
                fill_alpha=existing.fill_alpha,
                stroke_color=existing.stroke_color,
                stroke_width=existing.stroke_width,
                outline_color=existing.outline_color,
                outline_width=existing.outline_width,
                shadow_enabled=existing.shadow_enabled,
                shadow_blur=existing.shadow_blur,
                font_family=existing.font_family,
                font_size=existing.font_size,
            )
            item.setZValue(getattr(existing, "z", item.zValue()))
        item.changed.connect(self._emit_annotations)
        self._scene.addItem(item)
        self._annotation_items.append(item)
        if preset:
            self._scene.clearSelection()
            item.setSelected(True)
            self._emit_annotations()
            self.annotation_committed.emit()

    # ------------------------------------------------------------------
    def _start_path(self, pos: QPointF) -> None:
        settings = self._current_annotation_settings()
        self._pending_path = [pos]
        if self._pending_path_item:
            self._scene.removeItem(self._pending_path_item)
        self._pending_path_item = AnnotationPathItem(
            [pos],
            closed=self._annotation_mode == "polygon",
            fill_color=settings.fill_color if self._annotation_mode == "polygon" else None,
            fill_alpha=settings.fill_alpha if self._annotation_mode == "polygon" else 0.0,
            stroke_color=settings.stroke_color,
            stroke_width=settings.stroke_width,
            stroke_pattern=settings.stroke_pattern,
            start_marker=settings.start_marker,
            end_marker=settings.end_marker,
            outline_color=settings.outline_color,
            outline_width=settings.outline_width,
            shadow_enabled=settings.shadow_enabled,
            shadow_blur=settings.shadow_blur,
        )
        self._pending_path_item.setOpacity(0.6)
        self._scene.addItem(self._pending_path_item)

    # ------------------------------------------------------------------
    def _extend_path(self, pos: QPointF) -> None:
        if not self._pending_path:
            self._start_path(pos)
            return
        self._pending_path.append(pos)
        self._update_pending_path_preview()

    # ------------------------------------------------------------------
    def _update_pending_path_preview(self, hover: Optional[QPointF] = None) -> None:
        if not self._pending_path_item:
            return
        points = list(self._pending_path)
        if hover is not None:
            points.append(hover)
        self._pending_path_item._apply_points(points)

    # ------------------------------------------------------------------
    def _finalize_path(
        self,
        points: Optional[List[QPointF]] = None,
        *,
        closed: Optional[bool] = None,
        preset: bool = True,
        existing: Optional[AnnotationPath] = None,
    ) -> None:
        path_points = points or list(self._pending_path)
        required = 3 if (closed or self._annotation_mode == "polygon") else 2
        if len(path_points) < required:
            self._cancel_path()
            return
        settings = self._current_annotation_settings()
        item = AnnotationPathItem(
            path_points,
            closed=self._annotation_mode == "polygon" if closed is None else closed,
            fill_color=settings.fill_color if (closed or self._annotation_mode == "polygon") else None,
            fill_alpha=settings.fill_alpha if (closed or self._annotation_mode == "polygon") else 0.0,
            stroke_color=settings.stroke_color,
            stroke_width=settings.stroke_width,
            stroke_pattern=settings.stroke_pattern,
            start_marker=settings.start_marker,
            end_marker=settings.end_marker,
            outline_color=settings.outline_color,
            outline_width=settings.outline_width,
            shadow_enabled=settings.shadow_enabled,
            shadow_blur=settings.shadow_blur,
        )
        if existing:
            item.set_style(
                fill_color=existing.fill_color,
                fill_alpha=getattr(existing, "fill_alpha", settings.fill_alpha),
                stroke_color=existing.stroke_color,
                stroke_width=existing.stroke_width,
                stroke_pattern=getattr(existing, "stroke_pattern", settings.stroke_pattern),
                start_marker=getattr(existing, "start_marker", settings.start_marker),
                end_marker=getattr(existing, "end_marker", settings.end_marker),
                outline_color=existing.outline_color,
                outline_width=existing.outline_width,
                shadow_enabled=existing.shadow_enabled,
                shadow_blur=existing.shadow_blur,
            )
            item.setZValue(getattr(existing, "z", item.zValue()))
        item.changed.connect(self._emit_annotations)
        self._scene.addItem(item)
        self._annotation_items.append(item)
        self._cancel_path()
        if preset:
            self._emit_annotations()
            self.annotation_committed.emit()

    # ------------------------------------------------------------------
    def _cancel_path(self) -> None:
        self._pending_path = []
        if self._pending_path_item:
            self._scene.removeItem(self._pending_path_item)
        self._pending_path_item = None

        self._manual_points = [QPointF(handle.pos()) for handle in self._handles]
        self._update_polygon()
        self._update_overlay_pixmap()

        if self._emit_points_after_load:
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
        self._effect_cached_image = None
        self._effect_cache_key = None
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

    def set_line_thickness(self, thickness: float) -> None:
        """Adjust the amount of overlay dilation applied before warping."""

        clamped = max(0.0, min(thickness, 3.0))
        if abs(self._line_thickness - clamped) < 1e-3:
            return
        self._line_thickness = clamped
        self._effect_cache_key = None
        self._update_overlay_pixmap()

    def set_edge_smoothing(self, smoothing: float) -> None:
        """Adjust the anti-alias smoothing radius applied to the overlay."""

        clamped = max(0.0, min(smoothing, 5.0))
        if abs(self._edge_smoothing - clamped) < 1e-3:
            return
        self._edge_smoothing = clamped
        self._effect_cache_key = None
        self._update_overlay_pixmap()

    def set_outline_settings(self, color: str, thickness: float) -> None:
        """Update outline colour and thickness settings."""

        normalized, rgb = self._normalise_color(color)
        clamped = max(0.0, min(thickness, 6.0))

        changed = False
        if abs(self._outline_thickness - clamped) >= 1e-3:
            self._outline_thickness = clamped
            changed = True
        if self._outline_color_hex != normalized:
            self._outline_color_hex = normalized
            self._outline_color_rgb = rgb
            changed = True

        if changed:
            self._effect_cache_key = None
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
                    pen_color="#1f2933",
                    pen_width=1.4,
                )
                handle.setZValue(3.5)
                handle.moved.connect(self._handle_auto_handle_moved)
                label = QGraphicsSimpleTextItem(str(index + 1))
                label.setBrush(QColor("#1f2933"))
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

        self._auto_cursor_locked = enabled
        if enabled:
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        else:
            self._refresh_annotation_cursor()

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
            marker.setPen(QPen(QColor("#1f2933"), 1.2))
            marker.setZValue(2.5)
            marker.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
            marker.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            marker.setPos(point)
            self._scene.addItem(marker)
            self._auto_markers.append(marker)

            label = QGraphicsSimpleTextItem(str(index))
            label.setBrush(QColor("#1f2933"))
            label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
            label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            label.setParentItem(marker)
            label.setPos(-4.0, -6.0)

    # ------------------------------------------------------------------
    def mousePressEvent(self, event):  # type: ignore[override]
        if self._handle_annotation_press(event):
            return

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
    def contextMenuEvent(self, event):  # type: ignore[override]
        target = self.itemAt(event.pos())
        while target is not None and not isinstance(
            target, (AnnotationTextItem, AnnotationPathItem)
        ):
            target = target.parentItem()

        if target is not None and target in self._annotation_items:
            menu = QMenu(self)
            bring_front = menu.addAction("Bring to front")
            send_back = menu.addAction("Send to back")
            chosen = menu.exec(event.globalPos())
            if chosen == bring_front:
                self._raise_annotation(target)
            elif chosen == send_back:
                self._lower_annotation(target)
            return

        super().contextMenuEvent(event)

    # ------------------------------------------------------------------
    def _raise_annotation(self, item: QGraphicsItem) -> None:
        if not self._annotation_items:
            return
        max_z = max(entry.zValue() for entry in self._annotation_items)
        item.setZValue(max_z + 1.0)
        self._emit_annotations()

    def _lower_annotation(self, item: QGraphicsItem) -> None:
        if not self._annotation_items:
            return
        min_z = min(entry.zValue() for entry in self._annotation_items)
        item.setZValue(min_z - 1.0)
        self._emit_annotations()

    # ------------------------------------------------------------------
    def resizeEvent(self, event):  # type: ignore[override]
        super().resizeEvent(event)
        self._refit_view()

    # ------------------------------------------------------------------
    def _annotation_scene_pos(self, event) -> Optional[QPointF]:
        if self._photo_item is None:
            return None
        scene_pos = self.mapToScene(event.position().toPoint())
        item_pos = self._photo_item.mapFromScene(scene_pos)
        rect = self._photo_item.boundingRect()
        if rect.contains(item_pos):
            return self._photo_item.mapToScene(item_pos)
        return None

    # ------------------------------------------------------------------
    def _handle_annotation_press(self, event) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        if self._annotation_mode not in {"text", "line", "polygon"}:
            return False

        pos = self._annotation_scene_pos(event)
        if pos is None:
            return False

        if self._annotation_mode == "text":
            self._add_text_annotation(pos)
        elif self._annotation_mode in {"line", "polygon"}:
            if not self._pending_path:
                self._start_path(pos)
            else:
                self._extend_path(pos)
        event.accept()
        return True

    # ------------------------------------------------------------------
    def _handle_annotation_move(self, event) -> bool:
        if self._annotation_mode not in {"line", "polygon"}:
            return False
        if not self._pending_path:
            return False

        pos = self._annotation_scene_pos(event)
        if pos is None:
            return False
        self._update_pending_path_preview(pos)
        event.accept()
        return True

    # ------------------------------------------------------------------
    def mouseMoveEvent(self, event):  # type: ignore[override]
        if self._handle_annotation_move(event):
            return
        super().mouseMoveEvent(event)

    # ------------------------------------------------------------------
    def mouseDoubleClickEvent(self, event):  # type: ignore[override]
        if (
            self._annotation_mode in {"line", "polygon"}
            and self._pending_path
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self._finalize_path()
            event.accept()
            return

        if self._annotation_mode == "select" and event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.position().toPoint())
            if isinstance(item, (AnnotationTextItem, AnnotationPathItem)):
                if not item.isSelected():
                    self._scene.clearSelection()
                    item.setSelected(True)
                self.annotation_double_clicked.emit(item)
                event.accept()
                return

        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------
    def keyPressEvent(self, event):  # type: ignore[override]
        if event.key() == Qt.Key.Key_Escape and self._pending_path:
            self._cancel_path()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self._delete_selected_annotations():
                event.accept()
                return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    def _delete_selected_annotations(self) -> bool:
        removed = False
        for item in list(self._scene.selectedItems()):
            if isinstance(item, (AnnotationTextItem, AnnotationPathItem)):
                if item in self._annotation_items:
                    self._annotation_items.remove(item)
                self._scene.removeItem(item)
                removed = True

        if removed:
            self._emit_annotations()
            self.selection_changed.emit()
        return removed

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

        line_radius = float(self._line_thickness)
        outline_radius = float(self._outline_thickness)
        smoothing = float(self._edge_smoothing)
        cache_key = (line_radius, outline_radius, smoothing, self._outline_color_rgb)

        if (
            self._effect_cached_image is not None
            and self._effect_cache_key is not None
            and abs(self._effect_cache_key[0] - line_radius) < 1e-4
            and abs(self._effect_cache_key[1] - outline_radius) < 1e-4
            and abs(self._effect_cache_key[2] - smoothing) < 1e-4
            and self._effect_cache_key[3] == self._outline_color_rgb
        ):
            return self._effect_cached_image

        try:
            rendered = self._apply_strokes(
                self._overlay_array,
                line_radius,
                outline_radius,
                smoothing,
                self._outline_color_rgb,
            )
        except cv2.error:
            rendered = self._overlay_array
        else:
            if rendered is not self._overlay_array:
                rendered = np.ascontiguousarray(rendered)

        self._effect_cached_image = rendered
        self._effect_cache_key = cache_key
        return rendered

    @staticmethod
    def _normalise_color(value: Optional[str]) -> Tuple[str, Tuple[int, int, int]]:
        if not value:
            return ("transparent", (255, 255, 255))
        color = QColor(value)
        if not color.isValid():
            color = QColor("#FFFFFF")
        return (
            color.name(QColor.NameFormat.HexRgb).upper(),
            (color.red(), color.green(), color.blue()),
        )

    @staticmethod
    def _apply_strokes(
        image: np.ndarray,
        thickness: float,
        outline: float,
        smoothing: float,
        outline_color: Tuple[int, int, int],
    ) -> np.ndarray:
        if image.ndim != 3 or image.shape[2] < 4:
            return image

        alpha = image[..., 3]
        if not np.any(alpha):
            return image

        mask = alpha > 0
        result = image.copy()

        filled_mask = mask
        line_mask: Optional[np.ndarray] = None
        outline_mask = np.zeros_like(mask, dtype=bool)

        if thickness > 0.0:
            kernel_radius = max(1, int(math.ceil(thickness)))
            kernel_size = kernel_radius * 2 + 1
            kernel = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (kernel_size, kernel_size)
            )

            alpha_mask = np.where(mask, 255, 0).astype(np.uint8)
            dilated_alpha = cv2.dilate(alpha_mask, kernel, iterations=1)

            inverted = np.where(mask, 0, 255).astype(np.uint8)
            distances = cv2.distanceTransform(inverted, cv2.DIST_L2, 5)
            expanded = np.logical_or(mask, distances <= float(thickness))

            updated_alpha = result[..., 3]
            if np.any(expanded):
                updated_alpha = updated_alpha.copy()
                updated_alpha[expanded] = np.maximum(
                    updated_alpha[expanded], dilated_alpha[expanded]
                )
                result[..., 3] = updated_alpha

            filled_mask = result[..., 3] > 0

        line_mask = np.array(filled_mask, dtype=bool, copy=True)

        if np.any(filled_mask):
            rgb = result[..., :3]
            rgb[filled_mask, 0] = 255
            rgb[filled_mask, 1] = 0
            rgb[filled_mask, 2] = 0

        if outline > 0.0:
            outline_radius = max(1, int(math.ceil(outline)))
            outline_size = outline_radius * 2 + 1
            outline_kernel = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (outline_size, outline_size)
            )

            base_mask = result[..., 3] > 0
            base_alpha = np.where(base_mask, 255, 0).astype(np.uint8)
            expanded = cv2.dilate(base_alpha, outline_kernel, iterations=1)
            outline_mask = np.logical_and(expanded > 0, ~base_mask)

            if np.any(outline_mask):
                updated_alpha = result[..., 3].copy()
                updated_alpha[outline_mask] = np.maximum(
                    updated_alpha[outline_mask], expanded[outline_mask]
                )
                result[..., 3] = updated_alpha

                rgb = result[..., :3]
                r, g, b = outline_color
                rgb[outline_mask, 0] = r
                rgb[outline_mask, 1] = g
                rgb[outline_mask, 2] = b

        if smoothing > 0.0:
            base_result = result.copy()
            smooth_radius = max(1, int(math.ceil(smoothing)))
            kernel_size = smooth_radius * 2 + 1

            rgba = base_result.astype(np.float32) / 255.0
            alpha_plane = rgba[..., 3]
            premultiplied = rgba[..., :3] * alpha_plane[..., None]

            blurred_premultiplied = cv2.GaussianBlur(
                premultiplied, (kernel_size, kernel_size), 0
            )
            blurred_alpha = cv2.GaussianBlur(
                alpha_plane, (kernel_size, kernel_size), 0
            )

            blurred_alpha = np.clip(blurred_alpha, 0.0, 1.0)
            colour = np.zeros_like(blurred_premultiplied)
            mask = blurred_alpha > 1e-5
            if np.any(mask):
                colour[mask] = (
                    blurred_premultiplied[mask]
                    / blurred_alpha[mask, None]
                )

            rgba[..., :3] = np.clip(colour, 0.0, 1.0)
            rgba[..., 3] = blurred_alpha
            result = (rgba * 255.0 + 0.5).astype(np.uint8)

            core_mask = base_result[..., 3] >= 250
            if np.any(core_mask):
                result[..., :3][core_mask] = base_result[..., :3][core_mask]
                result[..., 3][core_mask] = base_result[..., 3][core_mask]

        if line_mask is not None and np.any(line_mask):
            result[..., 0][line_mask] = 255
            result[..., 1][line_mask] = 0
            result[..., 2][line_mask] = 0

        if np.any(outline_mask):
            r, g, b = outline_color
            result[..., 0][outline_mask] = r
            result[..., 1][outline_mask] = g
            result[..., 2][outline_mask] = b

        return result.astype(np.uint8, copy=False)


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
        painter.fillRect(rect, QColor("#f3f4f6"))

        border_color = "#38bdf8" if self._highlight_border else "#cbd5e1"
        border_pen = QPen(QColor(border_color))
        border_pen.setWidth(1)
        painter.setPen(border_pen)
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

        if not self._pixmap or not self._image_size:
            painter.setPen(QColor("#64748b"))
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
            handle_pen = QPen(QColor("#1f2933"), 1.5)

            for label, point in zip(corner_labels, corners):
                marker_rect = QRectF(point.x() - 9.0, point.y() - 9.0, 18.0, 18.0)
                painter.setBrush(handle_brush)
                painter.setPen(handle_pen)
                painter.drawEllipse(marker_rect)
                painter.setPen(QPen(QColor("#1f2933")))
                painter.drawText(marker_rect, Qt.AlignmentFlag.AlignCenter, label)

        if self._auto_points:
            marker_color = QColor("#f97316")
            pen = QPen(QColor("#1f2933"), 1.2)
            for index, point in enumerate(self._auto_points, start=1):
                px = dest_rect.left() + (point.x() / image_width) * dest_rect.width()
                py = dest_rect.top() + (point.y() / image_height) * dest_rect.height()
                marker_rect = QRectF(px - 7.0, py - 7.0, 14.0, 14.0)
                painter.setBrush(marker_color)
                painter.setPen(pen)
                painter.drawEllipse(marker_rect)
                painter.setPen(QPen(QColor("#1f2933")))
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
        self._view.annotations_changed.connect(self._handle_annotations_changed)
        self._view.selection_changed.connect(self._update_annotation_selection_state)
        self._view.annotation_double_clicked.connect(self._handle_annotation_double_clicked)
        self._view.annotation_committed.connect(self._handle_annotation_committed)

        self._duplicate_button = QPushButton("Copy")
        self._duplicate_button.setParent(self._view.viewport())
        self._duplicate_button.setVisible(False)
        self._duplicate_button.setObjectName("annotationDuplicateButton")
        self._duplicate_button.setFixedHeight(26)
        self._duplicate_button.clicked.connect(self._handle_duplicate_selected)
        self._node_button = QPushButton("Edit nodes")
        self._node_button.setParent(self._view.viewport())
        self._node_button.setVisible(False)
        self._node_button.setObjectName("annotationNodeButton")
        self._node_button.setFixedHeight(26)
        self._node_button.clicked.connect(self._handle_edit_nodes)
        self._tracked_annotation_item: Optional[QGraphicsItem] = None
        self._node_edit_item: Optional[AnnotationPathItem] = None

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

        self._tab_widget = QTabWidget()

        self._control_slider_width = 260

        self._annotation_header = QLabel("Annotations")
        self._annotation_header.setObjectName("annotationHeader")

        self._annotation_tool_group = QButtonGroup(self)
        self._annotation_tool_group.setExclusive(True)

        self._select_tool = QPushButton("Select / Move")
        self._select_tool.setCheckable(True)
        self._select_tool.setChecked(True)
        self._select_tool.toggled.connect(
            lambda checked: self._handle_annotation_tool_selected("select", checked)
        )
        self._annotation_tool_group.addButton(self._select_tool)

        self._text_tool = QPushButton("Text")
        self._text_tool.setCheckable(True)
        self._text_tool.toggled.connect(
            lambda checked: self._handle_annotation_tool_selected("text", checked)
        )
        self._annotation_tool_group.addButton(self._text_tool)

        self._line_tool = QPushButton("Line")
        self._line_tool.setCheckable(True)
        self._line_tool.toggled.connect(
            lambda checked: self._handle_annotation_tool_selected("line", checked)
        )
        self._annotation_tool_group.addButton(self._line_tool)

        self._polygon_tool = QPushButton("Polygon")
        self._polygon_tool.setCheckable(True)
        self._polygon_tool.toggled.connect(
            lambda checked: self._handle_annotation_tool_selected("polygon", checked)
        )
        self._annotation_tool_group.addButton(self._polygon_tool)

        self._edit_text_button = QPushButton("Edit selected text…")
        self._edit_text_button.setEnabled(False)
        self._edit_text_button.clicked.connect(self._handle_edit_text)

        self._fill_color_button = QPushButton("Fill")
        self._fill_color_button.setObjectName("annotationFillColor")
        self._fill_color_button.clicked.connect(
            lambda: self._choose_annotation_color("fill")
        )

        self._stroke_color_button = QPushButton("Stroke")
        self._stroke_color_button.setObjectName("annotationStrokeColor")
        self._stroke_color_button.clicked.connect(
            lambda: self._choose_annotation_color("stroke")
        )

        self._annotation_stroke_slider = QSlider(Qt.Orientation.Horizontal)
        self._annotation_stroke_slider.setRange(0, 80)
        self._annotation_stroke_slider.setPageStep(2)
        self._annotation_stroke_slider.setValue(20)
        self._annotation_stroke_slider.valueChanged.connect(
            self._handle_annotation_stroke_changed
        )
        self._configure_control_slider(self._annotation_stroke_slider)

        self._annotation_stroke_value_label = QLabel("2.0 px")
        self._annotation_stroke_value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self._annotation_outline_color_button = QPushButton("Outline")
        self._annotation_outline_color_button.setObjectName("annotationOutlineColor")
        self._annotation_outline_color_button.clicked.connect(
            lambda: self._choose_annotation_color("outline")
        )

        self._annotation_outline_slider = QSlider(Qt.Orientation.Horizontal)
        self._annotation_outline_slider.setRange(0, 60)
        self._annotation_outline_slider.setPageStep(2)
        self._annotation_outline_slider.setValue(10)
        self._annotation_outline_slider.valueChanged.connect(
            self._handle_annotation_outline_changed
        )
        self._configure_control_slider(self._annotation_outline_slider)

        self._annotation_outline_value_label = QLabel("1.0 px")
        self._annotation_outline_value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self._shadow_checkbox = QCheckBox("Drop shadow")
        self._shadow_checkbox.setChecked(True)
        self._shadow_checkbox.toggled.connect(self._handle_shadow_toggled)

        self._shadow_blur_slider = QSlider(Qt.Orientation.Horizontal)
        self._shadow_blur_slider.setRange(0, 60)
        self._shadow_blur_slider.setPageStep(2)
        self._shadow_blur_slider.setValue(8)
        self._shadow_blur_slider.valueChanged.connect(self._handle_shadow_blur_changed)
        self._configure_control_slider(self._shadow_blur_slider)

        self._shadow_blur_label = QLabel("8 px")
        self._shadow_blur_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self._font_picker = QFontComboBox()
        self._font_picker.setEditable(False)
        self._font_picker.currentFontChanged.connect(self._handle_font_changed)

        self._font_size_spin = QSpinBox()
        self._font_size_spin.setRange(8, 96)
        self._font_size_spin.setValue(28)
        self._font_size_spin.valueChanged.connect(self._handle_font_size_changed)

        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(0, 100)
        self._opacity_slider.setPageStep(5)
        self._opacity_slider.setValue(65)
        self._opacity_slider.valueChanged.connect(self._handle_opacity_changed)
        self._configure_control_slider(self._opacity_slider)

        self._opacity_value_label = QLabel("65%")
        self._opacity_value_label.setObjectName("overlayOpacityValue")
        self._opacity_value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self._line_thickness_slider = QSlider(Qt.Orientation.Horizontal)
        self._line_thickness_slider.setRange(0, 30)
        self._line_thickness_slider.setPageStep(1)
        self._line_thickness_slider.setValue(0)
        self._line_thickness_slider.valueChanged.connect(self._handle_line_thickness_changed)
        self._configure_control_slider(self._line_thickness_slider)

        self._line_thickness_value_label = QLabel("0.0 px")
        self._line_thickness_value_label.setObjectName("overlayLineBalanceValue")
        self._line_thickness_value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self._edge_smoothing_slider = QSlider(Qt.Orientation.Horizontal)
        self._edge_smoothing_slider.setRange(0, 50)
        self._edge_smoothing_slider.setPageStep(1)
        default_smoothing = int(round(self._state.overlay.edge_smoothing * 10))
        default_smoothing = max(0, min(default_smoothing, 50))
        self._edge_smoothing_slider.setValue(default_smoothing)
        self._edge_smoothing_slider.valueChanged.connect(
            self._handle_edge_smoothing_changed
        )
        self._configure_control_slider(self._edge_smoothing_slider)

        self._edge_smoothing_value_label = QLabel("Off")
        self._edge_smoothing_value_label.setObjectName("overlaySmoothingValue")
        self._edge_smoothing_value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self._outline_color_button = QPushButton()
        self._outline_color_button.setObjectName("overlayOutlineColor")
        self._outline_color_button.setMinimumWidth(72)
        self._outline_color_button.clicked.connect(self._choose_outline_color)

        self._outline_thickness_slider = QSlider(Qt.Orientation.Horizontal)
        self._outline_thickness_slider.setRange(0, 60)
        self._outline_thickness_slider.setPageStep(1)
        default_outline = int(round(self._state.overlay.outline_thickness * 10))
        self._outline_thickness_slider.setValue(default_outline)
        self._outline_thickness_slider.valueChanged.connect(
            self._handle_outline_thickness_changed
        )
        self._configure_control_slider(self._outline_thickness_slider)

        self._outline_thickness_value_label = QLabel("1.0 px")
        self._outline_thickness_value_label.setObjectName("overlayOutlineValue")
        self._outline_thickness_value_label.setAlignment(
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

        overlay_row = QHBoxLayout()
        overlay_row.addWidget(self._overlay_checkbox)
        overlay_row.addSpacing(12)
        opacity_label = QLabel("Opacity")
        overlay_row.addWidget(opacity_label)
        overlay_row.addWidget(self._opacity_slider)
        overlay_row.addWidget(self._opacity_value_label)
        overlay_row.addStretch(1)

        thickness_row = QHBoxLayout()
        thickness_row.addSpacing(12)
        thickness_label = QLabel("Line thickness")
        thickness_row.addWidget(thickness_label)
        thickness_row.addWidget(self._line_thickness_slider)
        thickness_row.addWidget(self._line_thickness_value_label)
        thickness_row.addStretch(1)

        smoothing_row = QHBoxLayout()
        smoothing_row.addSpacing(12)
        smoothing_label = QLabel("Line smoothing")
        smoothing_row.addWidget(smoothing_label)
        smoothing_row.addWidget(self._edge_smoothing_slider)
        smoothing_row.addWidget(self._edge_smoothing_value_label)
        smoothing_row.addStretch(1)

        outline_row = QHBoxLayout()
        outline_row.addSpacing(12)
        outline_label = QLabel("Outline")
        outline_row.addWidget(outline_label)
        outline_row.addWidget(self._outline_color_button)
        outline_row.addWidget(self._outline_thickness_slider)
        outline_row.addWidget(self._outline_thickness_value_label)
        outline_row.addStretch(1)

        alignment_buttons = QHBoxLayout()
        alignment_buttons.setSpacing(8)
        alignment_buttons.addWidget(self._color_filter_button)
        alignment_buttons.addWidget(self._reset_pins_button)
        alignment_buttons.addStretch(1)

        alignment_tab = QWidget()
        alignment_layout = QVBoxLayout(alignment_tab)
        alignment_layout.setContentsMargins(12, 8, 12, 8)
        alignment_layout.setSpacing(8)
        alignment_layout.addLayout(mode_row)
        alignment_layout.addLayout(overlay_row)
        alignment_layout.addLayout(thickness_row)
        alignment_layout.addLayout(smoothing_row)
        alignment_layout.addLayout(outline_row)
        alignment_layout.addLayout(alignment_buttons)
        alignment_layout.addStretch(1)

        tool_row = QHBoxLayout()
        tool_row.addSpacing(12)
        tool_label = QLabel("Tool")
        tool_row.addWidget(tool_label)
        tool_row.addWidget(self._select_tool)
        tool_row.addWidget(self._text_tool)
        tool_row.addWidget(self._line_tool)
        tool_row.addWidget(self._polygon_tool)
        tool_row.addStretch(1)

        edit_row = QHBoxLayout()
        edit_row.addSpacing(12)
        edit_row.addWidget(self._edit_text_button)
        edit_row.addStretch(1)

        style_row = QHBoxLayout()
        style_row.addSpacing(12)
        self._preset_style_button = QPushButton("Preset options…")
        self._preset_style_button.clicked.connect(self._open_annotation_presets_dialog)
        self._selection_style_button = QPushButton("Selected options…")
        self._selection_style_button.clicked.connect(self._open_selected_annotation_dialog)
        self._selection_style_button.setEnabled(False)
        style_row.addWidget(self._preset_style_button)
        style_row.addWidget(self._selection_style_button)
        style_row.addStretch(1)

        annotation_tab = QWidget()
        annotation_layout = QVBoxLayout(annotation_tab)
        annotation_layout.setContentsMargins(12, 8, 12, 8)
        annotation_layout.setSpacing(8)
        annotation_layout.addWidget(self._annotation_header)
        annotation_layout.addLayout(tool_row)
        annotation_layout.addLayout(edit_row)
        annotation_layout.addLayout(style_row)
        annotation_layout.addStretch(1)

        self._tab_widget.addTab(alignment_tab, "Pinning")
        self._tab_widget.addTab(annotation_tab, "Annotations")

        layout.addWidget(self._tab_widget)

        footer_row = QHBoxLayout()
        footer_row.setSpacing(8)
        footer_row.addStretch(1)
        footer_row.addWidget(self._status_label)
        footer_row.addWidget(self._restart_button)
        layout.addLayout(footer_row)

        self.setLayout(layout)

        self._set_outline_color_button(self._state.overlay.outline_color)
        self._update_outline_thickness_label(self._state.overlay.outline_thickness)
        self._update_edge_smoothing_label(self._state.overlay.edge_smoothing)
        self._sync_annotation_controls()

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
        self._sync_annotation_controls()
        self._base_overlay_image = base_overlay
        self._color_processing_token += 1
        self._latest_color_token = self._color_processing_token

        has_images = photo is not None and base_overlay is not None

        if not has_images:
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
            thickness = float(self._state.overlay.line_thickness)
            self._line_thickness_slider.blockSignals(True)
            self._line_thickness_slider.setValue(int(round(thickness * 10)))
            self._line_thickness_slider.blockSignals(False)
            self._update_line_thickness_label(thickness)
            self._view.set_line_thickness(thickness)
            smoothing = float(self._state.overlay.edge_smoothing)
            self._edge_smoothing_slider.blockSignals(True)
            self._edge_smoothing_slider.setValue(int(round(smoothing * 10)))
            self._edge_smoothing_slider.blockSignals(False)
            self._update_edge_smoothing_label(smoothing)
            self._view.set_edge_smoothing(smoothing)
            outline = float(self._state.overlay.outline_thickness)
            self._outline_thickness_slider.blockSignals(True)
            self._outline_thickness_slider.setValue(int(round(outline * 10)))
            self._outline_thickness_slider.blockSignals(False)
            self._update_outline_thickness_label(outline)
            self._set_outline_color_button(self._state.overlay.outline_color)
            self._view.set_outline_settings(
                self._state.overlay.outline_color,
                outline,
            )
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

        display_overlay = filtered_overlay
        if display_overlay is None and base_overlay is not None:
            has_filters = bool(
                self._state.overlay.color_filters_keep
                or self._state.overlay.color_filters_remove
            )
            if has_filters:
                try:
                    display_overlay = color_filters.apply_color_filters(
                        base_overlay,
                        self._state.overlay.color_filters_keep,
                        self._state.overlay.color_filters_remove,
                    )
                    self._state.overlay.filtered_overlay = display_overlay
                except Exception:
                    display_overlay = base_overlay
            else:
                display_overlay = base_overlay

        filtered_overlay = self._state.overlay.filtered_overlay
        if display_overlay is filtered_overlay:
            filtered_overlay = display_overlay

        self._view.load_images(photo_pixmap, display_overlay, point_sequence)
        self._view.setEnabled(True)
        self._set_display_overlay(display_overlay)
        self._set_controls_enabled(True)

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

        thickness = float(self._state.overlay.line_thickness)
        self._line_thickness_slider.blockSignals(True)
        self._line_thickness_slider.setValue(int(round(thickness * 10)))
        self._line_thickness_slider.blockSignals(False)
        self._update_line_thickness_label(thickness)

        smoothing = float(self._state.overlay.edge_smoothing)
        self._edge_smoothing_slider.blockSignals(True)
        self._edge_smoothing_slider.setValue(int(round(smoothing * 10)))
        self._edge_smoothing_slider.blockSignals(False)
        self._update_edge_smoothing_label(smoothing)

        outline = float(self._state.overlay.outline_thickness)
        self._outline_thickness_slider.blockSignals(True)
        self._outline_thickness_slider.setValue(int(round(outline * 10)))
        self._outline_thickness_slider.blockSignals(False)
        self._update_outline_thickness_label(outline)
        self._set_outline_color_button(self._state.overlay.outline_color)

        self._view.set_line_thickness(thickness)
        self._view.set_edge_smoothing(smoothing)
        self._view.set_outline_settings(self._state.overlay.outline_color, outline)
        self._view.set_overlay_settings(self._state.overlay.show_overlay, opacity)
        self._view.set_annotation_settings(self._state.annotations)
        self._view.load_annotations(self._state.annotations.annotations)
        self._view.set_annotation_mode(self._state.annotations.active_tool)
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

    def _handle_line_thickness_changed(self, value: int) -> None:
        thickness = max(0.0, min(value / 10.0, 3.0))
        self._state.overlay.line_thickness = thickness
        self._view.set_line_thickness(thickness)
        self._update_line_thickness_label(thickness)

    def _handle_edge_smoothing_changed(self, value: int) -> None:
        smoothing = max(0.0, min(value / 10.0, 5.0))
        self._state.overlay.edge_smoothing = smoothing
        self._view.set_edge_smoothing(smoothing)
        self._update_edge_smoothing_label(smoothing)

    def _handle_outline_thickness_changed(self, value: int) -> None:
        thickness = max(0.0, min(value / 10.0, 6.0))
        self._state.overlay.outline_thickness = thickness
        self._view.set_outline_settings(self._state.overlay.outline_color, thickness)
        self._update_outline_thickness_label(thickness)

    def _handle_opacity_changed(self, value: int) -> None:
        opacity = max(0.0, min(value / 100.0, 1.0))
        self._state.overlay.opacity = opacity
        self._view.set_overlay_settings(self._state.overlay.show_overlay, opacity)
        self._update_opacity_label(opacity)

    def _handle_overlay_visibility(self, checked: bool) -> None:
        self._state.overlay.show_overlay = checked
        self._view.set_overlay_settings(checked, self._state.overlay.opacity)

    def _handle_annotation_tool_selected(self, tool: str, checked: bool) -> None:
        if not checked:
            return
        self._set_active_annotation_tool(tool)

    def _set_active_annotation_tool(self, tool: str) -> None:
        self._state.annotations.active_tool = tool
        self._view.set_annotation_mode(tool)

        self._select_tool.blockSignals(True)
        self._text_tool.blockSignals(True)
        self._line_tool.blockSignals(True)
        self._polygon_tool.blockSignals(True)

        self._select_tool.setChecked(tool == "select")
        self._text_tool.setChecked(tool == "text")
        self._line_tool.setChecked(tool == "line")
        self._polygon_tool.setChecked(tool == "polygon")

        self._select_tool.blockSignals(False)
        self._text_tool.blockSignals(False)
        self._line_tool.blockSignals(False)
        self._polygon_tool.blockSignals(False)

        self._update_annotation_selection_state()

    def _handle_annotation_committed(self) -> None:
        self._set_active_annotation_tool("select")

    def _handle_edit_text(self) -> None:
        self._view.edit_selected_text()

    def _emit_annotations(self) -> None:
        self._view._emit_annotations()

    def resizeEvent(self, event):  # type: ignore[override]
        super().resizeEvent(event)
        self._refresh_annotation_helper_positions()

    def _update_annotation_selection_state(self) -> None:
        selected = self._view.selected_annotation()
        if self._tracked_annotation_item is not None and self._tracked_annotation_item is not selected:
            try:
                self._tracked_annotation_item.changed.disconnect(self._handle_selected_item_changed)  # type: ignore[attr-defined]
            except Exception:
                pass
            self._tracked_annotation_item = None
            self._set_node_edit_target(None)

        self._tracked_annotation_item = selected

        if selected is not None:
            try:
                selected.changed.connect(self._handle_selected_item_changed)  # type: ignore[attr-defined]
            except Exception:
                pass
            self._duplicate_button.setVisible(True)
            self._refresh_annotation_helper_positions()
            if isinstance(selected, AnnotationPathItem):
                self._node_button.setVisible(True)
                self._node_button.setText("Edit nodes" if selected is not self._node_edit_item else "Exit node edit")
            else:
                self._node_button.setVisible(False)
                self._set_node_edit_target(None)
        else:
            self._duplicate_button.setVisible(False)
            self._node_button.setVisible(False)
            self._set_node_edit_target(None)

        self._edit_text_button.setEnabled(self._view.has_selected_text())
        self._selection_style_button.setEnabled(selected is not None)

    def _handle_selected_item_changed(self) -> None:
        self._refresh_annotation_helper_positions()

    def _refresh_annotation_helper_positions(self) -> None:
        if not self._duplicate_button.isVisible():
            self._node_button.setVisible(False)
            return

        item = self._tracked_annotation_item or self._view.selected_annotation()
        if item is None:
            self._duplicate_button.setVisible(False)
            self._node_button.setVisible(False)
            return

        scene_rect = item.mapToScene(item.boundingRect()).boundingRect()
        target = scene_rect.topRight() + QPointF(8, -8)
        view_pos = self._view.mapFromScene(target)

        x = max(0, min(int(view_pos.x()), self._view.viewport().width() - self._duplicate_button.width()))
        y = max(0, min(int(view_pos.y()), self._view.viewport().height() - self._duplicate_button.height()))
        self._duplicate_button.move(x, y)

        if self._node_button.isVisible():
            node_x = max(0, min(int(view_pos.x()), self._view.viewport().width() - self._node_button.width()))
            node_y = max(0, min(int(view_pos.y() + self._duplicate_button.height() + 6), self._view.viewport().height() - self._node_button.height()))
            self._node_button.move(node_x, node_y)

    def _handle_duplicate_selected(self) -> None:
        self._view.duplicate_selected_annotation()
        self._refresh_annotation_helper_positions()

    def _handle_edit_nodes(self) -> None:
        selected = self._view.selected_annotation()
        if not isinstance(selected, AnnotationPathItem):
            self._set_node_edit_target(None)
            return
        if self._node_edit_item is selected:
            self._set_node_edit_target(None)
        else:
            self._set_node_edit_target(selected)

    def _set_node_edit_target(self, item: Optional[AnnotationPathItem]) -> None:
        if self._node_edit_item and self._node_edit_item is not item:
            self._node_edit_item.set_edit_mode(False)
        self._node_edit_item = item
        if item is not None:
            item.set_edit_mode(True)
            self._node_button.setVisible(True)
            self._node_button.setText("Exit node edit")
        else:
            self._node_button.setText("Edit nodes")
        self._refresh_annotation_helper_positions()

    def _open_annotation_presets_dialog(self) -> None:
        tool = self._state.annotations.active_tool
        dialog = _AnnotationStyleDialog(
            self,
            self._state.annotations,
            allow_font=tool == "text",
            allow_fill=tool != "line",
            allow_stroke=tool != "text",
            allow_outline=True,
            allow_text_value=False,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        updated = dialog.result_settings()
        updated.annotations = list(self._state.annotations.annotations)
        self._state.annotations = updated
        self._view.set_annotation_settings(updated, restyle_existing=False)

    def _open_selected_annotation_dialog(self, force_item: Optional[QGraphicsItem] = None) -> None:
        item = force_item or self._view.selected_annotation()
        if item is None:
            return

        base = self._state.annotations
        allow_font = isinstance(item, AnnotationTextItem)
        allow_fill = isinstance(item, AnnotationTextItem) or (
            isinstance(item, AnnotationPathItem) and item._closed
        )
        allow_stroke = not isinstance(item, AnnotationTextItem)
        allow_text_value = isinstance(item, AnnotationTextItem)

        if isinstance(item, AnnotationTextItem):
            base = replace(
                base,
                fill_color=item._fill_color,
                fill_alpha=item._fill_alpha,
                stroke_color=item._stroke_color,
                stroke_width=item._stroke_width,
                outline_color=item._outline_color,
                outline_width=item._outline_width,
                shadow_enabled=item._shadow_enabled,
                shadow_blur=item._shadow_blur,
                stroke_pattern=getattr(item, "_stroke_pattern", base.stroke_pattern),
                start_marker=getattr(item, "_start_marker", base.start_marker),
                end_marker=getattr(item, "_end_marker", base.end_marker),
                font_family=item._font_family,
                font_size=item._font_size,
            )
        elif isinstance(item, AnnotationPathItem):
            base = replace(
                base,
                fill_color=item._fill_color,
                fill_alpha=item._fill_alpha,
                stroke_color=item._stroke_color,
                stroke_width=item._stroke_width,
                stroke_pattern=item._stroke_pattern,
                start_marker=item._start_marker,
                end_marker=item._end_marker,
                outline_color=item._outline_color,
                outline_width=item._outline_width,
                shadow_enabled=item._shadow_enabled,
                shadow_blur=item._shadow_blur,
            )

        snapshot_settings = base
        snapshot_text = item.toPlainText() if isinstance(item, AnnotationTextItem) else None

        def _apply_to_item(updated: AnnotationSettings, maybe_text: Optional[str]) -> None:
            if isinstance(item, AnnotationTextItem):
                item.set_style(
                    fill_color=updated.fill_color,
                    fill_alpha=updated.fill_alpha,
                    stroke_color=updated.stroke_color,
                    stroke_width=0.0,
                    outline_color=updated.outline_color,
                    outline_width=updated.outline_width,
                    shadow_enabled=updated.shadow_enabled,
                    shadow_blur=updated.shadow_blur,
                    font_family=updated.font_family,
                    font_size=updated.font_size,
                )
                if maybe_text is not None:
                    item.set_text(maybe_text or "")
            elif isinstance(item, AnnotationPathItem):
                item.set_style(
                    fill_color=updated.fill_color if allow_fill else None,
                    fill_alpha=updated.fill_alpha if allow_fill else 0.0,
                    stroke_color=updated.stroke_color,
                    stroke_width=updated.stroke_width,
                    stroke_pattern=updated.stroke_pattern,
                    start_marker=updated.start_marker,
                    end_marker=updated.end_marker,
                    outline_color=updated.outline_color,
                    outline_width=updated.outline_width,
                    shadow_enabled=updated.shadow_enabled,
                    shadow_blur=updated.shadow_blur,
                )
            self._emit_annotations()

        dialog = _AnnotationStyleDialog(
            self,
            base,
            allow_font=allow_font,
            allow_fill=allow_fill,
            allow_stroke=allow_stroke,
            allow_outline=True,
            allow_text_value=allow_text_value,
            text_value=item.toPlainText() if isinstance(item, AnnotationTextItem) else "",
            live_apply=_apply_to_item,
        )

        result = dialog.exec()
        if result != QDialog.DialogCode.Accepted:
            _apply_to_item(snapshot_settings, snapshot_text)
            return

        _apply_to_item(dialog.result_settings(), dialog.text_value())

    def _handle_annotation_double_clicked(self, item: QGraphicsItem) -> None:
        self._open_selected_annotation_dialog(force_item=item)


    def _choose_annotation_color(self, role: str) -> None:
        annotations = self._state.annotations
        if role == "fill":
            current = annotations.fill_color
            button = self._fill_color_button
            title = "Select fill colour"
        elif role == "stroke":
            current = annotations.stroke_color
            button = self._stroke_color_button
            title = "Select stroke colour"
        else:
            current = annotations.outline_color
            button = self._annotation_outline_color_button
            title = "Select outline colour"

        initial = QColor(current)
        if not initial.isValid():
            initial = QColor("#FFFFFF")
        color = QColorDialog.getColor(initial, self, title)
        if not color.isValid():
            return

        normalized, _ = self._style_color_button(
            button, color.name(), button.objectName()
        )
        if role == "fill":
            annotations.fill_color = normalized
        elif role == "stroke":
            annotations.stroke_color = normalized
        else:
            annotations.outline_color = normalized
        self._push_annotation_settings_to_view()

    def _handle_annotation_stroke_changed(self, value: int) -> None:
        width = max(0.0, min(value / 10.0, 8.0))
        self._state.annotations.stroke_width = width
        self._update_annotation_stroke_label(width)
        self._push_annotation_settings_to_view()

    def _handle_annotation_outline_changed(self, value: int) -> None:
        width = max(0.0, min(value / 10.0, 6.0))
        self._state.annotations.outline_width = width
        self._update_annotation_outline_label(width)
        self._push_annotation_settings_to_view()

    def _handle_shadow_toggled(self, enabled: bool) -> None:
        self._state.annotations.shadow_enabled = enabled
        self._shadow_blur_slider.setEnabled(enabled)
        self._push_annotation_settings_to_view()

    def _handle_shadow_blur_changed(self, value: int) -> None:
        blur = max(0.0, min(float(value), 60.0))
        self._state.annotations.shadow_blur = blur
        self._update_shadow_blur_label(blur)
        self._push_annotation_settings_to_view()

    def _handle_font_changed(self, font: QFont) -> None:
        self._state.annotations.font_family = font.family()
        self._push_annotation_settings_to_view()

    def _handle_font_size_changed(self, size: int) -> None:
        self._state.annotations.font_size = max(8, min(size, 96))
        self._push_annotation_settings_to_view()

    def _handle_annotations_changed(self, annotations: list[AnnotationItem]) -> None:
        self._state.annotations.annotations = annotations

    def _choose_outline_color(self) -> None:
        initial = QColor(self._state.overlay.outline_color)
        if not initial.isValid():
            initial = QColor("#FFFFFF")
        color = QColorDialog.getColor(initial, self, "Select outline colour")
        if not color.isValid():
            return

        normalized, _ = EditorGraphicsView._normalise_color(color.name())
        if normalized == self._state.overlay.outline_color:
            return

        self._state.overlay.outline_color = normalized
        self._set_outline_color_button(normalized)
        self._view.set_outline_settings(normalized, self._state.overlay.outline_thickness)

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
        self._tab_widget.setTabEnabled(0, enabled)
        self._tab_widget.setTabEnabled(1, enabled)
        self._overlay_checkbox.setEnabled(enabled)
        self._opacity_slider.setEnabled(enabled)
        self._line_thickness_slider.setEnabled(enabled)
        self._edge_smoothing_slider.setEnabled(enabled)
        self._outline_thickness_slider.setEnabled(enabled)
        self._outline_color_button.setEnabled(enabled)
        self._reset_pins_button.setEnabled(enabled)
        self._select_tool.setEnabled(enabled)
        self._text_tool.setEnabled(enabled)
        self._line_tool.setEnabled(enabled)
        self._polygon_tool.setEnabled(enabled)
        self._edit_text_button.setEnabled(enabled and self._view.has_selected_text())
        self._preset_style_button.setEnabled(enabled)
        self._selection_style_button.setEnabled(enabled and self._view.selected_annotation() is not None)
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

    def _sync_annotation_controls(self) -> None:
        """Align annotation toolbar controls with state."""

        settings = self._state.annotations

        self._select_tool.blockSignals(True)
        self._text_tool.blockSignals(True)
        self._line_tool.blockSignals(True)
        self._polygon_tool.blockSignals(True)

        self._select_tool.setChecked(settings.active_tool == "select")
        self._text_tool.setChecked(settings.active_tool == "text")
        self._line_tool.setChecked(settings.active_tool == "line")
        self._polygon_tool.setChecked(settings.active_tool == "polygon")

        self._select_tool.blockSignals(False)
        self._text_tool.blockSignals(False)
        self._line_tool.blockSignals(False)
        self._polygon_tool.blockSignals(False)

        self._style_color_button(self._fill_color_button, settings.fill_color, "annotationFillColor")
        self._style_color_button(
            self._stroke_color_button, settings.stroke_color, "annotationStrokeColor"
        )
        self._style_color_button(
            self._annotation_outline_color_button,
            settings.outline_color,
            "annotationOutlineColor",
        )

        stroke_value = int(round(max(0.0, min(settings.stroke_width, 8.0)) * 10))
        self._annotation_stroke_slider.blockSignals(True)
        self._annotation_stroke_slider.setValue(stroke_value)
        self._annotation_stroke_slider.blockSignals(False)
        self._update_annotation_stroke_label(stroke_value / 10.0)

        outline_value = int(round(max(0.0, min(settings.outline_width, 6.0)) * 10))
        self._annotation_outline_slider.blockSignals(True)
        self._annotation_outline_slider.setValue(outline_value)
        self._annotation_outline_slider.blockSignals(False)
        self._update_annotation_outline_label(outline_value / 10.0)

        self._push_annotation_settings_to_view()

    def _push_annotation_settings_to_view(self) -> None:
        settings = self._state.annotations
        self._view.set_annotation_settings(settings)

        blur_value = max(0, min(int(round(settings.shadow_blur)), 60))
        self._shadow_checkbox.blockSignals(True)
        self._shadow_checkbox.setChecked(settings.shadow_enabled)
        self._shadow_checkbox.blockSignals(False)
        self._shadow_blur_slider.blockSignals(True)
        self._shadow_blur_slider.setEnabled(settings.shadow_enabled)
        self._shadow_blur_slider.setValue(blur_value)
        self._shadow_blur_slider.blockSignals(False)
        self._update_shadow_blur_label(float(blur_value))

        self._font_picker.blockSignals(True)
        desired_font = QFont(settings.font_family)
        self._font_picker.setCurrentFont(desired_font)
        self._font_picker.blockSignals(False)

        font_size = max(8, min(settings.font_size, 96))
        self._font_size_spin.blockSignals(True)
        self._font_size_spin.setValue(font_size)
        self._font_size_spin.blockSignals(False)

    def _set_display_overlay(self, image: Optional[Image.Image]) -> None:
        self._current_overlay_image = image
        self._view.update_overlay_image(image)
        self._preview_panel.set_overlay_image(self._base_overlay_image)

    def _update_opacity_label(self, opacity: float) -> None:
        percentage = int(round(opacity * 100))
        self._opacity_value_label.setText(f"{percentage}%")

    def _update_line_thickness_label(self, thickness: float) -> None:
        if thickness <= 0.001:
            self._line_thickness_value_label.setText("Off")
        else:
            self._line_thickness_value_label.setText(f"{thickness:.1f} px")

    def _update_edge_smoothing_label(self, smoothing: float) -> None:
        if smoothing <= 0.001:
            self._edge_smoothing_value_label.setText("Off")
        else:
            self._edge_smoothing_value_label.setText(f"{smoothing:.1f} px")

    def _update_outline_thickness_label(self, thickness: float) -> None:
        if thickness <= 0.001:
            self._outline_thickness_value_label.setText("Off")
        else:
            self._outline_thickness_value_label.setText(f"{thickness:.1f} px")

    def _update_annotation_stroke_label(self, width: float) -> None:
        if width <= 0.001:
            self._annotation_stroke_value_label.setText("Off")
        else:
            self._annotation_stroke_value_label.setText(f"{width:.1f} px")

    def _update_annotation_outline_label(self, width: float) -> None:
        if width <= 0.001:
            self._annotation_outline_value_label.setText("Off")
        else:
            self._annotation_outline_value_label.setText(f"{width:.1f} px")

    def _update_shadow_blur_label(self, blur: float) -> None:
        if blur <= 0.001:
            self._shadow_blur_label.setText("Off")
        else:
            self._shadow_blur_label.setText(f"{blur:.0f} px")

    def _configure_control_slider(self, slider: QSlider) -> None:
        slider.setFixedWidth(self._control_slider_width)
        slider.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def _set_outline_color_button(self, color: str) -> None:
        self._style_color_button(self._outline_color_button, color, "overlayOutlineColor")

    def _shutdown_color_threads(self) -> None:
        """Ensure background colour filter threads exit before destruction."""

        for token, thread in list(self._color_threads.items()):
            if thread.isRunning():
                thread.quit()
                thread.wait(5000)
            self._color_threads.pop(token, None)

    def _style_color_button(
        self, button: QPushButton, color: str, object_name: str
    ) -> Tuple[str, Tuple[int, int, int]]:
        """Apply consistent colouring to colour picker buttons."""

        normalized, rgb = EditorGraphicsView._normalise_color(color)
        brightness = (rgb[0] * 299 + rgb[1] * 587 + rgb[2] * 114) / 1000
        text_color = "#1f2933" if brightness > 160 else "#f8fafc"
        disabled_color = (
            "rgba(31, 41, 51, 0.6)" if text_color == "#1f2933" else "rgba(248, 250, 252, 0.6)"
        )
        style = (
            f"QPushButton#{object_name} {{"
            f" background-color: {normalized}; color: {text_color};"
            " border: 1px solid #94a3b8; padding: 4px 12px; border-radius: 4px;"
            " }\n"
            f"QPushButton#{object_name}:disabled {{"
            f" background-color: {normalized}; color: {disabled_color};"
            " }"
        )
        button.setStyleSheet(style)
        button.setText("None" if color is None else normalized)
        button.setToolTip("None" if color is None else normalized)
        return normalized, rgb
        self._color_workers.clear()

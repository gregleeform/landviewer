"""Application state models used by the desktop port."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import List, Literal, Optional, Tuple

from PIL import Image


class AppStage(Enum):
    """Represents the major screens of the application."""

    UPLOAD = auto()
    CROP = auto()
    EDIT = auto()


@dataclass(slots=True)
class ColorFilterSetting:
    """Represents a single keep/remove colour filter configuration."""

    color: str = "#000000"
    tolerance: int = 50


def default_keep_filters() -> List[ColorFilterSetting]:
    """Return the initial keep filter set matching the web prototype."""

    return [ColorFilterSetting("#FF0000", 50)]


def default_remove_filters() -> List[ColorFilterSetting]:
    """Return the initial remove filter set matching the web prototype."""

    return [
        ColorFilterSetting("#FFFFFF", 50),
        ColorFilterSetting("#FFFF00", 50),
    ]


@dataclass(slots=True)
class ImageSelection:
    """Holds metadata about a selected image file."""

    path: Optional[Path] = None
    image: Optional[Image.Image] = None
    resized_for_performance: bool = False
    cropped_image: Optional[Image.Image] = None
    crop_rect: Optional[Tuple[int, int, int, int]] = None
    rotation: float = 0.0

    def clear(self) -> None:
        """Resets the stored data for the slot."""
        self.path = None
        self.image = None
        self.resized_for_performance = False
        self.cropped_image = None
        self.crop_rect = None
        self.rotation = 0.0


@dataclass(slots=True)
class OverlaySettings:
    """Stores overlay visibility and alignment preferences."""

    show_overlay: bool = True
    opacity: float = 0.65
    line_thickness: float = 0.0
    edge_smoothing: float = 0.0
    outline_thickness: float = 1.0
    outline_color: str = "#FFFFFF"
    manual_points: Optional[Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float], Tuple[float, float]]] = None
    color_filters_keep: List[ColorFilterSetting] = field(default_factory=default_keep_filters)
    color_filters_remove: List[ColorFilterSetting] = field(default_factory=default_remove_filters)
    filtered_overlay: Optional[Image.Image] = None

    def reset(self) -> None:
        """Restore default overlay configuration."""

        self.show_overlay = True
        self.opacity = 0.65
        self.line_thickness = 0.0
        self.edge_smoothing = 0.0
        self.outline_thickness = 1.0
        self.outline_color = "#FFFFFF"
        self.clear_alignment()

    def clear_alignment(self) -> None:
        """Drop stored alignment and colour filter state."""

        self.manual_points = None
        self.filtered_overlay = None
        self.color_filters_keep = default_keep_filters()
        self.color_filters_remove = default_remove_filters()
        self.line_thickness = 0.0
        self.edge_smoothing = 0.0
        self.outline_thickness = 1.0
        self.outline_color = "#FFFFFF"


@dataclass(slots=True)
class AnnotationSettings:
    """Holds drawing tool presets and saved annotation items."""

    active_tool: str = "select"
    fill_color: str = "#ffffff"
    stroke_color: str = "#ff0000"
    stroke_width: float = 2.0
    outline_color: str = "#000000"
    outline_width: float = 1.0
    shadow_enabled: bool = True
    shadow_blur: float = 8.0
    font_family: str = "Noto Sans KR"
    font_size: int = 28
    annotations: List["AnnotationItem"] = field(default_factory=list)

    def reset(self) -> None:
        """Restore defaults and drop any staged annotations."""

        self.active_tool = "select"
        self.fill_color = "#ffffff"
        self.stroke_color = "#ff0000"
        self.stroke_width = 2.0
        self.outline_color = "#000000"
        self.outline_width = 1.0
        self.shadow_enabled = True
        self.shadow_blur = 8.0
        self.font_family = "Noto Sans KR"
        self.font_size = 28
        self.annotations = []


@dataclass(slots=True)
class AnnotationText:
    """Persisted text annotation details."""

    kind: Literal["text"] = "text"
    text: str = "새 텍스트"
    position: Tuple[float, float] = (0.0, 0.0)
    fill_color: str = "#ffffff"
    stroke_color: str = "#ff0000"
    stroke_width: float = 2.0
    outline_color: str = "#000000"
    outline_width: float = 1.0
    shadow_enabled: bool = True
    shadow_blur: float = 8.0
    font_family: str = "Noto Sans KR"
    font_size: int = 28


@dataclass(slots=True)
class AnnotationPath:
    """Persisted polyline or polygon annotation."""

    kind: Literal["path"] = "path"
    points: List[Tuple[float, float]] = field(default_factory=list)
    closed: bool = False
    fill_color: str = "#ffffff"
    stroke_color: str = "#ff0000"
    stroke_width: float = 2.0
    outline_color: str = "#000000"
    outline_width: float = 1.0
    shadow_enabled: bool = True
    shadow_blur: float = 8.0


# Alias used for collections and type hints.
AnnotationItem = AnnotationText | AnnotationPath


@dataclass(slots=True)
class AppState:
    """Container object for the global application state."""

    stage: AppStage = AppStage.UPLOAD
    cadastral: ImageSelection = field(default_factory=ImageSelection)
    photo: ImageSelection = field(default_factory=ImageSelection)
    overlay: OverlaySettings = field(default_factory=OverlaySettings)
    annotations: AnnotationSettings = field(default_factory=AnnotationSettings)

    def reset(self) -> None:
        """Resets the application to its initial state."""
        self.stage = AppStage.UPLOAD
        self.cadastral.clear()
        self.photo.clear()
        self.overlay.reset()
        self.annotations.reset()

"""Application state models used by the desktop port."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import List, Optional, Tuple

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
    manual_points: Optional[Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float], Tuple[float, float]]] = None
    color_filters_keep: List[ColorFilterSetting] = field(default_factory=list)
    color_filters_remove: List[ColorFilterSetting] = field(default_factory=list)
    filtered_overlay: Optional[Image.Image] = None

    def reset(self) -> None:
        """Restore default overlay configuration."""

        self.show_overlay = True
        self.opacity = 0.65
        self.clear_alignment()

    def clear_alignment(self) -> None:
        """Drop stored alignment and colour filter state."""

        self.manual_points = None
        self.filtered_overlay = None
        self.color_filters_keep = []
        self.color_filters_remove = []


@dataclass(slots=True)
class AppState:
    """Container object for the global application state."""

    stage: AppStage = AppStage.UPLOAD
    cadastral: ImageSelection = field(default_factory=ImageSelection)
    photo: ImageSelection = field(default_factory=ImageSelection)
    overlay: OverlaySettings = field(default_factory=OverlaySettings)

    def reset(self) -> None:
        """Resets the application to its initial state."""
        self.stage = AppStage.UPLOAD
        self.cadastral.clear()
        self.photo.clear()
        self.overlay.reset()

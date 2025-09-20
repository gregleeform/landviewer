"""Application state models used by the desktop port."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image


class AppStage(Enum):
    """Represents the major screens of the application."""

    UPLOAD = auto()
    CROP = auto()
    EDIT = auto()


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
class AppState:
    """Container object for the global application state."""

    stage: AppStage = AppStage.UPLOAD
    cadastral: ImageSelection = field(default_factory=ImageSelection)
    photo: ImageSelection = field(default_factory=ImageSelection)

    def reset(self) -> None:
        """Resets the application to its initial state."""
        self.stage = AppStage.UPLOAD
        self.cadastral.clear()
        self.photo.clear()

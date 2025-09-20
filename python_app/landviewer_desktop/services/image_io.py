"""Utility helpers for loading and preparing images."""
from __future__ import annotations

from pathlib import Path

from PIL import Image


DEFAULT_MAX_DIMENSION = 4000


def load_image(path: Path) -> Image.Image:
    """Loads an image from disk and converts it to RGBA."""
    image = Image.open(path)
    return image.convert("RGBA")


def should_suggest_resize(image: Image.Image, max_dimension: int = DEFAULT_MAX_DIMENSION) -> bool:
    """Return True if any dimension exceeds the threshold."""
    width, height = image.size
    return max(width, height) > max_dimension


def resized_copy(
    image: Image.Image,
    max_dimension: int = DEFAULT_MAX_DIMENSION,
    resample: int = Image.Resampling.LANCZOS,
) -> Image.Image:
    """Return a resized copy whose largest side matches ``max_dimension``."""
    width, height = image.size
    scale = max_dimension / float(max(width, height))
    if scale >= 1:
        return image.copy()
    new_size = int(width * scale), int(height * scale)
    return image.resize(new_size, resample=resample)


def rotate_image(image: Image.Image, quarter_turns: int) -> Image.Image:
    """Rotate ``image`` clockwise by ``quarter_turns`` multiples of 90°."""

    normalized = quarter_turns % 4
    if normalized == 0:
        return image

    transpose_map = {
        1: Image.Transpose.ROTATE_270,
        2: Image.Transpose.ROTATE_180,
        3: Image.Transpose.ROTATE_90,
    }
    return image.transpose(transpose_map[normalized])


def image_to_qpixmap(image: Image.Image):
    """Convert a Pillow image into a QPixmap for display."""
    from PySide6.QtGui import QImage, QPixmap

    rgba_image = image.convert("RGBA")
    data = rgba_image.tobytes("raw", "RGBA")
    qimage = QImage(data, rgba_image.width, rgba_image.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimage)

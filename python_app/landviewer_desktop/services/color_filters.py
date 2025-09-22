"""Utilities for applying keep/remove colour filters to overlay images."""
from __future__ import annotations

from typing import Sequence

import numpy as np
from PIL import Image

from landviewer_desktop.state import ColorFilterSetting


def _parse_hex_colour(value: str) -> np.ndarray:
    """Return an RGB triplet from ``value`` raising ``ValueError`` if invalid."""

    colour = value.strip().upper()
    if not colour:
        raise ValueError("Colour value cannot be empty.")
    if not colour.startswith("#"):
        colour = "#" + colour
    if len(colour) != 7:
        raise ValueError(f"Colour {value!r} must be a #RRGGBB hex code.")
    try:
        red = int(colour[1:3], 16)
        green = int(colour[3:5], 16)
        blue = int(colour[5:7], 16)
    except ValueError as exc:
        raise ValueError(f"Colour {value!r} must be a #RRGGBB hex code.") from exc
    return np.array([red, green, blue], dtype=np.float32)


def _tolerance_to_radius_squared(tolerance: int) -> float:
    """Convert a 0–100 tolerance slider value into a squared radius."""

    clamped = max(0, min(int(tolerance), 100))
    radius = (clamped / 100.0) * 255.0
    return radius * radius


def apply_color_filters(
    image: Image.Image,
    keep_filters: Sequence[ColorFilterSetting],
    remove_filters: Sequence[ColorFilterSetting],
) -> Image.Image:
    """Return ``image`` with keep/remove filters applied.

    Pixels that match any remove filter become transparent. When keep filters
    are provided the remaining pixels must match at least one keep filter or
    they will also become transparent. Matching pixels are recoloured to the
    exact keep colour, mimicking the React prototype behaviour.
    """

    if not keep_filters and not remove_filters:
        return image.copy()

    rgba = np.array(image.convert("RGBA"), dtype=np.uint8)
    if rgba.size == 0:
        return image.copy()

    rgb = rgba[..., :3].astype(np.float32)
    alpha = rgba[..., 3].astype(np.uint8)
    base_mask = alpha > 0

    remove_mask = np.zeros(alpha.shape, dtype=bool)
    for filter_setting in remove_filters:
        colour = _parse_hex_colour(filter_setting.color)
        tolerance_sq = _tolerance_to_radius_squared(filter_setting.tolerance)
        diff = rgb - colour
        distance_sq = np.sum(diff * diff, axis=-1)
        mask = (distance_sq <= tolerance_sq) & base_mask
        remove_mask |= mask

    if remove_mask.any():
        alpha[remove_mask] = 0
        base_mask &= ~remove_mask

    keep_mask = np.zeros(alpha.shape, dtype=bool)
    if keep_filters:
        for filter_setting in keep_filters:
            colour = _parse_hex_colour(filter_setting.color)
            tolerance_sq = _tolerance_to_radius_squared(filter_setting.tolerance)
            diff = rgb - colour
            distance_sq = np.sum(diff * diff, axis=-1)
            mask = (distance_sq <= tolerance_sq) & base_mask
            if not mask.any():
                continue
            keep_mask |= mask
            rgb[mask] = colour
            alpha[mask] = 255

        drop_mask = (~keep_mask) & base_mask
        if drop_mask.any():
            alpha[drop_mask] = 0

    rgb_uint8 = np.clip(rgb, 0, 255).astype(np.uint8)
    result = np.dstack((rgb_uint8, alpha))
    return Image.fromarray(result, mode="RGBA")

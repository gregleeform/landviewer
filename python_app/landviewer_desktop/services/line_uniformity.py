"""Vector-style re-rendering helpers for cadastral overlay strokes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


@dataclass(slots=True)
class LineSketch:
    """Caches distance/label data for reconstructing uniform overlay strokes."""

    distances: np.ndarray
    labels: np.ndarray
    skeleton: np.ndarray

    @property
    def shape(self) -> tuple[int, int]:
        return tuple(self.distances.shape[:2])


def prepare_sketch(image: np.ndarray) -> Optional[LineSketch]:
    """Extract a skeleton and distance map for an RGBA cadastral overlay."""

    if image.ndim != 3 or image.shape[2] < 4:
        return None

    alpha = image[..., 3]
    if not np.any(alpha):
        return None

    binary = np.zeros_like(alpha, dtype=np.uint8)
    binary[alpha > 0] = 255

    skeleton = _morphological_skeleton(binary)
    if not np.any(skeleton):
        skeleton = binary

    inverted = cv2.bitwise_not(skeleton)
    distances, labels = cv2.distanceTransformWithLabels(
        inverted,
        cv2.DIST_L2,
        5,
        labelType=cv2.DIST_LABEL_PIXEL,
    )

    return LineSketch(
        distances=distances.astype(np.float32),
        labels=labels.astype(np.int32),
        skeleton=skeleton,
    )


def render_uniform_overlay(
    source: np.ndarray,
    sketch: Optional[LineSketch],
    strength: float,
    *,
    min_radius: float = 1.25,
    max_radius: float = 7.5,
    feather: float = 1.25,
) -> np.ndarray:
    """Rebuild the overlay with vector-like strokes based on the given strength."""

    if sketch is None or strength <= 0.0:
        return source

    if source.ndim != 3 or source.shape[2] < 4:
        return source

    radius = float(min_radius + (max_radius - min_radius) * max(0.0, min(strength, 1.0)))
    radius = max(radius, 0.0)
    falloff = float(max(feather, 0.0))
    limit = radius + falloff

    height, width = sketch.shape
    if height == 0 or width == 0:
        return source

    flat_source = source.reshape(-1, source.shape[2])
    distances = sketch.distances.reshape(-1)
    labels = sketch.labels.reshape(-1)

    active = (labels > 0) & (distances <= limit)
    if not np.any(active):
        return source

    active_indices = np.flatnonzero(active)
    nearest = labels[active_indices] - 1
    samples = flat_source[nearest].astype(np.float32)

    weights = np.ones(active_indices.size, dtype=np.float32)
    if falloff > 1e-6:
        tail = distances[active_indices] > radius
        if np.any(tail):
            weights[tail] = np.clip(
                (limit - distances[active_indices][tail]) / falloff,
                0.0,
                1.0,
            )

    samples *= weights[:, None]

    result = source.copy().reshape(-1, source.shape[2])
    uniform_vals = np.clip(samples, 0.0, 255.0).astype(np.uint8)
    np.maximum(result[active_indices], uniform_vals, out=result[active_indices])

    return result.reshape(source.shape)


def _morphological_skeleton(binary: np.ndarray) -> np.ndarray:
    """Compute the skeleton of a binary mask using morphological thinning."""

    skeleton = np.zeros_like(binary, dtype=np.uint8)
    element = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    eroded = binary.copy()

    while True:
        opened = cv2.morphologyEx(eroded, cv2.MORPH_OPEN, element)
        temp = cv2.subtract(eroded, opened)
        skeleton = cv2.bitwise_or(skeleton, temp)
        eroded = cv2.erode(eroded, element)
        if not np.any(eroded):
            break

    return skeleton

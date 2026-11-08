"""Colour helpers: CIELAB conversion, perceptual distance, naming and palette merging."""

from __future__ import annotations

import cv2
import numpy as np

from .models import RGB

# Basic named colours used only for human-friendly layer names.
_NAMED: dict[str, RGB] = {
    "Black": (20, 20, 20), "White": (245, 245, 245), "Light Gray": (200, 200, 200),
    "Gray": (128, 128, 128), "Dark Gray": (70, 70, 70), "Red": (200, 30, 40),
    "Dark Red": (120, 20, 30), "Pink": (240, 150, 180), "Magenta": (200, 40, 150),
    "Orange": (240, 130, 30), "Gold": (220, 170, 40), "Yellow": (250, 220, 40),
    "Cream": (240, 230, 200), "Beige": (210, 190, 150), "Brown": (120, 70, 35),
    "Tan": (180, 140, 100), "Olive": (110, 110, 40), "Lime": (160, 210, 60),
    "Green": (40, 150, 60), "Dark Green": (20, 80, 40), "Teal": (20, 130, 130),
    "Cyan": (60, 200, 220), "Sky Blue": (120, 180, 230), "Blue": (30, 80, 200),
    "Navy": (20, 30, 90), "Purple": (110, 50, 150), "Lavender": (180, 160, 220),
    "Maroon": (100, 25, 45), "Silver": (180, 185, 190),
}


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """uint8 RGB array (..., 3) -> float32 CIELAB (L 0..100, a/b ~ -128..127)."""
    arr = np.asarray(rgb, dtype=np.uint8)
    shape = arr.shape
    flat = arr.reshape(-1, 1, 3).astype(np.float32) / 255.0
    lab = cv2.cvtColor(flat, cv2.COLOR_RGB2LAB)
    return lab.reshape(shape).astype(np.float32)


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    arr = np.asarray(lab, dtype=np.float32)
    shape = arr.shape
    rgb = cv2.cvtColor(arr.reshape(-1, 1, 3), cv2.COLOR_LAB2RGB)
    return np.clip(rgb * 255 + 0.5, 0, 255).astype(np.uint8).reshape(shape)


def delta_e(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """CIE76 distance between LAB colours (broadcasting)."""
    return np.sqrt(np.sum((np.asarray(a) - np.asarray(b)) ** 2, axis=-1))


_NAMED_LAB = {name: rgb_to_lab(np.array(rgb)) for name, rgb in _NAMED.items()}


def color_name(rgb: RGB) -> str:
    lab = rgb_to_lab(np.array(rgb, dtype=np.uint8))
    return min(_NAMED_LAB, key=lambda n: float(delta_e(lab, _NAMED_LAB[n])))


def merge_palette(lab_colors: np.ndarray, weights: np.ndarray, threshold: float) -> np.ndarray:
    """Greedy merge of colours closer than ``threshold`` ΔE.

    Returns an index map: original colour i -> merged group id (0..n-1).
    Heavier colours absorb lighter ones; group colour is the weighted mean.
    """
    n = len(lab_colors)
    order = np.argsort(-weights)
    group_of = -np.ones(n, dtype=int)
    centers: list[np.ndarray] = []
    totals: list[float] = []
    for i in order:
        best, best_d = -1, threshold
        for g, c in enumerate(centers):
            d = float(delta_e(lab_colors[i], c))
            if d < best_d:
                best, best_d = g, d
        if best < 0:
            centers.append(lab_colors[i].astype(np.float64))
            totals.append(float(weights[i]))
            group_of[i] = len(centers) - 1
        else:
            w = float(weights[i])
            centers[best] = (centers[best] * totals[best] + lab_colors[i] * w) / (totals[best] + w)
            totals[best] += w
            group_of[i] = best
    return group_of

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


def ciede2000(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """CIEDE2000 colour difference between LAB colours (broadcasting; kL = kC = kH = 1).

    Closer to what people see than CIE76, mainly for blues, greys and saturated
    colours - used where the user is told two threads "look alike"."""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    L1, a1, b1 = a[..., 0], a[..., 1], a[..., 2]
    L2, a2, b2 = b[..., 0], b[..., 1], b[..., 2]
    c_bar = (np.hypot(a1, b1) + np.hypot(a2, b2)) / 2
    g = 0.5 * (1 - np.sqrt(c_bar**7 / (c_bar**7 + 25.0**7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360
    dlp, dcp = L2 - L1, c2p - c1p
    dh = h2p - h1p
    dh = np.where(dh > 180, dh - 360, np.where(dh < -180, dh + 360, dh))
    dh = np.where(c1p * c2p == 0, 0.0, dh)
    dhp = 2 * np.sqrt(c1p * c2p) * np.sin(np.radians(dh) / 2)
    lbp, cbp = (L1 + L2) / 2, (c1p + c2p) / 2
    hsum = h1p + h2p
    hbp = np.where(np.abs(h1p - h2p) > 180, np.where(hsum < 360, (hsum + 360) / 2, (hsum - 360) / 2), hsum / 2)
    hbp = np.where(c1p * c2p == 0, hsum, hbp)
    t = (1 - 0.17 * np.cos(np.radians(hbp - 30)) + 0.24 * np.cos(np.radians(2 * hbp))
         + 0.32 * np.cos(np.radians(3 * hbp + 6)) - 0.20 * np.cos(np.radians(4 * hbp - 63)))
    d_theta = 30 * np.exp(-(((hbp - 275) / 25) ** 2))
    rc = 2 * np.sqrt(cbp**7 / (cbp**7 + 25.0**7))
    sl = 1 + 0.015 * (lbp - 50) ** 2 / np.sqrt(20 + (lbp - 50) ** 2)
    sc = 1 + 0.045 * cbp
    sh = 1 + 0.015 * cbp * t
    rt = -np.sin(np.radians(2 * d_theta)) * rc
    return np.sqrt((dlp / sl) ** 2 + (dcp / sc) ** 2 + (dhp / sh) ** 2 + rt * (dcp / sc) * (dhp / sh))


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

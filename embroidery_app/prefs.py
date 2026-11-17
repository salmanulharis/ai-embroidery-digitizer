"""Per-account preferences: the embroidery machine and the usual placement.

Two small choices the app asks once and then remembers, so the next design needs fewer
steps (docs/APP_UX_PLAN.md, batches A and B):

- ``machine``: which machine the account sews on. The Export dialog ticks that machine's
  file format first and folds the other formats away.
- ``placement``: what the designs are usually for (left chest, cap, ...). The start screen
  pre-selects it after a file is chosen; it sets the design size, the hoop and the
  suggested preset before the design is prepared for the first time.

Both tables live here and go to the browser in ``/api/config``. A preference is a key
from these tables, never design data: nothing about a picture, a file name or a design is
stored (privacy rules in CLAUDE.md). Unknown keys and values are refused.
"""

from __future__ import annotations

import json
import time

from . import db
from .export import FORMATS
from .ingest.raster import RASTER_RANGES
from .ingest.vector import VECTOR_RANGES

# key -> label shown in the Export dialog, and the file format that machine reads.
MACHINES: dict[str, dict[str, str]] = {
    "tajima": {"label": "Tajima, WONYO, Dahao, SWF, Ricoma (most commercial)", "format": "dst"},
    "brother": {"label": "Brother / Babylock", "format": "pes"},
    "janome": {"label": "Janome / Elna", "format": "jef"},
    "bernina": {"label": "Bernina", "format": "exp"},
    "melco": {"label": "Melco", "format": "exp"},
    "barudan": {"label": "Barudan", "format": "u01"},
    "husqvarna": {"label": "Husqvarna Viking / Pfaff", "format": "vp3"},
    "singer": {"label": "Singer", "format": "xxx"},
}

# key -> label, the longest side of the finished design in mm (None = keep the editor's
# value), and a hint. Common sizes for each placement; every value stays editable.
PLACEMENTS: dict[str, dict] = {
    "left_chest": {"label": "Left chest", "size_mm": 90, "hint": "Polo shirts, shirts, jackets"},
    "cap": {"label": "Cap front", "size_mm": 55, "hint": "Caps and hats", "hoop": "cap"},
    "sleeve": {"label": "Sleeve", "size_mm": 75, "hint": "Upper sleeve, small badges"},
    "full_front": {"label": "Full front", "size_mm": 200, "hint": "T-shirts, sweatshirts, bags"},
    "jacket_back": {"label": "Jacket back", "size_mm": 280, "hint": "Large back designs"},
    "custom": {"label": "Custom size", "size_mm": None, "hint": "Type the width yourself"},
}

KEYS = {"machine": MACHINES, "placement": PLACEMENTS}


def catalog() -> dict:
    """What the browser needs to show the choices (``/api/config``)."""
    return {
        "machines": [{"key": k, **v} for k, v in MACHINES.items()],
        "placements": [{"key": k, **v} for k, v in PLACEMENTS.items()],
    }


def clean(body: dict) -> dict:
    """Only known preferences with known values; ``None`` clears one."""
    if not isinstance(body, dict):
        raise ValueError("Preferences must be an object.")
    out = {}
    for k, v in body.items():
        if k not in KEYS:
            raise ValueError(f"Unknown preference: {k}.")
        if v is not None and v not in KEYS[k]:
            raise ValueError(f"Unknown {k}: {v}.")
        out[k] = v
    return out


def get(user_id: int) -> dict:
    with db.tx() as c:
        row = c.execute("SELECT prefs FROM user_prefs WHERE user_id = ?", (user_id,)).fetchone()
    try:
        data = json.loads(row["prefs"]) if row else {}
    except ValueError:
        data = {}
    # a value that is no longer offered (a removed machine) is forgotten, not an error
    return {k: v for k, v in data.items() if k in KEYS and v in KEYS[k]}


def save(user_id: int, body: dict) -> dict:
    """Merge ``body`` into the account's preferences and return them all."""
    change = clean(body)
    with db.tx() as c:
        row = c.execute("SELECT prefs FROM user_prefs WHERE user_id = ?", (user_id,)).fetchone()
        data = json.loads(row["prefs"]) if row else {}
        data.update(change)
        data = {k: v for k, v in data.items() if v is not None}
        c.execute(
            "INSERT INTO user_prefs (user_id, prefs, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET prefs = excluded.prefs, updated_at = excluded.updated_at",
            (user_id, json.dumps(data, sort_keys=True), time.time()),
        )
    return get(user_id)


def _check_tables() -> None:
    """Every machine's format exists and every size is one the editor accepts (import-time
    guard; tests/test_prefs.py checks the same with clear messages)."""
    lo = max(RASTER_RANGES["width_mm"][0], VECTOR_RANGES["width_mm"][0])
    hi = min(RASTER_RANGES["width_mm"][1], VECTOR_RANGES["width_mm"][1])
    for k, m in MACHINES.items():
        assert m["format"] in FORMATS, k
    for k, p in PLACEMENTS.items():
        assert p["size_mm"] is None or lo <= p["size_mm"] <= hi, k


_check_tables()

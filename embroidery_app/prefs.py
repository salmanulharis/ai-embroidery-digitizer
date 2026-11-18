"""Per-account preferences: the embroidery machine, the usual placement, fabric and thread brand.

Small choices the app asks once and then remembers, so the next design needs fewer
steps (docs/APP_UX_PLAN.md, batches A and B; docs/AUTOMATION_UX_PLAN.md, batch B):

- ``machine``: which machine the account sews on. The Export dialog ticks that machine's
  file format first and folds the other formats away.
- ``placement``: what the designs are usually for (left chest, cap, ...). The start screen
  pre-selects it after a file is chosen; it sets the design size, the hoop and the
  suggested preset before the design is prepared for the first time.
- ``fabric``: the fabric most designs are sewn on (fabrics.py); a new design starts with it.
- ``thread_chart``: the thread brand (threads.py, a chart file the server has); a new design
  starts with it, so the worksheet lists that brand's thread numbers.

The machine also gives a new design its speed for the time estimate (``spm``).

Both tables live here and go to the browser in ``/api/config``. A preference is a key
from these tables, never design data: nothing about a picture, a file name or a design is
stored (privacy rules in CLAUDE.md). Unknown keys and values are refused.
"""

from __future__ import annotations

import json
import time

from . import db, threads
from .export import FORMATS
from .fabrics import FABRIC_IDS
from .models import STITCH_RANGES
from .ingest.raster import RASTER_RANGES
from .ingest.vector import VECTOR_RANGES

# key -> label shown in the Export dialog, the file format that machine reads, and the speed
# (stitches per minute) a new design's time estimate starts with: a usual working speed, not
# the machine's maximum (machine_spm, Fabric, thread & machine; it only changes the figures).
MACHINES: dict[str, dict] = {
    "tajima": {"label": "Tajima, WONYO, Dahao, SWF, Ricoma (most commercial)", "format": "dst", "spm": 850},
    "brother": {"label": "Brother / Babylock", "format": "pes", "spm": 650},
    "janome": {"label": "Janome / Elna", "format": "jef", "spm": 650},
    "bernina": {"label": "Bernina", "format": "exp", "spm": 650},
    "melco": {"label": "Melco", "format": "exp", "spm": 850},
    "barudan": {"label": "Barudan", "format": "u01", "spm": 850},
    "husqvarna": {"label": "Husqvarna Viking / Pfaff", "format": "vp3", "spm": 650},
    "singer": {"label": "Singer", "format": "xxx", "spm": 600},
}

# key -> label, the longest side of the finished design in mm (None = keep the editor's
# value), and a hint; "hoop": "cap" picks the cap hoop, "fabric" the fabric a new design starts
# with (it wins over the usual fabric). Common sizes for each placement; every value stays editable.
PLACEMENTS: dict[str, dict] = {
    "left_chest": {"label": "Left chest", "size_mm": 90, "hint": "Polo shirts, shirts, jackets"},
    "cap": {"label": "Cap front", "size_mm": 55, "hint": "Caps and hats", "hoop": "cap", "fabric": "cap"},
    "sleeve": {"label": "Sleeve", "size_mm": 75, "hint": "Upper sleeve, small badges"},
    "full_front": {"label": "Full front", "size_mm": 200, "hint": "T-shirts, sweatshirts, bags"},
    "jacket_back": {"label": "Jacket back", "size_mm": 280, "hint": "Large back designs"},
    "custom": {"label": "Custom size", "size_mm": None, "hint": "Type the width yourself"},
}

# preference -> is this value one the app offers now? (thread charts are files the server has,
# so the brands can change while the app runs)
VALID = {
    "machine": lambda v: v in MACHINES,
    "placement": lambda v: v in PLACEMENTS,
    "fabric": lambda v: v in FABRIC_IDS,
    "thread_chart": lambda v: isinstance(v, str) and v in threads.charts(),
}
KEYS = tuple(VALID)


def _valid(k: str, v) -> bool:
    try:
        return isinstance(v, str) and VALID[k](v)
    except Exception:  # a thread chart folder that cannot be read: nothing is offered
        return False


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
        if k not in VALID:
            raise ValueError(f"Unknown preference: {k}.")
        if v is not None and not _valid(k, v):
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
    # a value that is no longer offered (a removed machine, a thread chart taken off the server)
    # is forgotten, not an error
    return {k: v for k, v in data.items() if k in VALID and _valid(k, v)}


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
    spm_lo, spm_hi = STITCH_RANGES["machine_spm"]
    for k, m in MACHINES.items():
        assert m["format"] in FORMATS, k
        assert spm_lo <= m["spm"] <= spm_hi and m["spm"] % 50 == 0, k  # the editor's speed field steps by 50
    for k, p in PLACEMENTS.items():
        assert p["size_mm"] is None or lo <= p["size_mm"] <= hi, k
        assert p.get("fabric") is None or p["fabric"] in FABRIC_IDS, k


_check_tables()

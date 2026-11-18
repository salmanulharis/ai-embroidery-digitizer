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
- ``preset``: the usual preset ("b:<built-in id>" or "u:<own preset id>"); a new design
  starts with it (applied with the starting values, so nothing is marked as changed). Only
  the account's own presets; a deleted one is forgotten. The tier is checked in the editor,
  which never receives a locked preset's values.

- ``hidden_checks``: check codes the account turned off ("Don't show them again"; important
  checks can only be hidden per design). ``auto_fix``: apply the important checks' setting
  fixes to a new design once, after its first stitches (off unless chosen).
- ``needles``: how many needles the machine has (default: the machine's ``needles``); more
  colours than needles is a check.
- ``price_per_1000``, ``price_min``, ``currency``: a price estimate in Export and on the
  worksheet (``quote``). Shown only to the account; never sent anywhere.
- ``garment``: the usual garment colour (#rrggbb); a new design starts with it.

The machine also gives a new design its speed for the time estimate (``spm``), the hoops it
takes (``hoops``: names from ``HOOPS``), which the editor prefers when it picks a hoop, and its
usual number of needles (``needles``: 1 = a single-needle home machine).

Both tables live here and go to the browser in ``/api/config``. A preference is a key
from these tables, never design data: nothing about a picture, a file name or a design is
stored (privacy rules in CLAUDE.md). Unknown keys and values are refused.
"""

from __future__ import annotations

import json
import re
import time

from . import db, threads
from .export import FORMATS
from .fabrics import FABRIC_IDS
from .models import STITCH_RANGES
from .presets import BUILTINS
from .ingest.raster import RASTER_RANGES
from .ingest.vector import VECTOR_RANGES

# key -> label shown in the Export dialog, the file format that machine reads, and the speed
# (stitches per minute) a new design's time estimate starts with: a usual working speed, not
# the machine's maximum (machine_spm, Fabric, thread & machine; it only changes the figures).
# The hoops the editor offers (width x height of the sewing field, mm). Options are chosen by
# their position in this list, so new hoops are added at the end.
HOOPS: list[dict] = [
    {"name": "100 x 100 mm", "width": 100, "height": 100},
    {"name": "150 x 150 mm", "width": 150, "height": 150},
    {"name": "200 x 200 mm", "width": 200, "height": 200},
    {"name": "300 x 300 mm", "width": 300, "height": 300},
    {"name": "360 x 200 mm", "width": 360, "height": 200},
    {"name": "Cap 70 x 140 mm", "width": 140, "height": 70},
    {"name": "Left chest ~90 mm", "width": 90, "height": 90},
    {"name": "130 x 180 mm (5 x 7 in)", "width": 130, "height": 180},
    {"name": "160 x 260 mm (6 x 10 in)", "width": 160, "height": 260},
]
_COMMERCIAL_HOOPS = ("100 x 100 mm", "150 x 150 mm", "200 x 200 mm", "300 x 300 mm", "360 x 200 mm",
                     "Cap 70 x 140 mm", "Left chest ~90 mm")
_HOME_HOOPS = ("100 x 100 mm", "130 x 180 mm (5 x 7 in)", "160 x 260 mm (6 x 10 in)", "200 x 200 mm")

# key -> label, file format, speed (stitches per minute: a usual working speed, not the machine's
# maximum; machine_spm only changes the figures) and the usual hoop sizes of that kind of machine
# (the common sizes of each range, to confirm against the machine's own list).
MACHINES: dict[str, dict] = {
    "tajima": {"label": "Tajima, WONYO, Dahao, SWF, Ricoma (most commercial)", "format": "dst", "spm": 850, "hoops": _COMMERCIAL_HOOPS, "needles": 15},
    "brother": {"label": "Brother / Babylock", "format": "pes", "spm": 650, "hoops": _HOME_HOOPS, "needles": 1},
    "janome": {"label": "Janome / Elna", "format": "jef", "spm": 650, "hoops": ("100 x 100 mm", "130 x 180 mm (5 x 7 in)", "200 x 200 mm"), "needles": 1},
    "bernina": {"label": "Bernina", "format": "exp", "spm": 650, "hoops": _HOME_HOOPS, "needles": 1},
    "melco": {"label": "Melco", "format": "exp", "spm": 850, "hoops": _COMMERCIAL_HOOPS, "needles": 16},
    "barudan": {"label": "Barudan", "format": "u01", "spm": 850, "hoops": _COMMERCIAL_HOOPS, "needles": 15},
    "husqvarna": {"label": "Husqvarna Viking / Pfaff", "format": "vp3", "spm": 650,
                  "hoops": ("100 x 100 mm", "130 x 180 mm (5 x 7 in)", "200 x 200 mm", "360 x 200 mm"), "needles": 1},
    "singer": {"label": "Singer", "format": "xxx", "spm": 600, "hoops": ("100 x 100 mm", "130 x 180 mm (5 x 7 in)", "160 x 260 mm (6 x 10 in)"), "needles": 1},
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
    # "b:<built-in id>" or "u:<own preset id>"; whose preset it is is checked in save / get
    "preset": lambda v: (v[2:] in {b.id for b in BUILTINS}) if v.startswith("b:") else bool(_OWN_PRESET.fullmatch(v)),
    "hidden_checks": lambda v: isinstance(v, list) and len(v) <= MAX_HIDDEN_CHECKS and all(
        isinstance(c, str) and _CHECK_CODE.fullmatch(c) for c in v) and len(set(v)) == len(v),
    "auto_fix": lambda v: isinstance(v, bool),
    "needles": lambda v: _whole(v) and NEEDLES[0] <= v <= NEEDLES[1],
    "price_per_1000": lambda v: _amount(v),
    "price_min": lambda v: _amount(v),
    "currency": lambda v: isinstance(v, str) and bool(_CURRENCY.fullmatch(v)),
    "garment": lambda v: isinstance(v, str) and bool(_HEX.fullmatch(v)),
}
_OWN_PRESET = re.compile(r"u:[1-9][0-9]{0,11}")
_CHECK_CODE = re.compile(r"[a-z][a-z_]{1,39}")
_CURRENCY = re.compile(r"[^\s<>&\"'`]{1,4}")  # a symbol or a short code: $, €, ₹, AED
_HEX = re.compile(r"#[0-9a-f]{6}")
MAX_HIDDEN_CHECKS = 50
NEEDLES = (1, 20)
MAX_PRICE = 100_000.0


def _whole(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _amount(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and 0 <= v <= MAX_PRICE
KEYS = tuple(VALID)


# preferences whose value is not text (every other one is a key or a code: a string)
_NOT_TEXT = ("hidden_checks", "auto_fix", "needles", "price_per_1000", "price_min")


def _valid(k: str, v) -> bool:
    try:
        return (k in _NOT_TEXT or isinstance(v, str)) and bool(VALID[k](v))
    except Exception:  # a thread chart folder that cannot be read: nothing is offered
        return False


def quote(user_id: int) -> dict | None:
    """The account's price estimate settings for the worksheet, or None when no price is set."""
    p = get(user_id)
    if not p.get("price_per_1000"):
        return None
    return {"per_1000": float(p["price_per_1000"]), "minimum": float(p.get("price_min") or 0), "currency": p.get("currency") or ""}


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
    # a value that is no longer offered (a removed machine, a thread chart taken off the server,
    # a deleted preset) is forgotten, not an error
    out = {k: v for k, v in data.items() if k in VALID and _valid(k, v)}
    if "preset" in out and not _preset_is_theirs(user_id, out["preset"]):
        del out["preset"]
    return out


def _preset_is_theirs(user_id: int, value: str) -> bool:
    """A built-in, or one of this account's own presets (never another account's)."""
    if not value.startswith("u:"):
        return True
    with db.tx() as c:
        return c.execute("SELECT 1 FROM user_presets WHERE id = ? AND user_id = ?", (int(value[2:]), user_id)).fetchone() is not None


def save(user_id: int, body: dict) -> dict:
    """Merge ``body`` into the account's preferences and return them all."""
    change = clean(body)
    if change.get("preset") and not _preset_is_theirs(user_id, change["preset"]):
        raise ValueError("Choose one of your own presets.")
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
    names = [h["name"] for h in HOOPS]
    assert len(set(names)) == len(names)
    for k, m in MACHINES.items():
        assert m["format"] in FORMATS, k
        assert m["hoops"] and set(m["hoops"]) <= set(names), k
        assert NEEDLES[0] <= m["needles"] <= NEEDLES[1], k
        assert spm_lo <= m["spm"] <= spm_hi and m["spm"] % 50 == 0, k  # the editor's speed field steps by 50
    for k, p in PLACEMENTS.items():
        assert p["size_mm"] is None or lo <= p["size_mm"] <= hi, k
        assert p.get("fabric") is None or p["fabric"] in FABRIC_IDS, k


_check_tables()

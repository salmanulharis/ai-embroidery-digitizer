"""Settings presets: named, reusable sets of editor settings.

A preset is a *partial* settings record, like a fabric (fabrics.py): only the keys it
contains are applied, every value stays editable afterwards. It is split into sections
that match the editor's left panel::

    {"stitch": {...}, "fabric": {...}, "image": {...}, "size": {...}}

- ``stitch``: stitch settings (density, lengths, styles, sewing options);
- ``fabric``: fabric & thread (fabric choice, thread weight, compensation, foundation).
  A preset with a ``fabric`` choice applies the fabric's values first, then its own;
- ``image``: picture settings (raster designs only; skipped with a note on vector designs);
- ``size``: design width and number of colours (off by default when saving).

Never in a preset: per-shape settings, layer colours or names, guide lines, geometry, the
picture itself. Only the keys in ``KEYS`` are accepted; values are clamped to the ranges
the editor offers, unknown keys and bad values are dropped and reported (``clean``).

Built-in presets live in this file (``BUILTINS``) and are covered by tests; their values
are starting points to confirm with a sew-out, like the fabric presets. Access is by tier
(``presets_basic`` / ``presets_premium`` features, entitlements.py); the Super Admin can
move a built-in to the other tier (``preset_access`` in the app_settings table). Premium
values are never sent to an account without the tier.

Each account's own presets are stored in ``user_presets`` (settings, name, description
only), limited by the ``custom_presets`` feature and the ``max_custom_presets`` limit.
After a downgrade, existing presets stay usable (apply, rename, edit, delete); only new
ones (create, duplicate) are refused.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from . import db, entitlements
from .entitlements import EntitlementError
from .fabrics import FABRIC_IDS
from .ingest.raster import BACKGROUNDS, RASTER_RANGES
from .models import FILL_PATTERN_TILES, FILL_PATTERNS, FILL_UNDERLAYS, SATIN_UNDERLAYS, STITCH_RANGES, THREAD_WEIGHTS

SCHEMA = 1
SECTIONS = ("stitch", "fabric", "image", "size")
TIERS = ("basic", "premium")
TIER_FEATURE = {"basic": "presets_basic", "premium": "presets_premium"}
MAX_NAME = 60
MAX_DESCRIPTION = 300
MAX_SETTINGS_BYTES = 16 * 1024
ACCESS_KEY = "preset_access"  # app_settings row: {builtin id: tier}


class NameTaken(ValueError):
    """The account already has a preset with this name (the browser offers Replace / Keep both)."""

    status = 409
    code = "name_taken"

    def __init__(self, name: str, existing_id: int):
        super().__init__(f"You already have a preset called \"{name}\".")
        self.existing_id = existing_id


# --------------------------------------------------------------------------- the settings a preset may hold

@dataclass(frozen=True)
class Key:
    section: str
    kind: str  # num | int | bool | enum | colours
    lo: float = 0.0
    hi: float = 0.0
    values: tuple = ()


def _num(section, lo, hi):
    return Key(section, "num", lo, hi)


def _int(section, lo, hi):
    return Key(section, "int", lo, hi)


def _enum(section, values):
    return Key(section, "enum", values=tuple(values))


_BOOL_S = Key("stitch", "bool")
_BOOL_F = Key("fabric", "bool")

# Ranges come from models.STITCH_RANGES and ingest/raster.RASTER_RANGES (the editor's).
KEYS: dict[str, Key] = {
    # stitch
    "fill_spacing_mm": _num("stitch", *STITCH_RANGES["fill_spacing_mm"]),
    "satin_spacing_mm": _num("stitch", *STITCH_RANGES["satin_spacing_mm"]),
    "fill_angle": _num("stitch", *STITCH_RANGES["fill_angle"]),
    "fill_stitch_length_mm": _num("stitch", *STITCH_RANGES["fill_stitch_length_mm"]),
    "satin_max_width_mm": _num("stitch", *STITCH_RANGES["satin_max_width_mm"]),
    "running_stitch_length_mm": _num("stitch", *STITCH_RANGES["running_stitch_length_mm"]),
    "running_repeats": _enum("stitch", (1, 3)),
    "border_width_mm": _num("stitch", *STITCH_RANGES["border_width_mm"]),
    "border_spacing_mm": _num("stitch", *STITCH_RANGES["border_spacing_mm"]),
    "stipple_spacing_mm": _num("stitch", *STITCH_RANGES["stipple_spacing_mm"]),
    "cross_cell_mm": _num("stitch", *STITCH_RANGES["cross_cell_mm"]),
    "motif_size_mm": _num("stitch", *STITCH_RANGES["motif_size_mm"]),
    "pattern_size_mm": _num("stitch", *STITCH_RANGES["pattern_size_mm"]),
    "jagged_mm": _num("stitch", *STITCH_RANGES["jagged_mm"]),
    "photo_row_mm": _num("stitch", *STITCH_RANGES["photo_row_mm"]),
    "trim_threshold_mm": _num("stitch", *STITCH_RANGES["trim_threshold_mm"]),
    "min_object_area_mm2": _num("stitch", *STITCH_RANGES["min_object_area_mm2"]),
    "fill_pattern": _enum("stitch", FILL_PATTERNS),
    "pattern": _enum("stitch", FILL_PATTERN_TILES),
    "motif": Key("stitch", "motif"),
    "photo_style": _enum("stitch", ("rows", "reef", "colour")),
    "optimize": _BOOL_S,
    "vary_fill_angle": _BOOL_S,
    "smart_direction": _BOOL_S,
    "tie_stitches": _BOOL_S,
    "optimize_order": _BOOL_S,
    "trapunto": _BOOL_S,
    "motif_warp": _BOOL_S,
    "photo_stitch": _BOOL_S,
    # fabric & thread
    "fabric": _enum("fabric", FABRIC_IDS),
    "thread_weight": _enum("fabric", THREAD_WEIGHTS),
    "pull_compensation_mm": _num("fabric", *STITCH_RANGES["pull_compensation_mm"]),
    "push_compensation_mm": _num("fabric", *STITCH_RANGES["push_compensation_mm"]),
    "underlay": _BOOL_F,
    "underlay_spacing_mm": _num("fabric", *STITCH_RANGES["underlay_spacing_mm"]),
    "fill_underlay": _enum("fabric", FILL_UNDERLAYS),
    "satin_underlay": _enum("fabric", SATIN_UNDERLAYS),
    "knockdown": _BOOL_F,
    # picture (raster designs)
    "background": _enum("image", BACKGROUNDS),
    "text_mode": _enum("image", ("auto", "sharp", "standard", "simple")),
    "brightness": _int("image", *RASTER_RANGES["brightness"]),
    "contrast": _int("image", *RASTER_RANGES["contrast"]),
    "sharpen": _int("image", *RASTER_RANGES["sharpen"]),
    "denoise": _int("image", *RASTER_RANGES["denoise"]),
    "min_feature_mm": _num("image", *RASTER_RANGES["min_feature_mm"]),
    "min_area_mm2": _num("image", *RASTER_RANGES["min_area_mm2"]),
    # size & colours
    "width_mm": _num("size", *RASTER_RANGES["width_mm"]),
    "num_colors": Key("size", "colours", *RASTER_RANGES["num_colors"]),
}


def _motifs() -> tuple[str, ...]:
    from .stitch import decorative

    return tuple(decorative.motifs())


def _value(name: str, key: Key, raw):
    """The clean value, or raise ValueError (the key is then dropped)."""
    if key.kind == "bool":
        if not isinstance(raw, bool):
            raise ValueError
        return raw
    if key.kind in ("enum", "motif"):
        values = _motifs() if key.kind == "motif" else key.values
        if isinstance(raw, bool):
            raise ValueError
        for v in values:  # numbers may arrive as strings from a form ("60")
            if raw == v or (isinstance(v, int) and isinstance(raw, str) and raw.strip() == str(v)):
                return v
        raise ValueError
    if key.kind == "colours":  # None / "" = automatic
        if raw is None or raw == "":
            return None
    if isinstance(raw, bool):
        raise ValueError
    x = float(raw)  # ValueError / TypeError for junk
    if x != x or x in (float("inf"), float("-inf")):
        raise ValueError
    x = min(max(x, key.lo), key.hi)
    if key.kind in ("int", "colours"):
        return int(round(x))
    return round(x, 3)


def clean(sections) -> tuple[dict, list[str]]:
    """Validate a preset's sections: ``(clean sections, dropped keys)``. A key in the wrong
    section is moved to its own; unknown keys, unknown sections and bad values are dropped."""
    out: dict[str, dict] = {}
    dropped: list[str] = []
    if not isinstance(sections, dict):
        raise ValueError("A preset's settings must be an object of sections.")
    for sec, values in sections.items():
        if sec not in SECTIONS or not isinstance(values, dict):
            dropped.append(str(sec))
            continue
        for name, raw in values.items():
            key = KEYS.get(name)
            if key is None:
                dropped.append(f"{sec}.{name}")
                continue
            try:
                v = _value(name, key, raw)
            except (TypeError, ValueError):
                dropped.append(f"{sec}.{name}")
                continue
            out.setdefault(key.section, {})[name] = v
    return {s: out[s] for s in SECTIONS if out.get(s)}, dropped


def kinds(sections: dict) -> list[str]:
    """What a preset changes, for the picker: "stitch" and / or "image"."""
    out = []
    if sections.get("stitch") or sections.get("fabric"):
        out.append("stitch")
    if sections.get("image"):
        out.append("image")
    if sections.get("size"):
        out.append("size")
    return out


def count_settings(sections: dict) -> int:
    return sum(len(v) for v in sections.values())


# --------------------------------------------------------------------------- built-in presets

@dataclass(frozen=True)
class Builtin:
    id: str
    label: str
    description: str
    tier: str  # default tier; the admin may move it (preset_access)
    sections: dict = field(default_factory=dict)
    needs: tuple[str, ...] = ()  # further features the preset relies on (e.g. photo_stitch)


# Starting values, all inside the editor's ranges. Not production values until the preset
# regression tests and a physical sew-out per preset pass (docs/ROADMAP.md 4.8).
# Smart direction stays out of the basic presets: it is opt-in until it has been sewn.
BUILTINS: tuple[Builtin, ...] = (
    Builtin("standard", "Standard", "Balanced settings for most logos on woven fabric.", "basic", {
        "stitch": {"fill_spacing_mm": 0.40, "satin_spacing_mm": 0.40, "fill_stitch_length_mm": 3.5, "optimize": True},
        "fabric": {"pull_compensation_mm": 0.20, "underlay": True, "fill_underlay": "edge_tatami", "satin_underlay": "edge"},
    }),
    Builtin("small_text", "Small text & logos", "Left-chest logos and lettering under 6 mm: fine thread, crisp details.",
            "basic", {
                "stitch": {"satin_spacing_mm": 0.35, "running_stitch_length_mm": 2.0},
                "fabric": {"thread_weight": 60, "pull_compensation_mm": 0.15, "satin_underlay": "center"},
                "image": {"text_mode": "sharp", "min_feature_mm": 0.2, "min_area_mm2": 0.3},
            }),
    Builtin("fine_detail", "Fine detail", "Detailed artwork of about 80-150 mm: denser, shorter stitches.", "premium", {
        "stitch": {"fill_spacing_mm": 0.38, "fill_stitch_length_mm": 3.0, "satin_spacing_mm": 0.36, "smart_direction": True},
        "image": {"sharpen": 1, "min_feature_mm": 0.2},
    }),
    # Coverage from a heavier foundation and denser satin: fill rows closer than 0.40 mm
    # left many stitches too short to sew on the test designs (tests/test_presets.py).
    Builtin("dense", "Dense coverage", "Full coverage on dark or textured fabric.", "premium", {
        "stitch": {"satin_spacing_mm": 0.35},
        "fabric": {"fill_underlay": "double_tatami", "pull_compensation_mm": 0.25},
    }),
    Builtin("lightweight", "Lightweight", "Thin fabric: lighter fill, fewer stitches.", "basic", {
        "stitch": {"fill_spacing_mm": 0.50, "satin_spacing_mm": 0.45},
        "fabric": {"fill_underlay": "edge", "pull_compensation_mm": 0.15},
    }),
    Builtin("large", "Large design", "Jacket backs over 200 mm: longer fill stitches, fewer cuts.", "premium", {
        "stitch": {"fill_spacing_mm": 0.45, "fill_stitch_length_mm": 4.0, "vary_fill_angle": True, "trim_threshold_mm": 3.0},
    }),
    Builtin("satin_forward", "Satin-forward", "Bold lettering and columns sewn as shiny satin where possible.", "premium", {
        "stitch": {"satin_max_width_mm": 12.0, "smart_direction": True},
    }),
    Builtin("fill_forward", "Fill-forward", "A flat, textured look: wide shapes become fill.", "premium", {
        "stitch": {"satin_max_width_mm": 5.0, "smart_direction": False},
    }),
    Builtin("clean_logo", "Clean logo (picture)", "Scanned or JPEG logos: background removed, edges sharpened, specks cleaned.",
            "basic", {"image": {"background": "auto", "sharpen": 1, "denoise": 2, "min_area_mm2": 1.5}}),
    Builtin("high_contrast", "High contrast (picture)", "Faded pictures: stronger contrast before colours are found.", "basic", {
        "image": {"contrast": 30, "sharpen": 1, "denoise": 2},
    }),
    Builtin("soft_colours", "Soft colours (picture)", "Pastel artwork: gentle contrast, smoother areas.", "premium", {
        "image": {"contrast": -10, "denoise": 3, "min_area_mm2": 2.0},
    }),
    Builtin("photo", "Photo", "Portraits and photos sewn as a photo texture in rows.", "premium", {
        "image": {"background": "none", "denoise": 1},
        "stitch": {"photo_stitch": True, "photo_style": "rows", "photo_row_mm": 1.0},
    }, needs=("photo_stitch",)),
)
_BY_ID = {b.id: b for b in BUILTINS}


def access_map() -> dict[str, str]:
    """The admin's tier changes: {builtin id: tier} (only presets moved from their default)."""
    with db.tx() as c:
        row = c.execute("SELECT value FROM app_settings WHERE key = ?", (ACCESS_KEY,)).fetchone()
    try:
        raw = json.loads(row["value"]) if row else {}
    except ValueError:
        raw = {}
    return {k: v for k, v in (raw if isinstance(raw, dict) else {}).items() if k in _BY_ID and v in TIERS}


def tier_of(builtin: Builtin, access: dict[str, str] | None = None) -> str:
    return (access if access is not None else access_map()).get(builtin.id, builtin.tier)


def set_access(changes: dict, actor_id: int | None) -> dict[str, tuple[str, str]]:
    """Move built-ins between tiers: {id: "basic" | "premium" | None (= its default)}.
    Returns {id: (old tier, new tier)} for the ones that changed."""
    if not isinstance(changes, dict):
        raise ValueError("Send an object of preset: tier.")
    for k, v in changes.items():
        if k not in _BY_ID:
            raise ValueError(f"Unknown preset: {k}")
        if v is not None and v not in TIERS:
            raise ValueError("A tier is basic, premium or null (the preset's own).")
    with db.tx() as c:
        cur = access_map()
        new = dict(cur)
        done = {}
        for k, v in changes.items():
            old_tier = cur.get(k, _BY_ID[k].tier)
            if v is None or v == _BY_ID[k].tier:
                new.pop(k, None)
            else:
                new[k] = v
            new_tier = new.get(k, _BY_ID[k].tier)
            if new_tier != old_tier:
                done[k] = (old_tier, new_tier)
        c.execute("INSERT INTO app_settings (key, value, updated_by, updated_at) VALUES (?, ?, ?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_by = excluded.updated_by, "
                  "updated_at = excluded.updated_at", (ACCESS_KEY, json.dumps(new), actor_id, time.time()))
    return done


def _plans_with(features: list[str]) -> list[str]:
    """Names of the enabled plans that include all these features (for "included in Pro")."""
    defaults = {k: f.default for k, f in entitlements.FEATURES.items()}
    with db.tx() as c:
        rows = c.execute("SELECT * FROM plans WHERE enabled = 1 ORDER BY sort_order, id").fetchall()
    out = []
    for r in rows:
        feats = defaults | entitlements.plan_dict(r)["features"]
        if all(feats.get(f) for f in features):
            out.append(r["name"])
    return out


def builtins_for(user_id: int) -> list[dict]:
    """The built-in presets as this account sees them: locked ones without their values, but
    with what they need and which plans include them."""
    ent = entitlements.effective(user_id)
    access = access_map()
    out = []
    for b in BUILTINS:
        tier = tier_of(b, access)
        needed = [TIER_FEATURE[tier], *b.needs]
        missing = [f for f in needed if not ent.has(f)]
        item = {"id": b.id, "label": b.label, "description": b.description, "tier": tier, "kinds": kinds(b.sections),
                "locked": bool(missing)}
        if missing:
            item["needs"] = [entitlements.FEATURES[f].label for f in missing]
            item["plans"] = _plans_with(needed)
        else:
            item["sections"] = b.sections
        out.append(item)
    return out


# Thresholds for suggesting a preset after the upload analysis (ai/builtin.py analyze_image).
SMALL_LETTERS_MM = 6.0  # "Small text & logos" is meant for lettering under ~6 mm
LARGE_DESIGN_MM = 200.0  # "Large design": jacket backs over ~200 mm


def suggest(analysis: dict, width_mm: float, user_id: int) -> dict | None:
    """Which built-in preset suits a picture, from the smart analysis after the upload:
    ``{id, label, reason}`` or None. Only presets the account can apply are suggested; the
    editor offers it with a button and never applies it by itself."""
    text = analysis.get("text") or {}
    letters = text.get("letter_height_mm")
    candidates: list[tuple[str, str]] = []
    if analysis.get("design_type") == "photo":
        candidates.append(("photo", "the picture looks like a photo"))
    if text.get("found") and letters and letters < SMALL_LETTERS_MM:
        candidates.append(("small_text", f"it has lettering about {letters:g} mm tall"))
    if width_mm > LARGE_DESIGN_MM:
        candidates.append(("large", f"the design is {width_mm:g} mm wide"))
    if analysis.get("cleanup") == "enhance" or (analysis.get("extract") or {}).get("denoise", 0) >= 3:
        candidates.append(("clean_logo", "the picture is grainy or low-resolution"))
    if not candidates:
        return None
    usable = {b["id"]: b for b in builtins_for(user_id) if not b["locked"]}
    for pid, reason in candidates:
        if pid in usable:
            return {"id": pid, "label": usable[pid]["label"], "reason": reason}
    return None


def admin_view() -> dict:
    """Admin > Plans: every built-in with its tier, and counts of saved presets (never their content)."""
    access = access_map()
    with db.tx() as c:
        r = c.execute("SELECT COUNT(*) AS n, COUNT(DISTINCT user_id) AS users FROM user_presets").fetchone()
    return {"builtins": [{"id": b.id, "label": b.label, "description": b.description, "default_tier": b.tier,
                          "tier": tier_of(b, access), "kinds": kinds(b.sections), "settings": count_settings(b.sections),
                          "needs": [entitlements.FEATURES[f].label for f in b.needs]} for b in BUILTINS],
            "saved": {"presets": r["n"], "accounts": r["users"]}}


# --------------------------------------------------------------------------- each account's own presets

def _name(value) -> str:
    name = re.sub(r"\s+", " ", str(value or "")).strip()
    if not 1 <= len(name) <= MAX_NAME:
        raise ValueError(f"Preset name: 1-{MAX_NAME} characters.")
    return name


def _description(value) -> str:
    d = str(value or "").strip()
    if len(d) > MAX_DESCRIPTION:
        raise ValueError(f"Description: at most {MAX_DESCRIPTION} characters.")
    return d


def _settings(sections) -> tuple[str, list[str]]:
    sec, dropped = clean(sections)
    if not sec:
        raise ValueError("Choose at least one group of settings to save.")
    text = json.dumps(sec, separators=(",", ":"), sort_keys=True)
    if len(text.encode()) > MAX_SETTINGS_BYTES:
        raise ValueError("These settings are too large for a preset.")
    return text, dropped


def _row(r) -> dict:
    try:
        raw = json.loads(r["settings"])
    except ValueError:
        raw = {}
    # schema upgrades go here (r["schema"] < SCHEMA); today there is only version 1
    sections, dropped = clean(raw if isinstance(raw, dict) else {})
    out = {"id": r["id"], "name": r["name"], "description": r["description"], "sections": sections, "kinds": kinds(sections),
           "schema": SCHEMA, "created_at": r["created_at"], "updated_at": r["updated_at"]}
    if dropped:
        out["skipped"] = dropped
    return out


def _status(c, ent) -> dict:
    n = c.execute("SELECT COUNT(*) AS n FROM user_presets WHERE user_id = ?", (ent.user_id,)).fetchone()["n"]
    limit = ent.limit_of("max_custom_presets")
    allowed = ent.has("custom_presets")
    return {"count": n, "limit": limit, "feature": allowed, "can_save": allowed and (limit is None or n < limit)}


def status(user_id: int) -> dict:
    """{count, limit (None = no limit), feature, can_save} for the account page and the editor."""
    ent = entitlements.effective(user_id)
    with db.tx() as c:
        return _status(c, ent)


def list_mine(user_id: int) -> dict:
    ent = entitlements.effective(user_id)
    with db.tx() as c:
        rows = c.execute("SELECT * FROM user_presets WHERE user_id = ? ORDER BY name COLLATE NOCASE, id", (user_id,)).fetchall()
        return {"presets": [_row(r) for r in rows]} | _status(c, ent)


def _require_room(c, user_id: int) -> None:
    """Inside the caller's write transaction: the feature, then the count against the limit."""
    ent = entitlements.effective(user_id)
    if not ent.has("custom_presets"):
        raise EntitlementError(f"Saving your own presets is not included in your {ent.plan['name']} plan. "
                               "Ask your administrator if you need it.", "feature_unavailable")
    st = _status(c, ent)
    if not st["can_save"]:
        raise EntitlementError(f"You have {st['count']} of {st['limit']} presets. Delete one to save a new preset.",
                               "preset_limit")


def _clash(c, user_id: int, name: str, not_id: int | None = None) -> None:
    r = c.execute("SELECT id, name FROM user_presets WHERE user_id = ? AND name = ? COLLATE NOCASE AND id IS NOT ?",
                  (user_id, name, not_id)).fetchone()
    if r:
        raise NameTaken(r["name"], r["id"])


def _get(c, user_id: int, preset_id: int):
    r = c.execute("SELECT * FROM user_presets WHERE id = ? AND user_id = ?", (preset_id, user_id)).fetchone()
    if r is None:
        raise LookupError("No such preset.")
    return r


def create(user_id: int, name, description, sections) -> dict:
    name, description = _name(name), _description(description)
    text, dropped = _settings(sections)
    now = time.time()
    with db.tx() as c:  # BEGIN IMMEDIATE: the count and the insert cannot interleave
        _require_room(c, user_id)
        _clash(c, user_id, name)
        cur = c.execute("INSERT INTO user_presets (user_id, name, description, settings, schema, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)", (user_id, name, description, text, SCHEMA, now, now))
        out = _row(_get(c, user_id, cur.lastrowid))
    if dropped:
        out["skipped"] = dropped
    return out


def update(user_id: int, preset_id: int, name=None, description=None, sections=None) -> dict:
    """Rename, describe or replace the settings of an own preset. Allowed after a downgrade."""
    now = time.time()
    with db.tx() as c:
        _get(c, user_id, preset_id)
        fields: dict = {}
        dropped: list[str] = []
        if name is not None:
            fields["name"] = _name(name)
            _clash(c, user_id, fields["name"], preset_id)
        if description is not None:
            fields["description"] = _description(description)
        if sections is not None:
            fields["settings"], dropped = _settings(sections)
            fields["schema"] = SCHEMA
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            c.execute(f"UPDATE user_presets SET {sets}, updated_at = ? WHERE id = ? AND user_id = ?",
                      [*fields.values(), now, preset_id, user_id])
        out = _row(_get(c, user_id, preset_id))
    if dropped:
        out["skipped"] = dropped
    return out


def duplicate(user_id: int, preset_id: int) -> dict:
    now = time.time()
    with db.tx() as c:
        src = _get(c, user_id, preset_id)
        _require_room(c, user_id)
        taken = {r["name"].lower() for r in c.execute("SELECT name FROM user_presets WHERE user_id = ?", (user_id,))}
        base = src["name"][:MAX_NAME - 10]
        name = f"{base} (copy)"
        n = 2
        while name.lower() in taken:
            name = f"{base} (copy {n})"
            n += 1
        cur = c.execute("INSERT INTO user_presets (user_id, name, description, settings, schema, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)", (user_id, name, src["description"], src["settings"], src["schema"], now, now))
        return _row(_get(c, user_id, cur.lastrowid))


def delete(user_id: int, preset_id: int) -> None:
    with db.tx() as c:
        _get(c, user_id, preset_id)
        c.execute("DELETE FROM user_presets WHERE id = ? AND user_id = ?", (preset_id, user_id))

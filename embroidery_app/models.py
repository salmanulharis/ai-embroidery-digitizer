"""Core data model shared by every pipeline stage.

Coordinates are always millimetres, origin top-left, y pointing down.
A ``Design`` is the approved, editable artwork (vector shapes grouped into
thread-colour layers). A ``StitchPlan`` is the machine-ready result.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import asdict, dataclass, field, replace
from typing import Iterable, Literal, Optional

from shapely import wkt
from shapely.geometry.base import BaseGeometry

StitchType = str  # one of STITCH_TYPES
ObjectKind = Literal["area", "line"]

RGB = tuple[int, int, int]


def rgb_to_hex(rgb: RGB) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(int(c) for c in rgb))


def hex_to_rgb(value: str) -> RGB:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


@dataclass
class ColorLayer:
    index: int
    rgb: RGB
    name: str = ""
    enabled: bool = True
    stitch_type: StitchType = "auto"
    angle: Optional[float] = None  # fill angle override, degrees

    @property
    def hex(self) -> str:
        return rgb_to_hex(self.rgb)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rgb"] = list(self.rgb)
        d["hex"] = self.hex
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ColorLayer":
        rgb = hex_to_rgb(d["hex"]) if "hex" in d and d["hex"] else tuple(d["rgb"])
        return cls(
            index=int(d["index"]),
            rgb=rgb,  # type: ignore[arg-type]
            name=d.get("name", ""),
            enabled=bool(d.get("enabled", True)),
            stitch_type=d.get("stitch_type") if d.get("stitch_type") in STITCH_TYPES else "auto",
            angle=None if d.get("angle") in (None, "") else float(d["angle"]),
        )


# Stitch styles a colour or a shape can be given: (id, label for the menus).
# "lettering": satin that follows each stroke (small text, script, fine line art).
# The decorative outlines and borders are sewn along a line, or around a filled
# shape's outline (see stitch/outlines.py).
STITCH_STYLES: tuple[tuple[str, str], ...] = (
    ("auto", "Automatic"),
    ("fill", "Fill (large areas)"),
    ("satin", "Satin (smooth, narrow)"),
    ("lettering", "Lettering (follows each stroke)"),
    ("running", "Outline only"),
    ("double_run", "Double run outline (there and back)"),
    ("backstitch", "Backstitch outline (hand-sewn look)"),
    ("stemstitch", "Stemstitch outline (rope look)"),
    ("sculpture_run", "Sculpture run outline (beaded look)"),
    ("zigzag", "Zigzag border (open)"),
    ("estitch", "E-stitch border (blanket edge)"),
    ("offset_fill", "Offset fill (rings that follow the outline)"),
    ("spiral_fill", "Spiral fill (one continuous spiral)"),
    ("contour_fill", "Contour fill (rows follow a curved column)"),
    ("radial_fill", "Radial fill (rays from the centre)"),
    ("shaded_fill", "Shaded fill (dense to light, one colour)"),
    ("stipple_fill", "Stipple fill (light meandering line)"),
    ("maze_fill", "Maze fill (right-angled meander)"),
    ("cross_stitch", "Cross stitch (hand cross-stitch look)"),
    ("motif_run", "Motif outline (chain of small motifs)"),
    ("hand_stitch", "Hand stitch outline (hand-sewn look)"),
    ("motif_fill", "Motif fill (rows of motifs)"),
    ("paired_motif_fill", "Paired motif fill (interlocking rows)"),
    ("pattern_fill", "Pattern fill (embossed pattern)"),
    ("jagged_fill", "Jagged fill (rough edges: fur, grass)"),
    ("raised_satin", "Raised satin (padded, up to 7 mm)"),
    ("applique", "Applique (placement, stop, tack, cover)"),
    ("guided_satin", "Guided satin (follows a line you draw)"),
    ("florentine_fill", "Florentine fill (rows bend with a line you draw)"),
    ("carved_fill", "Carved fill (a line you draw pressed into the fill)"),
    ("blend_fill", "Blend fill (fades into another colour)"),
    ("flexi_split", "Flexi split satin (pattern follows the column)"),
    ("motif_column", "Motif column (motifs as wide as the shape)"),
)
STITCH_TYPES = tuple(k for k, _ in STITCH_STYLES)

# The trade name of each style - what Wilcom and digitizers call it - shown next to the
# plain-language label in the menus, so both readers recognise it.
STITCH_TERMS: dict[str, str] = {
    "fill": "Tatami",
    "satin": "Satin column",
    "lettering": "Satin lettering",
    "running": "Run stitch",
    "double_run": "Double run",
    "backstitch": "Backstitch",
    "stemstitch": "Stemstitch",
    "sculpture_run": "Sculpture run",
    "zigzag": "Zigzag",
    "estitch": "E-stitch",
    "offset_fill": "Offset fill",
    "spiral_fill": "Spiral fill",
    "contour_fill": "Contour fill",
    "radial_fill": "Radial fill",
    "shaded_fill": "Accordion spacing",
    "stipple_fill": "Stipple fill",
    "maze_fill": "Maze fill",
    "cross_stitch": "Cross stitch",
    "motif_run": "Motif run",
    "hand_stitch": "Hand stitch",
    "motif_fill": "Motif fill",
    "paired_motif_fill": "Paired motif fill",
    "pattern_fill": "Program split",
    "jagged_fill": "Jagged edge",
    "raised_satin": "Raised satin",
    "applique": "Applique",
    "guided_satin": "Turning satin",
    "florentine_fill": "Florentine effect",
    "carved_fill": "Carving stamp / user-defined split",
    "blend_fill": "Multi blend",
    "flexi_split": "Flexi split",
    "motif_column": "Motif column",
}


def style_label(style: str) -> str:
    """Menu label with the trade name, e.g. "Fill (large areas) - Tatami". A label that
    already starts with its trade name ("Offset fill (...)") is not repeated."""
    label = dict(STITCH_STYLES).get(style, style)
    term = STITCH_TERMS.get(style)
    if not term or label.lower().startswith(term.lower()):
        return label
    return f"{label} — {term}"
OUTLINE_STYLES = ("double_run", "backstitch", "stemstitch", "sculpture_run", "zigzag", "estitch")
FILL_STYLE_NAMES = ("offset_fill", "spiral_fill", "contour_fill", "radial_fill", "shaded_fill",
                    "stipple_fill", "maze_fill", "cross_stitch")
DECOR_PATH_STYLES = ("motif_run", "hand_stitch")  # sewn along a line or a shape's outline
DECOR_AREA_STYLES = ("motif_fill", "paired_motif_fill", "pattern_fill", "jagged_fill", "raised_satin", "applique")
FILL_PATTERN_TILES = ("diamonds", "squares", "bricks", "scales")  # pattern fill (stitch/decorative.py)
GUIDED_STYLES = ("guided_satin", "florentine_fill", "carved_fill")  # need a guide line (stitch/guided.py)
GUIDE_MAX_POINTS = 400


def clean_guide(value) -> Optional[tuple]:
    """A guide line as a tuple of (x, y) mm points, or None when it is not a usable line."""
    if not value:
        return None
    try:
        pts = tuple((round(float(x), 3), round(float(y), 3)) for x, y in value)
    except (TypeError, ValueError):
        return None
    if len(pts) < 2 or len(pts) > GUIDE_MAX_POINTS or not all(map(lambda p: all(map(math.isfinite, p)), pts)):
        return None
    return pts


def new_object_id() -> str:
    return uuid.uuid4().hex[:10]


def normalize_angle(value: float) -> float:
    """Stitch rows have no front/back: 225° is the same direction as 45°."""
    return round(float(value) % 180.0, 2)


# Tatami textures: how the needle points shift from row to row (see stitch/methods.py).
FILL_PATTERNS = ("standard", "brick", "diagonal", "random")

# Named foundation (underlay) types, see stitch/underlay.py.
FILL_UNDERLAYS = ("none", "edge", "edge_tatami", "double_tatami")
SATIN_UNDERLAYS = ("none", "center", "edge", "zigzag", "edge_zigzag", "double_zigzag", "auto")
UNDERLAY_STYLES = tuple(dict.fromkeys(FILL_UNDERLAYS + SATIN_UNDERLAYS))

# Allowed ranges for per-shape thickness settings (same limits as the design-wide settings).
SPACING_RANGE_MM = (0.25, 1.0)  # gap between fill rows / satin stitches
PULL_RANGE_MM = (0.0, 0.8)  # extra width (shrink compensation)
PUSH_RANGE_MM = (0.0, 0.5)  # push compensation (columns / fills shortened at each end)


def _in_range(value, bounds: tuple[float, float]) -> Optional[float]:
    """Stored value -> float within bounds, or None (= use the design setting)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return round(v, 3) if bounds[0] <= v <= bounds[1] else None


@dataclass
class DesignObject:
    layer: int
    geometry: BaseGeometry  # Polygon/MultiPolygon for areas, LineString/MultiLineString for lines
    kind: ObjectKind = "area"
    stroke_width_mm: float = 0.0
    id: str = field(default_factory=new_object_id)
    # Per-shape overrides; None = use the colour's / design's setting.
    stitch_type: Optional[StitchType] = None
    angle: Optional[float] = None
    enabled: bool = True  # False = "don't stitch this shape"
    spacing_mm: Optional[float] = None  # thickness: fill row gap / satin stitch gap
    pull_mm: Optional[float] = None  # extra width (shrink compensation)
    bold: Optional[bool] = None  # outlines sewn 3x (True) / once (False)
    underlay_style: Optional[str] = None  # foundation type, see UNDERLAY_STYLES / stitch/underlay.py
    guide: Optional[tuple] = None  # guide line ((x, y), ...) in mm for the guided styles
    blend_to: Optional[int] = None  # blend fill: the colour (layer index) it fades into

    @property
    def customised(self) -> bool:
        return self.settings() != _DEFAULT_SETTINGS

    def settings(self) -> tuple:
        """All per-shape settings as one value. Merging, re-extract carry-over, reset
        and undo work on this tuple, so new settings only need adding here."""
        return (self.stitch_type, self.angle, self.enabled, self.spacing_mm, self.pull_mm, self.bold, self.underlay_style,
                self.guide, self.blend_to)

    def apply_settings(self, settings: tuple) -> None:
        (self.stitch_type, self.angle, self.enabled, self.spacing_mm, self.pull_mm, self.bold, self.underlay_style,
         self.guide, self.blend_to) = settings

    def clear_settings(self) -> None:
        self.apply_settings(_DEFAULT_SETTINGS)

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "layer": self.layer,
            "kind": self.kind,
            "stroke_width_mm": self.stroke_width_mm,
            "wkt": self.geometry.wkt,
        }
        # overrides are only written when set, so un-customised designs serialise as before
        if self.stitch_type is not None:
            d["stitch_type"] = self.stitch_type
        if self.angle is not None:
            d["angle"] = self.angle
        if not self.enabled:
            d["enabled"] = False
        if self.spacing_mm is not None:
            d["spacing_mm"] = self.spacing_mm
        if self.pull_mm is not None:
            d["pull_mm"] = self.pull_mm
        if self.bold is not None:
            d["bold"] = self.bold
        if self.underlay_style is not None:
            d["underlay_style"] = self.underlay_style
        if self.guide is not None:
            d["guide"] = [list(p) for p in self.guide]
        if self.blend_to is not None:
            d["blend_to"] = self.blend_to
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "DesignObject":
        st = d.get("stitch_type")
        angle = d.get("angle")
        bold = d.get("bold")
        underlay = d.get("underlay_style")
        return cls(
            layer=int(d["layer"]),
            geometry=wkt.loads(d["wkt"]),
            kind=d.get("kind", "area"),
            stroke_width_mm=float(d.get("stroke_width_mm", 0.0)),
            id=str(d.get("id") or new_object_id()),  # designs saved before ids existed get one
            stitch_type=st if st in STITCH_TYPES else None,
            angle=None if angle in (None, "") else normalize_angle(angle),
            enabled=bool(d.get("enabled", True)),
            spacing_mm=_in_range(d.get("spacing_mm"), SPACING_RANGE_MM),
            pull_mm=_in_range(d.get("pull_mm"), PULL_RANGE_MM),
            bold=bold if isinstance(bold, bool) else None,
            underlay_style=underlay if underlay in UNDERLAY_STYLES else None,
            guide=clean_guide(d.get("guide")),
            blend_to=int(d["blend_to"]) if isinstance(d.get("blend_to"), int) else None,
        )


_DEFAULT_SETTINGS = (None, None, True, None, None, None, None, None, None)


_CONFLICT_WARNING = (
    "Some shapes that were joined together had different stitch settings - "
    "the settings of the largest shape were kept. Check them in the shape settings."
)


@dataclass
class Design:
    width_mm: float
    height_mm: float
    layers: list[ColorLayer]
    objects: list[DesignObject]
    source_kind: str = "raster"  # raster | vector
    warnings: list[str] = field(default_factory=list)

    def layer(self, index: int) -> ColorLayer:
        for layer in self.layers:
            if layer.index == index:
                return layer
        raise KeyError(index)

    def layer_area(self, index: int) -> float:
        return sum(o.geometry.area for o in self.objects if o.layer == index and o.kind == "area")

    def normalize(self, layers: Optional[Iterable[int]] = None) -> "Design":
        """Make each colour's filled areas continuous regions (in place; returns self).

        A colour may hold several pieces that really form one region: shades merged
        by the user, near-identical vector colours, overlapping same-colour shapes.
        They are fused (seams bridged, see ``merge_areas``) so stitching treats them
        exactly like artwork drawn in one solid colour. Line objects are untouched.
        Idempotent; ``layers`` limits the work to the given layer indexes.
        """
        from .geometry import merge_areas_indexed, union_all

        targets = {l.index for l in self.layers} if layers is None else set(layers)
        pieces: dict[int, list[DesignObject]] = {}
        for o in self.objects:
            if o.kind == "area" and o.layer in targets:
                pieces.setdefault(o.layer, []).append(o)

        merged: dict[int, list[DesignObject]] = {}
        conflicts = False
        for layer, objs in pieces.items():
            if len(objs) < 2:
                merged[layer] = objs  # nothing to fuse: keep the objects (id + settings) as they are
                continue
            # everything of another colour blocks bridging (incl. disabled layers: fabric shows there)
            others = [
                o.geometry if o.kind == "area" else o.geometry.buffer(max(o.stroke_width_mm, 0.1) / 2)
                for o in self.objects
                if o.layer != layer
            ]
            out: list[DesignObject] = []
            used_ids: set[str] = set()
            for poly, src, untouched in merge_areas_indexed([o.geometry for o in objs], obstacles=union_all(others)):
                sources = [objs[i] for i in src] or [max(objs, key=lambda o: o.geometry.area)]
                main = max(sources, key=lambda o: o.geometry.area)
                if untouched and main.id not in used_ids:
                    out.append(main)  # untouched piece: keep the very same object
                    used_ids.add(main.id)
                    continue
                # fused region: settings carry over if all pieces agree, else the largest piece wins
                if len({o.settings() for o in sources}) > 1:
                    conflicts = True
                obj = DesignObject(
                    layer=layer,
                    geometry=poly,
                    id=main.id if main.id not in used_ids else new_object_id(),
                )
                obj.apply_settings(main.settings())
                used_ids.add(obj.id)
                out.append(obj)
            merged[layer] = out

        objects: list[DesignObject] = []
        emitted: set[int] = set()
        for o in self.objects:  # keep original object order; a layer's regions go where its first piece was
            if o.kind != "area" or o.layer not in merged:
                objects.append(o)
            elif o.layer not in emitted:
                emitted.add(o.layer)
                objects.extend(merged[o.layer])
        self.objects = objects
        if conflicts and _CONFLICT_WARNING not in self.warnings:
            self.warnings.append(_CONFLICT_WARNING)
        return self

    def normalized(self) -> "Design":
        """Normalized copy; the original (and its objects) are left untouched."""
        copy = replace(self, layers=list(self.layers), objects=list(self.objects), warnings=list(self.warnings))
        return copy.normalize()

    def to_dict(self) -> dict:
        return {
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "source_kind": self.source_kind,
            "warnings": self.warnings,
            "layers": [l.to_dict() for l in self.layers],
            "objects": [o.to_dict() for o in self.objects],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Design":
        return cls(
            width_mm=float(d["width_mm"]),
            height_mm=float(d["height_mm"]),
            layers=[ColorLayer.from_dict(l) for l in d["layers"]],
            objects=[DesignObject.from_dict(o) for o in d["objects"]],
            source_kind=d.get("source_kind", "raster"),
            warnings=list(d.get("warnings", [])),
        )

    def summary(self) -> dict:
        """Lightweight description for the UI (no geometry)."""
        layers = []
        for l in self.layers:
            d = l.to_dict()
            d["area_mm2"] = round(self.layer_area(l.index), 1)
            d["objects"] = sum(1 for o in self.objects if o.layer == l.index)
            d["customised"] = sum(1 for o in self.objects if o.layer == l.index and o.customised)
            layers.append(d)
        return {
            "width_mm": round(self.width_mm, 2),
            "height_mm": round(self.height_mm, 2),
            "source_kind": self.source_kind,
            "warnings": self.warnings,
            "layers": layers,
        }


THREAD_WEIGHTS = (40, 60)


@dataclass
class StitchParams:
    """Digitizing parameters. Defaults are conservative commercial values
    suitable for medium-weight fabric on multi-needle machines."""

    fill_spacing_mm: float = 0.40  # tatami row spacing (density)
    fill_stitch_length_mm: float = 3.5
    fill_angle: float = 45.0
    vary_fill_angle: bool = True  # rotate angle per layer for a richer look
    smart_direction: bool = False  # best direction for every section of the design (see stitch/smart.py)
    satin_spacing_mm: float = 0.40
    # wider columns become fill; satin stitches over max_stitch_mm (7) are auto-split
    satin_max_width_mm: float = 10.0
    satin_min_width_mm: float = 1.0  # 40 wt: narrower than ~1 mm breaks thread (Melco, ZSK)
    running_stitch_length_mm: float = 2.5
    running_repeats: int = 1  # 3 = bean / triple stitch
    border_width_mm: float = 2.0  # zigzag / E-stitch borders (a stroked line keeps its own width)
    border_spacing_mm: float = 1.2  # gap between zigzag / E-stitch border stitches
    stipple_spacing_mm: float = 2.0  # gap between the lines of stipple and maze fills
    cross_cell_mm: float = 3.0  # cross stitch grid size (Ink/Stitch default)
    fill_pattern: str = "standard"  # tatami texture: standard | brick | diagonal | random
    motif: str = "wave"  # motif outline / motif fill (stitch/library/motifs.json)
    motif_size_mm: float = 6.0
    pattern: str = "diamonds"  # pattern fill tile: diamonds | squares | bricks | scales
    pattern_size_mm: float = 8.0
    jagged_mm: float = 1.5  # jagged fill: how far the row ends wander
    motif_warp: bool = False  # 3D warp: motif fills bulge like a sphere
    trapunto: bool = False  # fills travel only along their outline (open fills: no visible travel)
    knockdown: bool = False  # open fill under small details on pile fabric (towel, fleece)
    photo_stitch: bool = False  # sew the whole picture as photo rows in one thread (stitch/photo.py)
    photo_row_mm: float = 1.0  # gap between photo stitch rows
    photo_style: str = "rows"  # rows (one thread) | reef (meander) | colour (rows in every colour)
    underlay: bool = True
    underlay_spacing_mm: float = 2.5
    fill_underlay: str = "edge_tatami"  # foundation under fills (see stitch/underlay.py)
    satin_underlay: str = "edge"  # foundation under single-angle satin
    pull_compensation_mm: float = 0.2
    # push compensation: fills and satin columns are shortened by this much at each end
    # (stitches push the fabric outward along the column); 0 = off
    push_compensation_mm: float = 0.0
    min_object_area_mm2: float = 0.05  # keeps dots/punctuation of lettering (enlarged to a sewable dot); specks are removed in step 2
    trim_threshold_mm: float = 2.0  # connectors longer than this are trimmed
    tie_stitches: bool = True
    max_stitch_mm: float = 7.0
    optimize: bool = True  # smart stitch reduction (see stitch/optimize.py)
    optimize_order: bool = True  # 2-opt sewing order within each colour (fewer, shorter jumps)
    thread_weight: int = 40  # 40 = standard thread; 60 = fine thread for small lettering
    # fabric preset chosen in the UI (see fabrics.py); the UI applies its values to the
    # settings above, the plan keeps the name for the worksheet's backing/needle advice
    fabric: str = "general"

    def __post_init__(self) -> None:
        from .fabrics import FABRIC_IDS

        if self.thread_weight not in THREAD_WEIGHTS:
            self.thread_weight = 40
        if self.fabric not in FABRIC_IDS:
            self.fabric = "general"
        if self.fill_underlay not in FILL_UNDERLAYS:
            self.fill_underlay = "edge_tatami"
        if self.fill_pattern not in FILL_PATTERNS:
            self.fill_pattern = "standard"
        if self.pattern not in FILL_PATTERN_TILES:
            self.pattern = "diamonds"
        self.motif_size_mm = min(max(float(self.motif_size_mm), 3.0), 20.0)
        self.pattern_size_mm = min(max(float(self.pattern_size_mm), 3.0), 30.0)
        self.jagged_mm = min(max(float(self.jagged_mm), 0.3), 5.0)
        self.photo_row_mm = min(max(float(self.photo_row_mm), 0.6), 3.0)
        if self.photo_style not in ("rows", "reef", "colour"):
            self.photo_style = "rows"
        if self.satin_underlay not in SATIN_UNDERLAYS:
            self.satin_underlay = "edge"
        self.push_compensation_mm = min(max(float(self.push_compensation_mm), PUSH_RANGE_MM[0]), PUSH_RANGE_MM[1])

    @classmethod
    def from_dict(cls, d: dict | None) -> "StitchParams":
        params = cls()
        for key, value in (d or {}).items():
            if hasattr(params, key) and value is not None:
                current = getattr(params, key)
                setattr(params, key, type(current)(value) if not isinstance(current, bool) else bool(value))
        params.__post_init__()
        return params

    def to_dict(self) -> dict:
        return asdict(self)


# Stitch commands
STITCH, JUMP, TRIM, COLOR_CHANGE = "stitch", "jump", "trim", "color_change"
STOP = "stop"  # the machine stops (thread not cut), e.g. to lay an applique piece


@dataclass
class Run:
    """A continuous sequence of needle penetrations belonging to one object."""

    kind: str  # fill | satin | running | underlay | travel | tie
    points: list[tuple[float, float]]
    object_id: int = -1


@dataclass
class ColorBlock:
    rgb: RGB
    name: str
    runs: list[Run] = field(default_factory=list)


@dataclass
class StitchPlan:
    width_mm: float
    height_mm: float
    blocks: list[ColorBlock]
    # Flattened machine sequence: (command, x_mm, y_mm, kind)
    ops: list[tuple[str, float, float, str]] = field(default_factory=list)
    optimization: dict = field(default_factory=dict)  # smart reduction report (empty when off)
    thread_weight: int = 40
    fabric: str = "general"  # fabric preset id (worksheet advice), see fabrics.py
    # machine check: spots where the thread may break or detail may not show
    # [{"x", "y", "type", "count"}], see stitch/safety.py
    risks: list[dict] = field(default_factory=list)

    def stats(self) -> dict:
        stitches = sum(1 for op in self.ops if op[0] == STITCH)
        jumps = sum(1 for op in self.ops if op[0] == JUMP)
        trims = sum(1 for op in self.ops if op[0] == TRIM)
        changes = sum(1 for op in self.ops if op[0] == COLOR_CHANGE)
        stops = sum(1 for op in self.ops if op[0] == STOP)
        thread_mm = 0.0
        prev = None
        for cmd, x, y, _ in self.ops:
            if cmd == STITCH and prev is not None:
                thread_mm += ((x - prev[0]) ** 2 + (y - prev[1]) ** 2) ** 0.5
            if cmd in (STITCH, JUMP):
                prev = (x, y)
        xs = [op[1] for op in self.ops if op[0] == STITCH] or [0]
        ys = [op[2] for op in self.ops if op[0] == STITCH] or [0]
        # ~750 stitches/min average incl. slowdowns, ~10 s per trim, ~15 s per colour change
        minutes = stitches / 750 + trims * 10 / 60 + (changes + stops) * 15 / 60
        return {
            "stitches": stitches,
            "jumps": jumps,
            "trims": trims,
            "color_changes": changes,
            "stops": stops,
            "colors": len(self.blocks),
            "width_mm": round(max(xs) - min(xs), 1),
            "height_mm": round(max(ys) - min(ys), 1),
            # top thread ~ stitched length * 1.0, bobbin ~ 1/3; report top thread metres
            "thread_m": round(thread_mm / 1000 * 1.1, 1),
            "est_minutes": round(minutes, 1),
        }

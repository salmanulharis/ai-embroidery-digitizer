"""Fabric presets: one choice that sets the stitch settings a fabric needs.

A preset only *fills in* existing stitch settings (the user can still change every
value) and names the backing and needle to use, which the production worksheet
prints for the machine operator. "general" is today's default settings, so choosing
nothing changes nothing.

Values are starting points to confirm with a test sew-out. Pull compensation follows
Wilcom's guideline values (drill/cotton 0.20 mm, T-shirt 0.35 mm, fleece 0.40 mm);
the other adjustments follow the direction of Wilcom's Auto Fabric presets (e.g.
knits: more compensation, extra zigzag foundation, slightly lighter density; fleece:
heavy foundation) and are engineering estimates, not published numbers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Fabric:
    id: str
    label: str
    settings: dict = field(default_factory=dict)  # StitchParams fields this fabric sets
    backing: str = ""
    needle: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _settings(
    pull: float, fill: float, satin: float, foundation: float, satin_max: float = 10.0,
    fill_under: str = "edge_tatami", satin_under: str = "edge",
) -> dict:
    return {
        "pull_compensation_mm": pull,
        "fill_spacing_mm": fill,
        "satin_spacing_mm": satin,
        "underlay": True,
        "underlay_spacing_mm": foundation,
        "fill_underlay": fill_under,
        "satin_underlay": satin_under,
        "satin_max_width_mm": satin_max,
    }


FABRICS: tuple[Fabric, ...] = (
    Fabric("general", "General (standard settings)", _settings(0.20, 0.40, 0.40, 2.5),
           "Tearaway for woven fabric, cutaway for knits", "75/11: sharp for woven fabric, ballpoint for knits"),
    Fabric("woven", "Cotton / twill (woven)", _settings(0.20, 0.40, 0.40, 2.5),
           "Tearaway", "75/11 sharp"),
    Fabric("denim", "Denim / canvas", _settings(0.15, 0.45, 0.40, 3.0),
           "Tearaway (heavy)", "75/11 or 80/12 sharp",
           "Firm fabric: a little lighter fill and less compensation keep it flat."),
    Fabric("knit", "T-shirt / jersey knit", _settings(0.35, 0.42, 0.40, 2.0, satin_under="edge_zigzag"),
           "Cutaway", "75/11 ballpoint",
           "Stretchy: hoop without stretching the fabric."),
    Fabric("pique", "Polo piqué", _settings(0.30, 0.40, 0.40, 2.0, satin_under="edge_zigzag"),
           "Cutaway + water-soluble topping", "75/11 ballpoint",
           "Topping keeps stitches on top of the textured surface."),
    Fabric("fleece", "Fleece / sweatshirt", _settings(0.40, 0.45, 0.40, 2.0, fill_under="double_tatami", satin_under="double_zigzag"),
           "Cutaway + water-soluble topping", "75/11 ballpoint",
           "Thick pile: avoid very small text; topping stops stitches sinking in."),
    Fabric("towel", "Towel / terry", _settings(0.30, 0.40, 0.40, 2.0, fill_under="double_tatami", satin_under="edge_zigzag"),
           "Tearaway or washaway + water-soluble topping", "75/11 sharp",
           "Loops can swallow small text: make lettering larger than on flat fabric."),
    Fabric("cap", "Cap", _settings(0.25, 0.38, 0.38, 2.0, satin_max=7.0),
           "Cap backing (tearaway)", "75/11 sharp",
           "Keep the design under about 57 mm tall; sew from the centre outwards and from the bottom up."),
)

FABRIC_IDS = tuple(f.id for f in FABRICS)
_BY_ID = {f.id: f for f in FABRICS}


def get_fabric(fabric_id: str | None) -> Fabric:
    """The preset with this id; unknown or missing ids are "general"."""
    return _BY_ID.get(fabric_id or "general", _BY_ID["general"])

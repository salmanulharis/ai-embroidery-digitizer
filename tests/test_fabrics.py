"""Fabric presets: General is today's settings, every preset stays within the
settings' limits, and the chosen fabric reaches the production worksheet."""

from dataclasses import fields

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box

from embroidery_app.ai.builtin import STITCH_LIMITS
from embroidery_app.api import app
from embroidery_app.export import worksheet
from embroidery_app.fabrics import FABRICS, get_fabric
from embroidery_app.models import FILL_UNDERLAYS, SATIN_UNDERLAYS, ColorLayer, Design, DesignObject, StitchParams
from embroidery_app.stitch import plan_stitches

PARAM_NAMES = {f.name for f in fields(StitchParams)}
UNDERLAY_SPACING = (1.5, 5.0)  # the UI's range for foundation spacing


def test_general_is_the_standard_settings():
    defaults = StitchParams().to_dict()
    general = get_fabric("general")
    assert {k: defaults[k] for k in general.settings} == general.settings
    assert StitchParams().fabric == "general"


@pytest.mark.parametrize("fabric", FABRICS, ids=lambda f: f.id)
def test_presets_only_set_known_settings_within_limits(fabric):
    assert fabric.label and fabric.backing and fabric.needle
    for key, value in fabric.settings.items():
        assert key in PARAM_NAMES
        limits = STITCH_LIMITS.get(key)
        if limits and limits[0] is bool:
            assert isinstance(value, bool)
        elif limits:
            assert limits[0] <= value <= limits[1], key
    assert UNDERLAY_SPACING[0] <= fabric.settings["underlay_spacing_mm"] <= UNDERLAY_SPACING[1]
    assert fabric.settings["fill_underlay"] in FILL_UNDERLAYS
    assert fabric.settings["satin_underlay"] in SATIN_UNDERLAYS


def test_stretchy_and_fluffy_fabrics_get_stronger_foundations():
    assert get_fabric("knit").settings["satin_underlay"] == "edge_zigzag"
    assert get_fabric("fleece").settings["fill_underlay"] == "double_tatami"
    assert get_fabric("woven").settings["fill_underlay"] == "edge_tatami"


def test_unknown_fabric_falls_back_to_general():
    assert StitchParams.from_dict({"fabric": "velvet"}).fabric == "general"
    assert get_fabric("velvet").id == "general"
    assert StitchParams.from_dict({"fabric": "knit"}).fabric == "knit"


def _plan(fabric: str):
    design = Design(40, 20, [ColorLayer(0, (20, 60, 200), "blue")], [DesignObject(0, box(2, 2, 30, 15))])
    return plan_stitches(design, StitchParams(fabric=fabric))


def test_fabric_name_alone_does_not_change_the_stitches():
    # the UI applies a preset's values; the name only travels to the worksheet
    assert _plan("knit").ops == _plan("general").ops


def test_worksheet_names_fabric_backing_and_needle():
    text = worksheet(_plan("knit"), "logo")
    assert "Fabric: T-shirt / jersey knit. Backing: Cutaway." in text
    assert "Needle: 75/11 ballpoint." in text
    general = worksheet(_plan("general"), "logo")
    assert "Fabric:" not in general
    assert "Needle: 75/11 (ballpoint for knits, sharp for woven fabric)." in general  # unchanged


def test_config_lists_the_fabrics():
    r = TestClient(app).get("/api/config").json()
    assert [f["id"] for f in r["fabrics"]] == [f.id for f in FABRICS]
    assert r["stitch_defaults"]["fabric"] == "general"
    knit = next(f for f in r["fabrics"] if f["id"] == "knit")
    assert knit["settings"]["pull_compensation_mm"] == 0.35

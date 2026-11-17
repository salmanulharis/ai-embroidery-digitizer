"""Per-account preferences (prefs.py): the machine (Export) and the usual placement (start
screen). Fixed tables, validated values, per account, never design data."""

import pytest

from accounts import admin, new_user, real_login  # noqa: F401
from embroidery_app import prefs
from embroidery_app.export import FORMATS
from embroidery_app.ingest.raster import RASTER_RANGES
from embroidery_app.ingest.vector import VECTOR_RANGES


def test_every_machine_reads_a_format_we_write():
    for key, m in prefs.MACHINES.items():
        assert m["format"] in FORMATS, key
        assert m["label"], key


def test_every_placement_size_is_one_the_editor_accepts():
    for key, p in prefs.PLACEMENTS.items():
        if p["size_mm"] is None:
            continue
        for ranges in (RASTER_RANGES, VECTOR_RANGES):
            lo, hi = ranges["width_mm"]
            assert lo <= p["size_mm"] <= hi, key
    assert prefs.PLACEMENTS["custom"]["size_mm"] is None  # "keep the editor's value" must exist


def test_config_sends_the_tables():
    from fastapi.testclient import TestClient

    from embroidery_app.api import app

    cfg = TestClient(app).get("/api/config").json()
    assert [m["key"] for m in cfg["machines"]] == list(prefs.MACHINES)
    assert [p["key"] for p in cfg["placements"]] == list(prefs.PLACEMENTS)


@pytest.mark.parametrize("body", [{"colour": "red"}, {"machine": "sewing-robot"}, {"placement": "forehead"}, ["machine"]])
def test_clean_refuses_unknown_keys_and_values(body):
    with pytest.raises(ValueError):
        prefs.clean(body)


def test_saved_per_account_merged_and_cleared(real_login):  # noqa: F811
    a = admin()
    _, one = new_user(a)
    _, two = new_user(a)
    assert one.get("/api/me/prefs").json() == {}
    assert one.put("/api/me/prefs", json={"machine": "brother"}).json() == {"machine": "brother"}
    assert one.put("/api/me/prefs", json={"placement": "cap"}).json() == {"machine": "brother", "placement": "cap"}
    assert two.get("/api/me/prefs").json() == {}  # the other account is not affected
    r = one.put("/api/me/prefs", json={"machine": "nope"})
    assert r.status_code == 400
    assert one.get("/api/me/prefs").json() == {"machine": "brother", "placement": "cap"}  # unchanged
    assert one.put("/api/me/prefs", json={"machine": None}).json() == {"placement": "cap"}


def test_needs_a_session(real_login):  # noqa: F811
    from accounts import browser

    assert browser().get("/api/me/prefs").status_code == 401
    assert browser().put("/api/me/prefs", json={"machine": "brother"}).status_code == 401


def test_the_analysis_measures_the_design_proportions():
    """The placement step sizes the longest side from the design itself, not the picture
    with its background (builtin.analyze_image -> measurements.design_aspect)."""
    import numpy as np

    from embroidery_app.ai.builtin import analyze_image

    img = np.full((400, 400, 4), 255, np.uint8)
    img[100:300, 50:350, :3] = (200, 40, 40)  # a 300 x 200 px red block on white
    a = analyze_image(img, 90)["measurements"]["design_aspect"]
    assert abs(a - 1.5) < 0.05, a

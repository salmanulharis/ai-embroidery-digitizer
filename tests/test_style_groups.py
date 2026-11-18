"""The style menus' groups cover every stitch style exactly once, and reach the browser."""

from fastapi.testclient import TestClient

from embroidery_app.api import app
from embroidery_app.models import STITCH_STYLE_GROUPS, STITCH_STYLES


def test_every_style_is_in_exactly_one_group():
    grouped = [k for _, keys in STITCH_STYLE_GROUPS for k in keys]
    assert sorted(grouped) == sorted(k for k, _ in STITCH_STYLES)
    assert len(grouped) == len(set(grouped))
    assert all(label and keys for label, keys in STITCH_STYLE_GROUPS)


def test_config_sends_the_groups():
    cfg = TestClient(app).get("/api/config").json()
    assert cfg["stitch_style_groups"] == [[label, list(keys)] for label, keys in STITCH_STYLE_GROUPS]

"""Stitch-method registry: built-ins are registered, and a new method plugs in
without touching the planner."""

import pytest
from shapely.geometry import box

from embroidery_app.models import Run, StitchParams
from embroidery_app.stitch import planner, registry
from embroidery_app.stitch.registry import StitchMethod

BUILT_INS = ("fill", "satin", "satin_band", "satin_line", "column", "stroke", "dot", "running", "outline")


def test_built_in_methods_are_registered_with_their_flags():
    assert set(BUILT_INS) <= set(registry.names())
    assert [m for m in BUILT_INS if registry.is_directional(m)] == ["fill", "satin"]
    assert [m for m in BUILT_INS if registry.is_outline(m)] == ["running", "outline"]
    assert [m for m in BUILT_INS if registry.is_multi_piece(m)] == ["column", "stroke", "dot"]
    assert not any(registry.is_density(m) for m in ("running", "outline"))


def test_unknown_and_duplicate_methods_are_rejected():
    with pytest.raises(KeyError):
        registry.get("no-such-method")
    with pytest.raises(ValueError):
        registry.register(StitchMethod("fill", lambda *a: []))


@pytest.fixture
def scratch_registry(monkeypatch):
    monkeypatch.setattr(registry, "_METHODS", dict(registry._METHODS))


def test_a_new_method_is_sewn_by_the_planner(scratch_registry):
    def corners(item, angle, params, start):
        x0, y0, x1, y1 = item.geom.bounds
        return [Run("fill", [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], item.oid)]

    registry.register(StitchMethod("corners", corners, density=True))
    item = planner._Item(7, box(0, 0, 10, 5), "corners")
    runs = planner._stitch_item(item, 0.0, StitchParams(), (0.0, 0.0))
    assert [r.points for r in runs] == [[(0, 0), (10, 0), (10, 5), (0, 5)]]
    assert runs[0].object_id == 7
    assert registry.is_density("corners")

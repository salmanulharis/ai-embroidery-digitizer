"""The optional rembg cut-out always runs with the configured, licence-safe model,
never rembg's own default."""

import sys
import types

import numpy as np
from PIL import Image

from embroidery_app.config import settings
from embroidery_app.ingest import raster


def test_rembg_uses_the_pinned_model(monkeypatch):
    calls = {"sessions": [], "removed_with": []}

    def new_session(model):
        calls["sessions"].append(model)
        return f"session:{model}"

    def remove(img, session=None):
        calls["removed_with"].append(session)
        rgba = np.zeros((*np.asarray(img).shape[:2], 4), np.uint8)
        rgba[2:, 2:, 3] = 255  # foreground away from the top-left corner
        return Image.fromarray(rgba)

    monkeypatch.setitem(sys.modules, "rembg", types.SimpleNamespace(new_session=new_session, remove=remove))
    monkeypatch.setattr(raster, "_REMBG_SESSIONS", {})

    rgb = np.full((6, 6, 3), 200, np.uint8)
    for _ in range(2):
        background = raster._ai_background(rgb)

    model = settings.rembg_model  # isnet-general-use unless REMBG_MODEL is set
    assert calls["sessions"] == [model]  # named explicitly, loaded once, then reused
    assert calls["removed_with"] == [f"session:{model}"] * 2
    assert background[0, 0] and not background[3, 3]

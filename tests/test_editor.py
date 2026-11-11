"""Editor: bulk colour editing, shape (area) editing, multi-step undo/redo and the
aligned editing reference."""

import io

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from shapely.geometry import Point

from embroidery_app import service
from embroidery_app.api import app

client = TestClient(app)


def _png(rgb) -> bytes:
    return cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))[1].tobytes()


def picture():
    """White picture: red disc, blue bar, black square - well apart."""
    img = np.full((500, 800, 3), 255, np.uint8)
    cv2.circle(img, (200, 200), 120, (200, 30, 30), -1)
    cv2.rectangle(img, (420, 120), (740, 200), (30, 60, 200), -1)
    cv2.rectangle(img, (450, 300), (600, 450), (20, 20, 20), -1)
    return img


def project(width=80):
    pid = client.post("/api/projects", files={"file": ("p.png", _png(picture()))}).json()["id"]
    r = client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": width, "num_colors": 4}})
    assert r.status_code == 200, r.text
    return pid, r.json()


def design(pid):
    return service.load_design(pid)


def layer_of(res, rgb_test):
    return next(l for l in res["design"]["layers"] if rgb_test(l["rgb"]))


RED = lambda c: c[0] > 150 and c[1] < 100
BLUE = lambda c: c[2] > 150 and c[0] < 100
BLACK = lambda c: sum(c) < 150


# ---------------------------------------------------------------- reference

def test_reference_picture_lines_up_with_the_traced_shapes():
    pid, res = project()
    ref = res["reference"]
    assert ref["picture"] and ref["design"] and len(ref["box"]) == 4
    x, y, w, h = ref["box"]
    pic = np.array(Image.open(io.BytesIO(client.get(f"/api/projects/{pid}/reference/picture").content)).convert("RGB"))
    ph, pw = pic.shape[:2]
    d = design(pid)
    for layer, test in ((layer_of(res, RED), RED), (layer_of(res, BLUE), BLUE), (layer_of(res, BLACK), BLACK)):
        shape = next(o.geometry for o in d.objects if o.layer == layer["index"])
        for _ in range(3):  # points inside the shape show that colour in the reference picture
            p = shape.representative_point() if _ == 0 else shape.centroid
            px = int((p.x - x) / w * pw)
            py = int((p.y - y) / h * ph)
            assert test(pic[py, px].tolist()), (layer["name"], pic[py, px])
        # and the outlines agree to within ~0.3 mm
        mask = np.zeros((ph, pw), np.uint8)
        for poly in [shape] if shape.geom_type == "Polygon" else shape.geoms:
            pts = np.array([((cx - x) / w * pw, (cy - y) / h * ph) for cx, cy in poly.exterior.coords], np.int32)
            cv2.fillPoly(mask, [pts], 1)
        ref_mask = np.array([test(c) for c in pic.reshape(-1, 3).tolist()]).reshape(ph, pw)
        iou = (ref_mask & (mask > 0)).sum() / max(1, (ref_mask | (mask > 0)).sum())
        assert iou > 0.93, (layer["name"], iou)


def test_reference_design_is_the_first_extraction_and_survives_edits():
    pid, res = project()
    snap = client.get(f"/api/projects/{pid}/reference/design")
    assert snap.status_code == 200 and snap.headers["content-type"].startswith("image/svg")
    red = layer_of(res, RED)
    obj = next(o for o in res["objects"] if o["layer"] == red["index"])
    client.post(f"/api/projects/{pid}/objects/edit", json={"ids": [obj["id"]], "op": "shrink", "size_mm": 1})
    assert client.get(f"/api/projects/{pid}/reference/design").text == snap.text  # still the original
    assert client.get(f"/api/projects/{pid}/reference/nope").status_code == 404


# ---------------------------------------------------------------- bulk colour editing

def test_thread_colour_change_updates_every_area_of_that_colour():
    pid, res = project()
    red = layer_of(res, RED)
    layers = [{**l, "hex": "#00ff00" if l["index"] == red["index"] else l["hex"]} for l in res["design"]["layers"]]
    r = client.put(f"/api/projects/{pid}/layers", json={"layers": layers}).json()
    assert next(l for l in r["design"]["layers"] if l["index"] == red["index"])["hex"] == "#00ff00"
    assert sum(1 for o in r["objects"] if o["layer"] == red["index"]) == sum(1 for o in res["objects"] if o["layer"] == red["index"])


def test_colour_scope_applies_settings_to_every_area_of_the_colour():
    pid, res = project()
    blue = layer_of(res, BLUE)
    one = next(o for o in res["objects"] if o["layer"] == blue["index"])
    r = client.put(f"/api/projects/{pid}/objects", json={"ids": [one["id"]], "scope": "colour", "angle": 30, "stitch_type": "satin"}).json()
    mine = [o for o in r["objects"] if o["layer"] == blue["index"]]
    assert mine and all(o["angle"] == 30 and o["stitch_type"] == "satin" for o in mine)
    assert all(o["angle"] is None for o in r["objects"] if o["layer"] != blue["index"])


def test_moving_areas_to_a_new_or_existing_colour():
    pid, res = project()
    red, blue = layer_of(res, RED), layer_of(res, BLUE)
    red_ids = [o["id"] for o in res["objects"] if o["layer"] == red["index"]]
    r = client.post(f"/api/projects/{pid}/objects/recolor", json={"ids": red_ids, "hex": "#ffaa00"}).json()
    orange = next(l for l in r["design"]["layers"] if l["hex"] == "#ffaa00")
    assert all(l["index"] != red["index"] for l in r["design"]["layers"])  # emptied colour is removed
    assert r["selected"] and all(next(o for o in r["objects"] if o["id"] == i)["layer"] == orange["index"] for i in r["selected"])
    r = client.post(f"/api/projects/{pid}/objects/recolor", json={"ids": r["selected"], "target": blue["index"]}).json()
    assert all(l["hex"] != "#ffaa00" for l in r["design"]["layers"])
    assert len(r["design"]["layers"]) == len(res["design"]["layers"]) - 1
    r = client.post(f"/api/projects/{pid}/objects/recolor", json={"ids": red_ids[:1], "hex": "red"})
    assert r.status_code == 400


# ---------------------------------------------------------------- shape editing

def area(pid, layer):
    return sum(o.geometry.area for o in design(pid).objects if o.layer == layer)


def test_erase_and_add_change_only_what_was_drawn():
    pid, res = project()
    red = layer_of(res, RED)
    rid = next(o["id"] for o in res["objects"] if o["layer"] == red["index"])
    g = next(o.geometry for o in design(pid).objects if o.id == rid)
    cx, cy = g.centroid.x, g.centroid.y
    before = area(pid, red["index"])
    r = client.post(f"/api/projects/{pid}/objects/edit", json={
        "ids": [rid], "op": "erase", "tool": "brush", "points": [[cx - 5, cy], [cx + 5, cy]], "size_mm": 2}).json()
    after = area(pid, red["index"])
    assert before - after == pytest.approx(10 * 2 + np.pi, rel=0.1)  # a 10 x 2 mm stroke with round ends
    assert r["selected"] and r["can_undo"]
    # lasso-add a square back into the gap
    r = client.post(f"/api/projects/{pid}/objects/edit", json={
        "ids": r["selected"], "op": "add", "tool": "lasso",
        "points": [[cx - 6, cy - 1.5], [cx + 6, cy - 1.5], [cx + 6, cy + 1.5], [cx - 6, cy + 1.5]]}).json()
    assert area(pid, red["index"]) >= before - 0.05
    # undo twice -> exactly the original outline
    client.post(f"/api/projects/{pid}/undo")
    client.post(f"/api/projects/{pid}/undo")
    assert area(pid, red["index"]) == pytest.approx(before, abs=1e-6)


def test_added_area_is_removed_from_other_colours_underneath():
    pid, res = project()
    red, blue = layer_of(res, RED), layer_of(res, BLUE)
    d = design(pid)
    bar = next(o.geometry for o in d.objects if o.layer == blue["index"])
    rid = next(o.id for o in d.objects if o.layer == red["index"])
    bx, by = bar.centroid.x, bar.centroid.y
    blue_before = area(pid, blue["index"])
    client.post(f"/api/projects/{pid}/objects/edit", json={
        "ids": [rid], "op": "add", "tool": "lasso", "points": [[bx - 3, by - 3], [bx + 3, by - 3], [bx + 3, by + 3], [bx - 3, by + 3]]})
    d = design(pid)
    reds = [o.geometry for o in d.objects if o.layer == red["index"]]
    blues = [o.geometry for o in d.objects if o.layer == blue["index"]]
    assert any(g.contains(Point(bx, by)) for g in reds)
    assert not any(g.contains(Point(bx, by)) for g in blues)
    assert area(pid, blue["index"]) < blue_before - 20
    overlap = sum(r.intersection(b).area for r in reds for b in blues)
    assert overlap < 0.05  # nothing is stitched twice


def test_grow_shrink_smooth_and_full_erase():
    pid, res = project()
    black = layer_of(res, BLACK)
    bid = next(o["id"] for o in res["objects"] if o["layer"] == black["index"])
    a0 = area(pid, black["index"])
    r = client.post(f"/api/projects/{pid}/objects/edit", json={"ids": [bid], "op": "grow", "size_mm": 0.2}).json()
    a1 = area(pid, black["index"])
    assert a1 > a0
    r = client.post(f"/api/projects/{pid}/objects/edit", json={"ids": r["selected"], "op": "shrink", "size_mm": 0.2}).json()
    assert area(pid, black["index"]) == pytest.approx(a0, rel=0.02)
    r = client.post(f"/api/projects/{pid}/objects/edit", json={"ids": r["selected"], "op": "smooth", "size_mm": 0.4})
    assert r.status_code == 200
    g = next(o.geometry for o in design(pid).objects if o.layer == black["index"])
    minx, miny, maxx, maxy = g.bounds
    r = client.post(f"/api/projects/{pid}/objects/edit", json={
        "ids": r.json()["selected"], "op": "erase", "tool": "lasso",
        "points": [[minx - 1, miny - 1], [maxx + 1, miny - 1], [maxx + 1, maxy + 1], [minx - 1, maxy + 1]]}).json()
    assert all(l["index"] != black["index"] or not any(o["layer"] == black["index"] for o in r["objects"]) for l in r["design"]["layers"])


@pytest.mark.parametrize("body", [
    {"op": "explode"},
    {"op": "add", "points": []},
    {"op": "add", "tool": "lasso", "points": [[0, 0], [1, 1]]},
    {"op": "add", "tool": "spray", "points": [[0, 0]]},
    {"op": "grow", "size_mm": 100},
    {"op": "add", "points": [[0, "x"]]},
])
def test_shape_edit_validation(body):
    pid, res = project()
    ids = [o["id"] for o in res["objects"]][:1]
    assert client.post(f"/api/projects/{pid}/objects/edit", json={"ids": ids, **body}).status_code in (400, 422)
    assert client.post(f"/api/projects/{pid}/objects/edit", json={"ids": [], "op": "grow"}).status_code == 400


# ---------------------------------------------------------------- undo / redo

def test_history_is_capped_and_old_single_undo_is_migrated():
    pid, res = project()
    oid = res["objects"][0]["id"]
    for i in range(service.HISTORY_LIMIT + 5):
        client.put(f"/api/projects/{pid}/objects", json={"ids": [oid], "angle": i})
    steps = 0
    while client.post(f"/api/projects/{pid}/undo").status_code == 200:
        steps += 1
    assert steps == service.HISTORY_LIMIT
    # a project saved with the old one-level undo keeps that step
    d = service._dir(pid)
    (d / "design.prev.json").write_text((d / "design.json").read_text(encoding="utf-8"), encoding="utf-8")
    assert service.can_undo(pid) and not (d / "design.prev.json").exists()
    assert client.post(f"/api/projects/{pid}/undo").status_code == 200

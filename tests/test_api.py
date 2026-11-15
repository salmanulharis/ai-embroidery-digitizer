import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from embroidery_app.api import app

client = TestClient(app)


def _upload(name, data):
    r = client.post("/api/projects", files={"file": (name, data)})
    assert r.status_code == 200, r.text
    return r.json()


def test_config():
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert "dst" in body["formats"] and body["stitch_defaults"]["underlay"] is True
    assert {"openai", "gemini", "openrouter", "anthropic", "ollama", "custom"} <= {p["id"] for p in body["ai"]}
    assert all({"label", "capabilities", "needs_key", "server_configured"} <= set(p) for p in body["ai"])
    assert set(body["ai_clean_modes"]) == {"redraw", "remove_background", "enhance"}


def test_page_and_scripts_are_revalidated():
    """After an update the browser must not mix the new page with cached old scripts."""
    for path in ("/", "/static/app.js", "/static/style.css"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", path
    etag = client.get("/static/app.js").headers["etag"]
    assert client.get("/static/app.js", headers={"If-None-Match": etag}).status_code == 304


def test_full_raster_workflow(logo_png):
    meta = _upload("logo.png", logo_png)
    pid = meta["id"]
    assert meta["kind"] == "raster"

    r = client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 80}})
    assert r.status_code == 200, r.text
    design = r.json()["design"]
    assert r.json()["svg"].startswith("<?xml")
    layers = design["layers"]

    # user edits: rename, force satin on the last layer, re-order
    layers[-1]["name"] = "Accent"
    layers[-1]["stitch_type"] = "fill"
    edited = [{k: l[k] for k in ("index", "hex", "name", "enabled", "stitch_type", "angle")} for l in layers][::-1]
    r = client.put(f"/api/projects/{pid}/layers", json={"layers": edited})
    assert r.status_code == 200
    assert r.json()["design"]["layers"][0]["name"] == "Accent"

    r = client.post(f"/api/projects/{pid}/stitch", json={"params": {"fill_spacing_mm": 0.45, "underlay": True}})
    assert r.status_code == 200, r.text
    prev = r.json()
    assert prev["stats"]["stitches"] > 500 and len(prev["ops"]) % 4 == 0

    for fmt in ("dst", "exp", "pes", "svg", "png", "txt"):
        r = client.get(f"/api/projects/{pid}/export/{fmt}")
        assert r.status_code == 200 and len(r.content) > 50, fmt
        assert "attachment" in r.headers["content-disposition"]

    r = client.get(f"/api/projects/{pid}/export/zip")
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert any(n.endswith(".dst") for n in names) and any(n.endswith(".svg") for n in names)
    assert sorted(n.rsplit(".", 1)[1] for n in names) == ["dst", "exp", "pes", "png", "svg", "txt"]  # unchanged bundle

    # only the files ticked in the download panel (order kept, duplicates and case ignored)
    r = client.get(f"/api/projects/{pid}/export/zip?formats=PES,txt, dst,pes")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert [n.rsplit(".", 1)[1] for n in z.namelist()] == ["pes", "txt", "dst"]
    single = client.get(f"/api/projects/{pid}/export/dst").content
    assert z.read(next(n for n in z.namelist() if n.endswith(".dst"))) == single  # same file as on its own
    assert client.get(f"/api/projects/{pid}/export/zip?formats=svg").status_code == 200
    for bad in ("exe", "dst,zip", "", " , "):
        r = client.get(f"/api/projects/{pid}/export/zip?formats={bad}")
        assert r.status_code == 400, bad
    assert client.get(f"/api/projects/{pid}/export/dst?formats=nonsense").status_code == 200  # only used with zip


def test_merge_layers(logo_png):
    pid = _upload("logo.png", logo_png)["id"]
    layers = client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 80}}).json()["design"]["layers"]
    r = client.post(f"/api/projects/{pid}/layers/merge", json={"source": layers[1]["index"], "target": layers[0]["index"]})
    assert r.status_code == 200
    assert len(r.json()["design"]["layers"]) == len(layers) - 1


def test_svg_workflow(svg_text):
    pid = _upload("art.svg", svg_text.encode())["id"]
    r = client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 120, "knockout": True}})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/projects/{pid}/stitch", json={"params": {}})
    assert r.status_code == 200
    assert client.get(f"/api/projects/{pid}/export/dst").status_code == 200


def test_overlapping_stitch_requests_compute_once(svg_text, monkeypatch):
    """The editor updates its preview automatically, so stitch requests for one design can
    overlap. They run one after the other: the second finds the plans in the cache."""
    import threading
    import time

    from embroidery_app import service

    pid = _upload("art.svg", svg_text.encode())["id"]
    assert client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 120}}).status_code == 200
    real, calls = service.plan_stitches, []

    def slow_plan(design, params):
        calls.append(params.optimize)
        time.sleep(0.2)  # long enough for the second request to arrive while the first runs
        return real(design, params)

    monkeypatch.setattr(service, "plan_stitches", slow_plan)
    results = []
    threads = [threading.Thread(target=lambda: results.append(
        client.post(f"/api/projects/{pid}/stitch", json={"params": {"fill_spacing_mm": 0.45}}))) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [r.status_code for r in results] == [200, 200]
    assert results[0].json()["stats"] == results[1].json()["stats"]
    assert sorted(calls) == [False, True]  # optimised + original plan, computed once for both requests
    # an export waits for the stitch run and uses the same plan
    assert client.get(f"/api/projects/{pid}/export/dst").status_code == 200
    assert len(calls) == 2


def test_wilcom_package(logo_png):
    """DST + EPS / SVG artwork (all colours and one file per colour) + worksheet + read-me.
    Never presented as a Wilcom EMB project."""
    import re

    pid = _upload("logo.png", logo_png)["id"]
    design = client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 80}}).json()["design"]
    assert client.post(f"/api/projects/{pid}/stitch", json={"params": {}}).status_code == 200
    r = client.get(f"/api/projects/{pid}/export/wilcom")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert 'filename="logo_for_wilcom.zip"' in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert {"logo.dst", "logo_artwork.eps", "logo_artwork.svg", "logo_worksheet.txt", "Open in Wilcom - read me.txt"} <= set(names)
    assert z.read("logo.dst") == client.get(f"/api/projects/{pid}/export/dst").content  # the same stitches
    sewn = [l for l in design["layers"] if l["enabled"]]
    per_colour = sorted(n for n in names if n.startswith("artwork_by_colour/"))
    assert len(per_colour) == len(sewn)
    assert per_colour[0].startswith("artwork_by_colour/01_")
    eps = z.read("logo_artwork.eps").decode("ascii")
    assert eps.startswith("%!PS-Adobe-3.0 EPSF-3.0") and eps.rstrip().endswith("%%EOF")
    w, h = map(float, re.search(r"%%HiResBoundingBox: 0 0 ([\d.]+) ([\d.]+)", eps).groups())
    assert abs(w / 72 * 25.4 - design["width_mm"]) < 0.01 and abs(h / 72 * 25.4 - design["height_mm"]) < 0.01
    assert eps.count("setrgbcolor") == len(sewn) and "eofill" in eps
    readme = z.read("Open in Wilcom - read me.txt").decode()
    assert "not a Wilcom .EMB project" in readme
    assert client.get(f"/api/projects/{pid}/export/zip?formats=wilcom").status_code == 400  # a download of its own


def test_eps_keeps_holes_lines_and_skips_switched_off_colours():
    from shapely.geometry import LineString, box

    from embroidery_app.export import design_to_eps
    from embroidery_app.models import ColorLayer, Design, DesignObject

    ring = box(0, 0, 40, 20).difference(box(10, 5, 20, 15))
    design = Design(40, 20, [ColorLayer(0, (255, 0, 0), "Red"), ColorLayer(1, (0, 0, 255), "Blue", enabled=False),
                             ColorLayer(2, (0, 0, 0), "Black")],
                    [DesignObject(0, ring), DesignObject(1, box(25, 2, 35, 8)),
                     DesignObject(2, LineString([(0, 19), (40, 19)]), kind="line", stroke_width_mm=0.8)])
    eps = design_to_eps(design)
    assert eps.count("setrgbcolor") == 2 and "Blue" not in eps  # switched-off colour left out
    red = eps.split("% colour 0")[1].split("% colour 2")[0]
    assert red.count("\nz\n") == 2 and "eofill" in red  # outline + hole, filled even-odd
    assert "0.800 setlinewidth stroke" in eps
    assert "%%BoundingBox: 0 0 114 57" in eps  # 40 x 20 mm in points, rounded up
    only = design_to_eps(design, layers=[2])
    assert only.count("setrgbcolor") == 1 and "eofill" not in only


def test_ai_file_via_pdf_compat():
    fitz = pytest.importorskip("pymupdf")
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    page.draw_rect(fitz.Rect(20, 20, 180, 180), color=None, fill=(0.1, 0.3, 0.8))
    page.draw_circle(fitz.Point(230, 100), 50, color=None, fill=(0.9, 0.1, 0.1))
    data = doc.tobytes()
    pid = _upload("logo.ai", data)["id"]
    r = client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 100}})
    assert r.status_code == 200, r.text
    hexes = {l["hex"] for l in r.json()["design"]["layers"]}
    assert len(hexes) >= 2


def test_errors():
    assert client.post("/api/projects", files={"file": ("x.emb", b"\x00\x01\x02")}).status_code == 400
    assert client.get("/api/projects/../../etc/source").status_code == 404
    assert client.get("/api/projects/000000000000/source").status_code == 404
    pid = _upload("art.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>')["id"]
    assert client.post(f"/api/projects/{pid}/stitch", json={"params": {}}).status_code == 400  # not extracted
    r = client.post(f"/api/projects/{pid}/ai/clean", json={"provider": "openai"})
    assert r.status_code == 400  # vector projects can't be AI-cleaned

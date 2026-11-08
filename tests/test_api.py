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
    assert {p["name"] for p in body["ai"]} == {"openai", "gemini", "openrouter", "ollama"}


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


def test_ai_file_via_pdf_compat():
    fitz = pytest.importorskip("fitz")
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

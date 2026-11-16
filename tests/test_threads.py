"""Thread charts (THREAD_CHARTS_DIR): parsing, nearest thread, and where matches appear
(preview, worksheet, PES/VP3). The charts here are made up for the tests."""

import os
import tempfile

import pystitch as pe
import pytest
from fastapi.testclient import TestClient

from embroidery_app import service, threads
from embroidery_app.api import app
from embroidery_app.export import export_embroidery, worksheet
from embroidery_app.models import StitchParams

client = TestClient(app)

GPL = """GIMP Palette
Name: Ink/Stitch: Test Rayon
Columns: 4
# RGB Value\t\t                    Color Name Number
 40\t 41\t 42\t                         Black   1000
227\t229\t251\t                  Bright White   1001
200\t 30\t 40\t                   Medium Red / Dark   1147
 30\t 60\t200\t                    Royal Blue   1076
300\t  0\t  0\t                     Broken   9999
just text
"""

CSV = """# Name: Test Poly
number,name,hex
5555,Lemon,#FAE030
6666,Leaf,2F9E44
,No number,#808080
7777,Bad,#zz
"""


@pytest.fixture
def charts(tmp_path, monkeypatch):
    (tmp_path / "Test Rayon.gpl").write_text(GPL, encoding="utf-8")
    (tmp_path / "test_poly.csv").write_text(CSV, encoding="utf-8")
    (tmp_path / "empty.csv").write_text("number,name,hex\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")
    monkeypatch.setenv("THREAD_CHARTS_DIR", str(tmp_path))
    return tmp_path


def test_gpl_and_csv_are_read(charts):
    found = threads.charts()
    assert sorted(found) == ["test-poly", "test-rayon"]  # empty and .txt skipped
    rayon, poly = found["test-rayon"], found["test-poly"]
    assert rayon.name == "Test Rayon"  # "Ink/Stitch:" prefix removed
    assert [(t.number, t.name) for t in rayon.threads] == [
        ("1000", "Black"), ("1001", "Bright White"), ("1147", "Medium Red / Dark"), ("1076", "Royal Blue")]
    assert poly.name == "Test Poly"
    assert [(t.number, t.name, t.rgb) for t in poly.threads] == [
        ("5555", "Lemon", (250, 224, 48)), ("6666", "Leaf", (47, 158, 68)), ("", "No number", (128, 128, 128))]
    assert threads.catalog() == [{"id": "test-poly", "name": "Test Poly", "count": 3},
                                 {"id": "test-rayon", "name": "Test Rayon", "count": 4}]


def test_reloads_when_the_folder_changes(charts):
    assert len(threads.charts()) == 2
    (charts / "more.csv").write_text("number,name,r,g,b\n1,One,1,2,3\n", encoding="utf-8")
    assert "more" in threads.charts()
    (charts / "more.csv").unlink()
    assert "more" not in threads.charts()


def test_oversized_files_are_skipped(charts, monkeypatch):
    monkeypatch.setattr(threads, "MAX_FILE_BYTES", 50)
    assert threads.charts() == {}


def test_no_folder_means_no_charts(tmp_path, monkeypatch):
    monkeypatch.setenv("THREAD_CHARTS_DIR", str(tmp_path / "missing"))
    assert threads.catalog() == []
    assert threads.matches("anything", [(1, 2, 3)]) is None


def test_nearest_thread(charts):
    m = threads.matches("test-rayon", [(205, 35, 45), (255, 255, 255), (20, 20, 20)])
    assert [x["number"] for x in m] == ["1147", "1001", "1000"]
    assert m[0]["brand"] == "Test Rayon" and m[0]["hex"] == "#c81e28" and m[0]["delta_e"] < 3
    assert threads.matches("", [(1, 2, 3)]) is None
    assert threads.matches("unknown", [(1, 2, 3)]) is None


def test_chart_id_is_validated():
    assert StitchParams.from_dict({"thread_chart": "test-rayon"}).thread_chart == "test-rayon"
    for bad in ("../etc", "A B", "x" * 60, 5):
        assert StitchParams.from_dict({"thread_chart": bad}).thread_chart in ("", "5")


def test_matches_in_preview_worksheet_and_vp3(charts, svg_text, monkeypatch):
    pid = client.post("/api/projects", files={"file": ("a.svg", svg_text.encode())}).json()["id"]
    assert client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 80}}).status_code == 200
    plain = client.post(f"/api/projects/{pid}/stitch", json={"params": {"optimize": False}}).json()
    assert all("thread" not in c for c in plain["colors"])
    calls = []
    real = service.plan_stitches
    monkeypatch.setattr(service, "plan_stitches", lambda d, p: calls.append(1) or real(d, p))
    r = client.post(f"/api/projects/{pid}/stitch", json={"params": {"optimize": False, "thread_chart": "test-rayon"}}).json()
    assert calls == []  # the stitches stay the same: no new plan
    assert r["ops"] == plain["ops"]
    assert all(c["thread"]["brand"] == "Test Rayon" and c["thread"]["number"] for c in r["colors"])

    plan = service.get_plan(pid)
    text = worksheet(plan, "t")
    first = r["colors"][0]["thread"]
    assert f"Test Rayon {first['number']} {first['name']}" in text
    assert "check against the real thread card" in text

    # VP3 keeps brand + number (PES is written as version 1, which has no thread records)
    path = os.path.join(tempfile.mkdtemp(), "t.vp3")
    with open(path, "wb") as f:
        f.write(export_embroidery(plan, "vp3"))
    back = pe.read(path)
    assert [(t.catalog_number, t.brand) for t in back.threadlist] == [(c["thread"]["number"], "Test Rayon") for c in r["colors"]]


def test_without_a_chart_exports_are_unchanged(svg_text):
    pid = client.post("/api/projects", files={"file": ("a.svg", svg_text.encode())}).json()["id"]
    client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 80}})
    client.post(f"/api/projects/{pid}/stitch", json={"params": {}})
    plan = service.get_plan(pid)
    a = export_embroidery(plan, "pes")
    plan.thread_chart = "not-there"  # an unknown chart (e.g. removed from the folder) is ignored
    assert export_embroidery(plan, "pes") == a
    assert "Threads: nearest" not in worksheet(plan, "t")


def test_config_lists_the_charts(charts):
    assert client.get("/api/config").json()["thread_charts"] == threads.catalog()

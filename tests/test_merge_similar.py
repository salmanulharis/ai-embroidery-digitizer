"""Merge similar colours: CIEDE2000, the server's suggestions and applying several merges
as one undo step."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from embroidery_app import service
from embroidery_app.api import app
from embroidery_app.colors import ciede2000

client = TestClient(app)

# navy / navy2 and red / red2 look alike (CIEDE2000 ~4) but are more than the import's
# CIE76 3 apart, so they arrive as separate colours; yellow is far from everything.
SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">
  <rect x="5" y="5" width="60" height="60" fill="rgb(30,58,138)"/>
  <rect x="75" y="5" width="20" height="20" fill="rgb(38,70,152)"/>
  <circle cx="140" cy="35" r="30" fill="rgb(200,30,40)"/>
  <rect x="120" y="75" width="15" height="15" fill="rgb(214,44,52)"/>
  <rect x="160" y="75" width="30" height="20" fill="rgb(250,220,40)"/>
</svg>"""


def project():
    pid = client.post("/api/projects", files={"file": ("c.svg", SVG.encode())}).json()["id"]
    r = client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 100}})
    assert r.status_code == 200, r.text
    return pid, r.json()["design"]["layers"]


def by_rgb(layers, rgb):
    return next(l["index"] for l in layers if tuple(l["rgb"]) == rgb)


# Sharma, Wu & Dalal (2005), "The CIEDE2000 color-difference formula", test data
SHARMA = [
    ((50, 2.6772, -79.7751), (50, 0, -82.7485), 2.0425),
    ((50, -1.3802, -84.2814), (50, 0, -82.7485), 1.0000),
    ((50, 0, 0), (50, -1, 2), 2.3669),
    ((50, 2.49, -0.001), (50, -2.49, 0.0011), 7.2195),  # hue angle across 0 / 360
    ((50, 2.5, 0), (73, 25, -18), 27.1492),
    ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ((22.7233, 20.0904, -46.6940), (23.0331, 14.9730, -42.5619), 2.0373),
    ((2.0776, 0.0795, -1.1350), (0.9033, -0.0636, -0.5514), 0.9082),
]


def test_ciede2000_matches_published_values():
    a = np.array([s[0] for s in SHARMA], float)
    b = np.array([s[1] for s in SHARMA], float)
    want = np.array([s[2] for s in SHARMA])
    assert np.allclose(ciede2000(a, b), want, atol=1e-3)
    assert np.allclose(ciede2000(b, a), want, atol=1e-3)  # symmetric
    assert float(ciede2000(a[0], a[0])) == 0.0


def test_suggestions_pair_look_alikes_into_the_larger_colour():
    pid, layers = project()
    assert len(layers) == 5
    r = client.get(f"/api/projects/{pid}/layers/similar")
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["max_de"] == service.SIMILAR_DEFAULT
    got = {(p["source"], p["target"]) for p in res["pairs"]}
    navy, navy2 = by_rgb(layers, (30, 58, 138)), by_rgb(layers, (38, 70, 152))
    red, red2 = by_rgb(layers, (200, 30, 40)), by_rgb(layers, (214, 44, 52))
    assert got == {(navy2, navy), (red2, red)}  # the small one goes into the big one; yellow stays
    for p in res["pairs"]:
        assert 0 < p["delta_e"] < res["max_de"]
        assert p["source_mm2"] < p["target_mm2"]
    assert [p["delta_e"] for p in res["pairs"]] == sorted(p["delta_e"] for p in res["pairs"])


def test_threshold_is_clamped_and_controls_the_pairs():
    pid, _ = project()
    assert client.get(f"/api/projects/{pid}/layers/similar?max_de=1").json()["pairs"] == []
    wide = client.get(f"/api/projects/{pid}/layers/similar?max_de=999").json()
    assert wide["max_de"] == service.SIMILAR_RANGE[1]
    assert client.get(f"/api/projects/{pid}/layers/similar?max_de=0").json()["max_de"] == service.SIMILAR_RANGE[0]
    # every colour is merged at most once and never into a colour that is merged itself
    sources = [p["source"] for p in wide["pairs"]]
    assert len(sources) == len(set(sources))
    assert not {p["target"] for p in wide["pairs"]} & set(sources)


def test_switched_off_colours_are_not_suggested():
    pid, layers = project()
    red2 = by_rgb(layers, (214, 44, 52))
    body = [{"index": l["index"], "hex": l["hex"], "name": l["name"], "enabled": l["index"] != red2,
             "stitch_type": l["stitch_type"], "angle": l["angle"]} for l in layers]
    assert client.put(f"/api/projects/{pid}/layers", json={"layers": body}).status_code == 200
    pairs = client.get(f"/api/projects/{pid}/layers/similar").json()["pairs"]
    assert all(red2 not in (p["source"], p["target"]) for p in pairs)
    assert len(pairs) == 1


def test_merge_many_is_one_undo_step():
    pid, layers = project()
    pairs = client.get(f"/api/projects/{pid}/layers/similar").json()["pairs"]
    before = service.load_design(pid).to_dict()
    r = client.post(f"/api/projects/{pid}/layers/merge-many", json={"pairs": pairs})
    assert r.status_code == 200, r.text
    after = r.json()["design"]["layers"]
    assert len(after) == 3
    assert {tuple(l["rgb"]) for l in after} == {(30, 58, 138), (200, 30, 40), (250, 220, 40)}  # the main threads stay
    d = service.load_design(pid)
    assert {o.layer for o in d.objects} == {l["index"] for l in after}
    assert client.get(f"/api/projects/{pid}/layers/similar").json()["pairs"] == []
    r = client.post(f"/api/projects/{pid}/undo")
    assert r.status_code == 200 and len(r.json()["design"]["layers"]) == 5
    assert service.load_design(pid).to_dict() == before


def test_merge_many_same_as_single_merges():
    """One pair through merge-many gives exactly the design of the existing merge."""
    pid_a, layers = project()
    pid_b, _ = project()
    navy, navy2 = by_rgb(layers, (30, 58, 138)), by_rgb(layers, (38, 70, 152))
    client.post(f"/api/projects/{pid_a}/layers/merge", json={"source": navy2, "target": navy})
    client.post(f"/api/projects/{pid_b}/layers/merge-many", json={"pairs": [{"source": navy2, "target": navy}]})
    def no_ids(pid):  # shape ids are random per project
        d = service.load_design(pid).to_dict()
        for o in d["objects"]:
            o.pop("id")
        return d

    assert no_ids(pid_a) == no_ids(pid_b)


def test_merge_many_follows_chains_and_refuses_loops():
    pid, layers = project()
    a, b, c = (by_rgb(layers, rgb) for rgb in ((30, 58, 138), (38, 70, 152), (250, 220, 40)))
    # a -> b and b -> c: a ends in c; b -> a would close a loop and is dropped
    r = client.post(f"/api/projects/{pid}/layers/merge-many",
                    json={"pairs": [{"source": a, "target": b}, {"source": b, "target": c}, {"source": c, "target": a}]})
    assert r.status_code == 200, r.text
    left = {l["index"] for l in r.json()["design"]["layers"]}
    assert c in left and a not in left and b not in left
    assert len(left) == 3


@pytest.mark.parametrize("pairs, message", [
    ([], "at least one"),
    ([{"source": 0, "target": 0}], "two different"),
    ([{"source": 0, "target": 999}], "no longer in the design"),
])
def test_merge_many_rejects_bad_requests(pairs, message):
    pid, layers = project()
    if pairs and pairs[0]["source"] == 0:
        pairs = [{**p, "source": layers[0]["index"], "target": layers[0]["index"] if p["target"] == 0 else p["target"]} for p in pairs]
    r = client.post(f"/api/projects/{pid}/layers/merge-many", json={"pairs": pairs})
    assert r.status_code == 400, r.text
    assert message in r.json()["detail"]
    assert len(service.load_design(pid).layers) == 5  # nothing changed


def test_config_sends_the_threshold_range():
    s = client.get("/api/config").json()["similar"]
    assert (s["min"], s["max"], s["default"]) == (*service.SIMILAR_RANGE, service.SIMILAR_DEFAULT)

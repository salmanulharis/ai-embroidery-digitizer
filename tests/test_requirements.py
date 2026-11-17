"""The libraries that shape the stitches are capped in requirements.txt: every install (CI,
the server) gets the minor version the golden plans were made with. Without a cap a new
release changes the stitches silently (OpenCV 5.1, Nov 2026)."""

import re
from pathlib import Path

STITCH_SHAPING = ["numpy", "opencv-python-headless", "pillow", "shapely", "pystitch", "pymupdf"]


def test_stitch_shaping_libraries_are_capped():
    lines = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text(encoding="utf-8").splitlines()
    spec = {}
    for line in lines:
        line = line.split("#", 1)[0].strip()
        m = re.match(r"([A-Za-z0-9_.\-\[\]]+?)\s*([<>=!~].*)?$", line)
        if m and line:
            spec[m.group(1).split("[")[0].lower()] = m.group(2) or ""
    for name in STITCH_SHAPING:
        assert name in spec, f"{name} missing from requirements.txt"
        assert ">=" in spec[name] and "<" in spec[name].replace("<=", ""), f"{name} needs a minimum and an upper cap: {spec[name]!r}"

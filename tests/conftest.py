import os
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytest

# isolate project storage before the app is imported
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="emb-test-"))
for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
    os.environ[key] = ""
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def make_logo() -> np.ndarray:
    """White background, blue ring, red text, thin green bar (RGBA)."""
    img = np.full((600, 900, 3), 255, np.uint8)
    cv2.circle(img, (300, 300), 220, (200, 40, 30), -1)
    cv2.circle(img, (300, 300), 120, (255, 255, 255), -1)
    cv2.putText(img, "AB", (520, 380), cv2.FONT_HERSHEY_SIMPLEX, 6, (30, 30, 200), 28)
    cv2.line(img, (80, 560), (820, 560), (20, 140, 20), 30)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGBA)


@pytest.fixture
def logo_rgba():
    return make_logo()


@pytest.fixture
def logo_png(logo_rgba) -> bytes:
    ok, buf = cv2.imencode(".png", cv2.cvtColor(logo_rgba, cv2.COLOR_RGBA2BGRA))
    return buf.tobytes()


SVG_SAMPLE = """<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">
  <rect x="0" y="0" width="200" height="100" fill="#ffffff"/>
  <rect x="10" y="10" width="80" height="80" fill="#1144cc"/>
  <circle cx="90" cy="50" r="30" fill="#dd2222"/>
  <path d="M120 10 H190 V90 H120 Z M140 30 H170 V70 H140 Z" fill="#118833" fill-rule="evenodd"/>
  <g transform="translate(0,5)"><path d="M10 92 L190 92" stroke="#000000" stroke-width="3" fill="none"/></g>
  <text x="10" y="20">Hello</text>
</svg>"""


@pytest.fixture
def svg_text() -> str:
    return SVG_SAMPLE

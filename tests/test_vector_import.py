"""SVG import via PyMuPDF + CSS / gradient pre-pass."""

import math

import pytest

from embroidery_app.ingest.vector import VectorOptions, _parse_svg, _prepare_svg, svg_to_design

ILLUSTRATOR = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">
  <defs><style>.cls-1{fill:#1144cc;}.cls-2{fill:#dd2222;stroke:#000;stroke-width:2px;} rect.big{fill:#00aa00}
  /* comment */ @media print { .cls-1 { fill: #000 } }</style></defs>
  <rect class="cls-1" x="10" y="10" width="60" height="60"/>
  <circle class="cls-2" cx="130" cy="40" r="25"/>
  <rect class="big cls-1" x="10" y="80" width="180" height="10"/>
  <rect class="cls-1" style="fill:#ffaa00" x="120" y="75" width="20" height="20"/>
</svg>"""


def colours(shapes, kind="area"):
    return sorted({s.rgb for s in shapes if s.kind == kind})


def test_illustrator_css_classes_specificity_and_inline_style():
    shapes, warnings = _parse_svg(ILLUSTRATOR)
    assert colours(shapes) == sorted({(17, 68, 204), (221, 34, 34), (0, 170, 0), (255, 170, 0)})
    assert colours(shapes, "line") == [(0, 0, 0)]  # stroke from CSS
    assert not warnings


def test_gradients_become_their_average_colour_with_warning():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 100 50">
      <defs><linearGradient id="g"><stop offset="0" stop-color="#ff0000"/><stop offset="1" style="stop-color:#0000ff"/></linearGradient>
      <linearGradient id="h" xlink:href="#g"/></defs>
      <rect x="0" y="0" width="40" height="40" fill="url(#g)"/>
      <rect x="50" y="0" width="40" height="40" style="fill:url('#h')"/></svg>"""
    shapes, warnings = _parse_svg(svg)
    assert colours(shapes) == [(128, 0, 128)]
    assert any("gradients" in w for w in warnings)


def test_mm_sized_svg_with_transforms_scales_consistently():
    """Regression: the old parser scaled transformed and untransformed shapes differently."""
    svg = """<svg xmlns="http://www.w3.org/2000/svg" width="200mm" height="100mm" viewBox="0 0 200 100">
      <g transform="translate(10,10) rotate(10)"><rect width="60" height="40" fill="#1144cc"/></g>
      <path d="M120 10 A30 30 0 1 1 119.9 10 Z M120 25 A15 15 0 1 0 120.1 25 Z" fill-rule="evenodd" fill="#dd2222"/>
      <defs><circle id="dot" r="6"/></defs><use href="#dot" x="170" y="80" fill="#118833"/></svg>"""
    shapes, _ = _parse_svg(svg)
    area = {s.rgb: s.geom.area for s in shapes if s.kind == "area"}
    rect, ring, dot = area[(17, 68, 204)], area[(221, 34, 34)], area[(17, 136, 51)]
    assert ring / rect == pytest.approx((math.pi * (30**2 - 15**2)) / 2400, rel=0.03)
    assert dot / rect == pytest.approx((math.pi * 36) / 2400, rel=0.03)


def test_design_scaled_to_width_and_text_warning():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 100">
      <rect x="0" y="0" width="400" height="100" fill="#123456"/><text x="5" y="50">Hi</text></svg>"""
    d = svg_to_design(svg, VectorOptions(width_mm=80))
    assert d.width_mm == pytest.approx(80, abs=0.01) and d.height_mm == pytest.approx(20, abs=0.05)
    assert any("text" in w for w in d.warnings)


@pytest.mark.parametrize("bad, message", [
    ("<svg xmlns='http://www.w3.org/2000/svg'><rect", "not valid SVG"),
    ("<html><body/></html>", "not an SVG"),
])
def test_invalid_svg_is_rejected_clearly(bad, message):
    with pytest.raises(ValueError, match=message):
        _prepare_svg(bad)

"""Every setting, settings group, stitch style, foundation / texture choice and check has help text
(static/help.js), so the editor's help cannot quietly fall behind new features."""

import re
from pathlib import Path

from embroidery_app import service
from embroidery_app.models import STITCH_STYLES

STATIC = Path(__file__).resolve().parent.parent / "embroidery_app" / "static"
HELP_JS = (STATIC / "help.js").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
BUILTIN = (Path(service.__file__).parent / "ai" / "builtin.py").read_text(encoding="utf-8")
SERVICE = Path(service.__file__).read_text(encoding="utf-8")


def _block(src: str, start: str) -> str:
    """The text of a JS object or array literal that starts at ``start`` (up to where it closes)."""
    i = src.index(start)
    open_ = start.rstrip()[-1]
    close = {"{": "}", "[": "]"}[open_]
    depth = 0
    for j in range(src.index(open_, i), len(src)):
        depth += {open_: 1, close: -1}.get(src[j], 0)
        if depth == 0:
            return src[i:j + 1]
    raise AssertionError(start)


def _line(src: str, start: str) -> str:
    return src[src.index(start):].splitlines()[0]


HELP_KEYS = set(re.findall(r"^  ([A-Za-z_0-9]+): \{", _block(HELP_JS, "const HELP = {"), re.M))
OPTIONS = _block(HELP_JS, "const OPTION_HELP = {")


def _option_keys(table: str) -> set[str]:
    return set(re.findall(r'^    ([a-z_0-9]+): "', _block(OPTIONS, f"  {table}: {{"), re.M))


def test_every_stitch_setting_has_help():
    params = re.findall(r'^  \["([a-z_0-9]+)", "', _block(APP_JS, "const PARAMS = ["), re.M)
    checks = re.findall(r'^  \["([a-z_0-9]+)", "', _block(APP_JS, "const CHECKS = ["), re.M)
    selects = re.findall(r'\["([a-z_]+)", "(?:fill|satin)", "', _line(APP_JS, "const UNDERLAY_FIELDS"))
    selects += re.findall(r'UNDERLAY_FIELDS\.push\(\["([a-z_]+)"', APP_JS)
    assert len(params) > 15 and len(checks) > 8 and len(selects) >= 5
    missing = [k for k in params + checks + selects if k not in HELP_KEYS]
    assert not missing, missing


def test_every_settings_group_has_help():
    groups = re.findall(r'^  \["(adv_[a-z]+)", "', APP_JS, re.M)
    assert len(groups) >= 5
    sections = re.findall(r'data-help="(sec_[a-z]+)"', (STATIC / "index.html").read_text(encoding="utf-8"))
    assert len(sections) >= 6
    missing = [k for k in groups + sections + ["sec_advanced", "shape_fine", "info_group", "checks_group"] if k not in HELP_KEYS]
    assert not missing, missing


def test_every_stitch_style_choice_explains_itself():
    have = _option_keys("stitch_style")
    missing = [k for k, _ in STITCH_STYLES if k not in have] + [k for k in ("inherit", "skip") if k not in have]
    assert not missing, missing


def test_every_foundation_and_texture_choice_explains_itself():
    labels = APP_JS[APP_JS.index("const UNDERLAY_LABELS"):]
    for table, start in (("fill_underlay", "  fill: ["), ("satin_underlay", "  satin: [")):
        values = re.findall(r'\["([a-z_]+)", "', _block(labels, start))
        assert values and not [v for v in values if v not in _option_keys(table)], (table, values)
    texture = re.findall(r'\["([a-z]+)", "', _line(APP_JS, "UNDERLAY_LABELS.pattern = "))
    assert texture and set(texture) <= _option_keys("fill_pattern")


def test_every_check_has_help():
    codes = set(re.findall(r'code="([a-z_]+)"', BUILTIN)) | set(service._CHECK_ACTIONS) | {"hoop"}
    codes |= set(re.findall(r'codes\.append\(\("([a-z_]+)"', SERVICE))
    assert len(codes) > 20
    missing = sorted(c for c in codes if f"check_{c}" not in HELP_KEYS)
    assert not missing, missing

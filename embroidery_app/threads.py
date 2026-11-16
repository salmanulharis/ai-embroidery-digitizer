"""Thread charts: the nearest brand thread (Madeira, Isacord, Robison-Anton, ...) for each colour.

Charts are files in a folder outside the code (``THREAD_CHARTS_DIR``, default
``DATA_DIR/thread-charts``), so no brand data is bundled with the app: brands own their
colour charts. Put licensed charts there (from your supplier), or public ones you have
checked the licence of - e.g. Ink/Stitch's palettes (GPL-3) work as they are.

Formats (one chart per file, the file name gives the id):

* GIMP palette ``.gpl`` (Ink/Stitch's format)::

      GIMP Palette
      Name: Ink/Stitch: Madeira Rayon
      # RGB Value   Color Name Number
       40  41  42   Black   1000

  three numbers, the thread name, its number as the last word.
* ``.csv`` with a header: ``number``, ``name`` and either ``hex`` or ``r``, ``g``, ``b``
  (optional first line ``# Name: Brand Line`` for the chart name).

Matching uses CIEDE2000 (``colors.ciede2000``). A match is a suggestion: thread cards
differ from screens, so the worksheet says to check against the real card.
"""

from __future__ import annotations

import csv
import io
import re
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .colors import ciede2000, rgb_to_lab
from .config import env, settings
from .models import RGB, hex_to_rgb, rgb_to_hex

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_THREADS = 5000  # per chart
MAX_CHARTS = 60
_ID_RE = re.compile(r"[^a-z0-9]+")
_GPL_LINE = re.compile(r"^\s*(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})\s+(.*?)\s*$")


@dataclass(frozen=True)
class Thread:
    number: str
    name: str
    rgb: RGB


@dataclass
class Chart:
    id: str
    name: str
    threads: list[Thread]
    lab: np.ndarray  # (n, 3)

    def nearest(self, rgb: RGB) -> dict:
        d = ciede2000(rgb_to_lab(np.array(rgb, np.uint8))[None, :], self.lab)
        i = int(np.argmin(d))
        t = self.threads[i]
        return {"chart": self.id, "brand": self.name, "number": t.number, "name": t.name,
                "hex": rgb_to_hex(t.rgb), "delta_e": round(float(d[i]), 1)}


def charts_dir() -> Path:
    return Path(env("THREAD_CHARTS_DIR") or settings.data_dir / "thread-charts")


def chart_id(path: Path) -> str:
    return _ID_RE.sub("-", path.stem.lower()).strip("-")[:48] or "chart"


def _clean_name(name: str, fallback: str) -> str:
    name = name.strip()
    if name.lower().startswith("ink/stitch:"):  # "Ink/Stitch: Madeira Rayon" -> "Madeira Rayon"
        name = name.split(":", 1)[1].strip()
    return name[:60] or fallback


def parse_gpl(text: str, fallback_name: str) -> tuple[str, list[Thread]]:
    name, threads = fallback_name, []
    for line in text.splitlines():
        if line.startswith("Name:"):
            name = _clean_name(line[5:], fallback_name)
            continue
        if not line.strip() or line.lstrip().startswith("#") or line.startswith(("GIMP", "Columns:")):
            continue
        m = _GPL_LINE.match(line)
        if not m:
            continue
        r, g, b = (int(m.group(i)) for i in (1, 2, 3))
        if max(r, g, b) > 255:
            continue
        rest = m.group(4).split()
        if not rest:
            continue
        number = rest[-1] if any(c.isdigit() for c in rest[-1]) else ""
        label = " ".join(rest[:-1] if number else rest)
        threads.append(Thread(number[:20], label[:60], (r, g, b)))
    return name, threads


def parse_csv(text: str, fallback_name: str) -> tuple[str, list[Thread]]:
    name = fallback_name
    lines = text.splitlines()
    if lines and lines[0].startswith("#"):
        head = lines.pop(0).lstrip("#").strip()
        if head.lower().startswith("name:"):
            name = _clean_name(head[5:], fallback_name)
    threads = []
    for row in csv.DictReader(io.StringIO("\n".join(lines))):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        try:
            if row.get("hex"):
                rgb = hex_to_rgb(row["hex"] if row["hex"].startswith("#") else "#" + row["hex"])
            else:
                rgb = tuple(int(row[c]) for c in ("r", "g", "b"))
                if not all(0 <= v <= 255 for v in rgb):
                    continue
        except (KeyError, ValueError, TypeError):
            continue
        threads.append(Thread(row.get("number", "")[:20], row.get("name", "")[:60], rgb))  # type: ignore[arg-type]
    return name, threads


def load_chart(path: Path) -> Chart | None:
    """One chart file, or None when it is not a usable chart."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fallback = path.stem.replace("_", " ").strip()[:60]
    parse = parse_gpl if path.suffix.lower() == ".gpl" else parse_csv
    name, threads = parse(text, fallback)
    threads = threads[:MAX_THREADS]
    if not threads:
        return None
    lab = rgb_to_lab(np.array([t.rgb for t in threads], np.uint8))
    return Chart(chart_id(path), name, threads, lab)


_lock = threading.Lock()
_cache: tuple[tuple, dict[str, Chart]] | None = None


def charts() -> dict[str, Chart]:
    """All charts in the folder, read again only when its files change."""
    global _cache
    folder = charts_dir()
    try:
        files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in (".gpl", ".csv"))[:MAX_CHARTS]
        stamp = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in files)
    except OSError:
        files, stamp = [], ()
    stamp = (str(folder), stamp)
    with _lock:
        if _cache is not None and _cache[0] == stamp:
            return _cache[1]
    found: dict[str, Chart] = {}
    for p in files:
        c = load_chart(p)
        if c is not None and c.id not in found:
            found[c.id] = c
    with _lock:
        _cache = (stamp, found)
    return found


def catalog() -> list[dict]:
    """For the editor: the charts to choose from."""
    return sorted(({"id": c.id, "name": c.name, "count": len(c.threads)} for c in charts().values()),
                  key=lambda c: c["name"].lower())


def closeness(delta_e: float) -> str:
    """How close a match is, in words (CIEDE2000)."""
    return "same colour" if delta_e < 2 else "very close" if delta_e < 3.5 else "close" if delta_e < 6 else "nearest available"


def matches(chart: str, colours: list[RGB]) -> list[dict] | None:
    """The nearest thread of ``chart`` for each colour; None when no (known) chart is chosen."""
    c = charts().get(chart) if chart else None
    return [c.nearest(rgb) for rgb in colours] if c else None

"""Application settings, read from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(_env("DATA_DIR", str(ROOT / "data")))
    max_upload_mb: int = int(_env("MAX_UPLOAD_MB", "40"))
    # Working resolution for raster tracing (pixels per mm of final design)
    raster_px_per_mm: float = float(_env("RASTER_PX_PER_MM", "12"))
    raster_max_side_px: int = int(_env("RASTER_MAX_SIDE_PX", "2000"))

    # --- Optional AI (the app works fully without it) ---
    # Each provider reads its own variables (e.g. OPENAI_API_KEY, GEMINI_VISION_MODEL) via
    # ``env()`` - see embroidery_app/ai/providers/. Users can also bring their own keys
    # from the browser; those are used per request and never stored on the server.
    ai_timeout_s: float = float(_env("AI_TIMEOUT_S", "120"))
    # Allow AI endpoints on private/loopback addresses (needed for a local Ollama or
    # LM Studio). Set to 0 on public deployments so users cannot make the server call
    # internal addresses.
    ai_allow_private_endpoints: bool = _env("AI_ALLOW_PRIVATE_ENDPOINTS", "1").lower() in ("1", "true", "yes")


def env(name: str, default: str = "") -> str:
    """Read an optional setting (used by AI providers for their own variables)."""
    return _env(name, default)


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)

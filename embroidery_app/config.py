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

    # --- Optional AI providers (all optional; app works fully without them) ---
    openai_api_key: str = _env("OPENAI_API_KEY")
    openai_image_model: str = _env("OPENAI_IMAGE_MODEL", "gpt-image-1")
    openai_vision_model: str = _env("OPENAI_VISION_MODEL", "gpt-4o-mini")

    gemini_api_key: str = _env("GEMINI_API_KEY")
    gemini_image_model: str = _env("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
    gemini_vision_model: str = _env("GEMINI_VISION_MODEL", "gemini-2.5-flash")

    openrouter_api_key: str = _env("OPENROUTER_API_KEY")
    openrouter_image_model: str = _env("OPENROUTER_IMAGE_MODEL", "google/gemini-2.5-flash-image")
    openrouter_vision_model: str = _env("OPENROUTER_VISION_MODEL", "google/gemini-2.5-flash")

    ollama_url: str = _env("OLLAMA_URL", "http://localhost:11434")
    ollama_vision_model: str = _env("OLLAMA_VISION_MODEL", "qwen2.5vl:7b")

    ai_timeout_s: float = float(_env("AI_TIMEOUT_S", "120"))


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)

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
    # Model for the optional rembg photo cut-out. Pinned on purpose: rembg's own default
    # (bria-rmbg / RMBG-2.0) needs a paid BRIA agreement for commercial use.
    rembg_model: str = _env("REMBG_MODEL", "isnet-general-use")

    # --- Accounts (see auth.py) ---
    # The first Super Admin, created at startup when no account has this email yet. Set as
    # GitHub repository secrets for the deployment, never in the code.
    super_admin_email: str = _env("SUPER_ADMIN_EMAIL").lower()
    super_admin_password: str = os.environ.get("SUPER_ADMIN_PASSWORD", "")
    # "Remember me" keeps a login for this many days; without it the login ends with the
    # browser session (and after at most one day on the server).
    session_days: int = int(_env("SESSION_DAYS", "30"))
    # Encrypts the users' AI keys in the database. Without it a random key is created once
    # in DATA_DIR/secret.key; setting it keeps the keys readable only with the secret.
    app_secret_key: str = _env("APP_SECRET_KEY")
    # Session cookie only over HTTPS: "auto" = when the request came over HTTPS.
    cookie_secure: str = _env("COOKIE_SECURE", "auto").lower()

    # --- Optional AI (the app works fully without it) ---
    # Each provider reads its own variables (e.g. OPENAI_API_KEY, GEMINI_VISION_MODEL) via
    # ``env()`` - see embroidery_app/ai/providers/. Each user's own keys are stored
    # encrypted per account (user_ai.py) and used only for that user's requests.
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

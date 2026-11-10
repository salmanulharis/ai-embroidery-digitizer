"""Building blocks shared by every AI provider.

A provider only has to implement three things - ``list_models``, ``edit_image`` and
``vision_json`` - and register itself (see ``registry.py``). Everything else
(credentials, retries, safe endpoints, secret redaction, model ranking) lives here.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import re
import socket
import time
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import urlparse

import httpx2

from ..config import env, settings

# Capabilities a model can have
EDIT = "edit"  # image in -> image out (clean-up, background removal, enhancement)
VISION = "vision"  # image(s) in -> text/JSON out (suggestions, stitch review)

MAX_IMAGE_BYTES = 30 * 1024 * 1024


class AIError(RuntimeError):
    """A provider call failed. Messages never contain the API key."""


@dataclass
class Credentials:
    """What the user (or the server .env) supplies for one provider. Used for a single
    request - never written to disk or logged."""

    api_key: str = ""
    base_url: str = ""
    edit_model: str = ""
    vision_model: str = ""
    user_base_url: bool = False  # True when the base URL came from the user (safety check applies)

    @classmethod
    def from_request(cls, data: Optional[dict]) -> "Credentials":
        data = data or {}

        def clean(key: str, limit: int) -> str:
            value = data.get(key) or ""
            return str(value).strip()[:limit] if isinstance(value, (str, int, float)) else ""

        base_url = clean("base_url", 300)
        return cls(
            api_key=clean("api_key", 500),
            base_url=base_url,
            edit_model=clean("edit_model", 200),
            vision_model=clean("vision_model", 200),
            user_base_url=bool(base_url),
        )

    def redact(self, text: str) -> str:
        if self.api_key and len(self.api_key) >= 6:
            text = text.replace(self.api_key, "***")
        return re.sub(r"(sk|key|AIza)[-_A-Za-z0-9]{12,}", "***", text)


@dataclass
class ModelInfo:
    id: str
    label: str = ""
    capabilities: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label or self.id, "capabilities": list(self.capabilities)}


class Provider:
    """Base class. Subclasses set the class attributes and implement the three methods."""

    id: str = ""
    label: str = ""
    capabilities: tuple[str, ...] = ()
    needs_key: bool = True
    supports_base_url: bool = False
    default_base_url: str = ""
    key_help_url: str = ""
    help: str = ""
    env_prefix: str = ""  # e.g. "OPENAI" -> OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_IMAGE_MODEL, OPENAI_VISION_MODEL
    # Fallback models used only when the provider's model list cannot be fetched.
    fallback_models: dict[str, str] = {}

    # ------------------------------------------------------------------ to implement
    def list_models(self, creds: Credentials) -> list[ModelInfo]:
        raise NotImplementedError

    def edit_image(self, creds: Credentials, model: str, png: bytes, prompt: str) -> bytes:
        raise AIError(f"{self.label} cannot edit pictures.")

    def vision_json(self, creds: Credentials, model: str, images: list[bytes], prompt: str) -> dict:
        raise AIError(f"{self.label} cannot look at pictures.")

    # ------------------------------------------------------------------ model choice
    def rank(self, capability: str, model_id: str) -> float:
        """Higher = better default for ``capability``. Override per provider."""
        return 0.0

    # ------------------------------------------------------------------ credentials
    def server_credentials(self) -> Credentials:
        p = self.env_prefix
        return Credentials(
            api_key=env(f"{p}_API_KEY"),
            base_url=env(f"{p}_BASE_URL") or (env("OLLAMA_URL") if p == "OLLAMA" else ""),
            edit_model=env(f"{p}_IMAGE_MODEL"),
            vision_model=env(f"{p}_VISION_MODEL"),
        )

    def server_configured(self) -> bool:
        c = self.server_credentials()
        return bool(c.api_key) if self.needs_key else bool(c.base_url or self.default_base_url) and self._reachable(c)

    def _reachable(self, creds: Credentials) -> bool:
        return True

    def resolve(self, user: Optional[Credentials]) -> Credentials:
        """User-supplied values win; the server's .env fills the gaps."""
        server = self.server_credentials()
        user = user or Credentials()
        creds = Credentials(
            api_key=user.api_key or server.api_key,
            base_url=(user.base_url or server.base_url or self.default_base_url).rstrip("/"),
            edit_model=user.edit_model or server.edit_model,
            vision_model=user.vision_model or server.vision_model,
            user_base_url=user.user_base_url,
        )
        if self.needs_key and not creds.api_key:
            raise AIError(f"Add your {self.label} API key in AI settings first.")
        if not creds.base_url:
            raise AIError(f"Enter the {self.label} address in AI settings first.")
        if creds.user_base_url:
            check_endpoint(creds.base_url)
        return creds

    def info(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "capabilities": list(self.capabilities),
            "needs_key": self.needs_key,
            "supports_base_url": self.supports_base_url,
            "default_base_url": self.default_base_url,
            "key_help_url": self.key_help_url,
            "help": self.help,
            "server_configured": self.server_configured(),
        }


# --------------------------------------------------------------------------- HTTP helpers

def check_endpoint(url: str) -> None:
    """Reject non-http(s) URLs and - unless allowed - private/internal addresses, so a
    public deployment cannot be used to reach internal services (SSRF)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise AIError("The AI address must start with http:// or https://")
    if settings.ai_allow_private_endpoints:
        return
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise AIError("The AI address could not be found.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise AIError("This server does not allow AI addresses on private or local networks.")


def request(
    creds: Credentials,
    method: str,
    url: str,
    *,
    retries: int = 1,
    timeout: Optional[float] = None,
    **kwargs,
) -> httpx2.Response:
    """HTTP call with one retry on rate limits / server errors and redacted errors."""
    last: Optional[str] = None
    for attempt in range(retries + 1):
        try:
            with httpx2.Client(timeout=timeout or settings.ai_timeout_s, follow_redirects=False) as client:
                resp = client.request(method, url, **kwargs)
        except httpx2.TimeoutException:
            last = "The AI service took too long to answer."
        except httpx2.HTTPError as exc:
            last = f"Could not reach the AI service ({type(exc).__name__})."
        else:
            if resp.status_code < 400:
                return resp
            if resp.status_code in (401, 403):
                raise AIError("The API key was rejected - check it in AI settings.")
            if resp.status_code == 404:
                raise AIError(creds.redact(f"Not found - check the model name. {_error_text(resp)}"))
            last = creds.redact(f"{resp.status_code}: {_error_text(resp)}")
            if resp.status_code not in (408, 409, 429, 500, 502, 503, 504):
                break
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise AIError(last or "The AI service failed.")


def _error_text(resp: httpx2.Response) -> str:
    try:
        data = resp.json()
        err = data.get("error", data) if isinstance(data, dict) else data
        msg = err.get("message") if isinstance(err, dict) else str(err)
        return str(msg or "")[:300]
    except Exception:
        return resp.text[:300]


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def decode_image(data: str | bytes) -> bytes:
    raw = base64.b64decode(data) if isinstance(data, str) else data
    if len(raw) > MAX_IMAGE_BYTES:
        raise AIError("The AI returned an image that is too large.")
    return raw


def parse_json(text: str) -> dict:
    """First JSON object in a model answer (tolerates ```json fences and chatter)."""
    if not text:
        raise AIError("The AI returned an empty answer.")
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise AIError("The AI answer was not in the expected format.")
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise AIError("The AI answer was not in the expected format.") from exc
    if not isinstance(value, dict):
        raise AIError("The AI answer was not in the expected format.")
    return value


def version_key(model_id: str) -> tuple:
    """Sort key: higher version numbers first (e.g. gemini-3.6 > gemini-3.5 > 2.5)."""
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", model_id)[:3]]
    return tuple(nums) + (0,) * (3 - len(nums))


def best(models: Iterable[ModelInfo], capability: str, rank) -> Optional[str]:
    candidates = [m for m in models if capability in m.capabilities]
    if not candidates:
        return None
    return max(candidates, key=lambda m: (rank(capability, m.id), version_key(m.id))).id

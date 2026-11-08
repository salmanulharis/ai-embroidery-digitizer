"""Optional AI assistance. The core pipeline never depends on it.

Two capabilities, used only where AI genuinely beats classic image processing:

* ``clean_image`` - an image-generation model redraws a messy input (photo of a
  shirt, low-res/blurred logo, scanned sketch) as flat, solid-colour artwork. The
  result is shown to the user for approval because generative models can alter
  details/text. Supported: OpenAI, Gemini, OpenRouter (Ollama has no image output).
* ``analyze`` - a vision LLM suggests colour count / background / notes. Supported:
  all four providers (Ollama with a local vision model such as qwen2.5vl).

All calls are plain REST via httpx so no vendor SDKs are required. Model names
come from settings and can be changed via environment variables.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Optional

import httpx

from ..config import settings

CLEAN_PROMPT = (
    "Redraw this logo/design as clean, flat artwork ready for machine embroidery. "
    "Rules: keep the exact same shapes, text, layout and proportions; use only solid flat colours "
    "(at most {colors} colours); remove photo background, fabric texture, shadows, gradients, "
    "glare, noise and outlines that are thinner than the rest; place the design on a plain pure white background; "
    "make thin lines bold enough to sew; do not add any new elements or text."
)

ANALYZE_PROMPT = (
    "You are an expert embroidery digitizer. Look at this artwork and reply with JSON only: "
    '{"design_type": "logo|text|illustration|photo", "suggested_colors": <int 1-15>, '
    '"background_hex": "#rrggbb or null", "has_small_text": <bool>, "has_gradients": <bool>, '
    '"needs_ai_cleanup": <bool>, "notes": "<one or two short sentences of digitizing advice>"}'
)


class AIError(RuntimeError):
    pass


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise AIError("Model did not return JSON")
    return json.loads(match.group(0))


def _client() -> httpx.Client:
    return httpx.Client(timeout=settings.ai_timeout_s)


def _raise(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        raise AIError(f"{resp.request.url.host} returned {resp.status_code}: {resp.text[:300]}")


class Provider:
    name = "base"
    can_clean = False
    can_analyze = False

    def available(self) -> bool:
        return False

    def clean_image(self, png: bytes, colors: int = 6) -> bytes:
        raise AIError(f"{self.name} cannot generate images")

    def analyze(self, png: bytes) -> dict:
        raise AIError(f"{self.name} cannot analyse images")

    def info(self) -> dict:
        return {"name": self.name, "available": self.available(), "clean": self.can_clean, "analyze": self.can_analyze}


class OpenAIProvider(Provider):
    name, can_clean, can_analyze = "openai", True, True

    def available(self) -> bool:
        return bool(settings.openai_api_key)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {settings.openai_api_key}"}

    def clean_image(self, png: bytes, colors: int = 6) -> bytes:
        with _client() as c:
            r = c.post(
                "https://api.openai.com/v1/images/edits",
                headers=self._headers(),
                data={"model": settings.openai_image_model, "prompt": CLEAN_PROMPT.format(colors=colors), "n": "1"},
                files={"image": ("design.png", png, "image/png")},
            )
        _raise(r)
        item = r.json()["data"][0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        return httpx.get(item["url"], timeout=60).content

    def analyze(self, png: bytes) -> dict:
        body = {
            "model": settings.openai_vision_model,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": ANALYZE_PROMPT},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + _b64(png)}},
            ]}],
        }
        with _client() as c:
            r = c.post("https://api.openai.com/v1/chat/completions", headers=self._headers(), json=body)
        _raise(r)
        return _parse_json(r.json()["choices"][0]["message"]["content"])


class GeminiProvider(Provider):
    name, can_clean, can_analyze = "gemini", True, True
    base = "https://generativelanguage.googleapis.com/v1beta/models"

    def available(self) -> bool:
        return bool(settings.gemini_api_key)

    def _call(self, model: str, prompt: str, png: bytes, json_out: bool = False) -> dict:
        body: dict = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/png", "data": _b64(png)}}]}]}
        if json_out:
            body["generationConfig"] = {"responseMimeType": "application/json"}
        with _client() as c:
            r = c.post(f"{self.base}/{model}:generateContent", headers={"x-goog-api-key": settings.gemini_api_key}, json=body)
        _raise(r)
        return r.json()

    def clean_image(self, png: bytes, colors: int = 6) -> bytes:
        data = self._call(settings.gemini_image_model, CLEAN_PROMPT.format(colors=colors), png)
        for part in data.get("candidates", [{}])[0].get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
        raise AIError("Gemini returned no image")

    def analyze(self, png: bytes) -> dict:
        data = self._call(settings.gemini_vision_model, ANALYZE_PROMPT, png, json_out=True)
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        return _parse_json("".join(p.get("text", "") for p in parts))


class OpenRouterProvider(Provider):
    name, can_clean, can_analyze = "openrouter", True, True
    url = "https://openrouter.ai/api/v1/chat/completions"

    def available(self) -> bool:
        return bool(settings.openrouter_api_key)

    def _post(self, body: dict) -> dict:
        with _client() as c:
            r = c.post(self.url, headers={"Authorization": f"Bearer {settings.openrouter_api_key}"}, json=body)
        _raise(r)
        return r.json()

    def _messages(self, prompt: str, png: bytes) -> list:
        return [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + _b64(png)}},
        ]}]

    def clean_image(self, png: bytes, colors: int = 6) -> bytes:
        data = self._post({
            "model": settings.openrouter_image_model,
            "modalities": ["image", "text"],
            "messages": self._messages(CLEAN_PROMPT.format(colors=colors), png),
        })
        msg = data["choices"][0]["message"]
        for img in msg.get("images", []) or []:
            url = img.get("image_url", {}).get("url", "")
            if url.startswith("data:"):
                return base64.b64decode(url.split(",", 1)[1])
        raise AIError("OpenRouter model returned no image (choose an image-output model)")

    def analyze(self, png: bytes) -> dict:
        data = self._post({"model": settings.openrouter_vision_model, "messages": self._messages(ANALYZE_PROMPT, png)})
        return _parse_json(data["choices"][0]["message"]["content"])


class OllamaProvider(Provider):
    name, can_clean, can_analyze = "ollama", False, True

    def available(self) -> bool:
        try:
            r = httpx.get(f"{settings.ollama_url}/api/tags", timeout=1.5)
            names = {m.get("name", "") for m in r.json().get("models", [])}
            want = settings.ollama_vision_model
            return any(n == want or n.split(":")[0] == want.split(":")[0] for n in names)
        except Exception:
            return False

    def analyze(self, png: bytes) -> dict:
        body = {
            "model": settings.ollama_vision_model,
            "stream": False,
            "format": "json",
            "messages": [{"role": "user", "content": ANALYZE_PROMPT, "images": [_b64(png)]}],
        }
        with _client() as c:
            r = c.post(f"{settings.ollama_url}/api/chat", json=body)
        _raise(r)
        return _parse_json(r.json()["message"]["content"])


PROVIDERS: dict[str, Provider] = {p.name: p for p in (OpenAIProvider(), GeminiProvider(), OpenRouterProvider(), OllamaProvider())}


def get_provider(name: str) -> Provider:
    if name not in PROVIDERS:
        raise AIError(f"Unknown AI provider '{name}'")
    provider = PROVIDERS[name]
    if not provider.available():
        raise AIError(f"Provider '{name}' is not configured (see .env.example)")
    return provider


def provider_status() -> list[dict]:
    return [p.info() for p in PROVIDERS.values()]


def first_available(capability: str) -> Optional[Provider]:
    for p in PROVIDERS.values():
        if getattr(p, f"can_{capability}") and p.available():
            return p
    return None

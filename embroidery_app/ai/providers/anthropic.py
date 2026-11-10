"""Anthropic Claude (design advice and stitch review; Claude does not create pictures)."""

from __future__ import annotations

from ..base import VISION, AIError, Credentials, ModelInfo, Provider, b64, parse_json, request
from ..registry import register

API_VERSION = "2023-06-01"


@register
class AnthropicProvider(Provider):
    id = "anthropic"
    label = "Anthropic Claude"
    capabilities = (VISION,)
    default_base_url = "https://api.anthropic.com/v1"
    key_help_url = "https://console.anthropic.com/settings/keys"
    help = "Design advice and stitch review. Cannot redraw pictures - pair it with another service for clean-up."
    env_prefix = "ANTHROPIC"
    fallback_models = {VISION: "claude-sonnet-5-5"}

    def _headers(self, creds: Credentials) -> dict:
        return {"x-api-key": creds.api_key, "anthropic-version": API_VERSION}

    def list_models(self, creds: Credentials) -> list[ModelInfo]:
        data = request(creds, "GET", f"{creds.base_url}/models", headers=self._headers(creds), params={"limit": "100"}).json()
        return [
            ModelInfo(str(m["id"]), m.get("display_name") or str(m["id"]), (VISION,))
            for m in data.get("data", []) if m.get("id")
        ]

    def rank(self, capability: str, model_id: str) -> float:
        # Sonnet: best balance of picture understanding, speed and price for this job
        return 2.0 if "sonnet" in model_id else 1.0 if "haiku" in model_id else 0.5 if "opus" in model_id else 0.0

    def vision_json(self, creds, model, images, prompt):
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64(p)}} for p in images
        ] + [{"type": "text", "text": prompt}]
        body = {"model": model, "max_tokens": 2000, "messages": [{"role": "user", "content": content}]}
        data = request(creds, "POST", f"{creds.base_url}/messages", headers=self._headers(creds), json=body).json()
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        if not text:
            raise AIError("Claude returned an empty answer.")
        return parse_json(text)

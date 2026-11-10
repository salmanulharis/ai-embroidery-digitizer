"""Any OpenAI-compatible service at a custom address (LM Studio, vLLM, Groq, Together,
DeepSeek, Azure-style gateways, ...). The easiest way to add a new provider without code."""

from __future__ import annotations

from ..base import EDIT, VISION, Credentials, ModelInfo, Provider, request
from ..registry import register
from .openai import bearer, chat_vision, images_edit


@register
class CustomOpenAICompatible(Provider):
    id = "custom"
    label = "OpenAI-compatible (custom address)"
    capabilities = (EDIT, VISION)
    needs_key = False  # many local servers need none; sent when given
    supports_base_url = True
    default_base_url = ""
    key_help_url = ""
    help = "Any service that speaks the OpenAI API - enter its address, e.g. http://localhost:1234/v1 for LM Studio."
    env_prefix = "CUSTOM_AI"

    def list_models(self, creds: Credentials) -> list[ModelInfo]:
        data = request(creds, "GET", f"{creds.base_url}/models", headers=bearer(creds), timeout=15).json()
        out = []
        for m in data.get("data", []):
            mid = str(m.get("id", ""))
            if not mid:
                continue
            # capabilities are not reported by the OpenAI API format: offer every model,
            # image-named ones for clean-up as well
            caps = (EDIT, VISION) if "image" in mid else (VISION,)
            out.append(ModelInfo(mid, mid, caps))
        return out

    def edit_image(self, creds, model, png, prompt):
        return images_edit(creds, f"{creds.base_url}/images/edits", model, png, prompt, bearer(creds))

    def vision_json(self, creds, model, images, prompt):
        return chat_vision(creds, f"{creds.base_url}/chat/completions", model, images, prompt, bearer(creds), json_mode=False)

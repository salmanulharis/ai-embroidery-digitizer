"""OpenRouter: one key for hundreds of models (Gemini, GPT, Claude, Qwen, ...)."""

from __future__ import annotations

from ..base import EDIT, VISION, AIError, Credentials, ModelInfo, Provider, decode_image, request
from ..registry import register
from .openai import bearer, chat_vision, data_url


@register
class OpenRouterProvider(Provider):
    id = "openrouter"
    label = "OpenRouter"
    capabilities = (EDIT, VISION)
    default_base_url = "https://openrouter.ai/api/v1"
    key_help_url = "https://openrouter.ai/keys"
    help = "One key for many AI models from different companies."
    env_prefix = "OPENROUTER"

    def _headers(self, creds: Credentials) -> dict:
        return {**bearer(creds), "X-Title": "Embroidery Digitizer"}

    def list_models(self, creds: Credentials) -> list[ModelInfo]:
        # output_modalities=all: without it only text-output models are listed
        data = request(creds, "GET", f"{creds.base_url}/models", headers=self._headers(creds),
                       params={"output_modalities": "all"}).json()
        out = []
        for m in data.get("data", []):
            arch = m.get("architecture") or {}
            ins, outs = set(arch.get("input_modalities") or []), set(arch.get("output_modalities") or [])
            caps = []
            if "image" in ins and "image" in outs:
                caps.append(EDIT)
            if "image" in ins and "text" in outs:
                caps.append(VISION)
            if caps:
                out.append(ModelInfo(str(m.get("id")), m.get("name") or str(m.get("id")), tuple(caps)))
        return out

    def rank(self, capability: str, model_id: str) -> float:
        score = -2.0 if "preview" in model_id else 0.0
        if capability == EDIT:
            score += 2 if ("gemini" in model_id and "image" in model_id) else 1 if "gpt-image" in model_id else 0
        else:
            score += 2 if ("gemini" in model_id and "flash" in model_id) else 1 if "claude" in model_id else 0
        return score

    def edit_image(self, creds, model, png, prompt):
        body = {
            "model": model,
            "modalities": ["image", "text"],
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url(png)}},
            ]}],
        }
        resp = request(creds, "POST", f"{creds.base_url}/chat/completions", headers=self._headers(creds), json=body)
        try:
            msg = resp.json()["choices"][0]["message"]
        except (KeyError, IndexError, ValueError) as exc:
            raise AIError("The AI did not return a picture.") from exc
        for img in msg.get("images") or []:
            url = (img.get("image_url") or {}).get("url", "")
            if url.startswith("data:") and "," in url:
                return decode_image(url.split(",", 1)[1])
        raise AIError("This OpenRouter model did not return a picture - choose a model that can create images.")

    def vision_json(self, creds, model, images, prompt):
        # not every routed model supports response_format, so JSON is requested in the prompt
        return chat_vision(creds, f"{creds.base_url}/chat/completions", model, images, prompt,
                           self._headers(creds), json_mode=False)

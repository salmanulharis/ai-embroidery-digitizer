"""Google Gemini (AI Studio API key)."""

from __future__ import annotations

from ..base import EDIT, VISION, AIError, Credentials, ModelInfo, Provider, b64, decode_image, parse_json, request
from ..registry import register

_NOT_VISION = ("embedding", "tts", "live", "audio", "aqa", "robotics", "computer-use", "image")


@register
class GeminiProvider(Provider):
    id = "gemini"
    label = "Google Gemini"
    capabilities = (EDIT, VISION)
    default_base_url = "https://generativelanguage.googleapis.com/v1beta"
    key_help_url = "https://aistudio.google.com/app/apikey"
    help = "Picture clean-up (Gemini image models) and design advice. Has a free tier."
    env_prefix = "GEMINI"
    fallback_models = {EDIT: "gemini-3.1-flash-image", VISION: "gemini-3.5-flash"}

    def _headers(self, creds: Credentials) -> dict:
        return {"x-goog-api-key": creds.api_key}

    def list_models(self, creds: Credentials) -> list[ModelInfo]:
        out, token = [], ""
        for _ in range(5):  # pagination
            params = {"pageSize": "1000", **({"pageToken": token} if token else {})}
            data = request(creds, "GET", f"{creds.base_url}/models", headers=self._headers(creds), params=params).json()
            for m in data.get("models", []):
                if "generateContent" not in (m.get("supportedGenerationMethods") or []):
                    continue
                mid = str(m.get("name", "")).split("/", 1)[-1]
                caps = []
                if "image" in mid and not mid.startswith("imagen"):
                    caps.append(EDIT)
                if mid.startswith("gemini") and not any(x in mid for x in _NOT_VISION):
                    caps.append(VISION)
                if caps:
                    out.append(ModelInfo(mid, m.get("displayName") or mid, tuple(caps)))
            token = data.get("nextPageToken") or ""
            if not token:
                break
        return out

    def rank(self, capability: str, model_id: str) -> float:
        score = 0.0
        if "preview" in model_id or "-exp" in model_id:
            score -= 2  # previews get shut down; prefer stable models
        if capability == EDIT and "flash-image" in model_id:
            score += 1
        if capability == VISION and "flash" in model_id and "lite" not in model_id:
            score += 1
        return score

    def _generate(self, creds, model, parts, config) -> list[dict]:
        body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": config}
        resp = request(creds, "POST", f"{creds.base_url}/models/{model}:generateContent",
                       headers=self._headers(creds), json=body)
        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason")
            raise AIError(f"Gemini returned no answer{f' ({reason})' if reason else ''}.")
        return (candidates[0].get("content") or {}).get("parts") or []

    def edit_image(self, creds, model, png, prompt):
        parts = [{"text": prompt}, {"inline_data": {"mime_type": "image/png", "data": b64(png)}}]
        for part in self._generate(creds, model, parts, {"responseModalities": ["TEXT", "IMAGE"]}):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return decode_image(inline["data"])
        raise AIError("Gemini did not return a picture - try another model.")

    def vision_json(self, creds, model, images, prompt):
        parts = [{"text": prompt}] + [{"inline_data": {"mime_type": "image/png", "data": b64(p)}} for p in images]
        out = self._generate(creds, model, parts, {"responseMimeType": "application/json"})
        return parse_json("".join(p.get("text", "") for p in out))

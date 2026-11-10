"""OpenAI, plus the OpenAI-style helpers reused by OpenRouter and custom endpoints."""

from __future__ import annotations

import re

from ..base import EDIT, VISION, AIError, Credentials, ModelInfo, Provider, b64, decode_image, parse_json, request
from ..registry import register

_NOT_CHAT = ("audio", "realtime", "transcribe", "tts", "search", "image", "embedding", "moderation", "instruct", "codex")
_DATED = re.compile(r"\d{4}-\d{2}-\d{2}")


def bearer(creds: Credentials) -> dict:
    return {"Authorization": f"Bearer {creds.api_key}"} if creds.api_key else {}


def data_url(png: bytes) -> str:
    return "data:image/png;base64," + b64(png)


def chat_vision(creds: Credentials, url: str, model: str, images: list[bytes], prompt: str,
                headers: dict, json_mode: bool = True, extra: dict | None = None) -> dict:
    """OpenAI-style chat completion with images; returns the parsed JSON answer."""
    content = [{"type": "text", "text": prompt}] + [{"type": "image_url", "image_url": {"url": data_url(p)}} for p in images]
    body = {"model": model, "messages": [{"role": "user", "content": content}], **(extra or {})}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    resp = request(creds, "POST", url, headers=headers, json=body)
    try:
        msg = resp.json()["choices"][0]["message"]
    except (KeyError, IndexError, ValueError) as exc:
        raise AIError("The AI answer was not in the expected format.") from exc
    text = msg.get("content")
    if isinstance(text, list):  # some providers return content parts
        text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
    return parse_json(text or "")


def images_edit(creds: Credentials, url: str, model: str, png: bytes, prompt: str, headers: dict) -> bytes:
    """OpenAI-style /images/edits."""
    resp = request(
        creds, "POST", url, headers=headers,
        data={"model": model, "prompt": prompt, "n": "1"},
        files={"image": ("design.png", png, "image/png")},
    )
    try:
        item = resp.json()["data"][0]
    except (KeyError, IndexError, ValueError) as exc:
        raise AIError("The AI did not return a picture.") from exc
    if item.get("b64_json"):
        return decode_image(item["b64_json"])
    if item.get("url", "").startswith("https://"):
        return decode_image(request(creds, "GET", item["url"]).content)
    raise AIError("The AI did not return a picture.")


@register
class OpenAIProvider(Provider):
    id = "openai"
    label = "OpenAI"
    capabilities = (EDIT, VISION)
    default_base_url = "https://api.openai.com/v1"
    key_help_url = "https://platform.openai.com/api-keys"
    help = "Picture clean-up (GPT Image) and design advice (GPT models)."
    env_prefix = "OPENAI"
    fallback_models = {EDIT: "gpt-image-2"}

    def list_models(self, creds: Credentials) -> list[ModelInfo]:
        resp = request(creds, "GET", f"{creds.base_url}/models", headers=bearer(creds))
        out = []
        for m in resp.json().get("data", []):
            mid = str(m.get("id", ""))
            caps = []
            if mid.startswith("gpt-image"):
                caps.append(EDIT)
            if re.match(r"^(gpt-(4o|4\.1|5|6|7)|o3|o4|chatgpt)", mid) and not any(x in mid for x in _NOT_CHAT):
                caps.append(VISION)
            if caps:
                out.append(ModelInfo(mid, mid, tuple(caps)))
        return out

    def rank(self, capability: str, model_id: str) -> float:
        score = -1.0 if _DATED.search(model_id) else 0.0  # prefer the rolling alias over dated snapshots
        if capability == VISION and ("mini" in model_id or "luna" in model_id):
            score += 1  # fast and inexpensive is plenty for design advice
        if capability == EDIT and "mini" not in model_id:
            score += 1  # full model keeps logo details better
        return score

    def edit_image(self, creds, model, png, prompt):
        return images_edit(creds, f"{creds.base_url}/images/edits", model, png, prompt, bearer(creds))

    def vision_json(self, creds, model, images, prompt):
        return chat_vision(creds, f"{creds.base_url}/chat/completions", model, images, prompt, bearer(creds))

"""Ollama: free AI models running on your own computer (design advice only)."""

from __future__ import annotations

import time

from ..base import VISION, AIError, Credentials, ModelInfo, Provider, b64, parse_json, request
from ..registry import register
from .openai import bearer

_VISION_NAMES = ("vl", "vision", "llava", "gemma3", "minicpm-v", "moondream", "bakllava", "mistral-small3")


@register
class OllamaProvider(Provider):
    id = "ollama"
    label = "Ollama (on this computer)"
    capabilities = (VISION,)
    needs_key = False
    supports_base_url = True
    default_base_url = "http://localhost:11434"
    key_help_url = "https://ollama.com/search?c=vision"
    help = "Free and private, runs locally. Install a vision model first, e.g. `ollama pull qwen3-vl`."
    env_prefix = "OLLAMA"
    _probe: tuple[float, bool] = (0.0, False)

    def _reachable(self, creds: Credentials) -> bool:
        """Ready = Ollama answers AND has at least one model that can look at pictures."""
        stamp, ok = OllamaProvider._probe
        if time.time() - stamp < 30:
            return ok
        probe = Credentials(api_key=creds.api_key, base_url=(creds.base_url or self.default_base_url).rstrip("/"))
        try:
            request(probe, "GET", f"{probe.base_url}/api/tags", retries=0, timeout=1.5)
            from ..registry import models_for

            ok = any(VISION in m.capabilities for m in models_for(self, probe))
        except AIError:
            ok = False
        OllamaProvider._probe = (time.time(), ok)
        return ok

    def list_models(self, creds: Credentials) -> list[ModelInfo]:
        tags = request(creds, "GET", f"{creds.base_url}/api/tags", headers=bearer(creds), timeout=10).json()
        out = []
        for m in tags.get("models", []):
            name = str(m.get("name", ""))
            vision = False
            try:
                info = request(creds, "POST", f"{creds.base_url}/api/show", headers=bearer(creds),
                               json={"model": name}, retries=0, timeout=10).json()
                caps = info.get("capabilities")
                vision = "vision" in caps if isinstance(caps, list) else any(v in name for v in _VISION_NAMES)
            except AIError:
                vision = any(v in name for v in _VISION_NAMES)
            if vision:
                out.append(ModelInfo(name, name, (VISION,)))
        return out

    def rank(self, capability: str, model_id: str) -> float:
        for score, key in ((3, "qwen3-vl"), (2, "gemma3"), (1.5, "qwen2.5vl"), (1, "llama3.2-vision"), (0.5, "llava")):
            if key in model_id:
                return score
        return 0.0

    def vision_json(self, creds, model, images, prompt):
        body = {
            "model": model,
            "stream": False,
            "format": "json",
            "messages": [{"role": "user", "content": prompt, "images": [b64(p) for p in images]}],
        }
        data = request(creds, "POST", f"{creds.base_url}/api/chat", headers=bearer(creds), json=body).json()
        return parse_json((data.get("message") or {}).get("content", ""))

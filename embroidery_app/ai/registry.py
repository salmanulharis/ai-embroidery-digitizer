"""Provider registry, live model lists (cached) and model selection."""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Optional

from .base import EDIT, VISION, AIError, Credentials, ModelInfo, Provider, best

PROVIDERS: dict[str, Provider] = {}
_cache: dict[str, tuple[float, list[ModelInfo]]] = {}
_lock = threading.Lock()
MODEL_CACHE_SECONDS = 600


def register(cls):
    """Class decorator: ``@register`` on a Provider subclass makes it available everywhere."""
    instance = cls()
    if not instance.id:
        raise ValueError("Provider needs an id")
    PROVIDERS[instance.id] = instance
    return cls


def get(provider_id: str) -> Provider:
    provider = PROVIDERS.get(provider_id or "")
    if provider is None:
        raise AIError(f"Unknown AI service '{provider_id}'.")
    return provider


def catalog() -> list[dict]:
    return [p.info() for p in PROVIDERS.values()]


def _cache_key(provider: Provider, creds: Credentials) -> str:
    raw = f"{provider.id}\0{creds.base_url}\0{creds.api_key}"
    return hashlib.sha256(raw.encode()).hexdigest()  # the key itself is never kept in memory as a dict key


def models_for(provider: Provider, creds: Credentials, refresh: bool = False) -> list[ModelInfo]:
    key = _cache_key(provider, creds)
    with _lock:
        hit = _cache.get(key)
        if hit and not refresh and time.time() - hit[0] < MODEL_CACHE_SECONDS:
            return hit[1]
    models = provider.list_models(creds)
    models.sort(key=lambda m: m.id)
    with _lock:
        _cache[key] = (time.time(), models)
    return models


def recommended(provider: Provider, models: list[ModelInfo]) -> dict[str, Optional[str]]:
    return {cap: best(models, cap, provider.rank) for cap in (EDIT, VISION) if cap in provider.capabilities}


def choose_model(provider: Provider, creds: Credentials, capability: str) -> str:
    """The user's / server's chosen model, else the best one from the live list,
    else the provider's fallback."""
    if capability not in provider.capabilities:
        raise AIError(f"{provider.label} cannot {'edit pictures' if capability == EDIT else 'look at pictures'}.")
    explicit = creds.edit_model if capability == EDIT else creds.vision_model
    if explicit:
        return explicit
    try:
        pick = best(models_for(provider, creds), capability, provider.rank)
        if pick:
            return pick
    except AIError:
        pass
    fallback = provider.fallback_models.get(capability)
    if fallback:
        return fallback
    raise AIError(f"Choose a model for {provider.label} in AI settings.")

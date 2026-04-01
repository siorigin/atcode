# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Model registry — query and cache available models from LLM providers.

Used by:
- ``invoke_with_retry`` for automatic model fallback
- ``GET /api/config/models`` for frontend model selector
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from loguru import logger

# Preferred fallback models — tried first (in order) if available from the provider.
# Well-tested flagship models known to work reliably with our tool-use prompts.
_PREFERRED_FALLBACK_IDS: list[str] = [
    "claude-sonnet-4-6",
    "gpt-5.4",
    "kimi-k2.5-thinking",
]

# Prefixes / substrings that indicate a model is NOT a chat model
_NON_CHAT_INDICATORS = {
    "text-embedding",
    "embedding",
    "whisper",
    "tts",
    "dall-e",
    "moderation",
    "davinci",
    "babbage",
    "ada",
    "curie",
    # Image generation
    "stable-diffusion",
    "sdxl",
    "midjourney",
    "mj_",
    "flux",
    "imagen",
    # Audio / music
    "suno",
    "musicgen",
    # Reranking / retrieval
    "rerank",
    "jina-clip",
    "jina-colbert",
    # Other non-chat
    "prompt_analyzer",
    "_imagine",
    "_edits",
}


class ModelRegistry:
    """Query and cache available chat models from an OpenAI-compatible ``/v1/models`` endpoint."""

    _cache: dict[str, tuple[list[dict[str, Any]], float]] = {}
    _cache_ttl: int = 300  # 5 minutes

    @classmethod
    def _is_chat_model(cls, model_id: str) -> bool:
        """Return True if ``model_id`` looks like a chat/completions model."""
        model_lower = model_id.lower()
        return not any(ind in model_lower for ind in _NON_CHAT_INDICATORS)

    @classmethod
    async def get_all_models(
        cls, base_url: str, api_key: str | None
    ) -> list[dict[str, Any]]:
        """Fetch raw model list from the provider (cached).

        Returns list of dicts with at least ``id`` and optionally ``owned_by``.
        """
        now = time.time()
        cache_key = base_url
        if cache_key in cls._cache:
            cached_models, cached_time = cls._cache[cache_key]
            if now - cached_time < cls._cache_ttl:
                return cached_models

        # Build URL — handle trailing slash variations
        url = base_url.rstrip("/")
        if url.endswith("/v1"):
            models_url = f"{url}/models"
        else:
            models_url = f"{url}/v1/models"

        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            # Keep UI-triggered model discovery responsive when the upstream
            # provider is slow or unreachable.
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(models_url, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            raw_models: list[dict[str, Any]] = data.get("data", [])
            # Normalise to [{id, owned_by}, ...]
            models = [
                {
                    "id": m.get("id", ""),
                    "owned_by": m.get("owned_by"),
                }
                for m in raw_models
                if m.get("id")
            ]
            cls._cache[cache_key] = (models, now)
            logger.debug(
                f"ModelRegistry: fetched {len(models)} models from {models_url}"
            )
            return models
        except Exception as e:
            logger.warning(f"ModelRegistry: failed to fetch models from {models_url}: {e}")
            # Return stale cache if available
            if cache_key in cls._cache:
                return cls._cache[cache_key][0]
            return []

    @classmethod
    async def get_chat_models(
        cls, base_url: str, api_key: str | None
    ) -> list[dict[str, Any]]:
        """Fetch available chat models, filtered to exclude embedding/tts/whisper/etc."""
        all_models = await cls.get_all_models(base_url, api_key)
        return [m for m in all_models if cls._is_chat_model(m["id"])]

    @classmethod
    async def get_fallback_model_ids(
        cls, base_url: str, api_key: str | None, exclude_model: str
    ) -> list[str]:
        """Return fallback model IDs: preferred flagships first, then remaining available models.

        1. Check which preferred flagship models exist in the provider's model list.
        2. Append any other available chat models after the preferred ones.
        3. Exclude the current (failed) model throughout.
        """
        chat_models = await cls.get_chat_models(base_url, api_key)
        available_ids = {m["id"] for m in chat_models} - {exclude_model}

        # Phase 1: preferred flagships (in priority order), if available
        result: list[str] = []
        for mid in _PREFERRED_FALLBACK_IDS:
            if mid in available_ids:
                result.append(mid)
                available_ids.discard(mid)

        # Phase 2: remaining models as a safety net (for open-source users / other providers)
        result.extend(sorted(available_ids))

        return result

    @classmethod
    def invalidate_cache(cls, base_url: str | None = None) -> None:
        """Clear the model cache (all or for a specific URL)."""
        if base_url:
            cls._cache.pop(base_url, None)
        else:
            cls._cache.clear()

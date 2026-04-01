# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Provider alias normalization helpers.

These helpers let user-facing configuration use clearer provider names while
keeping backward compatibility with older values.
"""

from __future__ import annotations

LLM_PROVIDER_ALIASES: dict[str, str] = {
    "openai": "openai-compatible",
    "openai-compatible": "openai-compatible",
    "openai_compatible": "openai-compatible",
    "compatible": "openai-compatible",
    "google": "gemini",
    "gemini": "gemini",
    "ollama": "ollama",
}

EMBEDDING_PROVIDER_ALIASES: dict[str, str] = {
    "openai": "openai-compatible",
    "openai-compatible": "openai-compatible",
    "openai_compatible": "openai-compatible",
    "voyage": "voyage",
    "vllm": "vllm",
}

LLM_RUNTIME_PROVIDERS: dict[str, str] = {
    "openai-compatible": "openai",
    "gemini": "google",
    "ollama": "ollama",
}


def _normalize_provider(
    provider: str | None, aliases: dict[str, str], default: str = ""
) -> str:
    """Normalize a provider name to a canonical user-facing value."""
    if not provider:
        return default
    key = provider.strip().lower()
    return aliases.get(key, key)


def normalize_llm_provider(provider: str | None) -> str:
    """Normalize an LLM provider name."""
    return _normalize_provider(provider, LLM_PROVIDER_ALIASES)


def normalize_embedding_provider(provider: str | None) -> str:
    """Normalize an embedding provider name."""
    return _normalize_provider(provider, EMBEDDING_PROVIDER_ALIASES)


def to_runtime_llm_provider(provider: str | None) -> str:
    """Map a canonical provider to the runtime backend implementation name."""
    normalized = normalize_llm_provider(provider)
    return LLM_RUNTIME_PROVIDERS.get(normalized, normalized)


def is_openai_compatible_llm_provider(provider: str | None) -> bool:
    """Return whether an LLM provider uses an OpenAI-compatible API surface."""
    return normalize_llm_provider(provider) == "openai-compatible"


def supports_openai_style_model_listing(provider: str | None) -> bool:
    """Return whether a provider is expected to expose a /v1/models endpoint."""
    return normalize_llm_provider(provider) in {"openai-compatible", "ollama"}

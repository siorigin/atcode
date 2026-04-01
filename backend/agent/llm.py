# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from typing import Any
from urllib.parse import urljoin

import httpx
from core.provider_utils import normalize_llm_provider, to_runtime_llm_provider


class LLMError(Exception):
    """Exception for LLM-related failures."""

    pass


# Backwards compatibility alias
LLMGenerationError = LLMError


def _check_ollama_running(endpoint: str = "http://localhost:11434") -> bool:
    """Check if Ollama server is running."""
    try:
        health_url = urljoin(endpoint.rstrip("/"), "/api/tags")
        with httpx.Client(timeout=5.0) as client:
            response = client.get(health_url)
            return response.status_code == 200
    except (httpx.RequestError, httpx.TimeoutException):
        return False


def create_model(config: Any, **kwargs: Any) -> Any:
    """
    Create a LangChain chat model from configuration.

    Args:
        config: Model configuration with provider, model_id, api_key, etc.
        **kwargs: Additional arguments passed to the model constructor.

    Returns:
        LangChain ChatModel instance.

    Raises:
        LLMError: If provider is unsupported or configuration is invalid.
    """
    provider = normalize_llm_provider(config.provider)
    runtime_provider = to_runtime_llm_provider(provider)

    if runtime_provider == "google":
        return _create_google_model(config, **kwargs)
    elif runtime_provider == "openai":
        return _create_openai_model(config, **kwargs)
    elif runtime_provider == "ollama":
        return _create_ollama_model(config, **kwargs)
    else:
        raise LLMError(
            "Unsupported provider: "
            f"{provider}. Use 'openai-compatible', 'gemini', or 'ollama'. "
            "Legacy aliases 'openai' and 'google' are also accepted."
        )


def _create_google_model(config: Any, **kwargs: Any) -> Any:
    """Create Gemini model."""
    # Default max_tokens for documentation generation
    # Use a high value to allow comprehensive documentation output
    default_max_tokens = 8192 if config.provider_type == "vertex" else 8192

    # Extract max_tokens from kwargs or use default
    max_tokens = kwargs.pop("max_tokens", default_max_tokens)

    if config.provider_type == "vertex":
        if not config.project_id:
            raise LLMError("Vertex AI requires project_id. Set LLM_PROJECT_ID in .env")

        try:
            from langchain_google_vertexai import ChatVertexAI
        except ImportError as e:
            raise LLMError(
                "Gemini Vertex AI support is not installed. "
                "Run: uv sync --extra google or uv sync --extra all"
            ) from e

        return ChatVertexAI(
            model_name=config.model_id,
            project=config.project_id,
            location=config.region or "us-central1",
            streaming=True,
            max_tokens=max_tokens,
            **kwargs,
        )
    else:
        if not config.api_key:
            raise LLMError("Gemini API requires api_key. Set LLM_API_KEY in .env")

        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError as e:
            raise LLMError(
                "Gemini support is not installed. "
                "Run: uv sync --extra google or uv sync --extra all"
            ) from e

        return ChatGoogleGenerativeAI(
            model=config.model_id,
            google_api_key=config.api_key,
            streaming=True,
            max_tokens=max_tokens,
            **kwargs,
        )


def _create_openai_model(config: Any, **kwargs: Any) -> Any:
    """Create an OpenAI-compatible chat model."""
    # Default max_tokens for documentation generation
    default_max_tokens = 16384

    # Extract max_tokens from kwargs or use default
    max_tokens = kwargs.pop("max_tokens", default_max_tokens)

    if not config.api_key:
        raise LLMError("OpenAI requires api_key. Set LLM_API_KEY in .env")

    from langchain_openai import ChatOpenAI

    # Reasoning models only accept temperature=1
    model_lower = (config.model_id or "").lower()
    is_reasoning = any(t in model_lower for t in (
        "-r1", "o1-", "o3-", "deepseek-r1", "deepseek-reasoner",
    ))
    # kimi-k2 thinking mode is incompatible with LangChain tool call replay
    # (API requires reasoning_content on every assistant message, but LangChain strips it).
    # Disable thinking to use instant mode which works correctly with tool calls.
    is_kimi_k2 = "kimi-k2" in model_lower
    if is_kimi_k2:
        temperature = 0.6  # kimi-k2 instant mode requires exactly 0.6
    elif is_reasoning:
        temperature = 1
    else:
        temperature = kwargs.pop("temperature", 0)

    model_kwargs = kwargs.pop("model_kwargs", {})
    if is_kimi_k2:
        model_kwargs.setdefault("extra_body", {})
        model_kwargs["extra_body"]["thinking"] = {"type": "disabled"}

    return ChatOpenAI(
        model=config.model_id,
        api_key=config.api_key,
        base_url=config.endpoint or "https://api.openai.com/v1",
        streaming=True,
        max_tokens=max_tokens,
        temperature=temperature,
        model_kwargs=model_kwargs,
        **kwargs,
    )


def _create_ollama_model(config: Any, **kwargs: Any) -> Any:
    """Create Ollama model (via OpenAI-compatible API)."""
    # Default max_tokens for documentation generation
    default_max_tokens = 16384

    # Extract max_tokens from kwargs or use default
    max_tokens = kwargs.pop("max_tokens", default_max_tokens)

    endpoint = config.endpoint or "http://localhost:11434/v1"
    base_url = endpoint.rstrip("/v1").rstrip("/")

    if not _check_ollama_running(base_url):
        raise LLMError(f"Ollama not running at {base_url}. Start with: ollama serve")

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=config.model_id,
        api_key="ollama",
        base_url=endpoint,
        streaming=True,
        max_tokens=max_tokens,
        **kwargs,
    )

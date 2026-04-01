# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from core.config import ModelConfig, settings
from core.provider_utils import (
    normalize_llm_provider,
    supports_openai_style_model_listing,
)
from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

router = APIRouter()


class ProviderConfigRequest(BaseModel):
    """Request model for setting provider configuration."""

    provider: str
    model: str
    api_key: str | None = None
    endpoint: str | None = None
    project_id: str | None = None
    region: str | None = None
    provider_type: str | None = None
    thinking_budget: int | None = None
    service_account_file: str | None = None


class ProviderConfigResponse(BaseModel):
    """Response model for provider configuration."""

    provider: str
    model_id: str
    endpoint: str | None = None
    region: str | None = None
    provider_type: str | None = None
    thinking_budget: int | None = None
    # Note: api_key and service_account_file are intentionally omitted for security


class AllConfigResponse(BaseModel):
    """Response model for all configurations."""

    orchestrator: ProviderConfigResponse


def _config_to_response(config: ModelConfig) -> ProviderConfigResponse:
    """Convert ModelConfig to response model (excluding sensitive fields)."""
    return ProviderConfigResponse(
        provider=config.provider,
        model_id=config.model_id,
        endpoint=config.endpoint,
        region=config.region,
        provider_type=config.provider_type,
        thinking_budget=config.thinking_budget,
    )


@router.get(
    "",
    response_model=AllConfigResponse,
    summary="Get Current Configuration",
    description="Get the current LLM provider configuration.",
)
async def get_config() -> AllConfigResponse:
    """
    Get the current active configuration for orchestrator.

    Returns:
        AllConfigResponse with current configuration
    """
    return AllConfigResponse(
        orchestrator=_config_to_response(settings.active_orchestrator_config),
    )


@router.get(
    "/orchestrator",
    response_model=ProviderConfigResponse,
    summary="Get Orchestrator Configuration",
    description="Get the current orchestrator LLM configuration.",
)
async def get_orchestrator_config() -> ProviderConfigResponse:
    """Get the current orchestrator configuration."""
    return _config_to_response(settings.active_orchestrator_config)


@router.put(
    "/orchestrator",
    response_model=ProviderConfigResponse,
    summary="Set Orchestrator Configuration",
    description="Set the orchestrator LLM provider configuration at runtime.",
)
async def set_orchestrator_config(
    request: ProviderConfigRequest,
) -> ProviderConfigResponse:
    """
    Set the orchestrator configuration at runtime.

    Args:
        request: New provider configuration

    Returns:
        Updated configuration
    """
    try:
        kwargs = {}
        if request.api_key:
            kwargs["api_key"] = request.api_key
        if request.endpoint:
            kwargs["endpoint"] = request.endpoint
        if request.project_id:
            kwargs["project_id"] = request.project_id
        if request.region:
            kwargs["region"] = request.region
        if request.provider_type:
            kwargs["provider_type"] = request.provider_type
        if request.thinking_budget is not None:
            kwargs["thinking_budget"] = request.thinking_budget
        if request.service_account_file:
            kwargs["service_account_file"] = request.service_account_file

        settings.set_orchestrator(request.provider, request.model, **kwargs)

        logger.info(
            f"Orchestrator configuration updated: provider={request.provider}, model={request.model}"
        )

        return _config_to_response(settings.active_orchestrator_config)
    except Exception as e:
        logger.error(f"Failed to set orchestrator config: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.delete(
    "/orchestrator",
    response_model=ProviderConfigResponse,
    summary="Reset Orchestrator Configuration",
    description="Reset orchestrator to default configuration from .env file.",
)
async def reset_orchestrator_config() -> ProviderConfigResponse:
    """Reset orchestrator to default configuration."""
    settings._active_orchestrator = None
    logger.info("Orchestrator configuration reset to default")
    return _config_to_response(settings.active_orchestrator_config)


# =============================================================================
# Model Listing
# =============================================================================


class ModelInfo(BaseModel):
    """A single available model."""

    id: str
    owned_by: str | None = None


class ModelListResponse(BaseModel):
    """Response for the model listing endpoint."""

    models: list[ModelInfo]
    provider_url: str
    current_model: str


@router.get(
    "/models",
    response_model=ModelListResponse,
    summary="List Available Chat Models",
    description="Query the configured LLM provider's /v1/models endpoint and return chat-capable models.",
)
async def list_models() -> ModelListResponse:
    """List available chat models from the configured LLM provider.

    Queries ``{LLM_BASE_URL}/models``, caches for 5 minutes,
    and filters out non-chat models (embedding, whisper, tts, etc.).
    """
    from agent.model_registry import ModelRegistry

    config = settings.active_llm_config
    provider = normalize_llm_provider(config.provider)

    if provider == "gemini":
        return ModelListResponse(
            models=[ModelInfo(id=config.model_id, owned_by="Google")],
            provider_url="gemini://sdk",
            current_model=config.model_id,
        )

    if not supports_openai_style_model_listing(provider):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Provider '{provider}' does not expose an OpenAI-style /v1/models "
                "listing endpoint."
            ),
        )

    base_url = config.endpoint or "https://api.openai.com/v1"

    try:
        chat_models = await ModelRegistry.get_chat_models(base_url, config.api_key)
    except Exception as e:
        logger.error(f"Failed to list models from {base_url}: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to query models: {e}")

    return ModelListResponse(
        models=[ModelInfo(id=m["id"], owned_by=m.get("owned_by")) for m in chat_models],
        provider_url=base_url,
        current_model=config.model_id,
    )

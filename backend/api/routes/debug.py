# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from typing import Any

from api.dependencies import get_cache, get_config, get_orchestrator_pool, get_storage
from api.models.response import PoolStats
from fastapi import APIRouter, Depends

router = APIRouter()


@router.get(
    "/pool-stats",
    response_model=PoolStats,
    summary="Pool Statistics",
    description="Get orchestrator pool statistics.",
)
async def pool_stats(pool=Depends(get_orchestrator_pool)) -> dict[str, Any]:
    """
    Get orchestrator pool statistics.

    Returns:
        Pool statistics including size, hits, misses, and orchestrator info
    """
    if pool:
        return pool.get_stats()
    return {"error": "Pool not initialized"}


@router.get(
    "/cache-stats",
    summary="Cache Statistics",
    description="Get cache backend statistics.",
)
async def cache_stats(cache=Depends(get_cache)) -> dict[str, Any]:
    """
    Get cache statistics.

    Returns:
        Cache statistics including hits, misses, and configuration
    """
    if cache and hasattr(cache, "get_stats"):
        return cache.get_stats()
    return {"error": "Cache stats not available"}


@router.get(
    "/config",
    summary="Configuration",
    description="Get current configuration (sanitized).",
)
async def config() -> dict[str, Any]:
    """
    Get current configuration with sensitive values removed.

    Returns:
        Sanitized configuration dict
    """
    config = get_config()

    # Remove sensitive values
    sensitive_keys = ["password", "secret", "key", "token", "api_key"]
    safe_config = {}

    for k, v in config.items():
        if not any(s in k.lower() for s in sensitive_keys):
            safe_config[k] = v
        else:
            safe_config[k] = "***REDACTED***"

    return safe_config


@router.get(
    "/storage-stats",
    summary="Storage Statistics",
    description="Get storage backend statistics.",
)
async def storage_stats(storage=Depends(get_storage)) -> dict[str, Any]:
    """
    Get storage backend statistics.

    Returns:
        Storage information
    """
    if storage:
        return {"type": type(storage).__name__, "initialized": True}
    return {"type": "unknown", "initialized": False}


@router.post(
    "/pool-cleanup",
    summary="Force Pool Cleanup",
    description="Force cleanup of idle orchestrators.",
)
async def force_pool_cleanup(pool=Depends(get_orchestrator_pool)) -> dict[str, str]:
    """
    Force cleanup of idle orchestrators.

    Returns:
        Status message
    """
    if pool:
        await pool._cleanup_sessions()
        return {"status": "Cleanup completed", "size": len(pool)}
    return {"status": "Pool not initialized"}


@router.post(
    "/cache-clear", summary="Clear Cache", description="Clear all cache entries."
)
async def clear_cache(cache=Depends(get_cache)) -> dict[str, str]:
    """
    Clear all cache entries.

    Returns:
        Status message
    """
    if cache:
        await cache.clear()
        return {"status": "Cache cleared"}
    return {"status": "Cache not initialized"}

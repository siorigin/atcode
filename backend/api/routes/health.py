# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import time
from datetime import UTC, datetime

from api.models.response import HealthResponse
from fastapi import APIRouter

router = APIRouter()

# Track service start time
_start_time = time.time()


def get_uptime() -> float:
    """Get service uptime in seconds."""
    return time.time() - _start_time


@router.get(
    "",
    response_model=HealthResponse,
    summary="Health Check",
    description="Check service health status and component availability.",
)
async def health_check() -> HealthResponse:
    """
    Perform health check and return service status.

    Returns:
        HealthResponse with status and component info
    """
    components: dict[str, str] = {}
    overall_status = "healthy"

    # Check orchestrator pool
    try:
        from api.dependencies import get_orchestrator_pool

        pool = get_orchestrator_pool()
        if pool:
            stats = pool.get_stats()
            if stats.get("initialized"):
                components["orchestrator_pool"] = "healthy"
            else:
                components["orchestrator_pool"] = "not_initialized"
        else:
            components["orchestrator_pool"] = "not_initialized"
    except Exception as e:
        components["orchestrator_pool"] = f"error: {str(e)}"
        overall_status = "degraded"

    # Check cache backend
    try:
        from api.dependencies import get_cache

        cache = get_cache()
        if cache:
            components["cache"] = "healthy"
        else:
            components["cache"] = "not_initialized"
    except Exception as e:
        components["cache"] = f"error: {str(e)}"
        # Cache failure is not critical

    # Check storage backend
    try:
        from api.dependencies import get_storage

        storage = get_storage()
        if storage:
            components["storage"] = "healthy"
        else:
            components["storage"] = "not_initialized"
    except Exception as e:
        components["storage"] = f"error: {str(e)}"
        overall_status = "degraded"

    return HealthResponse(
        status=overall_status,
        version="2.0.0",
        uptime_seconds=get_uptime(),
        components=components,
        timestamp=datetime.now(UTC),
    )


@router.get(
    "/ready",
    summary="Readiness Check",
    description="Check if service is ready to accept traffic.",
)
async def readiness_check() -> dict[str, str]:
    """
    Check if service is ready to handle requests.

    Returns:
        Simple status dict
    """
    try:
        from api.dependencies import get_orchestrator_pool

        pool = get_orchestrator_pool()
        if pool:
            return {"status": "ready"}
    except Exception:
        pass

    return {"status": "initializing"}


@router.get("/live", summary="Liveness Check", description="Check if service is alive.")
async def liveness_check() -> dict[str, str]:
    """
    Simple liveness check.

    Returns:
        Simple status dict
    """
    return {"status": "alive"}

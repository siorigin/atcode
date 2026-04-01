# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(UTC)


class ChatEventType(StrEnum):
    """Types of chat events."""

    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    RESPONSE = "response"
    COMPLETE = "complete"
    ERROR = "error"
    THINKING = "thinking"
    STATUS = "status"


class ChatEvent(BaseModel):
    """Chat event model for streaming responses."""

    type: ChatEventType = Field(..., description="Event type")
    content: str = Field("", description="Event content")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata"
    )
    timestamp: datetime | None = Field(
        default_factory=_utcnow, description="Event timestamp"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "type": "response",
                "content": "The authentication module uses JWT tokens...",
                "metadata": {"tokens_used": 150},
                "timestamp": "2024-01-01T12:00:00Z",
            }
        }


class SessionMetadata(BaseModel):
    """Session metadata model."""

    id: str = Field(..., description="Session identifier")
    user_id: str = Field(..., description="User identifier")
    repo_name: str = Field(..., description="Repository name")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    turns_count: int = Field(0, description="Number of conversation turns")
    first_query: str | None = Field(None, description="First query preview")
    last_query: str | None = Field(None, description="Last query preview")


class SessionResponse(BaseModel):
    """Session response model."""

    sessions: list[SessionMetadata] = Field(
        default_factory=list, description="List of sessions"
    )
    total: int = Field(0, description="Total number of sessions")
    limit: int = Field(100, description="Page size")
    offset: int = Field(0, description="Page offset")


class SessionDetailResponse(BaseModel):
    """Detailed session response model."""

    id: str = Field(..., description="Session identifier")
    user_id: str = Field(..., description="User identifier")
    repo_name: str = Field(..., description="Repository name")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    turns: list[dict[str, Any]] = Field(
        default_factory=list, description="Conversation turns"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Session metadata"
    )


class HealthResponse(BaseModel):
    """Health check response model."""

    status: Literal["healthy", "degraded", "unhealthy"] = Field(
        ..., description="Service health status"
    )
    version: str = Field("2.0.0", description="API version")
    uptime_seconds: float = Field(..., description="Service uptime in seconds")
    components: dict[str, str] = Field(
        default_factory=dict, description="Component health status"
    )
    timestamp: datetime = Field(
        default_factory=_utcnow, description="Health check timestamp"
    )


class OrchestratorInfo(BaseModel):
    """Orchestrator instance information."""

    key: str = Field(..., description="Orchestrator key (repo:mode)")
    created_at: datetime = Field(..., description="Creation timestamp")
    last_access: datetime = Field(..., description="Last access timestamp")
    access_count: int = Field(0, description="Total access count")
    idle_seconds: float = Field(0, description="Seconds since last access")


class PoolStats(BaseModel):
    """Orchestrator pool statistics."""

    size: int = Field(0, description="Current pool size")
    max_size: int = Field(5, description="Maximum pool size")
    total_requests: int = Field(0, description="Total requests processed")
    cache_hits: int = Field(0, description="Number of cache hits")
    cache_misses: int = Field(0, description="Number of cache misses")
    orchestrators: list[OrchestratorInfo] = Field(
        default_factory=list, description="List of active orchestrators"
    )


class ErrorResponse(BaseModel):
    """Error response model."""

    error: str = Field(..., description="Error message")
    detail: str | None = Field(None, description="Detailed error information")
    code: str | None = Field(None, description="Error code")
    request_id: str | None = Field(None, description="Request identifier for debugging")

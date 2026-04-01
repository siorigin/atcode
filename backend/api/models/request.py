# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from pydantic import BaseModel, Field


class DocumentContextRequest(BaseModel):
    """Document context for chat requests."""

    file_path: str | None = Field(
        None, description="Path to the document file for context"
    )
    selected_text: str | None = Field(
        None, description="Selected text from the document"
    )
    page_type: str | None = Field(
        None, description="Current page type (home, repo, operator, other)"
    )
    operator_name: str | None = Field(
        None, description="Current operator/document name being viewed"
    )
    document_title: str | None = Field(
        None, description="Human-readable page title"
    )
    source_paper_id: str | None = Field(
        None, description="Paper ID if the repo was built from a paper"
    )
    source_paper_title: str | None = Field(
        None, description="Paper title if the repo was built from a paper"
    )


class ModelOverride(BaseModel):
    """Per-request model override configuration (OpenAI-compatible)."""

    model: str = Field(
        ..., description="Model identifier (e.g. claude-haiku-4-5-20251001, gpt-5.1)"
    )


class ChatRequest(BaseModel):
    """Chat request model."""

    repo: str = Field(..., min_length=1, description="Repository name")
    message: str = Field(..., min_length=1, description="User message")
    session_id: str = Field(..., min_length=1, description="Session identifier")
    mode: str | None = Field(
        None, description="Deprecated — ignored. Kept for backward compatibility."
    )
    max_tool_calls: int | None = Field(
        None, ge=1, le=100, description="Maximum number of tool calls per turn"
    )
    preload: bool = Field(False, description="Whether to preload the orchestrator")
    skip_save_log: bool = Field(
        False, description="Whether to skip saving chat log (for batch evaluation)"
    )
    document_context: DocumentContextRequest | None = Field(
        None, description="Optional document context for the chat"
    )
    model_override: ModelOverride | None = Field(
        None, description="Per-request model override (only affects this request)"
    )
    truncate_turns: int | None = Field(
        None, ge=0,
        description="Keep first N user-assistant turn pairs, discard the rest. For edit-and-regenerate."
    )

    class Config:
        json_schema_extra = {
            "example": {
                "repo": "my-project",
                "message": "How does the authentication module work?",
                "session_id": "session-abc123",
                "mode": "default",
                "max_tool_calls": 10,
            }
        }


class SessionRequest(BaseModel):
    """Session management request model."""

    repo: str = Field(..., min_length=1, description="Repository name")
    limit: int = Field(
        100, ge=1, le=1000, description="Maximum number of sessions to return"
    )
    offset: int = Field(0, ge=0, description="Pagination offset")


class CreateSessionRequest(BaseModel):
    """Create session request model."""

    repo: str = Field(..., min_length=1, description="Repository name")
    session_id: str | None = Field(None, description="Optional custom session ID")
    metadata: dict | None = Field(None, description="Optional session metadata")

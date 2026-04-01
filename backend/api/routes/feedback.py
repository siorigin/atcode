# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import json
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

router = APIRouter()


# ============== Request/Response Models ==============


class FeedbackCreateRequest(BaseModel):
    """Request to create new feedback."""

    title: str = Field(..., min_length=1, max_length=200, description="Feedback title")
    description: str = Field(
        ..., min_length=1, max_length=2000, description="Detailed description"
    )
    category: Literal["bug", "feature", "improvement", "question", "other"] = Field(
        "other", description="Feedback category"
    )
    author: str | None = Field(
        None, max_length=50, description="Author name (optional)"
    )


class FeedbackUpdateRequest(BaseModel):
    """Request to update feedback status."""

    status: Literal["open", "resolved"] = Field(..., description="New status")


class FeedbackItem(BaseModel):
    """A feedback item."""

    id: str = Field(..., description="Unique feedback ID")
    title: str = Field(..., description="Feedback title")
    description: str = Field(..., description="Detailed description")
    category: str = Field(..., description="Feedback category")
    status: Literal["open", "resolved"] = Field(..., description="Current status")
    author: str = Field(..., description="Author name")
    created_at: str = Field(..., description="Creation timestamp")
    updated_at: str = Field(..., description="Last update timestamp")
    resolved_at: str | None = Field(None, description="Resolution timestamp")


class FeedbackListResponse(BaseModel):
    """Response for listing feedback."""

    feedback: list[FeedbackItem] = Field(
        default_factory=list, description="List of feedback items"
    )
    total: int = Field(0, description="Total count")
    open_count: int = Field(0, description="Count of open items")
    resolved_count: int = Field(0, description="Count of resolved items")


# ============== Helper Functions ==============


def get_feedback_dir() -> Path:
    """Get the feedback storage directory."""
    backend_dir = Path(__file__).parent.parent.parent  # routes -> api -> backend
    project_root = backend_dir.parent  # backend -> atcode
    feedback_dir = project_root / "data" / "feedback"
    feedback_dir.mkdir(parents=True, exist_ok=True)
    return feedback_dir


def get_feedback_file() -> Path:
    """Get the feedback JSON file path."""
    return get_feedback_dir() / "feedback.json"


def load_feedback() -> list[dict]:
    """Load all feedback from storage."""
    feedback_file = get_feedback_file()
    if not feedback_file.exists():
        return []
    try:
        with open(feedback_file, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load feedback: {e}")
        return []


def save_feedback(feedback_list: list[dict]) -> None:
    """Save all feedback to storage."""
    feedback_file = get_feedback_file()
    try:
        with open(feedback_file, "w", encoding="utf-8") as f:
            json.dump(feedback_list, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save feedback: {e}")
        raise HTTPException(status_code=500, detail="Failed to save feedback")


# ============== API Endpoints ==============


@router.post(
    "",
    response_model=FeedbackItem,
    summary="Create Feedback",
    description="Submit new feedback or report an issue.",
)
async def create_feedback(request: FeedbackCreateRequest) -> FeedbackItem:
    """Create a new feedback item."""
    try:
        now = datetime.utcnow().isoformat() + "Z"
        feedback_item = FeedbackItem(
            id=str(uuid4()),
            title=request.title.strip(),
            description=request.description.strip(),
            category=request.category,
            status="open",
            author=request.author.strip() if request.author else "Anonymous",
            created_at=now,
            updated_at=now,
            resolved_at=None,
        )

        feedback_list = load_feedback()
        feedback_list.insert(
            0, feedback_item.model_dump()
        )  # Insert at beginning (newest first)
        save_feedback(feedback_list)

        logger.info(f"Created feedback: {feedback_item.id} - {feedback_item.title}")
        return feedback_item

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create feedback: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "",
    response_model=FeedbackListResponse,
    summary="List Feedback",
    description="Get all feedback items, optionally filtered by status.",
)
async def list_feedback(
    status: Literal["open", "resolved", "all"] | None = Query(
        "all", description="Filter by status"
    ),
    limit: int = Query(100, ge=1, le=500, description="Maximum items to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
) -> FeedbackListResponse:
    """List all feedback with optional filtering."""
    try:
        feedback_list = load_feedback()

        # Count totals
        open_count = sum(1 for f in feedback_list if f.get("status") == "open")
        resolved_count = sum(1 for f in feedback_list if f.get("status") == "resolved")

        # Filter by status
        if status and status != "all":
            feedback_list = [f for f in feedback_list if f.get("status") == status]

        # Apply pagination
        total = len(feedback_list)
        feedback_list = feedback_list[offset : offset + limit]

        return FeedbackListResponse(
            feedback=[FeedbackItem(**f) for f in feedback_list],
            total=total,
            open_count=open_count,
            resolved_count=resolved_count,
        )

    except Exception as e:
        logger.error(f"Failed to list feedback: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/{feedback_id}",
    response_model=FeedbackItem,
    summary="Get Feedback",
    description="Get a specific feedback item by ID.",
)
async def get_feedback(feedback_id: str) -> FeedbackItem:
    """Get a single feedback item."""
    try:
        feedback_list = load_feedback()
        for f in feedback_list:
            if f.get("id") == feedback_id:
                return FeedbackItem(**f)
        raise HTTPException(status_code=404, detail="Feedback not found")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get feedback: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.patch(
    "/{feedback_id}",
    response_model=FeedbackItem,
    summary="Update Feedback Status",
    description="Update the status of a feedback item (resolve/unresolve).",
)
async def update_feedback(
    feedback_id: str, request: FeedbackUpdateRequest
) -> FeedbackItem:
    """Update feedback status."""
    try:
        feedback_list = load_feedback()
        now = datetime.utcnow().isoformat() + "Z"

        for i, f in enumerate(feedback_list):
            if f.get("id") == feedback_id:
                feedback_list[i]["status"] = request.status
                feedback_list[i]["updated_at"] = now

                if request.status == "resolved":
                    feedback_list[i]["resolved_at"] = now
                else:
                    feedback_list[i]["resolved_at"] = None

                save_feedback(feedback_list)
                logger.info(
                    f"Updated feedback {feedback_id} status to {request.status}"
                )
                return FeedbackItem(**feedback_list[i])

        raise HTTPException(status_code=404, detail="Feedback not found")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update feedback: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete(
    "/{feedback_id}", summary="Delete Feedback", description="Delete a feedback item."
)
async def delete_feedback(feedback_id: str) -> dict:
    """Delete a feedback item."""
    try:
        feedback_list = load_feedback()
        original_count = len(feedback_list)
        feedback_list = [f for f in feedback_list if f.get("id") != feedback_id]

        if len(feedback_list) == original_count:
            raise HTTPException(status_code=404, detail="Feedback not found")

        save_feedback(feedback_list)
        logger.info(f"Deleted feedback: {feedback_id}")

        return {
            "success": True,
            "id": feedback_id,
            "message": "Feedback deleted successfully",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete feedback: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

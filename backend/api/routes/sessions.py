# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from api.dependencies import get_storage
from api.middleware.auth import get_current_user, get_user_session_id
from api.models.request import CreateSessionRequest
from api.models.response import (
    SessionDetailResponse,
    SessionMetadata,
    SessionResponse,
)
from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger

router = APIRouter()


@router.get(
    "",
    response_model=SessionResponse,
    summary="List Sessions",
    description="List all sessions for the current user.",
)
async def list_sessions(
    repo: str | None = Query(None, description="Filter by repository"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum sessions to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    user_id: str = Depends(get_current_user),
    storage=Depends(get_storage),
) -> SessionResponse:
    """
    List sessions for the current user.

    Args:
        repo: Optional repository filter
        limit: Maximum number of sessions
        offset: Pagination offset
        user_id: Current user ID
        storage: Storage backend

    Returns:
        SessionResponse with list of sessions
    """
    try:
        sessions = await storage.list_sessions(
            user_id=user_id, repo_name=repo, limit=limit, offset=offset
        )

        # Convert to response model
        session_list = [
            SessionMetadata(
                id=s.get("id", ""),
                user_id=s.get("user_id", user_id),
                repo_name=s.get("repo_name", ""),
                created_at=s.get("created_at"),
                updated_at=s.get("updated_at"),
                turns_count=s.get("turns_count", 0),
                first_query=s.get("first_query"),
                last_query=s.get("last_query"),
            )
            for s in sessions
        ]

        return SessionResponse(
            sessions=session_list,
            total=len(session_list),  # TODO: Get actual total count
            limit=limit,
            offset=offset,
        )

    except Exception as e:
        logger.error(f"Error listing sessions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/{repo}",
    response_model=SessionResponse,
    summary="List Repository Sessions",
    description="List all sessions for a specific repository.",
)
async def list_repo_sessions(
    repo: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user_id: str = Depends(get_current_user),
    storage=Depends(get_storage),
) -> SessionResponse:
    """
    List sessions for a specific repository.

    Args:
        repo: Repository name
        limit: Maximum sessions
        offset: Pagination offset
        user_id: Current user ID
        storage: Storage backend

    Returns:
        SessionResponse with sessions
    """
    return await list_sessions(
        repo=repo, limit=limit, offset=offset, user_id=user_id, storage=storage
    )


@router.get(
    "/{repo}/{session_id}",
    response_model=SessionDetailResponse,
    summary="Get Session",
    description="Get detailed session information.",
)
async def get_session(
    repo: str,
    session_id: str,
    user_id: str = Depends(get_current_user),
    storage=Depends(get_storage),
) -> SessionDetailResponse:
    """
    Get session details.

    Args:
        repo: Repository name
        session_id: Session identifier
        user_id: Current user ID
        storage: Storage backend

    Returns:
        SessionDetailResponse with full session data
    """
    try:
        user_session_id = get_user_session_id(user_id, session_id)

        session_data = await storage.load_session(user_session_id, user_id)

        if not session_data:
            raise HTTPException(status_code=404, detail="Session not found")

        return SessionDetailResponse(
            id=session_data.get("id", session_id),
            user_id=session_data.get("user_id", user_id),
            repo_name=session_data.get("repo_name", repo),
            created_at=session_data.get("created_at"),
            updated_at=session_data.get("updated_at"),
            turns=session_data.get("turns", []),
            metadata=session_data.get("metadata", {}),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete(
    "/{session_id}", summary="Delete Session", description="Delete a session."
)
async def delete_session(
    session_id: str,
    user_id: str = Depends(get_current_user),
    storage=Depends(get_storage),
) -> dict:
    """
    Delete a session.

    Args:
        session_id: Session identifier
        user_id: Current user ID
        storage: Storage backend

    Returns:
        Success message
    """
    try:
        user_session_id = get_user_session_id(user_id, session_id)

        success = await storage.delete_session(user_session_id, user_id)

        if not success:
            raise HTTPException(status_code=404, detail="Session not found")

        logger.info(f"Deleted session: {session_id} for user: {user_id}")
        return {"message": "Session deleted successfully", "session_id": session_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "",
    response_model=SessionDetailResponse,
    summary="Create Session",
    description="Create a new session.",
)
async def create_session(
    request: CreateSessionRequest,
    user_id: str = Depends(get_current_user),
    storage=Depends(get_storage),
) -> SessionDetailResponse:
    """
    Create a new session.

    Args:
        request: Create session request
        user_id: Current user ID
        storage: Storage backend

    Returns:
        Created session details
    """
    import uuid
    from datetime import datetime

    try:
        # Generate session ID if not provided
        session_id = request.session_id or str(uuid.uuid4())
        user_session_id = get_user_session_id(user_id, session_id)

        # Check if session already exists
        exists = await storage.session_exists(user_session_id, user_id)
        if exists:
            raise HTTPException(status_code=409, detail="Session already exists")

        # Create session data
        now = datetime.utcnow()
        session_data = {
            "id": session_id,
            "user_id": user_id,
            "repo_name": request.repo,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "turns": [],
            "metadata": request.metadata or {},
        }

        # Save session
        success = await storage.save_session(
            session_id=user_session_id,
            user_id=user_id,
            repo_name=request.repo,
            data=session_data,
        )

        if not success:
            raise HTTPException(status_code=500, detail="Failed to create session")

        logger.info(f"Created session: {session_id} for user: {user_id}")

        return SessionDetailResponse(
            id=session_id,
            user_id=user_id,
            repo_name=request.repo,
            created_at=now,
            updated_at=now,
            turns=[],
            metadata=request.metadata or {},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating session: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import json
from collections.abc import AsyncGenerator

from api.dependencies import get_orchestrator_pool, get_storage
from api.middleware.auth import (
    get_current_user,
    get_user_session_id,
    validate_session_ownership,
)
from api.models.request import ChatRequest
from api.models.response import ChatEvent, ChatEventType
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from loguru import logger

router = APIRouter()


async def validate_session(session_id: str, user_id: str, storage) -> bool:
    """
    Validate session ownership.

    Args:
        session_id: Session identifier
        user_id: User identifier
        storage: Storage backend

    Returns:
        True if session is valid or new
    """
    # For new sessions, always allow
    user_session_id = get_user_session_id(user_id, session_id)

    # Check if session exists
    if storage:
        exists = await storage.session_exists(user_session_id, user_id)
        if exists:
            # Validate ownership
            return validate_session_ownership(user_session_id, user_id)

    # New session, allow creation
    return True


async def stream_chat_events(
    http_request: Request, chat_request: ChatRequest, user_id: str, pool, storage
) -> AsyncGenerator[str, None]:
    """
    Stream chat events as Server-Sent Events.

    Args:
        request: Chat request
        user_id: User identifier
        pool: Orchestrator pool (provides global orchestrator and ingestor)
        storage: Storage backend

    Yields:
        SSE formatted event strings
    """
    import os

    user_session_id = get_user_session_id(user_id, chat_request.session_id)

    try:
        # Send status event
        yield f"data: {json.dumps({'type': 'status', 'content': 'Initializing...'})}\n\n"

        # Get global orchestrator (already initialized at startup)
        orchestrator = pool.get_orchestrator()

        if not orchestrator:
            yield f"data: {json.dumps({'type': 'error', 'content': 'Failed to initialize orchestrator'})}\n\n"
            return

        # Get global ingestor
        ingestor = pool.get_ingestor()

        # Get repo path
        repo_path = pool.get_repo_path(chat_request.repo)

        # Send status event
        yield f"data: {json.dumps({'type': 'status', 'content': 'Processing...'})}\n\n"

        # Prepare context file path if needed (skip for __global__ mode)
        context_file = None
        is_global = chat_request.repo == "__global__"
        if (
            not is_global
            and chat_request.document_context
            and chat_request.document_context.file_path
        ):
            # Construct context file path using centralized config
            from core.config import get_wiki_doc_dir

            # Extract just the filename from the path
            # Frontend might send "/repos/sglang/filename" or just "filename"
            file_path = chat_request.document_context.file_path
            # Remove any leading path components to get just the filename
            file_name = os.path.basename(file_path)
            # Remove .json extension if present (we'll add it back)
            if file_name.endswith(".json"):
                file_name = file_name[:-5]

            context_file = str(get_wiki_doc_dir(chat_request.repo) / f"{file_name}.json")
            logger.info(f"Context file path: {context_file} (from: {file_path})")

        # Prepare model override if provided (just model name for OpenAI-compatible API)
        model_override = None
        if chat_request.model_override:
            model_override = chat_request.model_override.model
            logger.info(f"Using per-request model: {model_override}")

        # 🔧 CRITICAL FIX: Preload session if it exists
        # This ensures that when user loads a saved chat log, the backend orchestrator
        # has the full conversation history (not just the frontend store)
        # The request.preload flag is set by frontend when the session has previous messages
        if chat_request.preload:
            logger.info(
                f"Preloading session {user_session_id} for repo {chat_request.repo}"
            )
            # Use orchestrator's preload_session to load chat history from disk
            # This loads the messages into the orchestrator's session cache
            preload_success = orchestrator.preload_session(
                session_id=user_session_id,
                repo_name=chat_request.repo,
                repo_path=str(repo_path),
            )
            logger.info(f"Session preload result: {preload_success}")

        # Extract page context metadata (Phase 4: lightweight context)
        page_context = None
        if chat_request.document_context:
            dc = chat_request.document_context
            if dc.page_type or dc.operator_name:
                page_context = {
                    "page_type": dc.page_type,
                    "operator_name": dc.operator_name,
                    "document_title": dc.document_title,
                    "file_path": dc.file_path,
                }
                if dc.source_paper_id:
                    page_context["source_paper_id"] = dc.source_paper_id
                    page_context["source_paper_title"] = dc.source_paper_title
                if dc.selected_text:
                    page_context["selected_text"] = dc.selected_text

        # Stream chat response with repo info passed as parameters
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()

        async def produce_events() -> None:
            async for event in orchestrator.stream_chat(
                message=chat_request.message,
                repo_name=chat_request.repo,
                repo_path=str(repo_path),  # Pass repo_path as parameter
                ingestor=ingestor,  # Pass ingestor as parameter
                session_id=user_session_id,
                max_tool_calls=chat_request.max_tool_calls,
                context_file=context_file,
                page_context=page_context,  # Pass lightweight page context
                mode=chat_request.mode,  # Pass mode for dynamic prompt selection
                skip_save_log=chat_request.skip_save_log,  # Pass skip_save_log for batch evaluation
                model_override=model_override,  # Pass per-request model override
                truncate_turns=chat_request.truncate_turns,  # Pass truncate_turns for edit-regenerate
            ):
                await queue.put({"kind": "event", "payload": event})

        producer = asyncio.create_task(produce_events())

        try:
            while True:
                if await http_request.is_disconnected():
                    logger.info(
                        f"HTTP client disconnected for chat session {user_session_id}"
                    )
                    producer.cancel()
                    raise asyncio.CancelledError

                if producer.done() and queue.empty():
                    producer.result()
                    break

                try:
                    queued = await asyncio.wait_for(queue.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue

                event = queued["payload"]
                if not isinstance(event, dict):
                    continue

                event_data = {
                    "type": event.get("type", "response"),
                    "content": event.get("content", ""),
                    "metadata": event.get("metadata", {}),
                }
                yield f"data: {json.dumps(event_data)}\n\n"

                # No delay for response tokens - stream immediately for ChatGPT-like experience
                # Only add minimal delay for non-token events to prevent overwhelming
                if event.get("type") != "response":
                    await asyncio.sleep(0.01)
        finally:
            if not producer.done():
                producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)

        # NOTE: The orchestrator already yields a 'complete' event with full metadata
        # Do NOT send another complete event here - it causes duplicate message rendering
        # in the frontend (one with metadata, one without)

    except asyncio.CancelledError:
        logger.info(f"Chat stream cancelled for session {user_session_id}")
        yield f"data: {json.dumps({'type': 'error', 'content': 'Stream cancelled'})}\n\n"

    except Exception as e:
        logger.error(f"Chat stream error: {e}")
        yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"


@router.post(
    "/stream",
    summary="Stream Chat",
    description="Stream chat responses using Server-Sent Events.",
    response_class=StreamingResponse,
)
async def chat_stream(
    chat_request: ChatRequest,
    http_request: Request,
    user_id: str = Depends(get_current_user),
    pool=Depends(get_orchestrator_pool),
    storage=Depends(get_storage),
):
    """
    Stream chat responses as Server-Sent Events.

    Args:
        request: Chat request with repo, message, session_id, mode
        user_id: Current user ID (from auth)
        pool: Orchestrator pool
        storage: Storage backend

    Returns:
        StreamingResponse with SSE events
    """
    # Validate session ownership
    if not await validate_session(chat_request.session_id, user_id, storage):
        raise HTTPException(status_code=403, detail="Access denied to this session")

    logger.info(
        f"Chat request: user={user_id}, repo={chat_request.repo}, "
        f"session={chat_request.session_id}"
    )

    return StreamingResponse(
        stream_chat_events(http_request, chat_request, user_id, pool, storage),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@router.post(
    "",
    summary="Chat (Non-Streaming)",
    description="Send a chat message and receive the complete response.",
    response_model=ChatEvent,
)
async def chat(
    request: ChatRequest,
    user_id: str = Depends(get_current_user),
    pool=Depends(get_orchestrator_pool),
    storage=Depends(get_storage),
) -> ChatEvent:
    """
    Non-streaming chat endpoint.

    Collects all events and returns the final response.

    Args:
        request: Chat request
        user_id: Current user ID
        pool: Orchestrator pool
        storage: Storage backend

    Returns:
        Final chat response
    """
    # Validate session ownership
    if not await validate_session(request.session_id, user_id, storage):
        raise HTTPException(status_code=403, detail="Access denied to this session")

    user_session_id = get_user_session_id(user_id, request.session_id)

    try:
        # Get global orchestrator
        orchestrator = pool.get_orchestrator()

        if not orchestrator:
            raise HTTPException(
                status_code=500, detail="Failed to initialize orchestrator"
            )

        # Get global ingestor and repo path
        ingestor = pool.get_ingestor()
        repo_path = pool.get_repo_path(request.repo)

        # Prepare model override if provided (just model name for OpenAI-compatible API)
        model_override = None
        if request.model_override:
            model_override = request.model_override.model

        # Collect response
        full_response = ""
        metadata = {}

        async for event in orchestrator.stream_chat(
            message=request.message,
            repo_name=request.repo,
            repo_path=str(repo_path),  # ✅ Pass repo_path as parameter
            ingestor=ingestor,  # ✅ Pass ingestor as parameter
            session_id=user_session_id,
            max_tool_calls=request.max_tool_calls,
            mode=request.mode,  # ✅ Pass mode for dynamic prompt selection
            skip_save_log=request.skip_save_log,  # ✅ Pass skip_save_log for batch evaluation
            model_override=model_override,  # ✅ Pass per-request model override
            truncate_turns=request.truncate_turns,  # ✅ Pass truncate_turns for edit-regenerate
        ):
            if event.get("type") == "response":
                full_response += event.get("content", "")
            elif event.get("type") == "complete":
                metadata = event.get("metadata", {})

        return ChatEvent(
            type=ChatEventType.COMPLETE, content=full_response, metadata=metadata
        )

    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

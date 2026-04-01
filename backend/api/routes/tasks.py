# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import json
from dataclasses import asdict

from api.services.task_queue import TaskStatus, get_task_manager
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from loguru import logger
from pydantic import BaseModel, Field

router = APIRouter()


# ============== WebSocket Connection Manager ==============


class WebSocketConnectionManager:
    """Manages WebSocket connections for real-time task updates."""

    def __init__(self):
        """Initialize the connection manager."""
        self.active_connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        """Register a new WebSocket connection."""
        await websocket.accept()
        async with self._lock:
            self.active_connections.add(websocket)
        logger.debug(
            f"WebSocket connected. Total connections: {len(self.active_connections)}"
        )

    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""
        async with self._lock:
            self.active_connections.discard(websocket)
        logger.debug(
            f"WebSocket disconnected. Total connections: {len(self.active_connections)}"
        )

    async def broadcast(self, message: dict) -> None:
        """Broadcast a message to all connected clients."""
        if not self.active_connections:
            return

        # Make a copy of connections in case it changes during iteration
        async with self._lock:
            connections = list(self.active_connections)

        disconnected = []
        for connection in connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.debug(f"Failed to send WebSocket message: {e}")
                disconnected.append(connection)

        # Clean up disconnected connections
        if disconnected:
            async with self._lock:
                for conn in disconnected:
                    self.active_connections.discard(conn)


# Global WebSocket manager
_ws_manager = WebSocketConnectionManager()


def get_ws_manager() -> WebSocketConnectionManager:
    """Get the global WebSocket manager."""
    return _ws_manager


class TaskStatusResponse(BaseModel):
    """Response model for task status."""

    task_id: str = Field(..., description="Unique task identifier")
    status: str = Field(
        ...,
        description="Task status: pending, running, stalled, completed, failed, cancelled",
    )
    task_type: str = Field(
        "", description="Type of task: graph_build, overview_gen, doc_gen"
    )
    repo_name: str = Field("", description="Associated repository name")
    user_id: str = Field("", description="User who started the task")
    progress: int = Field(0, description="Progress percentage (0-100)")
    step: str = Field("", description="Current step identifier")
    status_message: str = Field("", description="Human-readable status message")
    error: str | None = Field(None, description="Error message if failed")
    created_at: str = Field("", description="When the task was created (ISO format)")
    started_at: str | None = Field(None, description="When the task started running")
    completed_at: str | None = Field(None, description="When the task completed")
    queue_position: int = Field(
        0, description="Position in queue (0 = running, 1+ = waiting)"
    )
    remote_host: str = Field(
        "", description="Host where task is running (for remote tasks)"
    )
    trajectory: list[dict] = Field(
        default_factory=list,
        description="Recent task trajectory events for debugging/progress inspection",
    )


class ActiveTasksResponse(BaseModel):
    """Response model for listing active tasks."""

    tasks: list[TaskStatusResponse] = Field(
        default_factory=list, description="List of active tasks"
    )
    total: int = Field(0, description="Total number of tasks returned")


class CancelTaskResponse(BaseModel):
    """Response model for cancel operation."""

    success: bool = Field(..., description="Whether cancellation was successful")
    task_id: str = Field(..., description="Task ID that was cancelled")
    message: str = Field("", description="Status message")


class CleanupResponse(BaseModel):
    """Response model for cleanup operation."""

    deleted_count: int = Field(0, description="Number of tasks deleted")
    message: str = Field("", description="Status message")


# ============== Helper Functions ==============


def task_state_to_response(state) -> TaskStatusResponse:
    """Convert TaskState to TaskStatusResponse."""
    return TaskStatusResponse(
        task_id=state.task_id,
        status=state.status.value
        if hasattr(state.status, "value")
        else str(state.status),
        task_type=state.task_type or "",
        repo_name=state.repo_name or "",
        user_id=state.user_id or "",
        progress=state.progress,
        step=state.step,
        status_message=state.status_message,
        error=state.error,
        created_at=state.created_at,
        started_at=state.started_at,
        completed_at=state.completed_at,
        queue_position=getattr(state, "queue_position", 0),
        remote_host=getattr(state, "remote_host", ""),
        trajectory=[
            asdict(event) if not isinstance(event, dict) else event
            for event in getattr(state, "trajectory", [])
        ],
    )


async def _fetch_remote_task_status(task_id: str, remote_host: str):
    """Fetch task status from a remote server.

    Args:
        task_id: The task ID to query
        remote_host: The remote host address

    Returns:
        TaskState from remote server, or None if fetch failed
    """
    import httpx
    from api.services.task_queue import TaskState

    try:
        # Query remote server for task status
        url = f"http://{remote_host}:8005/api/tasks/{task_id}"
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)

            if response.status_code == 200:
                data = response.json()
                # Convert response to TaskState
                status_map = {
                    "pending": TaskStatus.PENDING,
                    "running": TaskStatus.RUNNING,
                    "stalled": TaskStatus.STALLED,
                    "completed": TaskStatus.COMPLETED,
                    "failed": TaskStatus.FAILED,
                    "cancelled": TaskStatus.CANCELLED,
                }
                return TaskState(
                    task_id=data.get("task_id", task_id),
                    status=status_map.get(
                        data.get("status", "pending"), TaskStatus.PENDING
                    ),
                    task_type=data.get("task_type", ""),
                    repo_name=data.get("repo_name", ""),
                    user_id=data.get("user_id", ""),
                    progress=data.get("progress", 0),
                    step=data.get("step", ""),
                    status_message=data.get("status_message", ""),
                    error=data.get("error"),
                    created_at=data.get("created_at", ""),
                    started_at=data.get("started_at"),
                    completed_at=data.get("completed_at"),
                    queue_position=data.get("queue_position", 0),
                    remote_host="",  # Don't overwrite local remote_host
                    trajectory=data.get("trajectory", []),
                )
            else:
                logger.debug(f"Remote task status fetch failed: {response.status_code}")
                return None

    except Exception as e:
        logger.debug(f"Failed to fetch remote task status from {remote_host}: {e}")
        return None


async def _sync_remote_tasks(tasks: list, task_manager) -> list:
    """Sync status of remote tasks from their respective remote servers.

    Args:
        tasks: List of TaskState objects
        task_manager: The task manager instance

    Returns:
        Updated list of TaskState objects with synced remote statuses
    """
    import asyncio

    # Find remote tasks that need syncing
    remote_tasks = [
        t
        for t in tasks
        if t.remote_host
        and t.remote_host not in ("", "localhost", "127.0.0.1")
        and t.status
        not in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]
    ]

    if not remote_tasks:
        return tasks

    # Fetch remote statuses concurrently
    async def fetch_and_update(task):
        try:
            remote_state = await _fetch_remote_task_status(
                task.task_id, task.remote_host
            )
            if remote_state:
                # Update task with remote state
                task.status = remote_state.status
                task.progress = remote_state.progress
                task.step = remote_state.step
                task.status_message = remote_state.status_message
                task.error = remote_state.error
                task.started_at = remote_state.started_at
                task.completed_at = remote_state.completed_at
                task.queue_position = remote_state.queue_position
                task.trajectory = remote_state.trajectory
                # Save updated state
                await task_manager.store.save(task)
        except Exception as e:
            logger.debug(f"Failed to sync remote task {task.task_id}: {e}")

    # Run all fetches concurrently with a timeout
    await asyncio.gather(
        *[fetch_and_update(t) for t in remote_tasks], return_exceptions=True
    )

    return tasks


# ============== API Endpoints ==============


@router.get(
    "/active",
    response_model=ActiveTasksResponse,
    summary="List Active Tasks",
    description="List all active tasks and recently completed ones (last 5 minutes). Visible to all users.",
)
async def list_active_tasks(
    include_completed: bool = Query(
        True, description="Include recently completed tasks"
    ),
    recent_minutes: int = Query(
        5, ge=1, le=60, description="How many minutes back to include completed tasks"
    ),
    task_type: str | None = Query(None, description="Filter by task type"),
    repo_name: str | None = Query(None, description="Filter by repository name"),
    sync_remote: bool = Query(True, description="Sync status of remote tasks"),
) -> ActiveTasksResponse:
    """
    List all active tasks visible to all users.

    This endpoint is designed for multi-user environments where all users
    need visibility into ongoing operations like knowledge graph builds
    or documentation generation.

    For remote tasks, it will fetch and sync the latest status from the
    remote server (unless sync_remote=False).

    Returns:
        ActiveTasksResponse with list of tasks
    """
    try:
        task_manager = get_task_manager()
        tasks = await task_manager.list_active_tasks(
            include_recent_completed=include_completed,
            recent_minutes=recent_minutes,
        )

        # Sync remote task statuses
        if sync_remote:
            tasks = await _sync_remote_tasks(tasks, task_manager)

        # Apply filters if provided
        if task_type:
            tasks = [t for t in tasks if t.task_type == task_type]
        if repo_name:
            tasks = [t for t in tasks if t.repo_name == repo_name]

        response_tasks = [task_state_to_response(t) for t in tasks]

        return ActiveTasksResponse(tasks=response_tasks, total=len(response_tasks))

    except Exception as e:
        logger.error(f"Failed to list active tasks: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list tasks: {str(e)}")


@router.get(
    "/{task_id}",
    response_model=TaskStatusResponse,
    summary="Get Task Status",
    description="Get the current status of a specific task.",
)
async def get_task_status(task_id: str) -> TaskStatusResponse:
    """
    Get detailed status of a specific task.

    For remote tasks (running on another server), this endpoint will
    fetch the latest status from the remote server and sync it locally.

    Args:
        task_id: The unique task identifier

    Returns:
        TaskStatusResponse with current task state
    """
    try:
        task_manager = get_task_manager()
        state = await task_manager.get_task_status(task_id)

        if not state:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

        # For remote tasks that are not in terminal state, fetch latest status from remote
        if (
            state.remote_host
            and state.remote_host not in ("", "localhost", "127.0.0.1")
            and state.status
            not in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]
        ):
            try:
                remote_state = await _fetch_remote_task_status(
                    task_id, state.remote_host
                )
                if remote_state:
                    # Update local state with remote state
                    state.status = remote_state.status
                    state.progress = remote_state.progress
                    state.step = remote_state.step
                    state.status_message = remote_state.status_message
                    state.error = remote_state.error
                    state.started_at = remote_state.started_at
                    state.completed_at = remote_state.completed_at
                    state.queue_position = remote_state.queue_position
                    state.trajectory = remote_state.trajectory
                    # Preserve local remote_host info
                    # Save updated state locally
                    await task_manager.store.save(state)
            except Exception as e:
                logger.warning(f"Failed to sync remote task status: {e}")
                # Continue with local state if remote fetch fails

        return task_state_to_response(state)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get task status: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get task status: {str(e)}"
        )


@router.post(
    "/{task_id}/cancel",
    response_model=CancelTaskResponse,
    summary="Cancel Task",
    description="Cancel a running or pending task.",
)
async def cancel_task(
    task_id: str,
    from_remote: bool = Query(
        False, description="Internal: request from remote server"
    ),
) -> CancelTaskResponse:
    """
    Cancel a running or pending task.

    Args:
        task_id: The unique task identifier
        from_remote: If True, this is a cancel request from another server (internal use)

    Returns:
        CancelTaskResponse indicating success or failure
    """
    try:
        task_manager = get_task_manager()

        # Check if task exists
        state = await task_manager.get_task_status(task_id)
        if not state:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

        # Check if task can be cancelled
        # In shared filesystem environments, from_remote=True means this is a proxied request
        # and the file status may have been updated by the proxy server already.
        # We still need to try to stop the actual task.
        if state.status in [
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        ]:
            if not from_remote:
                return CancelTaskResponse(
                    success=False,
                    task_id=task_id,
                    message=f"Task already in terminal state: {state.status.value}",
                )
            # from_remote=True: continue to try stopping the actual task

        # Attempt to cancel
        cancelled = await task_manager.cancel_task(task_id, from_remote=from_remote)

        if cancelled:
            logger.info(f"Task cancelled: {task_id}")
            return CancelTaskResponse(
                success=True, task_id=task_id, message="Task has been cancelled"
            )
        else:
            # Task might have completed between check and cancel
            return CancelTaskResponse(
                success=False,
                task_id=task_id,
                message="Task could not be cancelled (may have already completed)",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to cancel task: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to cancel task: {str(e)}")


@router.delete(
    "/cleanup",
    response_model=CleanupResponse,
    summary="Cleanup Old Tasks",
    description="Remove old completed/failed/cancelled tasks from storage.",
)
async def cleanup_old_tasks(
    max_age_hours: int = Query(
        24, ge=1, le=168, description="Delete tasks older than this many hours"
    ),
) -> CleanupResponse:
    """
    Clean up old tasks from storage.

    This helps prevent the task store from growing indefinitely.

    Args:
        max_age_hours: Tasks older than this will be deleted (default: 24 hours)

    Returns:
        CleanupResponse with deletion count
    """
    try:
        task_manager = get_task_manager()
        deleted_count = await task_manager.cleanup_old_tasks(
            max_age_hours=max_age_hours
        )

        return CleanupResponse(
            deleted_count=deleted_count,
            message=f"Deleted {deleted_count} old tasks (older than {max_age_hours} hours)",
        )

    except Exception as e:
        logger.error(f"Failed to cleanup tasks: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to cleanup tasks: {str(e)}"
        )


@router.post(
    "/cleanup-zombies",
    response_model=CleanupResponse,
    summary="Force Cleanup Zombie Tasks",
    description="Force cleanup of zombie tasks (tasks marked as running/pending but have no actual execution process).",
)
async def cleanup_zombie_tasks() -> CleanupResponse:
    """
    Force cleanup of zombie tasks.

    Zombie tasks are tasks that are marked as RUNNING or PENDING in the file system,
    but have no corresponding asyncio.Task running. This typically happens after:
    - Server restart
    - Server crash
    - Task process killed unexpectedly

    This endpoint will:
    1. Find all RUNNING/PENDING tasks
    2. Check if they have a corresponding asyncio.Task
    3. Mark tasks without execution process as FAILED

    Returns:
        CleanupResponse with count of cleaned up tasks
    """
    try:
        task_manager = get_task_manager()

        # Get all tasks
        all_tasks = await task_manager.store.list()
        cleaned_count = 0

        from datetime import UTC, datetime

        now = datetime.now(UTC)

        for task in all_tasks:
            # Only check RUNNING and PENDING tasks
            if task.status not in [TaskStatus.RUNNING, TaskStatus.PENDING]:
                continue

            # Check if task has a running asyncio.Task
            has_asyncio_task = task.task_id in task_manager._running_tasks

            if not has_asyncio_task:
                # This is a zombie task - mark it as failed
                logger.warning(
                    f"Cleaning up zombie task: {task.task_id} "
                    f"(type={task.task_type}, repo={task.repo_name}, status={task.status.value})"
                )

                task.status = TaskStatus.FAILED
                task.error = "Task was interrupted (zombie task cleanup)"
                task.completed_at = now.isoformat()
                await task_manager.store.save(task)

                # Broadcast the update
                await task_manager._broadcast_task_update(task)

                cleaned_count += 1

        message = f"Cleaned up {cleaned_count} zombie task(s)"
        if cleaned_count > 0:
            logger.info(message)

        return CleanupResponse(deleted_count=cleaned_count, message=message)

    except Exception as e:
        logger.error(f"Failed to cleanup zombie tasks: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to cleanup zombie tasks: {str(e)}"
        )


@router.get(
    "/types/summary",
    summary="Get Task Type Summary",
    description="Get a summary of tasks grouped by type.",
)
async def get_task_type_summary() -> dict:
    """
    Get a summary of active tasks grouped by type.

    Returns:
        Dictionary with task counts by type
    """
    try:
        task_manager = get_task_manager()
        tasks = await task_manager.list_active_tasks(include_recent_completed=False)

        summary = {
            "graph_build": {"running": 0, "pending": 0},
            "overview_gen": {"running": 0, "pending": 0},
            "doc_gen": {"running": 0, "pending": 0},
            "git_checkout": {"running": 0, "pending": 0},
            "other": {"running": 0, "pending": 0},
        }

        for task in tasks:
            task_type = task.task_type or "other"
            if task_type not in summary:
                task_type = "other"

            if task.status == TaskStatus.RUNNING:
                summary[task_type]["running"] += 1
            elif task.status == TaskStatus.PENDING:
                summary[task_type]["pending"] += 1

        return {
            "summary": summary,
            "total_active": sum(s["running"] + s["pending"] for s in summary.values()),
        }

    except Exception as e:
        logger.error(f"Failed to get task summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get summary: {str(e)}")


class TaskEvent(BaseModel):
    """Task event model from Redis Stream."""

    id: str = Field(..., description="Redis Stream event ID")
    task_id: str = Field(..., description="Task ID")
    status: str = Field(..., description="Task status at time of event")
    timestamp: str = Field(..., description="Event timestamp (Unix timestamp)")
    task: dict = Field(..., description="Full task state at time of event")


class EventsResponse(BaseModel):
    """Response model for events endpoint."""

    events: list[TaskEvent] = Field(
        default_factory=list, description="List of task events"
    )
    count: int = Field(0, description="Number of events returned")
    last_id: str = Field("0", description="Last event ID (for next request)")


@router.get(
    "/events",
    response_model=EventsResponse,
    summary="Get Task Events",
    description="Get task events from Redis Stream for compensation (used when WebSocket reconnects).",
)
async def get_task_events(
    since: str = Query("0", description="Last event ID received (Redis Stream ID)"),
    limit: int = Query(
        100, ge=1, le=1000, description="Maximum number of events to return"
    ),
) -> EventsResponse:
    """
    Get task events from Redis Stream for compensation.

    This endpoint is used by the frontend to fetch missed events after
    WebSocket disconnect. It reads from the Redis Stream which contains
    all task state changes.

    Args:
        since: Last event ID received (Redis Stream ID, default "0" for all events)
        limit: Maximum number of events to return

    Returns:
        EventsResponse with list of events since the specified ID
    """
    try:
        task_manager = get_task_manager()

        # Only Redis store supports event streams
        if not task_manager.is_using_redis:
            return EventsResponse(events=[], count=0, last_id=since)

        # Get events from Redis Stream
        events = await task_manager._redis_store.get_events_since(since, limit)

        # Convert to response format
        event_responses = [
            TaskEvent(
                id=e["id"],
                task_id=e["task_id"],
                status=e["status"],
                timestamp=e["timestamp"],
                task=e["task"],
            )
            for e in events
        ]

        # Get last event ID for next request
        last_id = events[-1]["id"] if events else since

        return EventsResponse(
            events=event_responses, count=len(event_responses), last_id=last_id
        )

    except Exception as e:
        logger.error(f"Failed to get task events: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get events: {str(e)}")


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time task status updates.

    Clients connect to receive instant updates whenever any task status changes.
    This provides real-time notifications without requiring polling.

    Protocol:
    - Client sends: {"action": "subscribe"} to request updates
    - Server broadcasts: {"type": "task_update", "task": {...}} when tasks change
    - Server sends: {"type": "connected", "message": "Ready to receive updates"}
    """
    ws_manager = get_ws_manager()
    await ws_manager.connect(websocket)

    try:
        # Keep the connection open and listen for any client messages
        while True:
            # Receive message from client (for future extensibility)
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
                logger.debug(f"WebSocket received: {message}")

                # Handle different client commands
                if message.get("action") == "subscribe":
                    await websocket.send_json(
                        {
                            "type": "connected",
                            "message": "Connected to task updates stream",
                        }
                    )
            except json.JSONDecodeError:
                logger.warning("Invalid JSON received on WebSocket")
            except Exception as e:
                logger.warning(f"Error handling WebSocket message: {e}")

    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
        logger.debug("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
        try:
            await ws_manager.disconnect(websocket)
        except Exception:
            pass

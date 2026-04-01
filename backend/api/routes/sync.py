# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import json
from pathlib import Path
from typing import Any

from api.services.sync_state_store import get_sync_state_store
from api.services.task_queue import TaskType, get_task_manager
from core.git_executable import GIT
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from loguru import logger
from pydantic import BaseModel, Field

router = APIRouter()


# ============== Request/Response Models ==============


class CheckoutRequest(BaseModel):
    """Request to checkout a Git reference."""

    ref: str = Field(..., description="Branch name, tag name, or commit SHA")


class FetchRequest(BaseModel):
    """Request to fetch from a remote."""

    remote: str = Field("origin", description="Remote name to fetch from")


class PullRequest(BaseModel):
    """Request to pull from a remote."""

    remote: str = Field("origin", description="Remote name to pull from")
    branch: str | None = Field(
        None, description="Branch to pull (defaults to current branch)"
    )


class SyncStatus(BaseModel):
    """Current sync status."""

    is_watching: bool = Field(..., description="Whether file monitoring is active")
    is_processing: bool = Field(..., description="Whether currently processing updates")
    is_git_repo: bool = Field(..., description="Whether this is a Git repository")
    current_ref: str | None = Field(
        None, description="Current Git reference (branch/commit)"
    )
    current_ref_type: str | None = Field(
        None, description="Type of current reference: branch, tag, or commit"
    )
    pending_changes: int = Field(
        0, description="Number of changes waiting to be processed"
    )
    latest_result: dict[str, Any] | None = Field(
        None, description="Result of latest update"
    )
    watching_task_id: str | None = Field(
        None, description="Background task ID for file monitoring"
    )
    built_commit_sha: str | None = Field(
        None,
        description="Commit SHA the knowledge graph was last built against",
    )


class UpdateResultResponse(BaseModel):
    """Result of an update operation."""

    added: int = Field(0, description="Number of files added")
    modified: int = Field(0, description="Number of files modified")
    deleted: int = Field(0, description="Number of files deleted")
    added_files: list[str] = Field(
        default_factory=list, description="List of added file paths"
    )
    modified_files: list[str] = Field(
        default_factory=list, description="List of modified file paths"
    )
    deleted_files: list[str] = Field(
        default_factory=list, description="List of deleted file paths"
    )
    calls_created: int = Field(0, description="Number of new CALLS relationships")
    calls_rebuilt: int = Field(0, description="Number of rebuilt CALLS relationships")
    embeddings_generated: int = Field(
        0, description="Number of embeddings generated for new nodes"
    )
    duration_ms: float = Field(0.0, description="Duration in milliseconds")
    errors: list[str] = Field(default_factory=list, description="Error messages")
    total_changes: int = Field(0, description="Total number of file changes")
    success: bool = Field(
        ..., description="Whether the update completed without errors"
    )
    timestamp: str | None = Field(
        None, description="ISO timestamp of when the update completed"
    )


class GitRefResponse(BaseModel):
    """Git reference information."""

    name: str = Field(..., description="Reference name")
    ref_type: str = Field(..., description="Type: branch, tag, or commit")
    commit_sha: str = Field(..., description="Full commit SHA")
    short_sha: str = Field(..., description="Short (7-char) commit SHA")
    is_current: bool = Field(False, description="Whether currently checked out")


class CheckoutTaskResponse(BaseModel):
    """Response for checkout operation (background task)."""

    task_id: str = Field(..., description="Background task ID")
    status: str = Field(
        ..., description="Task status: pending, running, completed, failed, cancelled"
    )
    message: str = Field(..., description="Status message")


class PendingFileInfo(BaseModel):
    """A file pending sync."""

    path: str = Field(..., description="Relative file path")
    action: str = Field(..., description="Change type: add, modify, delete")


class SyncHistoryItem(BaseModel):
    """A single sync history entry."""

    timestamp: str = Field(..., description="ISO timestamp")
    total_changes: int = Field(0)
    added: int = Field(0)
    modified: int = Field(0)
    deleted: int = Field(0)
    added_files: list[str] = Field(
        default_factory=list, description="List of added file paths"
    )
    modified_files: list[str] = Field(
        default_factory=list, description="List of modified file paths"
    )
    deleted_files: list[str] = Field(
        default_factory=list, description="List of deleted file paths"
    )
    duration_ms: float = Field(0.0)
    success: bool = Field(True)
    errors: list[str] = Field(default_factory=list)


# ============== Sync WebSocket Manager ==============


class SyncWebSocketManager:
    """Manages WebSocket connections for real-time sync updates."""

    def __init__(self):
        self.active_connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self.active_connections.add(websocket)
        logger.debug(f"Sync WebSocket connected. Total: {len(self.active_connections)}")

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self.active_connections.discard(websocket)
        logger.debug(
            f"Sync WebSocket disconnected. Total: {len(self.active_connections)}"
        )

    async def broadcast(self, message: dict) -> None:
        if not self.active_connections:
            return
        async with self._lock:
            connections = list(self.active_connections)

        disconnected = []
        for conn in connections:
            try:
                await conn.send_json(message)
            except Exception:
                disconnected.append(conn)

        if disconnected:
            async with self._lock:
                for conn in disconnected:
                    self.active_connections.discard(conn)


_sync_ws_manager = SyncWebSocketManager()


def get_sync_ws_manager() -> SyncWebSocketManager:
    """Get the global sync WebSocket manager."""
    return _sync_ws_manager


async def broadcast_sync_status(project_name: str, sync_mgr: Any) -> None:
    """Broadcast sync status update to all connected WebSocket clients."""
    ws_mgr = get_sync_ws_manager()
    if not ws_mgr.active_connections:
        return

    status = sync_mgr.get_status()
    current_file = sync_mgr.current_processing_file

    await ws_mgr.broadcast(
        {
            "type": "sync_status",
            "project_name": project_name,
            "data": {
                **status,
                "current_file": current_file,
            },
        }
    )


async def broadcast_sync_progress(
    project_name: str, current_file: str | None, progress: int
) -> None:
    """Broadcast sync progress to WebSocket clients."""
    ws_mgr = get_sync_ws_manager()
    if not ws_mgr.active_connections:
        return

    await ws_mgr.broadcast(
        {
            "type": "sync_progress",
            "project_name": project_name,
            "data": {
                "current_file": current_file,
                "progress": progress,
            },
        }
    )


async def broadcast_sync_complete(project_name: str, result_data: dict) -> None:
    """Broadcast sync completion to WebSocket clients."""
    ws_mgr = get_sync_ws_manager()
    if not ws_mgr.active_connections:
        return

    await ws_mgr.broadcast(
        {
            "type": "sync_complete",
            "project_name": project_name,
            "data": result_data,
        }
    )


# ============== Persistent sync flag ==============


def _set_sync_enabled(project_name: str, enabled: bool) -> None:
    """Persist sync_enabled flag on the Project node in Memgraph.

    This survives server restarts so the frontend can always show
    which repos have incremental sync turned on.
    """
    try:
        from api.routes.graph import get_ingestor

        with get_ingestor(for_write=True) as ingestor:
            ingestor._execute_query(
                "MATCH (p:Project {name: $name}) SET p.sync_enabled = $enabled",
                {"name": project_name, "enabled": enabled},
            )
    except Exception as e:
        logger.warning(f"Failed to persist sync_enabled for {project_name}: {e}")


def _check_has_graph(project_name: str) -> bool:
    """Check if a project has an existing knowledge graph (any nodes).

    Returns:
        True if graph has at least one node, False otherwise.
    """
    try:
        from api.routes.graph import get_ingestor

        with get_ingestor() as ingestor:
            stats = ingestor.get_project_stats(project_name)
            return stats.get("node_count", 0) > 0
    except Exception as e:
        logger.warning(f"Failed to check graph existence for {project_name}: {e}")
        return True  # Assume graph exists on error to avoid unnecessary rebuilds


def _get_built_commit_sha(project_name: str, repo_path: Path | str | None = None) -> str | None:
    """Read the built_commit_sha from the incremental build state file."""
    try:
        from graph.sync.cache_registry import get_cache_registry

        # If no repo_path, try to get from sync manager
        if not repo_path and project_name in _sync_managers:
            repo_path = _sync_managers[project_name].repo_path
        if not repo_path:
            return None

        registry = get_cache_registry()
        state_dir = registry.get_cache_dir(project_name, repo_path)
        state_file = state_dir / f"{project_name}_state.json"
        if state_file.exists():
            with open(state_file) as f:
                data = json.load(f)
            return data.get("metadata", {}).get("built_commit_sha")
    except Exception as e:
        logger.debug(f"Failed to read built_commit_sha for {project_name}: {e}")
    return None


# ============== In-Memory Storage ==============
# Note: _sync_managers are still in-memory per process because they contain
# the actual file watchers. The watching task state is now in Redis for
# multi-worker visibility via SyncStateStore.

_sync_managers: dict[str, Any] = {}

# _watching_tasks has been replaced by Redis-based SyncStateStore
# for multi-worker support. See api/services/sync_state_store.py

# Global parser cache - loaded once and shared across all sync managers
_parsers_cache: tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None = None


def _get_parsers():
    """Get or initialize global parsers cache (shared across all sync managers)."""
    global _parsers_cache
    if _parsers_cache is None:
        from parser.loader import load_parsers

        logger.info("Loading parsers (global cache)...")
        _parsers_cache = load_parsers(return_languages=True)
        logger.info(
            f"Loaded {len(_parsers_cache[0])} parsers: {list(_parsers_cache[0].keys())}"
        )
    return _parsers_cache


async def _run_git_checkout_task(
    task_id: str,
    project_name: str,
    ref: str,
) -> None:
    """Background task function for git checkout with graph update.

    Args:
        task_id: Background task ID
        project_name: Project name
        ref: Git reference to checkout
    """
    from concurrent.futures import ThreadPoolExecutor

    task_manager = get_task_manager()
    sync_mgr = _sync_managers.get(project_name)

    if sync_mgr is None:
        await task_manager.update_task(
            task_id,
            status="failed",
            error=f"Sync manager for '{project_name}' not found",
        )
        return

    # Get the main event loop for thread-safe callback
    main_loop = asyncio.get_event_loop()

    # Track latest progress for fallback polling
    latest_progress = {"value": 0, "step": "", "message": ""}

    # Define step names for better UI display
    STEP_NAMES = {
        "computing_diff": "Computing changes",
        "git_reset": "Resetting local changes",
        "git_checkout": "Switching branch",
        "full_rebuild": "Full graph rebuild",
        "updating_graph": "Updating graph",
        "rebuilding_cache": "Rebuilding cache",
        "loading_registry": "Loading functions",
        "deleting": "Removing files",
        "adding": "Adding files",
        "modifying": "Modifying files",
        "rebuilding_calls": "Updating calls",
        "embedding": "Generating embeddings",
        "flushing": "Saving to database",
        "complete": "Complete",
    }

    # Set up progress callback for task updates (thread-safe)
    def progress_callback(progress: int, step: str, message: str):
        """Update task progress from within the sync operation (thread-safe)."""
        nonlocal latest_progress

        # Map step to a readable name
        step_display = STEP_NAMES.get(step, step)

        latest_progress = {
            "value": progress,
            "step": step,
            "step_display": step_display,
            "message": message,
        }
        try:

            async def _update():
                await task_manager.update_task(
                    task_id,
                    progress=progress,
                    step=step_display,  # Send readable step name to frontend
                    status_message=message,
                )

            # Use run_coroutine_threadsafe for cross-thread async calls
            future = asyncio.run_coroutine_threadsafe(_update(), main_loop)
            # Wait for completion with timeout (don't block too long)
            try:
                future.result(timeout=0.1)
            except Exception:
                # If timeout or error, the progress is stored in latest_progress
                # and will be picked up by the next successful update
                pass
        except Exception as e:
            logger.debug(f"Progress update deferred: {e}")

    # Set progress callback on the sync manager
    sync_mgr.set_progress_callback(progress_callback)

    # Create executor (don't use with context, we manage lifecycle manually)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="git_checkout")

    try:
        await task_manager.update_task(
            task_id, status="running", status_message=f"Checking out '{ref}'..."
        )

        # Run checkout in thread pool executor (blocking operation)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(executor, lambda: sync_mgr.checkout(ref))

        # Check for errors and update final status
        if result.errors:
            await task_manager.update_task(
                task_id,
                status="failed",
                progress=100,
                step="Complete",
                status_message="Checkout completed with errors",
                error=result.errors[0] if result.errors else "Unknown error",
                result={
                    "added": result.added,
                    "modified": result.modified,
                    "deleted": result.deleted,
                    "calls_rebuilt": result.calls_rebuilt,
                    "duration_ms": result.duration_ms,
                },
            )
        else:
            await task_manager.update_task(
                task_id,
                status="completed",
                progress=100,
                step="Complete",
                status_message=f"Checkout complete: {result.added} added, {result.modified} modified, {result.deleted} deleted",
                result={
                    "added": result.added,
                    "modified": result.modified,
                    "deleted": result.deleted,
                    "calls_rebuilt": result.calls_rebuilt,
                    "duration_ms": result.duration_ms,
                },
            )

    except asyncio.CancelledError:
        await task_manager.update_task(
            task_id, status="cancelled", error="Task was cancelled"
        )
    except Exception as e:
        logger.error(f"Git checkout task failed: {e}", exc_info=True)
        await task_manager.update_task(
            task_id, status="failed", step="Error", error=str(e)
        )
    finally:
        # Shutdown executor
        executor.shutdown(wait=True)
        # Clear progress callback
        sync_mgr.set_progress_callback(None)


def get_sync_manager(
    project_name: str,
    repo_path: Path | None = None,
    skip_embeddings: bool | None = None,
    track_variables: bool = True,
    subdirs: list[str] | None = None,
    async_embeddings: bool = False,
    use_polling: bool = False,
):
    """Get or create a sync manager for a project.

    Args:
        project_name: Project name
        repo_path: Repository path (required for first access)
        skip_embeddings: Skip embedding generation for new nodes (uses config default if None)
        track_variables: Track module/class-level variables in the graph
        subdirs: Optional list of subdirectory names to monitor
        async_embeddings: Generate embeddings asynchronously in background
        use_polling: Use polling observer instead of inotify (for NFS/network filesystems)

    Returns:
        RepoSyncManager instance

    Raises:
        HTTPException: If sync manager not found and no repo_path provided
    """
    from core.config import settings
    from graph.service import MemgraphIngestor
    from graph.sync import RepoSyncManager

    if project_name in _sync_managers:
        return _sync_managers[project_name]

    # Auto-recover repo_path from Memgraph when not provided
    if repo_path is None:
        try:
            from api.routes.graph import get_ingestor

            with get_ingestor() as ingestor:
                recovered = ingestor.get_project_path(project_name)
                if recovered and Path(recovered).is_dir():
                    repo_path = Path(recovered)
                    logger.info(
                        f"Auto-recovered repo_path for '{project_name}' from Memgraph: {recovered}"
                    )
        except Exception as e:
            logger.debug(f"Failed to auto-recover repo_path for '{project_name}': {e}")

    if repo_path is None:
        raise HTTPException(
            status_code=404,
            detail=f"Sync manager for '{project_name}' not found. Provide repo_path to initialize.",
        )

    # Get parsers from global cache (loaded once and shared)
    parsers, queries, language_objects = _get_parsers()
    logger.debug(f"Using cached parsers for sync manager '{project_name}'")

    # Create ingestor
    ingestor = MemgraphIngestor(
        host=settings.MEMGRAPH_HOST,
        port=settings.MEMGRAPH_PORT,
    )

    # Start ingestor connection
    ingestor.__enter__()

    # Use config default if not specified
    if skip_embeddings is None:
        skip_embeddings = getattr(settings, "SYNC_SKIP_EMBEDDINGS", False)

    # Create sync manager
    sync_mgr = RepoSyncManager(
        repo_path=repo_path,
        project_name=project_name,
        ingestor=ingestor,
        parsers=parsers,
        queries=queries,
        language_objects=language_objects,
        auto_start=False,  # Don't auto-start, let user control via API
        skip_embeddings=skip_embeddings,
        track_variables=track_variables,
        subdirs=set(subdirs) if subdirs else None,
        async_embeddings=async_embeddings,
        use_polling=use_polling,
    )

    _sync_managers[project_name] = sync_mgr

    # Set up WebSocket status change callback
    def _status_change_callback(mgr):
        """Trigger async broadcast from sync thread."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    broadcast_sync_status(project_name, mgr), loop
                )
        except RuntimeError:
            pass  # No event loop available

    sync_mgr.set_on_status_change(_status_change_callback)

    logger.info(
        f"Created sync manager for '{project_name}' (skip_embeddings={skip_embeddings}, track_variables={track_variables})"
    )

    return sync_mgr


# ============== Sync Control Endpoints ==============


@router.post("/{project_name}/start", response_model=dict)
async def start_watching(
    project_name: str,
    repo_path: str | None = Query(
        None, description="Repository path (required for first-time initialization)"
    ),
    skip_embeddings: bool = Query(
        False, description="Skip embedding generation for new nodes"
    ),
    async_embeddings: bool = Query(
        False,
        description="Generate embeddings asynchronously in background (recommended)",
    ),
    track_variables: bool = Query(
        True, description="Track module/class-level variables in the graph"
    ),
    auto_watch: bool = Query(
        True,
        description="Whether to automatically start file monitoring after initialization",
    ),
    subdirs: str | None = Query(
        None,
        description="Comma-separated list of subdirectory names to monitor (e.g. 'backend,frontend')",
    ),
    initial_sync: bool = Query(
        True,
        description="Perform initial sync to detect offline changes when starting monitoring",
    ),
    use_polling: bool = Query(
        False,
        description="Use polling observer instead of inotify (for NFS/network filesystems)",
    ),
):
    """Initialize sync manager and optionally start file monitoring.

    Args:
        project_name: Project name
        repo_path: Repository path (required if sync manager not initialized)
        skip_embeddings: Skip embedding generation for new nodes
        async_embeddings: Generate embeddings asynchronously in background (recommended)
        track_variables: Track module/class-level variables
        auto_watch: If True (default), start file monitoring immediately.
                   If False, only initialize the sync manager without watching.
        subdirs: Comma-separated list of subdirectory names to monitor.
                 If not provided, monitors the entire repo_path.
        initial_sync: If True (default), perform initial sync to detect offline changes
                     when starting monitoring.
        use_polling: If True, use polling observer instead of inotify.
                    Enable this for NFS, CIFS, or network filesystems.

    Returns:
        Status message with optional task_id for tracking and initial_sync result
    """
    subdirs_list = (
        [s.strip() for s in subdirs.split(",") if s.strip()] if subdirs else None
    )

    sync_mgr = await asyncio.to_thread(
        get_sync_manager,
        project_name,
        Path(repo_path) if repo_path else None,
        skip_embeddings,
        track_variables,
        subdirs_list,
        async_embeddings,
        use_polling,
    )

    if not auto_watch:
        # Just initialize, don't start watching
        return {
            "status": "initialized",
            "message": "Sync manager initialized (watching not started)",
        }

    # Get Redis state store for distributed state management
    store = get_sync_state_store()
    lock_token = None

    try:
        # Try to acquire distributed lock to prevent race conditions
        if store and store.is_connected:
            lock_token = await store.acquire_lock(project_name, ttl=30)
            if not lock_token:
                # Another request is processing this project
                raise HTTPException(
                    status_code=409,
                    detail="Another request is currently starting/stopping monitoring for this project. Please retry.",
                )

            # Check if there's already a watching task in Redis (cross-worker check)
            existing_task_id = await store.get_watching_task(project_name)
            if existing_task_id:
                # Verify the task is still running (not expired/failed)
                task_manager = get_task_manager()
                existing_task = await task_manager.get_task_status(existing_task_id)

                # Also verify sync_manager is actually watching
                # This catches the case where server restarted but Redis still has stale task
                sync_actually_running = sync_mgr.is_watching

                if (
                    existing_task
                    and existing_task.status.value in ("pending", "running")
                    and sync_actually_running
                ):
                    # Task is still active AND sync_manager is actually watching
                    return {
                        "status": "already_watching",
                        "message": "File monitoring is already active",
                        "task_id": existing_task_id,
                    }
                else:
                    # Task expired/failed OR sync_manager not actually running
                    # Clean up stale Redis entry and continue to start new watcher
                    await store.remove_watching_task(project_name)
                    if not sync_actually_running:
                        logger.info(
                            f"Cleaned up stale watching task for {project_name}: {existing_task_id} (sync_manager not running)"
                        )
                    else:
                        logger.info(
                            f"Cleaned up stale watching task for {project_name}: {existing_task_id} (task expired/failed)"
                        )

        else:
            # Fallback: Redis not available, use local sync manager state only
            if sync_mgr.is_watching:
                return {
                    "status": "already_watching",
                    "message": "File monitoring is already active (Redis unavailable)",
                    "task_id": None,
                }

        # Create a background task for tracking the watching session
        task_manager = get_task_manager()
        task_id = await task_manager.create_task(
            task_type=TaskType.SYNC_WATCHING.value,
            repo_name=project_name,
            initial_message=f"File monitoring started for {project_name}",
        )

        # Start watching with optional initial sync (runs in background)
        await asyncio.to_thread(sync_mgr.start_watching, initial_sync)

        # Store the task ID in Redis for multi-worker visibility
        if store and store.is_connected:
            await store.set_watching_task(project_name, task_id)

        # Mark task as running (it will stay running until stop is called)
        await task_manager.update_task(
            task_id,
            status="running",
            progress=0,
            step="watching",
            status_message=f"Monitoring file changes in {repo_path or project_name}",
        )

        # Persist sync_enabled flag for frontend display & restart recovery
        await asyncio.to_thread(_set_sync_enabled, project_name, True)

        response = {
            "status": "started",
            "message": "File monitoring started",
            "task_id": task_id,
        }
        if initial_sync:
            response["initial_sync"] = "started_in_background"
        return response

    finally:
        # Always release the lock if we acquired it
        if store and store.is_connected and lock_token:
            await store.release_lock(project_name, lock_token)


@router.post("/{project_name}/stop", response_model=dict)
async def stop_watching(project_name: str):
    """Stop file monitoring for a project.

    Args:
        project_name: Project name

    Returns:
        Status message with task_id if available
    """
    sync_mgr = get_sync_manager(project_name)

    # Get Redis state store
    store = get_sync_state_store()

    if not sync_mgr.is_watching:
        # Sync manager is not watching, but still clean up Redis if there's a stale task
        # This handles the case where server restarted but Redis still has old task record
        if store and store.is_connected:
            task_id = await store.get_watching_task(project_name)
            if task_id:
                await store.remove_watching_task(project_name)
                logger.info(f"Cleaned up stale Redis task during stop: {task_id}")
                return {
                    "status": "not_watching",
                    "message": "File monitoring is not active (cleaned up stale Redis state)",
                    "task_id": task_id,
                }
        return {"status": "not_watching", "message": "File monitoring is not active"}

    lock_token = None

    try:
        # Try to acquire distributed lock
        if store and store.is_connected:
            lock_token = await store.acquire_lock(project_name, ttl=30)
            if not lock_token:
                raise HTTPException(
                    status_code=409,
                    detail="Another request is currently starting/stopping monitoring for this project. Please retry.",
                )

        # Stop watching
        sync_mgr.stop_watching()

        # Get task ID and remove from Redis
        task_id = None
        if store and store.is_connected:
            task_id = await store.get_watching_task(project_name)
            await store.remove_watching_task(project_name)

        # Update task status to completed
        if task_id:
            task_manager = get_task_manager()
            await task_manager.update_task(
                task_id,
                status="completed",
                progress=100,
                step="stopped",
                status_message="File monitoring stopped",
            )

        # Clear sync_enabled flag
        await asyncio.to_thread(_set_sync_enabled, project_name, False)

        return {
            "status": "stopped",
            "message": "File monitoring stopped",
            "task_id": task_id,
        }

    finally:
        # Always release the lock if we acquired it
        if store and store.is_connected and lock_token:
            await store.release_lock(project_name, lock_token)


@router.get("/{project_name}/task", response_model=dict)
async def get_watching_task(project_name: str):
    """Get the background task associated with file monitoring.

    Args:
        project_name: Project name

    Returns:
        Task information if available
    """
    # Try to get task ID from Redis first
    store = get_sync_state_store()
    task_id = None

    if store and store.is_connected:
        task_id = await store.get_watching_task(project_name)

    if not task_id:
        return {
            "status": "no_task",
            "message": "No watching task found for this project",
        }

    task_manager = get_task_manager()
    task = await task_manager.get_task_status(task_id)
    if not task:
        # Task was cleaned up, remove from Redis
        if store and store.is_connected:
            await store.remove_watching_task(project_name)
        return {"status": "task_expired", "message": "Watching task has expired"}

    return {
        "status": "ok",
        "task_id": task_id,
        "task": task.to_dict() if hasattr(task, "to_dict") else task,
    }


@router.post("/{project_name}/now")
async def sync_now(
    project_name: str,
    repo_path: str | None = Query(
        None, description="Repository path (required for first-time initialization)"
    ),
    skip_embeddings: bool = Query(
        False, description="Skip embedding generation for new nodes"
    ),
):
    """Manually trigger sync (detect all changes and update graph).

    If no knowledge graph exists yet, automatically triggers a full graph build
    first (as a background task), then syncs. Returns CheckoutTaskResponse in
    that case so the frontend can poll for progress.

    Args:
        project_name: Project name
        repo_path: Repository path (required if sync manager not initialized)
        skip_embeddings: Skip embedding generation for new nodes

    Returns:
        UpdateResultResponse (if graph exists) or CheckoutTaskResponse (if building graph first)
    """
    sync_mgr = await asyncio.to_thread(
        get_sync_manager,
        project_name,
        Path(repo_path) if repo_path else None,
        skip_embeddings,
    )

    # Check if graph exists — if not, trigger full build as background task
    has_graph = await asyncio.to_thread(_check_has_graph, project_name)
    if not has_graph:
        resolved_repo_path = Path(repo_path) if repo_path else getattr(sync_mgr, "repo_path", None)
        if not resolved_repo_path:
            raise HTTPException(
                status_code=400,
                detail="No knowledge graph exists and repo_path is required to build one.",
            )

        task_manager = get_task_manager()
        task_id = await task_manager.create_task(
            task_type=TaskType.GRAPH_BUILD,
            repo_name=project_name,
            initial_message="Building knowledge graph (no existing graph found)...",
        )

        queue_position = await task_manager.run_task(
            task_id,
            _run_sync_with_build_task,
            project_name,
            resolved_repo_path,
            skip_embeddings,
        )

        return CheckoutTaskResponse(
            task_id=task_id,
            status="pending" if queue_position > 0 else "running",
            message="No knowledge graph found — building graph first, then syncing",
        )

    result = await asyncio.to_thread(sync_mgr.sync_now)

    result_response = UpdateResultResponse(
        added=result.added,
        modified=result.modified,
        deleted=result.deleted,
        added_files=getattr(result, "added_files", []),
        modified_files=getattr(result, "modified_files", []),
        deleted_files=getattr(result, "deleted_files", []),
        calls_created=result.calls_created,
        calls_rebuilt=result.calls_rebuilt,
        duration_ms=result.duration_ms,
        errors=result.errors,
        total_changes=result.total_changes,
        success=result.success,
        embeddings_generated=getattr(result, "embeddings_generated", 0),
        timestamp=getattr(result, "timestamp", None),
    )

    # Broadcast completion via WebSocket
    await broadcast_sync_complete(
        project_name,
        {
            "added": result.added,
            "modified": result.modified,
            "deleted": result.deleted,
            "added_files": getattr(result, "added_files", []),
            "modified_files": getattr(result, "modified_files", []),
            "deleted_files": getattr(result, "deleted_files", []),
            "total_changes": result.total_changes,
            "duration_ms": result.duration_ms,
            "success": result.success,
            "timestamp": getattr(result, "timestamp", ""),
        },
    )

    return result_response


@router.get("/{project_name}/status", response_model=SyncStatus)
async def get_sync_status(project_name: str):
    """Get current sync status for a project.

    Args:
        project_name: Project name

    Returns:
        Current sync status
    """
    # First check if there's a watching task in Redis (works across workers)
    watching_task_id = None
    store = get_sync_state_store()
    if store and store.is_connected:
        watching_task_id = await store.get_watching_task(project_name)

    # Read built_commit_sha from incremental state file (cheap file read)
    built_commit_sha = _get_built_commit_sha(project_name)

    # Try to get sync manager from current worker
    if project_name in _sync_managers:
        sync_mgr = _sync_managers[project_name]
        status = sync_mgr.get_status()

        # In multi-worker deployments, this worker may hold a stale local
        # manager while another worker owns the active watcher. Prefer the
        # distributed watching task signal over a stale local "not watching".
        if not status["is_watching"] and watching_task_id:
            return SyncStatus(
                is_watching=True,
                is_processing=False,
                is_git_repo=status["is_git_repo"],
                current_ref=status.get("current_ref"),
                current_ref_type=status.get("current_ref_type"),
                pending_changes=status["pending_changes"],
                latest_result=None,
                watching_task_id=watching_task_id,
                built_commit_sha=built_commit_sha,
            )

        latest_result = None
        if sync_mgr.latest_update_result:
            latest_result = {
                "total_changes": sync_mgr.latest_update_result.total_changes,
                "success": sync_mgr.latest_update_result.success,
                "duration_ms": sync_mgr.latest_update_result.duration_ms,
                "added_files": getattr(
                    sync_mgr.latest_update_result, "added_files", []
                ),
                "modified_files": getattr(
                    sync_mgr.latest_update_result, "modified_files", []
                ),
                "deleted_files": getattr(
                    sync_mgr.latest_update_result, "deleted_files", []
                ),
            }

        return SyncStatus(
            is_watching=status["is_watching"],
            is_processing=status["is_processing"],
            is_git_repo=status["is_git_repo"],
            current_ref=status.get("current_ref"),
            current_ref_type=status.get("current_ref_type"),
            pending_changes=status["pending_changes"],
            latest_result=latest_result,
            watching_task_id=watching_task_id,
            built_commit_sha=built_commit_sha,
        )

    # Sync manager not in this worker - check Redis for watching task
    if watching_task_id:
        # There's a watching task in Redis, so sync is running in another worker
        return SyncStatus(
            is_watching=True,
            is_processing=False,  # Can't know from here
            is_git_repo=True,  # Assume true
            current_ref=None,
            current_ref_type=None,
            pending_changes=0,
            latest_result=None,
            watching_task_id=watching_task_id,
            built_commit_sha=built_commit_sha,
        )

    # No sync manager and no watching task - not watching
    return SyncStatus(
        is_watching=False,
        is_processing=False,
        is_git_repo=False,
        current_ref=None,
        current_ref_type=None,
        pending_changes=0,
        latest_result=None,
        watching_task_id=None,
        built_commit_sha=built_commit_sha,
    )


# ============== Git Operation Endpoints ==============


def _get_or_init_sync_manager(project_name: str):
    """Get sync manager, auto-initializing from graph DB if needed."""
    try:
        return get_sync_manager(project_name)
    except HTTPException:
        pass
    # Sync manager not initialized — try to find repo path from graph DB
    from api.routes.graph import get_ingestor

    repo_path = None
    try:
        with get_ingestor() as ingestor:
            project_path = ingestor.get_project_path(project_name)
            if project_path:
                repo_path = Path(project_path)
    except Exception:
        pass
    if not repo_path or not repo_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Repository path for '{project_name}' not found",
        )
    return get_sync_manager(project_name, repo_path=repo_path)

@router.get("/{project_name}/git/branches", response_model=list[GitRefResponse])
async def list_branches(
    project_name: str,
    include_remote: bool = Query(False, description="Include remote branches"),
):
    """List all Git branches for a project.

    Args:
        project_name: Project name
        include_remote: Whether to include remote branches

    Returns:
        List of GitRef objects
    """
    sync_mgr = _get_or_init_sync_manager(project_name)

    if not sync_mgr.is_git_repo:
        raise HTTPException(status_code=400, detail="Not a Git repository")

    branches = sync_mgr.list_branches(include_remote=include_remote)

    return [
        GitRefResponse(
            name=ref.name,
            ref_type=ref.ref_type,
            commit_sha=ref.commit_sha,
            short_sha=ref.short_sha,
            is_current=ref.is_current,
        )
        for ref in branches
    ]


@router.get("/{project_name}/git/tags", response_model=list[GitRefResponse])
async def list_tags(project_name: str):
    """List all Git tags for a project.

    Args:
        project_name: Project name

    Returns:
        List of GitRef objects
    """
    sync_mgr = _get_or_init_sync_manager(project_name)

    if not sync_mgr.is_git_repo:
        raise HTTPException(status_code=400, detail="Not a Git repository")

    tags = sync_mgr.list_tags()

    return [
        GitRefResponse(
            name=ref.name,
            ref_type=ref.ref_type,
            commit_sha=ref.commit_sha,
            short_sha=ref.short_sha,
            is_current=ref.is_current,
        )
        for ref in tags
    ]


@router.get("/{project_name}/git/current", response_model=GitRefResponse | None)
async def get_current_ref(project_name: str):
    """Get the currently checked-out Git reference.

    Args:
        project_name: Project name

    Returns:
        Current GitRef or None
    """
    sync_mgr = get_sync_manager(project_name)

    if not sync_mgr.is_git_repo:
        raise HTTPException(status_code=400, detail="Not a Git repository")

    ref = sync_mgr.get_current_ref()

    if ref is None:
        return None

    return GitRefResponse(
        name=ref.name,
        ref_type=ref.ref_type,
        commit_sha=ref.commit_sha,
        short_sha=ref.short_sha,
        is_current=ref.is_current,
    )


@router.post("/{project_name}/git/fetch", response_model=dict)
async def fetch_remote(project_name: str, request: FetchRequest):
    """Fetch updates from a remote Git repository.

    Args:
        project_name: Project name
        request: Fetch request with remote name

    Returns:
        Status message
    """
    sync_mgr = get_sync_manager(project_name)

    if not sync_mgr.is_git_repo:
        raise HTTPException(status_code=400, detail="Not a Git repository")

    sync_mgr.fetch_remote(request.remote)

    return {"status": "fetched", "message": f"Fetched from remote '{request.remote}'"}


async def _run_sync_with_build_task(
    task_id: str, project_name: str, repo_path: Path, skip_embeddings: bool = False
):
    """Background task: build graph first, then run incremental sync.

    Used when sync_now is called but no graph exists yet.
    """
    from api.routes.graph import refresh_graph_task

    task_manager = get_task_manager()

    try:
        # Phase 1: Build graph
        await task_manager.update_task(
            task_id,
            status="running",
            progress=0,
            step="Building knowledge graph",
            status_message="No existing graph found — building from scratch...",
        )

        await refresh_graph_task(
            task_id, project_name, repo_path, fast_mode=skip_embeddings
        )

        # Phase 2: Sync (quick — graph was just built, so likely no changes)
        await task_manager.update_task(
            task_id,
            progress=97,
            step="Syncing",
            status_message="Running incremental sync...",
        )

        sync_mgr = get_sync_manager(project_name)
        result = await asyncio.to_thread(sync_mgr.sync_now)

        await task_manager.update_task(
            task_id,
            status="completed",
            progress=100,
            step="Complete",
            status_message=f"Graph built and synced: {result.added} added, {result.modified} modified, {result.deleted} deleted",
            result={
                "added": result.added,
                "modified": result.modified,
                "deleted": result.deleted,
                "duration_ms": result.duration_ms,
            },
        )

    except asyncio.CancelledError:
        await task_manager.update_task(
            task_id, status="cancelled", error="Task was cancelled"
        )
    except Exception as e:
        logger.error(f"Sync-with-build task failed: {e}", exc_info=True)
        await task_manager.update_task(
            task_id, status="failed", step="Error", error=str(e)
        )


async def _run_git_pull_task(task_id: str, project_name: str, remote: str, branch: str | None):
    """Background task for git pull operation.

    If no knowledge graph exists, builds it first before pulling.
    """
    from concurrent.futures import ThreadPoolExecutor

    task_manager = get_task_manager()
    sync_mgr = get_sync_manager(project_name)

    # Check if graph exists — if not, build it first
    has_graph = _check_has_graph(project_name)
    if not has_graph:
        repo_path = getattr(sync_mgr, "repo_path", None)
        if repo_path:
            from api.routes.graph import refresh_graph_task

            await task_manager.update_task(
                task_id,
                status="running",
                progress=0,
                step="Building knowledge graph",
                status_message="No existing graph found — building before pull...",
            )
            await refresh_graph_task(task_id, project_name, repo_path, fast_mode=True)

            # Re-check: refresh_graph_task sets status to completed, reset for pull phase
            await task_manager.update_task(
                task_id,
                status="running",
                progress=50,
                step="Pulling",
                status_message="Graph built — now pulling from remote...",
            )

    # Set up progress callback
    main_loop = asyncio.get_event_loop()

    # If we built graph first, pull progress maps to 50-100 range
    progress_offset = 50 if not has_graph else 0
    progress_scale = 0.5 if not has_graph else 1.0

    def progress_callback(progress: int, step: str, message: str):
        step_map = {
            "fetching": "Fetching",
            "computing_diff": "Computing diff",
            "stashed": "Stashing local changes",
            "merging": "Merging",
            "merged": "Merged",
            "unstashed": "Restoring local changes",
            "full_rebuild": "Full rebuild",
            "updating_graph": "Updating graph",
            "syncing_cache": "Syncing cache",
            "rebuilding_cache": "Rebuilding cache",
            "up_to_date": "Up to date",
        }
        step_display = step_map.get(step, step.replace("_", " ").title())
        mapped_progress = progress_offset + int(progress * progress_scale)
        try:

            async def _update():
                await task_manager.update_task(
                    task_id,
                    progress=mapped_progress,
                    step=step_display,
                    status_message=message,
                )

            future = asyncio.run_coroutine_threadsafe(_update(), main_loop)
            try:
                future.result(timeout=0.1)
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"Progress update deferred: {e}")

    sync_mgr.set_progress_callback(progress_callback)

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="git_pull")

    try:
        await task_manager.update_task(
            task_id,
            status="running",
            status_message=f"Pulling from {remote}/{branch or 'current'}...",
        )

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            executor, lambda: sync_mgr.pull(remote=remote, branch=branch)
        )

        if result.errors:
            await task_manager.update_task(
                task_id,
                status="failed",
                progress=100,
                step="Error",
                status_message="Pull completed with errors",
                error=result.errors[0] if result.errors else "Unknown error",
                result={
                    "added": result.added,
                    "modified": result.modified,
                    "deleted": result.deleted,
                    "errors": result.errors,
                },
            )
        else:
            await task_manager.update_task(
                task_id,
                status="completed",
                progress=100,
                step="Complete",
                status_message=f"Pull complete: {result.added} added, {result.modified} modified, {result.deleted} deleted",
                result={
                    "added": result.added,
                    "modified": result.modified,
                    "deleted": result.deleted,
                    "calls_rebuilt": result.calls_rebuilt,
                    "duration_ms": result.duration_ms,
                },
            )

    except asyncio.CancelledError:
        await task_manager.update_task(
            task_id, status="cancelled", error="Task was cancelled"
        )
    except Exception as e:
        logger.error(f"Git pull task failed: {e}", exc_info=True)
        await task_manager.update_task(
            task_id, status="failed", step="Error", error=str(e)
        )
    finally:
        executor.shutdown(wait=True)
        sync_mgr.set_progress_callback(None)


@router.post("/{project_name}/git/pull", response_model=CheckoutTaskResponse)
async def pull_remote(
    project_name: str,
    request: PullRequest,
    background: bool = Query(
        True, description="Run as background task (default: true)"
    ),
    repo_path: str | None = Query(
        None, description="Repository path (required for first-time initialization)"
    ),
):
    """Pull updates from remote (fetch + merge) and update graph.

    This will:
    1. Fetch from remote
    2. Merge changes (fast-forward preferred)
    3. Apply incremental graph updates
    4. Rebuild hash cache

    Args:
        project_name: Project name
        request: Pull request with remote and optional branch
        background: Whether to run as background task (default: true)
        repo_path: Repository path (required if sync manager not initialized)

    Returns:
        CheckoutTaskResponse with task_id for tracking progress
    """
    sync_mgr = await asyncio.to_thread(
        get_sync_manager, project_name, Path(repo_path) if repo_path else None
    )

    if not sync_mgr.is_git_repo:
        raise HTTPException(status_code=400, detail="Not a Git repository")

    if background:
        task_manager = get_task_manager()

        task_id = await task_manager.create_task(
            task_type=TaskType.GIT_CHECKOUT,
            repo_name=project_name,
            initial_message=f"Pulling from {request.remote}/{request.branch or 'current'}...",
        )

        queue_position = await task_manager.run_task(
            task_id,
            _run_git_pull_task,
            project_name,
            request.remote,
            request.branch,
        )

        if queue_position > 0:
            message = f"Pull queued at position {queue_position}"
        else:
            message = "Pull started"

        return CheckoutTaskResponse(
            task_id=task_id,
            status="pending" if queue_position > 0 else "running",
            message=message,
        )
    else:
        result = await asyncio.to_thread(
            sync_mgr.pull, remote=request.remote, branch=request.branch
        )

        return CheckoutTaskResponse(
            task_id="",
            status="completed" if not result.errors else "failed",
            message=f"Pull complete: {result.added} added, {result.modified} modified, {result.deleted} deleted",
        )


@router.post("/{project_name}/git/checkout", response_model=CheckoutTaskResponse)
async def checkout_ref(
    project_name: str,
    request: CheckoutRequest,
    background: bool = Query(
        True, description="Run as background task (default: true)"
    ),
    repo_path: str | None = Query(
        None, description="Repository path (required for first-time initialization)"
    ),
):
    """Switch to a different Git reference (branch/tag/commit).

    This will:
    1. Pause file monitoring
    2. Checkout the reference
    3. Apply incremental graph updates
    4. Resume file monitoring

    By default, this operation runs as a background task to avoid timeout
    on large repositories. The response includes a task_id that can be used
    to query progress.

    Args:
        project_name: Project name
        request: Checkout request with reference name
        background: Whether to run as background task (default: true)
        repo_path: Repository path (required if sync manager not initialized)

    Returns:
        CheckoutTaskResponse with task_id for tracking progress
    """
    sync_mgr = await asyncio.to_thread(
        get_sync_manager, project_name, Path(repo_path) if repo_path else None
    )

    if not sync_mgr.is_git_repo:
        raise HTTPException(status_code=400, detail="Not a Git repository")

    if background:
        # Create background task
        task_manager = get_task_manager()

        task_id = await task_manager.create_task(
            task_type=TaskType.GIT_CHECKOUT,
            repo_name=project_name,
            initial_message=f"Checking out '{request.ref}'...",
        )

        # Queue the task for execution
        queue_position = await task_manager.run_task(
            task_id,
            _run_git_checkout_task,
            project_name,
            request.ref,
        )

        if queue_position > 0:
            message = f"Checkout queued at position {queue_position}"
        else:
            message = "Checkout started"

        return CheckoutTaskResponse(
            task_id=task_id,
            status="pending" if queue_position > 0 else "running",
            message=message,
        )
    else:
        # Synchronous execution (not recommended for large changes)
        result = await asyncio.to_thread(sync_mgr.checkout, request.ref)

        return CheckoutTaskResponse(
            task_id="",
            status="completed" if not result.errors else "failed",
            message=f"Checkout complete: {result.added} added, {result.modified} modified, {result.deleted} deleted",
        )


# ============== Pending Files & History Endpoints ==============


@router.get("/{project_name}/pending", response_model=list[PendingFileInfo])
async def get_pending_files(project_name: str):
    """Get list of files pending sync.

    Args:
        project_name: Project name

    Returns:
        List of pending file changes
    """
    sync_mgr = get_sync_manager(project_name)
    pending = await asyncio.to_thread(sync_mgr.get_pending_files)

    return [PendingFileInfo(path=f["path"], action=f["action"]) for f in pending]


@router.get("/{project_name}/history", response_model=list[SyncHistoryItem])
async def get_sync_history(
    project_name: str,
    limit: int = Query(20, ge=1, le=50, description="Max history items to return"),
):
    """Get sync history for a project.

    Args:
        project_name: Project name
        limit: Maximum number of history items

    Returns:
        List of sync history items, newest first
    """
    sync_mgr = get_sync_manager(project_name)
    history = sync_mgr.get_history(limit=limit)

    return [
        SyncHistoryItem(
            timestamp=item.timestamp,
            total_changes=item.total_changes,
            added=item.added,
            modified=item.modified,
            deleted=item.deleted,
            added_files=getattr(item, "added_files", []),
            modified_files=getattr(item, "modified_files", []),
            deleted_files=getattr(item, "deleted_files", []),
            duration_ms=item.duration_ms,
            success=item.success,
            errors=item.errors,
        )
        for item in history
    ]


# ============== Sync WebSocket Endpoint ==============


@router.websocket("/{project_name}/ws")
async def sync_websocket(websocket: WebSocket, project_name: str):
    """WebSocket endpoint for real-time sync status updates.

    Clients connect to receive instant updates when sync status changes.

    Protocol:
    - Server sends: {"type": "sync_status", "project_name": "...", "data": {...}}
    - Server sends: {"type": "sync_progress", "project_name": "...", "data": {...}}
    - Server sends: {"type": "sync_complete", "project_name": "...", "data": {...}}
    """
    ws_mgr = get_sync_ws_manager()
    await ws_mgr.connect(websocket)

    try:
        # Send initial status
        try:
            sync_mgr = get_sync_manager(project_name)
            status = sync_mgr.get_status()
            await websocket.send_json(
                {
                    "type": "sync_status",
                    "project_name": project_name,
                    "data": {
                        **status,
                        "current_file": sync_mgr.current_processing_file,
                    },
                }
            )
        except Exception:
            await websocket.send_json(
                {
                    "type": "sync_status",
                    "project_name": project_name,
                    "data": None,
                }
            )

        # Keep connection alive, listen for client messages
        while True:
            data = await websocket.receive_text()
            try:
                import json

                message = json.loads(data)
                if message.get("action") == "refresh":
                    # Client requests a status refresh
                    try:
                        sync_mgr = get_sync_manager(project_name)
                        status = sync_mgr.get_status()
                        await websocket.send_json(
                            {
                                "type": "sync_status",
                                "project_name": project_name,
                                "data": {
                                    **status,
                                    "current_file": sync_mgr.current_processing_file,
                                },
                            }
                        )
                    except Exception:
                        pass
            except Exception:
                pass

    except WebSocketDisconnect:
        await ws_mgr.disconnect(websocket)
    except Exception as e:
        logger.debug(f"Sync WebSocket error: {e}")
        try:
            await ws_mgr.disconnect(websocket)
        except Exception:
            pass


# ============== Cleanup Endpoint ==============


@router.post("/{project_name}/cleanup", response_model=dict)
async def cleanup_sync_manager(project_name: str):
    """Cleanup and remove a sync manager for a project.

    Stops monitoring and releases resources.

    Args:
        project_name: Project name

    Returns:
        Status message
    """
    if project_name not in _sync_managers:
        raise HTTPException(
            status_code=404, detail=f"Sync manager for '{project_name}' not found"
        )

    sync_mgr = _sync_managers.pop(project_name)
    sync_mgr.stop_watching()

    return {
        "status": "cleaned_up",
        "message": f"Sync manager for '{project_name}' cleaned up",
    }


# ============== Git Blame Endpoint ==============


class BlameLineResponse(BaseModel):
    """Blame information for a single line."""

    line: int = Field(..., description="Line number (1-based)")
    sha: str = Field(..., description="Full commit SHA")
    short_sha: str = Field(..., description="Short commit SHA (7 chars)")
    author: str = Field(..., description="Author name")
    date: str = Field(..., description="Commit date (ISO format)")
    message: str = Field(..., description="Commit message (first line)")


class BlameResponse(BaseModel):
    """Git blame response."""

    file_path: str
    lines: list[BlameLineResponse]


@router.get("/{project_name}/git/blame", response_model=BlameResponse)
async def get_git_blame(
    project_name: str,
    file_path: str = Query(..., description="File path relative to repo root"),
):
    """Get git blame information for a file.

    Args:
        project_name: Project name
        file_path: File path relative to repository root

    Returns:
        BlameResponse with per-line blame data
    """
    import subprocess

    sync_mgr = _get_or_init_sync_manager(project_name)

    if not sync_mgr.is_git_repo:
        raise HTTPException(status_code=400, detail="Not a Git repository")

    repo_path = Path(sync_mgr.repo_path)
    full_path = repo_path / file_path
    if not full_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    try:
        result = subprocess.run(
            [GIT, "blame", "--porcelain", file_path],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"git blame failed: {result.stderr[:200]}",
            )

        # Parse porcelain output
        lines: list[BlameLineResponse] = []
        current_sha = ""
        current_author = ""
        current_date = ""
        current_message = ""
        commit_cache: dict[str, dict] = {}
        line_num = 0

        for raw_line in result.stdout.split("\n"):
            if not raw_line:
                continue

            # Header line: <sha> <orig_line> <final_line> [<num_lines>]
            parts = raw_line.split()
            if len(parts) >= 3 and len(parts[0]) == 40:
                current_sha = parts[0]
                line_num = int(parts[2])
                if current_sha in commit_cache:
                    cached = commit_cache[current_sha]
                    current_author = cached["author"]
                    current_date = cached["date"]
                    current_message = cached["message"]
                continue

            if raw_line.startswith("author "):
                current_author = raw_line[7:]
            elif raw_line.startswith("author-time "):
                from datetime import datetime, timezone

                ts = int(raw_line[12:])
                current_date = datetime.fromtimestamp(
                    ts, tz=timezone.utc
                ).strftime("%Y-%m-%d")
            elif raw_line.startswith("summary "):
                current_message = raw_line[8:]
            elif raw_line.startswith("\t"):
                # Content line — emit blame entry
                if current_sha not in commit_cache:
                    commit_cache[current_sha] = {
                        "author": current_author,
                        "date": current_date,
                        "message": current_message,
                    }
                lines.append(
                    BlameLineResponse(
                        line=line_num,
                        sha=current_sha,
                        short_sha=current_sha[:7],
                        author=current_author,
                        date=current_date,
                        message=current_message,
                    )
                )

        return BlameResponse(file_path=file_path, lines=lines)

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="git blame timed out")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"git blame error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

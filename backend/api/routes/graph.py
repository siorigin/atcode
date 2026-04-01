# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock as ThreadLock
from typing import Any

import redis as sync_redis
from api.services.build_scheduler import get_build_scheduler
from api.services.graph_sync import get_graph_sync_service
from api.services.task_queue import TaskStatus, TaskType, get_task_manager
from core.config import settings
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from graph.service import MemgraphIngestor, get_connection_pool
from loguru import logger
from pydantic import BaseModel, Field

router = APIRouter()


# ============== Redis Graph Cache (L2) ==============
# Caches /projects and /stats responses in Redis so that repeated page loads
# don't hit Memgraph. Individual project updates modify the cache in-place
# instead of invalidating everything.
#
# Stampede prevention: asyncio.Lock ensures only one request queries Memgraph
# on cache miss; others wait for the result.
# Warmup: call warm_graph_cache() at app startup to pre-fill the cache.

_redis_client: sync_redis.Redis | None = None
_CACHE_TTL = 86400  # 24 hours — graph data only changes on user operations
_CACHE_KEY_PROJECTS = "graph:projects_list"
_CACHE_KEY_STATS = "graph:stats"
_projects_lock = asyncio.Lock()
_stats_lock = asyncio.Lock()


def _get_redis() -> sync_redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = sync_redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


def _cache_get(key: str) -> dict | None:
    try:
        raw = _get_redis().get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _cache_set(key: str, data: dict) -> None:
    try:
        _get_redis().set(key, json.dumps(data), ex=_CACHE_TTL)
    except Exception:
        pass


def _cache_delete(*keys: str) -> None:
    try:
        _get_redis().delete(*keys)
    except Exception:
        pass


def _recalc_stats_from_projects(projects: list[dict]) -> dict:
    """Recalculate aggregated stats from the projects list."""
    total_nodes = 0
    total_rels = 0
    total_projects = 0
    node_types: dict[str, int] = {}
    for p in projects:
        total_nodes += p.get("node_count", 0)
        total_rels += p.get("relationship_count", 0)
        if p.get("has_graph"):
            total_projects += 1
        for label, count in p.get("node_types", {}).items():
            node_types[label] = node_types.get(label, 0) + count
    return {
        "total_projects": total_projects,
        "total_nodes": total_nodes,
        "total_relationships": total_rels,
        "node_types": node_types,
        "connected": True,
    }


def _write_graph_cache(projects: list[dict]) -> dict[str, dict]:
    """Write projects and derived stats into Redis with a fresh cache epoch."""
    cache_epoch = datetime.now(UTC).isoformat()
    projects_payload = {
        "projects": projects,
        "total": len(projects),
        "connected": True,
        "cache_epoch": cache_epoch,
    }
    stats_payload = {
        **_recalc_stats_from_projects(projects),
        "cache_epoch": cache_epoch,
    }
    _cache_set(_CACHE_KEY_PROJECTS, projects_payload)
    _cache_set(_CACHE_KEY_STATS, stats_payload)
    return {
        "projects": projects_payload,
        "stats": stats_payload,
    }


def _fetch_projects_from_memgraph() -> list[dict]:
    """Fetch all project summaries directly from Memgraph.

    This is intentionally used only by startup warmup and explicit cache-rebuild
    paths, not by request handlers serving the repos page.
    """
    from core.config import get_wiki_repos_dir

    with get_ingestor() as ingestor:
        projects_data = ingestor.list_projects()
        wiki_repos_dir = get_wiki_repos_dir()

        sync_flags: dict[str, bool] = {}
        try:
            rows = ingestor._execute_query(
                "MATCH (p:Project) RETURN p.name AS name, p.sync_enabled AS sync_enabled"
            )
            for row in rows:
                sync_flags[row["name"]] = bool(row.get("sync_enabled"))
        except Exception:
            pass

        projects = []
        for p in projects_data:
            project_name = p.get("name", "Unknown")
            project_path = ingestor.get_project_path(project_name)
            if not project_path:
                fallback_path = wiki_repos_dir / project_name
                if fallback_path.exists():
                    project_path = str(fallback_path)
            projects.append(
                ProjectInfo(
                    name=project_name,
                    node_count=p.get("node_count", 0),
                    relationship_count=p.get("relationship_count", 0),
                    has_graph=p.get("node_count", 0) > 0,
                    path=project_path,
                    sync_enabled=sync_flags.get(project_name, False),
                ).model_dump()
            )

        return projects


def _update_project_in_cache(project_name: str) -> None:
    """Update a single project's data in the Redis cache after build/refresh."""
    try:
        with get_ingestor() as ingestor:
            stats = ingestor.get_project_stats(project_name)
            project_path = ingestor.get_project_path(project_name)
            # Fetch sync_enabled flag
            sync_enabled = False
            try:
                rows = ingestor._execute_query(
                    "MATCH (p:Project {name: $name}) RETURN p.sync_enabled AS sync_enabled",
                    {"name": project_name},
                )
                if rows:
                    sync_enabled = bool(rows[0].get("sync_enabled"))
            except Exception:
                pass

        updated_entry = {
            "name": stats.get("name", project_name),
            "node_count": stats.get("node_count", 0),
            "relationship_count": stats.get("relationship_count", 0),
            "has_graph": stats.get("node_count", 0) > 0,
            "node_types": stats.get("node_types", {}),
            "path": project_path,
            "sync_enabled": sync_enabled,
        }

        cached = _cache_get(_CACHE_KEY_PROJECTS)
        if cached:
            projects = cached.get("projects", [])
            found = False
            for i, p in enumerate(projects):
                if p.get("name") == project_name:
                    projects[i] = updated_entry
                    found = True
                    break
            if not found:
                projects.append(updated_entry)
            _write_graph_cache(projects)
        else:
            # No cached projects list — just delete both keys so next request rebuilds
            _cache_delete(_CACHE_KEY_PROJECTS, _CACHE_KEY_STATS)
    except Exception as e:
        logger.debug(f"Failed to update project cache for {project_name}: {e}")
        _cache_delete(_CACHE_KEY_PROJECTS, _CACHE_KEY_STATS)


def _remove_project_from_cache(project_name: str) -> None:
    """Remove a single project from the Redis cache after deletion."""
    try:
        cached = _cache_get(_CACHE_KEY_PROJECTS)
        if cached:
            projects = [p for p in cached.get("projects", []) if p.get("name") != project_name]
            _write_graph_cache(projects)
    except Exception as e:
        logger.debug(f"Failed to remove project from cache: {e}")
        _cache_delete(_CACHE_KEY_PROJECTS, _CACHE_KEY_STATS)


async def _refresh_graph_cache_from_memgraph() -> dict[str, Any]:
    """Explicitly rebuild Redis graph cache from Memgraph.

    This path is reserved for startup warmup and explicit user-triggered sync.
    Request-time list/stats endpoints remain cache-only.
    """
    loop = asyncio.get_event_loop()
    projects = await loop.run_in_executor(None, _fetch_projects_from_memgraph)
    return _write_graph_cache(projects)


async def warm_graph_cache() -> None:
    """Pre-fill the Redis graph cache at app startup so the first user doesn't wait.

    Call this from the FastAPI lifespan/startup event. Failures are logged
    but never propagated — the app starts regardless.

    Always invalidates the old Redis cache first: after a backend restart the
    stale cache may contain outdated (or zero) counts while Memgraph already
    has fresh data.  Re-querying Memgraph takes <1 s for typical graphs.
    """
    # Invalidate stale Redis cache from the previous process
    _cache_delete(_CACHE_KEY_PROJECTS, _CACHE_KEY_STATS)

    logger.info("Warming graph cache from Memgraph...")
    for attempt in range(1, 4):
        try:
            await _refresh_graph_cache_from_memgraph()
            logger.info("Graph cache warmed successfully")
            return
        except Exception as e:
            if attempt < 3:
                logger.warning(
                    f"Graph cache warmup attempt {attempt}/3 failed: {e}, retrying in 2 s..."
                )
                await asyncio.sleep(2)
            else:
                logger.warning(f"Graph cache warmup failed after 3 attempts (non-fatal): {e}")
# ============== Request/Response Models ==============


class ProjectInfo(BaseModel):
    """Information about a project in the knowledge graph."""

    name: str = Field(..., description="Project name")
    node_count: int = Field(0, description="Number of nodes in the graph")
    relationship_count: int = Field(
        0, description="Number of relationships in the graph"
    )
    has_graph: bool = Field(True, description="Whether the project has graph data")
    path: str | None = Field(None, description="Local repository path")
    sync_enabled: bool = Field(False, description="Whether incremental sync is enabled")
    node_types: dict[str, int] = Field(default_factory=dict, description="Node count by label type")


class ProjectListResponse(BaseModel):
    """Response for listing projects."""

    projects: list[ProjectInfo] = Field(
        default_factory=list, description="List of projects"
    )
    total: int = Field(0, description="Total number of projects")
    connected: bool = Field(
        False, description="Whether graph cache is available from the backend"
    )
    cache_epoch: str | None = Field(
        None, description="Opaque cache epoch that changes when graph cache is rewritten"
    )


class CleanProjectResponse(BaseModel):
    """Response for cleaning a project."""

    success: bool = Field(..., description="Whether the operation was successful")
    project_name: str = Field(..., description="Name of the project that was cleaned")
    deleted_nodes: int = Field(0, description="Number of nodes deleted")
    message: str = Field("", description="Status message")


class RefreshProjectRequest(BaseModel):
    """Request to refresh a project's knowledge graph."""

    project_name: str = Field(..., description="Name of the project to refresh")
    repo_path: str | None = Field(
        None, description="Path to the repository (if different from default)"
    )


class RefreshProjectResponse(BaseModel):
    """Response for refreshing a project."""

    success: bool = Field(
        ..., description="Whether the operation was started successfully"
    )
    project_name: str = Field(..., description="Name of the project")
    job_id: str | None = Field(
        None, description="Background job ID for tracking progress"
    )
    message: str = Field("", description="Status message")


class GraphStatsResponse(BaseModel):
    """Response for graph statistics."""

    total_projects: int = Field(0, description="Total number of projects")
    total_nodes: int = Field(0, description="Total number of nodes across all projects")
    total_relationships: int = Field(0, description="Total number of relationships")
    node_types: dict[str, int] = Field(
        default_factory=dict, description="Count of each node type"
    )
    connected: bool = Field(False, description="Whether database is connected")
    cache_epoch: str | None = Field(
        None, description="Opaque cache epoch that changes when graph cache is rewritten"
    )


class GraphCacheSyncResponse(BaseModel):
    """Response for manually syncing Redis graph cache from Memgraph."""

    success: bool = Field(..., description="Whether cache refresh succeeded")
    total_projects: int = Field(0, description="Projects written into cache")
    total_nodes: int = Field(0, description="Total cached node count")
    total_relationships: int = Field(
        0, description="Total cached relationship count"
    )
    message: str = Field("", description="Status message")


# ============== Background task tracking ==============

# Now uses unified task_queue service for persistence and multi-user visibility


async def get_job_status(job_id: str) -> dict[str, Any] | None:
    """Get the status of a background job from unified task manager."""
    task_manager = get_task_manager()
    state = await task_manager.get_task_status(job_id)
    if not state:
        return None
    return {
        "job_id": state.task_id,
        "project_name": state.repo_name,
        "status": state.status.value,
        "progress": state.progress,
        "message": state.status_message,
        "created_at": state.created_at,
        "updated_at": state.started_at or state.created_at,
        "error": state.error,
    }


async def update_job_status(
    job_id: str, status: str, progress: int = 0, message: str = "", error: str = None
):
    """Update a job's status in unified task manager."""
    task_manager = get_task_manager()

    # Map string status to TaskStatus enum
    status_map = {
        "pending": TaskStatus.PENDING,
        "running": TaskStatus.RUNNING,
        "completed": TaskStatus.COMPLETED,
        "failed": TaskStatus.FAILED,
        "cancelled": TaskStatus.CANCELLED,
    }
    task_status = status_map.get(status, TaskStatus.RUNNING)

    await task_manager.update_task(
        job_id,
        status=task_status,
        progress=progress,
        status_message=message,
        error=error,
    )


# ============== Helper Functions ==============


class PooledIngestor:
    """
    Context manager that uses a connection from the pool.

    This avoids the overhead of creating new connections for each query.
    The connection is returned to the pool when exiting the context.
    """

    def __init__(
        self,
        host: str,
        port: int,
        batch_size: int = 1000,
        write_delay_ms: int | None = None,
    ):
        self._host = host
        self._port = port
        self._batch_size = batch_size
        self._write_delay_ms = write_delay_ms
        self._pool = None
        self._conn = None
        self._ingestor = None

    def __enter__(self) -> MemgraphIngestor:
        """Get connection from pool and create ingestor."""
        # Get connection pool
        self._pool = get_connection_pool(self._host, self._port, max_size=10)
        self._conn = self._pool.get()

        # Create ingestor with the pooled connection
        self._ingestor = MemgraphIngestor.__new__(MemgraphIngestor)
        self._ingestor._host = self._host
        self._ingestor._port = self._port
        self._ingestor.batch_size = self._batch_size
        self._ingestor.write_delay_ms = (
            self._write_delay_ms or settings.MEMGRAPH_WRITE_DELAY_MS
        )
        self._ingestor.conn = self._conn
        self._ingestor.node_buffer = []
        self._ingestor.relationship_buffer = []
        self._ingestor._node_buffer_lock = ThreadLock()
        self._ingestor._relationship_buffer_lock = ThreadLock()
        self._ingestor._flush_lock = ThreadLock()
        self._ingestor.unique_constraints = {
            "Project": "name",
            "Folder": "qualified_name",
            "Class": "qualified_name",
            "Function": "qualified_name",
            "Method": "qualified_name",
            "File": "qualified_name",
            "ExternalPackage": "name",
        }
        from graph.service import QueryCache

        self._ingestor._query_cache = QueryCache(max_size=1000, default_ttl=300.0)
        self._ingestor._last_health_check = 0.0
        self._ingestor._health_check_interval = 30.0
        self._ingestor._connection_lock = ThreadLock()

        return self._ingestor

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Return connection to pool."""
        if self._ingestor:
            # Flush any pending writes
            try:
                self._ingestor.flush_all()
            except Exception as e:
                logger.warning(f"Error flushing ingestor: {e}")

        if self._conn and self._pool:
            self._pool.put(self._conn)
            self._conn = None

        return False


def get_ingestor(for_write: bool = False) -> PooledIngestor:
    """
    Create a MemgraphIngestor that uses a connection pool.

    Uses the unified config helpers for mode-aware connection selection:
    - Standalone mode: all operations go to local instance
    - Replication mode as MAIN: all operations go to local instance
    - Replication mode as REPLICA: reads local, writes to MAIN

    Args:
        for_write: If True, return ingestor configured for write operations.

    Returns:
        PooledIngestor context manager for the appropriate instance.
    """
    if for_write:
        # Get write connection (local for standalone/MAIN, MAIN for REPLICA)
        host, port = settings.get_write_connection()

        # Legacy mode: use BUILD_HOST for writes if configured
        if settings.MEMGRAPH_BUILD_HOST:
            host, port = settings.MEMGRAPH_BUILD_HOST, settings.MEMGRAPH_BUILD_PORT

        if host != settings.MEMGRAPH_HOST:
            logger.debug(f"Using remote instance for write: {host}:{port}")

        return PooledIngestor(
            host=host,
            port=port,
            batch_size=settings.MEMGRAPH_BATCH_SIZE,
        )

    # Read connection (always local)
    host, port = settings.get_read_connection()
    return PooledIngestor(
        host=host,
        port=port,
        batch_size=settings.MEMGRAPH_BATCH_SIZE,
    )


def get_local_repos_path() -> Path:
    """Get the path to repositories directory from config."""
    # Use configured path from settings (relative to backend dir)
    backend_dir = Path(__file__).parent.parent.parent
    repos_path = backend_dir / settings.REPOS_BASE_PATH
    return repos_path.resolve()


# Global storage for active GraphUpdater instances (for cancellation)
_active_updaters: dict[str, Any] = {}
_updater_lock = asyncio.Lock()


async def cancel_graph_build(job_id: str) -> bool:
    """Cancel an active graph build by job_id.

    This function signals the GraphUpdater to stop at the next checkpoint.
    The updater runs in a ThreadPoolExecutor, so we can't directly cancel it,
    but we can set a flag that it checks periodically.

    Args:
        job_id: The job ID of the build to cancel

    Returns:
        True if the updater was found and signaled, False otherwise
    """
    async with _updater_lock:
        updater = _active_updaters.get(job_id)
        if updater:
            updater.cancel()
            logger.info(f"Signaled cancellation for graph build: {job_id}")
            return True
    return False


async def _run_graph_build_task(
    task_id: str,
    project_name: str,
    repo_path: Path,
    fast_mode: bool = False,
) -> None:
    """Wrapper function for graph build that integrates with task queue.

    This is called by task_manager.run_task() which handles:
    - Concurrency control (max concurrent graph builds)
    - Queue management (PENDING state for waiting tasks)
    - Task registration for cancellation support

    Args:
        task_id: The task ID (passed by task_manager)
        project_name: Name of the project to build
        repo_path: Path to the repository
        fast_mode: If True, skip semantic embeddings
    """
    # Simply delegate to refresh_graph_task
    # The task_manager wrapper handles status updates, cancellation, etc.
    await refresh_graph_task(task_id, project_name, repo_path, fast_mode)


async def refresh_graph_task(
    job_id: str,
    project_name: str,
    repo_path: Path,
    fast_mode: bool = False,
    write_delay_ms: int = 30,
    batch_size: int = 500,
    **kwargs,
):
    """Background task to refresh a project's knowledge graph.

    Args:
        job_id: Unique job identifier for tracking progress
        project_name: Name of the project to refresh
        repo_path: Path to the repository
        fast_mode: If True, skip semantic embedding generation for faster builds
        write_delay_ms: Delay between batch writes (ms) for read priority
        batch_size: Batch size for database writes (smaller = less blocking)
    """
    task_manager = get_task_manager()
    updater = None

    # Capture the event loop from the main thread BEFORE spawning worker thread
    # This is critical: worker threads don't have their own event loop
    main_loop = asyncio.get_running_loop()

    def _safe_schedule_coroutine(coro, timeout: float = 5.0):
        """Safely schedule a coroutine on the event loop from another thread.

        Handles event loop closure during worker shutdown gracefully.

        Args:
            coro: Coroutine to schedule
            timeout: Maximum time to wait for completion (seconds)
        """
        try:
            # Check if event loop is still running
            if main_loop is None or main_loop.is_closed():
                logger.debug("Event loop closed, skipping callback")
                return

            # Schedule coroutine and wait with timeout
            future = asyncio.run_coroutine_threadsafe(coro, main_loop)
            try:
                future.result(timeout=timeout)
            except TimeoutError:
                logger.warning(f"Callback timed out after {timeout}s")
                future.cancel()

        except RuntimeError as e:
            error_msg = str(e)
            if (
                "Event loop is closed" in error_msg
                or "no running event loop" in error_msg
            ):
                logger.debug(f"Event loop unavailable during callback: {error_msg}")
            else:
                logger.warning(f"RuntimeError in callback scheduling: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error in callback scheduling: {e}")

    # Progress callback that updates the task status
    # This runs in the GraphUpdater thread, so we use the captured main loop
    def progress_callback(progress: int, message: str):
        """Callback to update task progress from GraphUpdater.

        Maps GraphUpdater progress (0-100) to overall task progress (50-95).
        Thread-safe: handles event loop closure during worker shutdown.
        """
        # Map progress: 0-100 from GraphUpdater to 50-95 in overall task
        mapped_progress = 50 + int(progress * 0.45)
        # Schedule the coroutine on the main event loop from worker thread
        _safe_schedule_coroutine(
            update_job_status(job_id, "running", mapped_progress, message)
        )

    try:
        await update_job_status(job_id, "running", 10, "Connecting to database...")

        # Import here to avoid circular imports
        from concurrent.futures import ThreadPoolExecutor

        from graph.updater import GraphUpdater
        from parser.loader import load_parsers

        # Determine which instance to write to using unified config
        # - Standalone/MAIN: write to local instance
        # - REPLICA: write to MAIN instance (auto-detected)
        # - Legacy BUILD_HOST: use that if configured
        if settings.MEMGRAPH_BUILD_HOST:
            write_host = settings.MEMGRAPH_BUILD_HOST
            write_port = settings.MEMGRAPH_BUILD_PORT
            logger.info(
                f"Legacy mode: writing to BUILD instance {write_host}:{write_port}"
            )
        else:
            write_host, write_port = settings.get_write_connection()
            mode_desc = f"{settings.detected_role} mode"
            logger.info(f"{mode_desc}: writing to {write_host}:{write_port}")

        ingestor = MemgraphIngestor(
            host=write_host,
            port=write_port,
            batch_size=batch_size,
            write_delay_ms=write_delay_ms,
        )
        logger.info(
            f"Using throttled writes: batch_size={batch_size}, delay={write_delay_ms}ms"
        )

        with ingestor:
            # Step 1: Clean existing project data
            await update_job_status(
                job_id, "running", 20, "Cleaning existing graph data..."
            )
            ingestor.clean_project(project_name)

            # Step 2: Ensure constraints
            await update_job_status(
                job_id, "running", 30, "Setting up database constraints..."
            )
            ingestor.ensure_constraints()

            # Step 3: Load parsers with language objects for parallel parsing
            await update_job_status(
                job_id, "running", 40, "Loading language parsers..."
            )
            parsers, queries, language_objects = load_parsers(return_languages=True)

            # Step 4: Create updater and run with optimizations
            # Note: Storage mode switching is handled inside GraphUpdater.run()
            mode_msg = " (fast mode)" if fast_mode else ""
            await update_job_status(
                job_id, "running", 50, f"Starting graph build{mode_msg}..."
            )

            updater = GraphUpdater(
                ingestor,
                repo_path,
                parsers,
                queries,
                skip_embeddings=fast_mode,
                progress_callback=progress_callback,
                task_id=job_id,  # Pass task_id for file-based cancellation check
                language_objects=language_objects,
                enable_parallel_parsing=True,
            )

            # Register updater for cancellation support
            async with _updater_lock:
                _active_updaters[job_id] = updater

            # Run the synchronous GraphUpdater in a thread pool to avoid blocking
            # Use force_full_build=True because we just cleaned the database
            # This ensures incremental state doesn't cause a no-op when DB was cleared
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="graph_build"
            ) as executor:
                await loop.run_in_executor(
                    executor, lambda: updater.run(force_full_build=True)
                )

            # Determine sync strategy based on mode
            sync_service = get_graph_sync_service()

            if settings.is_replication_mode:
                # Replication mode: WAL handles sync automatically, no manual sync needed
                logger.info("Replication mode: WAL will auto-sync to REPLICA instances")
                await update_job_status(
                    job_id, "running", 98, "Build complete, WAL syncing to replicas..."
                )
                # Small delay to allow WAL to propagate (usually instant, but be safe)
                await asyncio.sleep(0.5)

            elif (
                sync_service.is_build_instance_configured()
                and settings.MEMGRAPH_BUILD_SYNC_MODE == "immediate"
            ):
                # Legacy mode: manual sync from build instance to primary
                await update_job_status(
                    job_id, "running", 96, "Syncing to primary instance..."
                )
                logger.info(
                    f"Starting sync for project {project_name} to primary instance"
                )

                try:
                    sync_job = await sync_service.sync_project(
                        project_name, job_id=f"{job_id}_sync"
                    )
                    if sync_job.status.value == "completed":
                        logger.info(
                            f"Sync completed: {sync_job.nodes_synced} nodes, {sync_job.relationships_synced} rels"
                        )
                    else:
                        logger.warning(
                            f"Sync finished with status: {sync_job.status.value}"
                        )
                except Exception as sync_error:
                    logger.error(f"Sync failed: {sync_error}", exc_info=True)
                    # Don't fail the whole job if sync fails - build was successful
                    await update_job_status(
                        job_id,
                        "completed",
                        100,
                        f"Build completed but sync failed: {sync_error}. Manual sync required.",
                    )
                    return

            # Yield to event loop so pending progress_callback coroutines complete
            await asyncio.sleep(0)

            completion_msg = "Knowledge graph refreshed successfully"
            if fast_mode:
                completion_msg += " (embeddings skipped)"
            if settings.is_replication_mode:
                completion_msg += " (WAL auto-synced)"
            elif sync_service.is_build_instance_configured():
                completion_msg += " (synced to primary)"
            # Invalidate repos cache since graph data changed
            from api.routes.repos import invalidate_repos_cache
            invalidate_repos_cache()
            # Update this project in Redis L2 cache
            _update_project_in_cache(project_name)
            await update_job_status(job_id, "completed", 100, completion_msg)
            logger.info(
                f"Successfully refreshed knowledge graph for project: {project_name}"
            )

    except asyncio.CancelledError:
        logger.info(f"Graph refresh task cancelled: {job_id}")
        # Cancel the updater if it's running in the thread pool
        if updater:
            updater.cancel()
        await update_job_status(job_id, "cancelled", 0, "Task was cancelled by user")
        raise

    except Exception as e:
        error_msg = str(e)
        # Check if it's a cancellation from the updater
        if "cancelled" in error_msg.lower():
            logger.info(f"Graph refresh task cancelled: {job_id}")
            await update_job_status(
                job_id, "cancelled", 0, "Task was cancelled by user"
            )
        else:
            logger.error(
                f"Failed to refresh knowledge graph for {project_name}: {e}",
                exc_info=True,
            )
            await update_job_status(
                job_id, "failed", 0, "Failed to refresh knowledge graph", error_msg
            )

    finally:
        # Remove from active updaters
        async with _updater_lock:
            _active_updaters.pop(job_id, None)
        # Always unregister the task when done
        task_manager.unregister_task(job_id)


# ============== API Endpoints ==============


@router.get(
    "/projects",
    response_model=ProjectListResponse,
    summary="List Graph Projects",
    description="List all projects currently stored in the knowledge graph database.",
)
async def list_graph_projects() -> ProjectListResponse:
    """
    List all projects in the knowledge graph.

    Returns:
        ProjectListResponse with list of projects and their statistics
    """
    cached = _cache_get(_CACHE_KEY_PROJECTS)
    if cached:
        cached.setdefault("connected", True)
        cached.setdefault("cache_epoch", None)
        return ProjectListResponse(**cached)

    logger.warning(
        "Graph projects cache miss; returning empty cache-only response. "
        "Memgraph is not queried on request path."
    )
    return ProjectListResponse(projects=[], total=0, connected=False)


@router.get(
    "/stats",
    response_model=GraphStatsResponse,
    summary="Get Graph Statistics",
    description="Get overall statistics about the knowledge graph database.",
)
async def get_graph_stats() -> GraphStatsResponse:
    """
    Get statistics about the knowledge graph.

    Uses Redis L2 cache for fast responses. Falls back to Memgraph queries on miss.

    Returns:
        GraphStatsResponse with database statistics
    """
    projects_cached = _cache_get(_CACHE_KEY_PROJECTS)
    if projects_cached and projects_cached.get("projects") is not None:
        stats_data = {
            **_recalc_stats_from_projects(projects_cached.get("projects", [])),
            "cache_epoch": projects_cached.get("cache_epoch"),
        }
        _cache_set(_CACHE_KEY_STATS, stats_data)
        return GraphStatsResponse(**stats_data)

    cached = _cache_get(_CACHE_KEY_STATS)
    if cached:
        cached.setdefault("cache_epoch", None)
        return GraphStatsResponse(**cached)

    logger.warning(
        "Graph stats cache miss; returning empty cache-only response. "
        "Memgraph is not queried on request path."
    )
    return GraphStatsResponse(
        total_projects=0,
        total_nodes=0,
        total_relationships=0,
        node_types={},
        connected=False,
    )


@router.post(
    "/cache/sync",
    response_model=GraphCacheSyncResponse,
    summary="Sync Graph Cache",
    description="Explicitly refresh Redis graph cache from Memgraph.",
)
async def sync_graph_cache() -> GraphCacheSyncResponse:
    """Manually rebuild graph cache from Memgraph for the repos page."""
    async with _projects_lock:
        try:
            payload = await _refresh_graph_cache_from_memgraph()
            stats_payload = payload["stats"]
            projects_payload = payload["projects"]
            return GraphCacheSyncResponse(
                success=True,
                total_projects=projects_payload.get("total", 0),
                total_nodes=stats_payload.get("total_nodes", 0),
                total_relationships=stats_payload.get("total_relationships", 0),
                message="Graph cache synced from Memgraph",
            )
        except Exception as e:
            logger.error(f"Failed to sync graph cache from Memgraph: {e}", exc_info=True)
            raise HTTPException(
                status_code=500, detail=f"Failed to sync graph cache: {str(e)}"
            )


@router.get(
    "/projects/{project_name}/stats",
    response_model=ProjectInfo,
    summary="Get Single Project Stats",
    description="Get statistics (node count, relationship count) for a specific project.",
)
async def get_project_stats(project_name: str) -> ProjectInfo:
    """
    Get statistics for a single project.

    OPTIMIZED: Uses efficient label-based counting with per-project caching.
    Results are cached for 30 seconds per project.

    Args:
        project_name: Name of the project

    Returns:
        ProjectInfo with node and relationship counts
    """
    try:
        loop = asyncio.get_event_loop()

        def _sync_query():
            with get_ingestor() as ingestor:
                # Use the optimized service method
                stats = ingestor.get_project_stats(project_name)
                # Also fetch the project path from DB
                project_path = ingestor.get_project_path(project_name)

                return ProjectInfo(
                    name=stats.get("name", project_name),
                    node_count=stats.get("node_count", 0),
                    relationship_count=stats.get("relationship_count", 0),
                    has_graph=stats.get("node_count", 0) > 0,
                    node_types=stats.get("node_types", {}),
                    path=project_path,
                )

        return await loop.run_in_executor(None, _sync_query)

    except Exception as e:
        logger.error(
            f"Failed to get project stats for {project_name}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to get project stats: {str(e)}"
        )


@router.delete(
    "/projects/{project_name}",
    response_model=CleanProjectResponse,
    summary="Clean Project Graph",
    description="Delete all knowledge graph data for a specific project.",
)
async def clean_project_graph(project_name: str) -> CleanProjectResponse:
    """
    Clean (delete) all graph data for a specific project.

    Args:
        project_name: Name of the project to clean

    Returns:
        CleanProjectResponse with deletion statistics
    """
    try:
        loop = asyncio.get_event_loop()

        def _sync_query():
            with get_ingestor() as ingestor:
                result = ingestor.clean_project(project_name)

                return CleanProjectResponse(
                    success=result.get("success", False),
                    project_name=project_name,
                    deleted_nodes=result.get("deleted_nodes", 0),
                    message=result.get("message", ""),
                )

        result = await loop.run_in_executor(None, _sync_query)
        # Invalidate repos cache since has_graph status changed
        from api.routes.repos import invalidate_repos_cache
        invalidate_repos_cache()
        # Remove this project from Redis L2 cache
        _remove_project_from_cache(project_name)
        return result

    except Exception as e:
        logger.error(f"Failed to clean project {project_name}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to clean project: {str(e)}"
        )


@router.post(
    "/projects/{project_name}/refresh",
    response_model=RefreshProjectResponse,
    summary="Refresh Project Graph",
    description="Rebuild the knowledge graph for a specific project from its source code.",
)
async def refresh_project_graph(
    project_name: str,
    background_tasks: BackgroundTasks,
    repo_path: str | None = Query(None, description="Custom repository path"),
    fast_mode: bool = Query(
        False, description="Skip semantic embeddings for faster builds"
    ),
) -> RefreshProjectResponse:
    """
    Refresh (rebuild) the knowledge graph for a project.

    This is a long-running operation that runs in the background.

    In replication mode (REPLICA server), the request is proxied to the MAIN server
    so that the CPU-intensive build happens on MAIN, not REPLICA.

    Args:
        project_name: Name of the project to refresh
        repo_path: Optional custom path to the repository
        fast_mode: If True, skip semantic embedding generation for faster builds

    Returns:
        RefreshProjectResponse with job ID for tracking progress
    """
    # In replication mode on REPLICA: proxy the request to MAIN server
    # This ensures the CPU-intensive build runs on MAIN, not here
    if settings.is_replica_node:
        logger.info(
            f"REPLICA mode: proxying build request to MAIN server {settings.MEMGRAPH_MAIN_HOST}"
        )
        return await _proxy_build_to_main(project_name, repo_path, fast_mode)

    # Local build (MAIN server or standalone mode)
    try:
        # Import here to avoid circular imports
        from core.config import get_wiki_repos_dir

        # Determine repository path
        path = None
        if repo_path:
            path = Path(repo_path)
        else:
            # Try to get path from Memgraph first
            try:
                with get_ingestor() as ingestor:
                    stored_path = ingestor.get_project_path(project_name)
                    if stored_path:
                        path = Path(stored_path)
                        logger.debug(f"Found project path in Memgraph: {path}")
            except Exception as e:
                logger.debug(f"Could not get path from Memgraph: {e}")

            # If not found in Memgraph, check common locations
            if not path or not path.exists():
                # Check wiki_repos directory first (commonly used)
                wiki_path = get_wiki_repos_dir() / project_name
                if wiki_path.exists():
                    path = wiki_path
                else:
                    # Fall back to local repos path
                    path = get_local_repos_path() / project_name

        # Verify repository exists
        if not path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Repository not found. Checked paths: {get_wiki_repos_dir() / project_name}, {get_local_repos_path() / project_name}",
            )

        # Create job using unified task manager
        task_manager = get_task_manager()
        mode_suffix = " (fast mode)" if fast_mode else ""
        job_id = await task_manager.create_task(
            task_type=TaskType.GRAPH_BUILD.value,
            repo_name=project_name,
            initial_message=f"Graph build queued for {project_name}{mode_suffix}",
        )

        # Use the task queue system with concurrency control
        # This ensures only MAX_CONCURRENT_GRAPH_BUILD tasks run at a time
        # Other tasks will stay in PENDING state until a slot is available
        queue_position = await task_manager.run_task(
            job_id,
            _run_graph_build_task,  # Wrapper function for the actual task
            project_name,
            path,
            fast_mode,
        )

        mode_msg = " (fast mode - skipping embeddings)" if fast_mode else ""
        if queue_position == 0:
            status_msg = (
                f"Graph refresh started{mode_msg}. Track progress with job ID: {job_id}"
            )
        else:
            status_msg = f"Graph refresh queued (position {queue_position}){mode_msg}. Track progress with job ID: {job_id}"

        return RefreshProjectResponse(
            success=True, project_name=project_name, job_id=job_id, message=status_msg
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to start refresh for {project_name}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to start refresh: {str(e)}"
        )


async def _proxy_build_to_main(
    project_name: str, repo_path: str | None, fast_mode: bool
) -> RefreshProjectResponse:
    """
    Proxy the build request to the MAIN server.

    This allows REPLICA servers to delegate CPU-intensive builds to MAIN,
    keeping REPLICA responsive for user queries.

    Also creates a local task entry with remote_host set for cross-machine cancel.
    """
    import httpx

    main_host = settings.MEMGRAPH_MAIN_HOST
    # Assume MAIN server runs on same API port (8005)
    main_api_url = f"http://{main_host}:8005/api/graph/projects/{project_name}/refresh"

    params = {}
    if repo_path:
        params["repo_path"] = repo_path
    if fast_mode:
        params["fast_mode"] = "true"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(main_api_url, params=params)

            if response.status_code == 200:
                data = response.json()
                job_id = data.get("job_id")
                logger.info(f"Build request proxied to MAIN, job_id: {job_id}")

                # In shared filesystem environments (NFS), the MAIN server already created
                # the task entry in the shared task_store. We just need to update the
                # remote_host field so this server knows where to proxy cancel requests.
                #
                # DON'T create a new task entry - that would overwrite MAIN's entry!
                task_manager = get_task_manager()
                existing_state = await task_manager.get_task_status(job_id)
                if existing_state:
                    # Task already exists (shared filesystem) - just update remote_host
                    existing_state.remote_host = main_host
                    await task_manager.store.save(existing_state)
                    logger.info(
                        f"Updated existing task {job_id} with remote_host={main_host}"
                    )
                else:
                    # Task doesn't exist locally - create tracking entry
                    # (This case handles non-shared filesystem deployments)
                    await task_manager.create_task(
                        task_id=job_id,
                        task_type=TaskType.GRAPH_BUILD.value,
                        repo_name=project_name,
                        initial_message=f"Build running on {main_host}",
                        remote_host=main_host,
                    )
                    logger.info(
                        f"Created local tracking entry for remote task {job_id}"
                    )

                return RefreshProjectResponse(
                    success=data.get("success", True),
                    project_name=project_name,
                    job_id=job_id,
                    message=f"Build delegated to MAIN server ({main_host}). {data.get('message', '')}",
                )
            else:
                error_detail = response.text
                logger.error(
                    f"MAIN server returned error: {response.status_code} - {error_detail}"
                )
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"MAIN server error: {error_detail}",
                )

    except httpx.ConnectError as e:
        logger.error(f"Cannot connect to MAIN server at {main_api_url}: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Cannot connect to MAIN server at {main_host}. Is it running?",
        )
    except httpx.TimeoutException as e:
        logger.error(f"Timeout connecting to MAIN server: {e}")
        raise HTTPException(
            status_code=504, detail=f"Timeout connecting to MAIN server at {main_host}"
        )


@router.get(
    "/jobs/{job_id}",
    summary="Get Job Status",
    description="Get the status of a background graph operation.",
)
async def get_graph_job_status(job_id: str) -> dict[str, Any]:
    """
    Get the status of a background job.

    Args:
        job_id: The job ID returned when starting the operation

    Returns:
        Job status information
    """
    job = await get_job_status(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return job


@router.post(
    "/projects/build",
    response_model=RefreshProjectResponse,
    summary="Build Graph from Repository",
    description="Build knowledge graph from a repository path.",
)
async def build_graph_from_repo(
    request: RefreshProjectRequest, background_tasks: BackgroundTasks
) -> RefreshProjectResponse:
    """
    Build a new knowledge graph from a repository.

    Args:
        request: Contains project_name and optional repo_path

    Returns:
        RefreshProjectResponse with job ID for tracking progress
    """
    return await refresh_project_graph(
        project_name=request.project_name,
        background_tasks=background_tasks,
        repo_path=request.repo_path,
    )


@router.delete(
    "/database",
    summary="Clean Entire Database",
    description="WARNING: Delete ALL data from the knowledge graph database.",
)
async def clean_entire_database(
    confirm: bool = Query(False, description="Must be true to confirm deletion"),
) -> dict[str, Any]:
    """
    Clean the entire database.

    Args:
        confirm: Must be True to proceed with deletion

    Returns:
        Status of the operation
    """
    if not confirm:
        raise HTTPException(
            status_code=400, detail="Must set confirm=true to delete entire database"
        )

    try:
        with get_ingestor() as ingestor:
            ingestor.clean_database()

            # Clear all Redis L2 cache
            _cache_delete(_CACHE_KEY_PROJECTS, _CACHE_KEY_STATS)

            return {"success": True, "message": "Database cleaned successfully"}

    except Exception as e:
        logger.error(f"Failed to clean database: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to clean database: {str(e)}"
        )


class NodeCodeResponse(BaseModel):
    """Response for node code retrieval."""

    qualified_name: str = Field(..., description="The qualified name of the node")
    name: str = Field(..., description="The short name of the node")
    code: str = Field(..., description="The source code")
    file: str = Field(..., description="The file path")
    start_line: int = Field(..., description="Start line number")
    end_line: int = Field(..., description="End line number")
    language: str = Field("python", description="Programming language")
    docstring: str | None = Field(None, description="Docstring if available")


class NodeSearchMatch(BaseModel):
    """A single node match from fuzzy search."""

    qualified_name: str = Field(..., description="The qualified name of the node")
    name: str = Field(..., description="The short name")
    node_type: str = Field(..., description="Node type: Class, Function, Method")
    match_quality: str = Field(
        ..., description="Match quality: exact, suffix, partial, fuzzy"
    )


class NodeSearchResponse(BaseModel):
    """Response for fuzzy node search - returns multiple potential matches."""

    query: str = Field(..., description="The original search query")
    matches: list[NodeSearchMatch] = Field(
        default_factory=list, description="Potential matches"
    )
    total: int = Field(0, description="Total matches found")
    suggestion: str = Field("", description="Suggested action or best match")


@router.get(
    "/node/{repo}/search",
    response_model=NodeSearchResponse,
    summary="Fuzzy Search for Nodes",
    description="Search for nodes with fuzzy matching when exact qualified_name is not known. Returns multiple potential matches.",
)
async def fuzzy_search_nodes(
    repo: str,
    q: str = Query(
        ...,
        description="Search query - can be partial name, qualified name fragments, or keywords",
    ),
    limit: int = Query(10, ge=1, le=50, description="Maximum matches to return"),
) -> NodeSearchResponse:
    """
    Fuzzy search for code nodes when exact qualified_name is unknown.

    Handles cases like:
    - "tensor.dim" → finds tensor module methods with 'dim' in name
    - "find_highest_dtype" → finds functions containing these words (not necessarily adjacent)
    - " ClassName.method" → searches across all repos for matches

    Args:
        repo: Repository name (used as default scope)
        q: Search query - flexible matching
        limit: Maximum number of matches to return

    Returns:
        NodeSearchResponse with list of potential matches and match quality indicators
    """
    original_query = q.strip()
    logger.info(f"[fuzzy_search_nodes] repo={repo}, query='{original_query}'")

    # Strip file extensions if present
    for ext in [".py", ".pyx", ".so", ".cpp", ".h", ".hpp", ".cc", ".c"]:
        if original_query.endswith(ext):
            original_query = original_query[: -len(ext)]
            break

    matches = []

    try:
        with get_ingestor() as ingestor:
            # Strategy 1: Exact match (fast path)
            exact_query = """
                MATCH (n:Function|Method|Class)
                WHERE n.qualified_name = $qn
                RETURN n.qualified_name AS qualified_name,
                       n.name AS name,
                       labels(n) AS type
                LIMIT 1
            """
            results = ingestor.fetch_all(exact_query, {"qn": original_query})
            if results:
                matches.append(
                    NodeSearchMatch(
                        qualified_name=results[0]["qualified_name"],
                        name=results[0]["name"],
                        node_type=results[0]["type"][0]
                        if results[0]["type"]
                        else "Unknown",
                        match_quality="exact",
                    )
                )

            # Strategy 2: Try with repo prefix
            if not matches and not original_query.startswith(f"{repo}."):
                with_prefix = f"{repo}.{original_query}"
                results = ingestor.fetch_all(exact_query, {"qn": with_prefix})
                if results:
                    matches.append(
                        NodeSearchMatch(
                            qualified_name=results[0]["qualified_name"],
                            name=results[0]["name"],
                            node_type=results[0]["type"][0]
                            if results[0]["type"]
                            else "Unknown",
                            match_quality="exact",
                        )
                    )

            # Strategy 3: Suffix match (e.g., ".tensor.dim" finds any qualified_name ending with ".tensor.dim")
            if len(matches) == 0:
                parts = original_query.split(".")
                if len(parts) >= 2:
                    search_suffix = f".{'.'.join(parts[-2:])}"  # Last 2 parts
                else:
                    search_suffix = f".{parts[-1]}"  # Last part

                suffix_query = """
                    MATCH (n:Function|Method|Class)
                    WHERE n.qualified_name ENDS WITH $suffix
                    RETURN n.qualified_name AS qualified_name,
                           n.name AS name,
                           labels(n) AS type
                    LIMIT $limit
                """
                results = ingestor.fetch_all(
                    suffix_query, {"suffix": search_suffix, "limit": limit}
                )
                for r in results:
                    matches.append(
                        NodeSearchMatch(
                            qualified_name=r["qualified_name"],
                            name=r["name"],
                            node_type=r["type"][0] if r["type"] else "Unknown",
                            match_quality="suffix",
                        )
                    )

            # Strategy 4: Partial match - find nodes where name contains each part of the query
            # For "tensor.dim", this finds nodes with "tensor" AND "dim" in qualified_name (not necessarily adjacent)
            if len(matches) == 0 and len(parts) >= 2:
                # Build a query that matches all parts anywhere in the qualified_name
                # Each part must appear somewhere in the qualified_name
                where_conditions = []
                params = {"limit": limit}
                for i, part in enumerate(parts):
                    if part:  # Skip empty parts
                        where_conditions.append(
                            f"toLower(n.qualified_name) CONTAINS $part{i}"
                        )
                        params[f"part{i}"] = part.lower()

                if where_conditions:
                    # Require ALL parts to match (AND logic)
                    where_clause = " AND ".join(where_conditions)
                    partial_query = f"""
                        MATCH (n:Function|Method|Class)
                        WHERE {where_clause}
                        RETURN DISTINCT n.qualified_name AS qualified_name,
                               n.name AS name,
                               labels(n) AS type,
                               size([p IN $parts WHERE toLower(n.qualified_name) CONTAINS p]) AS match_count
                        LIMIT $limit
                    """
                    params["parts"] = [p.lower() for p in parts if p]

                    results = ingestor.fetch_all(partial_query, params)
                    for r in results:
                        matches.append(
                            NodeSearchMatch(
                                qualified_name=r["qualified_name"],
                                name=r["name"],
                                node_type=r["type"][0] if r["type"] else "Unknown",
                                match_quality="partial",
                            )
                        )

            # Strategy 5: Fuzzy name match - match by function/class name only (last part of qualified_name)
            if len(matches) == 0:
                target_name = parts[-1] if parts else original_query
                name_query = """
                    MATCH (n:Function|Method|Class)
                    WHERE toLower(n.name) = $name_lower
                    RETURN n.qualified_name AS qualified_name,
                           n.name AS name,
                           labels(n) AS type
                    LIMIT $limit
                """
                results = ingestor.fetch_all(
                    name_query, {"name_lower": target_name.lower(), "limit": limit}
                )
                for r in results:
                    matches.append(
                        NodeSearchMatch(
                            qualified_name=r["qualified_name"],
                            name=r["name"],
                            node_type=r["type"][0] if r["type"] else "Unknown",
                            match_quality="fuzzy",
                        )
                    )

            # Strategy 6: CONTAINS match - find nodes where name contains the search term
            if len(matches) == 0:
                contains_query = """
                    MATCH (n:Function|Method|Class)
                    WHERE toLower(n.name) CONTAINS $name_lower
                    RETURN n.qualified_name AS qualified_name,
                           n.name AS name,
                           labels(n) AS type
                    LIMIT $limit
                """
                results = ingestor.fetch_all(
                    contains_query,
                    {"name_lower": original_query.lower(), "limit": limit},
                )
                for r in results:
                    matches.append(
                        NodeSearchMatch(
                            qualified_name=r["qualified_name"],
                            name=r["name"],
                            node_type=r["type"][0] if r["type"] else "Unknown",
                            match_quality="fuzzy",
                        )
                    )

            # Deduplicate matches by qualified_name, keeping best quality
            quality_rank = {"exact": 0, "suffix": 1, "partial": 2, "fuzzy": 3}
            seen = {}
            for match in matches:
                qn = match.qualified_name
                if (
                    qn not in seen
                    or quality_rank[match.match_quality]
                    < quality_rank[seen[qn].match_quality]
                ):
                    seen[qn] = match

            unique_matches = list(seen.values())
            # Sort by match quality, then by qualified_name
            unique_matches.sort(
                key=lambda m: (quality_rank.get(m.match_quality, 99), m.qualified_name)
            )
            unique_matches = unique_matches[:limit]

            # Generate suggestion
            suggestion = ""
            if unique_matches:
                best = unique_matches[0]
                if best.match_quality == "exact":
                    suggestion = f"Found exact match: [[{best.qualified_name}]]"
                elif best.match_quality == "suffix":
                    suggestion = f"Closest match: [[{best.qualified_name}]]"
                else:
                    suggestion = f"Best match: [[{best.qualified_name}]]"
            else:
                suggestion = "No matches found. Try a different search term or use find_nodes tool."

            return NodeSearchResponse(
                query=original_query,
                matches=unique_matches,
                total=len(unique_matches),
                suggestion=suggestion,
            )

    except Exception as e:
        logger.error(f"Failed to fuzzy search nodes: {e}", exc_info=True)
        return NodeSearchResponse(
            query=original_query,
            matches=[],
            total=0,
            suggestion=f"Search error: {str(e)}",
        )


@router.get(
    "/node/{repo}/code",
    response_model=NodeCodeResponse,
    summary="Get Node Source Code",
    description="Retrieve source code for a node by its qualified name.",
)
async def get_node_code(
    repo: str,
    qualified_name: str = Query(
        ...,
        description="Fully qualified name of the node (e.g., 'repo.module.Class.method')",
    ),
) -> NodeCodeResponse:
    """
    Get the source code for a specific node.

    Args:
        repo: Repository/project name (used as fallback, actual repo extracted from qualified_name)
        qualified_name: Fully qualified name of the code element

    Returns:
        NodeCodeResponse with source code and metadata
    """
    # Normalize: strip common file extensions from qualified_name
    # Users may reference files as "flops_counter.py" but qualified_name is "calflops.flops_counter"
    original_qn = qualified_name
    for ext in [".py", ".pyx", ".so", ".cpp", ".h", ".hpp", ".cc", ".c"]:
        if qualified_name.endswith(ext):
            qualified_name = qualified_name[: -len(ext)]
            logger.debug(
                f"[get_node_code] Stripped file extension: '{original_qn}' -> '{qualified_name}'"
            )
            break

    logger.info(f"[get_node_code] repo={repo}, qualified_name={qualified_name}")

    try:
        with get_ingestor() as ingestor:
            # Query the graph for node location
            query = """
                MATCH (n)
                WHERE n.qualified_name = $qn
                OPTIONAL MATCH (m:Module)-[:DEFINES]->(n)
                RETURN n.name AS name,
                       n.qualified_name AS qualified_name,
                       n.start_line AS start_line,
                       n.end_line AS end_line,
                       COALESCE(n.path, m.path) AS path,
                       n.docstring AS docstring,
                       labels(n) AS node_labels
                LIMIT 1
            """
            results = ingestor.fetch_all(query, {"qn": qualified_name})

            # If not found, try with repo prefix from URL
            if not results and not qualified_name.startswith(f"{repo}."):
                full_qn = f"{repo}.{qualified_name}"
                results = ingestor.fetch_all(query, {"qn": full_qn})
                if results:
                    qualified_name = full_qn
                    logger.info(
                        f"[get_node_code] Found with URL repo prefix: {qualified_name}"
                    )

            # Parse qualified_name parts for fallback searches
            parts = qualified_name.split(".")

            # If still not found, search by suffix across all repos (for Unresolved cross-repo refs)
            if not results:
                # Extract the last part(s) as search pattern
                if len(parts) >= 2:
                    # Use last 2 parts: parent.basename
                    search_suffix = f".{parts[-2]}.{parts[-1]}"
                else:
                    # Use only the basename (e.g., "flops_counter" from "flops_counter.py")
                    search_suffix = f".{parts[-1]}"
                suffix_query = """
                    MATCH (n)
                    WHERE n.qualified_name ENDS WITH $suffix
                    OPTIONAL MATCH (m:Module)-[:DEFINES]->(n)
                    RETURN n.name AS name,
                           n.qualified_name AS qualified_name,
                           n.start_line AS start_line,
                           n.end_line AS end_line,
                           COALESCE(n.path, m.path) AS path,
                           n.docstring AS docstring,
                           labels(n) AS node_labels
                    LIMIT 1
                """
                results = ingestor.fetch_all(suffix_query, {"suffix": search_suffix})
                if results:
                    # Update qualified_name to the one found in DB
                    qualified_name = results[0].get("qualified_name", qualified_name)
                    logger.info(
                        f"[get_node_code] Found via suffix search: {qualified_name}"
                    )

            # If still not found, try removing class name from qualified_name
            # This handles cases where frontend has "module.Class.method" but DB has "module.method"
            if not results and len(parts) >= 3:
                # Try: remove second-to-last part (potential class name)
                # e.g., "repo.module.Class.method" -> "repo.module.method"
                without_class = ".".join(parts[:-2] + [parts[-1]])
                results = ingestor.fetch_all(query, {"qn": without_class})
                if results:
                    qualified_name = without_class
                    logger.info(
                        f"[get_node_code] Found by removing class name: {qualified_name}"
                    )
                else:
                    # Also try suffix search with just the function name
                    name_suffix = f".{parts[-1]}"
                    suffix_query_name = """
                        MATCH (n)
                        WHERE n.qualified_name ENDS WITH $suffix AND n.name = $name
                        OPTIONAL MATCH (m:Module)-[:DEFINES]->(n)
                        RETURN n.name AS name,
                               n.qualified_name AS qualified_name,
                               n.start_line AS start_line,
                               n.end_line AS end_line,
                               COALESCE(n.path, m.path) AS path,
                               n.docstring AS docstring,
                               labels(n) AS node_labels
                        LIMIT 1
                    """
                    results = ingestor.fetch_all(
                        suffix_query_name, {"suffix": name_suffix, "name": parts[-1]}
                    )
                    if results:
                        qualified_name = results[0].get(
                            "qualified_name", qualified_name
                        )
                        logger.info(
                            f"[get_node_code] Found via name suffix search: {qualified_name}"
                        )

            # Fallback: Partial match - find nodes where qualified_name contains all parts
            # For "tensor.dim", finds nodes with both "tensor" AND "dim" somewhere in qualified_name
            if not results and len(parts) >= 2:
                where_conditions = []
                params = {}
                for i, part in enumerate(parts):
                    if part:  # Skip empty parts
                        where_conditions.append(
                            f"toLower(n.qualified_name) CONTAINS $part{i}"
                        )
                        params[f"part{i}"] = part.lower()

                if where_conditions:
                    where_clause = " AND ".join(where_conditions)
                    partial_query = f"""
                        MATCH (n)
                        WHERE {where_clause}
                        OPTIONAL MATCH (m:Module)-[:DEFINES]->(n)
                        RETURN n.name AS name,
                               n.qualified_name AS qualified_name,
                               n.start_line AS start_line,
                               n.end_line AS end_line,
                               COALESCE(n.path, m.path) AS path,
                               n.docstring AS docstring,
                               labels(n) AS node_labels
                        LIMIT 1
                    """
                    results = ingestor.fetch_all(partial_query, params)
                    if results:
                        qualified_name = results[0].get(
                            "qualified_name", qualified_name
                        )
                        logger.info(
                            f"[get_node_code] Found via partial match: {qualified_name}"
                        )

            # Fallback: Name-only match - find nodes with matching short name
            # For "dim", finds any function/class/method named "dim"
            if not results:
                target_name = parts[-1] if parts else qualified_name
                name_query = """
                    MATCH (n)
                    WHERE toLower(n.name) = $name_lower
                    OPTIONAL MATCH (m:Module)-[:DEFINES]->(n)
                    RETURN n.name AS name,
                           n.qualified_name AS qualified_name,
                           n.start_line AS start_line,
                           n.end_line AS end_line,
                           COALESCE(n.path, m.path) AS path,
                           n.docstring AS docstring,
                           labels(n) AS node_labels
                    LIMIT 1
                """
                results = ingestor.fetch_all(
                    name_query, {"name_lower": target_name.lower()}
                )
                if results:
                    qualified_name = results[0].get("qualified_name", qualified_name)
                    logger.info(
                        f"[get_node_code] Found via name match: {qualified_name}"
                    )

            # Extract actual repo from qualified_name (first part is always the repo name)
            # This handles cross-repo references correctly
            actual_repo = (
                qualified_name.split(".")[0] if "." in qualified_name else repo
            )
            if actual_repo != repo:
                logger.info(
                    f"[get_node_code] Cross-repo reference: URL repo={repo}, actual repo={actual_repo}"
                )

            if not results:
                raise HTTPException(
                    status_code=404, detail=f"Node not found: {qualified_name}"
                )

            node = results[0]
            node_labels = node.get("node_labels", [])
            file_path = node.get("path")
            start_line = node.get("start_line")
            end_line = node.get("end_line")

            # For File nodes, path is sufficient (we return the entire file)
            is_file_node = "File" in node_labels
            if not file_path:
                raise HTTPException(
                    status_code=404,
                    detail=f"Node location information incomplete for: {qualified_name}",
                )

            # For non-File nodes (Class/Function/Method), start_line and end_line are required
            if not is_file_node and (not start_line or not end_line):
                raise HTTPException(
                    status_code=404,
                    detail=f"Node location information incomplete for: {qualified_name}",
                )

            # Determine the absolute file path
            # Use actual_repo (extracted from qualified_name) for cross-repo support
            # Get project path from database (supports local paths, not just wiki_repos)
            project_path = ingestor.get_project_path(actual_repo)
            if project_path:
                repo_path = Path(project_path)
                # Check if the path exists, if not try the new data directory
                if not repo_path.exists():
                    from core.config import get_wiki_repos_dir

                    new_repo_path = get_wiki_repos_dir() / actual_repo
                    if new_repo_path.exists():
                        logger.info(
                            f"[get_node_code] Old path {repo_path} not found, using new path {new_repo_path}"
                        )
                        repo_path = new_repo_path
            else:
                # Fallback to wiki_repos for backwards compatibility
                from core.config import get_wiki_repos_dir

                repo_path = get_wiki_repos_dir() / actual_repo

            abs_file_path = repo_path / file_path
            logger.debug(f"[get_node_code] Looking for file at: {abs_file_path}")

            if not abs_file_path.exists():
                # Try alternative paths
                alt_paths = [
                    Path(file_path),  # Absolute path
                    repo_path / file_path.lstrip("/"),  # Without leading slash
                ]
                for alt_path in alt_paths:
                    if alt_path.exists():
                        abs_file_path = alt_path
                        break
                else:
                    logger.error(
                        f"Source file not found. Tried: {abs_file_path}, alternatives: {alt_paths}"
                    )
                    raise HTTPException(
                        status_code=404, detail=f"Source file not found: {file_path}"
                    )

            # Read the source code
            try:
                with open(abs_file_path, encoding="utf-8") as f:
                    if is_file_node:
                        # For File nodes, read the entire file
                        code = f.read()
                        # Set start_line and end_line to full file range
                        start_line = 1
                        end_line = code.count("\n") + 1
                    else:
                        # For Class/Function/Method, read specific line range
                        lines = f.readlines()
                        # Extract the relevant lines (1-indexed to 0-indexed)
                        code_lines = lines[start_line - 1 : end_line]
                        code = "".join(code_lines)
            except Exception as e:
                logger.error(f"Failed to read source file: {e}")
                raise HTTPException(
                    status_code=500, detail=f"Failed to read source file: {str(e)}"
                )

            # Determine language from file extension
            ext = abs_file_path.suffix.lower()
            language_map = {
                ".py": "python",
                ".js": "javascript",
                ".ts": "typescript",
                ".tsx": "tsx",
                ".jsx": "jsx",
                ".java": "java",
                ".cpp": "cpp",
                ".c": "c",
                ".h": "c",
                ".hpp": "cpp",
                ".rs": "rust",
                ".go": "go",
                ".rb": "ruby",
                ".php": "php",
                ".cs": "csharp",
                ".swift": "swift",
                ".kt": "kotlin",
                ".scala": "scala",
            }
            language = language_map.get(ext, "text")

            return NodeCodeResponse(
                qualified_name=node.get("qualified_name") or qualified_name,
                name=node.get("name") or qualified_name.split(".")[-1],
                code=code,
                file=file_path,
                start_line=start_line,
                end_line=end_line,
                language=language,
                docstring=node.get("docstring"),
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get node code: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get node code: {str(e)}"
        )


# ---------------------------------------------------------------------------
# Batch code endpoint
# ---------------------------------------------------------------------------


class BatchCodeRequest(BaseModel):
    """Request body for batch code retrieval."""

    qualified_names: list[str] = Field(
        ..., description="List of qualified names to fetch code for", max_length=200
    )


class BatchCodeItem(BaseModel):
    """A single item in batch code response."""

    qualified_name: str
    name: str | None = None
    code: str | None = None
    file: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    language: str | None = None
    docstring: str | None = None
    error: str | None = None


def _resolve_node_code(
    ingestor: MemgraphIngestor,
    repo: str,
    qualified_name: str,
) -> BatchCodeItem:
    """Resolve a single qualified_name to its source code.

    Shared helper used by both single and batch endpoints.
    Returns BatchCodeItem (with error field set on failure).
    """
    original_qn = qualified_name

    # Strip common file extensions
    for ext in [".py", ".pyx", ".so", ".cpp", ".h", ".hpp", ".cc", ".c"]:
        if qualified_name.endswith(ext):
            qualified_name = qualified_name[: -len(ext)]
            break

    base_query = """
        MATCH (n)
        WHERE n.qualified_name = $qn
        OPTIONAL MATCH (m:Module)-[:DEFINES]->(n)
        RETURN n.name AS name,
               n.qualified_name AS qualified_name,
               n.start_line AS start_line,
               n.end_line AS end_line,
               COALESCE(n.path, m.path) AS path,
               n.docstring AS docstring,
               labels(n) AS node_labels
        LIMIT 1
    """

    results = ingestor.fetch_all(base_query, {"qn": qualified_name})

    # Strategy 2: prefix with repo from URL
    if not results and not qualified_name.startswith(f"{repo}."):
        full_qn = f"{repo}.{qualified_name}"
        results = ingestor.fetch_all(base_query, {"qn": full_qn})
        if results:
            qualified_name = full_qn

    parts = qualified_name.split(".")

    # Strategy 3: suffix search
    if not results:
        if len(parts) >= 2:
            search_suffix = f".{parts[-2]}.{parts[-1]}"
        else:
            search_suffix = f".{parts[-1]}"
        suffix_query = """
            MATCH (n)
            WHERE n.qualified_name ENDS WITH $suffix
            OPTIONAL MATCH (m:Module)-[:DEFINES]->(n)
            RETURN n.name AS name,
                   n.qualified_name AS qualified_name,
                   n.start_line AS start_line,
                   n.end_line AS end_line,
                   COALESCE(n.path, m.path) AS path,
                   n.docstring AS docstring,
                   labels(n) AS node_labels
            LIMIT 1
        """
        results = ingestor.fetch_all(suffix_query, {"suffix": search_suffix})
        if results:
            qualified_name = results[0].get("qualified_name", qualified_name)

    # Strategy 4: remove class name
    if not results and len(parts) >= 3:
        without_class = ".".join(parts[:-2] + [parts[-1]])
        results = ingestor.fetch_all(base_query, {"qn": without_class})
        if results:
            qualified_name = without_class
        else:
            name_suffix = f".{parts[-1]}"
            suffix_query_name = """
                MATCH (n)
                WHERE n.qualified_name ENDS WITH $suffix AND n.name = $name
                OPTIONAL MATCH (m:Module)-[:DEFINES]->(n)
                RETURN n.name AS name,
                       n.qualified_name AS qualified_name,
                       n.start_line AS start_line,
                       n.end_line AS end_line,
                       COALESCE(n.path, m.path) AS path,
                       n.docstring AS docstring,
                       labels(n) AS node_labels
                LIMIT 1
            """
            results = ingestor.fetch_all(
                suffix_query_name, {"suffix": name_suffix, "name": parts[-1]}
            )
            if results:
                qualified_name = results[0].get("qualified_name", qualified_name)

    # Strategy 5: partial match
    if not results and len(parts) >= 2:
        where_conditions = []
        params: dict[str, Any] = {}
        for i, part in enumerate(parts):
            if part:
                where_conditions.append(
                    f"toLower(n.qualified_name) CONTAINS $part{i}"
                )
                params[f"part{i}"] = part.lower()
        if where_conditions:
            where_clause = " AND ".join(where_conditions)
            partial_query = f"""
                MATCH (n)
                WHERE {where_clause}
                OPTIONAL MATCH (m:Module)-[:DEFINES]->(n)
                RETURN n.name AS name,
                       n.qualified_name AS qualified_name,
                       n.start_line AS start_line,
                       n.end_line AS end_line,
                       COALESCE(n.path, m.path) AS path,
                       n.docstring AS docstring,
                       labels(n) AS node_labels
                LIMIT 1
            """
            results = ingestor.fetch_all(partial_query, params)
            if results:
                qualified_name = results[0].get("qualified_name", qualified_name)

    # Strategy 6: name-only match
    if not results:
        target_name = parts[-1] if parts else qualified_name
        name_query = """
            MATCH (n)
            WHERE toLower(n.name) = $name_lower
            OPTIONAL MATCH (m:Module)-[:DEFINES]->(n)
            RETURN n.name AS name,
                   n.qualified_name AS qualified_name,
                   n.start_line AS start_line,
                   n.end_line AS end_line,
                   COALESCE(n.path, m.path) AS path,
                   n.docstring AS docstring,
                   labels(n) AS node_labels
            LIMIT 1
        """
        results = ingestor.fetch_all(
            name_query, {"name_lower": target_name.lower()}
        )
        if results:
            qualified_name = results[0].get("qualified_name", qualified_name)

    if not results:
        return BatchCodeItem(qualified_name=original_qn, error="not found")

    node = results[0]
    node_labels = node.get("node_labels", [])
    file_path = node.get("path")
    start_line = node.get("start_line")
    end_line = node.get("end_line")
    is_file_node = "File" in node_labels

    if not file_path:
        return BatchCodeItem(qualified_name=original_qn, error="no file path")

    if not is_file_node and (not start_line or not end_line):
        return BatchCodeItem(qualified_name=original_qn, error="incomplete location")

    # Resolve absolute file path
    actual_repo = qualified_name.split(".")[0] if "." in qualified_name else repo
    project_path = ingestor.get_project_path(actual_repo)
    if project_path:
        repo_path = Path(project_path)
        if not repo_path.exists():
            from core.config import get_wiki_repos_dir

            new_repo_path = get_wiki_repos_dir() / actual_repo
            if new_repo_path.exists():
                repo_path = new_repo_path
    else:
        from core.config import get_wiki_repos_dir

        repo_path = get_wiki_repos_dir() / actual_repo

    abs_file_path = repo_path / file_path
    if not abs_file_path.exists():
        alt_paths = [Path(file_path), repo_path / file_path.lstrip("/")]
        for alt in alt_paths:
            if alt.exists():
                abs_file_path = alt
                break
        else:
            return BatchCodeItem(qualified_name=original_qn, error=f"file not found: {file_path}")

    try:
        with open(abs_file_path, encoding="utf-8") as f:
            if is_file_node:
                code = f.read()
                start_line = 1
                end_line = code.count("\n") + 1
            else:
                lines = f.readlines()
                code = "".join(lines[start_line - 1 : end_line])
    except Exception as e:
        return BatchCodeItem(qualified_name=original_qn, error=f"read error: {e}")

    ext = abs_file_path.suffix.lower()
    language_map = {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".tsx": "tsx", ".jsx": "jsx", ".java": "java", ".cpp": "cpp",
        ".c": "c", ".h": "c", ".hpp": "cpp", ".rs": "rust", ".go": "go",
        ".rb": "ruby", ".php": "php", ".cs": "csharp", ".swift": "swift",
        ".kt": "kotlin", ".scala": "scala", ".cu": "cuda",
    }

    return BatchCodeItem(
        qualified_name=node.get("qualified_name") or qualified_name,
        name=node.get("name") or qualified_name.split(".")[-1],
        code=code,
        file=file_path,
        start_line=start_line,
        end_line=end_line,
        language=language_map.get(ext, "text"),
        docstring=node.get("docstring"),
    )


@router.post(
    "/node/{repo}/batch-code",
    response_model=list[BatchCodeItem],
    summary="Batch Get Node Source Code",
    description="Retrieve source code for multiple nodes in one request.",
)
async def batch_get_node_codes(repo: str, body: BatchCodeRequest) -> list[BatchCodeItem]:
    """Batch fetch source code for multiple qualified_names."""
    logger.info(f"[batch_get_node_codes] repo={repo}, count={len(body.qualified_names)}")

    try:
        with get_ingestor() as ingestor:
            results = []
            for qn in body.qualified_names:
                try:
                    item = _resolve_node_code(ingestor, repo, qn)
                    results.append(item)
                except Exception as e:
                    logger.warning(f"[batch_get_node_codes] Error for {qn}: {e}")
                    results.append(BatchCodeItem(qualified_name=qn, error=str(e)))
            return results
    except Exception as e:
        logger.error(f"[batch_get_node_codes] Failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class NodeInfoResponse(BaseModel):
    """Response for node info retrieval - handles all node types."""

    qualified_name: str = Field(..., description="The qualified name of the node")
    name: str = Field(..., description="The short name of the node")
    node_type: str = Field(
        ...,
        description="Node type: Class, Function, Method, Folder, File, Module, etc.",
    )
    exists: bool = Field(True, description="Whether the node exists in the graph")
    has_code: bool = Field(
        True, description="Whether the node has source code to display"
    )
    file: str | None = Field(None, description="File path (if applicable)")
    path: str | None = Field(None, description="Directory path (for Folder nodes)")
    start_line: int | None = Field(None, description="Start line number (if has_code)")
    end_line: int | None = Field(None, description="End line number (if has_code)")
    docstring: str | None = Field(None, description="Docstring if available")
    child_count: int = Field(
        0, description="Number of children (for Folder/File nodes)"
    )
    action: str = Field(
        "view",
        description="Suggested UI action: 'view_code', 'browse_folder', 'view_file', or 'not_found'",
    )


@router.get(
    "/node/{repo}/info",
    response_model=NodeInfoResponse,
    summary="Get Node Information",
    description="Get information about a node including type, existence, and suggested action for UI.",
)
async def get_node_info(
    repo: str,
    qualified_name: str = Query(..., description="Fully qualified name of the node"),
) -> NodeInfoResponse:
    """
    Get information about a node for smart link handling.

    This endpoint handles all node types and returns appropriate action hints:
    - 'view_code': For Class/Function/Method - open code viewer
    - 'browse_folder': For Folder/Package - open file browser
    - 'view_file': For File (module) - open file view
    - 'not_found': Node doesn't exist in graph

    Args:
        repo: Repository name
        qualified_name: Fully qualified name of the node

    Returns:
        NodeInfoResponse with node type and suggested UI action
    """
    # Normalize: strip common file extensions from qualified_name
    # Users may reference files as "flops_counter.py" but qualified_name is "calflops.flops_counter"
    original_qn = qualified_name
    for ext in [".py", ".pyx", ".so", ".cpp", ".h", ".hpp", ".cc", ".c"]:
        if qualified_name.endswith(ext):
            qualified_name = qualified_name[: -len(ext)]
            logger.debug(
                f"[get_node_info] Stripped file extension: '{original_qn}' -> '{qualified_name}'"
            )
            break

    logger.info(f"[get_node_info] repo={repo}, qualified_name={qualified_name}")

    try:
        with get_ingestor() as ingestor:
            # Query for node with type information
            query = """
                MATCH (n)
                WHERE n.qualified_name = $qn
                OPTIONAL MATCH (m:Module)-[:DEFINES]->(n)
                RETURN n.name AS name,
                       n.qualified_name AS qualified_name,
                       n.path AS path,
                       n.start_line AS start_line,
                       n.end_line AS end_line,
                       n.docstring AS docstring,
                       n.is_package AS is_package,
                       labels(n) AS node_labels
                LIMIT 1
            """
            results = ingestor.fetch_all(query, {"qn": qualified_name})

            # Fallback: try with repo prefix
            if not results and not qualified_name.startswith(f"{repo}."):
                full_qn = f"{repo}.{qualified_name}"
                results = ingestor.fetch_all(query, {"qn": full_qn})
                if results:
                    qualified_name = full_qn

            # Fallback: suffix search for cross-repo references
            if not results:
                parts = qualified_name.split(".")
                if len(parts) >= 2:
                    # Use last 2 parts: parent.basename
                    search_suffix = f".{parts[-2]}.{parts[-1]}"
                else:
                    # Use only the basename (e.g., "flops_counter" from "flops_counter.py")
                    search_suffix = f".{parts[-1]}"
                suffix_query = """
                    MATCH (n)
                    WHERE n.qualified_name ENDS WITH $suffix
                    RETURN n.name AS name,
                           n.qualified_name AS qualified_name,
                           n.path AS path,
                           n.start_line AS start_line,
                           n.end_line AS end_line,
                           n.docstring AS docstring,
                           n.is_package AS is_package,
                           labels(n) AS node_labels
                    LIMIT 1
                """
                results = ingestor.fetch_all(suffix_query, {"suffix": search_suffix})
                if results:
                    qualified_name = results[0].get("qualified_name", qualified_name)

            # Fallback: Partial match - find nodes where qualified_name contains all parts
            # For "tensor.dim", finds nodes with both "tensor" AND "dim" somewhere in qualified_name
            if not results and len(parts) >= 2:
                where_conditions = []
                params = {}
                for i, part in enumerate(parts):
                    if part:  # Skip empty parts
                        where_conditions.append(
                            f"toLower(n.qualified_name) CONTAINS $part{i}"
                        )
                        params[f"part{i}"] = part.lower()

                if where_conditions:
                    where_clause = " AND ".join(where_conditions)
                    partial_query = f"""
                        MATCH (n:Function|Method|Class)
                        WHERE {where_clause}
                        RETURN n.name AS name,
                               n.qualified_name AS qualified_name,
                               n.path AS path,
                               n.start_line AS start_line,
                               n.end_line AS end_line,
                               n.docstring AS docstring,
                               labels(n) AS node_labels
                        LIMIT 1
                    """
                    results = ingestor.fetch_all(partial_query, params)
                    if results:
                        qualified_name = results[0].get(
                            "qualified_name", qualified_name
                        )
                        logger.info(
                            f"[get_node_info] Found via partial match: {qualified_name}"
                        )

            # Fallback: Name-only match - find nodes with matching short name
            # For "dim", finds any function/class/method named "dim"
            if not results:
                target_name = parts[-1] if parts else qualified_name
                name_query = """
                    MATCH (n:Function|Method|Class)
                    WHERE toLower(n.name) = $name_lower
                    RETURN n.name AS name,
                           n.qualified_name AS qualified_name,
                           n.path AS path,
                           n.start_line AS start_line,
                           n.end_line AS end_line,
                           n.docstring AS docstring,
                           labels(n) AS node_labels
                    LIMIT 1
                """
                results = ingestor.fetch_all(
                    name_query, {"name_lower": target_name.lower()}
                )
                if results:
                    qualified_name = results[0].get("qualified_name", qualified_name)
                    logger.info(
                        f"[get_node_info] Found via name match: {qualified_name}"
                    )

            if not results:
                return NodeInfoResponse(
                    qualified_name=qualified_name,
                    name=qualified_name.split(".")[-1],
                    node_type="Unknown",
                    exists=False,
                    has_code=False,
                    action="not_found",
                )

            node = results[0]
            node_labels = node.get("node_labels", [])
            name = node.get("name", qualified_name.split(".")[-1])
            path = node.get("path")
            start_line = node.get("start_line")
            end_line = node.get("end_line")

            # Determine node type and action
            node_type = "Unknown"
            action = "not_found"
            has_code = False
            child_count = 0

            if "Class" in node_labels:
                node_type = "Class"
                action = "view_code"
                has_code = True
            elif "Function" in node_labels:
                node_type = "Function"
                action = "view_code"
                has_code = True
            elif "Method" in node_labels:
                node_type = "Method"
                action = "view_code"
                has_code = True
            elif "Folder" in node_labels:
                node_type = "Package" if node.get("is_package") else "Folder"
                action = "browse_folder"
                has_code = False
                # Count children
                child_query = """
                    MATCH (f:Folder {qualified_name: $qn})-[:CONTAINS_FILE|CONTAINS_FOLDER]->(n)
                    RETURN count(n) AS count
                """
                child_results = ingestor.fetch_all(child_query, {"qn": qualified_name})
                child_count = child_results[0].get("count", 0) if child_results else 0
            elif "File" in node_labels:
                node_type = "File"
                # Check if it's a source file with exports
                if start_line and end_line:
                    action = "view_code"
                    has_code = True
                else:
                    action = "view_file"
                    has_code = False
                # Count exported items
                child_query = """
                    MATCH (f:File {qualified_name: $qn})-[:DEFINES]->(n)
                    RETURN count(n) AS count
                """
                child_results = ingestor.fetch_all(child_query, {"qn": qualified_name})
                child_count = child_results[0].get("count", 0) if child_results else 0
            elif (
                "ModuleInterface" in node_labels
                or "ModuleImplementation" in node_labels
            ):
                node_type = "Module"
                action = "view_file"
                has_code = False

            return NodeInfoResponse(
                qualified_name=node.get("qualified_name", qualified_name),
                name=name,
                node_type=node_type,
                exists=True,
                has_code=has_code,
                file=path if has_code else None,
                path=path if not has_code else None,
                start_line=start_line,
                end_line=end_line,
                docstring=node.get("docstring"),
                child_count=child_count,
                action=action,
            )

    except Exception as e:
        logger.error(f"Failed to get node info: {e}", exc_info=True)
        # Return a safe response indicating error
        return NodeInfoResponse(
            qualified_name=qualified_name,
            name=qualified_name.split(".")[-1],
            node_type="Error",
            exists=False,
            has_code=False,
            action="not_found",
        )


class FolderChildItem(BaseModel):
    """A single item in a folder."""

    qualified_name: str = Field(..., description="Qualified name of the item")
    name: str = Field(..., description="Short name")
    node_type: str = Field(..., description="Type: File, Folder, Package")
    is_package: bool = Field(False, description="Whether it's a package")
    child_count: int = Field(0, description="Number of children (for folders)")


class FolderChildrenResponse(BaseModel):
    """Response for folder children query."""

    qualified_name: str = Field(..., description="Folder qualified name")
    name: str = Field(..., description="Folder name")
    path: str = Field(..., description="Folder path")
    children: list[FolderChildItem] = Field(
        default_factory=list, description="Child items"
    )


@router.get(
    "/node/{repo}/children",
    response_model=FolderChildrenResponse,
    summary="Get Folder Children",
    description="Get the children (files and subfolders) of a Folder node.",
)
async def get_folder_children(
    repo: str,
    qualified_name: str = Query(..., description="Qualified name of the folder"),
) -> FolderChildrenResponse:
    """
    Get children of a Folder node.

    Args:
        repo: Repository name
        qualified_name: Qualified name of the folder

    Returns:
        FolderChildrenResponse with list of children
    """
    # Strip file extensions if present
    original_qn = qualified_name
    for ext in [".py", ".pyx", ".so", ".cpp", ".h", ".hpp", ".cc", ".c"]:
        if qualified_name.endswith(ext):
            qualified_name = qualified_name[: -len(ext)]
            break

    logger.info(f"[get_folder_children] repo={repo}, qualified_name={qualified_name}")

    try:
        with get_ingestor() as ingestor:
            # Query for folder and its children
            query = """
                MATCH (f:Folder {qualified_name: $qn})
                OPTIONAL MATCH (f)-[r:CONTAINS_FILE|CONTAINS_FOLDER]->(child)
                RETURN f.path AS path,
                       f.name AS name,
                       f.qualified_name AS qualified_name,
                       child.qualified_name AS child_qn,
                       child.name AS child_name,
                       child.is_package AS child_is_package,
                       labels(child) AS child_labels
                ORDER BY child_name
            """
            results = ingestor.fetch_all(query, {"qn": qualified_name})

            if not results:
                # Try with repo prefix
                if not qualified_name.startswith(f"{repo}."):
                    full_qn = f"{repo}.{qualified_name}"
                    results = ingestor.fetch_all(query, {"qn": full_qn})
                    if results:
                        qualified_name = full_qn

            if not results:
                # Try as Project root node (when browsing repo root)
                project_query = """
                    MATCH (p:Project {name: $qn})
                    OPTIONAL MATCH (p)-[r:CONTAINS_FILE|CONTAINS_FOLDER]->(child)
                    RETURN p.path AS path,
                           p.name AS name,
                           p.name AS qualified_name,
                           child.qualified_name AS child_qn,
                           child.name AS child_name,
                           child.is_package AS child_is_package,
                           labels(child) AS child_labels
                    ORDER BY child_name
                """
                results = ingestor.fetch_all(project_query, {"qn": repo})

            if not results:
                raise HTTPException(
                    status_code=404, detail=f"Folder not found: {original_qn}"
                )

            # Extract folder info and children
            folder_path = None
            folder_name = None
            folder_qn = None
            children_map = {}  # qualified_name -> child info

            for row in results:
                folder_path = folder_path or row.get("path")
                folder_name = folder_name or row.get("name")
                folder_qn = folder_qn or row.get("qualified_name")

                child_qn = row.get("child_qn")
                if child_qn:
                    child_labels = row.get("child_labels", [])
                    # Determine node type
                    if "Folder" in child_labels:
                        node_type = (
                            "Package" if row.get("child_is_package") else "Folder"
                        )
                    elif "File" in child_labels:
                        node_type = "File"
                    else:
                        node_type = child_labels[0] if child_labels else "Unknown"

                    # Count children for folders, or symbols for files
                    child_count = 0
                    if node_type in ("Folder", "Package"):
                        count_query = """
                            MATCH (n {qualified_name: $qn})-[:CONTAINS_FILE|CONTAINS_FOLDER]->(x)
                            RETURN count(x) AS count
                        """
                        count_results = ingestor.fetch_all(
                            count_query, {"qn": child_qn}
                        )
                        child_count = (
                            count_results[0].get("count", 0) if count_results else 0
                        )
                    elif node_type == "File":
                        # Count exported symbols (classes, functions, etc.)
                        sym_query = """
                            MATCH (n {qualified_name: $qn})-[:CONTAINS_DEFINITION]->(d)
                            RETURN count(d) AS count
                        """
                        sym_results = ingestor.fetch_all(
                            sym_query, {"qn": child_qn}
                        )
                        child_count = (
                            sym_results[0].get("count", 0) if sym_results else 0
                        )

                    children_map[child_qn] = {
                        "qualified_name": child_qn,
                        "name": row.get("child_name", child_qn.split(".")[-1]),
                        "node_type": node_type,
                        "is_package": bool(row.get("child_is_package") or False),
                        "child_count": child_count,
                    }

            # Sort by name and group (folders first, then files)
            sorted_children = sorted(
                children_map.values(),
                key=lambda x: (
                    0 if x["node_type"] in ("Folder", "Package") else 1,
                    x["name"].lower(),
                ),
            )

            return FolderChildrenResponse(
                qualified_name=folder_qn or qualified_name,
                name=folder_name or qualified_name.split(".")[-1],
                path=folder_path or "",
                children=[FolderChildItem(**c) for c in sorted_children],
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get folder children: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get folder children: {str(e)}"
        )


# ============== Build Queue Management ==============


@router.get(
    "/queue/status",
    summary="Get Build Queue Status",
    description="Get the current status of the graph build queue, including running and queued jobs.",
)
async def get_queue_status() -> dict[str, Any]:
    """
    Get the current build queue status.

    Returns:
        Queue status including current job, queued jobs, and throttling settings
    """
    scheduler = get_build_scheduler()
    return scheduler.get_queue_status()


@router.post(
    "/queue/throttling",
    summary="Update Throttling Settings",
    description="Adjust write throttling to balance build speed vs read responsiveness.",
)
async def update_throttling(
    level: str | None = Query(
        None, description="Throttle level: NONE, LOW, MEDIUM, HIGH, EXTREME"
    ),
    write_delay_ms: int | None = Query(
        None, ge=0, le=200, description="Delay between batches (ms)"
    ),
    batch_size: int | None = Query(
        None, ge=100, le=2000, description="Batch size for writes"
    ),
) -> dict[str, Any]:
    """
    Update throttling settings for graph builds.

    Use level for preset configurations:
    - NONE: Fastest builds, may block reads
    - LOW: Fast builds, minimal read impact
    - MEDIUM: Balanced (default)
    - HIGH: Slower builds, reads prioritized
    - EXTREME: Slowest builds, maximum read priority

    Or use write_delay_ms and batch_size for fine-tuned control.

    Returns:
        Updated throttling settings
    """
    scheduler = get_build_scheduler()

    if level:
        from api.services.build_scheduler import ThrottleLevel

        try:
            throttle_level = ThrottleLevel[level.upper()]
            scheduler.set_throttle_level(throttle_level)
        except KeyError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid throttle level: {level}. Valid values: NONE, LOW, MEDIUM, HIGH, EXTREME",
            )
    elif write_delay_ms is not None or batch_size is not None:
        scheduler.set_throttling(write_delay_ms=write_delay_ms, batch_size=batch_size)

    return scheduler.get_queue_status()["throttling"]


@router.post(
    "/queue/pause",
    summary="Pause Build Queue",
    description="Pause processing of new build jobs (current job continues).",
)
async def pause_queue() -> dict[str, Any]:
    """Pause the build queue."""
    scheduler = get_build_scheduler()
    scheduler.pause()
    return {
        "paused": True,
        "message": "Build queue paused. Current job will continue to completion.",
    }


@router.post(
    "/queue/resume",
    summary="Resume Build Queue",
    description="Resume processing of build jobs.",
)
async def resume_queue() -> dict[str, Any]:
    """Resume the build queue."""
    scheduler = get_build_scheduler()
    scheduler.resume()
    return {"paused": False, "message": "Build queue resumed."}


@router.delete(
    "/queue/jobs/{job_id}",
    summary="Cancel Build Job",
    description="Cancel a queued or running build job.",
)
async def cancel_build_job(job_id: str) -> dict[str, Any]:
    """Cancel a specific build job."""
    scheduler = get_build_scheduler()
    cancelled = await scheduler.cancel_job(job_id)
    if cancelled:
        return {"success": True, "message": f"Job {job_id} cancelled."}
    else:
        raise HTTPException(
            status_code=404, detail=f"Job {job_id} not found or already completed."
        )


# ============== Sync Management Endpoints ==============


@router.get(
    "/sync/status",
    summary="Get Sync Status",
    description="Get the current status of graph sync operations between build and primary instances.",
)
async def get_sync_status() -> dict[str, Any]:
    """Get sync service status and configuration."""
    sync_service = get_graph_sync_service()
    return sync_service.get_sync_status()


@router.post(
    "/sync/projects/{project_name}",
    summary="Sync Project to Primary",
    description="Manually trigger sync of a project from build instance to primary instance.",
)
async def sync_project_to_primary(
    project_name: str,
    clean_after: bool = Query(
        None, description="Clean build instance after sync (default: use config)"
    ),
) -> dict[str, Any]:
    """
    Manually sync a project from build instance to primary.

    Use this when:
    - MEMGRAPH_BUILD_SYNC_MODE is set to "manual"
    - A previous auto-sync failed
    - You want to re-sync a project
    """
    sync_service = get_graph_sync_service()

    if not sync_service.is_build_instance_configured():
        raise HTTPException(
            status_code=400,
            detail="Build instance not configured. Set MEMGRAPH_BUILD_HOST in .env",
        )

    try:
        sync_job = await sync_service.sync_project(
            project_name, clean_after_sync=clean_after
        )
        return {
            "success": sync_job.status.value == "completed",
            "job_id": sync_job.job_id,
            "project_name": project_name,
            "status": sync_job.status.value,
            "nodes_synced": sync_job.nodes_synced,
            "relationships_synced": sync_job.relationships_synced,
            "message": sync_job.message,
            "error": sync_job.error,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)}")


# ============== MCP API Endpoints ==============
# These endpoints provide HTTP API equivalents of the MCP tools
# to support the pure HTTP mode MCP server.


class FindNodesRequest(BaseModel):
    """Request for find_nodes API."""

    query: str = Field(..., description="Search query")
    search_strategy: str = Field(
        default="auto", description="Search strategy: auto, exact, pattern, regex, and"
    )
    node_type: str = Field(default="Code", description="Node type filter: Code, All")


class FindNodesResultItem(BaseModel):
    """A single node result from find_nodes."""

    qualified_name: str = Field(..., description="Fully qualified name")
    name: str = Field(..., description="Short name")
    type: list[str] = Field(default_factory=list, description="Node labels/types")
    path: str | None = Field(None, description="File path")
    decorators: list[str] | None = Field(None, description="Decorators if any")
    docstring: str | None = Field(None, description="Docstring if available")
    start_line: int | None = Field(None, description="Start line number")
    end_line: int | None = Field(None, description="End line number")

    @classmethod
    def _parse_list_field(cls, v: any) -> list[str] | None:
        """Parse list fields that may come as JSON strings from the DB."""
        if v is None:
            return None
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            try:
                import json as _json
                parsed = _json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except (ValueError, TypeError):
                pass
            return None
        return None

    def __init__(self, **data: any):
        if "decorators" in data:
            data["decorators"] = FindNodesResultItem._parse_list_field(
                data["decorators"]
            )
        super().__init__(**data)


class FindNodesResponse(BaseModel):
    """Response for find_nodes API."""

    success: bool = Field(..., description="Whether the search succeeded")
    count: int = Field(0, description="Number of results found")
    summary: str = Field("", description="Summary of results")
    results: list[FindNodesResultItem] = Field(
        default_factory=list, description="Matching nodes"
    )
    entry_points: list[str] = Field(
        default_factory=list, description="Entry point qualified names"
    )
    hierarchy_tree: str | None = Field(None, description="Hierarchy tree visualization")


@router.post(
    "/node/{repo}/find",
    response_model=FindNodesResponse,
    summary="Find Nodes in Graph",
    description="Search for code elements (functions, classes, methods) using various search strategies.",
)
async def find_nodes_api(repo: str, request: FindNodesRequest) -> FindNodesResponse:
    """
    Search for code elements in the knowledge graph.

    Supports multiple search strategies:
    - auto: Intelligently detects the best strategy
    - exact: Exact qualified name match
    - pattern: CONTAINS-based fuzzy search
    - regex: Regular expression search
    - and: All terms must match

    Args:
        repo: Repository/project name
        request: Search parameters

    Returns:
        FindNodesResponse with matching nodes
    """
    logger.info(
        f"[find_nodes_api] repo={repo}, query={request.query}, strategy={request.search_strategy}"
    )

    try:
        with get_ingestor() as ingestor:
            from agent.tools.graph_query import GraphQueryTools

            query_tools = GraphQueryTools(ingestor, repo)
            result = query_tools.find_nodes(
                query=request.query,
                search_strategy=request.search_strategy,
                node_type=request.node_type,
            )

            # Convert results to response format
            results = []
            for r in result.results[:50]:  # Limit results
                results.append(
                    FindNodesResultItem(
                        qualified_name=r.get("qualified_name", ""),
                        name=r.get("name", ""),
                        type=r.get("type", [])
                        if isinstance(r.get("type"), list)
                        else [r.get("type", "Unknown")],
                        path=r.get("path"),
                        decorators=r.get("decorators"),
                        docstring=r.get("docstring"),
                        start_line=r.get("start_line"),
                        end_line=r.get("end_line"),
                    )
                )

            return FindNodesResponse(
                success=result.success,
                count=result.count,
                summary=result.summary,
                results=results,
                entry_points=result.entry_points or [],
                hierarchy_tree=result.hierarchy_tree if result.hierarchy_tree else None,
            )

    except Exception as e:
        logger.error(f"Failed to find nodes: {e}", exc_info=True)
        return FindNodesResponse(
            success=False,
            count=0,
            summary=f"Error: {str(e)}",
            results=[],
        )


class FindCallsResponse(BaseModel):
    """Response for find_calls API."""

    success: bool = Field(..., description="Whether the search succeeded")
    count: int = Field(0, description="Number of results found")
    summary: str = Field("", description="Summary of results")
    results: list[FindNodesResultItem] = Field(
        default_factory=list, description="Related functions/methods"
    )


@router.get(
    "/node/{repo}/calls",
    response_model=FindCallsResponse,
    summary="Find Call Relationships",
    description="Find functions that call or are called by a specific function/method.",
)
async def find_calls_api(
    repo: str,
    qualified_name: str = Query(
        ..., description="Fully qualified name of the function/method"
    ),
    direction: str = Query(
        default="outgoing", description="Call direction: outgoing or incoming"
    ),
    depth: int = Query(default=1, ge=1, le=5, description="Traversal depth (1-5)"),
) -> FindCallsResponse:
    """
    Find call relationships for a function/method.

    Args:
        repo: Repository/project name
        qualified_name: Fully qualified name of the function/method
        direction: "outgoing" (what this calls) or "incoming" (what calls this)
        depth: Traversal depth (1-5)

    Returns:
        FindCallsResponse with related functions
    """
    logger.info(
        f"[find_calls_api] repo={repo}, qn={qualified_name}, direction={direction}, depth={depth}"
    )

    try:
        with get_ingestor() as ingestor:
            from agent.tools.graph_query import GraphQueryTools

            query_tools = GraphQueryTools(ingestor, repo)
            result = query_tools.find_calls(
                qualified_name=qualified_name,
                direction=direction,
                depth=depth,
            )

            # Convert results to response format
            results = []
            for r in result.results[:100]:  # Limit results
                results.append(
                    FindNodesResultItem(
                        qualified_name=r.get("qualified_name", ""),
                        name=r.get("name", ""),
                        type=r.get("type", [])
                        if isinstance(r.get("type"), list)
                        else [r.get("type", "Unknown")],
                        path=r.get("path"),
                        docstring=r.get("docstring"),
                        start_line=r.get("start_line"),
                        end_line=r.get("end_line"),
                    )
                )

            return FindCallsResponse(
                success=result.success,
                count=result.count,
                summary=result.summary,
                results=results,
            )

    except Exception as e:
        logger.error(f"Failed to find calls: {e}", exc_info=True)
        return FindCallsResponse(
            success=False,
            count=0,
            summary=f"Error: {str(e)}",
            results=[],
        )


class GetChildrenResultItem(BaseModel):
    """A single child node result."""

    name: str = Field(..., description="Node name")
    type: list[str] = Field(default_factory=list, description="Node labels/types")
    qualified_name: str | None = Field(None, description="Qualified name if available")
    path: str | None = Field(None, description="Path if available")
    depth: int = Field(1, description="Depth from parent")
    start_line: int | None = Field(None, description="Start line number")
    end_line: int | None = Field(None, description="End line number")


class GetChildrenResponse(BaseModel):
    """Response for get_children API."""

    success: bool = Field(..., description="Whether the query succeeded")
    count: int = Field(0, description="Number of children found")
    summary: str = Field("", description="Summary of results")
    results: list[GetChildrenResultItem] = Field(
        default_factory=list, description="Child nodes"
    )
    hierarchy_tree: str | None = Field(None, description="Hierarchy tree visualization")


@router.get(
    "/node/{repo}/children/enhanced",
    response_model=GetChildrenResponse,
    summary="Get Node Children (Enhanced)",
    description="Get children of a node with support for all identifier types.",
)
async def get_children_enhanced_api(
    repo: str,
    identifier: str = Query(
        ...,
        description="Identifier: project name, folder path, file path, or qualified_name",
    ),
    identifier_type: str = Query(
        default="auto", description="Type: auto, project, folder, file, class"
    ),
    depth: int = Query(default=1, ge=1, le=5, description="Depth to traverse (1-5)"),
    child_types: str | None = Query(
        None, description="Filter by type (comma-separated), e.g., 'Class,Function'"
    ),
) -> GetChildrenResponse:
    """
    Get children of a node with intelligent type detection.

    Supports all node types:
    - project: Returns Folder, File (directory structure)
    - folder: Returns Folder, File
    - file: Returns Class, Function
    - class: Returns Method

    Special identifiers:
    - "." or "current": Uses the repo parameter as project name

    Args:
        repo: Repository/project name
        identifier: Node identifier
        identifier_type: Type hint or "auto" for detection
        depth: Traversal depth
        child_types: Comma-separated type filter

    Returns:
        GetChildrenResponse with child nodes
    """
    logger.info(
        f"[get_children_enhanced_api] repo={repo}, identifier={identifier}, type={identifier_type}, depth={depth}"
    )

    # Handle special identifiers
    if identifier in (".", "current"):
        identifier = repo
        identifier_type = "project"

    try:
        with get_ingestor() as ingestor:
            from agent.tools.graph_query import GraphQueryTools

            query_tools = GraphQueryTools(ingestor, repo)
            result = query_tools.get_children(
                identifier=identifier,
                identifier_type=identifier_type,
                depth=depth,
                child_types=child_types,
            )

            # Convert results to response format
            results = []
            for r in result.results[:100]:  # Limit results
                results.append(
                    GetChildrenResultItem(
                        name=r.get("name", ""),
                        type=r.get("type", [])
                        if isinstance(r.get("type"), list)
                        else [r.get("type", "Unknown")],
                        qualified_name=r.get("qualified_name"),
                        path=r.get("path"),
                        depth=r.get("depth", 1),
                        start_line=r.get("start_line"),
                        end_line=r.get("end_line"),
                    )
                )

            return GetChildrenResponse(
                success=result.success,
                count=result.count,
                summary=result.summary,
                results=results,
                hierarchy_tree=result.hierarchy_tree if result.hierarchy_tree else None,
            )

    except Exception as e:
        logger.error(f"Failed to get children: {e}", exc_info=True)
        return GetChildrenResponse(
            success=False,
            count=0,
            summary=f"Error: {str(e)}",
            results=[],
        )


class ExploreCodeRequest(BaseModel):
    """Request for explore_code API."""

    identifier: str = Field(..., description="Qualified name of the code element")
    max_dependency_depth: int = Field(
        default=5, ge=1, le=10, description="Depth for dependency tree"
    )
    include_dependency_source_code: bool = Field(
        default=True, description="Include source code for dependencies"
    )


class ExploreCodeResponse(BaseModel):
    """Response for explore_code API."""

    success: bool = Field(..., description="Whether the exploration succeeded")
    qualified_name: str = Field("", description="Target qualified name")
    element_type: str = Field(
        "", description="Element type: Function, Method, Class, etc."
    )
    source_code: str | None = Field(None, description="Source code of the element")
    file_path: str | None = Field(None, description="File path")
    line_start: int | None = Field(None, description="Start line number")
    line_end: int | None = Field(None, description="End line number")
    docstring: str | None = Field(None, description="Docstring if available")
    callers: list[dict[str, Any]] = Field(
        default_factory=list, description="Functions that call this"
    )
    called: list[dict[str, Any]] = Field(
        default_factory=list, description="Functions this calls"
    )
    dependency_tree: str | None = Field(
        None, description="Dependency tree visualization"
    )
    dependency_source_codes: dict[str, str] = Field(
        default_factory=dict, description="Source codes of dependencies"
    )
    error: str | None = Field(None, description="Error message if failed")


@router.post(
    "/node/{repo}/explore",
    response_model=ExploreCodeResponse,
    summary="Explore Code Element",
    description="One-stop deep analysis of a code element with full context.",
)
async def explore_code_api(
    repo: str, request: ExploreCodeRequest
) -> ExploreCodeResponse:
    """
    Comprehensive code exploration with full context.

    Returns:
    - Source code with line numbers
    - All functions that call this (incoming calls)
    - All functions that this calls (outgoing dependencies)
    - Dependency tree visualization
    - Source code of dependencies (optional)

    Args:
        repo: Repository/project name
        request: Exploration parameters

    Returns:
        ExploreCodeResponse with comprehensive context
    """
    logger.info(f"[explore_code_api] repo={repo}, identifier={request.identifier}")

    try:
        with get_ingestor() as ingestor:
            from agent.tools.code_tools import CodeExplorer, CodeRetriever
            from agent.tools.graph_query import GraphQueryTools

            # Get project path for code retrieval
            project_path = ingestor.get_project_path(repo)

            # Initialize tools
            query_tools = GraphQueryTools(ingestor, repo)
            explorer = CodeExplorer(ingestor, repo, project_path)

            # Find the target element
            search_identifier = request.identifier
            if not search_identifier.startswith(f"{repo}."):
                search_identifier = f"{repo}.{search_identifier}"

            exact_result = query_tools.find_nodes(
                search_identifier, search_strategy="auto"
            )

            if not exact_result.success or not exact_result.results:
                # Try fuzzy search
                basename = (
                    search_identifier.split(".")[-1]
                    if "." in search_identifier
                    else search_identifier
                )
                pattern_result = query_tools.find_nodes(
                    query=basename, search_strategy="pattern"
                )

                if not pattern_result.success or not pattern_result.results:
                    return ExploreCodeResponse(
                        success=False,
                        error=f"No elements found for: '{request.identifier}'",
                    )

                # Use first match if only one found
                if pattern_result.count == 1:
                    exact_result = pattern_result
                else:
                    # Return list of candidates
                    candidates = [
                        r.get("qualified_name") for r in pattern_result.results[:10]
                    ]
                    return ExploreCodeResponse(
                        success=False,
                        error=f"Multiple matches found. Please specify: {', '.join(candidates)}",
                    )

            target = exact_result.results[0]
            target_qn = target.get("qualified_name")
            target_type = target.get("type", ["Unknown"])
            if isinstance(target_type, list):
                target_type = target_type[0] if target_type else "Unknown"

            # Get code context
            context = explorer.explore_code_context(
                identifier=target_qn,
                include_code=False,
                include_callers=True,
                include_called=True,
                include_imports=True,
                call_depth=1,
                include_statistics=True,
            )

            # Get source code
            source_code = None
            file_path = None
            line_start = None
            line_end = None

            if project_path:
                try:
                    retriever = CodeRetriever(project_path, ingestor, repo)
                    code_snippet = await retriever.find_code_snippet(target_qn)
                    if code_snippet.found:
                        source_code = code_snippet.source_code
                        file_path = code_snippet.file_path
                        line_start = code_snippet.line_start
                        line_end = code_snippet.line_end
                except Exception as e:
                    logger.warning(f"Failed to get code snippet: {e}")

            # Build dependency tree
            dependency_tree = None
            dependency_source_codes = {}

            if target_type in ["Function", "Method", "Class"]:
                try:
                    tree_result = explorer.build_dependency_tree(
                        from_qualified_name=target_qn,
                        max_depth=request.max_dependency_depth,
                        relationship_type="CALLS",
                        detect_circular=True,
                    )
                    if tree_result:
                        dependency_tree = tree_result.summary

                        # Get source code for dependencies
                        if request.include_dependency_source_code and project_path:

                            def collect_qns(node, collected):
                                qn = node.get("qualified_name") or node.get("name")
                                if qn and qn not in collected:
                                    collected.add(qn)
                                for child in node.get("children", []):
                                    collect_qns(child, collected)
                                return collected

                            dep_qns = collect_qns(tree_result.tree, set())
                            retriever = CodeRetriever(project_path, ingestor, repo)
                            for dep_qn in list(dep_qns)[:10]:  # Limit
                                if dep_qn != target_qn:
                                    try:
                                        snippet = await retriever.find_code_snippet(
                                            dep_qn
                                        )
                                        if snippet.found:
                                            dependency_source_codes[dep_qn] = (
                                                snippet.source_code
                                            )
                                    except Exception:
                                        pass

                except Exception as e:
                    logger.warning(f"Failed to build dependency tree: {e}")

            return ExploreCodeResponse(
                success=True,
                qualified_name=target_qn,
                element_type=target_type,
                source_code=source_code,
                file_path=file_path,
                line_start=line_start,
                line_end=line_end,
                docstring=context.docstring,
                callers=[
                    {"qualified_name": c.get("qualified_name"), "name": c.get("name")}
                    for c in context.callers[:30]
                ],
                called=[
                    {"qualified_name": c.get("qualified_name"), "name": c.get("name")}
                    for c in context.called[:30]
                ],
                dependency_tree=dependency_tree,
                dependency_source_codes=dependency_source_codes,
            )

    except Exception as e:
        logger.error(f"Failed to explore code: {e}", exc_info=True)
        return ExploreCodeResponse(
            success=False,
            error=str(e),
        )

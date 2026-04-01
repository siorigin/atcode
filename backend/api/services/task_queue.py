# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
try:
    import fcntl
except ImportError:
    fcntl = None  # Windows — file locking skipped (single-worker mode)
import json
import os
import random
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from .task_store_redis import RedisTaskStore

# Default concurrency limits per task type
DEFAULT_MAX_CONCURRENCY = {
    "graph_build": 1,  # Only 1 graph build at a time (CPU intensive)
    "overview_gen": 2,  # Allow 2 concurrent overview generations
    "doc_gen": 3,  # Allow 3 concurrent doc generations
    "git_checkout": 1,  # Only 1 git checkout at a time (exclusive)
    "sync_watching": 10,  # Allow multiple projects to watch simultaneously
    "paper_read": 2,  # Allow 2 concurrent paper reading pipelines
    "other": 100,  # Default for unknown types
}

# Environment variable overrides
MAX_CONCURRENT_GRAPH_BUILD = int(os.getenv("MAX_CONCURRENT_GRAPH_BUILD", "1"))
MAX_CONCURRENT_OVERVIEW_GEN = int(os.getenv("MAX_CONCURRENT_OVERVIEW_GEN", "2"))
MAX_CONCURRENT_DOC_GEN = int(os.getenv("MAX_CONCURRENT_DOC_GEN", "3"))
MAX_CONCURRENT_GIT_CHECKOUT = int(os.getenv("MAX_CONCURRENT_GIT_CHECKOUT", "1"))
MAX_TASK_TRAJECTORY_EVENTS = int(os.getenv("MAX_TASK_TRAJECTORY_EVENTS", "50"))


class TaskStatus(StrEnum):
    """Task execution status."""

    PENDING = "pending"
    RUNNING = "running"
    STALLED = "stalled"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskType(StrEnum):
    """Type of background task."""

    GRAPH_BUILD = "graph_build"
    OVERVIEW_GEN = "overview_gen"
    DOC_GEN = "doc_gen"
    GIT_CHECKOUT = "git_checkout"
    DOC_REGENERATE = "doc_regenerate"
    OPERATOR_REGENERATE = "operator_regenerate"
    SYNC_WATCHING = "sync_watching"  # Long-running file monitoring task
    PAPER_READ = "paper_read"  # Paper reading pipeline
    OTHER = "other"


@dataclass
class TaskTrajectoryEvent:
    """A single meaningful task-state transition for UI/debug visibility."""

    timestamp: str
    status: str = ""
    progress: int = 0
    step: str = ""
    message: str = ""
    error: str | None = None
    details: dict | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "TaskTrajectoryEvent":
        """Convert serialized event data back into a dataclass."""
        try:
            progress = int(data.get("progress", 0))
        except (TypeError, ValueError):
            progress = 0
        return cls(
            timestamp=data.get("timestamp", ""),
            status=data.get("status", ""),
            progress=progress,
            step=data.get("step", ""),
            message=data.get("message", ""),
            error=data.get("error") or None,
            details=data.get("details"),
        )


@dataclass
class TaskState:
    """Current state of a background task."""

    task_id: str
    status: TaskStatus
    task_type: str = ""  # TaskType value for categorization
    repo_name: str = ""  # Associated repository name
    user_id: str = ""  # User who started the task (for future use)
    progress: int = 0
    step: str = ""
    status_message: str = ""
    result: dict | None = None
    error: str | None = None
    created_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    queue_position: int = 0  # Position in queue (0 = running, 1+ = waiting)
    remote_host: str = ""  # Host where task is running (for cross-machine cancel)
    trajectory: list[TaskTrajectoryEvent] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Normalize serialized trajectory entries loaded from storage."""
        normalized: list[TaskTrajectoryEvent] = []
        for event in self.trajectory:
            if isinstance(event, TaskTrajectoryEvent):
                normalized.append(event)
            elif isinstance(event, dict):
                normalized.append(TaskTrajectoryEvent.from_dict(event))
        self.trajectory = normalized

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        # Handle status that might be string or enum
        status_value = (
            self.status.value if isinstance(self.status, TaskStatus) else self.status
        )
        return {
            **asdict(self),
            "status": status_value,
            "task_type": self.task_type,
            "repo_name": self.repo_name,
            "user_id": self.user_id,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "queue_position": self.queue_position,
            "remote_host": self.remote_host,
            "trajectory": [asdict(event) for event in self.trajectory],
        }


class InMemoryTaskStore:
    """In-memory task state storage."""

    def __init__(self):
        """Initialize in-memory store."""
        self._tasks: dict[str, TaskState] = {}
        self._lock = asyncio.Lock()

    async def get(self, task_id: str) -> TaskState | None:
        """Get task state by ID."""
        async with self._lock:
            return self._tasks.get(task_id)

    async def save(self, state: TaskState) -> None:
        """Save task state."""
        async with self._lock:
            self._tasks[state.task_id] = state

    async def list(self) -> list[TaskState]:
        """List all tasks."""
        async with self._lock:
            return list(self._tasks.values())

    async def delete(self, task_id: str) -> None:
        """Delete task state."""
        async with self._lock:
            self._tasks.pop(task_id, None)


class FileSystemTaskStore:
    """
    File-system based task state storage with in-memory index cache.

    The in-memory index is kept in sync with file system changes through
    save/delete operations. This avoids the overhead of scanning and parsing
    all JSON files for every list() call.

    Uses file locks (fcntl) to ensure safe concurrent access across multiple
    worker processes.
    """

    def __init__(self, storage_dir: Path):
        """Initialize file-system store."""
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

        # In-memory index: task_id -> TaskState
        self._index: dict[str, TaskState] = {}
        self._index_loaded = False
        self._index_dirty = False

    def _get_task_file(self, task_id: str) -> Path:
        """Get path to task state file."""
        return self.storage_dir / f"{task_id}.json"

    def _read_with_lock(self, file_path: Path) -> dict | None:
        """Read JSON file with shared lock (allows concurrent reads)."""
        try:
            with open(file_path) as f:
                if fcntl is not None:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    data = json.load(f)
                    return data
                finally:
                    if fcntl is not None:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception as e:
            logger.debug(f"Failed to read {file_path.name} with lock: {e}")
            return None

    def _write_with_lock(self, file_path: Path, data: dict) -> bool:
        """Write JSON file with non-blocking lock and exponential backoff.

        Uses LOCK_NB (non-blocking) to prevent deadlocks in multi-worker
        environments. Retries with exponential backoff up to 5 seconds total.

        Args:
            file_path: Path to write to
            data: Dictionary to serialize as JSON

        Returns:
            True if write succeeded, False on timeout or error
        """
        temp_file = None
        max_retries = 50  # 50 * 100ms = 5 seconds max
        base_delay = 0.1  # 100ms

        for attempt in range(max_retries):
            try:
                # Create temp file with unique name to avoid conflicts
                temp_file = file_path.with_suffix(
                    f".tmp.{os.getpid()}.{int(time.time() * 1000000)}"
                )

                with open(temp_file, "w") as f:
                    if fcntl is not None:
                        # Non-blocking lock - raises BlockingIOError if lock is held
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    try:
                        json.dump(data, f, indent=2)
                        f.flush()
                        os.fsync(f.fileno())  # Ensure data is written to disk
                    finally:
                        if fcntl is not None:
                            fcntl.flock(f.fileno(), fcntl.LOCK_UN)

                # Verify temp file exists before rename
                if not temp_file.exists():
                    logger.error(f"Temp file disappeared after write: {temp_file}")
                    return False

                # Atomic rename
                temp_file.replace(file_path)
                return True

            except BlockingIOError:
                # Lock is held by another process - retry with backoff
                if temp_file and temp_file.exists():
                    try:
                        temp_file.unlink()
                    except Exception:
                        pass

                if attempt < max_retries - 1:
                    # Exponential backoff with jitter to avoid thundering herd
                    delay = min(
                        base_delay * (1.2**attempt) + random.uniform(0, 0.05), 1.0
                    )
                    time.sleep(delay)
                else:
                    logger.warning(
                        f"File lock timeout after {max_retries} attempts: {file_path}"
                    )
                    return False

            except Exception as e:
                logger.error(f"Failed to write {file_path.name} with lock: {e}")
                # Clean up temp file if it exists
                if temp_file and temp_file.exists():
                    try:
                        temp_file.unlink()
                    except Exception:
                        pass
                return False

        return False

    def _cleanup_stale_temp_files(self) -> int:
        """Clean up orphaned .tmp.* files older than 1 hour.

        These files can be left behind when processes crash during writes.
        Called during index loading to clean up any leftover temp files.

        Returns:
            Number of temp files cleaned up
        """
        cleaned = 0
        try:
            # Find all temp files (pattern: *.tmp.*)
            for tmp_file in self.storage_dir.glob("*.tmp.*"):
                try:
                    stat = tmp_file.stat()
                    age = time.time() - stat.st_mtime

                    # Only clean up files older than 1 hour (crashed processes)
                    if age > 3600:
                        tmp_file.unlink()
                        cleaned += 1
                        logger.debug(
                            f"Cleaned up stale temp file: {tmp_file.name} (age: {age / 3600:.1f}h)"
                        )
                except Exception as e:
                    logger.debug(f"Failed to check/clean temp file {tmp_file}: {e}")

            if cleaned > 0:
                logger.info(f"Cleaned up {cleaned} stale temp file(s) from task store")

        except Exception as e:
            logger.warning(f"Failed to cleanup stale temp files: {e}")

        return cleaned

    async def _ensure_index_loaded(self) -> None:
        """Load all tasks from disk into the in-memory index (once)."""
        if self._index_loaded:
            return

        logger.info(f"Loading task store index from {self.storage_dir}...")
        start_time = asyncio.get_event_loop().time()

        # Clean up stale temp files first (orphaned from crashed processes)
        self._cleanup_stale_temp_files()

        self._index.clear()
        for task_file in self.storage_dir.glob("*.json"):
            try:
                data = self._read_with_lock(task_file)
                if data:
                    # Handle status conversion - may be string or already enum
                    status_value = data.get("status")
                    if isinstance(status_value, str):
                        try:
                            data["status"] = TaskStatus(status_value)
                        except ValueError:
                            # Invalid status value, use default
                            data["status"] = TaskStatus.FAILED
                    elif not isinstance(status_value, TaskStatus):
                        data["status"] = TaskStatus.FAILED
                    self._index[task_file.stem] = TaskState(**data)
            except Exception as e:
                logger.warning(f"Failed to read task file {task_file.name}: {e}")

        self._index_loaded = True
        elapsed = asyncio.get_event_loop().time() - start_time
        logger.info(
            f"Task store index loaded: {len(self._index)} tasks in {elapsed:.2f}s"
        )

    async def get(self, task_id: str) -> TaskState | None:
        """Get task state by ID.

        In multi-worker environments with shared filesystem:
        - For terminal states (COMPLETED/FAILED/CANCELLED): use in-memory index (immutable)
        - For active states (PENDING/RUNNING): always read from filesystem (might be updated by other workers)
        """
        async with self._lock:
            await self._ensure_index_loaded()

            # For tasks already in index, check if they're in terminal state
            # Terminal states don't change, so index is safe to use
            if task_id in self._index:
                cached_state = self._index[task_id]
                if cached_state.status in [
                    TaskStatus.COMPLETED,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLED,
                ]:
                    # Terminal state - immutable, safe to return from cache
                    return cached_state
                # Active state (PENDING/RUNNING) - might be updated by other workers
                # Fall through to re-read from filesystem

            # Read from filesystem (either not in index, or active state that might have changed)
            task_file = self._get_task_file(task_id)
            if task_file.exists():
                data = self._read_with_lock(task_file)
                if data:
                    try:
                        # Handle status conversion
                        status_value = data.get("status")
                        if isinstance(status_value, str):
                            try:
                                data["status"] = TaskStatus(status_value)
                            except ValueError:
                                data["status"] = TaskStatus.FAILED
                        elif not isinstance(status_value, TaskStatus):
                            data["status"] = TaskStatus.FAILED

                        state = TaskState(**data)
                        # ALWAYS update index after reading from filesystem
                        # This ensures cache is in sync with the latest file state
                        self._index[task_id] = state
                        return state
                    except Exception as e:
                        logger.warning(
                            f"Failed to parse task file {task_file.name}: {e}"
                        )

            return None

    async def save(self, state: TaskState) -> None:
        """Save task state with atomic write, file lock, and update index."""
        async with self._lock:
            await self._ensure_index_loaded()
            task_file = self._get_task_file(state.task_id)

            # Write with exclusive lock
            success = self._write_with_lock(task_file, state.to_dict())

            if success:
                # Update in-memory index
                self._index[state.task_id] = state
            else:
                logger.error(f"Failed to save task state {state.task_id}")

    async def list(self) -> list[TaskState]:
        """List all tasks from in-memory index (fast)."""
        async with self._lock:
            await self._ensure_index_loaded()
            return list(self._index.values())

    async def delete(self, task_id: str) -> None:
        """Delete task state and update index."""
        async with self._lock:
            await self._ensure_index_loaded()
            task_file = self._get_task_file(task_id)
            try:
                task_file.unlink(missing_ok=True)
                # Remove from in-memory index
                self._index.pop(task_id, None)
            except Exception as e:
                logger.error(f"Failed to delete task state {task_id}: {e}")


class BackgroundTaskManager:
    """
    Manages background task execution with concurrency control.

    Features:
    - Configurable max concurrency per task type
    - True queue with PENDING state
    - Cross-machine task cancellation
    - Redis persistence (single source of truth)
    """

    def __init__(
        self,
        store: InMemoryTaskStore | FileSystemTaskStore | RedisTaskStore | None = None,
        max_concurrency: dict[str, int] | None = None,
        use_redis: bool = False,
        redis_url: str | None = None,
    ):
        """
        Initialize task manager.

        Args:
            store: Task state storage backend (optional if use_redis=True)
            max_concurrency: Max concurrent tasks per type, e.g. {"graph_build": 1, "doc_gen": 3}
            use_redis: Whether to use Redis as the task store (recommended for multi-worker)
            redis_url: Redis connection URL (reads from env if not provided)
        """
        self._redis_store: RedisTaskStore | None = None
        self._use_redis = use_redis

        if use_redis:
            # Redis will be initialized in initialize_redis() method
            self.store = InMemoryTaskStore()  # Temporary, will be replaced
        else:
            self.store = store or InMemoryTaskStore()

        self._running_tasks: dict[str, asyncio.Task] = {}
        self._subscribers: dict[
            str, list[Callable]
        ] = {}  # task_id -> list of callbacks
        self._lock = asyncio.Lock()

        # Per-type locks for concurrent queue processing
        self._type_locks = {
            "graph_build": asyncio.Lock(),
            "overview_gen": asyncio.Lock(),
            "doc_gen": asyncio.Lock(),
            "git_checkout": asyncio.Lock(),
            "paper_read": asyncio.Lock(),
            "other": asyncio.Lock(),
        }
        self._cleanup_done = False

        # Concurrency control
        self._max_concurrency = max_concurrency or {
            "graph_build": MAX_CONCURRENT_GRAPH_BUILD,
            "overview_gen": MAX_CONCURRENT_OVERVIEW_GEN,
            "doc_gen": MAX_CONCURRENT_DOC_GEN,
            "git_checkout": MAX_CONCURRENT_GIT_CHECKOUT,
            "paper_read": DEFAULT_MAX_CONCURRENCY["paper_read"],
            "other": DEFAULT_MAX_CONCURRENCY["other"],
        }

        # Queue for pending tasks: task_type -> deque of (task_id, task_func, args, kwargs)
        self._pending_queue: dict[str, deque] = {
            "graph_build": deque(),
            "overview_gen": deque(),
            "doc_gen": deque(),
            "git_checkout": deque(),
            "paper_read": deque(),
            "other": deque(),
        }

        # Count of currently running tasks per type
        self._running_count: dict[str, int] = {
            "graph_build": 0,
            "overview_gen": 0,
            "doc_gen": 0,
            "git_checkout": 0,
            "paper_read": 0,
            "other": 0,
        }

        # Queue processor event
        self._queue_event = asyncio.Event()
        self._processor_task: asyncio.Task | None = None
        self._shutdown = False

        # Cleanup task
        self._cleanup_task: asyncio.Task | None = None

        logger.info(
            f"TaskManager initialized with concurrency limits: {self._max_concurrency}"
        )
        logger.info(
            f"Task store: {'Redis' if use_redis else type(self.store).__name__}"
        )

    async def initialize_redis(self, redis_url: str | None = None) -> bool:
        """
        Initialize Redis task store connection.

        Args:
            redis_url: Redis connection URL (optional, reads from config if not provided)

        Returns:
            True if connection successful
        """
        if not self._use_redis:
            return False

        try:
            from .task_store_redis import RedisTaskStore

            if redis_url is None:
                from core.config import settings

                redis_url = settings.REDIS_URL

            self._redis_store = RedisTaskStore(redis_url)
            connected = await self._redis_store.connect()

            if connected:
                self.store = self._redis_store
                logger.info("✅ Task store switched to Redis")
            return connected
        except Exception as e:
            logger.error(f"❌ Failed to initialize Redis task store: {e}")
            return False

    async def close_redis(self) -> None:
        """Close Redis connection."""
        if self._redis_store:
            await self._redis_store.close()
            logger.info("Redis task store connection closed")

    @property
    def is_using_redis(self) -> bool:
        """Check if using Redis as task store."""
        return self._use_redis and self._redis_store is not None

    async def cleanup_stale_tasks(self) -> int:
        """
        Clean up stale/zombie tasks on startup.

        In multi-worker environments with shared filesystem:
        - Don't clean up tasks running on remote hosts (they might be on another server)
        - Don't clean up recently started tasks (they might just be slow/long-running)
        - Only clean obvious local stale tasks (very old + no asyncio reference)

        Returns:
            Number of tasks cleaned up
        """
        if self._cleanup_done:
            return 0

        all_tasks = await self.store.list()
        cleaned_count = 0
        now = datetime.now(UTC)

        # Threshold: only clean tasks that are very stale (> 2 hours old)
        # This prevents accidentally killing long-running tasks
        from datetime import timedelta

        stale_threshold = now - timedelta(hours=2)

        for task in all_tasks:
            if task.status in [TaskStatus.PENDING, TaskStatus.RUNNING]:
                # Skip remote host tasks - they might be running on another server
                if task.remote_host and not self._is_local_host(task.remote_host):
                    logger.debug(
                        f"Skipping cleanup of remote task: {task.task_id} (remote_host={task.remote_host})"
                    )
                    continue

                # Skip recently started RUNNING tasks - they might be slow/long-running
                if task.status == TaskStatus.RUNNING and task.started_at:
                    try:
                        started_time = datetime.fromisoformat(
                            task.started_at.replace("Z", "+00:00")
                        )
                        if started_time > stale_threshold:
                            logger.debug(
                                f"Skipping cleanup of recently started task: {task.task_id}"
                            )
                            continue
                    except (ValueError, TypeError):
                        pass

                # Skip recently created PENDING tasks - they might still be in queue
                if task.status == TaskStatus.PENDING:
                    try:
                        created_time = datetime.fromisoformat(
                            task.created_at.replace("Z", "+00:00")
                        )
                        if created_time > stale_threshold:
                            logger.debug(
                                f"Skipping cleanup of recently created pending task: {task.task_id}"
                            )
                            continue
                    except (ValueError, TypeError):
                        pass

                # Check if this task has an associated running asyncio.Task
                if task.task_id not in self._running_tasks:
                    # This is a stale task - mark it as failed
                    logger.warning(
                        f"Cleaning up stale task: {task.task_id} "
                        f"(type={task.task_type}, repo={task.repo_name}, status={task.status.value})"
                    )
                    task.status = TaskStatus.FAILED
                    task.error = "Task was interrupted by server restart"
                    task.completed_at = now.isoformat()
                    await self.store.save(task)
                    cleaned_count += 1

        if cleaned_count > 0:
            logger.info(f"Cleaned up {cleaned_count} stale task(s) on startup")

        self._cleanup_done = True
        return cleaned_count

    async def create_task(
        self,
        task_id: str | None = None,
        task_type: str = "",
        repo_name: str = "",
        user_id: str = "",
        initial_message: str = "",
        remote_host: str = "",
    ) -> str:
        """
        Create a new background task.

        Args:
            task_id: Optional custom task ID (defaults to UUID)
            task_type: Type of task (graph_build, overview_gen, doc_gen)
            repo_name: Associated repository name
            user_id: User who started the task
            initial_message: Initial status message
            remote_host: Host where task will run (for cross-machine cancel)

        Returns:
            Task ID
        """
        task_id = task_id or str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()

        # Calculate initial queue position
        task_type_key = self._get_task_type_key(task_type)
        max_concurrent = self._max_concurrency.get(task_type_key, 2)
        current_running = self._running_count.get(task_type_key, 0)
        pending_count = len(self._pending_queue.get(task_type_key, deque()))

        if current_running < max_concurrent:
            queue_position = 0  # Will run immediately
        else:
            queue_position = pending_count + 1  # Position in queue

        state = TaskState(
            task_id=task_id,
            status=TaskStatus.PENDING,
            task_type=task_type,
            repo_name=repo_name,
            user_id=user_id,
            progress=0,
            step="",
            status_message=initial_message,
            result=None,
            error=None,
            created_at=now,
            started_at=None,
            completed_at=None,
            queue_position=queue_position,
            remote_host=remote_host,
            trajectory=[
                TaskTrajectoryEvent(
                    timestamp=now,
                    status=TaskStatus.PENDING.value,
                    progress=0,
                    step="queued" if queue_position > 0 else "",
                    message=initial_message,
                    details={"queue_position": queue_position},
                )
            ],
        )

        await self.store.save(state)
        logger.info(
            f"Created task: {task_id} (type={task_type}, repo={repo_name}, queue_pos={queue_position})"
        )
        return task_id

    async def get_task_status(self, task_id: str) -> TaskState | None:
        """Get current status of a task.

        When using Redis, this is a fast operation without additional caching.
        """
        return await self.store.get(task_id)

    async def subscribe(self, task_id: str, callback: Callable) -> None:
        """
        Subscribe to task updates.

        Callback will be called with TaskState whenever status changes.
        """
        async with self._lock:
            if task_id not in self._subscribers:
                self._subscribers[task_id] = []
            self._subscribers[task_id].append(callback)

    async def _notify_subscribers(self, state: TaskState) -> None:
        """Notify all subscribers of state change."""
        async with self._lock:
            callbacks = self._subscribers.get(state.task_id, [])

        for callback in callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(state)
                else:
                    callback(state)
            except Exception as e:
                logger.error(f"Error calling subscriber callback: {e}")

    async def update_task(
        self,
        task_id: str,
        status: TaskStatus | None = None,
        progress: int | None = None,
        step: str | None = None,
        status_message: str | None = None,
        result: dict | None = None,
        error: str | None = None,
        details: dict | None = None,
    ) -> TaskState | None:
        """
        Update task state.

        Returns the updated TaskState, or None if task not found.
        """
        state = await self.store.get(task_id)
        if not state:
            logger.warning(f"Task not found: {task_id}")
            return None

        # Guard: never regress from a terminal state (COMPLETED/FAILED/CANCELLED)
        # This prevents race conditions where a late-arriving progress callback
        # overwrites the final status with RUNNING.
        terminal_states = {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
        if state.status in terminal_states:
            if status is None or status not in terminal_states:
                logger.debug(
                    f"Ignoring update for task {task_id}: already in terminal state {state.status.value}"
                )
                return state

        previous_snapshot = (
            state.status.value if isinstance(state.status, TaskStatus) else state.status,
            state.progress,
            state.step,
            state.status_message,
            state.error,
        )

        # Update fields
        if status is not None:
            state.status = status
        if progress is not None:
            state.progress = progress
        if step is not None:
            state.step = step
        if status_message is not None:
            state.status_message = status_message
        if result is not None:
            state.result = result
        if error is not None:
            state.error = error

        # Set timestamps
        now = datetime.now(UTC).isoformat()
        if state.status == TaskStatus.RUNNING and not state.started_at:
            state.started_at = now
        if state.status in [
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        ]:
            state.completed_at = now
        elif state.status == TaskStatus.STALLED:
            state.completed_at = None

        current_snapshot = (
            state.status.value if isinstance(state.status, TaskStatus) else state.status,
            state.progress,
            state.step,
            state.status_message,
            state.error,
        )
        if current_snapshot != previous_snapshot or details:
            trajectory_event = TaskTrajectoryEvent(
                timestamp=now,
                status=current_snapshot[0],
                progress=state.progress,
                step=state.step,
                message=state.status_message or state.step or "",
                error=state.error,
                details=details,
            )
            if not state.trajectory or state.trajectory[-1] != trajectory_event:
                state.trajectory.append(trajectory_event)
                if len(state.trajectory) > MAX_TASK_TRAJECTORY_EVENTS:
                    state.trajectory = state.trajectory[-MAX_TASK_TRAJECTORY_EVENTS :]

        # Save to store (Redis automatically broadcasts update)
        await self.store.save(state)

        # Notify subscribers (for streaming responses)
        await self._notify_subscribers(state)

        # Broadcast to WebSocket (only if not using Redis, Redis store already broadcasts)
        if not self.is_using_redis:
            await self._broadcast_task_update(state)

        return state

    async def _broadcast_task_update(self, state: TaskState) -> None:
        """
        Broadcast task update via WebSocket to all connected clients.

        In multi-worker environments, also publishes to Redis Pub/Sub so
        updates reach clients connected to other worker processes.
        """
        task_data = {
            "task_id": state.task_id,
            "status": state.status.value
            if isinstance(state.status, TaskStatus)
            else str(state.status),
            "task_type": state.task_type,
            "repo_name": state.repo_name,
            "user_id": state.user_id,
            "progress": state.progress,
            "step": state.step,
            "status_message": state.status_message,
            "error": state.error,
            "created_at": state.created_at,
            "started_at": state.started_at,
            "completed_at": state.completed_at,
            "queue_position": state.queue_position,
            "remote_host": state.remote_host,
            "trajectory": [asdict(event) for event in state.trajectory],
        }

        try:
            # 1. Broadcast to local WebSocket clients (same worker)
            from api.routes.tasks import get_ws_manager

            ws_manager = get_ws_manager()
            await ws_manager.broadcast({"type": "task_update", "task": task_data})
        except Exception as e:
            logger.debug(f"Failed to broadcast task update via WebSocket: {e}")

        # 2. Publish to Redis Pub/Sub (for other workers)
        try:
            from api.services.task_pubsub import get_pubsub_manager

            pubsub = get_pubsub_manager()
            if pubsub and pubsub.is_connected:
                await pubsub.publish_task_update(task_data)
        except Exception as e:
            logger.debug(f"Failed to publish task update to Redis: {e}")

    async def start_queue_processor(self) -> None:
        """Start the background queue processor."""
        if self._processor_task is None or self._processor_task.done():
            self._shutdown = False
            self._processor_task = asyncio.create_task(self._process_queue())
            logger.info("Task queue processor started")

    async def stop_queue_processor(self) -> None:
        """Stop the queue processor gracefully."""
        self._shutdown = True
        self._queue_event.set()
        if self._processor_task and not self._processor_task.done():
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass
        logger.info("Task queue processor stopped")

    async def start_cleanup_job(self, cleanup_interval_hours: int = 24) -> None:
        """Start background cleanup job for old tasks.

        Args:
            cleanup_interval_hours: How often to run cleanup (default 24 hours)
        """
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(
                self._run_cleanup_job(cleanup_interval_hours)
            )
            logger.info(
                f"Task cleanup job started (interval: {cleanup_interval_hours} hours)"
            )

    async def stop_cleanup_job(self) -> None:
        """Stop the cleanup job gracefully."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        logger.info("Task cleanup job stopped")

    async def _run_cleanup_job(self, cleanup_interval_hours: int = 24) -> None:
        """Background task that periodically cleans up old tasks."""

        logger.info(
            f"Cleanup job running with interval: {cleanup_interval_hours} hours"
        )

        while not self._shutdown:
            try:
                # Wait for the interval
                await asyncio.sleep(cleanup_interval_hours * 3600)

                if self._shutdown:
                    break

                # Run cleanup
                if self.is_using_redis:
                    # Redis store has its own cleanup method
                    await self._redis_store.cleanup_old_tasks()
                else:
                    await self.cleanup_old_tasks(
                        completed_max_age_hours=168,  # Keep completed tasks for 7 days
                        failed_max_age_hours=24,  # Keep failed tasks for 1 day
                        cancelled_max_age_hours=24,  # Keep cancelled tasks for 1 day
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup job: {e}", exc_info=True)
                # Continue running even on error, just log it
                await asyncio.sleep(60)  # Wait before retry

    def _get_task_type_key(self, task_type: str) -> str:
        """Get the queue key for a task type."""
        if task_type in self._pending_queue:
            return task_type
        return "other"

    def _is_local_host(self, host: str) -> bool:
        """Check if a hostname/IP refers to the current server.

        This is important for shared filesystem environments where multiple
        servers share the same task storage directory.

        Args:
            host: Hostname or IP address to check

        Returns:
            True if the host refers to this server
        """
        import socket

        if not host or host in ("", "localhost", "127.0.0.1"):
            return True

        try:
            # Get all IP addresses for this machine
            hostname = socket.gethostname()
            local_ips = set()

            # Add hostname-based IPs
            try:
                local_ips.update(socket.gethostbyname_ex(hostname)[2])
            except socket.gaierror:
                pass

            # Add IPs from all network interfaces (cross-platform)
            try:
                for info in socket.getaddrinfo(hostname, None):
                    addr = info[4][0]
                    if addr not in ("127.0.0.1", "::1", "0.0.0.0"):
                        local_ips.add(addr)
            except Exception:
                pass

            # Check if the given host matches any local IP
            try:
                host_ip = socket.gethostbyname(host)
                if host_ip in local_ips or host_ip in ("127.0.0.1", "::1"):
                    return True
            except socket.gaierror:
                pass

            # Direct comparison
            if host in local_ips:
                return True

            # Check if host is the hostname
            if host == hostname or host.split(".")[0] == hostname.split(".")[0]:
                return True

            return False

        except Exception as e:
            logger.debug(f"Error checking if {host} is local: {e}")
            return False

    async def _update_queue_positions(self, task_type: str) -> None:
        """Update queue positions for all pending tasks of a type."""
        queue = self._pending_queue.get(task_type, deque())
        for i, (task_id, _, _, _) in enumerate(queue):
            state = await self.store.get(task_id)
            if state and state.status == TaskStatus.PENDING:
                state.queue_position = i + 1  # 1-indexed
                await self.store.save(state)

    async def _process_queue(self) -> None:
        """Background task that processes pending tasks."""
        logger.info("Queue processor started")

        while not self._shutdown:
            await self._queue_event.wait()
            self._queue_event.clear()

            # Process all task types
            for task_type in list(self._pending_queue.keys()):
                await self._process_queue_for_type(task_type)

    async def _process_queue_for_type(self, task_type: str) -> None:
        """Process pending tasks for a specific type."""
        queue = self._pending_queue.get(task_type, deque())
        max_concurrent = self._max_concurrency.get(task_type, 2)
        type_lock = self._type_locks.get(task_type, self._lock)

        while queue and self._running_count.get(task_type, 0) < max_concurrent:
            if self._shutdown:
                break

            async with type_lock:
                if not queue:
                    break
                task_id, task_func, args, kwargs = queue.popleft()
                self._running_count[task_type] = (
                    self._running_count.get(task_type, 0) + 1
                )

            # Update queue positions for remaining tasks
            await self._update_queue_positions(task_type)

            # Start the task
            await self._execute_task(task_id, task_type, task_func, args, kwargs)

    async def _execute_task(
        self,
        task_id: str,
        task_type: str,
        task_func: Callable,
        args: tuple,
        kwargs: dict,
    ) -> None:
        """Execute a task and handle completion."""

        async def wrapper():
            try:
                # Check if status is already RUNNING (set by run_task() for immediate execution)
                # Only update if it's not yet RUNNING (e.g., task from queue)
                state = await self.store.get(task_id)
                if state and state.status != TaskStatus.RUNNING:
                    await self.update_task(
                        task_id,
                        status=TaskStatus.RUNNING,
                        status_message="Task is now running",
                    )

                # Update state to show queue_position = 0 (running)
                state = await self.store.get(task_id)
                if state:
                    state.queue_position = 0
                    await self.store.save(state)

                await task_func(task_id, *args, **kwargs)
            except asyncio.CancelledError:
                logger.info(f"Task cancelled: {task_id}")
                await self.update_task(
                    task_id, status=TaskStatus.CANCELLED, error="Task was cancelled"
                )
            except Exception as e:
                logger.error(f"Task failed: {task_id}, error: {e}", exc_info=True)
                await self.update_task(task_id, status=TaskStatus.FAILED, error=str(e))
            finally:
                async with self._lock:
                    self._running_tasks.pop(task_id, None)
                    self._running_count[task_type] = max(
                        0, self._running_count.get(task_type, 1) - 1
                    )

                # Trigger queue processing for this type
                self._queue_event.set()

        # Create and store the task
        task = asyncio.create_task(wrapper())
        async with self._lock:
            self._running_tasks[task_id] = task

        logger.info(f"Started task: {task_id} (type={task_type})")

    async def run_task(
        self,
        task_id: str,
        task_func: Callable,
        *args,
        **kwargs,
    ) -> int:
        """
        Queue a task for execution with concurrency control.

        If the task type has available slots, it runs immediately.
        Otherwise, it stays in PENDING state until a slot is available.

        Args:
            task_id: Unique task identifier (must already be created via create_task)
            task_func: Async function to execute
            *args: Positional arguments for task_func
            **kwargs: Keyword arguments for task_func

        Returns:
            Queue position (0 = running immediately, 1+ = waiting in queue)
        """
        # Get task state to determine type
        state = await self.store.get(task_id)
        if not state:
            logger.error(f"Task not found: {task_id}")
            return -1

        task_type = self._get_task_type_key(state.task_type)
        max_concurrent = self._max_concurrency.get(task_type, 2)
        current_running = self._running_count.get(task_type, 0)
        type_lock = self._type_locks.get(task_type, self._lock)

        if current_running < max_concurrent:
            # Can run immediately - update status to RUNNING before returning API response
            # This ensures frontend sees the RUNNING status immediately
            await self.update_task(
                task_id, status=TaskStatus.RUNNING, status_message="Task starting..."
            )

            async with type_lock:
                self._running_count[task_type] = current_running + 1

            await self._execute_task(task_id, task_type, task_func, args, kwargs)
            return 0
        else:
            # Add to queue - task stays in PENDING state
            async with type_lock:
                self._pending_queue[task_type].append(
                    (task_id, task_func, args, kwargs)
                )
                queue_position = len(self._pending_queue[task_type])

            # Update state with queue position
            state.queue_position = queue_position
            state.status_message = f"Waiting in queue (position {queue_position})"
            await self.store.save(state)

            logger.info(
                f"Task {task_id} queued at position {queue_position} (type={task_type})"
            )
            return queue_position

    def register_task(self, task_id: str, asyncio_task: asyncio.Task) -> None:
        """
        Register an externally created asyncio task for tracking and cancellation.

        Use this when you create tasks with asyncio.create_task() directly
        instead of using run_task().

        Args:
            task_id: The task ID (should match the one in TaskState)
            asyncio_task: The asyncio.Task object to track
        """
        self._running_tasks[task_id] = asyncio_task
        logger.debug(f"Registered external task: {task_id}")

    def unregister_task(self, task_id: str) -> None:
        """
        Unregister a task when it completes.

        Call this in your task's finally block.
        """
        self._running_tasks.pop(task_id, None)
        logger.debug(f"Unregistered task: {task_id}")

    async def cancel_task(self, task_id: str, from_remote: bool = False) -> bool:
        """
        Cancel a running or pending task.

        For pending tasks: removes from queue
        For running tasks: cancels the asyncio task
        For remote tasks: proxies cancel request to remote host

        Args:
            task_id: Task ID to cancel
            from_remote: If True, this is a cancel request from a remote server,
                         don't proxy back to remote

        Returns True if task was cancelled, False if not found or already completed.
        """
        state = await self.store.get(task_id)
        if not state:
            logger.warning(f"Cancel: task not found in store: {task_id}")
            return False

        # In shared filesystem environments, the proxy server may have already updated
        # the status to cancelled. When from_remote=True, we should still try to
        # stop the actual task even if the file says it's already cancelled.
        if state.status in [
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        ]:
            if from_remote:
                # Remote request - still try to stop the actual task
                logger.info(
                    f"Cancel (from_remote): file shows terminal state {state.status.value}, but still trying to stop task: {task_id}"
                )
            else:
                logger.info(
                    f"Cancel: task already in terminal state: {task_id} ({state.status.value})"
                )
                return False  # Already terminal

        # In shared filesystem environments, we can signal cancellation by updating
        # the task file directly. The remote server's GraphUpdater checks the file
        # status periodically and will stop when it sees the cancelled state.
        #
        # This approach is more reliable than HTTP proxying, which can timeout
        # when the remote server is under heavy load during graph builds.
        if state.remote_host and state.remote_host not in (
            "",
            "localhost",
            "127.0.0.1",
        ):
            if not self._is_local_host(state.remote_host):
                # Remote task on shared filesystem - update status directly
                # The remote GraphUpdater will see this and stop
                logger.info(
                    "Cancel: remote task on shared filesystem, updating status directly"
                )
                await self.update_task(
                    task_id,
                    status=TaskStatus.CANCELLED,
                    error="Task cancelled by user (via shared filesystem)",
                )
                # Also try HTTP proxy as backup (non-blocking, short timeout)
                try:
                    import httpx

                    async with httpx.AsyncClient(timeout=2.0) as client:
                        cancel_url = f"http://{state.remote_host}:8005/api/tasks/{task_id}/cancel?from_remote=true"
                        await client.post(cancel_url)
                        logger.info("Cancel: backup HTTP proxy succeeded")
                except Exception as e:
                    logger.debug(
                        f"Cancel: backup HTTP proxy failed (expected if server busy): {e}"
                    )
                return True

        logger.info(f"Cancel: handling locally (from_remote={from_remote})")

        # Check if task is pending in queue
        if state.status == TaskStatus.PENDING:
            task_type = self._get_task_type_key(state.task_type)
            queue = self._pending_queue.get(task_type, deque())
            type_lock = self._type_locks.get(task_type, self._lock)

            async with type_lock:
                # Find and remove from queue
                for item in list(queue):
                    if item[0] == task_id:
                        queue.remove(item)
                        logger.info(f"Removed pending task from queue: {task_id}")
                        break

            # Update queue positions
            await self._update_queue_positions(task_type)

            await self.update_task(
                task_id,
                status=TaskStatus.CANCELLED,
                error="Task cancelled while pending in queue",
            )
            return True

        # Check if task is running locally (has asyncio.Task registered)
        async with self._lock:
            task = self._running_tasks.get(task_id)
            running_task_ids = list(self._running_tasks.keys())

        logger.info(
            f"Cancel check: task_id={task_id}, found_in_running={task is not None}, running_tasks={running_task_ids}"
        )

        if task:
            logger.info(f"Cancelling running task: {task_id}")

            # For graph_build tasks, signal the GraphUpdater to stop
            if state.task_type == "graph_build":
                try:
                    from api.routes.graph import cancel_graph_build

                    cancelled = await cancel_graph_build(task_id)
                    logger.info(f"cancel_graph_build returned: {cancelled}")
                except Exception as e:
                    logger.warning(f"Failed to signal graph updater cancellation: {e}")

            task.cancel()
            # Wait a bit for the task to process the cancellation
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
            except (TimeoutError, asyncio.CancelledError):
                pass
            return True

        # Task is RUNNING in file store but no asyncio task reference
        # This happens when the task was started but the reference was lost
        # (e.g., server restart, or task created by a different process)
        logger.info(f"Marking task as cancelled (no asyncio reference): {task_id}")

        # Still try to signal GraphUpdater cancellation even without asyncio task reference
        # The updater might be registered in _active_updaters separately
        if state.task_type == "graph_build":
            try:
                from api.routes.graph import cancel_graph_build

                cancelled = await cancel_graph_build(task_id)
                logger.info(
                    f"cancel_graph_build (no asyncio ref) returned: {cancelled}"
                )
            except Exception as e:
                logger.warning(f"Failed to signal graph updater cancellation: {e}")

        await self.update_task(
            task_id, status=TaskStatus.CANCELLED, error="Task cancelled by user"
        )
        return True

    async def _cancel_remote_task(self, task_id: str, remote_host: str) -> bool:
        """Proxy cancel request to remote host."""
        try:
            import httpx

            # Assume remote server runs on same API port (8005)
            # Pass from_remote=true to prevent infinite proxy loop
            cancel_url = (
                f"http://{remote_host}:8005/api/tasks/{task_id}/cancel?from_remote=true"
            )

            logger.info(f"Proxying cancel request to remote host: {cancel_url}")

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(cancel_url)

                if response.status_code == 200:
                    data = response.json()
                    if data.get("success"):
                        logger.info(
                            f"Remote task cancelled: {task_id} on {remote_host}"
                        )
                        # Update local state
                        await self.update_task(
                            task_id,
                            status=TaskStatus.CANCELLED,
                            error=f"Task cancelled on remote host {remote_host}",
                        )
                        return True
                    else:
                        # Remote returned success=false, update local state anyway
                        logger.warning(
                            f"Remote cancel returned success=false: {data.get('message')}"
                        )
                        await self.update_task(
                            task_id,
                            status=TaskStatus.CANCELLED,
                            error=f"Remote cancel: {data.get('message', 'unknown')}",
                        )
                        return True

                logger.warning(
                    f"Failed to cancel remote task: {response.status_code} - {response.text}"
                )
                # Update local state anyway
                await self.update_task(
                    task_id,
                    status=TaskStatus.CANCELLED,
                    error=f"Remote cancel failed: HTTP {response.status_code}",
                )
                return True

        except Exception as e:
            logger.error(
                f"Failed to cancel remote task {task_id} on {remote_host}: {e}"
            )
            # Update local state anyway
            await self.update_task(
                task_id,
                status=TaskStatus.CANCELLED,
                error=f"Task cancelled (remote cancel failed: {e})",
            )
            return True

    async def stream_task_updates(
        self,
        task_id: str,
        poll_interval: float = 0.5,
    ) -> AsyncIterator[TaskState]:
        """
        Stream task status updates (polling-based).

        Yields TaskState whenever it changes.
        Useful for Server-Sent Events (SSE).
        """
        last_state = None

        while True:
            state = await self.get_task_status(task_id)
            if state is None:
                logger.warning(f"Task not found: {task_id}")
                break

            # Yield if state changed
            if last_state is None or state != last_state:
                yield state
                last_state = state

            # Stop if task is terminal
            if state.status in [
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ]:
                break

            # Wait before next poll
            await asyncio.sleep(poll_interval)

    async def list_active_tasks(
        self,
        include_recent_completed: bool = True,
        recent_minutes: int = 5,
    ) -> list[TaskState]:
        """
        List all active tasks and optionally recent completed ones.

        Args:
            include_recent_completed: Include tasks completed in last N minutes
            recent_minutes: How many minutes back to include completed tasks

        Returns:
            List of TaskState objects
        """
        all_tasks = await self.store.list()

        if not include_recent_completed:
            # Only return active/resumable tasks
            return [
                t
                for t in all_tasks
                if t.status
                in [TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.STALLED]
            ]

        # Include active + recently completed tasks
        from datetime import timedelta

        cutoff = datetime.now(UTC) - timedelta(minutes=recent_minutes)

        result = []
        for task in all_tasks:
            if task.status in [
                TaskStatus.PENDING,
                TaskStatus.RUNNING,
                TaskStatus.STALLED,
            ]:
                result.append(task)
            elif task.completed_at:
                try:
                    completed_time = datetime.fromisoformat(
                        task.completed_at.replace("Z", "+00:00")
                    )
                    if completed_time > cutoff:
                        result.append(task)
                except (ValueError, TypeError):
                    pass

        # Sort by created_at descending (newest first)
        result.sort(key=lambda t: t.created_at, reverse=True)
        return result

    async def cleanup_old_tasks(
        self,
        completed_max_age_hours: int = 168,  # 7 days for completed tasks
        failed_max_age_hours: int = 24,  # 1 day for failed tasks
        cancelled_max_age_hours: int = 24,  # 1 day for cancelled tasks
    ) -> int:
        """
        Remove old completed/failed/cancelled tasks.

        Args:
            completed_max_age_hours: Age threshold for completed tasks (default 7 days)
            failed_max_age_hours: Age threshold for failed tasks (default 1 day)
            cancelled_max_age_hours: Age threshold for cancelled tasks (default 1 day)

        Returns:
            Number of tasks deleted
        """
        from datetime import timedelta

        all_tasks = await self.store.list()
        deleted_count = 0
        status_counts = {
            TaskStatus.COMPLETED: 0,
            TaskStatus.FAILED: 0,
            TaskStatus.CANCELLED: 0,
        }

        for task in all_tasks:
            if task.status == TaskStatus.COMPLETED:
                cutoff = datetime.now(UTC) - timedelta(hours=completed_max_age_hours)
            elif task.status == TaskStatus.FAILED:
                cutoff = datetime.now(UTC) - timedelta(hours=failed_max_age_hours)
            elif task.status == TaskStatus.CANCELLED:
                cutoff = datetime.now(UTC) - timedelta(hours=cancelled_max_age_hours)
            else:
                continue

            try:
                created_time = datetime.fromisoformat(
                    task.created_at.replace("Z", "+00:00")
                )
                if created_time < cutoff:
                    await self.store.delete(task.task_id)
                    deleted_count += 1
                    status_counts[task.status] += 1
            except (ValueError, TypeError):
                pass

        if deleted_count > 0:
            logger.info(
                f"Cleaned up {deleted_count} old tasks: "
                f"{status_counts[TaskStatus.COMPLETED]} completed, "
                f"{status_counts[TaskStatus.FAILED]} failed, "
                f"{status_counts[TaskStatus.CANCELLED]} cancelled"
            )
        return deleted_count


# Global task manager instance
_task_manager: BackgroundTaskManager | None = None


def get_task_manager(
    store_type: str = "redis",
    store_dir: Path | None = None,
    redis_url: str | None = None,
) -> BackgroundTaskManager:
    """
    Get or create the global task manager.

    Args:
        store_type: "redis" (recommended), "memory", or "filesystem"
        store_dir: Directory for filesystem store (required if store_type="filesystem")
        redis_url: Redis connection URL (optional, reads from config if not provided)

    Returns:
        BackgroundTaskManager instance
    """
    global _task_manager

    if _task_manager is None:
        if store_type == "filesystem":
            if not store_dir:
                raise ValueError("store_dir required for filesystem store")
            store = FileSystemTaskStore(store_dir)
            _task_manager = BackgroundTaskManager(store, use_redis=False)
        elif store_type == "memory":
            store = InMemoryTaskStore()
            _task_manager = BackgroundTaskManager(store, use_redis=False)
        else:  # redis
            _task_manager = BackgroundTaskManager(use_redis=True, redis_url=redis_url)

        logger.info(f"Initialized BackgroundTaskManager with {store_type} store")

    return _task_manager


def initialize_task_manager(
    store_type: str = "redis",
    store_dir: Path | None = None,
    redis_url: str | None = None,
) -> BackgroundTaskManager:
    """Initialize the task manager for the application.

    Args:
        store_type: "redis" (recommended), "memory", or "filesystem"
        store_dir: Directory for filesystem store
        redis_url: Redis connection URL (optional)

    Returns:
        BackgroundTaskManager instance
    """
    return get_task_manager(
        store_type=store_type, store_dir=store_dir, redis_url=redis_url
    )

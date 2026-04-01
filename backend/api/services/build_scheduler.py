# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from loguru import logger


class BuildPriority(Enum):
    """Priority levels for build jobs."""

    HIGH = 1  # User-initiated refresh
    NORMAL = 2  # Scheduled builds
    LOW = 3  # Background maintenance


class ThrottleLevel(Enum):
    """Throttle levels for build operations."""

    NONE = 0  # No throttling (fastest builds, may block reads)
    LOW = 1  # Light throttling (fast builds, minimal impact)
    MEDIUM = 2  # Moderate throttling (balanced)
    HIGH = 3  # Heavy throttling (slowest builds, reads prioritized)
    EXTREME = 4  # Maximum throttling (for emergency situations)


# Throttle settings per level: (write_delay_ms, batch_size, yield_interval)
THROTTLE_SETTINGS = {
    ThrottleLevel.NONE: (0, 1000, 0),
    ThrottleLevel.LOW: (20, 1000, 5),
    ThrottleLevel.MEDIUM: (50, 600, 10),
    ThrottleLevel.HIGH: (100, 400, 20),
    ThrottleLevel.EXTREME: (200, 200, 50),
}


@dataclass
class BuildJob:
    """Represents a queued build job."""

    job_id: str
    project_name: str
    repo_path: str
    priority: BuildPriority = BuildPriority.NORMAL
    fast_mode: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    started_at: str | None = None
    completed_at: str | None = None
    status: str = "queued"  # queued, running, completed, failed, cancelled

    # The actual coroutine to execute
    task_func: Callable[..., Coroutine] | None = None
    task_args: tuple = ()
    task_kwargs: dict = field(default_factory=dict)


class BuildScheduler:
    """
    Singleton scheduler that manages graph build queue.

    Key features:
    - Only ONE build runs at a time (prevents CPU saturation)
    - Priority queue (HIGH > NORMAL > LOW)
    - Configurable write throttling with preset levels
    - Auto-throttle based on system load
    - Status visibility for all users
    """

    _instance: BuildScheduler | None = None
    _lock = asyncio.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        # Build queue (priority-based)
        self._queue: deque[BuildJob] = deque()
        self._current_job: BuildJob | None = None
        self._current_task: asyncio.Task | None = None

        # Throttling settings - default to MEDIUM for balanced performance
        self._throttle_level: ThrottleLevel = ThrottleLevel.LOW
        settings = THROTTLE_SETTINGS[self._throttle_level]
        self._write_delay_ms: int = settings[0]
        self._batch_size: int = settings[1]
        self._yield_interval: int = settings[2]

        # Auto-throttle settings
        self._auto_throttle: bool = True  # Enable automatic throttle adjustment
        self._cpu_threshold_high: float = 80.0  # Increase throttle above this
        self._cpu_threshold_low: float = 50.0  # Decrease throttle below this

        # Status tracking
        self._completed_jobs: deque[BuildJob] = deque(maxlen=20)  # Keep last 20

        # Event for queue processing
        self._queue_event = asyncio.Event()
        self._shutdown = False

        # Background processor task
        self._processor_task: asyncio.Task | None = None

        # Pause/resume support
        self._paused = False
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # Start in unpaused state

        self._initialized = True
        logger.info(
            f"BuildScheduler initialized (throttle_level={self._throttle_level.name})"
        )

    async def start(self) -> None:
        """Start the background queue processor."""
        if self._processor_task is None or self._processor_task.done():
            self._shutdown = False
            self._processor_task = asyncio.create_task(self._process_queue())
            logger.info("BuildScheduler queue processor started")

    async def stop(self) -> None:
        """Stop the scheduler gracefully."""
        self._shutdown = True
        self._queue_event.set()

        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
            try:
                await self._current_task
            except asyncio.CancelledError:
                pass

        if self._processor_task and not self._processor_task.done():
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass

        logger.info("BuildScheduler stopped")

    async def enqueue(
        self,
        job_id: str,
        project_name: str,
        repo_path: str,
        task_func: Callable[..., Coroutine],
        priority: BuildPriority = BuildPriority.NORMAL,
        fast_mode: bool = False,
        **task_kwargs,
    ) -> int:
        """
        Add a build job to the queue.

        Args:
            job_id: Unique job identifier
            project_name: Name of the project
            repo_path: Path to repository
            task_func: Async function to execute
            priority: Job priority level
            fast_mode: Skip embeddings for faster builds
            **task_kwargs: Arguments to pass to task_func

        Returns:
            Position in queue (0 = running now, 1+ = waiting)
        """
        async with self._lock:
            # Check if this project is already in queue
            for existing_job in self._queue:
                if existing_job.project_name == project_name:
                    logger.warning(f"Project {project_name} already in queue, skipping")
                    return -1  # Already queued

            if self._current_job and self._current_job.project_name == project_name:
                logger.warning(f"Project {project_name} is currently building")
                return 0  # Currently running

            job = BuildJob(
                job_id=job_id,
                project_name=project_name,
                repo_path=repo_path,
                priority=priority,
                fast_mode=fast_mode,
                task_func=task_func,
                task_kwargs=task_kwargs,
            )

            # Insert based on priority (higher priority = earlier)
            inserted = False
            for i, existing in enumerate(self._queue):
                if job.priority.value < existing.priority.value:
                    self._queue.insert(i, job)
                    inserted = True
                    break

            if not inserted:
                self._queue.append(job)

            position = list(self._queue).index(job) + 1
            if self._current_job:
                position += 1  # Account for running job

            logger.info(
                f"Queued build job {job_id} for {project_name} "
                f"(priority={priority.name}, position={position})"
            )

            # Wake up the processor
            self._queue_event.set()

            return position

    async def _process_queue(self) -> None:
        """Background task that processes the build queue."""
        logger.info("Queue processor started")

        while not self._shutdown:
            # Wait for jobs
            await self._queue_event.wait()
            self._queue_event.clear()

            while self._queue and not self._shutdown:
                # Wait if paused
                await self._pause_event.wait()

                async with self._lock:
                    if not self._queue:
                        break
                    job = self._queue.popleft()
                    self._current_job = job
                    job.started_at = datetime.now(UTC).isoformat()
                    job.status = "running"

                logger.info(f"Starting build job {job.job_id} for {job.project_name}")

                try:
                    if job.task_func:
                        # Execute the build task with throttling settings
                        self._current_task = asyncio.create_task(
                            job.task_func(
                                job.job_id,
                                job.project_name,
                                job.repo_path,
                                job.fast_mode,
                                # Pass throttling settings
                                write_delay_ms=self._write_delay_ms,
                                batch_size=self._batch_size,
                                **job.task_kwargs,
                            )
                        )
                        await self._current_task
                        job.status = "completed"

                except asyncio.CancelledError:
                    logger.info(f"Build job {job.job_id} was cancelled")
                    job.status = "cancelled"
                except Exception as e:
                    logger.error(f"Build job {job.job_id} failed: {e}", exc_info=True)
                    job.status = "failed"
                finally:
                    job.completed_at = datetime.now(UTC).isoformat()
                    async with self._lock:
                        self._completed_jobs.append(job)
                        self._current_job = None
                        self._current_task = None

                    # Small delay between jobs to let reads through
                    await asyncio.sleep(0.5)

        logger.info("Queue processor stopped")

    def pause(self) -> None:
        """Pause queue processing (current job continues)."""
        self._paused = True
        self._pause_event.clear()
        logger.info("Build queue paused")

    def resume(self) -> None:
        """Resume queue processing."""
        self._paused = False
        self._pause_event.set()
        logger.info("Build queue resumed")

    def set_throttle_level(self, level: ThrottleLevel) -> None:
        """Set throttle level using predefined settings."""
        self._throttle_level = level
        settings = THROTTLE_SETTINGS[level]
        self._write_delay_ms = settings[0]
        self._batch_size = settings[1]
        self._yield_interval = settings[2]
        logger.info(
            f"Throttle level set to {level.name}: "
            f"delay={self._write_delay_ms}ms, batch={self._batch_size}"
        )

    def get_queue_status(self) -> dict[str, Any]:
        """Get current queue status."""
        return {
            "current_job": {
                "job_id": self._current_job.job_id,
                "project_name": self._current_job.project_name,
                "started_at": self._current_job.started_at,
                "status": self._current_job.status,
            }
            if self._current_job
            else None,
            "queue_length": len(self._queue),
            "queued_jobs": [
                {
                    "job_id": job.job_id,
                    "project_name": job.project_name,
                    "priority": job.priority.name,
                    "created_at": job.created_at,
                    "status": job.status,
                }
                for job in self._queue
            ],
            "recent_jobs": [
                {
                    "job_id": job.job_id,
                    "project_name": job.project_name,
                    "status": job.status,
                    "started_at": job.started_at,
                    "completed_at": job.completed_at,
                }
                for job in list(self._completed_jobs)[-5:]  # Last 5 completed
            ],
            "paused": self._paused,
            "throttling": {
                "level": self._throttle_level.name,
                "write_delay_ms": self._write_delay_ms,
                "batch_size": self._batch_size,
                "auto_throttle": self._auto_throttle,
            },
        }

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a queued or running job."""
        async with self._lock:
            # Check if it's the current job
            if self._current_job and self._current_job.job_id == job_id:
                if self._current_task and not self._current_task.done():
                    self._current_task.cancel()
                    return True

            # Check queue
            for job in list(self._queue):
                if job.job_id == job_id:
                    self._queue.remove(job)
                    return True

        return False

    def set_throttling(
        self,
        write_delay_ms: int | None = None,
        batch_size: int | None = None,
    ) -> None:
        """
        Adjust throttling settings.

        Higher write_delay_ms and lower batch_size = more CPU for reads
        Lower write_delay_ms and higher batch_size = faster builds
        """
        if write_delay_ms is not None:
            self._write_delay_ms = max(0, write_delay_ms)
        if batch_size is not None:
            self._batch_size = max(100, min(2000, batch_size))

        logger.info(
            f"Throttling updated: write_delay={self._write_delay_ms}ms, "
            f"batch_size={self._batch_size}"
        )


# Global singleton instance
_scheduler: BuildScheduler | None = None


def get_build_scheduler() -> BuildScheduler:
    """Get the global BuildScheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = BuildScheduler()
    return _scheduler


async def initialize_build_scheduler() -> BuildScheduler:
    """Initialize and start the build scheduler."""
    scheduler = get_build_scheduler()
    await scheduler.start()
    return scheduler

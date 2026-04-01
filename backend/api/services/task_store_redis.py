# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import builtins
import json
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime

import redis.asyncio as redis
from loguru import logger

from .task_queue import TaskState, TaskStatus

# Redis keys
TASK_KEY_PREFIX = "atcode:task:"
ACTIVE_TASKS_KEY = "atcode:active_tasks"
TASK_EVENTS_STREAM = "atcode:task_events"
TASK_UPDATE_CHANNEL = "atcode:task_updates"


class RedisTaskStore:
    """
    Redis-based task store with strong consistency and event compensation.

    Features:
    - Atomic operations with pipeline
    - Event stream for compensation (handles missed Pub/Sub messages)
    - Single source of truth across all workers
    - Automatic expiration (7 days)
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        task_ttl_seconds: int = 86400 * 7,  # 7 days
    ):
        """
        Initialize Redis task store.

        Args:
            redis_url: Redis connection URL
            task_ttl_seconds: Time-to-live for task data (default 7 days)
        """
        self.redis_url = redis_url
        self.task_ttl = task_ttl_seconds
        self._redis: redis.Redis | None = None
        self._connected = False

    async def connect(self) -> bool:
        """
        Connect to Redis.

        Returns:
            True if connection successful
        """
        try:
            self._redis = redis.from_url(
                self.redis_url, encoding="utf-8", decode_responses=True
            )
            await self._redis.ping()
            self._connected = True
            logger.info(f"✅ RedisTaskStore connected: {self.redis_url}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to connect to Redis: {e}")
            self._connected = False
            return False

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            self._connected = False
            logger.info("RedisTaskStore connection closed")

    @property
    def is_connected(self) -> bool:
        """Check if connected to Redis."""
        return self._connected and self._redis is not None

    async def save(self, state: TaskState) -> bool:
        """
        Atomically save task state and publish event.

        Uses Redis pipeline to ensure atomicity:
        1. Save task to Hash (main storage)
        2. Update active tasks Set (index)
        3. Append to Stream (event log for compensation)
        4. Publish to Pub/Sub (fast real-time notification)

        Args:
            state: Task state to save

        Returns:
            True if save successful
        """
        if not self.is_connected:
            logger.error("Cannot save: Redis not connected")
            return False

        task_key = f"{TASK_KEY_PREFIX}{state.task_id}"
        task_data = self._state_to_dict(state)
        event_payload = state.to_dict()

        pipe = self._redis.pipeline()

        # 1. Save to Hash (main storage)
        pipe.hset(task_key, mapping=task_data)
        pipe.expire(task_key, self.task_ttl)

        # 2. Update active tasks index
        status_str = (
            state.status.value if hasattr(state.status, "value") else state.status
        )
        if status_str in [
            TaskStatus.PENDING.value,
            TaskStatus.RUNNING.value,
            TaskStatus.STALLED.value,
        ]:
            pipe.sadd(ACTIVE_TASKS_KEY, state.task_id)
        else:
            pipe.srem(ACTIVE_TASKS_KEY, state.task_id)

        # 3. Write to event stream (for compensation)
        event_data = {
            "task_id": state.task_id,
            "status": status_str,
            "timestamp": str(time.time()),
            "data": json.dumps(event_payload),
        }
        # maxlen=10000 keeps the stream size bounded
        pipe.xadd(TASK_EVENTS_STREAM, event_data, maxlen=10000)

        # 4. Publish to Pub/Sub (fast path for real-time updates)
        pipe.publish(
            TASK_UPDATE_CHANNEL,
            json.dumps({"type": "task_update", "task": event_payload}),
        )

        try:
            await pipe.execute()
            return True
        except Exception as e:
            logger.error(f"Failed to save task {state.task_id}: {e}")
            return False

    async def get(self, task_id: str) -> TaskState | None:
        """
        Get task state by ID.

        Args:
            task_id: Task ID to retrieve

        Returns:
            Task state or None if not found
        """
        if not self.is_connected:
            logger.error("Cannot get: Redis not connected")
            return None

        task_key = f"{TASK_KEY_PREFIX}{task_id}"
        try:
            data = await self._redis.hgetall(task_key)
            if not data:
                return None
            return self._dict_to_state(data)
        except Exception as e:
            logger.error(f"Failed to get task {task_id}: {e}")
            return None

    async def delete(self, task_id: str) -> bool:
        """
        Delete task state.

        Args:
            task_id: Task ID to delete

        Returns:
            True if deleted, False if not found
        """
        if not self.is_connected:
            return False

        task_key = f"{TASK_KEY_PREFIX}{task_id}"
        pipe = self._redis.pipeline()
        pipe.delete(task_key)
        pipe.srem(ACTIVE_TASKS_KEY, task_id)

        try:
            results = await pipe.execute()
            return results[0] > 0
        except Exception as e:
            logger.error(f"Failed to delete task {task_id}: {e}")
            return False

    async def list_active(self) -> builtins.list[TaskState]:
        """
        Get all active (PENDING or RUNNING) tasks.

        Returns:
            List of active task states
        """
        if not self.is_connected:
            return []

        try:
            # Get active task IDs from the Set
            task_ids = await self._redis.smembers(ACTIVE_TASKS_KEY)
            if not task_ids:
                return []

            # Batch fetch all tasks
            tasks = []
            pipe = self._redis.pipeline()
            for task_id in task_ids:
                pipe.hgetall(f"{TASK_KEY_PREFIX}{task_id}")

            results = await pipe.execute()

            for data in results:
                if data:
                    try:
                        state = self._dict_to_state(data)
                        # Only return truly active tasks (in case of stale index)
                        if state.status in [TaskStatus.PENDING, TaskStatus.RUNNING]:
                            tasks.append(state)
                    except Exception as e:
                        logger.warning(f"Failed to parse task data: {e}")

            return tasks
        except Exception as e:
            logger.error(f"Failed to list active tasks: {e}")
            return []

    async def list(self, limit: int = 1000) -> builtins.list[TaskState]:
        """
        Get all tasks (alias for list_all for compatibility).

        Args:
            limit: Maximum number of tasks to return

        Returns:
            List of task states
        """
        return await self.list_all(limit)

    async def list_all(self, limit: int = 100) -> builtins.list[TaskState]:
        """
        Get all tasks (for management interface).

        Args:
            limit: Maximum number of tasks to return

        Returns:
            List of task states
        """
        if not self.is_connected:
            return []

        tasks = []
        cursor = 0

        try:
            while True:
                cursor, keys = await self._redis.scan(
                    cursor, match=f"{TASK_KEY_PREFIX}*", count=100
                )

                for key in keys:
                    if len(tasks) >= limit:
                        return tasks

                    try:
                        data = await self._redis.hgetall(key)
                        if data:
                            tasks.append(self._dict_to_state(data))
                    except Exception as e:
                        logger.warning(f"Failed to read task {key}: {e}")

                if cursor == 0:
                    break

            return tasks
        except Exception as e:
            logger.error(f"Failed to list all tasks: {e}")
            return []

    async def get_events_since(
        self, last_id: str = "0", count: int = 100
    ) -> builtins.list[dict]:
        """
        Get events since a given ID (for compensation).

        Used by frontend to fetch missed events after WebSocket disconnect.

        Args:
            last_id: Last event ID received (default "0" for all)
            count: Maximum number of events to return

        Returns:
            List of event dictionaries with keys: id, task_id, status, timestamp, task
        """
        if not self.is_connected:
            return []

        try:
            # Use "(" prefix to get events AFTER last_id (exclusive)
            events = await self._redis.xrange(
                TASK_EVENTS_STREAM, min=f"({last_id}", count=count
            )

            result = []
            for event_id, data in events:
                result.append(
                    {
                        "id": event_id,
                        "task_id": data["task_id"],
                        "status": data["status"],
                        "timestamp": data["timestamp"],
                        "task": json.loads(data["data"]),
                    }
                )

            return result
        except Exception as e:
            logger.error(f"Failed to get events since {last_id}: {e}")
            return []

    async def cleanup_old_tasks(self, max_age_seconds: int = 86400 * 7) -> int:
        """
        Clean up old completed/failed tasks.

        Args:
            max_age_seconds: Maximum age to keep tasks (default 7 days)

        Returns:
            Number of tasks cleaned up
        """
        if not self.is_connected:
            return 0

        try:
            # Scan for old tasks
            cutoff_time = time.time() - max_age_seconds
            cursor = 0
            cleaned = 0

            while True:
                cursor, keys = await self._redis.scan(
                    cursor, match=f"{TASK_KEY_PREFIX}*", count=100
                )

                for key in keys:
                    try:
                        # Check task age and status
                        data = await self._redis.hgetall(key)
                        if data:
                            completed_at = data.get("completed_at")
                            status = data.get("status")

                            # Only clean terminal states
                            if status in ["completed", "failed", "cancelled"]:
                                if completed_at:
                                    try:
                                        comp_time = datetime.fromisoformat(
                                            completed_at.replace("Z", "+00:00")
                                        ).timestamp()
                                        if comp_time < cutoff_time:
                                            task_id = key.replace(TASK_KEY_PREFIX, "")
                                            await self.delete(task_id)
                                            cleaned += 1
                                    except (ValueError, TypeError):
                                        pass
                    except Exception as e:
                        logger.debug(f"Failed to check task {key}: {e}")

                if cursor == 0:
                    break

            if cleaned > 0:
                logger.info(f"Cleaned up {cleaned} old tasks")

            return cleaned
        except Exception as e:
            logger.error(f"Failed to cleanup old tasks: {e}")
            return 0

    def _state_to_dict(self, state: TaskState) -> dict:
        """Convert TaskState to dict for Redis storage."""
        return {
            "task_id": state.task_id,
            "status": state.status.value
            if isinstance(state.status, TaskStatus)
            else state.status,
            "task_type": state.task_type,
            "repo_name": state.repo_name,
            "user_id": state.user_id,
            "progress": str(state.progress),
            "step": state.step,
            "status_message": state.status_message,
            "result": json.dumps(state.result) if state.result else "",
            "error": state.error or "",
            "created_at": state.created_at,
            "started_at": state.started_at or "",
            "completed_at": state.completed_at or "",
            "queue_position": str(state.queue_position),
            "remote_host": state.remote_host,
            "trajectory": json.dumps(
                [
                    asdict(event) if is_dataclass(event) else event
                    for event in state.trajectory
                ]
            ),
        }

    def _dict_to_state(self, data: dict) -> TaskState:
        """Convert dict from Redis to TaskState."""
        # Parse result JSON if present
        result = None
        if data.get("result"):
            try:
                result = json.loads(data["result"])
            except (json.JSONDecodeError, TypeError):
                pass

        trajectory = []
        if data.get("trajectory"):
            try:
                trajectory = json.loads(data["trajectory"])
            except (json.JSONDecodeError, TypeError):
                trajectory = []

        return TaskState(
            task_id=data["task_id"],
            status=TaskStatus(data["status"]),
            task_type=data["task_type"],
            repo_name=data["repo_name"],
            user_id=data["user_id"],
            progress=int(data.get("progress", 0)),
            step=data.get("step", ""),
            status_message=data.get("status_message", ""),
            result=result,
            error=data.get("error") or None,
            created_at=data["created_at"],
            started_at=data.get("started_at") or None,
            completed_at=data.get("completed_at") or None,
            queue_position=int(data.get("queue_position", 0)),
            remote_host=data.get("remote_host", ""),
            trajectory=trajectory,
        )


# Global singleton instance
_redis_store: RedisTaskStore | None = None


async def get_redis_store(redis_url: str | None = None) -> RedisTaskStore:
    """
    Get or create the global Redis store instance.

    Args:
        redis_url: Optional Redis URL (uses default if not provided)

    Returns:
        RedisTaskStore instance
    """
    global _redis_store

    if _redis_store is None:
        if redis_url is None:
            from core.config import settings

            redis_url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")

        _redis_store = RedisTaskStore(redis_url)
        await _redis_store.connect()

    return _redis_store


async def close_redis_store() -> None:
    """Close the global Redis store instance."""
    global _redis_store
    if _redis_store:
        await _redis_store.close()
        _redis_store = None

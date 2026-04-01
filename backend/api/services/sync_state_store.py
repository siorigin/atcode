# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Redis-based sync state storage for multi-worker environments.

This module provides distributed state management for file monitoring tasks,
ensuring that only one monitoring task runs per project across all workers.

Key features:
- Distributed locking to prevent race conditions
- Redis hash for storing watching task state
- Automatic lock expiration (TTL) to prevent deadlocks
"""

from __future__ import annotations

import uuid

import redis.asyncio as redis
from loguru import logger

# Redis keys
WATCHING_KEY = "atcode:sync:watching"  # Hash: project_name -> task_id
LOCK_PREFIX = "atcode:sync:lock:"  # String with TTL: lock token


class SyncStateStore:
    """
    Redis-based sync state storage for multi-worker environments.

    Manages watching task state across multiple API workers using Redis.
    Provides distributed locking to prevent race conditions when starting
    or stopping monitoring tasks.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        """
        Initialize sync state store.

        Args:
            redis_url: Redis connection URL
        """
        self.redis_url = redis_url
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
            logger.info(f"SyncStateStore connected to Redis: {self.redis_url}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect SyncStateStore to Redis: {e}")
            self._connected = False
            return False

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            self._connected = False
            logger.info("SyncStateStore connection closed")

    @property
    def is_connected(self) -> bool:
        """Check if connected to Redis."""
        return self._connected and self._redis is not None

    async def get_watching_task(self, project_name: str) -> str | None:
        """
        Get the watching task ID for a project.

        Args:
            project_name: Project name to look up

        Returns:
            Task ID if project has an active watching task, None otherwise
        """
        if not self.is_connected:
            logger.warning(
                "SyncStateStore: Redis not connected, cannot get watching task"
            )
            return None

        try:
            task_id = await self._redis.hget(WATCHING_KEY, project_name)
            return task_id
        except Exception as e:
            logger.error(f"Failed to get watching task for {project_name}: {e}")
            return None

    async def set_watching_task(self, project_name: str, task_id: str) -> bool:
        """
        Set the watching task ID for a project.

        Args:
            project_name: Project name
            task_id: Task ID to associate with the project

        Returns:
            True if set successful
        """
        if not self.is_connected:
            logger.warning(
                "SyncStateStore: Redis not connected, cannot set watching task"
            )
            return False

        try:
            await self._redis.hset(WATCHING_KEY, project_name, task_id)
            logger.debug(f"Set watching task: {project_name} -> {task_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to set watching task for {project_name}: {e}")
            return False

    async def remove_watching_task(self, project_name: str) -> bool:
        """
        Remove the watching task entry for a project.

        Args:
            project_name: Project name to remove

        Returns:
            True if removed (or didn't exist), False on error
        """
        if not self.is_connected:
            logger.warning(
                "SyncStateStore: Redis not connected, cannot remove watching task"
            )
            return False

        try:
            await self._redis.hdel(WATCHING_KEY, project_name)
            logger.debug(f"Removed watching task for: {project_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to remove watching task for {project_name}: {e}")
            return False

    async def list_watching_tasks(self) -> dict[str, str]:
        """
        List all projects with active watching tasks.

        Returns:
            Dictionary mapping project_name -> task_id
        """
        if not self.is_connected:
            return {}

        try:
            return await self._redis.hgetall(WATCHING_KEY)
        except Exception as e:
            logger.error(f"Failed to list watching tasks: {e}")
            return {}

    async def acquire_lock(self, project_name: str, ttl: int = 30) -> str | None:
        """
        Acquire a distributed lock for a project.

        Uses Redis SET NX (set if not exists) with TTL for atomic locking.
        The lock automatically expires after TTL seconds to prevent deadlocks.

        Args:
            project_name: Project name to lock
            ttl: Lock time-to-live in seconds (default 30s)

        Returns:
            Unique lock token if acquired, None if lock is held by another
        """
        if not self.is_connected:
            logger.warning("SyncStateStore: Redis not connected, cannot acquire lock")
            return None

        lock_key = f"{LOCK_PREFIX}{project_name}"
        token = str(uuid.uuid4())

        try:
            # SET NX with TTL: atomic set-if-not-exists
            acquired = await self._redis.set(
                lock_key,
                token,
                nx=True,  # Only set if key doesn't exist
                ex=ttl,  # Expire after TTL seconds
            )

            if acquired:
                logger.debug(f"Acquired lock for {project_name} (token={token[:8]}...)")
                return token
            else:
                logger.debug(
                    f"Failed to acquire lock for {project_name}: already locked"
                )
                return None
        except Exception as e:
            logger.error(f"Failed to acquire lock for {project_name}: {e}")
            return None

    async def release_lock(self, project_name: str, token: str) -> bool:
        """
        Release a distributed lock for a project.

        Only releases the lock if the token matches (prevents releasing
        locks held by other processes).

        Args:
            project_name: Project name to unlock
            token: The lock token returned by acquire_lock

        Returns:
            True if lock was released, False if token didn't match or error
        """
        if not self.is_connected:
            logger.warning("SyncStateStore: Redis not connected, cannot release lock")
            return False

        lock_key = f"{LOCK_PREFIX}{project_name}"

        # Lua script for atomic compare-and-delete
        # This ensures we only delete if the token matches
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """

        try:
            result = await self._redis.eval(lua_script, 1, lock_key, token)
            if result:
                logger.debug(f"Released lock for {project_name}")
                return True
            else:
                logger.warning(
                    f"Failed to release lock for {project_name}: token mismatch"
                )
                return False
        except Exception as e:
            logger.error(f"Failed to release lock for {project_name}: {e}")
            return False

    async def extend_lock(self, project_name: str, token: str, ttl: int = 30) -> bool:
        """
        Extend the TTL of an existing lock.

        Useful for long-running operations that need to hold the lock
        longer than the initial TTL.

        Args:
            project_name: Project name
            token: The lock token to verify ownership
            ttl: New TTL in seconds

        Returns:
            True if lock was extended, False if token didn't match or error
        """
        if not self.is_connected:
            return False

        lock_key = f"{LOCK_PREFIX}{project_name}"

        # Lua script for atomic compare-and-expire
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("expire", KEYS[1], ARGV[2])
        else
            return 0
        end
        """

        try:
            result = await self._redis.eval(lua_script, 1, lock_key, token, ttl)
            return bool(result)
        except Exception as e:
            logger.error(f"Failed to extend lock for {project_name}: {e}")
            return False


# Global singleton instance
_sync_state_store: SyncStateStore | None = None


async def initialize_sync_state_store(redis_url: str) -> SyncStateStore:
    """
    Initialize the global sync state store.

    Args:
        redis_url: Redis connection URL

    Returns:
        SyncStateStore instance
    """
    global _sync_state_store

    if _sync_state_store is None:
        _sync_state_store = SyncStateStore(redis_url)
        connected = await _sync_state_store.connect()
        if connected:
            logger.info("SyncStateStore initialized successfully")
        else:
            logger.warning(
                "SyncStateStore failed to connect, sync state will not be shared across workers"
            )

    return _sync_state_store


def get_sync_state_store() -> SyncStateStore | None:
    """
    Get the global sync state store instance.

    Returns:
        SyncStateStore instance or None if not initialized
    """
    return _sync_state_store


async def cleanup_sync_state_store() -> None:
    """Close the global sync state store connection."""
    global _sync_state_store

    if _sync_state_store:
        await _sync_state_store.close()
        _sync_state_store = None

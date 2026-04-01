# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable

from loguru import logger

# Redis channel for task updates
TASK_UPDATE_CHANNEL = "atcode:task_updates"


class TaskPubSubManager:
    """
    Manages Redis Pub/Sub for cross-worker task broadcasting.

    Each worker process:
    1. Subscribes to the Redis channel on startup
    2. Publishes task updates to the channel
    3. Forwards received updates to local WebSocket clients
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        """
        Initialize the Pub/Sub manager.

        Args:
            redis_url: Redis connection URL
        """
        self.redis_url = redis_url
        self._redis_pub = None
        self._redis_sub = None
        self._pubsub = None
        self._listener_task = None
        self._callbacks = []
        self._running = False
        # Unique ID for this worker to skip own messages
        self._worker_id = f"{os.getpid()}_{id(asyncio)}"

    @property
    def is_connected(self) -> bool:
        """Check if connected to Redis."""
        return self._redis_pub is not None

    async def connect(self) -> bool:
        """
        Connect to Redis and start listening for updates.

        Returns:
            True if connection successful, False otherwise
        """
        try:
            import redis.asyncio as redis

            self._redis_pub = redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            self._redis_sub = redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )

            # Test connection
            await self._redis_pub.ping()

            # Subscribe to updates channel
            self._pubsub = self._redis_sub.pubsub()
            await self._pubsub.subscribe(TASK_UPDATE_CHANNEL)

            self._running = True
            self._listener_task = asyncio.create_task(self._listen())

            logger.info(
                f"TaskPubSubManager connected to Redis (worker_id={self._worker_id[:20]}...)"
            )
            return True

        except Exception as e:
            logger.warning(f"Redis Pub/Sub not available: {e}")
            self._redis_pub = None
            self._redis_sub = None
            return False

    async def disconnect(self) -> None:
        """Disconnect from Redis and stop listening."""
        self._running = False

        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass

        if self._pubsub:
            try:
                await self._pubsub.unsubscribe(TASK_UPDATE_CHANNEL)
                await self._pubsub.close()
            except Exception as e:
                logger.debug(f"Error closing pubsub: {e}")

        if self._redis_pub:
            try:
                await self._redis_pub.close()
            except Exception:
                pass

        if self._redis_sub:
            try:
                await self._redis_sub.close()
            except Exception:
                pass

        self._redis_pub = None
        self._redis_sub = None
        logger.info("TaskPubSubManager disconnected")

    async def publish_task_update(self, task_data: dict) -> bool:
        """
        Publish task update to all workers via Redis.

        Args:
            task_data: Task state dictionary to broadcast

        Returns:
            True if published successfully, False otherwise
        """
        if not self._redis_pub:
            return False

        try:
            message = json.dumps({"worker_id": self._worker_id, "task": task_data})
            await self._redis_pub.publish(TASK_UPDATE_CHANNEL, message)
            logger.debug(f"Published task update: {task_data.get('task_id')}")
            return True

        except Exception as e:
            logger.error(f"Failed to publish task update: {e}")
            return False

    def on_task_update(
        self, callback: Callable[[dict], Awaitable[None] | None]
    ) -> None:
        """
        Register a callback for updates from other workers.

        The callback receives the task data dictionary.

        Args:
            callback: Async or sync function to call on task update
        """
        self._callbacks.append(callback)

    async def _listen(self) -> None:
        """Listen for messages from Redis."""
        logger.debug("Pub/Sub listener started")

        while self._running:
            try:
                msg = await self._pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if msg and msg["type"] == "message":
                    await self._handle_message(msg["data"])

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Pub/Sub listener error: {e}")
                await asyncio.sleep(1)

        logger.debug("Pub/Sub listener stopped")

    async def _handle_message(self, data: str) -> None:
        """
        Handle a received message from Redis.

        Args:
            data: JSON string with worker_id and task data
        """
        try:
            message = json.loads(data)

            # Skip our own messages to avoid duplicate broadcasts
            if message.get("worker_id") == self._worker_id:
                logger.debug("Skipping own message from Redis")
                return

            task_data = message.get("task")
            if not task_data:
                return

            logger.debug(
                f"Received task update from other worker: {task_data.get('task_id')}"
            )

            # Forward to all registered callbacks
            for callback in self._callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(task_data)
                    else:
                        callback(task_data)
                except Exception as e:
                    logger.error(f"Error in Pub/Sub callback: {e}")

        except Exception as e:
            logger.error(f"Error handling Pub/Sub message: {e}")


# Global singleton instance
_pubsub_manager: TaskPubSubManager | None = None


def get_pubsub_manager() -> TaskPubSubManager | None:
    """Get the global Pub/Sub manager instance."""
    return _pubsub_manager


async def initialize_pubsub(redis_url: str) -> TaskPubSubManager | None:
    """
    Initialize the global Pub/Sub manager.

    This should be called during application startup. It sets up the
    Redis connection and registers the callback to forward updates
    to local WebSocket clients.

    Args:
        redis_url: Redis connection URL

    Returns:
        TaskPubSubManager instance if connected, None otherwise
    """
    global _pubsub_manager

    if _pubsub_manager is not None:
        return _pubsub_manager

    _pubsub_manager = TaskPubSubManager(redis_url)

    if await _pubsub_manager.connect():
        # Register callback to forward Redis messages to local WebSocket clients
        from api.routes.tasks import get_ws_manager

        async def broadcast_to_local(task_data: dict) -> None:
            """Forward task update from Redis to local WebSocket clients."""
            try:
                ws_manager = get_ws_manager()
                await ws_manager.broadcast(
                    {
                        "type": "task_update",
                        "task": task_data,
                        "via_redis": True,  # Mark that this came via Redis
                    }
                )
            except Exception as e:
                logger.debug(f"Failed to broadcast Redis message to local clients: {e}")

        _pubsub_manager.on_task_update(broadcast_to_local)
        logger.info("Registered Pub/Sub callback for local WebSocket broadcast")

    return _pubsub_manager


async def cleanup_pubsub() -> None:
    """Clean up the global Pub/Sub manager."""
    global _pubsub_manager

    if _pubsub_manager:
        await _pubsub_manager.disconnect()
        _pubsub_manager = None

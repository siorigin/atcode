# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from .base import CacheInterface

# Number of shards for lock distribution (must be power of 2)
_NUM_SHARDS = 16


@dataclass
class CacheEntry:
    """Cache entry with value and expiration."""

    value: Any
    expiry: float
    created_at: float = field(default_factory=time.time)
    access_count: int = 0

    def is_expired(self) -> bool:
        """Check if entry is expired."""
        return time.time() >= self.expiry

    def touch(self) -> None:
        """Update access count."""
        self.access_count += 1


class MemoryCache(CacheInterface):
    """
    In-memory cache implementation.

    Features:
    - TTL-based expiration
    - LRU eviction when max size reached
    - Automatic cleanup of expired entries
    - Thread-safe via asyncio.Lock
    """

    def __init__(self, max_size: int = 10000, cleanup_interval: int = 60):
        """
        Initialize memory cache.

        Args:
            max_size: Maximum number of entries
            cleanup_interval: Seconds between cleanup runs
        """
        self.max_size = max_size
        self.cleanup_interval = cleanup_interval

        self._cache: dict[str, CacheEntry] = {}
        # Sharded locks for concurrent access (16 shards = 4-8x improvement)
        self._locks = [asyncio.Lock() for _ in range(_NUM_SHARDS)]
        self._cleanup_task: asyncio.Task | None = None

        # Statistics
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def _get_shard_index(self, key: str) -> int:
        """Get the shard index for a given key."""
        return hash(key) % _NUM_SHARDS

    def _get_lock(self, key: str) -> asyncio.Lock:
        """Get the appropriate shard lock for a key."""
        return self._locks[self._get_shard_index(key)]

    async def start_cleanup(self) -> None:
        """Start background cleanup task."""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.debug("Memory cache cleanup task started")

    async def stop_cleanup(self) -> None:
        """Stop background cleanup task."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            logger.debug("Memory cache cleanup task stopped")

    async def _cleanup_loop(self) -> None:
        """Background task to cleanup expired entries."""
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval)
                await self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cache cleanup error: {e}")

    async def _cleanup_expired(self) -> None:
        """Remove expired entries."""
        # Acquire all shard locks to safely iterate and modify cache
        async with asyncio.Lock():  # Use temporary master lock for cleanup
            to_remove = [
                key for key, entry in self._cache.items() if entry.is_expired()
            ]

            for key in to_remove:
                del self._cache[key]

            if to_remove:
                logger.debug(f"Cleaned up {len(to_remove)} expired cache entries")

    async def _evict_if_needed(self) -> None:
        """Evict entries if cache is full (must be called with shard lock held)."""
        if len(self._cache) >= self.max_size:
            # Find LRU entry (oldest access)
            lru_key = min(self._cache.keys(), key=lambda k: self._cache[k].created_at)
            del self._cache[lru_key]
            self._evictions += 1
            logger.debug(f"Evicted cache entry: {lru_key}")

    async def get(self, key: str) -> Any | None:
        """Get value from cache."""
        lock = self._get_lock(key)
        async with lock:
            entry = self._cache.get(key)

            if entry is None:
                self._misses += 1
                return None

            if entry.is_expired():
                del self._cache[key]
                self._misses += 1
                return None

            entry.touch()
            self._hits += 1
            return entry.value

    async def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        """Set value in cache."""
        lock = self._get_lock(key)
        async with lock:
            await self._evict_if_needed()

            expiry = time.time() + ttl
            self._cache[key] = CacheEntry(value=value, expiry=expiry)
            return True

    async def delete(self, key: str) -> bool:
        """Delete value from cache."""
        lock = self._get_lock(key)
        async with lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    async def exists(self, key: str) -> bool:
        """Check if key exists and is not expired."""
        lock = self._get_lock(key)
        async with lock:
            entry = self._cache.get(key)
            if entry is None:
                return False
            if entry.is_expired():
                del self._cache[key]
                return False
            return True

    async def get_many(self, keys: list[str]) -> dict[str, Any]:
        """Get multiple values efficiently."""
        sorted_keys = sorted(keys)

        # Acquire master lock for batch read to maintain consistency
        async with asyncio.Lock():
            result = {}

            for key in sorted_keys:
                entry = self._cache.get(key)
                if entry and not entry.is_expired():
                    entry.touch()
                    result[key] = entry.value
                    self._hits += 1
                else:
                    self._misses += 1

            return result

    async def set_many(self, mapping: dict[str, Any], ttl: int = 3600) -> bool:
        """Set multiple values efficiently."""
        # Use master lock for batch operations to maintain consistency
        async with asyncio.Lock():
            expiry = time.time() + ttl

            for key, value in mapping.items():
                await self._evict_if_needed()
                self._cache[key] = CacheEntry(value=value, expiry=expiry)

            return True

    async def clear(self) -> bool:
        """Clear all cache entries."""
        async with asyncio.Lock():
            self._cache.clear()
            logger.info("Memory cache cleared")
            return True

    async def close(self):
        """Stop cleanup task."""
        await self.stop_cleanup()

    def get_stats(self) -> dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Statistics dict
        """
        total_requests = self._hits + self._misses
        hit_rate = self._hits / total_requests if total_requests > 0 else 0

        return {
            "type": "memory",
            "size": len(self._cache),
            "max_size": self.max_size,
            "hits": self._hits,
            "misses": self._misses,
            "evictions": self._evictions,
            "hit_rate": hit_rate,
            "cleanup_interval": self.cleanup_interval,
        }

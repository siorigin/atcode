# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from abc import ABC, abstractmethod
from typing import Any


class CacheInterface(ABC):
    """
    Abstract base class for cache backends.

    All cache implementations must inherit from this class
    and implement all abstract methods.
    """

    @abstractmethod
    async def get(self, key: str) -> Any | None:
        """
        Get value from cache.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found/expired
        """
        pass

    @abstractmethod
    async def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        """
        Set value in cache with TTL.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time to live in seconds (default: 1 hour)

        Returns:
            True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """
        Delete value from cache.

        Args:
            key: Cache key

        Returns:
            True if deleted, False if key didn't exist
        """
        pass

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """
        Check if key exists in cache.

        Args:
            key: Cache key

        Returns:
            True if key exists and not expired
        """
        pass

    async def get_many(self, keys: list[str]) -> dict[str, Any]:
        """Get multiple values from cache.

        Args:
            keys: List of cache keys

        Returns:
            Dict mapping keys to values (missing keys excluded)
        """
        result: dict[str, Any] = {}
        for key in keys:
            value = await self.get(key)
            if value is not None:
                result[key] = value
        return result

    async def set_many(self, mapping: dict[str, Any], ttl: int = 3600) -> bool:
        """
        Set multiple values in cache.

        Args:
            mapping: Dict of key-value pairs
            ttl: Time to live in seconds

        Returns:
            True if all successful
        """
        success = True
        for key, value in mapping.items():
            if not await self.set(key, value, ttl):
                success = False
        return success

    async def delete_many(self, keys: list[str]) -> int:
        """
        Delete multiple keys from cache.

        Args:
            keys: List of cache keys

        Returns:
            Number of keys deleted
        """
        count = 0
        for key in keys:
            if await self.delete(key):
                count += 1
        return count

    async def clear(self) -> bool:
        """
        Clear all cache entries.

        Returns:
            True if successful
        """
        # Default implementation - subclasses should override for efficiency
        return True

    async def close(self) -> None:
        """Close cache connection (if applicable)."""


class CacheError(Exception):
    """Base exception for cache errors."""


class CacheConnectionError(CacheError):
    """Raised when cache connection fails."""


class SerializationError(CacheError):
    """Raised when value serialization fails."""

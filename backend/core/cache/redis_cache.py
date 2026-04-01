# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import json
import pickle
from typing import Any

from loguru import logger

from .base import CacheConnectionError, CacheInterface, SerializationError


class RedisCache(CacheInterface):
    """
    Redis-based cache implementation.

    Features:
    - Distributed caching across processes/servers
    - TTL-based expiration (handled by Redis)
    - Connection pooling
    - Automatic reconnection
    """

    def __init__(
        self,
        url: str = "redis://localhost:6379/0",
        prefix: str = "atcode:",
        serializer: str = "pickle",
    ):
        """
        Initialize Redis cache.

        Args:
            url: Redis connection URL
            prefix: Key prefix for namespacing
            serializer: Serialization method ("pickle" or "json")
        """
        self.url = url
        self.prefix = prefix
        self.serializer = serializer

        self._redis = None
        self._connected = False

        # Statistics
        self._hits = 0
        self._misses = 0
        self._errors = 0

    async def _ensure_connected(self) -> None:
        """Ensure Redis connection is established."""
        if self._redis is None:
            try:
                import redis.asyncio as redis

                self._redis = redis.from_url(
                    self.url,
                    encoding="utf-8",
                    decode_responses=False,  # We handle encoding ourselves
                )
                # Test connection
                await self._redis.ping()
                self._connected = True
                logger.info(f"Connected to Redis: {self.url}")
            except ImportError:
                raise CacheConnectionError(
                    "redis package not installed. Install with: pip install redis"
                )
            except Exception as e:
                self._redis = None
                self._connected = False
                raise CacheConnectionError(f"Failed to connect to Redis: {e}")

    def _make_key(self, key: str) -> str:
        """Add prefix to key."""
        return f"{self.prefix}{key}"

    def _serialize(self, value: Any) -> bytes:
        """Serialize value for storage."""
        try:
            if self.serializer == "json":
                return json.dumps(value).encode("utf-8")
            else:  # pickle
                return pickle.dumps(value)
        except Exception as e:
            raise SerializationError(f"Failed to serialize value: {e}")

    def _deserialize(self, data: bytes) -> Any:
        """Deserialize value from storage."""
        try:
            if self.serializer == "json":
                return json.loads(data.decode("utf-8"))
            else:  # pickle
                return pickle.loads(data)
        except Exception as e:
            raise SerializationError(f"Failed to deserialize value: {e}")

    async def get(self, key: str) -> Any | None:
        """Get value from Redis."""
        try:
            await self._ensure_connected()

            data = await self._redis.get(self._make_key(key))

            if data is None:
                self._misses += 1
                return None

            self._hits += 1
            return self._deserialize(data)

        except CacheConnectionError:
            raise
        except Exception as e:
            self._errors += 1
            logger.error(f"Redis get error: {e}")
            return None

    async def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        """Set value in Redis with TTL."""
        try:
            await self._ensure_connected()

            data = self._serialize(value)
            result = await self._redis.setex(self._make_key(key), ttl, data)
            return bool(result)

        except CacheConnectionError:
            raise
        except Exception as e:
            self._errors += 1
            logger.error(f"Redis set error: {e}")
            return False

    async def delete(self, key: str) -> bool:
        """Delete value from Redis."""
        try:
            await self._ensure_connected()

            result = await self._redis.delete(self._make_key(key))
            return result > 0

        except CacheConnectionError:
            raise
        except Exception as e:
            self._errors += 1
            logger.error(f"Redis delete error: {e}")
            return False

    async def exists(self, key: str) -> bool:
        """Check if key exists in Redis."""
        try:
            await self._ensure_connected()

            result = await self._redis.exists(self._make_key(key))
            return result > 0

        except CacheConnectionError:
            raise
        except Exception as e:
            self._errors += 1
            logger.error(f"Redis exists error: {e}")
            return False

    async def get_many(self, keys: list[str]) -> dict[str, Any]:
        """Get multiple values efficiently using MGET."""
        try:
            await self._ensure_connected()

            if not keys:
                return {}

            prefixed_keys = [self._make_key(k) for k in keys]
            values = await self._redis.mget(prefixed_keys)

            result = {}
            for key, data in zip(keys, values, strict=True):
                if data is not None:
                    result[key] = self._deserialize(data)
                    self._hits += 1
                else:
                    self._misses += 1

            return result

        except CacheConnectionError:
            raise
        except Exception as e:
            self._errors += 1
            logger.error(f"Redis mget error: {e}")
            return {}

    async def set_many(self, mapping: dict[str, Any], ttl: int = 3600) -> bool:
        """Set multiple values efficiently using pipeline."""
        try:
            await self._ensure_connected()

            if not mapping:
                return True

            pipe = self._redis.pipeline()

            for key, value in mapping.items():
                data = self._serialize(value)
                pipe.setex(self._make_key(key), ttl, data)

            await pipe.execute()
            return True

        except CacheConnectionError:
            raise
        except Exception as e:
            self._errors += 1
            logger.error(f"Redis mset error: {e}")
            return False

    async def delete_many(self, keys: list[str]) -> int:
        """Delete multiple keys efficiently."""
        try:
            await self._ensure_connected()

            if not keys:
                return 0

            prefixed_keys = [self._make_key(k) for k in keys]
            result = await self._redis.delete(*prefixed_keys)
            return result

        except CacheConnectionError:
            raise
        except Exception as e:
            self._errors += 1
            logger.error(f"Redis delete_many error: {e}")
            return 0

    async def clear(self) -> bool:
        """Clear all keys with our prefix."""
        try:
            await self._ensure_connected()

            # Find all keys with our prefix
            pattern = f"{self.prefix}*"
            cursor = 0
            deleted = 0

            while True:
                cursor, keys = await self._redis.scan(
                    cursor=cursor, match=pattern, count=100
                )

                if keys:
                    deleted += await self._redis.delete(*keys)

                if cursor == 0:
                    break

            logger.info(f"Redis cache cleared ({deleted} keys)")
            return True

        except CacheConnectionError:
            raise
        except Exception as e:
            self._errors += 1
            logger.error(f"Redis clear error: {e}")
            return False

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            self._redis = None
            self._connected = False
            logger.info("Redis connection closed")

    def get_stats(self) -> dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Statistics dict
        """
        total_requests = self._hits + self._misses
        hit_rate = self._hits / total_requests if total_requests > 0 else 0

        return {
            "type": "redis",
            "url": self.url.split("@")[-1]
            if "@" in self.url
            else self.url,  # Hide password
            "prefix": self.prefix,
            "connected": self._connected,
            "hits": self._hits,
            "misses": self._misses,
            "errors": self._errors,
            "hit_rate": hit_rate,
            "serializer": self.serializer,
        }

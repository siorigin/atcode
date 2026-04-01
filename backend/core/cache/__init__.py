# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from .base import CacheConnectionError, CacheError, CacheInterface, SerializationError
from .memory_cache import MemoryCache

__all__ = [
    "CacheConnectionError",
    "CacheError",
    "CacheInterface",
    "MemoryCache",
    "SerializationError",
]

# Conditional import for Redis
try:
    from .redis_cache import RedisCache  # noqa: F401

    __all__.append("RedisCache")
except ImportError:
    pass  # Redis not available

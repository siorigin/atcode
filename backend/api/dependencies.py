# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
from functools import lru_cache
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from api.orchestrator_pool import OrchestratorPool
    from core.cache.base import CacheInterface
    from core.storage.base import StorageInterface


# Global instances (initialized lazily)
_orchestrator_pool: OrchestratorPool | None = None
_storage: StorageInterface | None = None
_cache: CacheInterface | None = None


def get_orchestrator_pool() -> OrchestratorPool | None:
    """Get the global orchestrator pool instance.

    Returns:
        OrchestratorPool instance or ``None`` if not initialized.
    """
    return _orchestrator_pool


def get_storage() -> StorageInterface | None:
    """Get the global storage backend instance.

    Returns:
        StorageInterface instance or ``None`` if not initialized.
    """
    return _storage


def get_cache() -> CacheInterface | None:
    """Get the global cache backend instance.

    Returns:
        CacheInterface instance or ``None`` if not initialized.
    """
    return _cache


async def initialize_dependencies(config: dict | None = None) -> None:
    """Initialize all dependencies.

    Args:
        config: Optional configuration dict.
    """
    global _orchestrator_pool, _storage, _cache

    config = config or {}

    # Initialize storage
    storage_backend = config.get("storage_backend", "file")
    storage_path = config.get("storage_path", None)

    # Resolve storage path - use centralized config
    if not storage_path or storage_path == "./wiki_chat":
        from core.config import get_wiki_chat_dir

        storage_path = str(get_wiki_chat_dir())

    from core.storage.file_storage import FileStorage

    if storage_backend == "file":
        _storage = FileStorage(base_path=storage_path)
        logger.info(f"Initialized file storage: {storage_path}")
    else:
        _storage = FileStorage(base_path=storage_path)
        logger.warning(
            f"Unknown storage backend '{storage_backend}', using file storage"
        )

    # Initialize cache
    cache_backend = config.get("cache_backend", "memory")

    if cache_backend == "redis":
        try:
            from core.cache.redis_cache import RedisCache

            redis_url = config.get("redis_url", "redis://localhost:6379/0")
            _cache = RedisCache(url=redis_url)
            # Test connection
            await _cache.set("_test", "ok", ttl=1)
            await _cache.delete("_test")
            logger.info(f"Initialized Redis cache: {redis_url}")
        except Exception as e:
            logger.warning(
                f"Failed to initialize Redis cache: {e}, falling back to memory cache"
            )
            from core.cache.memory_cache import MemoryCache

            _cache = MemoryCache()
    else:
        from core.cache.memory_cache import MemoryCache

        _cache = MemoryCache()
        logger.info("Initialized memory cache")

    # Initialize orchestrator pool (universal orchestrator architecture)
    from api.orchestrator_pool import OrchestratorPool

    pool_config = {
        "max_size": config.get("orchestrator_pool_size", 5),
        "idle_timeout": config.get("orchestrator_idle_timeout", 3600),
        "cleanup_interval": config.get("orchestrator_cleanup_interval", 300),
        "storage": _storage,
        "cache": _cache,
    }

    _orchestrator_pool = OrchestratorPool(**pool_config)
    await _orchestrator_pool.initialize()
    logger.info("Initialized universal orchestrator pool (single global orchestrator)")


async def cleanup_dependencies() -> None:
    """Cleanup all dependencies."""
    global _orchestrator_pool, _storage, _cache

    if _orchestrator_pool:
        await _orchestrator_pool.cleanup()
        _orchestrator_pool = None
        logger.info("Cleaned up orchestrator pool")

    if _cache:
        try:
            if hasattr(_cache, "close"):
                await _cache.close()
        except Exception as e:
            logger.warning(f"Error closing cache: {e}")
        _cache = None
        logger.info("Cleaned up cache")

    _storage = None
    logger.info("Cleaned up storage")


@lru_cache
def get_config() -> dict:
    """Get application configuration.

    Returns:
        Configuration dict with values from environment variables.
    """
    return {
        # API settings
        "api_mode": os.getenv("API_MODE", "fastapi"),
        "api_host": os.getenv("API_HOST", "0.0.0.0"),
        "api_port": int(os.getenv("API_PORT", "8000")),
        # Storage settings
        "storage_backend": os.getenv("STORAGE_BACKEND", "file"),
        "storage_path": os.getenv("FILE_STORAGE_PATH", None),
        # Cache settings
        "cache_backend": os.getenv("CACHE_BACKEND", "memory"),
        "redis_url": os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        # Orchestrator pool settings
        "orchestrator_pool_size": int(os.getenv("ORCHESTRATOR_POOL_SIZE", "5")),
        "orchestrator_idle_timeout": int(
            os.getenv("ORCHESTRATOR_IDLE_TIMEOUT", "3600")
        ),
        "orchestrator_cleanup_interval": int(
            os.getenv("ORCHESTRATOR_CLEANUP_INTERVAL", "300")
        ),
        # Logging
        "log_level": os.getenv("LOG_LEVEL", "INFO"),
        "log_dir": os.getenv("LOG_DIR", "./data/logs"),
        "log_json": os.getenv("LOG_JSON", "false").lower() == "true",
    }

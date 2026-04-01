# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import os
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from agent.orchestrators.chat import ChatOrchestrator
    from core.cache.base import CacheInterface
    from core.storage.base import StorageInterface
    from graph.updater import MemgraphIngestor


class OrchestratorPool:
    """
    Manages a SINGLE global ChatOrchestrator instance.

    Unlike the previous architecture where each repo had its own orchestrator,
    this pool maintains ONE universal orchestrator that serves ALL repos.
    The repo_path and ingestor are passed to stream_chat() as parameters.

    Benefits:
    - Memory: ~2GB fixed (model loaded once)
    - Latency: <0.5s for any repo (no cold start)
    - Scalability: Unlimited repos, same memory footprint
    - Concurrency: Connection pool for parallel graph queries
    """

    # Calculate default connection pool size based on worker count
    # Formula: max(50, workers * 10) to handle 5+ workers × 4 threads each
    @staticmethod
    def _calculate_default_pool_size() -> int:
        """Calculate default connection pool size based on worker count."""
        worker_count = int(os.environ.get("API_WORKERS", "1"))
        # Each worker may have multiple threads (default 4 for graph builds)
        # So we need workers * 10 connections minimum, with a floor of 50
        pool_size = max(50, worker_count * 10)
        return pool_size

    def __init__(
        self,
        max_size: int = 5,  # Kept for backward compatibility, not used
        idle_timeout: int = 3600,
        cleanup_interval: int = 300,
        storage: StorageInterface | None = None,
        cache: CacheInterface | None = None,
        connection_pool_size: int | None = None,  # Auto-calculated if None
    ):
        """
        Initialize orchestrator pool.

        Args:
            max_size: (Deprecated) Not used in new architecture
            idle_timeout: Seconds before cleanup (for session cache)
            cleanup_interval: Seconds between cleanup runs
            storage: Storage backend for sessions
            cache: Cache backend for session state
            connection_pool_size: Max connections in Memgraph pool (auto-scales with workers if None)
        """
        self.idle_timeout = idle_timeout
        self.cleanup_interval = cleanup_interval
        self.storage = storage
        self.cache = cache

        # Auto-calculate connection pool size based on workers if not specified
        if connection_pool_size is None:
            connection_pool_size = self._calculate_default_pool_size()

        self.connection_pool_size = connection_pool_size

        # Single global orchestrator instance
        self._global_orchestrator: ChatOrchestrator | None = None

        # Connection pool for Memgraph (replaces single ingestor)
        self._connection_pool = None  # MemgraphConnectionPool instance

        # Single global Memgraph ingestor (uses connection pool internally)
        self._global_ingestor: MemgraphIngestor | None = None

        # LRU cache for project paths (repo_name -> (path, timestamp))
        self._repo_path_cache: OrderedDict[str, tuple[Path, float]] = OrderedDict()
        self._repo_path_cache_ttl = 60  # seconds
        self._repo_path_cache_lock = asyncio.Lock()

        self.lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task | None = None
        self._initialized = False

        # Statistics
        self._total_requests = 0
        self._created_at: datetime | None = None

    async def initialize(self):
        """Initialize global orchestrator and ingestor with connection pool."""
        if self._initialized:
            return

        async with self.lock:
            if self._initialized:
                return

            logger.info("Initializing global orchestrator pool...")

            # Import required modules
            from agent.orchestrators.chat import create_chat_orchestrator
            from core.config import settings
            from graph.service import get_connection_pool
            from graph.updater import MemgraphIngestor

            # Create global Memgraph connection pool for read operations
            read_host, read_port = settings.get_read_connection()

            # Use env var override if specified, otherwise use auto-calculated size
            env_pool_size = os.environ.get("MEMGRAPH_POOL_SIZE")
            if env_pool_size:
                pool_size = int(env_pool_size)
                logger.info(
                    f"Connection pool size from MEMGRAPH_POOL_SIZE env var: {pool_size}"
                )
            else:
                pool_size = self.connection_pool_size
                worker_count = int(os.environ.get("API_WORKERS", "1"))
                logger.info(
                    f"Connection pool size auto-calculated: {pool_size} "
                    f"(based on {worker_count} workers, formula: max(50, workers * 10))"
                )

            logger.info(
                f"Creating Memgraph connection pool (size={pool_size}, {settings.detected_role} mode)..."
            )
            self._connection_pool = get_connection_pool(
                host=read_host,
                port=read_port,
                max_size=pool_size,
            )
            logger.info(
                f"Connection pool created: {read_host}:{read_port} (max {pool_size} connections)"
            )

            # Create global Memgraph ingestor for read operations
            logger.info("Creating global Memgraph ingestor...")
            self._global_ingestor = MemgraphIngestor(
                host=read_host,
                port=read_port,
                batch_size=settings.resolve_batch_size(None),
            )
            # Connect the ingestor
            self._global_ingestor.__enter__()
            logger.info(f"Global ingestor connected: {read_host}:{read_port}")

            # Create global orchestrator (model loaded here, once)
            logger.info("Creating global Chat Orchestrator...")
            self._global_orchestrator = create_chat_orchestrator(mode="default")
            logger.info("Global Chat Orchestrator created")

            # Start background cleanup task
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            self._created_at = datetime.utcnow()
            self._initialized = True

            logger.info(
                "Global orchestrator pool initialized (model loaded once, serves all repos)"
            )

    def get_orchestrator(self) -> ChatOrchestrator | None:
        """
        Get the global orchestrator instance.

        Returns:
            ChatOrchestrator instance (universal, not repo-bound)

        Raises:
            RuntimeError: If pool not initialized
        """
        if not self._initialized or not self._global_orchestrator:
            raise RuntimeError(
                "Orchestrator pool not initialized. Call initialize() first."
            )

        self._total_requests += 1
        return self._global_orchestrator

    def get_ingestor(self) -> MemgraphIngestor | None:
        """
        Get the global Memgraph ingestor instance.

        Returns:
            MemgraphIngestor instance

        Raises:
            RuntimeError: If pool not initialized
        """
        if not self._initialized or not self._global_ingestor:
            raise RuntimeError(
                "Orchestrator pool not initialized. Call initialize() first."
            )

        return self._global_ingestor

    def get_repo_path(self, repo_name: str) -> Path:
        """
        Get the repository path for a given repo name.

        Uses LRU cache (60-second TTL) to avoid database lookups.
        Falls back to wiki_repos directory if cache miss.
        For ``__global__`` mode, returns the wiki_repos root directory.

        Args:
            repo_name: Repository name

        Returns:
            Path to the repository
        """
        # Global mode — no specific repo
        if repo_name == "__global__":
            from core.config import get_wiki_repos_dir

            return get_wiki_repos_dir()

        now = datetime.utcnow().timestamp()

        # Check cache (non-async for performance)
        if repo_name in self._repo_path_cache:
            path, timestamp = self._repo_path_cache[repo_name]
            if now - timestamp < self._repo_path_cache_ttl:
                # Cache hit, move to end (LRU)
                self._repo_path_cache.move_to_end(repo_name)
                return path

        # Cache miss or expired - get from database
        if self._initialized and self._global_ingestor:
            try:
                project_path = self._global_ingestor.get_project_path(repo_name)
                if project_path:
                    path = Path(project_path)
                    # Check if path exists, if not try new data directory
                    if not path.exists():
                        from core.config import get_wiki_repos_dir

                        new_path = get_wiki_repos_dir() / repo_name
                        if new_path.exists():
                            logger.info(
                                f"Old path {path} not found, using new path {new_path}"
                            )
                            # Cache the new path
                            self._repo_path_cache[repo_name] = (new_path, now)
                            # Keep cache bounded to 100 entries
                            if len(self._repo_path_cache) > 100:
                                self._repo_path_cache.popitem(last=False)
                            return new_path

                    # Cache the path
                    self._repo_path_cache[repo_name] = (path, now)
                    # Keep cache bounded to 100 entries
                    if len(self._repo_path_cache) > 100:
                        self._repo_path_cache.popitem(last=False)
                    return path
            except Exception as e:
                logger.debug(f"Failed to get project path from database: {e}")

        # Fallback: use wiki_repos directory from centralized config
        from core.config import get_wiki_repos_dir

        path = get_wiki_repos_dir() / repo_name

        # Cache the fallback path
        self._repo_path_cache[repo_name] = (path, now)
        # Keep cache bounded to 100 entries
        if len(self._repo_path_cache) > 100:
            self._repo_path_cache.popitem(last=False)

        return path

    # Legacy method for backward compatibility
    async def get_or_create(
        self, repo_name: str, mode: str = "default", repo_path: str | None = None
    ) -> ChatOrchestrator | None:
        """
        Legacy method - returns global orchestrator.

        Note: mode and repo_path parameters are ignored.
        The new architecture uses a single global orchestrator.
        Repo info should be passed to stream_chat() instead.

        Args:
            repo_name: Repository name (ignored in new architecture)
            mode: Chat mode (ignored in new architecture)
            repo_path: Repository path (ignored in new architecture)

        Returns:
            Global ChatOrchestrator instance
        """
        logger.debug(
            f"get_or_create called for {repo_name} - "
            f"returning global orchestrator (new architecture)"
        )
        return self.get_orchestrator()

    async def _cleanup_loop(self):
        """Background task for periodic maintenance."""
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval)
                await self._cleanup_sessions()
            except asyncio.CancelledError:
                logger.debug("Cleanup task cancelled")
                break
            except Exception as e:
                logger.error(f"Cleanup error: {e}")

    async def _cleanup_sessions(self):
        """Cleanup stale sessions from the orchestrator's session cache."""
        async with self.lock:
            if self._global_orchestrator:
                # Cleanup stale sessions (delegate to orchestrator)
                self._global_orchestrator._sessions.cleanup_stale(self.idle_timeout)

    async def cleanup(self):
        """Cleanup all resources including connection pool."""
        # Cancel cleanup task
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        async with self.lock:
            # Clear repo path cache
            self._repo_path_cache.clear()

            # Close global ingestor connection
            if self._global_ingestor:
                try:
                    self._global_ingestor.__exit__(None, None, None)
                    logger.info("Closed global ingestor connection")
                except Exception as e:
                    logger.warning(f"Error closing global ingestor: {e}")
                self._global_ingestor = None

            # Close connection pool
            if self._connection_pool:
                try:
                    self._connection_pool.close()
                    logger.info("Closed Memgraph connection pool")
                except Exception as e:
                    logger.warning(f"Error closing connection pool: {e}")
                self._connection_pool = None

            # Clear orchestrator references
            self._global_orchestrator = None
            self._initialized = False

        logger.info("Orchestrator pool cleaned up")

    def get_connection_pool(self):
        """
        Get the Memgraph connection pool.

        Returns:
            MemgraphConnectionPool instance or None
        """
        return self._connection_pool

    def get_stats(self) -> dict[str, Any]:
        """
        Get pool statistics including connection pool stats.

        Returns:
            Statistics dict
        """
        # Get session stats from orchestrator
        session_count = 0
        tool_cache_count = 0
        workflow_cache_count = 0

        if self._global_orchestrator:
            session_count = len(self._global_orchestrator._sessions._cache)
            tool_cache_count = len(self._global_orchestrator._tool_cache)
            workflow_cache_count = len(self._global_orchestrator._workflow_cache)

        # Get connection pool stats
        pool_stats = None
        if self._connection_pool:
            pool_stats = self._connection_pool.stats()

        return {
            "architecture": "universal_orchestrator",
            "initialized": self._initialized,
            "created_at": self._created_at.isoformat() if self._created_at else None,
            "total_requests": self._total_requests,
            "session_count": session_count,
            "tool_cache_count": tool_cache_count,
            "workflow_cache_count": workflow_cache_count,
            "ingestor_connected": self._global_ingestor is not None,
            "connection_pool": pool_stats,
        }

    def __len__(self) -> int:
        """Get number of cached items (sessions + tools + workflows)."""
        if not self._global_orchestrator:
            return 0
        return (
            len(self._global_orchestrator._sessions._cache)
            + len(self._global_orchestrator._tool_cache)
            + len(self._global_orchestrator._workflow_cache)
        )

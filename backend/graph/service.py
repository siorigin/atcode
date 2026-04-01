# Copyright 2025 Vitali Avagyan.
# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: MIT AND Apache-2.0
#
# This file is derived from code-graph-rag (MIT License).
# Modifications by SiOrigin Co. Ltd. are licensed under Apache-2.0.
# See the LICENSE file in the root directory for details.

import threading
from collections import defaultdict
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Lock
from time import sleep, time
from typing import Any

import mgclient
from core.config import settings
from loguru import logger

# Try to use xxhash for faster hashing, fallback to md5 if not available
try:
    import xxhash

    def HASH_FUNC(s: str) -> str:
        """Compute hash using xxhash (fast)."""
        return xxhash.xxh64(s).hexdigest()

except ImportError:
    from hashlib import md5

    def HASH_FUNC(s: str) -> str:
        """Compute hash using md5 (fallback)."""
        return md5(s.encode()).hexdigest()


class MemgraphConnectionPool:
    """
    Thread-safe connection pool for Memgraph.

    Reuses connections to avoid the overhead of establishing new connections
    for each query. Connections are health-checked before being returned.
    """

    def __init__(
        self, host: str, port: int, max_size: int = 10, idle_timeout: float = 300.0
    ):
        """
        Initialize the connection pool.

        Args:
            host: Memgraph host
            port: Memgraph port
            max_size: Maximum number of connections in the pool
            idle_timeout: Close connections idle for longer than this (seconds)
        """
        self._host = host
        self._port = port
        self._max_size = max_size
        self._idle_timeout = idle_timeout
        self._pool: Queue = Queue(maxsize=max_size)
        self._created_count = 0
        self._lock = Lock()
        self._closed = False

        # Pre-create a few connections for warm start
        initial_size = min(3, max_size)
        for _ in range(initial_size):
            try:
                conn = self._new_connection()
                self._pool.put(conn, block=False)
            except Exception as e:
                logger.warning(f"Failed to create initial connection: {e}")

    def _new_connection(self) -> mgclient.Connection:
        """Create a new Memgraph connection."""
        with self._lock:
            if self._closed:
                raise RuntimeError("Connection pool is closed")
            self._created_count += 1

        conn = mgclient.connect(host=self._host, port=self._port)
        conn.autocommit = True
        return conn

    def _is_healthy(self, conn: mgclient.Connection) -> bool:
        """Check if a connection is healthy."""
        try:
            cursor = conn.cursor()
            cursor.execute("RETURN 1 AS health")
            cursor.fetchall()
            cursor.close()
            return True
        except Exception:
            return False

    def get(self, timeout: float = 5.0) -> mgclient.Connection:
        """
        Get a connection from the pool.

        Args:
            timeout: Maximum time to wait for a connection (seconds)

        Returns:
            A healthy Memgraph connection

        Raises:
            RuntimeError: If pool is closed
            Empty: If timeout waiting for connection
        """
        if self._closed:
            raise RuntimeError("Connection pool is closed")

        try:
            # Try to get a connection from the pool
            conn = self._pool.get(timeout=timeout)

            # Health check the connection
            if not self._is_healthy(conn):
                logger.debug("Unhealthy connection from pool, creating new one")
                try:
                    conn.close()
                except Exception:
                    pass
                conn = self._new_connection()

            return conn

        except Empty:
            # Pool is empty, create a new connection if under limit
            with self._lock:
                if self._created_count < self._max_size:
                    logger.debug("Pool empty, creating new connection")
                    return self._new_connection()

            # At max capacity, wait for a connection
            logger.debug("Pool at capacity, waiting for available connection")
            conn = self._pool.get(timeout=timeout)

            if not self._is_healthy(conn):
                try:
                    conn.close()
                except Exception:
                    pass
                conn = self._new_connection()

            return conn

    def put(self, conn: mgclient.Connection) -> None:
        """
        Return a connection to the pool.

        If the pool is closed, the connection is closed immediately.
        """
        if self._closed:
            try:
                conn.close()
            except Exception:
                pass
            return

        try:
            # Non-blocking put - if pool is full, close the connection
            self._pool.put(conn, block=False)
        except Full:
            # Pool is full, close the excess connection
            logger.debug("Pool full, closing excess connection")
            try:
                conn.close()
            except Exception:
                pass

    def close(self) -> None:
        """Close all connections in the pool and shutdown."""
        with self._lock:
            if self._closed:
                return
            self._closed = True

        # Close all connections in the pool
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                conn.close()
            except Exception:
                pass

        logger.info(
            f"Connection pool closed (created {self._created_count} connections total)"
        )

    def stats(self) -> dict[str, Any]:
        """Get pool statistics."""
        return {
            "host": self._host,
            "port": self._port,
            "max_size": self._max_size,
            "current_size": self._pool.qsize(),
            "created_count": self._created_count,
            "closed": self._closed,
        }


# Global connection pools
_pools: dict[tuple[str, int], MemgraphConnectionPool] = {}
_pools_lock = Lock()


def get_connection_pool(
    host: str, port: int, max_size: int = 20
) -> MemgraphConnectionPool:
    """
    Get or create a connection pool for the given host:port.

    Args:
        host: Memgraph host
        port: Memgraph port
        max_size: Maximum pool size

    Returns:
        ConnectionPool instance
    """
    key = (host, port)

    with _pools_lock:
        if key not in _pools:
            _pools[key] = MemgraphConnectionPool(host, port, max_size=max_size)
            logger.info(f"Created new connection pool for {host}:{port}")
        return _pools[key]


def close_all_pools() -> None:
    """Close all connection pools (for shutdown)."""
    with _pools_lock:
        for pool in _pools.values():
            pool.close()
        _pools.clear()


class QueryCache:
    """
    Query cache for graph data.

    Design: Graph data is essentially static after creation/rebuild.
    We use long TTLs (5 minutes default) since data only changes when
    users explicitly trigger graph operations (create/rebuild/clean).
    """

    def __init__(self, max_size: int = 1000, default_ttl: float = 300.0):
        """
        Initialize query cache.

        Args:
            max_size: Maximum number of cached entries
            default_ttl: Default time-to-live in seconds
        """
        self._cache: dict[
            str, tuple[Any, float, float]
        ] = {}  # key -> (value, expire_time, ttl)
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._lock = Lock()
        self._hits = 0
        self._misses = 0

    def _make_key(self, query: str, params: dict | None) -> str:
        """Create a cache key from query and params using fast hashing."""
        param_str = str(sorted(params.items())) if params else ""
        return HASH_FUNC(f"{query}:{param_str}")

    def get(self, query: str, params: dict | None = None) -> tuple[bool, Any]:
        """
        Get cached result if available and not expired.

        Returns:
            Tuple of (hit: bool, value: Any)
        """
        key = self._make_key(query, params)
        with self._lock:
            if key in self._cache:
                value, expire_time, _ = self._cache[key]
                if time() < expire_time:
                    self._hits += 1
                    return True, value
                else:
                    # Expired, remove it
                    del self._cache[key]
            self._misses += 1
            return False, None

    def set(
        self, query: str, params: dict | None, value: Any, ttl: float | None = None
    ) -> None:
        """Cache a query result."""
        key = self._make_key(query, params)
        ttl = ttl if ttl is not None else self._default_ttl
        expire_time = time() + ttl

        with self._lock:
            # Evict oldest entries if cache is full
            if len(self._cache) >= self._max_size:
                # Remove 10% oldest entries
                to_remove = max(1, self._max_size // 10)
                sorted_keys = sorted(
                    self._cache.keys(), key=lambda k: self._cache[k][1]
                )
                for k in sorted_keys[:to_remove]:
                    del self._cache[k]

            self._cache[key] = (value, expire_time, ttl)

    def invalidate(self, pattern: str | None = None) -> int:
        """
        Invalidate cache entries.

        Args:
            pattern: If provided, only invalidate entries where query contains pattern.
                    If None, clear entire cache.

        Returns:
            Number of entries invalidated
        """
        with self._lock:
            if pattern is None:
                count = len(self._cache)
                self._cache.clear()
                return count
            else:
                # For pattern-based invalidation, we'd need to store original queries
                # For now, just clear all (conservative approach)
                count = len(self._cache)
                self._cache.clear()
                return count

    def stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = self._hits / total if total > 0 else 0.0
            return {
                "size": len(self._cache),
                "max_size": self._max_size,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": f"{hit_rate:.2%}",
            }


class MemgraphIngestor:
    """Handles all communication and query execution with the Memgraph database."""

    _QUERY_CACHE_MAX_SIZE = 1000  # Maximum entries in the query result cache
    _QUERY_CACHE_TTL_SECS = (
        300.0  # Query cache TTL (5 minutes); graph is static between ops
    )
    _HEALTH_CHECK_INTERVAL_SECS = 30.0  # Seconds between connection health checks
    _CLEANUP_BATCH_SIZE = 1000  # Batch size for node/relationship deletion queries

    def __init__(
        self,
        host: str,
        port: int,
        batch_size: int | None = None,
        write_delay_ms: int | None = None,
    ):
        """Initialize the Memgraph ingestor with connection and batching parameters.

        Args:
            host: Memgraph server hostname.
            port: Memgraph server port.
            batch_size: Number of items per Cypher UNWIND batch (default from config).
            write_delay_ms: Milliseconds to sleep between batches for throttling (default from config).
        """
        self._host = host
        self._port = port
        # Use configured batch_size if not specified
        self.batch_size = (
            batch_size if batch_size is not None else settings.MEMGRAPH_BATCH_SIZE
        )
        if self.batch_size < 1:
            raise ValueError("batch_size must be a positive integer")
        self.full_build_batch_size = settings.MEMGRAPH_FULL_BUILD_BATCH_SIZE
        self.write_delay_ms = (
            write_delay_ms
            if write_delay_ms is not None
            else settings.MEMGRAPH_WRITE_DELAY_MS
        )
        self.conn: mgclient.Connection | None = None
        self.node_buffer: list[tuple[str, dict[str, Any]]] = []
        self.relationship_buffer: list[tuple[tuple, str, tuple, dict | None]] = []
        # Thread-safety locks for parallel processing (Pass 3)
        self._node_buffer_lock = Lock()
        self._relationship_buffer_lock = Lock()
        self._flush_lock = Lock()  # Prevents concurrent flush operations (use Lock, not RLock, to detect reentrancy bugs)
        self._is_flushing = False  # Flag to track if a flush is in progress
        self.unique_constraints = {
            "Project": "name",
            "Folder": "qualified_name",  # Changed to qualified_name for proper project isolation
            "Class": "qualified_name",
            "Function": "qualified_name",
            "Method": "qualified_name",
            "Variable": "qualified_name",
            "File": "qualified_name",  # Changed to qualified_name for proper project isolation
            "ExternalPackage": "name",
        }

        # Query cache - long TTL since graph data is static between operations
        self._query_cache = QueryCache(
            max_size=self._QUERY_CACHE_MAX_SIZE,
            default_ttl=self._QUERY_CACHE_TTL_SECS,
        )

        # Connection health tracking
        self._last_health_check = 0.0
        self._health_check_interval = self._HEALTH_CHECK_INTERVAL_SECS
        self._connection_lock = Lock()

        # Background flusher state
        self._bg_flush_thread: threading.Thread | None = None
        self._bg_flush_stop = False
        self._bg_flush_interval = 0.1  # seconds between buffer checks (fast polling)
        self._bg_flush_min_batch = 20000  # accumulate larger batches before flushing (reduces flush count, larger UNWIND batches)
        self._bg_flush_max_wait = (
            10.0  # max seconds between flushes (prevents starvation)
        )

        # Full-build CREATE mode for nodes.
        # When True, all node flushes use CREATE instead of MERGE.
        # CREATE skips the existence check that MERGE performs, which is
        # significantly faster when the database was cleaned before a full build.
        # In-buffer dedup in _flush_nodes_impl + unique constraints catch duplicates.
        # If CREATE fails with "already exists", we fall back to MERGE for that batch.
        self._use_create_nodes = False

        # Shared node ID cache built by background flusher.
        # Maps (label, id_key, id_val) -> internal DB node ID.
        # When the bg flusher writes nodes with collect_node_ids=True,
        # it merges results here so flush_all() can skip the supplemental
        # cache lookup (expensive DB roundtrip).
        self._bg_node_id_cache: dict[tuple, int] = {}
        self._bg_node_id_cache_lock = Lock()

        # Deferred flush mode: when True, all proactive/automatic flushes are
        # suppressed.  Nodes and relationships accumulate entirely in memory
        # until flush_all() is called explicitly.  This eliminates bg flusher
        # overhead (polling, lock contention, per-batch DB round-trips) and
        # makes the CPU passes purely CPU-bound.
        self._deferred_flush = False

    def __enter__(self) -> "MemgraphIngestor":
        logger.info(f"Connecting to Memgraph at {self._host}:{self._port}...")
        self.conn = mgclient.connect(host=self._host, port=self._port)
        self.conn.autocommit = True
        self._last_health_check = time()
        logger.info("Successfully connected to Memgraph.")
        return self

    def __exit__(
        self, exc_type: type | None, exc_val: Exception | None, exc_tb: Any
    ) -> None:
        # Check if this is a cancellation exception
        is_cancelled = (
            exc_type is not None
            and exc_val is not None
            and "cancelled" in str(exc_val).lower()
        )

        if exc_type:
            if is_cancelled:
                logger.info(
                    f"Build cancelled: {exc_val}. Skipping flush to exit quickly."
                )
            else:
                logger.error(
                    f"An exception occurred: {exc_val}. Flushing remaining items...",
                    exc_info=True,
                )

        # Only flush if not cancelled - skip flush on cancellation for faster response
        if not is_cancelled:
            self.flush_all()

        if self.conn:
            self.conn.close()
            logger.info("\nDisconnected from Memgraph.")

    def _check_connection_health(self) -> bool:
        """Check if connection is healthy and reconnect if necessary."""
        current_time = time()
        if current_time - self._last_health_check < self._health_check_interval:
            return True

        with self._connection_lock:
            # Double-check after acquiring lock
            if current_time - self._last_health_check < self._health_check_interval:
                return True

            try:
                if self.conn:
                    cursor = self.conn.cursor()
                    cursor.execute("RETURN 1 AS health")
                    cursor.fetchall()
                    cursor.close()
                    self._last_health_check = time()
                    return True
            except Exception as e:
                logger.warning(
                    f"Connection health check failed: {e}, attempting reconnect..."
                )
                return self._reconnect()

        return False

    def execute_query(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        use_cache: bool = False,
        cache_ttl: float | None = None,
    ) -> list:
        """
        Public method to execute a Cypher query with optional caching.
        This is a wrapper around _execute_query for external use.

        Args:
            query: Cypher query string
            params: Query parameters
            use_cache: Whether to use query cache (only for read queries)
            cache_ttl: Custom TTL for this query (seconds)

        Returns:
            List of result dictionaries
        """
        return self._execute_query(query, params, use_cache, cache_ttl)

    def get_storage_mode(self) -> str:
        """Get current storage mode.

        Returns:
            Current storage mode: IN_MEMORY_TRANSACTIONAL, IN_MEMORY_ANALYTICAL, or ON_DISK_TRANSACTIONAL
        """
        try:
            result = self._execute_query("SHOW STORAGE INFO")
            for row in result:
                # Handle both quoted and unquoted field names
                storage_info = row.get("storage info") or row.get("storage_info")
                if storage_info in ("storage mode", "storage_mode"):
                    value = row.get("value", "UNKNOWN")
                    # Remove quotes if present
                    if isinstance(value, str):
                        value = value.strip('"')
                    return value
            return "UNKNOWN"
        except Exception as e:
            logger.warning(f"Failed to get storage mode: {e}")
            return "UNKNOWN"

    def set_storage_mode(self, mode: str) -> bool:
        """Set storage mode.

        Args:
            mode: Storage mode to set (IN_MEMORY_TRANSACTIONAL, IN_MEMORY_ANALYTICAL, or ON_DISK_TRANSACTIONAL)

        Returns:
            True if successful, False otherwise

        Note:
            IN_MEMORY_ANALYTICAL mode enables multi-threaded writes for much faster bulk imports,
            but does not support transactions, rollback, or WAL. Use it for batch imports only.

        Warning:
            Switching storage modes may terminate existing connections!
        """
        valid_modes = [
            "IN_MEMORY_TRANSACTIONAL",
            "IN_MEMORY_ANALYTICAL",
            "ON_DISK_TRANSACTIONAL",
        ]
        if mode not in valid_modes:
            logger.error(f"Invalid storage mode: {mode}. Must be one of {valid_modes}")
            return False

        try:
            logger.info(f"Attempting to set storage mode to {mode}...")
            self._execute_query(f"STORAGE MODE {mode}")
            logger.info(f"Storage mode successfully set to {mode}")
            return True
        except Exception as e:
            logger.error(f"Failed to set storage mode to {mode}: {e}")
            # Try to reconnect after mode switch failure
            logger.info("Attempting to reconnect after mode switch...")
            if self._reconnect():
                logger.info("Reconnected successfully after mode switch")
            return False

    def _reconnect(self) -> bool:
        """Attempt to reconnect to Memgraph."""
        max_retries = 3
        retry_delay = 1.0

        for attempt in range(max_retries):
            try:
                if self.conn:
                    try:
                        self.conn.close()
                    except Exception:
                        pass

                self.conn = mgclient.connect(host=self._host, port=self._port)
                self.conn.autocommit = True
                self._last_health_check = time()
                logger.info(
                    f"Successfully reconnected to Memgraph (attempt {attempt + 1})"
                )
                return True
            except Exception as e:
                logger.warning(f"Reconnect attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    sleep(retry_delay * (2**attempt))

        logger.error("Failed to reconnect to Memgraph after all retries")
        return False

    def _execute_query(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        use_cache: bool = False,
        cache_ttl: float | None = None,
    ) -> list:
        """
        Execute a Cypher query with optional caching.

        Args:
            query: Cypher query string
            params: Query parameters
            use_cache: Whether to use query cache (only for read queries)
            cache_ttl: Custom TTL for this query (seconds)

        Returns:
            List of result dictionaries
        """
        if not self.conn:
            raise ConnectionError("Not connected to Memgraph.")

        # Check cache first for read queries
        if use_cache:
            hit, cached_result = self._query_cache.get(query, params)
            if hit:
                return cached_result

        # Periodic health check (non-blocking for most calls)
        self._check_connection_health()

        params = params or {}
        cursor = None
        try:
            cursor = self.conn.cursor()
            cursor.execute(query, params)
            if not cursor.description:
                return []
            column_names = [desc.name for desc in cursor.description]
            result = [dict(zip(column_names, row)) for row in cursor.fetchall()]

            # Cache the result if caching is enabled
            if use_cache:
                self._query_cache.set(query, params, result, cache_ttl)

            return result
        except Exception as e:
            # Check if it's a connection error and try to reconnect
            error_str = str(e).lower()
            if any(
                kw in error_str
                for kw in ["connection", "socket", "closed", "bad session", "session"]
            ):
                logger.warning(
                    f"Connection error detected: {e}, attempting reconnect..."
                )
                if self._reconnect():
                    # Retry the query once after reconnection
                    try:
                        cursor = self.conn.cursor()
                        cursor.execute(query, params)
                        if not cursor.description:
                            return []
                        column_names = [desc.name for desc in cursor.description]
                        return [
                            dict(zip(column_names, row)) for row in cursor.fetchall()
                        ]
                    except Exception as retry_error:
                        logger.error(f"Query failed after reconnect: {retry_error}")
                        raise

            if "already exists" not in error_str and "constraint" not in error_str:
                logger.error(f"!!! Cypher Error: {e}")
                logger.error(f"    Query: {query}")
                logger.error(f"    Params: {params}")
            raise
        finally:
            if cursor:
                cursor.close()

    def _execute_batch(self, query: str, params_list: list[dict[str, Any]]) -> None:
        if not self.conn or not params_list:
            return
        cursor = None
        try:
            cursor = self.conn.cursor()
            batch_query = f"UNWIND $batch AS row\n{query}"
            cursor.execute(batch_query, {"batch": params_list})
        except Exception as e:
            error_msg = str(e).lower()
            # Silently ignore "already exists" errors (expected with CREATE)
            if "already exists" in error_msg:
                logger.debug("Skipping duplicate relationships (expected with CREATE)")
                return
            # Log other errors but don't crash the process
            logger.error(f"!!! Batch Cypher Error: {e}")
            logger.error(f"    Query: {query}")
            if len(params_list) > 10:
                logger.error(
                    "    Params (first 10 of {}): {}...",
                    len(params_list),
                    params_list[:10],
                )
            else:
                logger.error(f"    Params: {params_list}")
            # Don't raise - continue processing other batches
        finally:
            if cursor:
                cursor.close()

    def _execute_batch_create_with_fallback(
        self,
        create_query: str,
        params_list: list[dict[str, Any]],
        merge_query: str,
    ) -> bool:
        """Execute a CREATE batch, falling back to MERGE on 'already exists' errors.

        Tries CREATE first (fast, skips existence check). If it fails with a unique
        constraint violation ('already exists'), retries with MERGE (safe, handles
        duplicates). Returns True if CREATE succeeded, False if fallback was used.
        """
        if not self.conn or not params_list:
            return True
        cursor = None
        try:
            cursor = self.conn.cursor()
            batch_query = f"UNWIND $batch AS row\n{create_query}"
            cursor.execute(batch_query, {"batch": params_list})
            return True
        except Exception as e:
            error_msg = str(e).lower()
            if "already exists" in error_msg:
                # CREATE hit a unique constraint — fall back to MERGE for this batch
                logger.debug("CREATE batch hit duplicate, falling back to MERGE")
                self._execute_batch(merge_query, params_list)
                return False
            # Other errors: log and continue
            logger.error(f"!!! Batch CREATE Cypher Error: {e}")
            logger.error(f"    Query: {create_query}")
            return True  # Don't count as fallback
        finally:
            if cursor:
                cursor.close()

    def _execute_batch_with_return(
        self, query: str, params_list: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Execute a batch query that returns results."""
        if not self.conn or not params_list:
            return []
        cursor = None
        try:
            cursor = self.conn.cursor()
            batch_query = f"UNWIND $batch AS row\n{query}"
            cursor.execute(batch_query, {"batch": params_list})
            if not cursor.description:
                return []
            column_names = [desc.name for desc in cursor.description]
            return [dict(zip(column_names, row)) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"!!! Batch Cypher Error: {e}")
            logger.error(f"    Query: {query}")
            raise
        finally:
            if cursor:
                cursor.close()

    def clean_database(self) -> None:
        logger.info("--- Cleaning database... ---")

        # Delete all nodes and relationships
        self._execute_query("MATCH (n) DETACH DELETE n;")

        # Delete vector indexes
        logger.info("Deleting vector indexes...")
        deleted = self.delete_vector_indexes()
        if deleted > 0:
            logger.info(f"Deleted {deleted} vector indexes")

        # Invalidate all cached queries after database change
        self._query_cache.invalidate()
        logger.info("--- Database cleaned. ---")

    def list_project_names(self) -> list[str]:
        """
        Lightweight method to get only project names (no statistics).

        This is optimized for agent tools that only need to know which repositories
        are available, without the overhead of calculating node/relationship counts.

        Returns:
            List of project names (strings)
        """
        # Use a separate cache key for lightweight query
        cache_key = "LIST_PROJECT_NAMES_ONLY"
        hit, cached_result = self._query_cache.get(cache_key, None)
        if hit:
            return cached_result

        # Fast query - only get project names (uses index)
        projects_query = "MATCH (p:Project) RETURN p.name as name ORDER BY p.name"
        projects = self._execute_query(projects_query)

        if not projects:
            project_names = []
        else:
            project_names = [p["name"] for p in projects]

        # Cache for 30 minutes - project names rarely change
        self._query_cache.set(cache_key, None, project_names, ttl=1800.0)

        return project_names

    def list_projects(self) -> list[dict[str, Any]]:
        """
        List all projects in the database with their node and relationship counts.

        OPTIMIZED V9: Uses optimized queries for both nodes and edges.
        - Nodes: UNION queries with indexed label lookups (parallelizable)
        - Edges: MATCH from indexed start nodes (much faster than full edge scan)
        - Results are cached for 5 minutes to reduce database load
        """
        # Try to get from cache first (use a fixed cache key for this query)
        cache_key_query = "LIST_PROJECTS_OPTIMIZED_V9"
        hit, cached_result = self._query_cache.get(cache_key_query, None)
        if hit:
            return cached_result

        # STEP 1: Get all project names (fast, uses index)
        projects_query = "MATCH (p:Project) RETURN p.name as name ORDER BY p.name"
        projects = self._execute_query(projects_query)

        if not projects:
            return []

        # STEP 2: Get node counts per project using UNION (parallelizable)
        results = []
        for p in projects:
            project_name = p["name"]
            prefix = f"{project_name}."

            # Check per-project cache
            project_cache_key = f"PROJECT_STATS_V9_{project_name}"
            hit, cached_data = self._query_cache.get(project_cache_key, None)
            if hit:
                results.append(
                    {
                        "name": project_name,
                        "node_count": cached_data["node_count"],
                        "relationship_count": cached_data["relationship_count"],
                    }
                )
                continue

            # Use UNION ALL for parallelizable counting (nodes)
            count_query = """
            MATCH (n:File) WHERE n.qualified_name STARTS WITH $prefix RETURN count(n) as cnt
            UNION ALL
            MATCH (n:Function) WHERE n.qualified_name STARTS WITH $prefix RETURN count(n) as cnt
            UNION ALL
            MATCH (n:Class) WHERE n.qualified_name STARTS WITH $prefix RETURN count(n) as cnt
            UNION ALL
            MATCH (n:Method) WHERE n.qualified_name STARTS WITH $prefix RETURN count(n) as cnt
            UNION ALL
            MATCH (n:Folder) WHERE n.qualified_name STARTS WITH $prefix RETURN count(n) as cnt
            """

            # OPTIMIZED: Count relationships by starting from indexed nodes
            # Instead of scanning all edges, we find nodes first, then count their edges
            # This is much faster because qualified_name is indexed
            rel_count_query = """
            MATCH (a)-[r]->(b)
            WHERE a.qualified_name STARTS WITH $prefix
            RETURN count(r) as cnt
            UNION ALL
            MATCH (a)-[r]->(b)
            WHERE b.qualified_name STARTS WITH $prefix
            AND NOT a.qualified_name STARTS WITH $prefix
            RETURN count(r) as cnt
            """

            try:
                result = self._execute_query(count_query, {"prefix": prefix})
                # Sum up all counts + 1 for Project node
                node_count = sum(r.get("cnt", 0) for r in result) + 1 if result else 1
            except Exception as e:
                logger.debug(f"Count query failed for {project_name}: {e}")
                node_count = 0

            try:
                rel_result = self._execute_query(rel_count_query, {"prefix": prefix})
                # Sum up edge counts from both queries
                relationship_count = (
                    sum(r.get("cnt", 0) for r in rel_result) if rel_result else 0
                )
            except Exception as e:
                logger.debug(f"Relationship count query failed for {project_name}: {e}")
                relationship_count = 0

            # Cache per-project count for 5 minutes
            self._query_cache.set(
                project_cache_key,
                None,
                {"node_count": node_count, "relationship_count": relationship_count},
                ttl=300.0,
            )

            results.append(
                {
                    "name": project_name,
                    "node_count": node_count,
                    "relationship_count": relationship_count,
                }
            )

        # Cache the combined results for 5 minutes
        self._query_cache.set(cache_key_query, None, results, ttl=300.0)

        return results

    def get_project_stats(self, project_name: str) -> dict[str, Any]:
        """
        Get statistics for a single project.

        OPTIMIZED V6: Uses UNION queries for parallel counting.
        Each label type is counted separately for better performance.

        Results are cached per-project for 5 minutes (data is static between operations).

        Args:
            project_name: Name of the project

        Returns:
            Dictionary with name, node_count, relationship_count
        """
        # Check per-project cache first
        cache_key = f"PROJECT_STATS_CACHE_V6_{project_name}"
        hit, cached = self._query_cache.get(cache_key, None)
        if hit:
            return cached

        try:
            prefix = f"{project_name}."

            # Use UNION ALL for parallelizable counting
            count_query = """
            MATCH (n:File) WHERE n.qualified_name STARTS WITH $prefix RETURN count(n) as cnt
            UNION ALL
            MATCH (n:Function) WHERE n.qualified_name STARTS WITH $prefix RETURN count(n) as cnt
            UNION ALL
            MATCH (n:Class) WHERE n.qualified_name STARTS WITH $prefix RETURN count(n) as cnt
            UNION ALL
            MATCH (n:Method) WHERE n.qualified_name STARTS WITH $prefix RETURN count(n) as cnt
            UNION ALL
            MATCH (n:Folder) WHERE n.qualified_name STARTS WITH $prefix RETURN count(n) as cnt
            """

            label_order = ["File", "Function", "Class", "Method", "Folder"]
            result = self._execute_query(count_query, {"prefix": prefix})
            # Build node_types from individual label counts
            node_types: dict[str, int] = {}
            total = 0
            if result:
                for i, row in enumerate(result):
                    cnt = row.get("cnt", 0)
                    total += cnt
                    if i < len(label_order) and cnt > 0:
                        node_types[label_order[i]] = cnt
            total += 1  # +1 for Project node
            node_types["Project"] = node_types.get("Project", 0) + 1

            # Count relationships (same approach as list_projects)
            rel_count_query = """
            MATCH (a)-[r]->(b)
            WHERE a.qualified_name STARTS WITH $prefix
            RETURN count(r) as cnt
            UNION ALL
            MATCH (a)-[r]->(b)
            WHERE b.qualified_name STARTS WITH $prefix
            AND NOT a.qualified_name STARTS WITH $prefix
            RETURN count(r) as cnt
            """
            try:
                rel_result = self._execute_query(rel_count_query, {"prefix": prefix})
                relationship_count = sum(r.get("cnt", 0) for r in rel_result) if rel_result else 0
            except Exception:
                relationship_count = 0

            stats = {
                "name": project_name,
                "node_count": total,
                "relationship_count": relationship_count,
                "node_types": node_types,
            }

            # Cache per-project stats for 5 minutes
            self._query_cache.set(cache_key, None, stats, ttl=300.0)

            return stats

        except Exception as e:
            logger.debug(f"Failed to get project stats for {project_name}: {e}")
            return {
                "name": project_name,
                "node_count": 0,
                "relationship_count": 0,
            }

    def project_exists(self, project_name: str) -> bool:
        """
        Check if a project with the given name exists in the database.

        Args:
            project_name: The name of the project to check

        Returns:
            True if project exists, False otherwise
        """
        try:
            query = "MATCH (p:Project {name: $name}) RETURN count(p) as cnt"
            result = self._execute_query(query, {"name": project_name})
            return result and result[0].get("cnt", 0) > 0
        except Exception as e:
            logger.warning(f"Failed to check project existence for {project_name}: {e}")
            return False

    def get_project_path(self, project_name: str) -> str | None:
        """
        Get the repository path for a project from the database.

        This is essential for local path support - instead of hardcoding wiki_repos,
        we retrieve the actual path stored when the project was built.

        Args:
            project_name: The name of the project

        Returns:
            The absolute path to the project's repository, or None if not found
        """
        # Check cache first
        cache_key = f"PROJECT_PATH_{project_name}"
        hit, cached_path = self._query_cache.get(cache_key, None)
        if hit:
            return cached_path

        try:
            query = "MATCH (p:Project {name: $name}) RETURN p.path as path"
            result = self._execute_query(query, {"name": project_name})

            if result and result[0].get("path"):
                path = result[0]["path"]
                # Cache for 5 minutes
                self._query_cache.set(cache_key, None, path, ttl=300.0)
                return path

            return None

        except Exception as e:
            logger.warning(f"Failed to get path for project {project_name}: {e}")
            return None

    def clean_project(self, project_name: str) -> dict[str, Any]:
        """
        Remove all nodes and relationships belonging to a specific project.

        This works by:
        1. Deleting nodes reachable from the Project node via relationships
        2. Deleting nodes that have qualified_name starting with the project name
        3. Deleting the Project node itself

        Args:
            project_name: The name of the project to remove

        Returns:
            Dictionary with deletion statistics
        """
        logger.info(f"--- Cleaning project '{project_name}'... ---")

        # First, verify the project exists
        check_query = "MATCH (p:Project {name: $name}) RETURN p.name as name"
        project_check = self._execute_query(check_query, {"name": project_name})

        if not project_check:
            logger.info(
                f"Project '{project_name}' not found in database (nothing to clean)."
            )
            return {
                "project_name": project_name,
                "deleted_nodes": 0,
                "deleted_relationships": 0,
                "success": True,
                "message": f"Project '{project_name}' not found (nothing to clean)",
            }

        # PHASE 0: Get repo_path BEFORE deleting any data
        # This is critical - if we delete the Project node first, we can't retrieve repo_path
        repo_path = None
        try:
            repo_path_query = (
                "MATCH (p:Project {name: $project_name}) RETURN p.path as path"
            )
            repo_result = self._execute_query(
                repo_path_query, {"project_name": project_name}
            )
            if repo_result and repo_result[0].get("path"):
                repo_path = Path(repo_result[0]["path"])
                logger.debug(f"Retrieved repo_path before cleanup: {repo_path}")
        except Exception as e:
            logger.debug(f"Could not retrieve repo_path: {e}")

        project_prefix = f"{project_name}."
        total_deleted = 0
        batch_size = self._CLEANUP_BATCH_SIZE

        # PHASE 1: Delete nodes by qualified_name (Module, Class, Function, Method)
        # These nodes have qualified_name that starts with project_name
        # Note: Folder nodes with is_package=true also have qualified_name but are handled in Phase 2
        logger.debug("Phase 1: Deleting nodes by qualified_name prefix...")
        while True:
            delete_query = """
            MATCH (n)
            WHERE n.qualified_name STARTS WITH $project_prefix
               OR n.qualified_name = $project_name
            WITH n LIMIT $batch_size
            DETACH DELETE n
            RETURN count(n) as deleted_count
            """
            result = self._execute_query(
                delete_query,
                {
                    "project_prefix": project_prefix,
                    "project_name": project_name,
                    "batch_size": batch_size,
                },
            )
            deleted = result[0].get("deleted_count", 0) if result else 0
            total_deleted += deleted

            if deleted == 0:
                break

            logger.debug(
                f"  Deleted {deleted} nodes by qualified_name, total: {total_deleted}"
            )

        # PHASE 2: Delete File and Folder nodes connected to the Project
        # These nodes use 'path' property without project prefix, so we must traverse
        logger.debug("Phase 2: Deleting File and Folder nodes via relationships...")
        while True:
            # Delete files/folders that are reachable from the project
            # Using variable-length path to catch all nested structures
            delete_rel_query = """
            MATCH (p:Project {name: $project_name})-[*1..10]->(n)
            WHERE n:File OR n:Folder
            WITH n LIMIT $batch_size
            DETACH DELETE n
            RETURN count(n) as deleted_count
            """
            result = self._execute_query(
                delete_rel_query,
                {"project_name": project_name, "batch_size": batch_size},
            )
            deleted = result[0].get("deleted_count", 0) if result else 0
            total_deleted += deleted

            if deleted == 0:
                break

            logger.debug(
                f"  Deleted {deleted} File/Folder nodes, total: {total_deleted}"
            )

        # PHASE 3: Delete any remaining connected nodes (ExternalPackage, etc.)
        logger.debug("Phase 3: Deleting remaining connected nodes...")
        while True:
            delete_connected_query = """
            MATCH (p:Project {name: $project_name})-[*1..10]->(n)
            WITH n LIMIT $batch_size
            DETACH DELETE n
            RETURN count(n) as deleted_count
            """
            result = self._execute_query(
                delete_connected_query,
                {"project_name": project_name, "batch_size": batch_size},
            )
            deleted = result[0].get("deleted_count", 0) if result else 0
            total_deleted += deleted

            if deleted == 0:
                break

            logger.debug(f"  Deleted {deleted} connected nodes, total: {total_deleted}")

        # PHASE 4: Delete the Project node itself
        logger.debug("Phase 4: Deleting Project node...")
        delete_project_query = """
        MATCH (p:Project {name: $project_name})
        DETACH DELETE p
        RETURN count(p) as deleted_count
        """
        result = self._execute_query(
            delete_project_query, {"project_name": project_name}
        )
        deleted = result[0].get("deleted_count", 0) if result else 0
        total_deleted += deleted

        # Invalidate cache after project deletion
        self._query_cache.invalidate()

        # PHASE 5: Delete ALL incremental build state files
        # This ensures that subsequent rebuilds will be full builds, not incremental
        # Uses repo_path retrieved in PHASE 0 (before Project node was deleted)
        try:
            if repo_path and repo_path.exists():
                atcode_dir = repo_path / ".atcode"
                if atcode_dir.exists():
                    # List of all state files that should be deleted
                    state_files_to_delete = [
                        f"{project_name}_state.json",  # IncrementalBuilder state
                        f"{project_name}_definitions.json",  # DefinitionsStore
                        f"{project_name}_callers.json",  # CallersIndex
                        "hash_cache.json",  # ChangeWatcher cache
                    ]

                    for state_file_name in state_files_to_delete:
                        state_file = atcode_dir / state_file_name
                        if state_file.exists():
                            try:
                                state_file.unlink()
                                logger.info(f"Deleted state file: {state_file}")
                            except Exception as e:
                                logger.debug(f"Could not delete {state_file}: {e}")

                    # Delete AST cache directory
                    ast_cache_dir = atcode_dir / "ast_cache"
                    if ast_cache_dir.exists():
                        import shutil

                        try:
                            shutil.rmtree(ast_cache_dir)
                            logger.info(f"Deleted AST cache directory: {ast_cache_dir}")
                        except Exception as e:
                            logger.debug(f"Could not delete AST cache: {e}")
                else:
                    logger.debug(f"No .atcode directory found at {repo_path}")
            else:
                # Fallback: try common locations if repo_path was not available
                logger.debug("repo_path not available, trying common locations...")
                possible_locations = [
                    Path(f".atcode/{project_name}_state.json"),  # Current directory
                    Path(
                        f"data/wiki_repos/{project_name}/.atcode/{project_name}_state.json"
                    ),  # Default clone location
                ]

                deleted_state = False
                for state_file in possible_locations:
                    if state_file.exists():
                        try:
                            state_file.unlink()
                            logger.info(f"Deleted incremental state file: {state_file}")
                            deleted_state = True
                            break
                        except Exception as e:
                            logger.debug(
                                f"Could not delete state file {state_file}: {e}"
                            )

                if not deleted_state:
                    logger.debug(
                        f"No incremental state file found for '{project_name}' (or already deleted)"
                    )

        except Exception as e:
            # Don't fail the clean operation if state file deletion fails
            logger.warning(
                f"Could not delete incremental state files for '{project_name}': {e}"
            )

        # PHASE 6: Rebuild vector indexes to ensure correct dimensions
        # After cleaning a project, we should rebuild indexes to match current embedding config
        logger.info("Rebuilding vector indexes with current embedding dimension...")
        try:
            # Delete old indexes
            deleted_indexes = self.delete_vector_indexes()
            if deleted_indexes > 0:
                logger.info(f"Deleted {deleted_indexes} old vector indexes")

            # Get current embedding dimension from config
            from core.config import settings

            dimension = getattr(settings, "EMBEDDING_DIMENSION", 1536)

            # Create new indexes with correct dimension
            self.setup_vector_index(dimension=dimension)
            logger.info(f"Rebuilt vector indexes with dimension={dimension}")
        except Exception as e:
            logger.warning(f"Failed to rebuild vector indexes: {e}")
            # Don't fail the clean operation if index rebuild fails

        logger.info(
            f"--- Project '{project_name}' cleaned: {total_deleted} nodes deleted. ---"
        )

        return {
            "project_name": project_name,
            "deleted_nodes": total_deleted,
            "success": True,
            "message": f"Successfully deleted {total_deleted} nodes for project '{project_name}'",
        }

    def delete_file_nodes(
        self, project_name: str, file_path: str, module_qn_prefix: str
    ) -> int:
        """
        Delete all nodes associated with a specific file from the graph.

        This is used during incremental builds when a file is deleted.
        It removes:
        1. The File node itself
        2. All Function, Method, Class nodes defined in this file (by qualified_name prefix)

        Args:
            project_name: The project name
            file_path: The absolute or relative path of the file
            module_qn_prefix: The qualified name prefix for nodes in this file
                             (e.g., "project_name.module.submodule")

        Returns:
            Total number of nodes deleted
        """
        logger.debug(
            f"Deleting nodes for file: {file_path} (prefix: {module_qn_prefix})"
        )

        total_deleted = 0

        # PHASE 1: Delete nodes by qualified_name prefix
        # This catches Function, Method, Class nodes defined in this file
        delete_by_qn_query = """
        MATCH (n)
        WHERE n.qualified_name STARTS WITH $prefix_dot
           OR n.qualified_name = $prefix
        DETACH DELETE n
        RETURN count(n) as deleted_count
        """
        result = self._execute_query(
            delete_by_qn_query,
            {
                "prefix_dot": f"{module_qn_prefix}.",
                "prefix": module_qn_prefix,
            },
        )
        deleted = result[0].get("deleted_count", 0) if result else 0
        total_deleted += deleted
        if deleted > 0:
            logger.debug(f"  Deleted {deleted} nodes by qualified_name prefix")

        # PHASE 2: Delete the File node by path
        delete_file_query = """
        MATCH (f:File)
        WHERE f.path = $file_path OR f.path ENDS WITH $file_suffix
        DETACH DELETE f
        RETURN count(f) as deleted_count
        """
        # Try both absolute path and relative path matching
        file_suffix = (
            "/" + file_path.lstrip("/")
            if file_path.startswith("/")
            else "/" + file_path
        )
        result = self._execute_query(
            delete_file_query,
            {
                "file_path": file_path,
                "file_suffix": file_suffix,
            },
        )
        deleted = result[0].get("deleted_count", 0) if result else 0
        total_deleted += deleted
        if deleted > 0:
            logger.debug(f"  Deleted {deleted} File nodes")

        if total_deleted > 0:
            logger.info(f"Deleted {total_deleted} nodes for file: {file_path}")
            # Invalidate cache after deletion
            self._query_cache.invalidate()

        return total_deleted

    def drop_all_constraints(self) -> int:
        """Drop all existing constraints. Required before switching to ANALYTICAL mode.

        Returns:
            Number of constraints dropped.
        """
        dropped = 0
        try:
            result = self._execute_query("SHOW CONSTRAINT INFO;")
            if result:
                for row in result:
                    # Memgraph returns: constraint_type, label, properties
                    constraint_type = row.get("constraint type", "")
                    label = row.get("label", "")
                    properties = row.get("properties", "")
                    try:
                        if "unique" in constraint_type.lower():
                            # properties might be a list like "['name']" or a string
                            if isinstance(properties, list):
                                prop = properties[0] if properties else ""
                            else:
                                prop = str(properties).strip("[]'\" ")
                            if label and prop:
                                self._execute_query(
                                    f"DROP CONSTRAINT ON (n:{label}) ASSERT n.{prop} IS UNIQUE;"
                                )
                                dropped += 1
                                logger.debug(f"Dropped constraint: {label}.{prop}")
                        elif "exists" in constraint_type.lower():
                            if isinstance(properties, list):
                                prop = properties[0] if properties else ""
                            else:
                                prop = str(properties).strip("[]'\" ")
                            if label and prop:
                                self._execute_query(
                                    f"DROP CONSTRAINT ON (n:{label}) ASSERT EXISTS (n.{prop});"
                                )
                                dropped += 1
                                logger.debug(f"Dropped exists constraint: {label}.{prop}")
                    except Exception as e:
                        logger.debug(f"Failed to drop constraint {label}: {e}")
            logger.info(f"Dropped {dropped} constraints for ANALYTICAL mode switch")
        except Exception as e:
            logger.warning(f"Failed to enumerate constraints: {e}")
        return dropped

    def ensure_constraints(self) -> None:
        logger.info("Ensuring constraints...")

        # For File and Folder, we need to drop old path-based constraints first
        # and create new qualified_name-based constraints for project isolation
        old_constraints = {
            "File": "path",
            "Folder": "path",
        }

        for label, old_prop in old_constraints.items():
            try:
                self._execute_query(
                    f"DROP CONSTRAINT ON (n:{label}) ASSERT n.{old_prop} IS UNIQUE;"
                )
                logger.info(
                    f"Dropped old {label}.{old_prop} constraint for project isolation"
                )
            except Exception:
                pass  # Constraint may not exist

        # Create all constraints based on unique_constraints
        for label, prop in self.unique_constraints.items():
            try:
                self._execute_query(
                    f"CREATE CONSTRAINT ON (n:{label}) ASSERT n.{prop} IS UNIQUE;"
                )
                logger.info(f"Created {label}.{prop} constraint")
            except Exception as e:
                logger.debug(f"Constraint {label}.{prop} may already exist: {e}")

        logger.info("Constraints checked/created.")

    def ensure_indexes(self) -> None:
        """
        Create indexes for frequently queried properties.

        Indexes significantly improve query performance for:
        - qualified_name lookups (most common)
        - path-based queries
        - name searches

        Note: Memgraph automatically creates indexes for unique constraints,
        but we add additional indexes for non-unique properties.
        """
        logger.info("Ensuring indexes...")

        # Index on qualified_name for all code nodes (most important for query performance)
        index_queries = [
            # qualified_name is the most frequently queried property
            "CREATE INDEX ON :Function(qualified_name);",
            "CREATE INDEX ON :Method(qualified_name);",
            "CREATE INDEX ON :Class(qualified_name);",
            # File uses both path and qualified_name for lookups
            "CREATE INDEX ON :File(path);",
            "CREATE INDEX ON :File(qualified_name);",
            # Folder uses path as primary key, qualified_name for package folders
            "CREATE INDEX ON :Folder(path);",
            "CREATE INDEX ON :Folder(qualified_name);",
            "CREATE INDEX ON :Folder(is_package);",
            # name is used for pattern searches
            "CREATE INDEX ON :Function(name);",
            "CREATE INDEX ON :Method(name);",
            "CREATE INDEX ON :Class(name);",
            # Project name for project scoping
            "CREATE INDEX ON :Project(name);",
            # ExternalPackage name for dependency relationships
            "CREATE INDEX ON :ExternalPackage(name);",
        ]

        for index_query in index_queries:
            try:
                self._execute_query(index_query)
            except Exception as e:
                # Index might already exist, which is fine
                if "already exists" not in str(e).lower():
                    logger.debug(f"Index creation note: {e}")

        logger.info("Indexes checked/created.")

    def ensure_node_batch(self, label: str, properties: dict[str, Any]) -> None:
        """Adds a node to the buffer. Thread-safe for parallel processing."""
        with self._node_buffer_lock:
            self.node_buffer.append((label, properties))
            should_flush = len(self.node_buffer) >= self.batch_size

        # In deferred flush mode, never flush proactively — accumulate everything
        # in memory for a single bulk write in flush_all().
        if self._deferred_flush:
            return

        # Try to flush without holding buffer lock to avoid deadlock.
        # Skip proactive flush when the background flusher is running to avoid
        # blocking the caller and to let the bg flusher build larger batches.
        if (
            should_flush
            and not self._is_bg_flusher_active()
            and self._try_flush_nodes_async()
        ):
            logger.debug(
                "Node buffer reached batch size ({}). Performing incremental flush.",
                self.batch_size,
            )

    def extend_node_buffer(self, items: list[tuple[str, dict]]) -> None:
        """Bulk-append nodes to the buffer. Reduces lock overhead vs individual calls."""
        with self._node_buffer_lock:
            self.node_buffer.extend(items)
            should_flush = len(self.node_buffer) >= self.batch_size

        # In deferred flush mode, never flush proactively.
        if self._deferred_flush:
            return

        # Skip proactive flush when the background flusher is running — let it
        # handle all DB writes so the caller (merge loop) stays CPU-bound.
        # This avoids the caller blocking on _flush_lock (even non-blocking
        # attempts add overhead) and lets the bg flusher build larger batches
        # with a more complete node ID cache for relationship writes.
        if (
            should_flush
            and not self._is_bg_flusher_active()
            and self._try_flush_nodes_async()
        ):
            logger.debug("Node buffer reached batch size after bulk extend. Flushed.")

    def extend_relationship_buffer(self, items: list[tuple]) -> None:
        """Bulk-append relationships to the buffer. Reduces lock overhead vs individual calls."""
        with self._relationship_buffer_lock:
            self.relationship_buffer.extend(items)

    def _try_flush_nodes_async(self) -> bool:
        """Try to flush nodes asynchronously without blocking. Returns True if flush was performed."""
        # Step 1: Atomically swap buffer (without holding flush_lock)
        with self._node_buffer_lock:
            if not self.node_buffer:
                return False
            buffer_to_flush = self.node_buffer
            self.node_buffer = []

        # Step 2: Try to acquire flush lock without blocking
        if not self._flush_lock.acquire(blocking=False):
            # Put buffer back - another thread is flushing
            with self._node_buffer_lock:
                self.node_buffer.extend(buffer_to_flush)
            return False

        try:
            # Perform the actual flush (use CREATE in full-build mode for speed)
            # Collect node IDs so flush_all() can skip supplemental cache queries.
            bg_ids = self._flush_nodes_impl(
                buffer_to_flush,
                use_create=self._use_create_nodes,
                progress_callback=None,
                collect_node_ids=True,
            )
            if bg_ids:
                with self._bg_node_id_cache_lock:
                    self._bg_node_id_cache.update(bg_ids)
            return True
        finally:
            self._flush_lock.release()

    def ensure_relationship_batch(
        self,
        from_spec: tuple[str, str, Any],
        rel_type: str,
        to_spec: tuple[str, str, Any],
        properties: dict[str, Any] | None = None,
    ) -> None:
        """Adds a relationship to the buffer. Thread-safe for parallel processing."""
        from_label, from_key, from_val = from_spec
        to_label, to_key, to_val = to_spec

        with self._relationship_buffer_lock:
            self.relationship_buffer.append(
                (
                    (from_label, from_key, from_val),
                    rel_type,
                    (to_label, to_key, to_val),
                    properties,
                )
            )

        # Check if we should flush (without holding buffer lock)
        # Note: We don't auto-flush relationships to avoid deadlock
        # Relationships will be flushed when:
        # 1. Node buffer is full (flush_nodes also flushes relationships)
        # 2. Explicitly called (flush_relationships or flush_all)

    def _try_flush_relationships_async(self) -> bool:
        """Try to flush relationships asynchronously without blocking. Returns True if flush was performed."""
        # Step 1: Atomically swap buffer (without holding flush_lock)
        with self._relationship_buffer_lock:
            if not self.relationship_buffer:
                return False
            buffer_to_flush = self.relationship_buffer
            self.relationship_buffer = []

        # Step 2: Try to acquire flush lock without blocking
        if not self._flush_lock.acquire(blocking=False):
            # Put buffer back - another thread is flushing
            # CRITICAL: Insert at the beginning to preserve order (old items should be flushed first)
            with self._relationship_buffer_lock:
                self.relationship_buffer = buffer_to_flush + self.relationship_buffer
            return False

        try:
            # Also flush any pending nodes first (use CREATE in full-build mode)
            # Collect node IDs for the shared cache.
            with self._node_buffer_lock:
                if self.node_buffer:
                    node_buffer = self.node_buffer
                    self.node_buffer = []
                    bg_ids = self._flush_nodes_impl(
                        node_buffer,
                        use_create=self._use_create_nodes,
                        progress_callback=None,
                        collect_node_ids=True,
                    )
                    if bg_ids:
                        with self._bg_node_id_cache_lock:
                            self._bg_node_id_cache.update(bg_ids)

            # Now flush relationships, using node ID cache for fast ID-based MATCH
            with self._bg_node_id_cache_lock:
                id_cache_snapshot = (
                    dict(self._bg_node_id_cache) if self._bg_node_id_cache else None
                )
            self._flush_relationships_impl(
                buffer_to_flush, progress_callback=None, node_id_cache=id_cache_snapshot
            )
            return True
        finally:
            self._flush_lock.release()

    def _flush_relationships_blocking(self) -> bool:
        """Flush relationships with blocking lock acquisition.

        Unlike _try_flush_relationships_async() which uses non-blocking lock,
        this method waits for the lock. Designed for use in a dedicated flush
        thread where blocking is acceptable (the caller thread pool handles
        serialization). This ensures data is actually flushed rather than
        being put back into the buffer on lock contention.

        Returns True if flush was performed, False if buffer was empty.
        """
        # Step 1: Atomically swap buffer
        with self._relationship_buffer_lock:
            if not self.relationship_buffer:
                return False
            buffer_to_flush = self.relationship_buffer
            self.relationship_buffer = []

        # Step 2: Acquire flush lock (blocking — wait for any in-progress flush)
        self._flush_lock.acquire()
        try:
            # Flush any pending nodes first (relationship endpoints must exist)
            # Collect node IDs for the shared cache.
            with self._node_buffer_lock:
                if self.node_buffer:
                    node_buffer = self.node_buffer
                    self.node_buffer = []
                    bg_ids = self._flush_nodes_impl(
                        node_buffer,
                        use_create=self._use_create_nodes,
                        progress_callback=None,
                        collect_node_ids=True,
                    )
                    if bg_ids:
                        with self._bg_node_id_cache_lock:
                            self._bg_node_id_cache.update(bg_ids)

            # Flush relationships, using node ID cache for fast ID-based MATCH
            with self._bg_node_id_cache_lock:
                id_cache_snapshot = (
                    dict(self._bg_node_id_cache) if self._bg_node_id_cache else None
                )
            self._flush_relationships_impl(
                buffer_to_flush, progress_callback=None, node_id_cache=id_cache_snapshot
            )
            return True
        finally:
            self._flush_lock.release()

    def _is_bg_flusher_active(self) -> bool:
        """Check if the background flusher thread is currently running."""
        return self._bg_flush_thread is not None and self._bg_flush_thread.is_alive()

    def enable_deferred_flush(self, use_create: bool = False) -> None:
        """Enable deferred flush mode: accumulate all writes in memory.

        In this mode, no DB writes occur until flush_all() is called.
        This eliminates background flusher overhead (polling, lock contention,
        per-batch DB round-trips) and makes CPU passes purely CPU-bound.

        Args:
            use_create: If True, use CREATE instead of MERGE when flushing
                       (faster for full builds after clean_database()).
        """
        self._deferred_flush = True
        self._use_create_nodes = use_create
        # Clear the bg node ID cache (not needed in deferred mode since
        # flush_all builds the cache during its own node flush).
        with self._bg_node_id_cache_lock:
            self._bg_node_id_cache.clear()
        logger.info(f"Deferred flush mode enabled (use_create={use_create})")

    def disable_deferred_flush(self) -> None:
        """Disable deferred flush mode (restore normal behavior)."""
        self._deferred_flush = False
        logger.info("Deferred flush mode disabled")

    # ------------------------------------------------------------------
    # CSV bulk import: fast alternative to UNWIND for large flushes
    # ------------------------------------------------------------------

    def _flush_all_csv(
        self,
        node_buffer: list[tuple[str, dict[str, Any]]],
        rel_buffer: list[tuple[tuple, str, tuple, dict | None]],
        use_create_nodes: bool = True,
        use_create_rels: bool = True,
        progress_callback=None,
    ) -> bool:
        """Flush all buffered nodes and relationships via parallel LOAD CSV.

        Uses Memgraph's ``csv_utils.create_csv_file`` module to write CSV
        data **inside the Memgraph container**, then ``LOAD CSV`` to bulk-
        import.

        **Parallel execution**: In IN_MEMORY_ANALYTICAL mode, Memgraph
        supports concurrent writes.  This method:
        1. Prepares ALL CSV files and queries upfront (sequentially on main conn)
        2. Executes node LOAD CSV queries in PARALLEL via separate connections
        3. Waits for all nodes to complete (rels need nodes to exist)
        4. Executes relationship LOAD CSV queries in PARALLEL

        Args:
            node_buffer: List of (label, properties) tuples.
            rel_buffer:  List of (from_spec, rel_type, to_spec, props) tuples.
            use_create_nodes: Use CREATE instead of MERGE for nodes.
            use_create_rels: Use CREATE instead of MERGE for relationships.
            progress_callback: Optional (current, total) callback.

        Returns:
            True if CSV import succeeded, False if caller should fall back
            to the regular UNWIND approach.
        """
        import io
        import json
        import os
        import threading as _threading
        import time as _time
        from concurrent.futures import ThreadPoolExecutor, as_completed

        total_nodes = len(node_buffer)
        total_rels = len(rel_buffer)
        total_items = total_nodes + total_rels
        if total_items == 0:
            return True

        csv_start = _time.monotonic()

        # Track CSV files for cleanup
        csv_files: list[str] = []
        csv_files_lock = _threading.Lock()

        # Prefer direct filesystem writes (fast, no Bolt overhead) over
        # csv_utils stored procedure (slow, sends full CSV as Bolt param).
        # Direct writes work when Memgraph reads from the same filesystem
        # (i.e., not running inside a separate Docker container).
        # Probe: write a small test CSV and verify Memgraph can LOAD it.
        _use_direct_fs = False
        _probe_path = "/tmp/atcode_probe.csv"
        try:
            with open(_probe_path, "w") as _pf:
                _pf.write("x\n1\n")
            self._execute_query(
                f"LOAD CSV FROM '{_probe_path}' WITH HEADER AS row "
                f"RETURN row.x LIMIT 1"
            )
            _use_direct_fs = True
            logger.info("LOAD CSV: direct filesystem access verified")
        except Exception as _e:
            logger.info(f"LOAD CSV: direct filesystem not available ({_e}), using csv_utils")
        finally:
            try:
                import os as _os
                _os.unlink(_probe_path)
            except OSError:
                pass

        def _write_csv_file(filename: str, content: str) -> str:
            """Write CSV content to the filesystem for LOAD CSV.

            Tries direct filesystem write first (fast), falls back to
            csv_utils stored procedure if direct write is not accessible
            by Memgraph.
            """
            nonlocal _use_direct_fs
            path = f"/tmp/atcode_{filename}"

            if _use_direct_fs:
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(content)
                    with csv_files_lock:
                        csv_files.append(path)
                    return path
                except OSError as e:
                    logger.warning(
                        f"Direct CSV write failed ({e}), "
                        f"falling back to csv_utils"
                    )
                    _use_direct_fs = False

            # Fallback: use csv_utils stored procedure
            try:
                self._execute_query(
                    "CALL csv_utils.create_csv_file($path, $content) "
                    "YIELD filepath RETURN filepath",
                    {"path": path, "content": content},
                )
                with csv_files_lock:
                    csv_files.append(path)
                return path
            except Exception:
                raise

        def _delete_csv_file(path: str) -> None:
            """Delete a CSV file."""
            try:
                import os as _os
                _os.unlink(path)
            except OSError:
                # Fallback: try via csv_utils
                try:
                    self._execute_query(
                        "CALL csv_utils.delete_csv_file($path)",
                        {"path": path},
                    )
                except Exception:
                    pass

        def _csv_escape(s: str) -> str:
            """Escape a string for CSV (quote if needed)."""
            if not s:
                return ""
            needs_quote = "," in s or '"' in s or "\n" in s or "\r" in s
            s = s.replace("\r", "").replace("\n", "\\n")
            if needs_quote:
                s = '"' + s.replace('"', '""') + '"'
            return s

        def _build_csv_string(header: list[str], rows_iter, col_serializer) -> str:
            """Build a CSV string in memory using io.StringIO."""
            buf = io.StringIO()
            buf.write(",".join(header))
            buf.write("\n")
            for row in rows_iter:
                vals: list[str] = []
                for k in header:
                    v = row.get(k)
                    vals.append(col_serializer(k, v))
                buf.write(",".join(vals))
                buf.write("\n")
            return buf.getvalue()

        def _node_serializer(k: str, v: Any) -> str:
            if v is None:
                return ""
            if isinstance(v, bool):
                return "true" if v else "false"
            if isinstance(v, (list, tuple)):
                return _csv_escape(json.dumps(v))
            if isinstance(v, str):
                return _csv_escape(v)
            return str(v)

        # ── Thread-local connections for parallel LOAD CSV execution ──
        thread_local = _threading.local()
        connections_to_close: list[mgclient.Connection] = []
        conn_lock = _threading.Lock()

        def _get_thread_conn() -> mgclient.Connection:
            """Get or create a connection for this thread."""
            conn = getattr(thread_local, "conn", None)
            if conn is None:
                conn = mgclient.connect(host=self._host, port=self._port)
                conn.autocommit = True
                thread_local.conn = conn
                with conn_lock:
                    connections_to_close.append(conn)
            return conn

        def _exec_load_csv(query: str, fallback_query: str | None = None) -> bool:
            """Execute a LOAD CSV query on a thread-local connection.

            Returns True on success, False on unrecoverable / unsupported failure.
            """
            conn = _get_thread_conn()
            cursor = None
            try:
                cursor = conn.cursor()
                cursor.execute(query)
                return True
            except Exception as e:
                error_msg = str(e).lower()
                if "already exists" in error_msg and fallback_query:
                    cursor2 = None
                    try:
                        cursor2 = conn.cursor()
                        cursor2.execute(fallback_query)
                        return True
                    except Exception as e2:
                        logger.error(f"LOAD CSV MERGE fallback failed: {e2}")
                        return False
                    finally:
                        if cursor2:
                            cursor2.close()
                elif "already exists" in error_msg:
                    return True
                elif any(
                    kw in error_msg
                    for kw in ("load csv", "csv_utils", "csv file", "not found")
                ):
                    logger.warning(f"LOAD CSV not available: {e}")
                    return False
                else:
                    logger.error(f"LOAD CSV import failed: {e}")
                    return False
            finally:
                if cursor:
                    cursor.close()

        # Thread-safe progress tracking
        processed_count = [0]
        progress_lock = _threading.Lock()

        def _report_progress(count: int) -> None:
            with progress_lock:
                processed_count[0] += count
                if progress_callback:
                    progress_callback(processed_count[0], total_items)

        def _run_work_items_parallel(
            work_items: list[tuple[str, str | None, int]],
            max_workers: int,
            phase_name: str,
        ) -> bool:
            """Execute (query, fallback, count) work items in parallel.

            Returns True if all succeeded, False on any failure.
            """
            if not work_items:
                return True
            num_workers = min(max_workers, os.cpu_count() or 4, len(work_items))

            if num_workers <= 1:
                for query, fallback, count in work_items:
                    if not _exec_load_csv(query, fallback):
                        return False
                    _report_progress(count)
                return True

            logger.info(
                f"  {phase_name}: {len(work_items)} work items, "
                f"{num_workers} parallel workers"
            )
            any_failed = False
            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                future_to_count = {}
                for query, fallback, count in work_items:
                    f = executor.submit(_exec_load_csv, query, fallback)
                    future_to_count[f] = count
                for f in as_completed(future_to_count):
                    if not f.result():
                        any_failed = True
                    else:
                        _report_progress(future_to_count[f])
            return not any_failed

        try:
            # ============================================================
            # Phase 1: Prepare + import nodes via CSV (grouped by label)
            # ============================================================
            nodes_by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for label, props in node_buffer:
                nodes_by_label[label].append(props)

            node_verb = "CREATE" if use_create_nodes else "MERGE"
            node_start = _time.monotonic()

            # Build work items: (query, fallback_query_or_None, row_count)
            node_work_items: list[tuple[str, str | None, int]] = []

            for label, props_list in nodes_by_label.items():
                if not props_list:
                    continue
                id_key = self.unique_constraints.get(label)
                if not id_key:
                    continue

                # Deduplicate by id_key (last wins)
                deduped: dict[Any, dict[str, Any]] = {}
                for props in props_list:
                    id_val = props.get(id_key)
                    if id_val is None:
                        continue
                    deduped[id_val] = props
                rows = list(deduped.values())
                if not rows:
                    continue

                # Discover all property keys across rows of this label
                all_keys: list[str] = []
                key_set: set[str] = set()
                for row in rows:
                    for k in row:
                        if k not in key_set:
                            key_set.add(k)
                            all_keys.append(k)

                if id_key in all_keys:
                    all_keys.remove(id_key)
                all_keys.insert(0, id_key)

                # Detect column types from first non-None values
                col_types: dict[str, str] = {}
                for k in all_keys:
                    for row in rows:
                        v = row.get(k)
                        if v is not None:
                            if isinstance(v, bool):
                                col_types[k] = "bool"
                            elif isinstance(v, int):
                                col_types[k] = "int"
                            elif isinstance(v, float):
                                col_types[k] = "float"
                            elif isinstance(v, (list, tuple)):
                                col_types[k] = "list"
                            else:
                                col_types[k] = "str"
                            break
                    else:
                        col_types[k] = "str"

                csv_content = _build_csv_string(all_keys, rows, _node_serializer)

                # Write CSV to filesystem (direct write preferred, csv_utils fallback)
                csv_path = _write_csv_file(f"nodes_{label}.csv", csv_content)

                # Build LOAD CSV query with proper type casting
                set_clauses: list[str] = []
                for k in all_keys:
                    if k == id_key:
                        continue
                    ctype = col_types.get(k, "str")
                    if ctype == "int":
                        set_clauses.append(
                            f"n.{k} = CASE WHEN row.{k} = '' THEN null "
                            f"ELSE toInteger(row.{k}) END"
                        )
                    elif ctype == "float":
                        set_clauses.append(
                            f"n.{k} = CASE WHEN row.{k} = '' THEN null "
                            f"ELSE toFloat(row.{k}) END"
                        )
                    elif ctype == "bool":
                        set_clauses.append(
                            f"n.{k} = CASE WHEN row.{k} = '' THEN null "
                            f"WHEN row.{k} = 'true' THEN true ELSE false END"
                        )
                    else:
                        set_clauses.append(
                            f"n.{k} = CASE WHEN row.{k} = '' THEN null ELSE row.{k} END"
                        )

                set_stmt = ", ".join(set_clauses) if set_clauses else ""
                id_type = col_types.get(id_key, "str")
                id_expr = (
                    f"toInteger(row.{id_key})" if id_type == "int" else f"row.{id_key}"
                )

                query = f"LOAD CSV FROM '{csv_path}' WITH HEADER AS row\n"
                query += f"{node_verb} (n:{label} {{{id_key}: {id_expr}}})\n"
                if set_stmt:
                    query += f"SET {set_stmt}"

                fallback = None
                if use_create_nodes:
                    fallback = query.replace(f"{node_verb} (n:", "MERGE (n:", 1)

                node_work_items.append((query, fallback, len(rows)))

            csv_prep_elapsed = _time.monotonic() - node_start

            # ── Execute node LOAD CSV in parallel ──
            total_node_rows = sum(w[2] for w in node_work_items)
            logger.info(
                f"CSV node import: {len(node_work_items)} labels, "
                f"{total_node_rows} nodes (prep={csv_prep_elapsed:.2f}s)"
            )
            if not _run_work_items_parallel(node_work_items, 8, "Nodes"):
                return False

            node_elapsed = _time.monotonic() - node_start
            logger.info(
                f"CSV node import complete: {total_node_rows} nodes in {node_elapsed:.2f}s"
            )

            # ============================================================
            # Phase 2: Prepare + import relationships via CSV (parallel)
            # ============================================================
            rel_start = _time.monotonic()
            if total_rels > 0:
                rel_verb = "CREATE" if use_create_rels else "MERGE"

                # Ensure indexes before relationship import (fast in ANALYTICAL mode)
                self.ensure_indexes()

                # Group by (from_label, from_key, rel_type, to_label, to_key)
                rels_by_pattern: dict[tuple, list[tuple[str, str]]] = defaultdict(list)

                for from_node, rel_type, to_node, _props in rel_buffer:
                    from_label, from_key, from_val = from_node
                    to_label, to_key, to_val = to_node
                    pattern = (from_label, from_key, rel_type, to_label, to_key)
                    rels_by_pattern[pattern].append((from_val, to_val))

                rel_work_items: list[tuple[str, str | None, int]] = []

                # Track deduped CALLS count for accurate verification
                _deduped_calls_count = 0

                for pattern, rels_list in rels_by_pattern.items():
                    from_label, from_key, rel_type, to_label, to_key = pattern
                    if not rels_list:
                        continue

                    if use_create_rels:
                        rels_list = list(dict.fromkeys(rels_list))

                    if rel_type == "CALLS":
                        _deduped_calls_count += len(rels_list)

                    buf = io.StringIO()
                    buf.write("from_val,to_val\n")
                    for from_val, to_val in rels_list:
                        buf.write(
                            f"{_csv_escape(str(from_val))},{_csv_escape(str(to_val))}\n"
                        )
                    csv_content = buf.getvalue()

                    safe_rel = rel_type.replace(" ", "_").replace("/", "_")
                    csv_path = _write_csv_file(
                        f"rels_{safe_rel}_{from_label}_{to_label}.csv",
                        csv_content,
                    )

                    query = (
                        f"LOAD CSV FROM '{csv_path}' WITH HEADER AS row\n"
                        f"MATCH (a:{from_label} {{{from_key}: row.from_val}}), "
                        f"(b:{to_label} {{{to_key}: row.to_val}})\n"
                        f"{rel_verb} (a)-[:{rel_type}]->(b)"
                    )
                    rel_work_items.append((query, None, len(rels_list)))

                rel_prep_elapsed = _time.monotonic() - rel_start
                total_rel_rows = sum(w[2] for w in rel_work_items)

                logger.info(
                    f"CSV rel import: {len(rel_work_items)} patterns, "
                    f"{total_rel_rows} rels (prep={rel_prep_elapsed:.2f}s)"
                )

                # ── Execute relationship LOAD CSV in parallel ──
                if not _run_work_items_parallel(rel_work_items, 16, "Rels"):
                    return False

                # Store deduped CALLS count for verification
                self._last_deduped_calls_count = _deduped_calls_count

            rel_elapsed = _time.monotonic() - rel_start
            csv_elapsed = _time.monotonic() - csv_start
            logger.info(
                f"CSV bulk import complete: {total_nodes} nodes ({node_elapsed:.1f}s) + "
                f"{total_rels} rels ({rel_elapsed:.1f}s) = {csv_elapsed:.1f}s total"
            )

            return True
        except Exception as e:
            logger.error(f"CSV bulk import failed: {e}")
            import traceback as _tb

            logger.error(f"Traceback:\n{_tb.format_exc()}")
            return False
        finally:
            # Clean up thread-local connections
            for conn in connections_to_close:
                try:
                    conn.close()
                except Exception:
                    pass
            # Clean up CSV files
            for f in csv_files:
                _delete_csv_file(f)

    # ------------------------------------------------------------------
    # Background flusher: overlaps DB I/O with CPU-bound parsing
    # ------------------------------------------------------------------

    def start_background_flusher(self, use_create: bool = False) -> None:
        """Start a daemon thread that periodically flushes nodes and relationships.

        This allows DB writes to overlap with CPU-bound work (e.g., AST parsing
        in Pass 1), reducing the time spent in the final flush_all (Pass 4).

        Args:
            use_create: If True, use CREATE instead of MERGE for faster inserts
                       (both nodes and relationships). Safe for full builds after clean_database().
        """
        if self._bg_flush_thread is not None and self._bg_flush_thread.is_alive():
            logger.warning("Background flusher already running, not starting another")
            return

        self._bg_flush_stop = False
        self._bg_flush_use_create = use_create
        # Enable CREATE mode for nodes globally when doing a full build.
        # This also affects _try_flush_nodes_async, _try_flush_relationships_async,
        # flush_relationships, and flush_all.
        self._use_create_nodes = use_create
        # Reset the shared node ID cache for this build
        with self._bg_node_id_cache_lock:
            self._bg_node_id_cache.clear()

        def _bg_flusher():
            """Background thread loop: flush nodes then relationships when enough data accumulates.

            Uses a PIPELINED approach: relationship flushing from cycle N runs
            concurrently with node flushing from cycle N+1.  This overlaps the
            two heaviest DB write operations, cutting effective flush time.

            Ordering guarantee: Within a cycle, nodes are ALWAYS flushed before
            their corresponding relationships.  Pipelining only overlaps rels
            from the PREVIOUS cycle (whose nodes are already in DB) with nodes
            from the CURRENT cycle.
            """
            import time as _time
            from concurrent.futures import Future, ThreadPoolExecutor

            min_batch = self._bg_flush_min_batch
            max_wait = self._bg_flush_max_wait
            poll_interval = self._bg_flush_interval
            logger.info(
                f"Background flusher started (poll={poll_interval}s, "
                f"min_batch={min_batch}, max_wait={max_wait}s, pipelined=True)"
            )
            total_nodes_flushed = 0
            total_rels_flushed = 0
            flush_count = 0
            last_flush_time = _time.monotonic()

            # Pipeline state: track an in-flight relationship flush future
            # so the next cycle's node flush can run concurrently.
            pending_rel_future: Future | None = None
            pending_rel_count = 0
            # Single-thread executor for pipelined rel flushing
            # (rel flush itself uses up to 16 parallel connections internally)
            rel_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="bg-rel-flush"
            )

            def _flush_rels_task(rel_buf, use_create, id_cache_snapshot):
                """Task to flush relationships in a separate thread."""
                self._flush_relationships_impl(
                    rel_buf,
                    use_create=use_create,
                    progress_callback=None,
                    node_id_cache=id_cache_snapshot,
                )

            try:
                while not self._bg_flush_stop:
                    _time.sleep(poll_interval)
                    if self._bg_flush_stop:
                        break

                    # Check buffer sizes without acquiring locks (approximate, may race)
                    # This avoids lock contention on every poll cycle
                    node_count = len(self.node_buffer)
                    rel_count = len(self.relationship_buffer)
                    total_pending = node_count + rel_count

                    if total_pending == 0:
                        continue

                    # Decide whether to flush now:
                    # 1. Enough data accumulated for efficient parallel execution
                    # 2. OR max wait time exceeded (prevent data from sitting too long)
                    time_since_flush = _time.monotonic() - last_flush_time
                    should_flush = (
                        total_pending >= min_batch or time_since_flush >= max_wait
                    )

                    if not should_flush:
                        continue

                    # Step 1: Grab and flush nodes first
                    node_buf = None
                    with self._node_buffer_lock:
                        if self.node_buffer:
                            node_buf = self.node_buffer
                            self.node_buffer = []

                    # Step 2: Grab relationships AFTER nodes are taken
                    # (any rels in the buffer now should reference nodes we already grabbed
                    #  or nodes already in the DB from previous flushes)
                    rel_buf = None
                    with self._relationship_buffer_lock:
                        if self.relationship_buffer:
                            rel_buf = self.relationship_buffer
                            self.relationship_buffer = []

                    if not node_buf and not rel_buf:
                        continue

                    # Acquire flush lock (blocking - wait for any in-progress flush)
                    acquired = self._flush_lock.acquire(timeout=5.0)
                    if not acquired:
                        # Put buffers back if we couldn't get the lock
                        if node_buf:
                            with self._node_buffer_lock:
                                self.node_buffer = node_buf + self.node_buffer
                        if rel_buf:
                            with self._relationship_buffer_lock:
                                self.relationship_buffer = (
                                    rel_buf + self.relationship_buffer
                                )
                        continue

                    try:
                        flush_count += 1
                        n_nodes = len(node_buf) if node_buf else 0
                        n_rels = len(rel_buf) if rel_buf else 0
                        flush_start = _time.monotonic()

                        # Wait for any in-flight pipelined rel flush from the PREVIOUS cycle
                        # before we flush the current cycle's nodes.  This ensures the
                        # previous rels are fully written before we release the flush lock.
                        if pending_rel_future is not None:
                            try:
                                pending_rel_future.result(timeout=120)
                                total_rels_flushed += pending_rel_count
                            except Exception as e:
                                logger.error(f"Pipelined rel flush error: {e}")
                            pending_rel_future = None
                            pending_rel_count = 0

                        # Flush nodes (relationships need their endpoints)
                        # Use CREATE in full-build mode for faster inserts.
                        # Collect node IDs so flush_all() can skip supplemental cache queries.
                        if node_buf:
                            bg_ids = self._flush_nodes_impl(
                                node_buf,
                                use_create=self._use_create_nodes,
                                progress_callback=None,
                                collect_node_ids=True,
                            )
                            if bg_ids:
                                with self._bg_node_id_cache_lock:
                                    self._bg_node_id_cache.update(bg_ids)
                            total_nodes_flushed += n_nodes

                        # Pipeline: start relationship flush in a background thread.
                        # The rels reference nodes from THIS cycle (just flushed above)
                        # or earlier cycles, so all endpoints exist in DB.
                        # We DON'T wait for this to complete — the next cycle's node
                        # flush can proceed concurrently.
                        if rel_buf:
                            # Snapshot node ID cache for the rel flush thread
                            with self._bg_node_id_cache_lock:
                                id_cache_snapshot = (
                                    dict(self._bg_node_id_cache)
                                    if self._bg_node_id_cache
                                    else None
                                )
                            pending_rel_future = rel_executor.submit(
                                _flush_rels_task,
                                rel_buf,
                                self._bg_flush_use_create,
                                id_cache_snapshot,
                            )
                            pending_rel_count = n_rels

                        elapsed = _time.monotonic() - flush_start
                        last_flush_time = _time.monotonic()
                        logger.info(
                            f"Background flush #{flush_count}: "
                            f"{n_nodes} nodes + {n_rels} rels(pipelined) in {elapsed:.1f}s "
                            f"(cumulative: {total_nodes_flushed} nodes, {total_rels_flushed} rels)"
                        )
                    except Exception as e:
                        logger.error(f"Background flush error: {e}")
                        import traceback

                        logger.error(traceback.format_exc())
                    finally:
                        self._flush_lock.release()

                # Drain: wait for any final pipelined rel flush before exiting
                if pending_rel_future is not None:
                    try:
                        pending_rel_future.result(timeout=120)
                        total_rels_flushed += pending_rel_count
                    except Exception as e:
                        logger.error(f"Final pipelined rel flush error: {e}")

            finally:
                rel_executor.shutdown(wait=True)

            logger.info(
                f"Background flusher stopped. "
                f"Total: {total_nodes_flushed} nodes + {total_rels_flushed} rels "
                f"in {flush_count} flushes"
            )

        self._bg_flush_thread = threading.Thread(
            target=_bg_flusher, name="bg-flusher", daemon=True
        )
        self._bg_flush_thread.start()

    def stop_background_flusher(self) -> None:
        """Signal the background flusher to stop and wait for it to finish.

        Note: Does NOT reset _use_create_nodes here because flush_all (Pass 4)
        may still need it after the background flusher is stopped.
        _use_create_nodes is reset by the next start_background_flusher() call.
        """
        if self._bg_flush_thread is None or not self._bg_flush_thread.is_alive():
            return
        self._bg_flush_stop = True
        self._bg_flush_thread.join(timeout=30.0)
        if self._bg_flush_thread.is_alive():
            logger.warning("Background flusher did not stop within 30s")
        self._bg_flush_thread = None

    def _flush_relationships_impl(
        self,
        buffer_to_flush: list,
        use_create: bool = False,
        progress_callback=None,
        node_id_cache: dict | None = None,
    ) -> None:
        """Internal implementation of flush_relationships (must be called with _flush_lock held).

        Uses parallel connections for bulk relationship flushing when buffer is large.
        In IN_MEMORY_ANALYTICAL mode, Memgraph supports concurrent writes, so we can
        use multiple connections to flush relationship batches in parallel.

        Args:
            buffer_to_flush: The buffer to flush (already swapped out)
            use_create: If True, use CREATE instead of MERGE for faster inserts
                       (safe when relationships are guaranteed new, e.g. full builds)
            progress_callback: Optional callback function(current, total) to report progress
            node_id_cache: Optional dict mapping (label, key, value) -> internal node ID.
                          When provided, uses id-based MATCH for O(1) lookups instead of
                          property index lookups. Falls back to property MATCH on cache miss.
        """
        import os
        import threading
        from concurrent.futures import ThreadPoolExecutor, as_completed

        buffer_size = len(buffer_to_flush)

        # Deduplicate relationships when using CREATE to avoid duplicate edges.
        # MERGE handles dedup at the DB level, but CREATE does not.
        if use_create:
            seen: set[tuple] = set()
            deduped: list = []
            for item in buffer_to_flush:
                from_node, rel_type, to_node, _props = item
                key = (from_node[2], rel_type, to_node[2])
                if key not in seen:
                    seen.add(key)
                    deduped.append(item)
            removed = buffer_size - len(deduped)
            if removed > 0:
                logger.info(
                    f"Deduplicated relationships: {buffer_size} -> {len(deduped)} (removed {removed} duplicates)"
                )
                buffer_to_flush = deduped
                buffer_size = len(buffer_to_flush)

        rels_by_pattern = defaultdict(list)

        if node_id_cache:
            # ID-based path: resolve node IDs from cache, separate into id-resolved and fallback
            id_rels_by_pattern = defaultdict(list)
            fallback_rels_by_pattern = defaultdict(list)

            for from_node, rel_type, to_node, props in buffer_to_flush:
                from_label, from_key, from_val = from_node
                to_label, to_key, to_val = to_node
                from_id = node_id_cache.get((from_label, from_key, from_val))
                to_id = node_id_cache.get((to_label, to_key, to_val))

                if from_id is not None and to_id is not None:
                    pattern = (
                        rel_type,
                    )  # ID-based queries don't need label/key in pattern
                    id_rels_by_pattern[pattern].append(
                        {"from_id": from_id, "to_id": to_id, "props": props or {}}
                    )
                else:
                    # Fallback to property-based MATCH
                    pattern = (from_label, from_key, rel_type, to_label, to_key)
                    fallback_rels_by_pattern[pattern].append(
                        {"from_val": from_val, "to_val": to_val, "props": props or {}}
                    )

            id_resolved = sum(len(v) for v in id_rels_by_pattern.values())
            fallback_count = sum(len(v) for v in fallback_rels_by_pattern.values())
            if fallback_count > 0:
                logger.info(
                    f"Node ID cache: {id_resolved} resolved, {fallback_count} fallback to property MATCH"
                )
            else:
                logger.info(
                    f"Node ID cache: all {id_resolved} relationships resolved by ID"
                )

            rels_by_pattern = fallback_rels_by_pattern
        else:
            id_rels_by_pattern = {}
            for from_node, rel_type, to_node, props in buffer_to_flush:
                pattern = (from_node[0], from_node[1], rel_type, to_node[0], to_node[1])
                rels_by_pattern[pattern].append(
                    {
                        "from_val": from_node[2],
                        "to_val": to_node[2],
                        "props": props or {},
                    }
                )

        # Choose CREATE or MERGE based on parameter
        rel_verb = "CREATE" if use_create else "MERGE"

        # Build all work items: list of (query_string, sub_batch_params)
        work_items: list[tuple[str, list[dict]]] = []

        # Batch size per UNWIND query.  For full builds (use_create=True) we use
        # a larger batch size to reduce per-query overhead (parsing, planning,
        # network round-trips) while keeping enough work items for parallel
        # distribution across 16 connections.  5000 per batch ≈ 16-40 work
        # items for typical 80-200K relationship counts.
        if use_create:
            parallel_sub_batch_size = 5000
        else:
            parallel_sub_batch_size = 2000

        # Build work items for ID-resolved relationships (fast path)
        for pattern, params_list in id_rels_by_pattern.items():
            rel_type = pattern[0]
            has_props = any(p["props"] for p in params_list)

            query = (
                f"MATCH (a) WHERE id(a) = row.from_id "
                f"MATCH (b) WHERE id(b) = row.to_id\n"
                f"{rel_verb} (a)-[r:{rel_type}]->(b)"
            )
            if has_props:
                query += "\nSET r += row.props"

            for i in range(0, len(params_list), parallel_sub_batch_size):
                sub_batch = params_list[i : i + parallel_sub_batch_size]
                work_items.append((query, sub_batch))

        # Build work items for fallback (property-based) relationships
        for pattern, params_list in rels_by_pattern.items():
            from_label, from_key, rel_type, to_label, to_key = pattern
            has_props = any(p["props"] for p in params_list)

            query = (
                f"MATCH (a:{from_label} {{{from_key}: row.from_val}}), "
                f"(b:{to_label} {{{to_key}: row.to_val}})\n"
                f"{rel_verb} (a)-[r:{rel_type}]->(b)"
            )
            if has_props:
                query += "\nSET r += row.props"

            for i in range(0, len(params_list), parallel_sub_batch_size):
                sub_batch = params_list[i : i + parallel_sub_batch_size]
                work_items.append((query, sub_batch))

        total_items_count = sum(len(wb[1]) for wb in work_items)
        logger.info(
            f"Prepared {len(work_items)} work items for {total_items_count} relationships"
        )

        # Determine parallelism level
        # Use multiple connections for flushes >2K rels (lowered from 5K to ensure
        # background flusher batches also benefit from parallel writes).
        # Memgraph IN_MEMORY_ANALYTICAL mode supports concurrent writes;
        # more workers = more throughput on multi-core machines.
        num_workers = 1
        if buffer_size > 2000 and len(work_items) > 1:
            num_workers = min(16, os.cpu_count() or 4, len(work_items))

        if num_workers <= 1:
            # Sequential execution (small buffer or single work item)
            processed_items = 0
            for query, sub_batch in work_items:
                self._execute_batch(query, sub_batch)
                processed_items += len(sub_batch)
                if progress_callback:
                    progress_callback(processed_items, buffer_size)

            logger.info(
                f"Flushed {buffer_size} relationships in {len(work_items)} batches (sequential)."
            )
            return

        # Parallel execution using multiple connections
        logger.info(
            f"Flushing {buffer_size} relationships in parallel with {num_workers} connections..."
        )

        # Thread-local connections for each worker
        thread_local = threading.local()
        connections_to_close: list[mgclient.Connection] = []
        connections_lock = threading.Lock()

        def get_thread_connection() -> mgclient.Connection:
            """Get or create a connection for this thread."""
            conn = getattr(thread_local, "conn", None)
            if conn is None:
                conn = mgclient.connect(host=self._host, port=self._port)
                conn.autocommit = True
                thread_local.conn = conn
                with connections_lock:
                    connections_to_close.append(conn)
            return conn

        def execute_work_item(item: tuple[str, list[dict]]) -> int:
            """Execute a single work item using thread-local connection."""
            query, sub_batch = item
            conn = get_thread_connection()
            batch_query = f"UNWIND $batch AS row\n{query}"
            cursor = None
            try:
                cursor = conn.cursor()
                cursor.execute(batch_query, {"batch": sub_batch})
                return len(sub_batch)
            except Exception as e:
                error_msg = str(e).lower()
                if "already exists" in error_msg:
                    return len(sub_batch)
                logger.error(f"!!! Parallel batch Cypher Error: {e}")
                logger.error(f"    Query: {query}")
                return 0
            finally:
                if cursor:
                    cursor.close()

        # Execute work items in parallel
        processed_items = 0
        try:
            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                futures = {
                    executor.submit(execute_work_item, item): item
                    for item in work_items
                }
                for future in as_completed(futures):
                    count = future.result()
                    processed_items += count
                    if progress_callback:
                        progress_callback(processed_items, buffer_size)
        finally:
            # Clean up all thread-local connections
            for conn in connections_to_close:
                try:
                    conn.close()
                except Exception:
                    pass

        logger.info(
            f"Flushed {buffer_size} relationships in {len(work_items)} batches "
            f"using {num_workers} parallel connections."
        )

    def flush_nodes(self, use_create: bool = False, progress_callback=None) -> None:
        """Flushes the buffered nodes to the database with optimized batching.

        Thread-safe: Uses locks to prevent concurrent flush operations and buffer corruption.

        Args:
            use_create: If True, use CREATE instead of MERGE for faster inserts.
                       Use this when nodes are guaranteed to be new (e.g., after deletion).
            progress_callback: Optional callback function(current, total) to report progress

        Optimization strategies:
        1. Groups nodes by label for efficient batch processing
        2. Uses larger sub-batches within each label group
        3. Minimizes query overhead by batching property updates
        4. Reduced throttling frequency for IN_MEMORY_ANALYTICAL mode
        5. Optional CREATE mode for faster inserts when nodes are new
        """
        # Step 1: Atomically swap buffer (without holding flush_lock)
        with self._node_buffer_lock:
            if not self.node_buffer:
                return
            # Take ownership of the buffer and replace with empty one
            buffer_to_flush = self.node_buffer
            self.node_buffer = []

        # Step 2: Acquire flush lock and perform flush
        # This is the critical fix: we only acquire flush_lock AFTER releasing buffer_lock
        with self._flush_lock:
            self._flush_nodes_impl(buffer_to_flush, use_create, progress_callback)

    def _flush_nodes_impl(
        self,
        buffer_to_flush: list,
        use_create: bool = False,
        progress_callback=None,
        collect_node_ids: bool = False,
    ) -> dict | None:
        """Internal implementation of flush_nodes (must be called with _flush_lock held).

        Uses parallel connections for bulk node flushing when buffer is large.
        In IN_MEMORY_ANALYTICAL mode, Memgraph supports concurrent writes, so we can
        use multiple connections to flush node batches in parallel.

        Args:
            buffer_to_flush: The buffer to flush (already swapped out)
            use_create: If True, use CREATE instead of MERGE for faster inserts
            progress_callback: Optional callback function(current, total) to report progress
            collect_node_ids: If True, return a dict mapping (label, id_key, id_val) -> db_node_id.
                             Builds the node ID cache as a side effect of flushing via RETURN clause,
                             eliminating a separate full-scan DB query for cache building.

        Returns:
            If collect_node_ids is True, returns dict mapping (label, id_key, id_val) -> int.
            Otherwise returns None.
        """
        import os
        import threading
        from concurrent.futures import ThreadPoolExecutor, as_completed

        buffer_size = len(buffer_to_flush)
        nodes_by_label = defaultdict(list)
        for label, props in buffer_to_flush:
            nodes_by_label[label].append(props)

        skipped_total = 0

        # Build all work items: list of (query_string, sub_batch_params, label, id_key)
        # The label and id_key metadata are needed when collect_node_ids is True
        # Each work item: (query, sub_batch, label, id_key, merge_fallback_query_or_None)
        work_items: list[tuple[str, list[dict], str, str, str | None]] = []

        # Batch size per UNWIND query.  Full builds use a larger batch size to
        # reduce per-query overhead; incremental builds use 2 000 for parallelism.
        if use_create:
            parallel_sub_batch_size = 5000
        else:
            parallel_sub_batch_size = 2000

        for label, props_list in nodes_by_label.items():
            if not props_list:
                continue
            id_key = self.unique_constraints.get(label)
            if not id_key:
                logger.warning(
                    f"No unique constraint defined for label '{label}'. Skipping flush."
                )
                skipped_total += len(props_list)
                continue

            # Prepare all rows for this label, deduplicating by id_key
            # (last occurrence wins, matching MERGE semantics for parallel safety)
            deduped: dict[Any, dict[str, Any]] = {}
            for props in props_list:
                if id_key not in props:
                    logger.warning(
                        "Skipping {} node missing required '{}' property: {}",
                        label,
                        id_key,
                        props,
                    )
                    skipped_total += 1
                    continue
                id_val = props[id_key]
                row_props = {k: v for k, v in props.items() if k != id_key}
                deduped[id_val] = {"id": id_val, "props": row_props}
            batch_rows = list(deduped.values())

            if not batch_rows:
                continue

            # Build query once per label (CREATE for full builds, MERGE for incremental)
            verb = "CREATE" if use_create else "MERGE"
            query = f"{verb} (n:{label} {{{id_key}: row.id}})\nSET n += row.props"
            # Build MERGE fallback query when using CREATE.
            # If a CREATE batch fails with "already exists" (some nodes were flushed
            # previously by the background flusher), we retry with MERGE to ensure
            # all nodes in the batch are created correctly.
            merge_fallback = (
                f"MERGE (n:{label} {{{id_key}: row.id}})\nSET n += row.props"
                if use_create
                else None
            )
            # When collecting node IDs, add RETURN clause to get DB-assigned node IDs
            # as a side effect of the flush operation (eliminates separate cache query)
            if collect_node_ids:
                query += "\nRETURN id(n) AS nid, row.id AS key_val"
                if merge_fallback:
                    merge_fallback += "\nRETURN id(n) AS nid, row.id AS key_val"

            for i in range(0, len(batch_rows), parallel_sub_batch_size):
                sub_batch = batch_rows[i : i + parallel_sub_batch_size]
                work_items.append((query, sub_batch, label, id_key, merge_fallback))

        total_items_count = sum(len(wb[1]) for wb in work_items)
        logger.info(
            f"Prepared {len(work_items)} node work items for {total_items_count} nodes"
        )

        # Node ID cache to build during flush (when collect_node_ids=True)
        node_id_cache: dict | None = {} if collect_node_ids else None

        # Determine parallelism level
        # Use multiple connections for flushes >2K nodes (lowered from 5K to ensure
        # background flusher batches also benefit from parallel writes).
        # Memgraph IN_MEMORY_ANALYTICAL mode supports concurrent writes.
        num_workers = 1
        if buffer_size > 2000 and len(work_items) > 1:
            num_workers = min(16, os.cpu_count() or 4, len(work_items))

        if num_workers <= 1:
            # Sequential execution (small buffer or single work item)
            processed_items = 0
            create_fallback_count = 0
            for query, sub_batch, label, id_key, merge_fallback in work_items:
                if collect_node_ids:
                    # Use _execute_batch_with_return to collect node IDs
                    try:
                        results = self._execute_batch_with_return(query, sub_batch)
                        for row in results:
                            node_id_cache[(label, id_key, row["key_val"])] = row["nid"]
                    except Exception as e:
                        error_msg = str(e).lower()
                        if "already exists" in error_msg and merge_fallback:
                            # CREATE failed due to duplicate — retry with MERGE fallback
                            create_fallback_count += 1
                            try:
                                results = self._execute_batch_with_return(
                                    merge_fallback, sub_batch
                                )
                                for row in results:
                                    node_id_cache[(label, id_key, row["key_val"])] = (
                                        row["nid"]
                                    )
                            except Exception as e2:
                                logger.warning(
                                    f"MERGE fallback also failed for {label}: {e2}"
                                )
                                fallback_query = merge_fallback.rsplit("\nRETURN", 1)[0]
                                self._execute_batch(fallback_query, sub_batch)
                        else:
                            logger.warning(
                                f"Failed to collect node IDs for {label}: {e}"
                            )
                            # Fall back to write-only (strip RETURN clause)
                            fallback_query = (merge_fallback or query).rsplit(
                                "\nRETURN", 1
                            )[0]
                            self._execute_batch(fallback_query, sub_batch)
                else:
                    if merge_fallback:
                        # Try CREATE first; if it fails with "already exists", retry with MERGE
                        if not self._execute_batch_create_with_fallback(
                            query, sub_batch, merge_fallback
                        ):
                            create_fallback_count += 1
                    else:
                        self._execute_batch(query, sub_batch)
                processed_items += len(sub_batch)
                if progress_callback:
                    progress_callback(processed_items, buffer_size)
            if create_fallback_count:
                logger.info(
                    f"CREATE->MERGE fallback triggered for {create_fallback_count} batches"
                )

            logger.info(
                f"Flushed {total_items_count} of {buffer_size} buffered nodes in {len(work_items)} batches (sequential)."
            )
            if collect_node_ids:
                logger.info(f"Collected {len(node_id_cache)} node IDs during flush")
            if skipped_total:
                logger.info(
                    "Skipped {} buffered nodes due to missing identifiers or constraints.",
                    skipped_total,
                )
            return node_id_cache

        # Parallel execution using multiple connections
        logger.info(
            f"Flushing {buffer_size} nodes in parallel with {num_workers} connections..."
        )

        # Thread-local connections for each worker
        thread_local = threading.local()
        connections_to_close: list[mgclient.Connection] = []
        connections_lock = threading.Lock()

        def get_thread_connection() -> mgclient.Connection:
            """Get or create a connection for this thread."""
            conn = getattr(thread_local, "conn", None)
            if conn is None:
                conn = mgclient.connect(host=self._host, port=self._port)
                conn.autocommit = True
                thread_local.conn = conn
                with connections_lock:
                    connections_to_close.append(conn)
            return conn

        # Thread-safe lock for collecting node IDs from parallel workers
        cache_lock = threading.Lock() if collect_node_ids else None
        # Counter for CREATE->MERGE fallbacks in parallel mode
        parallel_fallback_count = [0]
        fallback_lock = threading.Lock()

        def _run_query_on_conn(conn, query_str, sub_batch, label, id_key):
            """Execute a query on a given connection, optionally collecting node IDs.

            Returns len(sub_batch) on success.
            Raises on error (caller handles retry logic).
            """
            batch_query = f"UNWIND $batch AS row\n{query_str}"
            cursor = conn.cursor()
            try:
                cursor.execute(batch_query, {"batch": sub_batch})
                if collect_node_ids and cursor.description:
                    column_names = [desc.name for desc in cursor.description]
                    id_entries = []
                    for row_data in cursor.fetchall():
                        row = dict(zip(column_names, row_data))
                        id_entries.append((label, id_key, row["key_val"], row["nid"]))
                    if id_entries:
                        with cache_lock:
                            for entry in id_entries:
                                node_id_cache[entry[:3]] = entry[3]
                return len(sub_batch)
            finally:
                cursor.close()

        def execute_work_item(
            item: tuple[str, list[dict], str, str, str | None],
        ) -> int:
            """Execute a single work item using thread-local connection.

            When merge_fallback is provided (use_create mode), tries CREATE first
            and falls back to MERGE on 'already exists' constraint violations.
            When collect_node_ids is True, also collects returned node IDs
            into the shared node_id_cache dict (thread-safe via cache_lock).
            """
            query, sub_batch, label, id_key, merge_fallback = item
            conn = get_thread_connection()
            try:
                return _run_query_on_conn(conn, query, sub_batch, label, id_key)
            except Exception as e:
                error_msg = str(e).lower()
                if "already exists" in error_msg and merge_fallback:
                    # CREATE failed — retry with MERGE fallback
                    with fallback_lock:
                        parallel_fallback_count[0] += 1
                    try:
                        return _run_query_on_conn(
                            conn, merge_fallback, sub_batch, label, id_key
                        )
                    except Exception as e2:
                        logger.error(f"!!! MERGE fallback also failed: {e2}")
                        return 0
                elif "already exists" in error_msg:
                    return len(sub_batch)
                logger.error(f"!!! Parallel node batch Cypher Error: {e}")
                logger.error(f"    Query: {query}")
                return 0

        # Execute work items in parallel
        processed_items = 0
        try:
            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                futures = {
                    executor.submit(execute_work_item, item): item
                    for item in work_items
                }
                for future in as_completed(futures):
                    count = future.result()
                    processed_items += count
                    if progress_callback:
                        progress_callback(processed_items, buffer_size)
        finally:
            # Clean up all thread-local connections
            for conn in connections_to_close:
                try:
                    conn.close()
                except Exception:
                    pass

        logger.info(
            f"Flushed {total_items_count} of {buffer_size} buffered nodes in {len(work_items)} batches "
            f"using {num_workers} parallel connections."
        )
        if parallel_fallback_count[0]:
            logger.info(
                f"CREATE->MERGE fallback triggered for {parallel_fallback_count[0]} of {len(work_items)} batches (parallel)"
            )
        if collect_node_ids:
            logger.info(f"Collected {len(node_id_cache)} node IDs during flush")
        if skipped_total:
            logger.info(
                "Skipped {} buffered nodes due to missing identifiers or constraints.",
                skipped_total,
            )
        return node_id_cache

    def flush_relationships(
        self, use_create: bool = False, progress_callback=None
    ) -> None:
        """Flushes the buffered relationships to the database with optimized batching.

        Thread-safe: Uses locks to prevent concurrent flush operations and buffer corruption.

        Args:
            use_create: If True, use CREATE instead of MERGE for faster inserts
                       (safe for full builds where no duplicates exist)
            progress_callback: Optional callback function(current, total) to report progress
        """
        # Step 1: Atomically swap buffer (without holding flush_lock)
        with self._relationship_buffer_lock:
            if not self.relationship_buffer:
                return
            # Take ownership of the buffer and replace with empty one
            buffer_to_flush = self.relationship_buffer
            self.relationship_buffer = []

        # Step 2: Acquire flush lock and perform flush
        # This is the critical fix: we only acquire flush_lock AFTER releasing buffer_lock
        with self._flush_lock:
            # CRITICAL: Flush nodes first so relationships can find their endpoints
            # Without this, MATCH queries fail because nodes haven't been written yet
            # Collect node IDs for the shared cache.
            with self._node_buffer_lock:
                if self.node_buffer:
                    node_buffer = self.node_buffer
                    self.node_buffer = []
                    bg_ids = self._flush_nodes_impl(
                        node_buffer,
                        use_create=self._use_create_nodes,
                        progress_callback=None,
                        collect_node_ids=True,
                    )
                    if bg_ids:
                        with self._bg_node_id_cache_lock:
                            self._bg_node_id_cache.update(bg_ids)

            # Use node ID cache for fast ID-based MATCH when available
            with self._bg_node_id_cache_lock:
                id_cache_snapshot = (
                    dict(self._bg_node_id_cache) if self._bg_node_id_cache else None
                )
            self._flush_relationships_impl(
                buffer_to_flush,
                use_create=use_create,
                progress_callback=progress_callback,
                node_id_cache=id_cache_snapshot,
            )

    def flush_all(self, use_create_rels: bool = False, progress_callback=None) -> None:
        """Flush all pending writes to database with optional progress reporting.

        Tries CSV bulk import first (faster for large buffers), then falls
        back to the traditional UNWIND batch approach if CSV fails.

        Args:
            use_create_rels: If True, use CREATE instead of MERGE for relationships
                            (faster for full builds where no duplicate rels exist).
                            Nodes always use MERGE to avoid unique constraint violations.
            progress_callback: Optional callback function(current, total) to report progress
        """
        try:
            import time as _time

            # CRITICAL FIX: Hold flush_lock for the entire operation to ensure atomicity
            # This prevents other threads from interleaving flushes during this operation
            with self._flush_lock:
                flush_all_start = _time.monotonic()

                # Atomically swap both buffers under their respective locks
                with self._node_buffer_lock:
                    total_nodes = len(self.node_buffer)
                    node_buffer_to_flush = self.node_buffer
                    self.node_buffer = []

                with self._relationship_buffer_lock:
                    total_rels = len(self.relationship_buffer)
                    rel_buffer_to_flush = self.relationship_buffer
                    self.relationship_buffer = []

                total_items = total_nodes + total_rels

                if total_items == 0:
                    return

                logger.info(
                    f"flush_all: {total_nodes} nodes + {total_rels} relationships ({total_items} total)"
                )

                # ----------------------------------------------------------
                # Try CSV bulk import first (significantly faster for large
                # buffers because LOAD CSV bypasses Bolt parameter
                # serialization and uses Memgraph's optimised streaming
                # import path).  Falls back to UNWIND on failure.
                # ----------------------------------------------------------
                csv_threshold = 1000  # Use CSV for buffers > 1K items
                if total_items >= csv_threshold:
                    logger.info("Attempting CSV bulk import...")
                    csv_ok = self._flush_all_csv(
                        node_buffer_to_flush,
                        rel_buffer_to_flush,
                        use_create_nodes=self._use_create_nodes,
                        use_create_rels=use_create_rels,
                        progress_callback=progress_callback,
                    )
                    if csv_ok:
                        self._query_cache.invalidate()
                        csv_elapsed = _time.monotonic() - flush_all_start
                        logger.info(
                            f"flush_all (CSV) complete: {total_nodes} nodes + "
                            f"{total_rels} rels = {csv_elapsed:.1f}s total"
                        )
                        return
                    else:
                        logger.warning(
                            "CSV bulk import failed, falling back to UNWIND approach"
                        )

                # ----------------------------------------------------------
                # UNWIND batch approach (fallback / small buffers)
                # ----------------------------------------------------------
                # Use a mutable list to avoid 'nonlocal' scoping issues
                # This is more robust than using a plain variable with nested functions
                processed_state = [0]  # processed_state[0] holds the current progress

                # Flush nodes with progress (directly using buffer, not calling flush_nodes)
                # When relationships need flushing, collect node IDs during the node flush
                # (via RETURN clause in MERGE queries) to build the cache incrementally,
                # eliminating a separate full-scan DB query for cache building.
                need_id_cache = total_rels > 0
                node_flush_start = _time.monotonic()
                node_id_cache = None
                if total_nodes > 0:

                    def node_progress(current, total):
                        if total > 0:
                            processed_state[0] = int((current / total) * total_nodes)
                            if progress_callback:
                                progress_callback(processed_state[0], total_items)

                    node_id_cache = self._flush_nodes_impl(
                        node_buffer_to_flush,
                        use_create=self._use_create_nodes,
                        progress_callback=node_progress,
                        collect_node_ids=need_id_cache,
                    )
                node_flush_elapsed = _time.monotonic() - node_flush_start

                # Build node ID cache by combining:
                # 1. IDs from the current node flush (node_id_cache from above)
                # 2. IDs collected by the background flusher (_bg_node_id_cache)
                # 3. Supplemental DB lookup for any remaining missing endpoints
                cache_elapsed = 0.0
                if need_id_cache:
                    try:
                        cache_start = _time.monotonic()
                        if node_id_cache is None:
                            node_id_cache = {}

                        # Merge in background flusher's node ID cache (collected
                        # during Pass 1-3 as the bg thread wrote nodes to DB).
                        # This eliminates most/all supplemental DB queries.
                        bg_cache_count = 0
                        with self._bg_node_id_cache_lock:
                            if self._bg_node_id_cache:
                                bg_cache_count = len(self._bg_node_id_cache)
                                # bg cache entries not already in node_id_cache
                                for key, nid in self._bg_node_id_cache.items():
                                    if key not in node_id_cache:
                                        node_id_cache[key] = nid

                        # Collect unique endpoint keys from the relationship buffer
                        # that are NOT already in the cache from node flushing + bg cache
                        missing_by_label: dict[str, set] = defaultdict(set)
                        for from_node, rel_type, to_node, props in rel_buffer_to_flush:
                            for node_spec in (from_node, to_node):
                                n_label, n_key, n_val = node_spec
                                if (n_label, n_key, n_val) not in node_id_cache:
                                    missing_by_label[n_label].add(n_val)

                        # Fetch IDs only for missing endpoints (batched by label)
                        supplemental_count = 0
                        total_missing = sum(len(v) for v in missing_by_label.values())
                        if total_missing > 0:
                            logger.info(
                                f"Supplemental cache lookup: {total_missing} missing endpoints across {len(missing_by_label)} labels"
                            )
                            for label, missing_vals in missing_by_label.items():
                                if not missing_vals:
                                    continue
                                id_key = self.unique_constraints.get(
                                    label, "qualified_name"
                                )
                                missing_list = list(missing_vals)
                                sup_batch_size = 10000
                                for i in range(0, len(missing_list), sup_batch_size):
                                    batch = missing_list[i : i + sup_batch_size]
                                    try:
                                        results = self._execute_query(
                                            f"UNWIND $vals AS v "
                                            f"MATCH (n:{label} {{{id_key}: v}}) "
                                            f"RETURN id(n) AS nid, v AS key_val",
                                            {"vals": batch},
                                        )
                                        for row in results:
                                            node_id_cache[
                                                (label, id_key, row["key_val"])
                                            ] = row["nid"]
                                            supplemental_count += 1
                                    except Exception as e:
                                        logger.warning(
                                            f"Supplemental cache query failed for {label}: {e}"
                                        )

                        from_flush = (
                            len(node_id_cache) - supplemental_count - bg_cache_count
                        )
                        cache_elapsed = _time.monotonic() - cache_start
                        logger.info(
                            f"Node ID cache: {from_flush} from flush + "
                            f"{bg_cache_count} from bg flusher + "
                            f"{supplemental_count} supplemental = {len(node_id_cache)} total "
                            f"in {cache_elapsed:.2f}s"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to build node ID cache, falling back to property MATCH: {e}"
                        )
                        node_id_cache = None

                # Flush relationships with progress (directly using buffer, not calling flush_relationships)
                rel_flush_start = _time.monotonic()
                if total_rels > 0:
                    # Capture the base progress (0 if no nodes were flushed)
                    base = processed_state[0]

                    def rel_progress(current, total):
                        if total > 0:
                            processed_state[0] = base + int(
                                (current / total) * total_rels
                            )
                            if progress_callback:
                                progress_callback(processed_state[0], total_items)

                    self._flush_relationships_impl(
                        rel_buffer_to_flush,
                        use_create=use_create_rels,
                        progress_callback=rel_progress,
                        node_id_cache=node_id_cache,
                    )
                rel_flush_elapsed = _time.monotonic() - rel_flush_start

                # Invalidate cache after bulk writes
                self._query_cache.invalidate()
                flush_all_elapsed = _time.monotonic() - flush_all_start
                logger.info(
                    f"flush_all complete: {total_nodes} nodes ({node_flush_elapsed:.1f}s) + "
                    f"{total_rels} rels ({rel_flush_elapsed:.1f}s) + "
                    f"ID cache ({cache_elapsed:.1f}s) = {flush_all_elapsed:.1f}s total"
                )
        except Exception as e:
            logger.critical(f"!!! CRITICAL ERROR in flush_all: {e}")
            import traceback

            logger.critical(f"Traceback:\n{traceback.format_exc()}")
            raise

    def fetch_all(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        use_cache: bool = False,
        cache_ttl: float | None = None,
    ) -> list:
        """
        Execute a query and fetch all results with optional caching.

        Args:
            query: Cypher query string
            params: Query parameters
            use_cache: Whether to cache results (recommended for repeated read queries)
            cache_ttl: Custom TTL in seconds (default: 60s)

        Returns:
            List of result dictionaries
        """
        logger.debug(f"Executing fetch query: {query} with params: {params}")
        return self._execute_query(
            query, params, use_cache=use_cache, cache_ttl=cache_ttl
        )

    def invalidate_cache(self, pattern: str | None = None) -> int:
        """
        Manually invalidate cache entries.

        Args:
            pattern: Optional pattern to match (currently clears all if provided)

        Returns:
            Number of entries invalidated
        """
        return self._query_cache.invalidate(pattern)

    def get_children(
        self,
        identifier: str,
        identifier_type: str = "auto",
        depth: int = 1,
        project_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get all children of a node (File or Class) with configurable depth.

        This is a unified interface that supports querying children for:
        - File nodes: returns Function/Class nodes (for source code files with qualified_name)
        - Class nodes: returns Method nodes

        Args:
            identifier: Identifier to find the parent node:
                - For File: file path (relative path) or qualified_name
                - For Class: class qualified_name
            identifier_type: Type of identifier, one of:
                - "auto": Automatically detect (tries File path, then File QN, then Class QN)
                - "file": Treat as file path or qualified_name
                - "class": Treat as class qualified_name
            depth: Maximum depth to traverse (default: 1)
                - depth=1: Direct children only
                - depth=2: Children and grandchildren, etc.
                - Use depth=-1 for unlimited depth (not recommended for large graphs)
            project_name: Optional project name for proper isolation (recommended when using path-based identifiers)

        Returns:
            List of child nodes with their properties, each containing:
            - All node properties (qualified_name, name, start_line, etc.)
            - type: List of labels for the node (e.g., ['Function'])
            - parent_type: Type of parent node
            - parent_identifier: Original identifier used
            - depth: Depth level at which this node was found

        Examples:
            >>> # Query File children (Function/Class)
            >>> children = ingestor.get_children("src/utils.py", "file", project_name="my_project")
            >>>
            >>> # Query File children by qualified_name
            >>> children = ingestor.get_children("project.src.utils", "file")
            >>>
            >>> # Query Class children (Method) with depth=1
            >>> children = ingestor.get_children("project.src.utils.MyClass", "class")
            >>>
            >>> # Auto-detect node type
            >>> children = ingestor.get_children("src/utils.py", project_name="my_project")
            >>>
            >>> # Query with depth=2 (children and grandchildren)
            >>> children = ingestor.get_children("project.src.utils", "module", depth=2)
        """
        if depth < 1 and depth != -1:
            raise ValueError("depth must be >= 1 or -1 for unlimited")

        # Determine parent node type and build query
        if identifier_type == "auto":
            # Try to detect: first check if it's a file path, then module QN, then class QN

            # Try as File path
            file_results = self._query_file_children(identifier, depth, project_name)
            if file_results:
                return file_results

            # Try as File qualified_name
            file_qn_results = self._query_file_children_by_qn(identifier, depth)
            if file_qn_results:
                return file_qn_results

            # Try as Class qualified_name
            class_results = self._query_class_children(identifier, depth)
            if class_results:
                return class_results

            # If nothing found, return empty list
            return []

        elif identifier_type == "file":
            # Try path first, then qualified_name
            results = self._query_file_children(identifier, depth, project_name)
            if results:
                return results
            return self._query_file_children_by_qn(identifier, depth)
        elif identifier_type == "module":
            # For backwards compatibility, "module" is treated as "file"
            results = self._query_file_children(identifier, depth)
            if results:
                return results
            return self._query_file_children_by_qn(identifier, depth)
        elif identifier_type == "class":
            return self._query_class_children(identifier, depth)
        else:
            raise ValueError(
                f"Invalid identifier_type: {identifier_type}. Must be 'auto', 'file', 'module', or 'class'"
            )

    def _query_file_children(
        self, file_path: str, depth: int, project_name: str | None = None
    ) -> list[dict[str, Any]]:
        """Query children of a File node by path.

        Source code files have qualified_name and can DEFINE Function/Class nodes.
        This method finds the File by path and returns its children.

        Args:
            file_path: File path (relative) to find
            depth: Maximum depth to traverse
            project_name: Optional project name for proper isolation (highly recommended)
        """
        # Find the file's qualified_name first
        # Use project_name prefix if provided for proper isolation
        if project_name:
            # Use qualified_name with project prefix for accurate matching
            # The qualified_name format is: project_name.path.to.file
            # So we search for files that start with the project name and end with the file name
            file_name = Path(file_path).stem
            file_query = """
            MATCH (f:File)
            WHERE f.qualified_name STARTS WITH $project_prefix
              AND f.qualified_name ENDS WITH $file_name
            RETURN f.qualified_name AS file_qn
            LIMIT 1
            """
            file_results = self.fetch_all(
                file_query,
                {"project_prefix": f"{project_name}.", "file_name": file_name},
            )
        else:
            # Fallback: use path match (may return wrong result if multiple projects have same path)
            file_query = """
            MATCH (f:File)
            WHERE f.path = $file_path
            RETURN f.qualified_name AS file_qn
            LIMIT 1
            """
            file_results = self.fetch_all(file_query, {"file_path": file_path})

        if not file_results or not file_results[0].get("file_qn"):
            return []
        file_qn = file_results[0]["file_qn"]
        # Query children using the qualified_name
        return self._query_file_children_by_qn(file_qn, depth)

    def _query_file_children_by_qn(
        self, file_qn: str, depth: int
    ) -> list[dict[str, Any]]:
        """Query children of a File node by qualified_name."""
        # File defines Function/Class, which can have their own children (Class -> Method)
        all_results = []

        # Always include direct children (Function/Class) at depth 1
        query1 = """
        MATCH (f:File)-[:DEFINES]->(n)
        WHERE f.qualified_name = $file_qn
        RETURN properties(n) AS properties, labels(n) AS type, 1 AS depth
        """
        depth1_results = self.fetch_all(query1, {"file_qn": file_qn})
        for result in depth1_results:
            result["parent_type"] = "File"
            result["parent_identifier"] = file_qn
            all_results.append(result)

        # If depth >= 2, also include methods of classes
        if depth == -1 or depth >= 2:
            query2 = """
            MATCH (f:File)-[:DEFINES]->(c:Class)-[:DEFINES_METHOD]->(method:Method)
            WHERE f.qualified_name = $file_qn
            RETURN properties(method) AS properties, labels(method) AS type, 2 AS depth
            """
            depth2_results = self.fetch_all(query2, {"file_qn": file_qn})
            for result in depth2_results:
                result["parent_type"] = "File"
                result["parent_identifier"] = file_qn
                all_results.append(result)

        return self._format_child_results(all_results, file_qn, "File")

    def _query_class_children(self, class_qn: str, depth: int) -> list[dict[str, Any]]:
        """Query children of a Class node.

        Args:
            class_qn: Class qualified name
            depth: Maximum depth to traverse (currently unused as Class only has Method children)
        """
        # Class defines Method (currently only one level deep)
        # For now, Class only has direct Method children, so depth > 1 returns same as depth=1
        # The depth parameter is kept for API consistency with other _query_*_children methods
        _ = depth  # Acknowledge parameter for future extensibility
        query = """
        MATCH (c:Class)-[:DEFINES_METHOD]->(m:Method)
        WHERE c.qualified_name = $class_qn
        RETURN properties(m) AS properties, labels(m) AS type,
               'Class' AS parent_type, c.qualified_name AS parent_identifier, 1 AS depth
        ORDER BY m.start_line
        """
        results = self.fetch_all(query, {"class_qn": class_qn})
        return self._format_child_results(results, class_qn, "Class")

    def _format_child_results(
        self, results: list[dict[str, Any]], parent_id: str, parent_type: str
    ) -> list[dict[str, Any]]:
        """Format query results into a consistent structure."""
        formatted_results = []
        for result in results:
            node_dict = result["properties"].copy() if result.get("properties") else {}
            node_dict["type"] = result.get("type", [])
            node_dict["parent_type"] = result.get("parent_type", parent_type)
            node_dict["parent_identifier"] = result.get("parent_identifier", parent_id)
            node_dict["depth"] = result.get("depth", 1)
            formatted_results.append(node_dict)
        return formatted_results

    def get_nodes_with_module_context(
        self,
        qualified_names: list[str],
        include_source: bool = True,
        repo_path: str | None = None,
    ) -> dict[str, Any]:
        """Fetch nodes grouped by module with module context.

        This method optimizes context retrieval by grouping nodes by their
        containing module, avoiding redundant context for multiple nodes
        from the same file.

        Args:
            qualified_names: List of Function/Class/Method qualified names
            include_source: Whether to read source code from files
            repo_path: Repository root for source file reading (string path)

        Returns:
            {
                "modules": {
                    "project.module.submodule": {
                        "file_path": "path/to/file.py",
                        "module_context": "from x import y\\n...",
                        "nodes": [
                            {
                                "qualified_name": "project.module.submodule.func",
                                "type": "Function",
                                "start_line": 45,
                                "end_line": 52,
                                "source": "def func(): ..."  # if include_source
                            }
                        ]
                    }
                }
            }
        """
        from pathlib import Path

        # Step 1: Fetch all nodes and their containing modules
        nodes_query = """
        UNWIND $qns AS qn
        MATCH (n)
        WHERE n.qualified_name = qn AND (n:Function OR n:Class OR n:Method)
        OPTIONAL MATCH (m:File)-[:DEFINES|DEFINES_METHOD*1..2]->(n)
        RETURN
            n.qualified_name AS qualified_name,
            labels(n)[0] AS type,
            n.start_line AS start_line,
            n.end_line AS end_line,
            n.docstring AS docstring,
            n.name AS name,
            m.qualified_name AS module_qn,
            m.path AS module_path,
            m.module_context AS module_context
        """

        results = self.fetch_all(nodes_query, {"qns": qualified_names})

        # Step 2: Group by module
        modules: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"nodes": [], "file_path": None, "module_context": None}
        )

        for record in results:
            module_qn = record.get("module_qn")
            if not module_qn:
                continue

            modules[module_qn]["file_path"] = record.get("module_path")
            modules[module_qn]["module_context"] = record.get("module_context")
            modules[module_qn]["nodes"].append(
                {
                    "qualified_name": record["qualified_name"],
                    "type": record["type"],
                    "name": record.get("name"),
                    "start_line": record.get("start_line"),
                    "end_line": record.get("end_line"),
                    "docstring": record.get("docstring"),
                }
            )

        # Step 3: Optionally read source code
        if include_source and repo_path:
            repo_path_obj = Path(repo_path)
            for module_qn, module_data in modules.items():
                file_path = module_data.get("file_path")
                if not file_path:
                    continue

                full_path = repo_path_obj / file_path
                if not full_path.exists():
                    continue

                try:
                    lines = full_path.read_text().splitlines()
                    for node in module_data["nodes"]:
                        start = node.get("start_line")
                        end = node.get("end_line")
                        if start is not None and end is not None:
                            # Line numbers are 1-indexed, convert to 0-indexed
                            node["source"] = "\n".join(lines[start - 1 : end])
                except Exception as e:
                    logger.warning(f"Failed to read source from {full_path}: {e}")

        return {"modules": dict(modules)}

    def format_nodes_with_context_for_agent(
        self,
        query_result: dict[str, Any],
    ) -> str:
        """Format the result of get_nodes_with_module_context for agent consumption.

        This formats the grouped query result into a markdown document that
        provides clear context for each module and its contained nodes.

        Args:
            query_result: Result from get_nodes_with_module_context()

        Returns:
            Formatted markdown string with module context and node code
        """
        output_parts: list[str] = []

        modules = query_result.get("modules", {})

        for module_qn, module_data in modules.items():
            file_path = module_data.get("file_path", "Unknown")
            module_context = module_data.get("module_context", "")
            nodes = module_data.get("nodes", [])

            # Module header
            output_parts.append(f"## Module: {module_qn}")
            output_parts.append(f"**File:** {file_path}")
            output_parts.append("")

            # Module context
            if module_context:
                output_parts.append("### Module Context (imports & configurations):")
                output_parts.append("```python")
                output_parts.append(module_context)
                output_parts.append("```")
                output_parts.append("")

            # Nodes
            if nodes:
                output_parts.append("### Nodes:")
                output_parts.append("")

                for node in nodes:
                    node_type = node.get("type", "Unknown")
                    node_name = node.get("name", node.get("qualified_name", "Unknown"))
                    start_line = node.get("start_line", "?")
                    end_line = node.get("end_line", "?")
                    source = node.get("source", "")
                    docstring = node.get("docstring", "")

                    output_parts.append(
                        f"#### {node_type}: `{node_name}` (lines {start_line}-{end_line})"
                    )

                    if docstring:
                        output_parts.append(f"> {docstring}")
                        output_parts.append("")

                    if source:
                        output_parts.append("```python")
                        output_parts.append(source)
                        output_parts.append("```")

                    output_parts.append("")

            output_parts.append("---")
            output_parts.append("")

        return "\n".join(output_parts)

    def get_all_projects(self) -> list[str]:
        """
        Get all project names in the database.

        Returns:
            List of project names
        """
        query = "MATCH (p:Project) RETURN p.name AS name ORDER BY p.name"
        results = self.fetch_all(query, use_cache=False)
        return [r["name"] for r in results]

    def get_project_structure_info(self, project_name: str) -> dict[str, Any]:
        """
        Get structural information about a project to help diagnose issues.

        Supports both package-based and package-less repositories.

        Returns:
            Dict with info about project structure
        """
        info = {
            "project_exists": False,
            "package_count": 0,
            "module_count": 0,
            "file_count": 0,
            "is_package_based": None,
            "has_direct_modules": False,
        }

        # Check if project exists
        project_query = "MATCH (p:Project {name: $project}) RETURN count(p) AS count"
        result = self.fetch_all(
            project_query, {"project": project_name}, use_cache=False
        )
        if result and result[0]["count"] > 0:
            info["project_exists"] = True

        if not info["project_exists"]:
            return info

        # Count packages (folders with is_package=true)
        packages_query = """
        MATCH (p:Project {name: $project})-[:CONTAINS_FOLDER]->(pkg:Folder {is_package: true})
        RETURN count(pkg) AS count
        """
        result = self.fetch_all(
            packages_query, {"project": project_name}, use_cache=False
        )
        if result:
            info["package_count"] = result[0]["count"]

        # Check if this is a package-based or flat structure
        info["is_package_based"] = info["package_count"] > 0

        if info["is_package_based"]:
            # Package-based repository
            # Count modules (files with qualified_name)
            modules_query = """
            MATCH (p:Project {name: $project})-[:CONTAINS_FOLDER*0..]->(pkg:Folder {is_package: true})-[:CONTAINS_FILE]->(f:File)
            WHERE f.qualified_name IS NOT NULL
            RETURN count(f) AS count
            """
            result = self.fetch_all(
                modules_query, {"project": project_name}, use_cache=False
            )
            if result:
                info["module_count"] = result[0]["count"]

            # Count files
            files_query = """
            MATCH (p:Project {name: $project})-[:CONTAINS_FOLDER*0..]->(pkg:Folder {is_package: true})-[:CONTAINS_FILE]->(f:File)
            RETURN count(f) AS count
            """
            result = self.fetch_all(
                files_query, {"project": project_name}, use_cache=False
            )
            if result:
                info["file_count"] = result[0]["count"]
        else:
            # Non-package repository: modules are directly in project or in Files
            modules_query = """
            MATCH (m:File)
            WHERE m.qualified_name STARTS WITH $project_prefix
            RETURN count(m) AS count
            """
            result = self.fetch_all(
                modules_query, {"project_prefix": f"{project_name}."}, use_cache=False
            )
            if result:
                info["module_count"] = result[0]["count"]
                info["has_direct_modules"] = result[0]["count"] > 0

            # Count files directly connected to project
            files_query = """
            MATCH (p:Project {name: $project})-[:CONTAINS_FILE]->(f:File)
            RETURN count(f) AS count
            """
            result = self.fetch_all(
                files_query, {"project": project_name}, use_cache=False
            )
            if result:
                info["file_count"] = result[0]["count"]

        return info

    def _get_flat_module_tree(
        self, project_name: str, min_node_count: int = 0
    ) -> list[dict[str, Any]]:
        """
        Get module tree for non-package repositories.

        When a repository has no package structure (no folders with is_package=true),
        return modules as top-level items.
        Useful for projects like DeepSeek-V3 with flat file structures.

        Args:
            project_name: Name of the project
            min_node_count: Minimum node count to include a module

        Returns:
            List of module dicts formatted as top-level items
        """
        # Get all modules for this project
        modules_query = """
        MATCH (m:File)
        WHERE m.qualified_name STARTS WITH $project_prefix
        OPTIONAL MATCH (m)-[:DEFINES]->(n)
        WHERE n:Function OR n:Class
        WITH m, count(DISTINCT n) AS direct_count
        OPTIONAL MATCH (m)-[:DEFINES]->(c:Class)-[:DEFINES_METHOD]->(method:Method)
        WITH m, direct_count, count(DISTINCT method) AS method_count
        RETURN m.qualified_name AS qualified_name,
               m.name AS name,
               m.path AS path,
               direct_count + method_count AS node_count
        ORDER BY m.path
        """

        modules = self.fetch_all(
            modules_query,
            {"project_prefix": f"{project_name}."},
            use_cache=True,
            cache_ttl=120.0,
        )

        # Get functions and classes for each module
        module_children_query = """
        MATCH (m:File {qualified_name: $module_qn})-[:DEFINES]->(n)
        WHERE n:Function OR n:Class
        RETURN n.qualified_name AS qualified_name,
               n.name AS name,
               labels(n)[0] AS type,
               n.docstring AS docstring,
               n.start_line AS start_line,
               n.end_line AS end_line
        ORDER BY n.start_line
        """

        result = []
        for mod in modules:
            if min_node_count > 0 and mod["node_count"] < min_node_count:
                continue

            # Get children for this module
            children = self.fetch_all(
                module_children_query,
                {"module_qn": mod["qualified_name"]},
                use_cache=True,
                cache_ttl=120.0,
            )

            functions = [c for c in children if c["type"] == "Function"]
            classes = [c for c in children if c["type"] == "Class"]

            result.append(
                {
                    "qualified_name": mod["qualified_name"],
                    "name": mod["name"],
                    "path": mod["path"] or "",
                    "type": "module",
                    "node_count": mod["node_count"],
                    "functions": functions,
                    "classes": classes,
                }
            )

        return result

    def get_package_tree(
        self,
        project_name: str,
        max_depth: int = 3,
        min_node_count: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Get hierarchical package structure with node counts for a project.

        This method traverses the knowledge graph to build a tree structure
        representing the package hierarchy, including modules and their
        node counts (functions, classes, methods).

        Supports two repository structures:
        1. Traditional: Project → Folder (is_package=true) → File → Functions/Classes
        2. Flat: Project → File → Functions/Classes (no packages)

        Args:
            project_name: Name of the project to query
            max_depth: Maximum package nesting depth (default: 3)
            min_node_count: Minimum node count to include a module (default: 0)

        Returns:
            List of package dicts with hierarchical structure (for package-based repos)
            Or list of module dicts (for non-package repos):
            [
                {
                    "qualified_name": "repo.package",
                    "name": "package",
                    "path": "package/",
                    "type": "package",
                    "node_count": 25,
                    "children": [...]
                }
            ]
        """
        # Step 1: Check if this is a package-based or non-package repository
        has_packages_query = """
        MATCH (p:Project {name: $project})-[:CONTAINS_FOLDER]->(pkg:Folder {is_package: true})
        RETURN count(pkg) as pkg_count
        """
        pkg_result = self.fetch_all(
            has_packages_query, {"project": project_name}, use_cache=True
        )
        has_packages = pkg_result and pkg_result[0].get("pkg_count", 0) > 0

        if not has_packages:
            # Non-package repository: return modules directly as top-level items
            return self._get_flat_module_tree(project_name, min_node_count)

        # Step 1: Get all packages (folders with is_package=true) for this project
        packages_query = """
        MATCH (p:Project {name: $project})-[:CONTAINS_FOLDER*1..{depth}]->(pkg:Folder {is_package: true})
        RETURN pkg.qualified_name AS qualified_name,
               pkg.name AS name,
               pkg.path AS path
        ORDER BY pkg.path
        """.replace("{depth}", str(max_depth))

        packages = self.fetch_all(
            packages_query,
            {"project": project_name},
            use_cache=True,
            cache_ttl=120.0,
        )

        # Step 2: Get all modules with their node counts
        modules_query = """
        MATCH (p:Project {name: $project})-[:CONTAINS_FOLDER*1..{depth}]->(pkg:Folder {is_package: true})-[:CONTAINS_FILE]->(f:File)
        WHERE f.qualified_name IS NOT NULL
        OPTIONAL MATCH (f)-[:DEFINES]->(n)
        WHERE n:Function OR n:Class
        WITH f, pkg, count(DISTINCT n) AS direct_count
        OPTIONAL MATCH (f)-[:DEFINES]->(c:Class)-[:DEFINES_METHOD]->(method:Method)
        WITH f, pkg, direct_count, count(DISTINCT method) AS method_count
        RETURN f.qualified_name AS qualified_name,
               f.name AS name,
               f.path AS path,
               pkg.qualified_name AS parent_package,
               direct_count + method_count AS node_count
        ORDER BY f.path
        """.replace("{depth}", str(max_depth))

        modules = self.fetch_all(
            modules_query,
            {"project": project_name},
            use_cache=True,
            cache_ttl=120.0,
        )

        # Step 3: Get functions and classes for each module
        module_children_query = """
        MATCH (m:File {qualified_name: $module_qn})-[:DEFINES]->(n)
        WHERE n:Function OR n:Class
        RETURN n.qualified_name AS qualified_name,
               n.name AS name,
               labels(n)[0] AS type,
               n.docstring AS docstring,
               n.start_line AS start_line,
               n.end_line AS end_line
        ORDER BY n.start_line
        """

        # Build lookup maps
        package_map: dict[str, dict[str, Any]] = {}
        for pkg in packages:
            package_map[pkg["qualified_name"]] = {
                "qualified_name": pkg["qualified_name"],
                "name": pkg["name"],
                "path": pkg["path"] or "",
                "type": "package",
                "node_count": 0,
                "children": [],
            }

        module_map: dict[str, dict[str, Any]] = {}
        for mod in modules:
            if min_node_count > 0 and mod["node_count"] < min_node_count:
                continue

            # Get functions and classes for this module
            children = self.fetch_all(
                module_children_query,
                {"module_qn": mod["qualified_name"]},
                use_cache=True,
                cache_ttl=120.0,
            )

            functions = [c for c in children if c["type"] == "Function"]
            classes = [c for c in children if c["type"] == "Class"]

            module_data = {
                "qualified_name": mod["qualified_name"],
                "name": mod["name"],
                "path": mod["path"] or "",
                "type": "module",
                "node_count": mod["node_count"],
                "parent_package": mod["parent_package"],
                "functions": functions,
                "classes": classes,
            }
            module_map[mod["qualified_name"]] = module_data

            # Add module to parent package
            parent_pkg = mod["parent_package"]
            if parent_pkg in package_map:
                package_map[parent_pkg]["children"].append(module_data)
                package_map[parent_pkg]["node_count"] += mod["node_count"]

        # Step 4: Build package hierarchy (nest sub-packages)
        # Sort packages by path depth (deepest first for bottom-up construction)
        sorted_packages = sorted(
            package_map.values(),
            key=lambda x: x["path"].count("/"),
            reverse=True,
        )

        # Track which packages are sub-packages (have been nested)
        nested_packages: set[str] = set()

        for pkg in sorted_packages:
            pkg_qn = pkg["qualified_name"]
            # Find parent package by qualified_name prefix
            parts = pkg_qn.rsplit(".", 1)
            if len(parts) == 2:
                parent_qn = parts[0]
                if parent_qn in package_map and parent_qn != project_name:
                    # Add as child of parent package
                    package_map[parent_qn]["children"].append(pkg)
                    package_map[parent_qn]["node_count"] += pkg["node_count"]
                    nested_packages.add(pkg_qn)

        # Step 5: Return only top-level packages (not nested as children)
        top_level_packages = [
            pkg for qn, pkg in package_map.items() if qn not in nested_packages
        ]

        # Sort by path for consistent ordering
        top_level_packages.sort(key=lambda x: x["path"])

        return top_level_packages

    def get_module_info(
        self,
        module_qn: str,
        include_source: bool = False,
        repo_path: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Get detailed information about a specific module.

        Args:
            module_qn: Module qualified name
            include_source: Whether to include source code
            repo_path: Repository root path for source reading

        Returns:
            Module info dict with functions, classes, methods, and optionally source
        """
        from pathlib import Path

        # Get module basic info
        module_query = """
        MATCH (m:File {qualified_name: $module_qn})
        RETURN m.qualified_name AS qualified_name,
               m.name AS name,
               m.path AS path,
               m.docstring AS docstring,
               m.module_context AS module_context
        """
        results = self.fetch_all(module_query, {"module_qn": module_qn}, use_cache=True)
        if not results:
            return None

        module_info = results[0]

        # Get functions
        functions_query = """
        MATCH (m:File {qualified_name: $module_qn})-[:DEFINES]->(f:Function)
        RETURN f.qualified_name AS qualified_name,
               f.name AS name,
               f.docstring AS docstring,
               f.start_line AS start_line,
               f.end_line AS end_line,
               f.signature AS signature
        ORDER BY f.start_line
        """
        functions = self.fetch_all(
            functions_query, {"module_qn": module_qn}, use_cache=True
        )

        # Get classes with their methods
        classes_query = """
        MATCH (m:File {qualified_name: $module_qn})-[:DEFINES]->(c:Class)
        OPTIONAL MATCH (c)-[:DEFINES_METHOD]->(method:Method)
        WITH c, collect({
            qualified_name: method.qualified_name,
            name: method.name,
            docstring: method.docstring,
            start_line: method.start_line,
            end_line: method.end_line,
            signature: method.signature
        }) AS methods
        RETURN c.qualified_name AS qualified_name,
               c.name AS name,
               c.docstring AS docstring,
               c.start_line AS start_line,
               c.end_line AS end_line,
               c.bases AS bases,
               methods
        ORDER BY c.start_line
        """
        classes = self.fetch_all(
            classes_query, {"module_qn": module_qn}, use_cache=True
        )

        # Clean up methods (remove null entries from collect)
        for cls in classes:
            cls["methods"] = [
                m for m in cls.get("methods", []) if m.get("qualified_name")
            ]

        # Get imports
        imports_query = """
        MATCH (m:File {qualified_name: $module_qn})-[:IMPORTS]->(imported)
        RETURN imported.qualified_name AS qualified_name,
               imported.name AS name,
               labels(imported)[0] AS type
        """
        imports = self.fetch_all(
            imports_query, {"module_qn": module_qn}, use_cache=True
        )

        # Get callers (what calls into this module)
        callers_query = """
        MATCH (m:File {qualified_name: $module_qn})-[:DEFINES]->(n)<-[:CALLS]-(caller)
        WHERE NOT caller.qualified_name STARTS WITH $module_qn
        WITH DISTINCT caller
        RETURN caller.qualified_name AS qualified_name,
               caller.name AS name,
               labels(caller)[0] AS type
        LIMIT 20
        """
        callers = self.fetch_all(
            callers_query, {"module_qn": module_qn}, use_cache=True
        )

        result = {
            **module_info,
            "functions": functions,
            "classes": classes,
            "imports": imports,
            "callers": callers,
        }

        # Optionally read source code
        if include_source and repo_path and module_info.get("path"):
            try:
                full_path = Path(repo_path) / module_info["path"]
                if full_path.exists():
                    result["source"] = full_path.read_text()
            except Exception as e:
                logger.warning(f"Failed to read source for {module_qn}: {e}")

        return result

    def get_function_call_context(
        self,
        qualified_name: str,
        depth: int = 2,
    ) -> dict[str, Any]:
        """
        Get detailed call context for a function/method including callers and callees.

        Args:
            qualified_name: Function/Method qualified name
            depth: How many levels of calls to traverse (default: 2)

        Returns:
            {
                "qualified_name": "module.func",
                "name": "func",
                "type": "Function",
                "docstring": "...",
                "signature": "func(a, b)",
                "callers": [
                    {"qualified_name": "...", "name": "...", "type": "Function", "call_count": 2}
                ],
                "callees": [
                    {"qualified_name": "...", "name": "...", "type": "Function", "call_count": 1}
                ],
                "call_chain": [...]  # For deeper analysis
            }
        """
        # Get basic info and direct callers/callees
        query = """
        MATCH (n)
        WHERE n.qualified_name = $qn AND (n:Function OR n:Method)
        OPTIONAL MATCH (caller)-[r1:CALLS]->(n)
        WITH n, collect(DISTINCT {
            qualified_name: caller.qualified_name,
            name: caller.name,
            type: labels(caller)[0],
            docstring: caller.docstring
        }) AS callers
        OPTIONAL MATCH (n)-[r2:CALLS]->(callee)
        WITH n, callers, collect(DISTINCT {
            qualified_name: callee.qualified_name,
            name: callee.name,
            type: labels(callee)[0],
            docstring: callee.docstring
        }) AS callees
        RETURN n.qualified_name AS qualified_name,
               n.name AS name,
               labels(n)[0] AS type,
               n.docstring AS docstring,
               n.signature AS signature,
               n.start_line AS start_line,
               n.end_line AS end_line,
               callers,
               callees
        """

        results = self.fetch_all(
            query, {"qn": qualified_name}, use_cache=True, cache_ttl=120.0
        )

        if not results:
            return {}

        result = results[0]

        # Clean up None entries from collect
        callers = [c for c in result.get("callers", []) if c.get("qualified_name")]
        callees = [c for c in result.get("callees", []) if c.get("qualified_name")]

        return {
            "qualified_name": result["qualified_name"],
            "name": result.get("name"),
            "type": result.get("type"),
            "docstring": result.get("docstring"),
            "signature": result.get("signature"),
            "start_line": result.get("start_line"),
            "end_line": result.get("end_line"),
            "callers": callers,
            "callees": callees,
        }

    def get_class_hierarchy(
        self,
        class_qn: str,
    ) -> dict[str, Any]:
        """
        Get class inheritance hierarchy and method relationships.

        Args:
            class_qn: Class qualified name

        Returns:
            {
                "qualified_name": "module.ClassName",
                "name": "ClassName",
                "bases": ["BaseClass"],
                "docstring": "...",
                "methods": [
                    {
                        "name": "method",
                        "signature": "method(self, x)",
                        "docstring": "...",
                        "callers": [...],
                        "callees": [...]
                    }
                ],
                "subclasses": [{"qualified_name": "...", "name": "..."}]
            }
        """
        # Get class info with methods and their call relationships
        query = """
        MATCH (c:Class {qualified_name: $qn})
        OPTIONAL MATCH (c)-[:DEFINES_METHOD]->(m:Method)
        OPTIONAL MATCH (caller)-[:CALLS]->(m)
        OPTIONAL MATCH (m)-[:CALLS]->(callee)
        WITH c, m,
             collect(DISTINCT {qn: caller.qualified_name, name: caller.name}) AS method_callers,
             collect(DISTINCT {qn: callee.qualified_name, name: callee.name}) AS method_callees
        WITH c, collect({
            qualified_name: m.qualified_name,
            name: m.name,
            signature: m.signature,
            docstring: m.docstring,
            start_line: m.start_line,
            end_line: m.end_line,
            callers: method_callers,
            callees: method_callees
        }) AS methods
        OPTIONAL MATCH (subclass:Class)-[:INHERITS]->(c)
        WITH c, methods, collect(DISTINCT {
            qualified_name: subclass.qualified_name,
            name: subclass.name
        }) AS subclasses
        RETURN c.qualified_name AS qualified_name,
               c.name AS name,
               c.bases AS bases,
               c.docstring AS docstring,
               c.start_line AS start_line,
               c.end_line AS end_line,
               methods,
               subclasses
        """

        results = self.fetch_all(
            query, {"qn": class_qn}, use_cache=True, cache_ttl=120.0
        )

        if not results:
            return {}

        result = results[0]

        # Clean up methods
        methods = []
        for m in result.get("methods", []):
            if m.get("qualified_name"):
                m["callers"] = [c for c in m.get("callers", []) if c.get("qn")]
                m["callees"] = [c for c in m.get("callees", []) if c.get("qn")]
                methods.append(m)

        subclasses = [
            s for s in result.get("subclasses", []) if s.get("qualified_name")
        ]

        return {
            "qualified_name": result["qualified_name"],
            "name": result.get("name"),
            "bases": result.get("bases") or [],
            "docstring": result.get("docstring"),
            "start_line": result.get("start_line"),
            "end_line": result.get("end_line"),
            "methods": methods,
            "subclasses": subclasses,
        }

    def get_module_call_graph(
        self,
        module_qn: str,
        include_external: bool = False,
    ) -> dict[str, Any]:
        """
        Get the call graph within a module showing internal and external calls.

        Args:
            module_qn: Module qualified name
            include_external: Whether to include calls to external modules

        Returns:
            {
                "internal_calls": [
                    {"from": "func_a", "to": "func_b", "count": 3}
                ],
                "outgoing_calls": [
                    {"from": "func_a", "to": "external.module.func", "count": 1}
                ],
                "incoming_calls": [
                    {"from": "other.module.func", "to": "func_a", "count": 2}
                ]
            }
        """
        # Internal calls within the module
        internal_query = """
        MATCH (m:File {qualified_name: $module_qn})-[:DEFINES]->(caller)
        MATCH (m)-[:DEFINES]->(callee)
        MATCH (caller)-[r:CALLS]->(callee)
        RETURN caller.name AS from_name,
               caller.qualified_name AS from_qn,
               callee.name AS to_name,
               callee.qualified_name AS to_qn,
               count(r) AS call_count
        ORDER BY call_count DESC
        """

        internal_calls = self.fetch_all(
            internal_query, {"module_qn": module_qn}, use_cache=True
        )

        # Outgoing calls to other modules
        outgoing_query = """
        MATCH (m:File {qualified_name: $module_qn})-[:DEFINES]->(caller)
        MATCH (caller)-[r:CALLS]->(callee)
        WHERE NOT (m)-[:DEFINES]->(callee)
        RETURN caller.name AS from_name,
               caller.qualified_name AS from_qn,
               callee.name AS to_name,
               callee.qualified_name AS to_qn,
               count(r) AS call_count
        ORDER BY call_count DESC
        LIMIT 30
        """

        outgoing_calls = self.fetch_all(
            outgoing_query, {"module_qn": module_qn}, use_cache=True
        )

        # Incoming calls from other modules
        incoming_query = """
        MATCH (m:File {qualified_name: $module_qn})-[:DEFINES]->(callee)
        MATCH (caller)-[r:CALLS]->(callee)
        WHERE NOT (m)-[:DEFINES]->(caller)
        RETURN caller.name AS from_name,
               caller.qualified_name AS from_qn,
               callee.name AS to_name,
               callee.qualified_name AS to_qn,
               count(r) AS call_count
        ORDER BY call_count DESC
        LIMIT 30
        """

        incoming_calls = self.fetch_all(
            incoming_query, {"module_qn": module_qn}, use_cache=True
        )

        return {
            "internal_calls": [
                {
                    "from": c["from_name"],
                    "from_qn": c["from_qn"],
                    "to": c["to_name"],
                    "to_qn": c["to_qn"],
                    "count": c["call_count"],
                }
                for c in internal_calls
            ],
            "outgoing_calls": [
                {
                    "from": c["from_name"],
                    "from_qn": c["from_qn"],
                    "to": c["to_name"],
                    "to_qn": c["to_qn"],
                    "count": c["call_count"],
                }
                for c in outgoing_calls
            ],
            "incoming_calls": [
                {
                    "from": c["from_name"],
                    "from_qn": c["from_qn"],
                    "to": c["to_name"],
                    "to_qn": c["to_qn"],
                    "count": c["call_count"],
                }
                for c in incoming_calls
            ],
        }

    def get_package_call_graph(
        self,
        package_qn: str,
        project_name: str,
    ) -> dict[str, Any]:
        """
        Get call relationships between modules within a package folder.

        Args:
            package_qn: Package folder qualified name (folder with is_package=true)
            project_name: Project name for filtering

        Returns:
            {
                "inter_module_calls": [
                    {"from_module": "pkg.mod_a", "to_module": "pkg.mod_b", "count": 5}
                ],
                "external_dependencies": [
                    {"module": "pkg.mod_a", "depends_on": "other_pkg.mod", "count": 3}
                ]
            }
        """
        # Inter-module calls within the package (folder with is_package=true)
        inter_module_query = """
        MATCH (pkg:Folder {qualified_name: $package_qn, is_package: true})-[:CONTAINS_FILE]->(f1:File)
        MATCH (f1)-[:DEFINES]->(caller)
        MATCH (pkg)-[:CONTAINS_FILE]->(f2:File)
        MATCH (f2)-[:DEFINES]->(callee)
        MATCH (caller)-[r:CALLS]->(callee)
        WHERE f1.qualified_name <> f2.qualified_name
        RETURN f1.qualified_name AS from_module,
               f1.name AS from_name,
               f2.qualified_name AS to_module,
               f2.name AS to_name,
               count(r) AS call_count
        ORDER BY call_count DESC
        LIMIT 20
        """

        inter_module_calls = self.fetch_all(
            inter_module_query, {"package_qn": package_qn}, use_cache=True
        )

        # External dependencies (calls to modules outside the package)
        external_query = """
        MATCH (pkg:Folder {qualified_name: $package_qn, is_package: true})-[:CONTAINS_FILE]->(f:File)
        MATCH (f)-[:DEFINES]->(caller)
        MATCH (caller)-[r:CALLS]->(callee)
        WHERE callee.qualified_name STARTS WITH $project_prefix
          AND NOT callee.qualified_name STARTS WITH $package_prefix
        OPTIONAL MATCH (ext_m:File)-[:DEFINES]->(callee)
        RETURN f.qualified_name AS from_module,
               f.name AS from_name,
               ext_m.qualified_name AS to_module,
               ext_m.name AS to_name,
               count(r) AS call_count
        ORDER BY call_count DESC
        LIMIT 20
        """

        external_deps = self.fetch_all(
            external_query,
            {
                "package_qn": package_qn,
                "project_prefix": f"{project_name}.",
                "package_prefix": f"{package_qn}.",
            },
            use_cache=True,
        )

        return {
            "inter_module_calls": [
                {
                    "from_module": c["from_module"],
                    "from_name": c["from_name"],
                    "to_module": c["to_module"],
                    "to_name": c["to_name"],
                    "count": c["call_count"],
                }
                for c in inter_module_calls
            ],
            "external_dependencies": [
                {
                    "from_module": c["from_module"],
                    "from_name": c["from_name"],
                    "to_module": c["to_module"],
                    "to_name": c["to_name"],
                    "count": c["call_count"],
                }
                for c in external_deps
                if c["to_module"]
            ],
        }

    def get_key_functions(
        self,
        module_qn: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Get the most important functions/methods in a module based on call relationships.

        Args:
            module_qn: Module qualified name
            top_k: Number of top functions to return

        Returns:
            List of functions with their importance scores and call info
        """
        query = """
        MATCH (m:File {qualified_name: $module_qn})-[:DEFINES]->(n)
        WHERE n:Function OR n:Method
        OPTIONAL MATCH (caller)-[:CALLS]->(n)
        OPTIONAL MATCH (n)-[:CALLS]->(callee)
        WITH n,
             count(DISTINCT caller) AS caller_count,
             count(DISTINCT callee) AS callee_count
        RETURN n.qualified_name AS qualified_name,
               n.name AS name,
               labels(n)[0] AS type,
               n.signature AS signature,
               n.docstring AS docstring,
               n.start_line AS start_line,
               n.end_line AS end_line,
               caller_count,
               callee_count,
               (caller_count * 0.6 + callee_count * 0.4) AS importance_score
        ORDER BY importance_score DESC
        LIMIT $top_k
        """

        results = self.fetch_all(
            query, {"module_qn": module_qn, "top_k": top_k}, use_cache=True
        )

        return [
            {
                "qualified_name": r["qualified_name"],
                "name": r["name"],
                "type": r["type"],
                "signature": r["signature"],
                "docstring": r["docstring"],
                "start_line": r["start_line"],
                "end_line": r["end_line"],
                "caller_count": r["caller_count"],
                "callee_count": r["callee_count"],
                "importance_score": r["importance_score"],
            }
            for r in results
        ]

    def read_source_lines(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
        repo_path: str,
    ) -> str | None:
        """
        Read specific lines from a source file.

        Args:
            file_path: Relative path to file
            start_line: Start line number (1-indexed)
            end_line: End line number (1-indexed)
            repo_path: Repository root path

        Returns:
            Source code string or None if failed
        """
        from pathlib import Path

        try:
            full_path = Path(repo_path) / file_path
            if not full_path.exists():
                return None

            with open(full_path, encoding="utf-8") as f:
                lines = f.readlines()

            # Adjust for 0-indexed
            start_idx = max(0, start_line - 1)
            end_idx = min(len(lines), end_line)

            return "".join(lines[start_idx:end_idx])
        except Exception as e:
            logger.warning(f"Failed to read source lines from {file_path}: {e}")
            return None

    # =========================================================================
    # Vector Search Methods (Memgraph Native Vector Support)
    # =========================================================================

    def setup_vector_index(self, dimension: int = 1536) -> None:
        """Create vector indexes for semantic search.

        This sets up vector indexes on Function, Method, and Class nodes
        to enable similarity search using embeddings stored in the graph.

        Args:
            dimension: Embedding dimension (1536 for text-embedding-3-small)

        Note:
            Requires Memgraph version >= 3.2 with vector search support.
            If indexes already exist, they will be skipped.
        """
        indexes = [
            ("function_embedding_idx", "Function", "embedding"),
            ("method_embedding_idx", "Method", "embedding"),
            ("class_embedding_idx", "Class", "embedding"),
        ]

        for index_name, label, prop in indexes:
            try:
                # Check if index exists by attempting to get info
                # Note: SHOW VECTOR INDEX INFO returns index info in Memgraph 3.2+
                try:
                    result = self._execute_query("SHOW VECTOR INDEX INFO")
                    existing = (
                        {r.get("index_name") for r in result} if result else set()
                    )
                except Exception:
                    # If SHOW VECTOR INDEX INFO fails, assume no indexes exist
                    existing = set()

                if index_name not in existing:
                    query = f"""
                        CREATE VECTOR INDEX {index_name}
                        ON :{label}({prop})
                        WITH CONFIG {{
                            "dimension": {dimension},
                            "capacity": 500000,
                            "metric": "cos"
                        }}
                    """
                    self._execute_query(query)
                    logger.info(f"Created vector index: {index_name}")
                else:
                    logger.debug(f"Vector index already exists: {index_name}")
            except Exception as e:
                error_str = str(e).lower()
                if "already exists" in error_str:
                    logger.debug(f"Vector index already exists: {index_name}")
                elif "not supported" in error_str or "unknown" in error_str:
                    logger.warning(
                        f"Vector index creation not supported (Memgraph >= 3.2 required): {e}"
                    )
                else:
                    logger.warning(f"Failed to create vector index {index_name}: {e}")

    def update_embeddings_batch(
        self,
        label: str,
        embeddings_data: list[dict[str, Any]],
    ) -> int:
        """Batch update embeddings for nodes.

        This method efficiently updates the embedding property on multiple
        nodes in a single database transaction.

        Args:
            label: Node label (Function, Method, Class)
            embeddings_data: List of dictionaries with:
                - qualified_name: str - Node identifier
                - embedding: list[float] - Embedding vector

        Returns:
            Number of successfully updated nodes

        Example:
            >>> data = [
            ...     {"qualified_name": "module.func", "embedding": [0.1, 0.2, ...]},
            ...     {"qualified_name": "module.Class", "embedding": [0.3, 0.4, ...]},
            ... ]
            >>> updated = ingestor.update_embeddings_batch("Function", data)
        """
        if not embeddings_data:
            return 0

        query = f"""
            UNWIND $data AS row
            MATCH (n:{label} {{qualified_name: row.qualified_name}})
            SET n.embedding = row.embedding
            RETURN count(n) AS updated
        """

        try:
            result = self._execute_query(query, {"data": embeddings_data})
            updated_count = result[0]["updated"] if result else 0
            logger.debug(f"Updated {updated_count} {label} embeddings")
            return updated_count
        except Exception as e:
            logger.error(f"Failed to batch update embeddings for {label}: {e}")
            return 0

    def vector_search(
        self,
        index_name: str,
        query_vector: list[float],
        top_k: int = 10,
        project_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search for similar nodes using vector similarity.

        This method performs semantic search by finding nodes with
        embeddings similar to the query vector.

        Args:
            index_name: Name of the vector index to search
                       (function_embedding_idx, method_embedding_idx, class_embedding_idx)
            query_vector: Query embedding vector
            top_k: Number of results to return
            project_name: Optional project name to filter results

        Returns:
            List of dictionaries with:
                - qualified_name: str - Node identifier
                - name: str - Node name
                - similarity: float - Cosine similarity score

        Example:
            >>> query_vec = embed_code("authenticate user")
            >>> results = ingestor.vector_search(
            ...     "function_embedding_idx",
            ...     query_vec,
            ...     top_k=5,
            ...     project_name="myproject"
            ... )
        """
        if project_name:
            # Filter by project prefix
            query = f"""
                CALL vector_search.search("{index_name}", {top_k * 2}, $query_vector)
                YIELD node, similarity
                WITH node, similarity
                WHERE node.qualified_name STARTS WITH $prefix
                RETURN node.qualified_name AS qualified_name,
                       node.name AS name,
                       similarity
                ORDER BY similarity DESC
                LIMIT {top_k}
            """
            params = {"query_vector": query_vector, "prefix": f"{project_name}."}
        else:
            query = f"""
                CALL vector_search.search("{index_name}", {top_k}, $query_vector)
                YIELD node, similarity
                RETURN node.qualified_name AS qualified_name,
                       node.name AS name,
                       similarity
            """
            params = {"query_vector": query_vector}

        try:
            results = self._execute_query(query, params)
            return results or []
        except Exception as e:
            error_str = str(e).lower()
            if "not found" in error_str or "does not exist" in error_str:
                logger.debug(f"Vector index not found: {index_name}")
            elif "not supported" in error_str:
                logger.warning("Vector search not supported (Memgraph >= 3.2 required)")
            else:
                logger.error(f"Vector search failed on {index_name}: {e}")
            return []

    def vector_search_all_types(
        self,
        query_vector: list[float],
        top_k: int = 10,
        project_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search across all code entity types (Function, Method, Class).

        This is a convenience method that searches all vector indexes
        and returns combined results sorted by similarity.

        Args:
            query_vector: Query embedding vector
            top_k: Number of results to return (total across all types)
            project_name: Optional project name to filter results

        Returns:
            List of dictionaries with qualified_name, name, type, and similarity
        """
        all_results: list[dict[str, Any]] = []

        index_type_map = [
            ("function_embedding_idx", "Function"),
            ("method_embedding_idx", "Method"),
            ("class_embedding_idx", "Class"),
        ]

        for index_name, node_type in index_type_map:
            try:
                hits = self.vector_search(
                    index_name=index_name,
                    query_vector=query_vector,
                    top_k=top_k,
                    project_name=project_name,
                )
                for hit in hits:
                    hit["type"] = node_type
                all_results.extend(hits)
            except Exception as e:
                logger.debug(f"Search on {index_name} failed: {e}")

        # Sort by similarity and limit to top_k
        all_results.sort(key=lambda x: x.get("similarity", 0), reverse=True)
        return all_results[:top_k]

    def delete_vector_indexes(self) -> int:
        """Delete all vector indexes for code entities.

        Returns:
            Number of indexes deleted
        """
        deleted = 0
        index_names = [
            "function_embedding_idx",
            "method_embedding_idx",
            "class_embedding_idx",
        ]

        for index_name in index_names:
            try:
                self._execute_query(f"DROP VECTOR INDEX {index_name}")
                logger.info(f"Deleted vector index: {index_name}")
                deleted += 1
            except Exception as e:
                if "not found" not in str(e).lower():
                    logger.debug(f"Could not delete index {index_name}: {e}")

        return deleted

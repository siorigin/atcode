# Copyright 2025 Vitali Avagyan.
# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: MIT AND Apache-2.0
#
# This file is derived from code-graph-rag (MIT License).
# Modifications by SiOrigin Co. Ltd. are licensed under Apache-2.0.
# See the LICENSE file in the root directory for details.

import os
import sys
import threading
import time
import traceback
from collections import OrderedDict, defaultdict
from collections.abc import Callable, ItemsView, KeysView
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from pathlib import Path
from threading import Lock
from typing import Any

from core.config import BINARY_FILE_EXTENSIONS, IGNORE_PATTERNS
from core.fqn_resolver import find_function_source_by_fqn
from core.fs_utils import walk_files
from core.git_executable import GIT
from core.gitignore_parser import GitIgnoreParser
from core.language_config import LANGUAGE_FQN_CONFIGS, get_language_config
from core.source_extraction import extract_source_with_fallback
from loguru import logger
from parser.factory import ProcessorFactory
from parser.processors.pending_call import PendingCall
from parser.processors.stdlib_checker import StdlibChecker
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from tree_sitter import Node, Parser

# Import optimizations
from .optimizations.cpu_limiter import CPUConfig, CPULimiter
from .optimizations.incremental import IncrementalBuilder, IncrementalDiff
from .optimizations.parser_pool import ParserPool
from .service import MemgraphIngestor

# Global exception hook for unhandled exceptions
original_excepthook = sys.excepthook


def global_exception_handler(exc_type, exc_value, exc_traceback):
    """Global exception handler to log all unhandled exceptions before process exit."""
    if exc_type is KeyboardInterrupt:
        # Allow normal keyboard interrupt
        original_excepthook(exc_type, exc_value, exc_traceback)
        return

    logger.critical(
        f"Unhandled exception: {exc_type.__name__}: {exc_value}\n"
        f"Thread: {threading.current_thread().name}\n"
        f"Traceback:\n{''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))}"
    )
    # Call original handler to maintain default behavior
    if original_excepthook:
        original_excepthook(exc_type, exc_value, exc_traceback)


# Install global exception handler
sys.excepthook = global_exception_handler

# Number of parallel workers for file I/O operations
DEFAULT_IO_WORKERS = min(32, (os.cpu_count() or 4) * 2)
# Batch size for embedding generation (API batch)
# OpenAI API supports up to 2048 texts per request, so use larger batches
# to minimize the number of API calls (each call has network latency)
EMBEDDING_BATCH_SIZE = 2048


# ---------------------------------------------------------------------------
# CollectingIngestor: lightweight stand-in used in child processes
# ---------------------------------------------------------------------------
class CollectingIngestor:
    """Drop-in replacement for MemgraphIngestor that collects operations without DB writes.

    Used in forked child processes for multiprocessing-based definition extraction.
    Each child gets its own instance, so no locks are needed.
    """

    __slots__ = ("nodes", "relationships")

    def __init__(self) -> None:
        self.nodes: list[tuple[str, dict]] = []
        self.relationships: list[tuple[tuple, str, tuple, dict | None]] = []

    def ensure_node_batch(self, label: str, properties: dict) -> None:
        self.nodes.append((label, properties))

    def ensure_relationship_batch(
        self,
        from_spec: tuple,
        rel_type: str,
        to_spec: tuple,
        properties: dict | None = None,
    ) -> None:
        self.relationships.append((from_spec, rel_type, to_spec, properties))

    def extend_node_buffer(self, items: list[tuple[str, dict]]) -> None:
        """Bulk-append nodes. Mirrors MemgraphIngestor.extend_node_buffer interface."""
        self.nodes.extend(items)

    def extend_relationship_buffer(self, items: list[tuple]) -> None:
        """Bulk-append relationships. Mirrors MemgraphIngestor.extend_relationship_buffer interface."""
        self.relationships.extend(items)


# ---------------------------------------------------------------------------
# Module-level globals for fork-based multiprocessing (Pass 1)
# Set in the parent BEFORE creating the ProcessPoolExecutor so that forked
# children inherit them via copy-on-write.
# ---------------------------------------------------------------------------
_mp_p1_file_list: "list[tuple[Path, bytes, str]] | None" = (
    None  # (filepath, content, language)
)
_mp_p1_call_processor: "Any | None" = (
    None  # CallProcessor (read-only for collect_pending_calls)
)
_mp_p1_queries: "dict | None" = None  # tree-sitter queries
_mp_p1_structural_elements: "dict | None" = None  # structural_elements map
_mp_p1_repo_path: "Path | None" = None
_mp_p1_project_name: "str | None" = None
_mp_p1_language_objects: "dict | None" = (
    None  # language name -> Language objects for parser creation
)


def _mp_process_definitions_batch(
    index_range: tuple[int, int],
) -> dict:
    """Multiprocessing worker for Pass 1 definition extraction.

    Runs in a forked child process. Inherits the parent's tree-sitter Query
    objects, language configs, and processor state via copy-on-write.

    For each file in the batch:
    1. Re-parses the AST from source bytes (tree-sitter Nodes can't survive fork)
    2. Runs definition extraction using a local CollectingIngestor
    3. Collects pending calls
    4. Returns all collected data

    Args:
        index_range: (start, end) indices into _mp_p1_file_list

    Returns:
        Dict with collected nodes, relationships, registry entries, etc.
    """
    from collections import defaultdict

    from parser.processors.definition import DefinitionProcessor
    from parser.processors.import_ import ImportProcessor
    from tree_sitter import Parser as TSParser

    queries = _mp_p1_queries
    repo_path = _mp_p1_repo_path
    project_name = _mp_p1_project_name
    structural_elements = _mp_p1_structural_elements
    parent_call_proc = _mp_p1_call_processor
    file_list = _mp_p1_file_list
    language_objects = _mp_p1_language_objects
    start, end = index_range

    # Create local collecting ingestor
    collecting_ingestor = CollectingIngestor()

    # Create local data structures (not shared with parent)
    local_simple_name_lookup = defaultdict(set)
    local_module_qn_to_file_path = {}

    # Simple function registry dict (mimics FunctionRegistryTrie interface)
    local_registry: dict[str, str] = {}

    # Create local ImportProcessor with collecting ingestor
    local_import_proc = ImportProcessor(
        repo_path_getter=lambda: repo_path,
        project_name_getter=lambda: project_name,
        ingestor=collecting_ingestor,
        function_registry=local_registry,
    )

    # Create local DefinitionProcessor with collecting ingestor
    local_def_proc = DefinitionProcessor(
        ingestor=collecting_ingestor,
        repo_path=repo_path,
        project_name=project_name,
        function_registry=local_registry,
        simple_name_lookup=local_simple_name_lookup,
        import_processor=local_import_proc,
        module_qn_to_file_path=local_module_qn_to_file_path,
    )

    # Create a local tree-sitter parser cache (language -> Parser)
    local_parsers: dict[str, TSParser] = {}

    def get_parser(lang: str) -> "TSParser | None":
        """Get or create a tree-sitter parser for the language."""
        if lang not in local_parsers:
            lang_obj = language_objects.get(lang) if language_objects else None
            if lang_obj is None:
                return None
            p = TSParser(lang_obj)
            local_parsers[lang] = p
        return local_parsers[lang]

    # Results to return
    all_pending_calls = []
    files_processed = 0

    # Sub-timing accumulators (seconds)
    t_parse = 0.0
    t_definitions = 0.0
    t_pending_calls = 0.0

    for idx in range(start, end):
        filepath, content, language = file_list[idx]

        try:
            # Re-parse AST in child process
            parser = get_parser(language)
            if parser is None:
                continue
            _t0 = time.time()
            tree = parser.parse(content)
            root_node = tree.root_node
            t_parse += time.time() - _t0

            # Process definitions
            _t1 = time.time()
            result = local_def_proc.process_file_with_ast(
                filepath,
                root_node,
                language,
                queries,
                structural_elements,
            )
            t_definitions += time.time() - _t1

            if result:
                # Collect pending calls using the parent's CallProcessor (read-only)
                # The CallProcessor is inherited via fork, its internal state is read-only
                _t2 = time.time()
                file_pending_calls = parent_call_proc.collect_pending_calls_in_file(
                    filepath, root_node, language, queries
                )
                if file_pending_calls:
                    all_pending_calls.extend(file_pending_calls)
                t_pending_calls += time.time() - _t2

            files_processed += 1

        except Exception:
            # Continue processing other files in the batch
            pass

    # Convert defaultdict sets to regular dict of lists for pickling
    simple_name_data = {k: list(v) for k, v in local_simple_name_lookup.items()}
    module_qn_data = {k: str(v) for k, v in local_module_qn_to_file_path.items()}

    return {
        "nodes": collecting_ingestor.nodes,
        "relationships": collecting_ingestor.relationships,
        "registry": local_registry,
        "simple_names": simple_name_data,
        "module_qn_paths": module_qn_data,
        "class_inheritance": dict(local_def_proc.class_inheritance),
        "import_mapping": dict(local_import_proc.import_mapping),
        "pending_calls": all_pending_calls,
        "files_processed": files_processed,
        "timings": {
            "parse": round(t_parse, 3),
            "definitions": round(t_definitions, 3),
            "pending_calls": round(t_pending_calls, 3),
        },
    }


class FunctionRegistryTrie:
    """Thread-safe Trie data structure optimized for function qualified name lookups.

    All write operations are protected by a lock to enable parallel definition processing.
    Read operations are lock-free for performance (dict reads are atomic in CPython).
    """

    def __init__(self) -> None:
        self.root: dict[str, Any] = {}
        self._entries: dict[str, str] = {}
        self._lock = Lock()  # Protects write operations
        # Cache for find_ending_with results (cleared on bulk inserts)
        self._suffix_cache: dict[str, list[str]] = {}
        self._cache_enabled = True  # Can be disabled during bulk operations

    def insert(self, qualified_name: str, func_type: str) -> None:
        """Insert a function into the trie. Thread-safe."""
        with self._lock:
            self._entries[qualified_name] = func_type

            # Build trie path from qualified name parts
            parts = qualified_name.split(".")
            current = self.root

            for part in parts:
                if part not in current:
                    current[part] = {}
                current = current[part]

            # Mark end of qualified name
            current["__type__"] = func_type
            current["__qn__"] = qualified_name

    def bulk_insert(self, entries: dict[str, str]) -> None:
        """Bulk-insert multiple entries under a single lock acquisition.

        Significantly reduces lock contention vs calling insert() per item,
        especially during parallel file processing where many threads call
        function_registry[qn] = type for every function/class in each file.
        """
        with self._lock:
            for qualified_name, func_type in entries.items():
                self._entries[qualified_name] = func_type

                parts = qualified_name.split(".")
                current = self.root
                for part in parts:
                    if part not in current:
                        current[part] = {}
                    current = current[part]

                current["__type__"] = func_type
                current["__qn__"] = qualified_name

    def get(self, qualified_name: str, default: str | None = None) -> str | None:
        """Get function type by exact qualified name."""
        return self._entries.get(qualified_name, default)

    def __contains__(self, qualified_name: str) -> bool:
        """Check if qualified name exists in registry."""
        return qualified_name in self._entries

    def __getitem__(self, qualified_name: str) -> str:
        """Get function type by qualified name."""
        return self._entries[qualified_name]

    def __setitem__(self, qualified_name: str, func_type: str) -> None:
        """Set function type for qualified name."""
        self.insert(qualified_name, func_type)

    def __delitem__(self, qualified_name: str) -> None:
        """Remove qualified name from registry and clean up trie structure. Thread-safe.

        Performs proper cleanup of the trie to prevent memory leaks during
        long-running sessions with file deletions/updates.
        """
        with self._lock:
            if qualified_name not in self._entries:
                return

            del self._entries[qualified_name]

            # Clean up trie structure by removing empty nodes
            parts = qualified_name.split(".")
            self._cleanup_trie_path(parts, self.root)

    def _cleanup_trie_path(self, parts: list[str], node: dict[str, Any]) -> bool:
        """Recursively clean up empty trie nodes.

        Args:
            parts: Remaining parts of the qualified name path
            node: Current trie node

        Returns:
            True if current node is empty and can be deleted
        """
        if not parts:
            # Remove the qualifier markers if they exist
            node.pop("__qn__", None)
            node.pop("__type__", None)
            # Node is empty if it has no children
            return len(node) == 0

        part = parts[0]
        if part not in node:
            return False  # Path doesn't exist

        # Recursively check if child can be cleaned up
        child_empty = self._cleanup_trie_path(parts[1:], node[part])

        # If child is empty and has no other qualified names, remove it
        if child_empty:
            del node[part]

        # A node can be cleaned up if it's not an endpoint and has no children.
        is_endpoint = "__qn__" in node
        has_children = any(not key.startswith("__") for key in node)
        return not has_children and not is_endpoint

    def keys(self) -> KeysView[str]:
        """Return all qualified names."""
        return self._entries.keys()

    def items(self) -> ItemsView[str, str]:
        """Return all (qualified_name, type) pairs."""
        return self._entries.items()

    def __len__(self) -> int:
        """Return number of entries."""
        return len(self._entries)

    def find_with_prefix_and_suffix(self, prefix: str, suffix: str) -> list[str]:
        """Find all qualified names that start with prefix and end with suffix."""
        results = []
        prefix_parts = prefix.split(".") if prefix else []

        # Navigate to prefix in trie
        current = self.root
        for part in prefix_parts:
            if part not in current:
                return []  # Prefix doesn't exist
            current = current[part]

        # DFS to find all entries under this prefix that end with suffix
        def dfs(node: dict[str, Any]) -> None:
            if "__qn__" in node:
                qn = node["__qn__"]
                if qn.endswith(f".{suffix}"):
                    results.append(qn)

            for key, child in node.items():
                if not key.startswith("__"):  # Skip metadata keys
                    dfs(child)

        dfs(current)
        return results

    def find_ending_with(self, suffix: str) -> list[str]:
        """Find all qualified names ending with the given suffix."""
        # Check cache first
        if self._cache_enabled and suffix in self._suffix_cache:
            return self._suffix_cache[suffix]

        # Compute and cache the result
        result = [qn for qn in self._entries.keys() if qn.endswith(f".{suffix}")]
        if self._cache_enabled:
            self._suffix_cache[suffix] = result
        return result

    def clear_suffix_cache(self) -> None:
        """Clear the suffix search cache. Call this after bulk inserts."""
        self._suffix_cache.clear()

    def disable_cache(self) -> None:
        """Disable caching during bulk operations."""
        self._cache_enabled = False

    def enable_cache(self) -> None:
        """Re-enable caching after bulk operations."""
        self._cache_enabled = True

    def find_with_prefix(self, prefix: str) -> list[tuple[str, str]]:
        """Find all qualified names that start with the given prefix.

        Args:
            prefix: The prefix to search for (e.g., "module.Class.method")

        Returns:
            List of (qualified_name, type) tuples matching the prefix
        """
        results = []
        prefix_parts = prefix.split(".")

        # Navigate to prefix in trie
        current = self.root
        for part in prefix_parts:
            if part not in current:
                return []  # Prefix doesn't exist
            current = current[part]

        # DFS to find all entries under this prefix
        def dfs(node: dict[str, Any]) -> None:
            if "__qn__" in node:
                qn = node["__qn__"]
                func_type = node["__type__"]
                results.append((qn, func_type))

            for key, child in node.items():
                if not key.startswith("__"):  # Skip metadata keys
                    dfs(child)

        dfs(current)
        return results

    # =========================================================================
    # Serialization methods for persistence (Phase 1 optimization)
    # =========================================================================

    def to_dict(self) -> dict[str, Any]:
        """Serialize registry to dictionary for JSON persistence.

        Returns:
            Dictionary containing entries and metadata for reconstruction
        """
        with self._lock:
            return {
                "version": 1,
                "entries": dict(self._entries),  # Copy entries
            }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FunctionRegistryTrie":
        """Deserialize registry from dictionary.

        Args:
            data: Dictionary from to_dict() or JSON load

        Returns:
            New FunctionRegistryTrie instance populated with data
        """
        registry = cls()

        if not data or "entries" not in data:
            return registry

        entries = data.get("entries", {})

        # Disable cache during bulk load for performance
        registry.disable_cache()

        try:
            # Bulk insert all entries
            for qn, func_type in entries.items():
                registry.insert(qn, func_type)
        finally:
            registry.enable_cache()
            registry.clear_suffix_cache()

        return registry

    def load_from_entries(self, entries: dict[str, str]) -> None:
        """Load entries from a flat dictionary (faster than from_dict for updates).

        Clears existing data and loads new entries.

        Args:
            entries: Dictionary mapping qualified_name to func_type
        """
        with self._lock:
            # Clear existing data
            self._entries.clear()
            self.root.clear()
            self._suffix_cache.clear()

        # Disable cache during bulk load
        self.disable_cache()

        try:
            for qn, func_type in entries.items():
                self.insert(qn, func_type)
        finally:
            self.enable_cache()

    def get_entries_copy(self) -> dict[str, str]:
        """Get a copy of all entries as a flat dictionary.

        Returns:
            Dictionary mapping qualified_name to func_type
        """
        with self._lock:
            return dict(self._entries)


class BoundedASTCache:
    """Memory-aware AST cache with automatic cleanup to prevent memory leaks.

    Uses LRU eviction strategy and monitors memory usage to maintain
    reasonable memory consumption during long-running analysis sessions.

    Thread-safe: All operations are protected by a lock for concurrent access.

    In multi-worker environments (5+ uvicorn workers), this cache monitors
    system memory pressure and triggers eviction when memory exceeds 75%
    to prevent OOM kills during parallel parsing.
    """

    _MEMORY_CHECK_INTERVAL = 100  # Check system memory every N cache operations
    _MEMORY_CHECK_COOLDOWN_SECS = 5.0  # Minimum seconds between memory checks
    _MEMORY_PRESSURE_THRESHOLD = 0.75  # Trigger eviction at 75% memory usage
    _EVICTION_TARGET_RATIO = 0.5  # Evict down to 50% of current size
    _MIN_CACHE_ENTRIES_AFTER_EVICTION = (
        100  # Keep at least this many entries after eviction
    )

    def __init__(self, max_entries: int = 10000, max_memory_mb: int = 2000):
        """Initialize the bounded AST cache.

        Args:
            max_entries: Maximum number of AST entries to cache (default 10000)
            max_memory_mb: Soft memory limit in MB for cache eviction (default 2000MB)
        """
        self.cache: OrderedDict[Path, tuple[Node, str]] = OrderedDict()
        self.max_entries = max_entries
        self.max_memory_bytes = max_memory_mb * 1024 * 1024
        self._lock = Lock()

        # Memory pressure monitoring
        self._operation_count = 0
        self._memory_check_interval = self._MEMORY_CHECK_INTERVAL
        self._last_memory_check = 0.0
        self._memory_check_cooldown = self._MEMORY_CHECK_COOLDOWN_SECS

    def __setitem__(self, key: Path, value: tuple[Node, str]) -> None:
        """Add or update an AST cache entry with automatic cleanup."""
        with self._lock:
            # Remove existing entry if present to update LRU order
            if key in self.cache:
                del self.cache[key]

            # Add new entry
            self.cache[key] = value

            # Evict entries if we exceed limits
            self._enforce_limits_unlocked()

        # Check for system memory pressure periodically (outside lock)
        self._maybe_check_memory_pressure()

    def __getitem__(self, key: Path) -> tuple[Node, str]:
        """Get AST cache entry and mark as recently used."""
        with self._lock:
            value = self.cache[key]
            # Move to end to mark as recently used
            self.cache.move_to_end(key)
            return value

    def __delitem__(self, key: Path) -> None:
        """Remove entry from cache."""
        with self._lock:
            if key in self.cache:
                del self.cache[key]

    def __contains__(self, key: Path) -> bool:
        """Check if key exists in cache."""
        with self._lock:
            return key in self.cache

    def __len__(self) -> int:
        """Return number of cached entries."""
        with self._lock:
            return len(self.cache)

    def items(self) -> list[tuple[Path, tuple[Node, str]]]:
        """Return all cache items as a list (thread-safe snapshot)."""
        with self._lock:
            return list(self.cache.items())

    def _enforce_limits_unlocked(self) -> None:
        """Enforce cache size and memory limits by evicting old entries.

        Note: Must be called with lock held.
        """
        # Check entry count limit
        while len(self.cache) > self.max_entries:
            self.cache.popitem(last=False)  # Remove least recently used

        # Check memory limit (rough estimate)
        if self._should_evict_for_memory_unlocked():
            entries_to_remove = max(1, len(self.cache) // 10)  # Remove 10% of entries
            logger.debug(
                f"Memory pressure: evicting {entries_to_remove} AST cache entries"
            )
            for _ in range(entries_to_remove):
                if self.cache:
                    self.cache.popitem(last=False)

    def _should_evict_for_memory_unlocked(self) -> bool:
        """Check if we should evict entries due to memory pressure.

        Note: Must be called with lock held.
        """
        try:
            # Use sys.getsizeof for a rough memory estimate
            cache_size = sum(sys.getsizeof(v) for v in self.cache.values())
            return cache_size > self.max_memory_bytes
        except Exception:
            # If memory checking fails, use conservative entry-based eviction
            return len(self.cache) > self.max_entries * 0.8

    def _check_system_memory_pressure(self) -> float:
        """Check current system memory usage ratio.

        Returns:
            Memory usage as a ratio (0.0 to 1.0), or 0.0 on error.
        """
        try:
            import psutil

            vm = psutil.virtual_memory()
            memory_ratio = vm.percent / 100.0
            logger.debug(
                f"System memory: {vm.percent:.1f}% used ({vm.available / 1024**3:.1f}GB available)"
            )
            return memory_ratio
        except ImportError:
            logger.debug("psutil not available for memory monitoring")
            return 0.0
        except Exception as e:
            logger.debug(f"Failed to check system memory: {e}")
            return 0.0

    def maybe_evict_for_memory_pressure(self) -> int:
        """Evict entries if system memory is under pressure.

        This method is called periodically (every N operations) to check
        system memory and evict cache entries if needed. In multi-worker
        environments, this prevents OOM kills during parallel parsing.

        Returns:
            Number of entries evicted, or 0 if no eviction needed.
        """
        import time

        current_time = time.time()

        # Rate limit memory checks to avoid overhead
        if current_time - self._last_memory_check < self._memory_check_cooldown:
            return 0

        self._last_memory_check = current_time

        memory_ratio = self._check_system_memory_pressure()

        if memory_ratio > self._MEMORY_PRESSURE_THRESHOLD:
            with self._lock:
                current_size = len(self.cache)
                if current_size == 0:
                    return 0

                # Evict down to target ratio of current size
                target_size = max(
                    self._MIN_CACHE_ENTRIES_AFTER_EVICTION,
                    int(current_size * self._EVICTION_TARGET_RATIO),
                )
                evicted = 0

                while len(self.cache) > target_size:
                    self.cache.popitem(last=False)  # Remove least recently used
                    evicted += 1

                if evicted > 0:
                    logger.warning(
                        f"Memory pressure ({memory_ratio:.1%}): evicted {evicted} entries "
                        f"(from {current_size} to {len(self.cache)})"
                    )
                return evicted

        return 0

    def _maybe_check_memory_pressure(self) -> None:
        """Periodically check memory pressure during cache operations.

        Called after every N operations to avoid checking on every access.
        Note: Must be called WITHOUT the lock held.
        """
        self._operation_count += 1
        if self._operation_count >= self._memory_check_interval:
            self._operation_count = 0
            self.maybe_evict_for_memory_pressure()


class GraphUpdater:
    """Parses code using Tree-sitter and updates the graph.

    Supports parallel file processing for improved performance on large codebases.
    """

    # --- Processing thresholds and intervals ---
    _DEFAULT_FILE_ESTIMATE = 5000  # Default file count for adaptive cache sizing
    _GENERIC_FILE_BATCH_SIZE = (
        100  # Batch size for processing generic (non-parseable) files
    )
    _GENERIC_PROGRESS_INTERVAL = 500  # Update task progress every N generic files
    _PROGRESS_REPORT_INTERVAL = (
        100  # Report progress every N files during parsing/definitions
    )
    _SEQUENTIAL_PROGRESS_INTERVAL = (
        50  # Update task progress every N files in sequential mode
    )
    _SLOW_FILE_THRESHOLD_SECS = (
        60.0  # Log warning for files taking longer than this (seconds)
    )
    _MAX_FILE_TIMEOUT_SECS = 180.0  # Skip files that exceed this timeout (seconds)

    def __init__(
        self,
        ingestor: MemgraphIngestor,
        repo_path: Path,
        parsers: dict[str, Parser],
        queries: dict[str, Any],
        cache_entries: int | None = None,
        cache_memory_mb: int | None = None,
        parallel_workers: int | None = None,
        skip_embeddings: bool = False,
        # New optimization parameters
        max_cpu_percent: int = 80,
        enable_incremental: bool = True,
        incremental_state_dir: Path | str | None = None,
        # Progress callback for external progress tracking (e.g., web UI)
        progress_callback: Callable[[int, str], None] | None = None,
        # Task ID for file-based cancellation check (shared filesystem environments)
        task_id: str | None = None,
        # Language objects for parallel parsing (optional, enables ParserPool)
        language_objects: dict[str, Any] | None = None,
        # Enable parallel AST parsing (requires language_objects)
        enable_parallel_parsing: bool = True,
        # Embedding granularity: "method" (all methods) or "class" (only class + function)
        # "class" is faster but less precise for method-level search
        # Defaults to config setting if not specified
        embedding_granularity: str | None = None,
        # Project name (defaults to repo_path.name if not specified)
        project_name: str | None = None,
        # Subdirectories to include (filters which directories are processed)
        # If None, all directories are processed. If provided, only these
        # subdirectory names (relative to repo_path) are included.
        subdirs: list[str] | None = None,
    ):
        """Initialize GraphUpdater.

        Args:
            ingestor: MemgraphIngestor instance for database operations
            repo_path: Path to the repository to analyze
            parsers: Dictionary of language name to Tree-sitter Parser
            queries: Dictionary of language name to query configuration
            cache_entries: Maximum AST cache entries (auto-calculated if None)
            cache_memory_mb: Maximum AST cache memory in MB (auto-calculated if None)
            parallel_workers: Number of parallel workers for file I/O (auto-calculated if None)
            skip_embeddings: If True, skip semantic embedding generation (faster builds)
            max_cpu_percent: Maximum CPU usage percentage (default: 80%)
            enable_incremental: Enable incremental builds (only process changed files)
            incremental_state_dir: Directory for incremental build state storage
            progress_callback: Optional callback function(progress: int, message: str) for external progress tracking
            language_objects: Dict of language name to Language objects (for ParserPool)
            enable_parallel_parsing: Enable parallel AST parsing (default: True)
            embedding_granularity: Embedding level - "class" (Class+Function only, faster) or "method" (all methods)
            project_name: Project name for the graph (defaults to repo_path.name if not specified)
            subdirs: Optional list of subdirectory names to include (filters processing)
        """
        self.ingestor = ingestor
        self.progress_callback = progress_callback
        self.repo_path = repo_path
        self.parsers = parsers
        self.queries = self._prepare_queries_with_parsers(queries, parsers)
        # Use provided project_name, or fall back to directory name
        self.project_name = project_name if project_name else repo_path.name
        self.function_registry = FunctionRegistryTrie()
        self.simple_name_lookup: dict[str, set[str]] = defaultdict(set)
        self.ignore_dirs = IGNORE_PATTERNS
        self.skip_embeddings = skip_embeddings

        # File size limit from config (KB -> bytes). 0 means no limit.
        from core.config import settings as _settings

        self._max_file_size_bytes: int = (
            _settings.MAX_FILE_SIZE_KB * 1024 if _settings.MAX_FILE_SIZE_KB > 0 else 0
        )

        # Get embedding granularity from config if not specified
        if embedding_granularity is None:
            from core.config import settings

            embedding_granularity = getattr(settings, "EMBEDDING_GRANULARITY", "method")
        self.embedding_granularity = embedding_granularity  # "class" or "method"

        self.subdirs = (
            set(subdirs) if subdirs else None
        )  # Store as set for faster lookup

        # Cancellation support - can be set from another thread
        self._cancelled = False
        self._cancel_lock = Lock()

        # Task ID for file-based cancellation check (shared filesystem environments)
        # When running in a multi-server setup with shared task_store directory,
        # the proxy server can cancel tasks by updating the task file status.
        # GraphUpdater checks this file periodically to detect cross-machine cancellation.
        self.task_id = task_id

        # Time-based cancellation checking for better responsiveness
        self._last_cancel_check = 0.0
        self._cancel_check_interval_sec = 0.5  # Check at least every 500ms

        # File-based cancellation check caching to reduce I/O overhead
        self._file_cancel_cache_time = 0.0
        self._file_cancel_cache_result = False
        self._file_cancel_cache_ttl = 0.2  # Cache for 200ms

        # External dependency tracking
        self.tracked_external_dependencies: set[str] = set()
        self.external_call_counts: dict[str, int] = defaultdict(int)

        # Pending calls for deferred resolution (replaces AST-based call processing)
        # Populated in Pass 1, resolved in Pass 2
        self.pending_calls: list[PendingCall] = []
        self._pending_calls_lock = Lock()  # Thread-safe for parallel collection

        # Track resolved call count for verification after flush
        self._last_resolved_call_count = 0

        # CPU limiter for controlled resource usage
        # Dynamic worker count: scale with available CPUs for maximum throughput.
        # Workers don't directly write to DB (they buffer), so connection contention is not an issue.
        # On 192-CPU machines, 64 workers gives ~3x speedup vs 16 while keeping IPC overhead low.
        # Override via ATCODE_BUILD_WORKERS environment variable.
        cpu_count = os.cpu_count() or 4
        default_workers = min(
            max(16, cpu_count // 3), 64
        )  # Scale: 16 min, cpu/3, 64 max
        optimal_workers = int(
            os.environ.get("ATCODE_BUILD_WORKERS", default_workers)
        )
        self.cpu_limiter = CPULimiter(
            CPUConfig(
                max_cpu_percent=90,  # Use full CPU - we have H100 servers
                max_workers=optimal_workers,
                nice_level=0,  # Normal priority for faster processing
                yield_interval_ms=20,  # No yielding - maximize throughput
                batch_yield_threshold=10000,  # Effectively disable yield
                use_cpu_affinity=False,  # Disable CPU affinity to avoid auto-scaling workers
                min_idle_threshold=80.0,  # Bind to cores with >80% idle
            )
        )

        # Parallel processing configuration (from CPU limiter)
        self.parallel_workers = self.cpu_limiter.get_max_workers()
        logger.info(
            f"Parallel workers: {self.parallel_workers} (CPU limit disabled for max throughput)"
        )

        # Parser pool for parallel AST parsing
        self.parser_pool: ParserPool | None = None
        self.language_objects = language_objects  # Store for multiprocessing Pass 1
        self.enable_parallel_parsing = enable_parallel_parsing
        if enable_parallel_parsing and language_objects:
            self.parser_pool = ParserPool(
                language_objects=language_objects,
                pool_size=self.parallel_workers,
            )
            logger.info(
                f"ParserPool initialized with {self.parallel_workers} instances per language"
            )
        elif enable_parallel_parsing:
            logger.debug("Parallel parsing disabled: language_objects not provided")

        # Thread-safe locks for shared state
        self._registry_lock = Lock()
        self._lookup_lock = Lock()
        self._precomputed_hash_lock = Lock()

        # Full-build hash cache reuse: populated during file processing so we
        # can skip a second repository scan after the build completes.
        self._precomputed_hash_cache: dict[str, str] | None = None
        self._precomputed_hash_targets: set[str] = set()
        self._precomputed_hash_stats: dict[str, float | int] = {}

        # Incremental build support
        self.enable_incremental = enable_incremental
        self.incremental_builder: IncrementalBuilder | None = None
        self._incremental_diff: IncrementalDiff | None = None

        if enable_incremental:
            self.incremental_builder = IncrementalBuilder(
                repo_path=repo_path,
                project_name=self.project_name,
                state_dir=incremental_state_dir,
                ignore_patterns=IGNORE_PATTERNS,
            )

        # Determine cache parameters
        if cache_entries is not None and cache_memory_mb is not None:
            # Use explicit overrides
            max_entries, max_memory_mb = cache_entries, cache_memory_mb
            logger.info(f"Manual cache: {max_entries} entries, {max_memory_mb}MB")
        else:
            # Use reasonable defaults instead of counting files (avoids full directory traversal)
            # BoundedASTCache uses LRU eviction, so oversized defaults are safe
            from core.config import calculate_adaptive_cache_params

            default_file_estimate = self._DEFAULT_FILE_ESTIMATE
            max_entries, max_memory_mb = calculate_adaptive_cache_params(
                default_file_estimate
            )
            logger.info(
                f"Adaptive cache (default estimate): {max_entries} entries, {max_memory_mb}MB"
            )

        # Adjust cache size based on number of workers to prevent memory exhaustion
        # In multi-worker environments, each worker has its own cache, so divide by worker count
        worker_count = int(os.environ.get("API_WORKERS", "1"))
        if worker_count > 1:
            memory_divisor = max(1, worker_count)
            original_entries, original_mb = max_entries, max_memory_mb
            max_memory_mb = max(500, max_memory_mb // memory_divisor)
            max_entries = max(1000, max_entries // memory_divisor)
            logger.info(
                f"Multi-worker adjustment ({worker_count} workers): "
                f"cache reduced from {original_entries} entries/{original_mb}MB "
                f"to {max_entries} entries/{max_memory_mb}MB"
            )

        self.ast_cache = BoundedASTCache(
            max_entries=max_entries, max_memory_mb=max_memory_mb
        )

        # Create processor factory with all dependencies
        self.factory = ProcessorFactory(
            ingestor=self.ingestor,
            repo_path_getter=lambda: self.repo_path,
            project_name_getter=lambda: self.project_name,
            queries=self.queries,
            function_registry=self.function_registry,
            simple_name_lookup=self.simple_name_lookup,
            ast_cache=self.ast_cache,
            subdirs=self.subdirs,
        )

    def _is_dependency_file(self, file_name: str, filepath: Path) -> bool:
        """Check if a file is a dependency file that should be processed for external dependencies."""
        dependency_files = {
            "pyproject.toml",
            "requirements.txt",
            "package.json",
            "cargo.toml",
            "go.mod",
            "gemfile",
            "composer.json",
        }

        # Check by filename
        if file_name.lower() in dependency_files:
            return True

        # Check by extension (for .csproj files)
        if filepath.suffix.lower() == ".csproj":
            return True

        return False

    def _report_progress(self, progress: int, message: str) -> None:
        """Report progress to external callback if available.

        Args:
            progress: Progress percentage (0-100)
            message: Human-readable status message
        """
        if self.progress_callback:
            try:
                self.progress_callback(progress, message)
            except Exception as e:
                logger.warning(f"Progress callback failed: {e}")
        logger.info(f"[{progress}%] {message}")

    def _get_head_commit_sha(self) -> str | None:
        """Get current HEAD commit SHA from the repository."""
        import subprocess

        try:
            result = subprocess.run(
                [GIT, "rev-parse", "HEAD"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception as e:
            logger.debug(f"Failed to get HEAD commit SHA: {e}")
        return None

    def _count_processable_files(self) -> int:
        """Count files that will be processed (excluding ignored paths and respecting subdirs filter)."""
        return len(
            walk_files(self.repo_path, self.ignore_dirs, subdirs=self.subdirs)
        )

    def _reset_precomputed_hash_cache(self) -> None:
        """Reset full-build hash cache reuse state."""
        with self._precomputed_hash_lock:
            self._precomputed_hash_cache = None
            self._precomputed_hash_targets = set()
            self._precomputed_hash_stats = {}

    def _setup_precomputed_hash_cache(
        self,
        candidate_files: list[Path],
        gitignore_parser: GitIgnoreParser,
    ) -> None:
        """Identify files whose hashes should be persisted after a full build."""
        self._reset_precomputed_hash_cache()
        if not self._is_full_build:
            return

        from graph.sync.watcher import ChangeWatcher

        start = time.perf_counter()
        hash_files = [
            fp
            for fp in candidate_files
            if not ChangeWatcher._should_ignore_static(
                fp, self.repo_path, self.ignore_dirs, gitignore_parser, self.subdirs
            )
        ]
        filter_ms = (time.perf_counter() - start) * 1000

        with self._precomputed_hash_lock:
            self._precomputed_hash_cache = {}
            self._precomputed_hash_targets = {
                str(fp.relative_to(self.repo_path)) for fp in hash_files
            }
            self._precomputed_hash_stats = {
                "candidate_count": len(candidate_files),
                "target_count": len(hash_files),
                "filter_ms": filter_ms,
                "other_hash_ms": 0.0,
                "other_hash_count": 0,
                "parseable_hash_count": 0,
            }

        logger.info(
            f"Prepared precomputed hash cache targets: {len(hash_files)}/{len(candidate_files)} "
            f"files in {filter_ms:.1f}ms"
        )

    def _should_collect_precomputed_hash(self, filepath: Path) -> bool:
        """Whether the file should be included in the persisted hash cache."""
        if self._precomputed_hash_cache is None:
            return False
        try:
            rel_path = str(filepath.relative_to(self.repo_path))
        except ValueError:
            return False
        return rel_path in self._precomputed_hash_targets

    def _store_precomputed_hash_from_bytes(self, filepath: Path, content: bytes) -> None:
        """Store a hash for a file whose bytes are already in memory."""
        if self._precomputed_hash_cache is None:
            return
        try:
            rel_path = str(filepath.relative_to(self.repo_path))
        except ValueError:
            return
        if rel_path not in self._precomputed_hash_targets:
            return

        from graph.sync.watcher import ChangeWatcher

        digest = ChangeWatcher._hash_bytes_static(content)
        with self._precomputed_hash_lock:
            if self._precomputed_hash_cache is not None:
                self._precomputed_hash_cache[rel_path] = digest
                self._precomputed_hash_stats["parseable_hash_count"] = (
                    int(self._precomputed_hash_stats.get("parseable_hash_count", 0)) + 1
                )

    def _precompute_hashes_for_files(self, files: list[Path]) -> None:
        """Hash non-parseable files during full build to avoid a second scan."""
        if self._precomputed_hash_cache is None or not files:
            return

        from graph.sync.watcher import ChangeWatcher

        files = [fp for fp in files if self._should_collect_precomputed_hash(fp)]
        if not files:
            return

        start = time.perf_counter()

        def hash_file(filepath: Path) -> tuple[str | None, str]:
            try:
                content = filepath.read_bytes()
                return (
                    str(filepath.relative_to(self.repo_path)),
                    ChangeWatcher._hash_bytes_static(content),
                )
            except Exception as e:
                logger.debug(f"Failed to precompute hash for {filepath}: {e}")
                return None, ""

        hashed_count = 0
        max_workers = min(8, len(files) or 1)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(hash_file, fp): fp for fp in files}
            for future in as_completed(futures):
                rel_path, digest = future.result()
                if rel_path and digest:
                    with self._precomputed_hash_lock:
                        if self._precomputed_hash_cache is not None:
                            self._precomputed_hash_cache[rel_path] = digest
                    hashed_count += 1

        duration_ms = (time.perf_counter() - start) * 1000
        with self._precomputed_hash_lock:
            self._precomputed_hash_stats["other_hash_ms"] = duration_ms
            self._precomputed_hash_stats["other_hash_count"] = hashed_count

        logger.info(
            f"Precomputed hashes for {hashed_count}/{len(files)} non-parseable files "
            f"in {duration_ms:.1f}ms ({max_workers} workers)"
        )

    def _write_precomputed_hash_cache(self) -> bool:
        """Persist the full-build hash cache collected during file processing."""
        with self._precomputed_hash_lock:
            if not self._precomputed_hash_cache:
                return False
            cache_data = dict(self._precomputed_hash_cache)
            stats = dict(self._precomputed_hash_stats)
            target_count = len(self._precomputed_hash_targets)

        from graph.sync.watcher import ChangeWatcher

        logger.info(
            f"Writing precomputed hash cache from full build: {len(cache_data)}/{target_count} "
            f"files pre-seeded (filter={stats.get('filter_ms', 0.0):.1f}ms)"
        )
        ChangeWatcher.build_initial_cache(
            self.repo_path,
            project_name=self.project_name,
            subdirs=self.subdirs,
            precomputed_cache=cache_data,
        )
        return True

    def _prepare_queries_with_parsers(
        self, queries: dict[str, Any], parsers: dict[str, Parser]
    ) -> dict[str, Any]:
        """Add parser references to query objects for processors."""
        updated_queries = {}
        for lang, query_data in queries.items():
            if lang in parsers:
                updated_queries[lang] = {**query_data, "parser": parsers[lang]}
            else:
                updated_queries[lang] = query_data
        return updated_queries

    def cancel(self) -> None:
        """Request cancellation of the running build.

        Thread-safe: can be called from any thread to stop the build.
        The build will stop at the next checkpoint.
        """
        with self._cancel_lock:
            self._cancelled = True
        logger.info(f"Cancellation requested for {self.project_name}")

    def is_cancelled(self) -> bool:
        """Check if cancellation has been requested.

        Thread-safe: can be called from any thread.

        In shared filesystem environments, also checks the task file status.
        This allows cross-machine cancellation: when a proxy server updates
        the task file to 'cancelled', GraphUpdater will detect it and stop.
        """
        with self._cancel_lock:
            if self._cancelled:
                return True

        # Check file-based cancellation for shared filesystem environments
        if self.task_id:
            if self._check_task_file_cancelled():
                # Set the internal flag so subsequent checks are fast
                with self._cancel_lock:
                    self._cancelled = True
                return True

        return False

    def _check_task_file_cancelled(self) -> bool:
        """Check if task file indicates cancellation.

        This enables cross-machine cancellation in shared filesystem deployments.
        The proxy server can cancel a task by updating the task file status,
        and GraphUpdater will detect this and stop.

        Uses caching to reduce I/O overhead from frequent file checks.

        Returns:
            True if task file exists and shows cancelled status, False otherwise
        """
        if not self.task_id:
            return False

        # Use cached result if still valid
        import time

        now = time.time()
        if now - self._file_cancel_cache_time < self._file_cancel_cache_ttl:
            return self._file_cancel_cache_result

        try:
            import json

            # Task store path is in data directory
            project_root = Path(__file__).parent.parent.parent
            task_file = project_root / "data" / "task_store" / f"{self.task_id}.json"

            if not task_file.exists():
                result = False
                self._file_cancel_cache_time = now
                self._file_cancel_cache_result = result
                return result

            with open(task_file) as f:
                data = json.load(f)
                status = data.get("status", "")
                if status == "cancelled":
                    logger.info(
                        f"Detected file-based cancellation for task {self.task_id}"
                    )
                    result = True
                    self._file_cancel_cache_time = now
                    self._file_cancel_cache_result = result
                    return result
        except Exception as e:
            # Don't fail the build if we can't read the task file
            logger.debug(f"Failed to check task file for cancellation: {e}")

        result = False
        self._file_cancel_cache_time = now
        self._file_cancel_cache_result = result
        return result

    def _check_cancelled(self, stage: str = "") -> None:
        """Check if cancelled and raise exception if so.

        Args:
            stage: Description of the current stage for logging

        Raises:
            RuntimeError: If build has been cancelled
        """
        import time

        self._last_cancel_check = time.time()
        if self.is_cancelled():
            msg = f"Build cancelled at {stage}" if stage else "Build cancelled"
            logger.info(msg)
            raise RuntimeError(msg)

    def _should_check_cancelled(self) -> bool:
        """Check if enough time has passed to warrant a cancellation check.

        Returns:
            True if at least _cancel_check_interval_sec has passed since last check
        """
        import time

        return time.time() - self._last_cancel_check >= self._cancel_check_interval_sec

    def run(self, force_full_build: bool = False) -> None:
        """Orchestrates the parsing and ingestion process.

        Uses parallel file I/O for improved performance on large codebases.
        Supports incremental builds to only process changed files.

        Args:
            force_full_build: If True, ignore incremental state and rebuild everything
        """
        self._report_progress(0, f"Starting graph build for {self.project_name}...")
        self._check_cancelled("startup")

        # Switch to ANALYTICAL mode for faster bulk writes (multi-threaded)
        # This benefits all build paths (API route, MCP route, direct calls)
        original_storage_mode = None
        constraints_dropped = False
        try:
            original_storage_mode = self.ingestor.get_storage_mode()
            if original_storage_mode != "IN_MEMORY_ANALYTICAL":
                logger.info(
                    f"Switching from {original_storage_mode} to IN_MEMORY_ANALYTICAL for bulk import"
                )
                # Drop constraints first — ANALYTICAL mode doesn't support them
                dropped = self.ingestor.drop_all_constraints()
                if dropped > 0:
                    constraints_dropped = True
                    logger.info(f"Dropped {dropped} constraints before ANALYTICAL switch")
                self.ingestor.set_storage_mode("IN_MEMORY_ANALYTICAL")
        except Exception as e:
            logger.warning(f"Failed to switch storage mode: {e}")
            original_storage_mode = None  # Don't attempt restore on failure

        try:
            self._run_impl(force_full_build)
        finally:
            # Clean up: stop background flusher if running, disable deferred mode
            self.ingestor.stop_background_flusher()
            self.ingestor._deferred_flush = False

            # Restore original storage mode after build
            if (
                original_storage_mode
                and original_storage_mode != "IN_MEMORY_ANALYTICAL"
            ):
                try:
                    logger.info(f"Restoring storage mode to {original_storage_mode}")
                    self.ingestor.set_storage_mode(original_storage_mode)
                except Exception as e:
                    logger.warning(f"Failed to restore storage mode: {e}")

            # Re-create constraints after switching back from ANALYTICAL mode
            if constraints_dropped:
                try:
                    self.ingestor.ensure_constraints()
                    logger.info("Re-created constraints after ANALYTICAL mode build")
                except Exception as e:
                    logger.warning(f"Failed to re-create constraints: {e}")

    def _run_impl(self, force_full_build: bool = False) -> None:
        """Internal implementation of the build process.

        Separated from run() to allow storage mode switch in the outer try/finally.

        Executes a multi-pass pipeline:
          Pass 1: Discover and categorize files (parseable, dependency, generic)
          Pass 2: Parse files, extract definitions, and create graph nodes
          Pass 3: Resolve call relationships between functions/methods
          Pass 4: Flush remaining data and finalize the graph

        Supports both full and incremental builds. In incremental mode, only
        changed files (detected via content hashing) are re-processed.

        Args:
            force_full_build: If True, skip incremental diffing and rebuild everything.
        """
        self._reset_precomputed_hash_cache()

        # Track if this is a full build (no incremental diff) for optimization decisions
        self._is_full_build = False

        self.ingestor.ensure_node_batch(
            "Project",
            {
                "name": self.project_name,
                "path": str(self.repo_path),
            },
        )
        logger.info(f"Ensuring Project: {self.project_name} (path: {self.repo_path})")
        logger.info(
            f"Using {self.parallel_workers} parallel workers for file processing"
        )

        # Compute incremental diff if enabled
        # Skip for first-time build (no previous state) - not needed and slow
        if (
            self.enable_incremental
            and self.incremental_builder
            and not force_full_build
        ):
            # Check if this is a first-time build (no previous state)
            has_previous_state = (
                self.incremental_builder.load_previous_state() is not None
            )
            if not has_previous_state:
                # First build - skip expensive diff computation, just do full build
                self._incremental_diff = None
                logger.info(
                    "No previous state found - full build required (state file may have been cleaned)"
                )
            else:
                self._report_progress(5, "Computing incremental diff...")
                self._incremental_diff = self.incremental_builder.compute_diff()
                if self._incremental_diff.has_changes:
                    logger.info(f"Incremental build: {self._incremental_diff}")
                else:
                    logger.info("No changes detected since last build")
                    # Still need to ensure the project exists in the database
                    self.ingestor.flush_all()
                    self._report_progress(100, "No changes detected - build complete")
                    return
        else:
            self._incremental_diff = None
            if force_full_build:
                logger.info("Forced full build (ignoring incremental state)")

        # Full build = no incremental diff (all files will be processed)
        self._is_full_build = self._incremental_diff is None

        console = Console()

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=False,
        ) as progress:
            # Pass 0: Handle deleted and modified files (for incremental builds)
            # Modified files need to be cleaned first, then re-processed in later passes
            if self._incremental_diff:
                # Handle deleted files
                if self._incremental_diff.deleted_files:
                    self._report_progress(
                        5,
                        f"Cleaning {len(self._incremental_diff.deleted_files)} deleted files...",
                    )
                    task0 = progress.add_task(
                        "Pass 0: Cleaning deleted files",
                        total=len(self._incremental_diff.deleted_files),
                    )
                    for rel_path in self._incremental_diff.deleted_files:
                        full_path = self.repo_path / rel_path
                        self.remove_file_from_state(full_path)
                        self.incremental_builder.mark_file_deleted(rel_path)
                        progress.update(task0, advance=1)
                        self.cpu_limiter.yield_cpu()

                # Handle modified files - remove old state before re-processing
                # This ensures CALLS relationships are properly updated
                if self._incremental_diff.modified_files:
                    self._report_progress(
                        7,
                        f"Cleaning {len(self._incremental_diff.modified_files)} modified files...",
                    )
                    task0_mod = progress.add_task(
                        "Pass 0: Cleaning modified files",
                        total=len(self._incremental_diff.modified_files),
                    )
                    for filepath in self._incremental_diff.modified_files:
                        self.remove_file_from_state(filepath)
                        progress.update(task0_mod, advance=1)
                        self.cpu_limiter.yield_cpu()

            # ================================================================
            # Pass 1: Structure Discovery + Definition Collection
            # (Merges original Pass 1 + Pass 2)
            # ================================================================
            self._check_cancelled("Pass 1")
            self._report_progress(
                10, "Pass 1: Discovering structure and collecting definitions..."
            )

            # Enable deferred flush mode: accumulate ALL nodes and relationships
            # in memory during CPU-bound passes (P1, P2, P3), then write
            # everything to DB in a single bulk operation in Pass 4.
            # This eliminates bg flusher overhead (polling, lock contention,
            # per-batch DB round-trips) and keeps CPU passes purely CPU-bound.
            self.ingestor.enable_deferred_flush(use_create=self._is_full_build)

            # Part 1a: Identify packages and folder structure (quick)
            # Initialize gitignore parser early so both identify_structure
            # and _process_files_parallel use consistent gitignore filtering.
            gitignore_parser = GitIgnoreParser(self.repo_path)
            gitignore_parser.load()
            self.factory.gitignore_parser = gitignore_parser
            self.factory.structure_processor.identify_structure()

            # Part 1b: Process files, collect definitions (uses its own progress bar)
            self._process_files_parallel(progress)
            self._report_progress(35, "Pass 1: Structure discovery complete")

            # Log pending calls collected
            logger.info(
                f"Pass 1 complete: {len(self.function_registry)} definitions, "
                f"{len(self.pending_calls)} pending calls collected"
            )

            # Update LocalModuleFilter with discovered external dependencies
            discovered = self.factory.import_processor.get_discovered_externals()
            if discovered:
                local_filter = self.factory.call_processor.local_module_filter
                filtered_externals = set()
                for module_name in discovered:
                    if local_filter.is_local_module(module_name):
                        continue
                    if self._is_stdlib_module(module_name):
                        continue
                    filtered_externals.add(module_name)

                if filtered_externals:
                    local_filter.add_tracked_externals(filtered_externals)
                    self.tracked_external_dependencies.update(filtered_externals)
                    logger.info(
                        f"Discovered external dependencies: {filtered_externals}"
                    )

            # Ensure builtin nodes exist for C++/JS builtins
            self._ensure_builtin_nodes()

            # Free AST cache memory when using PendingCall approach
            # PendingCall objects already contain all needed info for call resolution,
            # so the AST cache is no longer needed. _extract_source_code has line-based fallback.
            if self.pending_calls:
                ast_entries = len(self.ast_cache)
                self.ast_cache.cache.clear()
                logger.info(
                    f"Cleared AST cache ({ast_entries} entries) - using PendingCall approach"
                )

            # ================================================================
            # Pass 2: Call Resolution + Embedding Generation
            # (Merges original Pass 3 + Pass 6)
            # ================================================================
            self._check_cancelled("Pass 2")
            self._report_progress(40, "Pass 2: Resolving calls...")

            # Part 2a: Process function calls using pending calls approach
            # This uses PendingCall objects collected in Pass 1 instead of AST cache
            if self.pending_calls:
                self._resolve_pending_calls_with_progress(progress)
            else:
                # Fallback to AST-based approach if no pending calls collected
                # (for backwards compatibility or if collection was skipped)
                self._process_function_calls_with_progress(progress)

            # Collect external call counts
            if self.factory.call_processor.external_call_counts:
                for (
                    module,
                    count,
                ) in self.factory.call_processor.external_call_counts.items():
                    self.external_call_counts[module] += count

            # Part 2b: Generate embeddings (uses its own progress bar)
            # Check if embedding service is available before attempting generation
            if not self.skip_embeddings:
                from .embedder import is_embedding_available

                if not is_embedding_available():
                    logger.info(
                        "Skipping embeddings: embedding service not configured or unavailable"
                    )
                    self._report_progress(
                        70, "Pass 2: Skipped embeddings (service not available)"
                    )
                else:
                    self._report_progress(
                        55, "Pass 2: Generating semantic embeddings..."
                    )
                    self._generate_semantic_embeddings_batched(progress)
                    self._report_progress(
                        70, "Pass 2: Call resolution and embeddings complete"
                    )
            else:
                self._report_progress(
                    70, "Pass 2: Call resolution complete (embeddings skipped)"
                )

            # ================================================================
            # Pass 3: Post-processing (Method Overrides)
            # ================================================================
            self._check_cancelled("Pass 3")
            self._report_progress(75, "Pass 3: Processing method overrides...")
            task3 = progress.add_task("Pass 3: Method Overrides", total=100)
            self.factory.definition_processor.process_all_method_overrides()
            progress.update(task3, completed=100)

            # ================================================================
            # Pass 4: Persistence (Flush to Database)
            # ================================================================
            # Disable deferred flush mode so flush_all() actually writes to DB.
            # (In deferred mode, all nodes+rels accumulated in memory during P1-P3.)
            self.ingestor.disable_deferred_flush()

            self._check_cancelled("Pass 4")
            self._report_progress(80, "Pass 4: Writing data to database...")
            task4 = progress.add_task("Pass 4: Flushing to database", total=100)

            # Create progress callback to update rich progress bar
            def flush_progress(current, total):
                if total > 0:
                    percent = int((current / total) * 100)
                    progress.update(task4, completed=percent)

            self.ingestor.flush_all(
                use_create_rels=self._is_full_build, progress_callback=flush_progress
            )
            progress.update(task4, completed=100)
            self._report_progress(95, "Pass 4: Database write complete")

            # Verify relationship count after flush
            self._verify_relationship_count()

            # Store external dependency metadata
            self._store_external_dependency_metadata()

        # Save incremental build state (include current HEAD commit SHA)
        if self.enable_incremental and self.incremental_builder:
            built_sha = self._get_head_commit_sha()
            if built_sha:
                self.incremental_builder.set_metadata(
                    "built_commit_sha", built_sha
                )
            self.incremental_builder.finalize_build()

        # Build hash cache so sync can detect offline changes without a slow rebuild
        try:
            from graph.sync.watcher import ChangeWatcher

            ChangeWatcher.build_initial_cache(
                self.repo_path,
                subdirs=self.subdirs,
                project_name=self.project_name,
            )
        except Exception as e:
            logger.warning(f"Failed to build initial hash cache: {e}")

        self._report_progress(
            100,
            f"Graph build complete! Found {len(self.function_registry)} functions/methods",
        )
        logger.info(
            f"\n--- Found {len(self.function_registry)} functions/methods in codebase ---"
        )
        if self.tracked_external_dependencies:
            logger.info(
                f"--- Tracked external dependencies: {self.tracked_external_dependencies} ---"
            )
        logger.info("\n--- Analysis complete. ---")

    def _store_external_dependency_metadata(self) -> None:
        """Store project-level external dependency information."""
        if not self.tracked_external_dependencies and not self.external_call_counts:
            return

        # Update project node with external dependency info
        # Note: This requires the project node to already exist
        tracked_deps = list(self.tracked_external_dependencies)
        call_counts = dict(self.external_call_counts)

        if tracked_deps or call_counts:
            logger.info(
                f"Project has {len(tracked_deps)} tracked external dependencies "
                f"with {sum(call_counts.values())} total calls"
            )

    def _verify_relationship_count(self) -> None:
        """Verify that the expected number of CALLS relationships were created.

        This helps detect data loss issues during the flush process.
        Scoped to the current project to avoid counting relationships from other projects.

        Uses the deduped count from CSV flush (if available) for accurate comparison,
        since the call resolver may emit duplicate CALLS for the same caller→callee pair.
        """
        if self._last_resolved_call_count == 0:
            return  # No calls were resolved, nothing to verify

        try:
            # Query the actual count of CALLS relationships scoped to this project
            prefix = self.project_name + "."
            result = self.ingestor.fetch_all(
                "MATCH (a)-[r:CALLS]->() WHERE a.qualified_name STARTS WITH $prefix RETURN count(r) as cnt",
                {"prefix": prefix},
            )
            if result:
                actual_count = result[0]["cnt"]
                # Prefer deduped count from CSV flush (accurate), fall back to resolved count
                deduped_count = getattr(self.ingestor, "_last_deduped_calls_count", 0)
                expected_count = deduped_count if deduped_count > 0 else self._last_resolved_call_count

                # Allow some tolerance (5%) for relationships that couldn't be created
                # due to missing nodes (e.g., unresolved external dependencies)
                tolerance = 0.05
                min_expected = int(expected_count * (1 - tolerance))

                if actual_count < min_expected:
                    logger.warning(
                        f"RELATIONSHIP COUNT MISMATCH: Expected ~{expected_count} CALLS relationships, "
                        f"but only {actual_count} were created ({100 * actual_count / expected_count:.1f}%). "
                        f"This may indicate data loss during flush."
                    )
                else:
                    logger.info(
                        f"Relationship verification passed: {actual_count} CALLS relationships "
                        f"(expected ~{expected_count})"
                    )
        except Exception as e:
            logger.warning(f"Failed to verify relationship count: {e}")

    def _is_stdlib_module(self, module_name: str) -> bool:
        """Check if a module is part of the Python standard library.

        Delegates to StdlibChecker for unified stdlib detection across the codebase.

        Args:
            module_name: The root module name to check (e.g., "re", "os")

        Returns:
            True if the module is part of Python's standard library
        """
        return StdlibChecker.is_python_stdlib(module_name)

    def _ensure_builtin_nodes(self) -> None:
        """Create nodes for builtin functions/operators that may be called.

        Only creates builtins for languages that have parsers configured in this project.
        This ensures CALLS relationships to C++ operators and JS builtins can be established.
        Without these nodes, relationships like Function.X -> builtin.cpp.operator_plus would fail.
        """
        parser_languages = set(self.parsers.keys())
        has_cpp = bool(parser_languages & {"cpp", "c", "cuda"})
        has_js = bool(parser_languages & {"javascript", "typescript", "tsx", "jsx"})

        created_count = 0

        # C++ builtin operators (e.g. +, -, *, <) are NOT created as nodes.
        # They are language primitives, not meaningful code relationships.
        # Every `a + b` in CUDA code would generate a CALLS edge, producing
        # massive noise (e.g. 567K useless edges for sikernel vs 4K real ones).
        # User-defined operator overloads are handled by _resolve_cpp_operator_call
        # which checks simple_name_lookup for real definitions in the codebase.

        # JavaScript builtin prototypes (only if JS/TS parsers are active)
        if has_js:
            js_builtins = [
                # Function prototype methods
                "builtin.Function.prototype.bind",
                "builtin.Function.prototype.call",
                "builtin.Function.prototype.apply",
                # Array prototype methods
                "builtin.Array.prototype.push",
                "builtin.Array.prototype.pop",
                "builtin.Array.prototype.map",
                "builtin.Array.prototype.filter",
                "builtin.Array.prototype.reduce",
                "builtin.Array.prototype.forEach",
                "builtin.Array.prototype.find",
                "builtin.Array.prototype.findIndex",
                "builtin.Array.prototype.includes",
                "builtin.Array.prototype.slice",
                "builtin.Array.prototype.splice",
                "builtin.Array.prototype.concat",
                "builtin.Array.prototype.join",
                "builtin.Array.prototype.sort",
                "builtin.Array.prototype.reverse",
                # String prototype methods
                "builtin.String.prototype.split",
                "builtin.String.prototype.substring",
                "builtin.String.prototype.trim",
                "builtin.String.prototype.toLowerCase",
                "builtin.String.prototype.toUpperCase",
                "builtin.String.prototype.replace",
                "builtin.String.prototype.includes",
                "builtin.String.prototype.startsWith",
                "builtin.String.prototype.endsWith",
                # Global functions
                "builtin.console.log",
                "builtin.console.error",
                "builtin.console.warn",
                "builtin.JSON.parse",
                "builtin.JSON.stringify",
                "builtin.Object.keys",
                "builtin.Object.values",
                "builtin.Object.entries",
                "builtin.Object.assign",
                "builtin.Array.from",
                "builtin.Array.isArray",
                "builtin.Math.random",
                "builtin.Math.floor",
                "builtin.Math.ceil",
                "builtin.Math.round",
                "builtin.Math.abs",
                "builtin.Math.max",
                "builtin.Math.min",
                "builtin.Date.now",
            ]

            for qn in js_builtins:
                name = qn.split(".")[-1]
                self.ingestor.ensure_node_batch(
                    "Function",
                    {
                        "qualified_name": qn,
                        "name": name,
                        "is_builtin": True,
                    },
                )
                self.function_registry[qn] = "Function"
                self.simple_name_lookup[name].add(qn)
            created_count += len(js_builtins)

        if created_count > 0:
            logger.debug(f"Created {created_count} builtin function nodes")
        else:
            logger.debug(
                f"Skipped builtin nodes (no C++/JS parsers active, languages: {parser_languages})"
            )

    def remove_file_from_state(self, file_path: Path) -> None:
        """Removes all state associated with a file from the updater's memory and database.

        This method is called during incremental builds when a file is deleted.
        It cleans up:
        1. In-memory state (AST cache, function registry, simple name lookup)
        2. Database nodes (File, Function, Method, Class nodes defined by this file)
        """
        logger.debug(f"Removing state for: {file_path}")

        # Clear AST cache
        if file_path in self.ast_cache:
            del self.ast_cache[file_path]
            logger.debug("  - Removed from ast_cache")

        # Determine the module qualified name prefix for the file
        try:
            relative_path = file_path.relative_to(self.repo_path)
        except ValueError:
            logger.warning(f"File not relative to repo_path, skipping: {file_path}")
            return

        if file_path.name == "__init__.py":
            parent_parts = list(relative_path.parent.parts)
            if parent_parts and parent_parts[0] == self.project_name:
                parent_parts = parent_parts[1:]
            module_qn_prefix = ".".join(
                [self.project_name] + parent_parts
            )
        else:
            parts = list(relative_path.with_suffix("").parts)
            if parts and parts[0] == self.project_name:
                parts = parts[1:]
            module_qn_prefix = ".".join(
                [self.project_name] + parts
            )

        # We need to find all qualified names that belong to this file/module
        qns_to_remove = set()

        # Clean function_registry and collect qualified names to remove
        for qn in list(self.function_registry.keys()):
            if qn.startswith(module_qn_prefix + ".") or qn == module_qn_prefix:
                qns_to_remove.add(qn)
                del self.function_registry[qn]

        if qns_to_remove:
            logger.debug(
                f"  - Removing {len(qns_to_remove)} QNs from function_registry"
            )

        # Clean simple_name_lookup
        for simple_name, qn_set in self.simple_name_lookup.items():
            original_count = len(qn_set)
            new_qn_set = qn_set - qns_to_remove
            if len(new_qn_set) < original_count:
                self.simple_name_lookup[simple_name] = new_qn_set
                logger.debug(f"  - Cleaned simple_name '{simple_name}'")

        # Clean database nodes associated with this file
        # This is critical for proper incremental builds - without this,
        # deleted files leave orphan nodes in the graph
        try:
            deleted_count = self.ingestor.delete_file_nodes(
                project_name=self.project_name,
                file_path=str(relative_path),
                module_qn_prefix=module_qn_prefix,
            )
            if deleted_count > 0:
                logger.debug(f"  - Deleted {deleted_count} nodes from database")
        except Exception as e:
            logger.warning(f"Failed to delete file nodes from database: {e}")

    def _process_files_with_progress(self, progress: Progress, task_id: int) -> None:
        """Second pass with progress bar: Efficiently processes all files, parses them, and caches their ASTs."""

        # Initialize gitignore parser
        gitignore_parser = GitIgnoreParser(self.repo_path)
        gitignore_parser.load()

        # Collect all files using pruning walk (avoids entering ignored dirs)
        all_files = walk_files(
            self.repo_path, self.ignore_dirs, gitignore_parser=gitignore_parser
        )

        # Update progress bar total
        progress.update(task_id, total=len(all_files))

        # Process files with progress updates
        for filepath in all_files:
            # Check if this file type is supported for parsing
            lang_config = get_language_config(filepath.suffix)
            if lang_config and lang_config.name in self.parsers:
                # Parse as Module and cache AST
                result = self.factory.definition_processor.process_file(
                    filepath,
                    lang_config.name,
                    self.queries,
                    self.factory.structure_processor.structural_elements,
                )
                if result:
                    root_node, language = result
                    self.ast_cache[filepath] = (root_node, language)

                    # Collect pending calls for deferred resolution in Pass 2
                    file_pending_calls = (
                        self.factory.call_processor.collect_pending_calls_in_file(
                            filepath, root_node, language, self.queries
                        )
                    )
                    if file_pending_calls:
                        with self._pending_calls_lock:
                            self.pending_calls.extend(file_pending_calls)

                # Also create CONTAINS_FILE relationship for parseable files
                self.factory.structure_processor.process_generic_file(
                    filepath, filepath.name
                )

            elif self._is_dependency_file(filepath.name, filepath):
                self.factory.definition_processor.process_dependencies(filepath)
                # Also create CONTAINS_FILE relationship for dependency files
                self.factory.structure_processor.process_generic_file(
                    filepath, filepath.name
                )
            else:
                # Use StructureProcessor to handle generic files
                self.factory.structure_processor.process_generic_file(
                    filepath, filepath.name
                )

            # Update progress
            progress.update(task_id, advance=1)

    def _process_files_parallel(self, progress: Progress) -> None:
        """Second pass with parallel I/O and parsing: Efficiently processes files.

        This version uses:
        1. Parallel file reading (I/O bound)
        2. Parallel AST parsing using ParserPool (if available)
        3. Sequential definition processing (maintains correctness)
        4. Batched progress updates
        5. More frequent database flushes for large codebases
        6. Incremental build support (only process changed files)
        """

        # Initialize gitignore parser for this build
        gitignore_parser = GitIgnoreParser(self.repo_path)
        gitignore_parser.load()

        def should_process_file(filepath: Path) -> bool:
            """Check if file should be processed based on incremental build state."""
            if not self._incremental_diff:
                return True  # Full build - process all files
            # Only process added or modified files
            return filepath in self._incremental_diff.files_to_process

        def read_file_content(filepath: Path) -> tuple[Path, bytes | None, str | None]:
            """Read file content in parallel. Returns (path, content, error)."""
            try:
                content = filepath.read_bytes()
                return (filepath, content, None)
            except Exception as e:
                return (filepath, None, str(e))

        # Collect files using pruning walk (avoids entering ignored/hidden dirs)
        all_files = [
            fp
            for fp in walk_files(
                self.repo_path,
                self.ignore_dirs,
                gitignore_parser=gitignore_parser,
                subdirs=self.subdirs,
            )
            if should_process_file(fp)
        ]

        total_files = len(all_files)

        if self._incremental_diff:
            logger.info(f"Incremental: Found {total_files} candidate files to process")
        else:
            logger.info(f"Found {total_files} candidate files to filter and process")

        # Categorize files by type for efficient processing
        parseable_files: list[Path] = []
        dependency_files: list[Path] = []
        generic_files: list[Path] = []

        # Extensions to skip entirely (compiled files, caches, binary files, etc.)
        SKIP_EXTENSIONS = BINARY_FILE_EXTENSIONS | {
            ".pyc",
            ".pyo",
            ".pyd",
            ".o",
            ".a",
            ".so",
            ".dll",
            ".dylib",
            ".lib",
            ".rlib",
            ".bc",
            ".class",
            ".swp",
            ".swo",
            ".bak",
            ".tmp",
        }

        for filepath in all_files:
            if filepath.suffix.lower() in SKIP_EXTENSIONS:
                continue

            lang_config = get_language_config(filepath.suffix)
            if lang_config and lang_config.name in self.parsers:
                parseable_files.append(filepath)
            elif self._is_dependency_file(filepath.name, filepath):
                dependency_files.append(filepath)
            else:
                generic_files.append(filepath)

        # Calculate actual files to process (after filtering)
        actual_files_to_process = (
            len(parseable_files) + len(dependency_files) + len(generic_files)
        )
        skipped_count = total_files - actual_files_to_process
        logger.info(
            f"File categorization: {len(parseable_files)} parseable, {len(dependency_files)} dependency, {len(generic_files)} generic (skipped {skipped_count} compiled/binary files)"
        )

        # Create progress task with ACTUAL file count (after filtering)
        task_id = progress.add_task("  Parsing files", total=actual_files_to_process)

        if self._incremental_diff:
            logger.info(
                f"Incremental: Processing {actual_files_to_process} files with {self.parallel_workers} workers"
            )
        else:
            logger.info(
                f"Processing {actual_files_to_process} files with {self.parallel_workers} workers"
            )

        # Use config setting for flush threshold (default 5000)
        from core.config import settings

        flush_threshold = settings.MEMGRAPH_INTERMEDIATE_FLUSH_INTERVAL
        yield_threshold = 100  # Yield CPU every N files

        # Initialize processed counter (must be initialized before conditional blocks)
        processed = 0

        # Phase 1: Process parseable files
        if parseable_files:
            # Check cancellation before starting I/O
            self._check_cancelled("Pass 2 - Phase 1 start")

            # Use parallel parsing if ParserPool is available
            if self.parser_pool:
                processed = self._process_files_with_parser_pool(
                    parseable_files,
                    progress,
                    task_id,
                    processed,
                    flush_threshold,
                    yield_threshold,
                )
            else:
                # Fallback to sequential parsing (original behavior)
                processed = self._process_files_sequential(
                    parseable_files,
                    progress,
                    task_id,
                    processed,
                    flush_threshold,
                    yield_threshold,
                    read_file_content,
                )

        # Phase 2: Process dependency files
        self._check_cancelled("Pass 2 - Phase 2 start")
        for filepath in dependency_files:
            self.factory.definition_processor.process_dependencies(filepath)
            self.factory.structure_processor.process_generic_file(
                filepath, filepath.name
            )
            processed += 1
            progress.update(task_id, completed=processed)

            # Yield CPU periodically
            if processed % yield_threshold == 0:
                self.cpu_limiter.yield_cpu()

        # Phase 3: Process generic files in parallel
        if generic_files:
            self._check_cancelled("Pass 2 - Phase 3 start")
            logger.info(
                f"Phase 3: Processing {len(generic_files)} generic files (headers, configs, etc.)"
            )

            # Generic files don't need parsing, just file metadata
            batch_size = self._GENERIC_FILE_BATCH_SIZE
            total_generic = len(generic_files)
            generic_processed = 0

            for i in range(0, total_generic, batch_size):
                # Check cancellation every batch
                self._check_cancelled(f"Pass 2 - Phase 3 ({i} generic files)")
                batch = generic_files[i : i + batch_size]

                for filepath in batch:
                    self.factory.structure_processor.process_generic_file(
                        filepath, filepath.name
                    )

                generic_processed += len(batch)

                # Update progress every batch
                progress.update(task_id, completed=generic_processed)

                if (
                    generic_processed % self._GENERIC_PROGRESS_INTERVAL == 0
                    or generic_processed == total_generic
                ):
                    # Generic files are Phase 3 (35-40% range)
                    generic_progress = 35 + int((generic_processed / total_generic) * 5)
                    self._report_progress(
                        generic_progress,
                        f"Processing generic files... ({generic_processed}/{total_generic})",
                    )

                # Yield CPU after each batch
                self.cpu_limiter.yield_cpu()

            logger.info(f"Completed processing {total_generic} generic files")
            processed += generic_processed

        logger.info(f"Completed processing {total_files} files")

    def _process_files_with_parser_pool(
        self,
        parseable_files: list[Path],
        progress: Progress,
        task_id: int,
        processed: int,
        flush_threshold: int,
        yield_threshold: int,
    ) -> int:
        """Process parseable files using ParserPool for parallel AST parsing.

        This method:
        1. Reads all files in parallel
        2. Parses all ASTs in parallel using ParserPool
        3. Processes definitions sequentially (to maintain correctness)

        Args:
            parseable_files: List of files to process
            progress: Progress bar instance
            task_id: Progress bar task ID
            processed: Current count of processed files
            flush_threshold: Flush database every N files
            yield_threshold: Yield CPU every N files

        Returns:
            Updated processed count
        """
        from tree_sitter import Node

        # Step 1: Read all file contents in parallel
        file_contents: dict[Path, bytes] = {}
        file_languages: dict[Path, str] = {}

        def read_file(
            filepath: Path,
        ) -> tuple[Path, bytes | None, str | None, str | None]:
            """Read file and determine language."""
            try:
                content = filepath.read_bytes()
                lang_config = get_language_config(filepath.suffix)
                language = lang_config.name if lang_config else None
                return (filepath, content, language, None)
            except Exception as e:
                return (filepath, None, None, str(e))

        phase1a_start = time.time()
        logger.info(f"Phase 1a: Reading {len(parseable_files)} files in parallel...")
        with self.cpu_limiter.create_thread_pool() as executor:
            futures = {executor.submit(read_file, fp): fp for fp in parseable_files}
            for future in as_completed(futures):
                if self.is_cancelled():
                    executor.shutdown(wait=False, cancel_futures=True)
                    self._check_cancelled("Pass 2 - Phase 1a I/O")
                filepath, content, language, error = future.result()
                if content is not None and language is not None:
                    file_contents[filepath] = content
                    file_languages[filepath] = language
                elif error:
                    logger.debug(f"Failed to read {filepath}: {error}")

        phase1a_time = time.time() - phase1a_start
        logger.info(
            f"Phase 1a complete: Read {len(file_contents)} files in {phase1a_time:.1f}s"
        )

        # Step 2 & 3: Parse ASTs and process definitions
        # When multiprocessing is available, skip separate Phase 1b (AST parsing)
        # because child processes re-parse ASTs from source bytes (tree-sitter Nodes
        # can't survive fork). This saves time by avoiding redundant parsing.
        use_multiprocessing = self.language_objects is not None

        phase1b_start = time.time()
        parsed_asts: dict[Path, tuple[Node, str]] = {}

        if not use_multiprocessing:
            # Fallback: Parse ASTs in threads (needed for threaded definition processing)
            logger.info(
                f"Phase 1b: Parsing {len(file_contents)} ASTs in parallel using ParserPool..."
            )

            _max_size = self._max_file_size_bytes or (1024 * 1024)  # fallback 1MB

            def parse_file(
                filepath: Path,
            ) -> tuple[Path, Node | None, str | None, str | None]:
                """Parse a single file using ParserPool."""
                try:
                    content = file_contents[filepath]
                    language = file_languages[filepath]
                    if len(content) > _max_size:
                        return (
                            filepath,
                            None,
                            None,
                            f"File too large ({len(content)} bytes)",
                        )
                    with self.parser_pool.get_parser(language) as parser:
                        tree = parser.parse(content)
                        return (filepath, tree.root_node, language, None)
                except Exception as e:
                    return (filepath, None, None, str(e))

            parsed_count = 0
            with self.cpu_limiter.create_thread_pool() as executor:
                futures = {
                    executor.submit(parse_file, fp): fp for fp in file_contents.keys()
                }
                for future in as_completed(futures):
                    if self.is_cancelled():
                        executor.shutdown(wait=False, cancel_futures=True)
                        self._check_cancelled("Pass 2 - Phase 1b parsing")
                    filepath, root_node, language, error = future.result()
                    if root_node is not None and language is not None:
                        parsed_asts[filepath] = (root_node, language)
                        parsed_count += 1
                        if parsed_count % self._PROGRESS_REPORT_INTERVAL == 0:
                            logger.info(
                                f"Parsed {parsed_count}/{len(file_contents)} files"
                            )
                    elif error:
                        logger.debug(f"Failed to parse {filepath}: {error}")
        else:
            logger.info(
                "Phase 1b: Skipping separate AST parsing (threaded_direct combines parse+extract)"
            )

        phase1b_time = time.time() - phase1b_start
        logger.info(f"Phase 1b complete in {phase1b_time:.1f}s")

        phase1c_start = time.time()
        total_files = len(parseable_files)

        # Always use threading: tree-sitter releases the GIL so parsing runs in
        # true parallel, and DefinitionProcessor is already thread-safe.  This
        # eliminates the IPC/pickle overhead of multiprocessing (Direction 2).
        if self.language_objects is not None:
            processed = self._process_definitions_threaded_direct(
                parseable_files,
                file_contents,
                file_languages,
                progress,
                task_id,
                total_files,
            )
        elif parsed_asts:
            processed = self._process_definitions_threaded(
                parseable_files, parsed_asts, progress, task_id, total_files
            )
        else:
            processed = 0

        phase1c_time = time.time() - phase1c_start
        logger.info(
            f"Phase 1c complete: Processed {processed} files in {phase1c_time:.1f}s"
        )
        logger.info(
            f"Pass 1 sub-timings: Read={phase1a_time:.1f}s, Parse={phase1b_time:.1f}s, Definitions={phase1c_time:.1f}s"
        )

        return processed

    def _process_definitions_threaded_direct(
        self,
        parseable_files: list[Path],
        file_contents: dict[Path, bytes],
        file_languages: dict[Path, str],
        progress: Progress,
        task_id: int,
        total_files: int,
    ) -> int:
        """Process definitions using threads with direct shared-state access.

        Unlike _process_definitions_multiprocess which forks child processes
        (requiring pickle serialization of results + parent merge loop),
        this method uses ThreadPoolExecutor where each thread:
        1. Creates a thread-local tree-sitter parser
        2. Parses the file (C extension releases the GIL — true parallelism)
        3. Calls the shared DefinitionProcessor directly (already thread-safe)
        4. Collects pending calls directly into the shared list

        Benefits over multiprocessing:
        - Zero IPC/pickle overhead (~28K nodes + ~45K rels skip serialization)
        - No parent merge loop (threads write directly to shared structures)
        - Shared registries/buffers eliminate data copying
        - tree-sitter parsing still runs in parallel (GIL released in C extension)

        Args:
            parseable_files: List of files to process
            file_contents: Pre-read file contents
            file_languages: Language for each file
            progress: Progress bar
            task_id: Progress bar task ID
            total_files: Total number of files

        Returns:
            Number of files processed
        """
        from tree_sitter import Parser as TSParser

        workers = self.parallel_workers
        language_objects = self.language_objects
        logger.info(
            f"Phase 1c: Processing {total_files} definitions with THREADS "
            f"({workers} workers, tree-sitter releases GIL)..."
        )

        # Build file list, skipping very large files (configurable via MAX_FILE_SIZE_KB)
        max_size = self._max_file_size_bytes or (1024 * 1024)  # fallback 1MB
        mp_file_list: list[tuple[Path, bytes, str]] = []
        for fp in parseable_files:
            if fp in file_contents and fp in file_languages:
                content = file_contents[fp]
                if len(content) > max_size:
                    logger.info(
                        f"Skipping large file ({len(content) // 1024}KB > {max_size // 1024}KB limit): {fp.name}"
                    )
                    continue
                mp_file_list.append((fp, content, file_languages[fp]))

        if not mp_file_list:
            logger.warning("No files to process in threaded mode")
            return 0

        # Thread-local storage for parsers (one per language per thread)
        _thread_local = threading.local()

        def _get_thread_parser(lang: str) -> "TSParser | None":
            """Get or create a thread-local tree-sitter parser for the language."""
            parsers = getattr(_thread_local, "parsers", None)
            if parsers is None:
                _thread_local.parsers = {}
                parsers = _thread_local.parsers
            if lang not in parsers:
                lang_obj = language_objects.get(lang) if language_objects else None
                if lang_obj is None:
                    return None
                parsers[lang] = TSParser(lang_obj)
            return parsers[lang]

        # Shared counters (thread-safe)
        processed_count = [0]
        count_lock = Lock()

        # Sub-timing accumulators (approximate, no lock needed for floats)
        agg_parse = [0.0]
        agg_defs = [0.0]
        agg_calls = [0.0]

        # Pre-compute structure processor ref for thread access
        _structure_processor = self.factory.structure_processor

        def process_file_batch(
            batch: list[tuple[Path, bytes, str]],
        ) -> int:
            """Process a batch of files: parse AST + extract definitions + collect calls + structure.

            Processing a batch per future instead of one file per future reduces:
            - Future creation overhead (N files -> N/batch_size futures)
            - GIL acquisitions for progress/lock updates
            - Lock contention on _pending_calls_lock (one bulk extend per batch)
            - as_completed iteration overhead
            """
            batch_processed = 0
            # Collect pending calls locally, extend shared list once per batch
            batch_pending_calls: list = []

            for filepath, content, language in batch:
                try:
                    # Step 1: Parse AST (C extension releases GIL — true parallel)
                    parser = _get_thread_parser(language)
                    if parser is None:
                        _structure_processor.process_generic_file(
                            filepath, filepath.name
                        )
                        continue
                    t0 = time.time()
                    tree = parser.parse(content)
                    root_node = tree.root_node
                    agg_parse[0] += time.time() - t0

                    # Step 2: Extract definitions using shared DefinitionProcessor
                    # (already thread-safe via threading.local + locks)
                    t1 = time.time()
                    result = (
                        self.factory.definition_processor.process_file_with_ast(
                            filepath,
                            root_node,
                            language,
                            self.queries,
                            _structure_processor.structural_elements,
                        )
                    )
                    agg_defs[0] += time.time() - t1

                    if result:
                        # Step 3: Collect pending calls (read-only access to
                        # CallProcessor state). Accumulate locally to reduce
                        # lock contention — one bulk extend per batch.
                        t2 = time.time()
                        file_pending_calls = self.factory.call_processor.collect_pending_calls_in_file(
                            filepath, root_node, language, self.queries
                        )
                        if file_pending_calls:
                            batch_pending_calls.extend(file_pending_calls)
                        agg_calls[0] += time.time() - t2

                    # Step 4: Create CONTAINS_FILE relationship (thread-safe
                    # via buffer lock). Done inside the worker to avoid
                    # sequential post-processing loop.
                    _structure_processor.process_generic_file(
                        filepath, filepath.name
                    )

                    batch_processed += 1

                except Exception:
                    # Still create CONTAINS_FILE even on error
                    try:
                        _structure_processor.process_generic_file(
                            filepath, filepath.name
                        )
                    except Exception:
                        pass

            # Bulk-extend pending calls once per batch (1 lock vs N locks)
            if batch_pending_calls:
                with self._pending_calls_lock:
                    self.pending_calls.extend(batch_pending_calls)

            return batch_processed

        # Split files into batches — one batch per worker thread.
        # This reduces future count from N to num_workers, minimizing
        # scheduling overhead, GIL contention, and lock acquisitions.
        num_batches = max(1, min(workers, len(mp_file_list)))
        batch_size = (len(mp_file_list) + num_batches - 1) // num_batches
        file_batches: list[list[tuple[Path, bytes, str]]] = []
        for i in range(0, len(mp_file_list), batch_size):
            file_batches.append(mp_file_list[i : i + batch_size])
        logger.info(
            f"Split {len(mp_file_list)} files into {len(file_batches)} batches "
            f"(~{batch_size} files/batch)"
        )

        # Process file batches using ThreadPoolExecutor
        try:
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="def-worker"
            ) as executor:
                futures = {
                    executor.submit(process_file_batch, batch): batch
                    for batch in file_batches
                }

                for future in as_completed(futures):
                    try:
                        batch_result = future.result()
                    except Exception:
                        batch_result = 0

                    with count_lock:
                        processed_count[0] += batch_result
                        current = processed_count[0]

                    # Update progress bar
                    progress.update(task_id, completed=current)
                    if (
                        current % self._PROGRESS_REPORT_INTERVAL == 0
                        or current >= total_files
                    ):
                        file_progress = 10 + int((current / total_files) * 25)
                        self._report_progress(
                            file_progress,
                            f"Processing definitions... ({current}/{total_files})",
                        )

        except Exception as e:
            logger.warning(f"Threaded definition processing failed: {e}")
            import traceback

            logger.warning(traceback.format_exc())

        processed = processed_count[0]

        # Note: CONTAINS_FILE relationships are now created inside the parallel
        # worker (process_single_file Step 4), eliminating the sequential loop.

        # Update incremental state for all processed files
        if self.incremental_builder:
            for fp in parseable_files:
                if fp in file_languages:
                    self.incremental_builder.update_file_state(
                        fp, language=file_languages[fp]
                    )

        # Log timing breakdown
        logger.info(
            f"Phase 1c threaded complete: {processed} files, "
            f"{len(self.function_registry)} registry entries, "
            f"{len(self.pending_calls)} pending calls"
        )
        logger.info(
            f"Phase 1c THREAD timings (summed across {workers} workers): "
            f"parse={agg_parse[0]:.1f}s, definitions={agg_defs[0]:.1f}s, "
            f"pending_calls={agg_calls[0]:.1f}s, "
            f"total_cpu={agg_parse[0] + agg_defs[0] + agg_calls[0]:.1f}s"
        )

        return processed

    def _process_definitions_multiprocess(
        self,
        parseable_files: list[Path],
        file_contents: dict[Path, bytes],
        file_languages: dict[Path, str],
        progress: Progress,
        task_id: int,
        total_files: int,
    ) -> int:
        """Process definitions using multiprocessing for true CPU parallelism.

        Uses fork-based multiprocessing to bypass the GIL. Each child process:
        1. Re-parses ASTs from source bytes (tree-sitter Nodes can't survive fork)
        2. Extracts definitions using a local CollectingIngestor
        3. Collects pending calls
        4. Returns lightweight results

        Parent process then merges results into its own state.
        Falls back to threaded processing if multiprocessing fails.
        """
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor, as_completed

        workers = self.parallel_workers
        logger.info(
            f"Phase 1c: Processing {total_files} definitions with MULTIPROCESSING "
            f"({workers} workers, bypassing GIL)..."
        )

        # Build file list for child processes: (filepath, content, language)
        # Skip very large files (configurable via MAX_FILE_SIZE_KB)
        max_size = self._max_file_size_bytes or (1024 * 1024)  # fallback 1MB
        mp_file_list: list[tuple[Path, bytes, str]] = []
        for fp in parseable_files:
            if fp in file_contents and fp in file_languages:
                content = file_contents[fp]
                if len(content) > max_size:
                    logger.info(
                        f"Skipping large file ({len(content) // 1024}KB > {max_size // 1024}KB limit): {fp.name}"
                    )
                    continue
                mp_file_list.append((fp, content, file_languages[fp]))

        if not mp_file_list:
            logger.warning("No files to process in multiprocess mode")
            return 0

        # Set module-level globals before fork (inherited via copy-on-write)
        global _mp_p1_file_list, _mp_p1_call_processor, _mp_p1_queries
        global _mp_p1_structural_elements, _mp_p1_repo_path, _mp_p1_project_name
        global _mp_p1_language_objects

        _mp_p1_file_list = mp_file_list
        _mp_p1_call_processor = self.factory.call_processor
        _mp_p1_queries = self.queries
        _mp_p1_structural_elements = (
            self.factory.structure_processor.structural_elements
        )
        _mp_p1_repo_path = self.repo_path
        _mp_p1_project_name = self.project_name
        _mp_p1_language_objects = self.language_objects

        # Create index ranges for batches.
        # Target ~2x workers batches for good load balancing while keeping
        # IPC overhead low (each batch result is pickle-serialized).
        # Minimum 100 files/batch to amortize per-batch overhead.
        batch_size = max(100, len(mp_file_list) // (workers * 2))
        index_ranges = [
            (i, min(i + batch_size, len(mp_file_list)))
            for i in range(0, len(mp_file_list), batch_size)
        ]
        logger.info(
            f"Split {len(mp_file_list)} files into {len(index_ranges)} batches "
            f"of ~{batch_size} ({workers} workers)"
        )

        processed = 0
        # Aggregate worker sub-timings across all batches
        agg_worker_parse = 0.0
        agg_worker_defs = 0.0
        agg_worker_calls = 0.0
        # Parent-side merge timing
        t_ipc = 0.0  # future.result() deserialization
        t_merge = 0.0  # merging data into parent state
        batches_completed = 0

        try:
            ctx = multiprocessing.get_context("fork")

            with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as executor:
                futures = {
                    executor.submit(_mp_process_definitions_batch, idx_range): idx_range
                    for idx_range in index_ranges
                }

                for future in as_completed(futures):
                    # Time IPC deserialization
                    _t_ipc0 = time.time()
                    try:
                        batch_result = future.result()
                    except Exception as e:
                        logger.warning(f"Multiprocess batch failed: {e}")
                        continue
                    t_ipc += time.time() - _t_ipc0

                    # Aggregate worker timings
                    wt = batch_result.get("timings", {})
                    agg_worker_parse += wt.get("parse", 0)
                    agg_worker_defs += wt.get("definitions", 0)
                    agg_worker_calls += wt.get("pending_calls", 0)
                    batches_completed += 1

                    # Time merge operations
                    _t_merge0 = time.time()

                    # Merge nodes into real ingestor (bulk append to reduce lock overhead)
                    self.ingestor.extend_node_buffer(batch_result["nodes"])

                    # Merge relationships into real ingestor (bulk append)
                    self.ingestor.extend_relationship_buffer(
                        batch_result["relationships"]
                    )

                    # Merge function registry
                    for qn, node_type in batch_result["registry"].items():
                        self.function_registry[qn] = node_type

                    # Merge simple name lookup
                    for name, qn_list in batch_result["simple_names"].items():
                        for qn in qn_list:
                            self.simple_name_lookup[name].add(qn)

                    # Merge module_qn_to_file_path
                    for module_qn, path_str in batch_result["module_qn_paths"].items():
                        self.factory.module_qn_to_file_path[module_qn] = Path(path_str)

                    # Merge class inheritance
                    for class_qn, parents in batch_result["class_inheritance"].items():
                        self.factory.definition_processor.class_inheritance[
                            class_qn
                        ] = parents

                    # Merge import mapping
                    for module_qn, mapping in batch_result["import_mapping"].items():
                        self.factory.import_processor.import_mapping[module_qn] = (
                            mapping
                        )

                    # Merge pending calls
                    if batch_result["pending_calls"]:
                        self.pending_calls.extend(batch_result["pending_calls"])

                    batch_processed = batch_result["files_processed"]
                    processed += batch_processed

                    t_merge += time.time() - _t_merge0

                    # Update progress
                    progress.update(task_id, completed=processed)
                    if (
                        processed % self._PROGRESS_REPORT_INTERVAL == 0
                        or processed >= total_files
                    ):
                        file_progress = 10 + int((processed / total_files) * 25)
                        self._report_progress(
                            file_progress,
                            f"Processing definitions... ({processed}/{total_files})",
                        )

                    # No proactive flush here — the background flusher thread
                    # (started before Pass 1) drains the buffer periodically.
                    # Any remaining data is flushed in Pass 4's flush_all().
                    # This keeps the merge loop fully CPU-bound (~20s vs ~370s
                    # when blocking on synchronous DB writes).

            # Create CONTAINS_FILE relationships (must be done in parent)
            for fp in parseable_files:
                self.factory.structure_processor.process_generic_file(fp, fp.name)

            # Update incremental state for all processed files
            if self.incremental_builder:
                for fp in parseable_files:
                    if fp in file_languages:
                        self.incremental_builder.update_file_state(
                            fp, language=file_languages[fp]
                        )

            # Log detailed Phase 1c timing breakdown
            logger.info(
                f"Phase 1c multiprocess complete: {processed} files, "
                f"{len(self.function_registry)} registry entries, "
                f"{len(self.pending_calls)} pending calls"
            )
            logger.info(
                f"Phase 1c WORKER timings (summed across {batches_completed} batches, "
                f"{workers} workers): "
                f"parse={agg_worker_parse:.1f}s, definitions={agg_worker_defs:.1f}s, "
                f"pending_calls={agg_worker_calls:.1f}s, "
                f"total_worker_cpu="
                f"{agg_worker_parse + agg_worker_defs + agg_worker_calls:.1f}s"
            )
            logger.info(
                f"Phase 1c PARENT timings: "
                f"ipc_deserialize={t_ipc:.1f}s, merge={t_merge:.1f}s, "
                f"total_parent_wallclock={t_ipc + t_merge:.1f}s "
                f"(DB writes deferred to background flusher + Pass 4)"
            )
            # Per-file averages
            if processed > 0:
                logger.info(
                    f"Phase 1c per-file averages: "
                    f"parse={1000 * agg_worker_parse / processed:.2f}ms, "
                    f"definitions={1000 * agg_worker_defs / processed:.2f}ms, "
                    f"pending_calls={1000 * agg_worker_calls / processed:.2f}ms"
                )

        except Exception as e:
            logger.warning(f"Multiprocessing failed, falling back to threaded: {e}")
            import traceback

            logger.warning(traceback.format_exc())
            # Clean up globals
            _mp_p1_file_list = None
            _mp_p1_call_processor = None
            _mp_p1_queries = None
            _mp_p1_structural_elements = None
            _mp_p1_repo_path = None
            _mp_p1_project_name = None
            _mp_p1_language_objects = None
            # Fallback: re-parse ASTs and use threaded approach
            from tree_sitter import Node

            parsed_asts: dict[Path, tuple[Node, str]] = {}
            for fp in parseable_files:
                if fp in file_contents and fp in file_languages:
                    try:
                        lang = file_languages[fp]
                        with self.parser_pool.get_parser(lang) as parser:
                            tree = parser.parse(file_contents[fp])
                            parsed_asts[fp] = (tree.root_node, lang)
                    except Exception:
                        pass
            processed = self._process_definitions_threaded(
                parseable_files, parsed_asts, progress, task_id, total_files
            )
        finally:
            # Clean up module-level globals
            _mp_p1_file_list = None
            _mp_p1_call_processor = None
            _mp_p1_queries = None
            _mp_p1_structural_elements = None
            _mp_p1_repo_path = None
            _mp_p1_project_name = None
            _mp_p1_language_objects = None

        return processed

    def _process_definitions_threaded(
        self,
        parseable_files: list[Path],
        parsed_asts: dict,
        progress: Progress,
        task_id: int,
        total_files: int,
    ) -> int:
        """Process definitions using ThreadPoolExecutor (original approach, GIL-limited).

        This is the fallback when multiprocessing is unavailable.
        """
        logger.info(
            f"Phase 1c: Processing {len(parsed_asts)} definitions with THREADS "
            f"({self.parallel_workers} workers)..."
        )
        flush_threshold = max(500, total_files // 2)
        max_file_timeout = self._MAX_FILE_TIMEOUT_SECS

        processed_count = [0]
        count_lock = Lock()

        def process_single_file(filepath: Path) -> tuple[Path, bool, str | None]:
            """Process definitions for a single file."""
            if filepath not in parsed_asts:
                return (filepath, False, "Not in parsed_asts")

            root_node, language = parsed_asts[filepath]
            file_start_time = time.time()

            try:
                result = self.factory.definition_processor.process_file_with_ast(
                    filepath,
                    root_node,
                    language,
                    self.queries,
                    self.factory.structure_processor.structural_elements,
                )
                def_time = time.time() - file_start_time

                if def_time > max_file_timeout:
                    return (filepath, False, f"Timeout after {def_time:.2f}s")

                if result:
                    self.ast_cache[filepath] = (root_node, language)
                    file_pending_calls = (
                        self.factory.call_processor.collect_pending_calls_in_file(
                            filepath, root_node, language, self.queries
                        )
                    )
                    if file_pending_calls:
                        with self._pending_calls_lock:
                            self.pending_calls.extend(file_pending_calls)
                    if self.incremental_builder:
                        self.incremental_builder.update_file_state(
                            filepath, language=language
                        )

                self.factory.structure_processor.process_generic_file(
                    filepath, filepath.name
                )
                return (filepath, True, None)

            except Exception as e:
                return (filepath, False, str(e))

        with self.cpu_limiter.create_thread_pool() as executor:
            futures = {
                executor.submit(process_single_file, fp): fp for fp in parseable_files
            }
            pending_futures = set(futures.keys())

            while pending_futures:
                done, pending_futures = wait(
                    pending_futures, timeout=0.3, return_when=FIRST_COMPLETED
                )

                if self.is_cancelled():
                    executor.shutdown(wait=False, cancel_futures=True)
                    self._check_cancelled("Pass 1c - threaded definitions")

                for future in done:
                    filepath, success, error = future.result()
                    with count_lock:
                        processed_count[0] += 1
                        current = processed_count[0]
                    progress.update(task_id, completed=current)

                    if (
                        current % self._PROGRESS_REPORT_INTERVAL == 0
                        or current == total_files
                    ):
                        file_progress = 10 + int((current / total_files) * 25)
                        self._report_progress(
                            file_progress,
                            f"Processing definitions... ({current}/{total_files})",
                        )
                    if current % flush_threshold == 0:
                        self.ingestor.flush_nodes()

        return processed_count[0]

    def _process_files_sequential(
        self,
        parseable_files: list[Path],
        progress: Progress,
        task_id: int,
        processed: int,
        flush_threshold: int,
        yield_threshold: int,
        read_file_content,
    ) -> int:
        """Process parseable files with sequential parsing (fallback when no ParserPool).

        This is the original behavior: parallel I/O, sequential parsing.

        Args:
            parseable_files: List of files to process
            progress: Progress bar instance
            task_id: Progress bar task ID
            processed: Current count of processed files
            flush_threshold: Flush database every N files
            yield_threshold: Yield CPU every N files
            read_file_content: Function to read file content

        Returns:
            Updated processed count
        """
        # Read file contents in parallel
        file_contents: dict[Path, bytes] = {}
        with self.cpu_limiter.create_thread_pool() as executor:
            futures = {
                executor.submit(read_file_content, fp): fp for fp in parseable_files
            }
            pending = set(futures.keys())

            while pending:
                # Use short timeout to check cancellation frequently
                done, pending = wait(pending, timeout=0.3, return_when=FIRST_COMPLETED)

                # Check for cancellation first
                if self.is_cancelled():
                    executor.shutdown(wait=False, cancel_futures=True)
                    self._check_cancelled("Pass 2 - Phase 1 I/O")

                for future in done:
                    filepath, content, error = future.result()
                    if content is not None:
                        file_contents[filepath] = content
                    elif error:
                        logger.debug(f"Failed to read {filepath}: {error}")

        # Parse files sequentially (original behavior)
        cancel_check_interval = 10
        progress_update_interval = self._SEQUENTIAL_PROGRESS_INTERVAL
        total_files = len(parseable_files)

        for filepath in parseable_files:
            content = file_contents.get(filepath)
            if content is not None:
                lang_config = get_language_config(filepath.suffix)
                if lang_config:
                    # Process file with pre-read content
                    result = (
                        self.factory.definition_processor.process_file_with_content(
                            filepath,
                            content,
                            lang_config.name,
                            self.queries,
                            self.factory.structure_processor.structural_elements,
                        )
                    )
                    if result:
                        root_node, language = result
                        self.ast_cache[filepath] = (root_node, language)

                        # Collect pending calls for deferred resolution in Pass 2
                        file_pending_calls = (
                            self.factory.call_processor.collect_pending_calls_in_file(
                                filepath, root_node, language, self.queries
                            )
                        )
                        if file_pending_calls:
                            with self._pending_calls_lock:
                                self.pending_calls.extend(file_pending_calls)

                        # Update incremental state
                        if self.incremental_builder:
                            self.incremental_builder.update_file_state(
                                filepath, language=lang_config.name
                            )

                    # Create CONTAINS_FILE relationship
                    self.factory.structure_processor.process_generic_file(
                        filepath, filepath.name
                    )

            processed += 1
            progress.update(task_id, completed=processed)

            # Update task progress with current file info
            if processed % progress_update_interval == 0 or processed == total_files:
                # Calculate progress percentage (10-35 range for Pass 1)
                file_progress = 10 + int(
                    (processed / total_files) * 25
                )  # Maps to 10-35%
                rel_path = filepath.relative_to(self.repo_path)
                self._report_progress(
                    file_progress,
                    f"Parsing files... ({processed}/{total_files}) {rel_path}",
                )

            # Check cancellation frequently (both counter-based and time-based)
            if processed % cancel_check_interval == 0 or self._should_check_cancelled():
                self._check_cancelled(f"Pass 2 - Phase 1 parsing ({processed} files)")

            # Periodic incremental flush for large codebases
            # Only flush nodes here; relationships accumulate and get flushed in Pass 4
            if processed % flush_threshold == 0:
                logger.info(f"Intermediate flush at file {processed}: flushing nodes")
                self.ingestor.flush_nodes()

            # Yield CPU periodically
            if processed % yield_threshold == 0:
                self.cpu_limiter.yield_cpu()

        return processed

    def _process_function_calls_with_progress(self, progress: Progress) -> None:
        """Third pass with progress bar: Process function calls using the cached ASTs.

        Uses parallel processing when ParserPool is available (indicates parallel mode enabled).
        """
        # Create a copy of items to prevent "OrderedDict mutated during iteration" errors
        ast_cache_items = list(self.ast_cache.items())

        # Create our own progress task
        task_id = progress.add_task("  Resolving calls", total=len(ast_cache_items))

        # Use parallel processing if we have parallel parsing enabled
        if self.parser_pool and len(ast_cache_items) > 10:
            self._process_function_calls_parallel(ast_cache_items, progress, task_id)
        else:
            self._process_function_calls_sequential(ast_cache_items, progress, task_id)

    def _resolve_pending_calls_with_progress(self, progress: Progress) -> None:
        """Resolve pending calls using the deferred resolution approach.

        This method uses PendingCall objects collected during Pass 1 instead of
        re-traversing cached ASTs. Benefits:
        - ~99% memory reduction (no AST caching needed for call resolution)
        - Simpler code path (just iterate through PendingCall list)
        - Same resolution accuracy (uses complete function_registry)
        - Parallel processing for improved performance
        """
        if not self.pending_calls:
            logger.info("No pending calls to resolve")
            self._report_progress(45, "Resolving calls... (no pending calls)")
            return

        total_calls = len(self.pending_calls)
        task_id = progress.add_task("  Resolving calls (pending)", total=total_calls)

        logger.info(f"Resolving {total_calls} pending calls...")
        self._report_progress(42, f"Resolving {total_calls} function calls...")

        # Use sequential processing: call resolution is pure CPU (~1-3s for
        # ~45K calls — just dict lookups).  The previous parallel=True path
        # forked 64 multiprocessing workers, whose fork+pickle+merge overhead
        # (~80s) vastly exceeded the actual work.  Sequential is optimal here.
        resolved_count = self.factory.call_processor.resolve_pending_calls(
            self.pending_calls,
            parallel=False,
        )

        # Store resolved count for later verification
        self._last_resolved_call_count = resolved_count

        progress.update(task_id, completed=total_calls)

        logger.info(
            f"Resolved {resolved_count}/{total_calls} calls using pending call approach"
        )
        self._report_progress(48, f"Resolved {resolved_count}/{total_calls} calls")

        # Memory cleanup: free pending_calls list and clear resolution caches
        # These are no longer needed after call resolution completes
        pending_count = len(self.pending_calls)
        self.pending_calls.clear()
        logger.info(f"Freed pending_calls list ({pending_count} entries)")

        # Clear inheritance resolution cache in CallProcessor
        self.factory.call_processor.clear_resolution_caches()

    def _process_function_calls_sequential(
        self,
        ast_cache_items: list,
        progress: Progress,
        task_id: int,
    ) -> None:
        """Process function calls sequentially (original behavior)."""
        cancel_check_interval = 10
        processed = 0
        for file_path, (root_node, language) in ast_cache_items:
            self.factory.call_processor.process_calls_in_file(
                file_path, root_node, language, self.queries
            )
            # Update progress
            progress.update(task_id, advance=1)
            processed += 1

            # Check cancellation frequently (both counter-based and time-based)
            if processed % cancel_check_interval == 0 or self._should_check_cancelled():
                self._check_cancelled(f"Pass 3 - function calls ({processed} files)")

    def _process_function_calls_parallel(
        self,
        ast_cache_items: list,
        progress: Progress,
        task_id: int,
    ) -> None:
        """Process function calls in parallel across files.

        This is safe because:
        1. function_registry is read-only at this point (all definitions collected in Pass 2)
        2. import_mapping is read-only
        3. CallProcessor's write operations are now thread-safe (locked)
        4. ingestor.ensure_relationship_batch is already thread-safe
        """
        import threading
        from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

        logger.info(
            f"Processing {len(ast_cache_items)} files for call relationships in parallel..."
        )

        # Thread-safe progress counter
        processed_count = [0]
        count_lock = threading.Lock()

        def process_file_calls(item):
            """Process calls in a single file."""
            file_path, (root_node, language) = item
            try:
                self.factory.call_processor.process_calls_in_file(
                    file_path, root_node, language, self.queries
                )
                return (file_path, None)
            except Exception as e:
                return (file_path, str(e))

        with ThreadPoolExecutor(max_workers=self.parallel_workers) as executor:
            futures = {
                executor.submit(process_file_calls, item): item
                for item in ast_cache_items
            }
            pending = set(futures.keys())

            while pending:
                # Use short timeout to check cancellation frequently
                done, pending = wait(pending, timeout=0.3, return_when=FIRST_COMPLETED)

                # Check for cancellation first
                if self.is_cancelled():
                    executor.shutdown(wait=False, cancel_futures=True)
                    self._check_cancelled(
                        f"Pass 3 - parallel calls ({processed_count[0]} files)"
                    )

                for future in done:
                    # Thread-safe counter update
                    with count_lock:
                        processed_count[0] += 1

                    # Update progress
                    progress.update(task_id, advance=1)

                    # Handle any errors
                    file_path, error = future.result()
                    if error:
                        logger.debug(f"Error processing calls in {file_path}: {error}")

    def _generate_semantic_embeddings(self) -> None:
        """Generate and store semantic embeddings for functions and methods.

        Uses API-based embeddings (text-embedding-3-small) and stores directly
        to Memgraph for native vector search.
        """
        try:
            from .embedder import embed_code, get_embedding_dimension

            logger.info("--- Generating semantic embeddings ---")

            # Setup vector indexes first
            dimension = get_embedding_dimension()
            self.ingestor.setup_vector_index(dimension=dimension)

            # Query database for all Function and Method nodes with their source info
            # Functions: File -[:DEFINES]-> Function
            # Methods: File -[:DEFINES]-> Class -[:DEFINES_METHOD]-> Method
            # Use parameterized query to avoid injection
            project_prefix = f"{self.project_name}."
            query = """
            MATCH (f:File)-[:DEFINES]->(n:Function)
            WHERE f.qualified_name STARTS WITH $project_prefix
            RETURN 'Function' AS node_type, n.qualified_name AS qualified_name,
                   n.start_line AS start_line, n.end_line AS end_line,
                   f.path AS path
            UNION ALL
            MATCH (f:File)-[:DEFINES]->(c:Class)-[:DEFINES_METHOD]->(n:Method)
            WHERE f.qualified_name STARTS WITH $project_prefix
            RETURN 'Method' AS node_type, n.qualified_name AS qualified_name,
                   n.start_line AS start_line, n.end_line AS end_line,
                   f.path AS path
            """

            results = self.ingestor._execute_query(
                query, {"project_prefix": project_prefix}
            )

            if not results:
                logger.info("No functions or methods found for embedding generation")
                return

            logger.info(f"Generating embeddings for {len(results)} functions/methods")

            # Group by node type for batch updates
            embeddings_by_type: dict[str, list[dict]] = {"Function": [], "Method": []}

            for result in results:
                node_type = result.get("node_type", "Function")
                qualified_name = result["qualified_name"]
                start_line = result.get("start_line")
                end_line = result.get("end_line")
                file_path = result.get("path")

                # Extract source code
                source_code = self._extract_source_code(
                    qualified_name, file_path, start_line, end_line
                )

                if source_code:
                    try:
                        embedding = embed_code(source_code)
                        embeddings_by_type.setdefault(node_type, []).append(
                            {"qualified_name": qualified_name, "embedding": embedding}
                        )
                    except Exception as e:
                        logger.warning(f"Failed to embed {qualified_name}: {e}")
                else:
                    logger.debug(f"No source code found for {qualified_name}")

            # Store embeddings to Memgraph by node type
            total_stored = 0
            for node_type, embeddings_data in embeddings_by_type.items():
                if embeddings_data:
                    stored = self._store_embeddings_to_memgraph(
                        embeddings_data, node_type
                    )
                    total_stored += stored

            logger.info(
                f"Successfully stored {total_stored} semantic embeddings to Memgraph"
            )

        except Exception as e:
            logger.warning(f"Failed to generate semantic embeddings: {e}")

    def _extract_source_code(
        self, qualified_name: str, file_path: str, start_line: int, end_line: int
    ) -> str | None:
        """Extract source code for a function/method from cached AST or file."""
        if not file_path or not start_line or not end_line:
            return None

        file_path_obj = (self.repo_path / file_path).resolve()

        # Create AST extractor function if AST is available
        ast_extractor = None
        if file_path_obj in self.ast_cache:
            root_node, language = self.ast_cache[file_path_obj]
            fqn_config = LANGUAGE_FQN_CONFIGS.get(language)

            if fqn_config:

                def ast_extractor_func(qname: str, path: Path) -> str | None:
                    return find_function_source_by_fqn(
                        root_node,
                        qname,
                        path,
                        self.repo_path,
                        self.project_name,
                        fqn_config,
                    )

                ast_extractor = ast_extractor_func

        # Use shared utility with AST-based extraction and line-based fallback
        return extract_source_with_fallback(
            file_path_obj, start_line, end_line, qualified_name, ast_extractor
        )

    def _generate_semantic_embeddings_batched(self, progress: Progress) -> None:
        """Generate and store semantic embeddings with batch processing.

        This optimized version:
        1. Extracts all source code in parallel using ThreadPoolExecutor
        2. Generates embeddings using parallel API calls for efficiency
        3. Stores embeddings in batches to reduce memory and DB transaction size
        4. Uses pipeline processing (generate + store in chunks)
        """
        try:
            from .embedder import (
                embed_code_batch_for_repo,
                get_device_info,
                get_embedding_dimension,
            )

            # Log device information
            device_info = get_device_info()
            logger.info("--- Generating semantic embeddings ---")
            logger.info(f"Provider: {device_info.get('device_name', 'unknown')}")
            logger.info(f"Dimension: {device_info.get('embedding_dimension', 1536)}")

            # Setup vector indexes first
            dimension = get_embedding_dimension()
            self.ingestor.setup_vector_index(dimension=dimension)

            # Build query based on embedding granularity
            # "class" mode: Only Class + Function (faster, ~44% less embeddings)
            # "method" mode: Class + Function + Method (more precise for method-level search)
            # Use parameterized query to avoid injection and improve performance
            project_prefix = f"{self.project_name}."

            if self.embedding_granularity == "class":
                # Only Function and Class embeddings (skip Method)
                query = """
                MATCH (f:File)-[:DEFINES]->(n:Function)
                WHERE f.qualified_name STARTS WITH $project_prefix
                RETURN 'Function' AS node_type, n.qualified_name AS qualified_name,
                       n.start_line AS start_line, n.end_line AS end_line,
                       f.path AS path
                UNION ALL
                MATCH (f:File)-[:DEFINES]->(n:Class)
                WHERE f.qualified_name STARTS WITH $project_prefix
                RETURN 'Class' AS node_type, n.qualified_name AS qualified_name,
                       n.start_line AS start_line, n.end_line AS end_line,
                       f.path AS path
                """
                logger.info(
                    "Embedding granularity: 'class' (Class + Function only, skipping Methods)"
                )
            else:
                # Full granularity: Function + Method
                query = """
                MATCH (f:File)-[:DEFINES]->(n:Function)
                WHERE f.qualified_name STARTS WITH $project_prefix
                RETURN 'Function' AS node_type, n.qualified_name AS qualified_name,
                       n.start_line AS start_line, n.end_line AS end_line,
                       f.path AS path
                UNION ALL
                MATCH (f:File)-[:DEFINES]->(c:Class)-[:DEFINES_METHOD]->(n:Method)
                WHERE f.qualified_name STARTS WITH $project_prefix
                RETURN 'Method' AS node_type, n.qualified_name AS qualified_name,
                       n.start_line AS start_line, n.end_line AS end_line,
                       f.path AS path
                """
                logger.info("Embedding granularity: 'method' (Function + Method)")

            results = self.ingestor._execute_query(
                query, {"project_prefix": project_prefix}
            )

            if not results:
                logger.info("No functions or methods found for embedding generation")
                return

            total = len(results)
            logger.info(f"Generating embeddings for {total} functions/methods")

            # Create our own progress task
            task_id = progress.add_task("  Generating embeddings", total=total)

            # Phase 1: Extract all source code in parallel
            source_data: list[tuple[dict, str | None]] = []

            def extract_source(result: dict) -> tuple[dict, str | None]:
                qualified_name = result["qualified_name"]
                start_line = result.get("start_line")
                end_line = result.get("end_line")
                file_path = result.get("path")
                source = self._extract_source_code(
                    qualified_name, file_path, start_line, end_line
                )
                return result, source

            self._check_cancelled("Pass 2 - Embedding extraction")
            with ThreadPoolExecutor(max_workers=self.parallel_workers) as executor:
                futures = [executor.submit(extract_source, r) for r in results]
                for future in as_completed(futures):
                    if self.is_cancelled():
                        executor.shutdown(wait=False, cancel_futures=True)
                        self._check_cancelled("Pass 2 - Embedding extraction cancelled")
                    try:
                        result, source = future.result()
                        source_data.append((result, source))
                    except Exception as e:
                        logger.debug(f"Failed to extract source: {e}")

            # Filter out entries without source code
            valid_entries = [(r, s) for r, s in source_data if s]
            logger.info(f"Extracted source for {len(valid_entries)}/{total} functions")

            # Update task total to reflect only valid entries
            progress.update(task_id, total=len(valid_entries) if valid_entries else 1)

            if not valid_entries:
                logger.warning(
                    "No source code extracted, skipping embedding generation"
                )
                progress.update(task_id, completed=1)
                return

            # Phase 2: Pipeline processing - generate embeddings in chunks and store immediately
            # This reduces memory usage and provides better progress feedback
            self._check_cancelled("Pass 2 - Embedding generation")

            # Use smaller chunks for pipeline processing
            # Each chunk: generate embeddings -> store to DB -> update progress
            PIPELINE_CHUNK_SIZE = 512  # Process 512 items at a time (reduced for better cancellation response)
            DB_BATCH_SIZE = 500  # Store 500 embeddings per DB transaction

            total_stored = 0
            processed_count = 0
            total_to_process = len(valid_entries)

            for chunk_start in range(0, total_to_process, PIPELINE_CHUNK_SIZE):
                self._check_cancelled(
                    f"Pass 2 - Embedding chunk {chunk_start}/{total_to_process}"
                )

                chunk_entries = valid_entries[
                    chunk_start : chunk_start + PIPELINE_CHUNK_SIZE
                ]
                chunk_codes = [source for _, source in chunk_entries]

                # Update task progress at start of each chunk
                chunk_progress = 55 + int(
                    (processed_count / total_to_process) * 15
                )  # Maps to 55-70%
                self._report_progress(
                    chunk_progress,
                    f"Generating embeddings... ({processed_count}/{total_to_process})",
                )

                try:
                    # Generate embeddings for this chunk (uses parallel API calls internally)
                    # Use configured max concurrent for better performance
                    from core.config import settings

                    chunk_embeddings = embed_code_batch_for_repo(
                        chunk_codes,
                        repo_name=self.project_name,
                        batch_size=EMBEDDING_BATCH_SIZE,
                        parallel=True,
                        max_concurrent=settings.EMBEDDING_MAX_CONCURRENT,
                    )

                    # Group embeddings by node type and store in small batches
                    embeddings_by_type: dict[str, list[dict]] = {
                        "Function": [],
                        "Method": [],
                        "Class": [],
                    }

                    for (result, _), embedding in zip(chunk_entries, chunk_embeddings):
                        node_type = result.get("node_type", "Function")
                        embeddings_by_type.setdefault(node_type, []).append(
                            {
                                "qualified_name": result["qualified_name"],
                                "embedding": embedding,
                            }
                        )
                        processed_count += 1

                    # Store in small batches to reduce transaction size
                    for node_type, embeddings_data in embeddings_by_type.items():
                        for i in range(0, len(embeddings_data), DB_BATCH_SIZE):
                            batch = embeddings_data[i : i + DB_BATCH_SIZE]
                            stored = self._store_embeddings_to_memgraph(
                                batch, node_type
                            )
                            total_stored += stored

                    # Update progress after each pipeline chunk
                    progress.update(task_id, completed=processed_count)

                except Exception as e:
                    logger.warning(f"Failed to process embedding chunk: {e}")
                    # Fall back to individual processing for this chunk
                    from .embedder import embed_code

                    for result, source in chunk_entries:
                        try:
                            embedding = embed_code(source)
                            node_type = result.get("node_type", "Function")
                            stored = self._store_embeddings_to_memgraph(
                                [
                                    {
                                        "qualified_name": result["qualified_name"],
                                        "embedding": embedding,
                                    }
                                ],
                                node_type,
                            )
                            total_stored += stored
                            processed_count += 1
                            progress.update(task_id, completed=processed_count)
                        except Exception as e2:
                            logger.warning(
                                f"Failed to embed {result['qualified_name']}: {e2}"
                            )
                            processed_count += 1

            logger.info(
                f"Successfully stored {total_stored} semantic embeddings to Memgraph"
            )

        except Exception as e:
            logger.warning(f"Failed to generate semantic embeddings: {e}")

    def _store_embeddings_to_memgraph(
        self, embeddings_data: list[dict[str, Any]], node_type: str = "Function"
    ) -> int:
        """Store embeddings directly to Memgraph nodes.

        This method stores embeddings as a property on the nodes in Memgraph,
        enabling native vector search without requiring an external vector database.

        Args:
            embeddings_data: List of dicts with:
                - qualified_name: str - Node identifier
                - embedding: list[float] - Embedding vector
            node_type: Node label (Function, Method, Class)

        Returns:
            Number of embeddings successfully stored
        """
        if not embeddings_data:
            return 0

        try:
            # Use the ingestor's batch update method
            updated = self.ingestor.update_embeddings_batch(node_type, embeddings_data)
            return updated
        except Exception as e:
            logger.warning(f"Failed to store embeddings to Memgraph: {e}")
            return 0

    def _setup_vector_indexes(self) -> None:
        """Setup Memgraph vector indexes for semantic search.

        This creates vector indexes on Function, Method, and Class nodes
        to enable similarity search. Should be called after embeddings
        are stored.
        """
        try:
            from .embedder import get_embedding_dimension

            dimension = get_embedding_dimension()
            self.ingestor.setup_vector_index(dimension=dimension)
            logger.info(f"Vector indexes configured (dimension={dimension})")
        except Exception as e:
            logger.warning(f"Failed to setup vector indexes: {e}")

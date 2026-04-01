# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Incremental updater for the knowledge graph.

Simplified version that uses Memgraph as the single source of truth.
No local caches (FunctionRegistry, CallersIndex, DefinitionsStore).

For file additions and deletions, uses ProcessorFactory for full AST
processing (definitions + calls + imports). For modifications, uses
SimpleUpdater for definition-level diff.
"""

import asyncio
import os
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from core.language_config import get_language_config
from core.source_extraction import extract_source_with_fallback
from graph.service import MemgraphIngestor
from loguru import logger
from parser.factory import ProcessorFactory
from tree_sitter import Node, Parser

from .models import FileChange, UpdateResult
from .simple_updater import SimpleUpdater

# Progress callback type: (progress: int, step: str, message: str) -> None
ProgressCallback = Callable[[int, str, str], Any] | None

# File progress callback type: (file_path: str, file_index: int, total_files: int) -> None
FileProgressCallback = Callable[[str, int, int], Any] | None


class IncrementalUpdater:
    """Incremental Memgraph updates with file-level and definition-level granularity.

    This class provides efficient incremental updates to the knowledge graph.
    Memgraph is the single source of truth - no local caches are maintained.

    For file additions: Full parse via ProcessorFactory (definitions + calls).
    For file deletions: DETACH DELETE by qualified_name prefix.
    For file modifications: Definition-level diff via SimpleUpdater.

    Example:
        updater = IncrementalUpdater(
            ingestor=MemgraphIngestor(...),
            repo_path=Path("/path/to/repo"),
            project_name="myproject",
            parsers={"python": parser, ...},
            queries={"python": {...}, ...},
        )
        result = updater.apply_changes([FileChange(...)])
    """

    def __init__(
        self,
        ingestor: MemgraphIngestor,
        repo_path: Path,
        project_name: str,
        parsers: dict[str, Parser],
        queries: dict[str, Any],
        progress_callback: ProgressCallback = None,
        skip_embeddings: bool = False,
        embedding_granularity: str = "class",
        parallel_workers: int | None = None,
        state_dir: Path | None = None,
        async_embeddings: bool = False,
        track_variables: bool = True,
        # Legacy parameters (kept for backward compatibility, ignored)
        smart_update: bool = False,
    ):
        """Initialize the incremental updater.

        Args:
            ingestor: MemgraphIngestor instance for database operations.
            repo_path: Repository root path.
            project_name: Project name for qualified names.
            parsers: Tree-sitter parser dictionary.
            queries: Language query configuration dictionary.
            progress_callback: Optional callback for progress updates.
            skip_embeddings: If True, skip embedding generation.
            embedding_granularity: "class" or "method".
            parallel_workers: Workers for embedding generation.
            state_dir: Directory for persistent state (overrides auto-detection).
            async_embeddings: If True, generate embeddings in background.
            track_variables: If True, track module/class-level variables.
            smart_update: Legacy parameter, ignored (always uses simplified approach).
        """
        from .cache_registry import get_cache_registry

        self.ingestor = ingestor
        self.repo_path = Path(repo_path)
        self.project_name = project_name
        self.parsers = parsers
        self.queries = self._add_parser_references_to_queries(parsers, queries)
        self.progress_callback = progress_callback

        # File-level progress callback
        self.file_progress_callback: FileProgressCallback = None

        # State directory with fallback support:
        # 1. Explicit state_dir parameter (highest priority)
        # 2. CacheRegistry with fallback (uses project_name)
        if state_dir:
            self.state_dir = state_dir
        else:
            registry = get_cache_registry()
            self.state_dir = registry.get_cache_dir(project_name, self.repo_path)

        # Embedding settings
        self.skip_embeddings = skip_embeddings
        self.embedding_granularity = embedding_granularity
        self.parallel_workers = parallel_workers or (os.cpu_count() or 4)
        self.async_embeddings = async_embeddings

        # Async embedding queue
        self._embedding_queue = None
        if async_embeddings and not skip_embeddings:
            from .embedding_queue import EmbeddingQueue

            self._embedding_queue = EmbeddingQueue(
                ingestor=ingestor,
                repo_path=self.repo_path,
                project_name=project_name,
                parallel_workers=self.parallel_workers,
            )
            self._embedding_queue.start()

        # Track nodes that need embeddings
        self._nodes_pending_embeddings: list[dict[str, Any]] = []

        # SimpleUpdater for definition-level diffs (modifications)
        self._simple_updater = SimpleUpdater(
            ingestor=ingestor,
            repo_path=repo_path,
            project_name=project_name,
            parsers=parsers,
            queries=queries,
            track_variables=track_variables,
        )

        # Lightweight in-memory state for ProcessorFactory
        # These are populated on-demand from Memgraph, not persisted locally
        self._function_registry = None
        self._simple_name_lookup: dict[str, set[str]] = defaultdict(set)
        self._ast_cache: dict[Path, tuple[Node, str]] = {}
        self._module_qn_to_file_path: dict[str, Path] = {}

        # Processor factory (lazy init)
        self._factory: ProcessorFactory | None = None

    def _get_factory(self) -> ProcessorFactory:
        """Get or create the ProcessorFactory, loading registry from Memgraph."""
        if self._factory is None:
            # Load function registry from Memgraph (no local cache)
            self._load_function_registry_from_db()

            self._factory = ProcessorFactory(
                ingestor=self.ingestor,
                repo_path_getter=lambda: self.repo_path,
                project_name_getter=lambda: self.project_name,
                queries=self.queries,
                function_registry=self._function_registry,
                simple_name_lookup=self._simple_name_lookup,
                ast_cache=self._ast_cache,
            )
        return self._factory

    def _load_function_registry_from_db(self) -> None:
        """Load function registry directly from Memgraph.

        No local cache - always queries the database.
        """
        from graph.updater import FunctionRegistryTrie

        logger.info("Loading function registry from Memgraph...")
        start_time = time.time()

        self._function_registry = FunctionRegistryTrie()

        results = self.ingestor.fetch_all("""
            MATCH (n)
            WHERE n:Function OR n:Method OR n:Class
            RETURN n.qualified_name AS qualified_name, labels(n) AS labels
        """)

        self._function_registry.disable_cache()
        try:
            for result in results:
                qn = result.get("qualified_name")
                labels = result.get("labels", [])
                if not qn:
                    continue

                node_type = "Function"
                if "Method" in labels:
                    node_type = "Method"
                elif "Class" in labels:
                    node_type = "Class"

                self._function_registry[qn] = node_type
                simple_name = qn.split(".")[-1]
                self._simple_name_lookup[simple_name].add(qn)
        finally:
            self._function_registry.enable_cache()

        duration_ms = (time.time() - start_time) * 1000
        logger.info(
            f"Loaded {len(self._function_registry)} entries from Memgraph "
            f"in {duration_ms:.1f}ms"
        )

    def _add_parser_references_to_queries(
        self, parsers: dict, queries: dict
    ) -> dict[str, Any]:
        """Add parser references to query objects for processors."""
        updated_queries: dict[str, Any] = {}
        for lang, query_data in queries.items():
            if lang in parsers:
                updated_queries[lang] = {**query_data, "parser": parsers[lang]}
            else:
                updated_queries[lang] = query_data
        return updated_queries

    def _update_progress(self, progress: int, step: str, message: str) -> None:
        """Update progress via callback if provided."""
        if self.progress_callback:
            try:
                self._latest_progress = (progress, step, message)
                if not asyncio.iscoroutinefunction(self.progress_callback):
                    self.progress_callback(progress, step, message)
            except Exception as e:
                logger.warning(f"Progress callback failed: {e}")

    def _notify_file_progress(self, file_path: str, index: int, total: int) -> None:
        """Notify file-level progress for current file being processed."""
        if self.file_progress_callback:
            try:
                self.file_progress_callback(file_path, index, total)
            except Exception as e:
                logger.debug(f"File progress callback failed: {e}")

    # =========================================================================
    # File Operations
    # =========================================================================

    def _get_module_qn_prefix(self, file_path: Path) -> str:
        """Get the qualified name prefix for a file."""
        try:
            relative_path = file_path.relative_to(self.repo_path)
        except ValueError:
            return ""

        if file_path.name == "__init__.py":
            parts = list(relative_path.parent.parts)
        else:
            parts = list(relative_path.with_suffix("").parts)

        return ".".join([self.project_name] + parts)

    def _update_registry_for_file(self, module_qn: str) -> None:
        """Update function registry with definitions from a specific file.

        Queries Memgraph for all definitions with the given module prefix
        and adds them to the in-memory registry.

        Args:
            module_qn: Module qualified name prefix (e.g., "project.module")
        """
        if self._function_registry is None:
            return

        try:
            new_defs = self.ingestor.fetch_all(
                """
                MATCH (n) WHERE n.qualified_name STARTS WITH $prefix
                AND (n:Function OR n:Method OR n:Class)
                RETURN n.qualified_name AS qn, labels(n) AS labels
            """,
                {"prefix": module_qn + "."},
            )

            for d in new_defs:
                qn = d.get("qn")
                if not qn:
                    continue
                labels = d.get("labels", [])
                node_type = (
                    "Method"
                    if "Method" in labels
                    else ("Class" if "Class" in labels else "Function")
                )
                self._function_registry[qn] = node_type
                simple_name = qn.split(".")[-1]
                self._simple_name_lookup[simple_name].add(qn)
        except Exception as e:
            logger.debug(f"Failed to update registry for {module_qn}: {e}")

    def _remove_registry_entries_for_file(self, module_qn: str) -> None:
        """Remove registry entries for a specific file.

        Args:
            module_qn: Module qualified name prefix (e.g., "project.module")
        """
        if self._function_registry is None:
            return

        registry_keys = list(self._function_registry.keys())
        for qn in registry_keys:
            if qn.startswith(module_qn + ".") or qn == module_qn:
                del self._function_registry[qn]
                simple_name = qn.split(".")[-1]
                self._simple_name_lookup[simple_name].discard(qn)

    def _remove_file(self, file_path: Path) -> None:
        """Delete all nodes for a file from the graph.

        Uses DETACH DELETE via ingestor to remove all associated nodes.
        """
        logger.debug(f"Removing file nodes: {file_path}")

        module_qn_prefix = self._get_module_qn_prefix(file_path)
        if not module_qn_prefix:
            return

        try:
            relative_path = file_path.relative_to(self.repo_path)
        except ValueError:
            return

        # Clean up in-memory state if factory was initialized
        if self._function_registry is not None:
            registry_keys = list(self._function_registry.keys())
            for qn in registry_keys:
                if qn.startswith(module_qn_prefix + ".") or qn == module_qn_prefix:
                    del self._function_registry[qn]
                    # Also clean simple_name_lookup
                    simple_name = qn.split(".")[-1]
                    self._simple_name_lookup[simple_name].discard(qn)

        # Remove from AST cache
        self._ast_cache.pop(file_path, None)
        self._module_qn_to_file_path.pop(module_qn_prefix, None)

        # Delete from database
        self.ingestor.delete_file_nodes(
            project_name=self.project_name,
            file_path=str(relative_path),
            module_qn_prefix=module_qn_prefix,
        )

    def _ensure_folder_chain(self, file_path: Path) -> None:
        """Ensure all parent folders exist in the graph."""
        try:
            relative_path = file_path.relative_to(self.repo_path)
        except ValueError:
            return

        parent_dir = relative_path.parent
        if parent_dir == Path("."):
            return

        folders_to_create: list[Path] = []
        current = parent_dir
        while current != Path("."):
            folders_to_create.insert(0, current)
            current = current.parent

        factory = self._get_factory()

        for folder_path in folders_to_create:
            # Normalize: skip first part if it matches project_name
            folder_parts = list(folder_path.parts)
            if folder_parts and folder_parts[0] == self.project_name:
                folder_parts = folder_parts[1:]
            folder_qn = ".".join([self.project_name] + folder_parts)
            full_folder_path = self.repo_path / folder_path
            is_package = (full_folder_path / "__init__.py").exists()

            self.ingestor.ensure_node_batch(
                "Folder",
                {
                    "path": str(folder_path),
                    "name": folder_path.name,
                    "is_package": is_package,
                    "qualified_name": folder_qn,
                },
            )

            parent_folder = folder_path.parent
            if parent_folder == Path("."):
                self.ingestor.ensure_relationship_batch(
                    ("Project", "name", self.project_name),
                    "CONTAINS_FOLDER",
                    ("Folder", "qualified_name", folder_qn),
                )
            else:
                parent_parts = list(parent_folder.parts)
                if parent_parts and parent_parts[0] == self.project_name:
                    parent_parts = parent_parts[1:]
                parent_qn = ".".join([self.project_name] + parent_parts)
                self.ingestor.ensure_relationship_batch(
                    ("Folder", "qualified_name", parent_qn),
                    "CONTAINS_FOLDER",
                    ("Folder", "qualified_name", folder_qn),
                )

            factory.structure_processor.structural_elements[folder_path] = folder_qn

    def _ensure_file_node(self, file_path: Path) -> str | None:
        """Ensure File node exists with CONTAINS_FILE relationship."""
        try:
            relative_path = file_path.relative_to(self.repo_path)
        except ValueError:
            return None

        parent_dir = relative_path.parent
        # Normalize: skip first part if it matches project_name
        file_parts = list(parent_dir.parts) + [file_path.stem]
        if file_parts and file_parts[0] == self.project_name:
            file_parts = file_parts[1:]
        file_qualified_name = ".".join([self.project_name] + file_parts)

        self.ingestor.ensure_node_batch(
            "File",
            {
                "path": str(relative_path),
                "name": file_path.name,
                "extension": file_path.suffix,
                "qualified_name": file_qualified_name,
            },
        )

        if parent_dir == Path("."):
            self.ingestor.ensure_relationship_batch(
                ("Project", "name", self.project_name),
                "CONTAINS_FILE",
                ("File", "qualified_name", file_qualified_name),
            )
        else:
            parent_parts = list(parent_dir.parts)
            if parent_parts and parent_parts[0] == self.project_name:
                parent_parts = parent_parts[1:]
            parent_qn = ".".join([self.project_name] + parent_parts)
            self.ingestor.ensure_relationship_batch(
                ("Folder", "qualified_name", parent_qn),
                "CONTAINS_FILE",
                ("File", "qualified_name", file_qualified_name),
            )

        return file_qualified_name

    def _add_file(self, file_path: Path, flush: bool = True) -> str | None:
        """Add file: parse and create nodes + outgoing edges.

        Uses ProcessorFactory for full AST processing.
        """
        logger.debug(f"Adding file: {file_path}")

        self._ensure_folder_chain(file_path)
        self._ensure_file_node(file_path)

        if flush:
            self.ingestor.flush_all()

        lang_config = get_language_config(file_path.suffix)
        if not lang_config or lang_config.name not in self.parsers:
            return None

        language = lang_config.name
        factory = self._get_factory()

        # Parse file and create definitions
        try:
            result = factory.definition_processor.process_file(
                file_path,
                language,
                self.queries,
                factory.structure_processor.structural_elements,
            )
        except Exception as e:
            logger.warning(f"Failed to parse file {file_path}: {e}")
            return None

        if result is None:
            return None

        root_node, _ = result

        # Collect and resolve pending calls
        try:
            file_pending_calls = factory.call_processor.collect_pending_calls_in_file(
                file_path, root_node, language, self.queries
            )
            if file_pending_calls:
                resolved = factory.call_processor.resolve_pending_calls(
                    file_pending_calls
                )
                logger.debug(
                    f"Resolved {resolved}/{len(file_pending_calls)} calls for {file_path.name}"
                )
        except Exception as e:
            logger.warning(f"Failed to resolve calls for {file_path}: {e}")

        module_qn = self._get_module_qn_prefix(file_path)
        if module_qn:
            self._module_qn_to_file_path[module_qn] = file_path

        if flush:
            self.ingestor.flush_all()

        # Update function registry with new definitions from this file
        if self._function_registry is not None and module_qn:
            self._update_registry_for_file(module_qn)

        if not self.skip_embeddings:
            self._collect_nodes_for_embeddings(file_path, module_qn)

        return module_qn

    def _add_file_deferred(self, file_path: Path) -> tuple[str | None, list]:
        """Add file in deferred mode: parse and accumulate nodes/rels in memory.

        Unlike _add_file(), this method:
        - Never calls flush_all() (all writes stay in buffer)
        - Collects pending calls instead of resolving them immediately
        - Relies on definition_processor.process_file() to update the
          shared function_registry automatically (no DB query needed)
        - Skips embedding collection (deferred to post-flush phase)

        Returns:
            (module_qn, pending_calls) for batched processing in later phases.
        """
        logger.debug(f"Adding file (deferred): {file_path}")

        self._ensure_folder_chain(file_path)
        self._ensure_file_node(file_path)
        # NO flush_all() — writes stay in buffer

        lang_config = get_language_config(file_path.suffix)
        if not lang_config or lang_config.name not in self.parsers:
            module_qn = self._get_module_qn_prefix(file_path)
            return module_qn, []

        language = lang_config.name
        factory = self._get_factory()

        # Parse file and create definitions (buffered via ensure_node_batch)
        # definition_processor.process_file() automatically updates
        # the shared function_registry and simple_name_lookup.
        try:
            result = factory.definition_processor.process_file(
                file_path,
                language,
                self.queries,
                factory.structure_processor.structural_elements,
            )
        except Exception as e:
            logger.warning(f"Failed to parse file {file_path}: {e}")
            return None, []

        if result is None:
            return None, []

        root_node, _ = result

        # Collect pending calls (DON'T resolve — deferred to Phase 3)
        file_pending_calls = []
        try:
            file_pending_calls = factory.call_processor.collect_pending_calls_in_file(
                file_path, root_node, language, self.queries
            )
        except Exception as e:
            logger.warning(f"Failed to collect calls for {file_path}: {e}")

        module_qn = self._get_module_qn_prefix(file_path)
        if module_qn:
            self._module_qn_to_file_path[module_qn] = file_path

        # NO flush_all() — writes stay in buffer
        # NO _update_registry_for_file() — process_file() already updated it
        # NO _collect_nodes_for_embeddings() — deferred to Phase 5

        return module_qn, file_pending_calls

    def _modify_file(self, file_path: Path) -> str | None:
        """Modify file: use definition-level diff for efficient updates.

        For modifications, SimpleUpdater compares old definitions in Memgraph
        with new AST, updating only changed definitions. CALLS relationships
        are rebuilt by removing and re-adding the file.
        """
        module_qn = self._get_module_qn_prefix(file_path)
        if not module_qn:
            return None

        file_qn = ".".join(
            [self.project_name]
            + list(file_path.relative_to(self.repo_path).parent.parts)
            + [file_path.stem]
        )

        # Remove old registry entries for this file before updating
        self._remove_registry_entries_for_file(module_qn)

        # Use SimpleUpdater for definition-level diff
        self._simple_updater._update_file_definitions(file_path, file_qn, module_qn)

        # Rebuild CALLS for this file (delete old calls, re-parse)
        self._rebuild_calls_for_single_file(file_path)

        # Re-add updated definitions to registry
        if self._function_registry is not None:
            self._update_registry_for_file(module_qn)

        if not self.skip_embeddings:
            self._collect_nodes_for_embeddings(file_path, module_qn)

        return module_qn

    def _modify_file_deferred(self, file_path: Path) -> tuple[str | None, list]:
        """Modify file in deferred mode: definition diff + collect pending calls.

        Unlike _modify_file(), this method:
        - Does NOT rebuild CALLS per-file (deferred to Phase 3 batch resolution)
        - Does NOT call _update_registry_for_file() (updates registry from
          parsed data instead of querying DB)
        - Skips embedding collection (deferred to post-flush phase)

        SimpleUpdater._update_file_definitions() still runs normally — its
        internal flush_all() only flushes small per-definition buffered nodes
        and its execute_query() calls are direct DB operations for definition
        deletes/updates. This overhead is small and acceptable.

        Returns:
            (module_qn, pending_calls) for batched processing in later phases.
        """
        module_qn = self._get_module_qn_prefix(file_path)
        if not module_qn:
            return None, []

        file_qn = ".".join(
            [self.project_name]
            + list(file_path.relative_to(self.repo_path).parent.parts)
            + [file_path.stem]
        )

        # Remove old registry entries for this file before updating
        self._remove_registry_entries_for_file(module_qn)

        # Use SimpleUpdater for definition-level diff (queries DB for old defs,
        # parses new defs, applies CRUD diff — small per-definition DB ops)
        self._simple_updater._update_file_definitions(file_path, file_qn, module_qn)

        # Update in-memory registry from parsed definitions (no DB query needed).
        # SimpleUpdater._parse_definitions() is fast (tree-sitter < 1ms/file).
        new_defs = self._simple_updater._parse_definitions(file_path, module_qn)
        if self._function_registry is not None:
            for d in new_defs:
                qn = d["qualified_name"]
                node_type = d["type"]
                if node_type in ("Function", "Method", "Class"):
                    self._function_registry[qn] = node_type
                    simple_name = qn.split(".")[-1]
                    self._simple_name_lookup[simple_name].add(qn)

        # Collect pending calls (DON'T resolve or flush — deferred to Phase 3)
        file_pending_calls = []
        lang_config = get_language_config(file_path.suffix)
        if lang_config and lang_config.name in self.parsers:
            language = lang_config.name
            try:
                parser = self.parsers[language]
                source_bytes = file_path.read_bytes()
                tree = parser.parse(source_bytes)
                root_node = tree.root_node

                factory = self._get_factory()
                file_pending_calls = (
                    factory.call_processor.collect_pending_calls_in_file(
                        file_path, root_node, language, self.queries
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to collect calls for {file_path}: {e}")

        # NO _rebuild_calls_for_single_file() — deferred to Phase 3 batch
        # NO _update_registry_for_file() — updated from parsed data above
        # NO _collect_nodes_for_embeddings() — deferred to Phase 5

        return module_qn, file_pending_calls

    def _rebuild_calls_for_single_file(self, file_path: Path) -> int:
        """Rebuild CALLS relationships for a single modified file.

        Removes existing outgoing CALLS from this file's definitions
        and re-parses them.
        """
        module_qn = self._get_module_qn_prefix(file_path)
        if not module_qn:
            return 0

        # Remove old CALLS from this file
        self.ingestor.execute_query(
            """
            MATCH (caller)-[r:CALLS]->(target)
            WHERE caller.qualified_name STARTS WITH $prefix
            DELETE r
            """,
            {"prefix": module_qn + "."},
        )

        # Re-parse calls
        lang_config = get_language_config(file_path.suffix)
        if not lang_config or lang_config.name not in self.parsers:
            return 0

        language = lang_config.name
        factory = self._get_factory()

        try:
            parser = self.parsers[language]
            source_bytes = file_path.read_bytes()
            tree = parser.parse(source_bytes)
            root_node = tree.root_node

            file_pending_calls = factory.call_processor.collect_pending_calls_in_file(
                file_path, root_node, language, self.queries
            )
            if file_pending_calls:
                resolved = factory.call_processor.resolve_pending_calls(
                    file_pending_calls
                )
                self.ingestor.flush_relationships()
                return resolved
        except Exception as e:
            logger.warning(f"Failed to rebuild calls for {file_path}: {e}")

        return 0

    def _find_dependent_files(self, module_qns: set[str]) -> list[Path]:
        """Find files depending on specified modules via IMPORTS."""
        if not module_qns:
            return []

        module_roots = {qn.split(".")[0] for qn in module_qns}
        dependent_files: list[Path] = []

        for module_root in module_roots:
            try:
                results = self.ingestor.fetch_all(
                    """
                    MATCH (f:File)-[:IMPORTS]->(e:ExternalPackage {name: $module_root})
                    RETURN f.path as path
                    """,
                    {"module_root": module_root},
                )
                for result in results:
                    path_str = result.get("path")
                    if path_str:
                        path = (
                            self.repo_path / path_str
                            if not Path(path_str).is_absolute()
                            else Path(path_str)
                        )
                        if path not in dependent_files:
                            dependent_files.append(path)
            except Exception as e:
                logger.debug(f"Failed to find dependents for {module_root}: {e}")

        return dependent_files

    def _rebuild_calls_for_files(self, files: list[Path]) -> int:
        """Reprocess call relationships for specified files."""
        if not files:
            return 0

        logger.info(f"Rebuilding calls for {len(files)} dependent files")
        calls_created = 0
        factory = self._get_factory()

        for file_path in files:
            if not file_path.exists():
                continue

            lang_config = get_language_config(file_path.suffix)
            if not lang_config or lang_config.name not in self.parsers:
                continue

            language = lang_config.name

            # Get cached AST or reparse
            if file_path in self._ast_cache:
                root_node, _ = self._ast_cache[file_path]
            else:
                parser = self.parsers.get(language)
                if not parser:
                    continue
                try:
                    tree = parser.parse(file_path.read_bytes())
                    root_node = tree.root_node
                except Exception as e:
                    logger.debug(f"Failed to reparse {file_path}: {e}")
                    continue

            try:
                file_pending_calls = (
                    factory.call_processor.collect_pending_calls_in_file(
                        file_path, root_node, language, self.queries
                    )
                )
                if file_pending_calls:
                    resolved = factory.call_processor.resolve_pending_calls(
                        file_pending_calls
                    )
                    calls_created += resolved
            except Exception as e:
                logger.debug(f"Failed to rebuild calls for {file_path}: {e}")

        self.ingestor.flush_relationships()
        return calls_created

    # =========================================================================
    # Batch Pipeline Helpers (used by optimized apply_changes)
    # =========================================================================

    def _resolve_calls_batch(
        self,
        all_pending_calls: list,
        modified_module_qns: set[str],
        result: UpdateResult,
    ) -> None:
        """Phase 3: Batch delete old CALLS and resolve all pending calls at once.

        Mirrors the full build's Pass 2 — single batch resolution instead of
        per-file DELETE + resolve + flush cycles.
        """
        if not all_pending_calls and not modified_module_qns:
            return

        self._update_progress(70, "resolving_calls", "Resolving call relationships...")

        # Batch DELETE old CALLS for all modified modules
        if modified_module_qns:
            for module_qn in modified_module_qns:
                try:
                    self.ingestor.execute_query(
                        """
                        MATCH (caller)-[r:CALLS]->(target)
                        WHERE caller.qualified_name STARTS WITH $prefix
                        DELETE r
                        """,
                        {"prefix": module_qn + "."},
                    )
                except Exception as e:
                    logger.warning(f"Failed to delete CALLS for {module_qn}: {e}")

        # Resolve ALL pending calls at once (like full build Pass 2)
        if all_pending_calls:
            factory = self._get_factory()
            try:
                resolved = factory.call_processor.resolve_pending_calls(
                    all_pending_calls
                )
                result.calls_rebuilt = resolved
                logger.info(
                    f"Batch resolved {resolved}/{len(all_pending_calls)} calls"
                )
            except Exception as e:
                logger.warning(f"Failed to resolve pending calls: {e}")
                result.add_error(f"Call resolution: {e}")

        # NOTE: Relationship writes stay in buffer — flushed in Phase 4

    def _batch_update_registry(self, module_qns: set[str]) -> None:
        """Phase 5: Batch update function registry from DB after flush.

        Runs after flush_all() so all nodes are committed to DB.
        Replaces per-file _update_registry_for_file() calls.
        """
        if self._function_registry is None or not module_qns:
            return

        logger.info(f"Batch updating registry for {len(module_qns)} modules...")
        total_added = 0

        for module_qn in module_qns:
            try:
                new_defs = self.ingestor.fetch_all(
                    """
                    MATCH (n) WHERE n.qualified_name STARTS WITH $prefix
                    AND (n:Function OR n:Method OR n:Class)
                    RETURN n.qualified_name AS qn, labels(n) AS labels
                    """,
                    {"prefix": module_qn + "."},
                )

                for d in new_defs:
                    qn = d.get("qn")
                    if not qn:
                        continue
                    labels = d.get("labels", [])
                    node_type = (
                        "Method"
                        if "Method" in labels
                        else ("Class" if "Class" in labels else "Function")
                    )
                    self._function_registry[qn] = node_type
                    simple_name = qn.split(".")[-1]
                    self._simple_name_lookup[simple_name].add(qn)
                    total_added += 1
            except Exception as e:
                logger.debug(f"Failed to update registry for {module_qn}: {e}")

        logger.info(f"Registry updated: {total_added} entries from {len(module_qns)} modules")

    # =========================================================================
    # Main Entry Point
    # =========================================================================

    def apply_changes(self, changes: list[FileChange]) -> UpdateResult:
        """Apply a batch of file changes to the graph.

        Uses an accumulate-then-flush pipeline (modeled after the full build's
        multi-pass architecture) to minimize DB round-trips:

          Phase 0: Setup (deferred flush, optional ANALYTICAL mode)
          Phase 1: Process deletions
          Phase 2: Accumulate definitions + collect pending calls
          Phase 3: Batch call resolution
          Phase 4: Single flush_all()
          Phase 5: Post-processing (registry, embeddings, dependent calls)

        Args:
            changes: List of FileChange objects to apply.

        Returns:
            UpdateResult with statistics.
        """
        start_time = time.time()
        result = UpdateResult()

        if not changes:
            return result

        logger.info("=" * 60)
        logger.info(f"INCREMENTAL UPDATE: Applying {len(changes)} file changes")
        logger.info(f"Project: {self.project_name}, Repo: {self.repo_path}")

        # Group changes
        deletions = [c for c in changes if c.action == "delete"]
        additions = [c for c in changes if c.action == "add"]
        modifications = [c for c in changes if c.action == "modify"]
        total_add_mod = len(additions) + len(modifications)

        logger.info(
            f"Change breakdown: {len(deletions)} del, "
            f"{len(additions)} add, {len(modifications)} mod"
        )

        # Helper to get relative path
        def get_relative_path(file_path: Path) -> str:
            try:
                return str(file_path.relative_to(self.repo_path))
            except ValueError:
                return str(file_path)

        # =================================================================
        # Phase 0: Setup — deferred flush + optional ANALYTICAL mode
        # =================================================================
        ANALYTICAL_THRESHOLD = 20
        use_analytical = total_add_mod >= ANALYTICAL_THRESHOLD
        original_storage_mode = None
        constraints_dropped = False

        if use_analytical:
            try:
                original_storage_mode = self.ingestor.get_storage_mode()
                if original_storage_mode != "IN_MEMORY_ANALYTICAL":
                    logger.info(
                        f"Switching from {original_storage_mode} to "
                        "IN_MEMORY_ANALYTICAL for incremental update"
                    )
                    dropped = self.ingestor.drop_all_constraints()
                    if dropped > 0:
                        constraints_dropped = True
                    self.ingestor.set_storage_mode("IN_MEMORY_ANALYTICAL")
            except Exception as e:
                logger.warning(f"Failed to switch storage mode: {e}")
                original_storage_mode = None

        # Enable deferred flush: accumulate all writes in memory.
        # use_create=False because nodes may already exist (MERGE required).
        self.ingestor.enable_deferred_flush(use_create=False)

        try:
            # =============================================================
            # Phase 1: Deletions (direct DB ops — can't defer deletes)
            # =============================================================
            if deletions:
                self._update_progress(
                    10, "deleting", f"Deleting {len(deletions)} files..."
                )
                for i, change in enumerate(deletions):
                    try:
                        self._notify_file_progress(
                            str(change.path), i, len(deletions)
                        )
                        self._remove_file(change.path)
                        result.deleted += 1
                        result.deleted_files.append(get_relative_path(change.path))
                    except Exception as e:
                        result.add_error(f"Delete {change.path}: {e}")

            # =============================================================
            # Phase 2: Accumulate definitions + collect pending calls
            # =============================================================
            all_pending_calls: list = []
            all_module_qns: set[str] = set()
            modified_module_qns: set[str] = set()  # Modules that need CALLS rebuild

            # Process additions (deferred — no flush, no call resolution)
            if additions:
                self._update_progress(
                    30, "adding", f"Adding {len(additions)} files..."
                )
                for i, change in enumerate(additions):
                    try:
                        self._notify_file_progress(
                            str(change.path), i, len(additions)
                        )
                        module_qn, pending_calls = self._add_file_deferred(
                            change.path
                        )
                        if module_qn:
                            all_module_qns.add(module_qn)
                            # New files also need CALLS created from their calls
                            modified_module_qns.add(module_qn)
                        if pending_calls:
                            all_pending_calls.extend(pending_calls)
                        result.added += 1
                        result.added_files.append(get_relative_path(change.path))
                    except Exception as e:
                        result.add_error(f"Add {change.path}: {e}")

            # Process modifications (deferred — definition diff only, no call rebuild)
            if modifications:
                self._update_progress(
                    50, "modifying", f"Modifying {len(modifications)} files..."
                )
                for i, change in enumerate(modifications):
                    try:
                        self._notify_file_progress(
                            str(change.path), i, len(modifications)
                        )
                        module_qn, pending_calls = self._modify_file_deferred(
                            change.path
                        )
                        if module_qn:
                            all_module_qns.add(module_qn)
                            modified_module_qns.add(module_qn)
                        if pending_calls:
                            all_pending_calls.extend(pending_calls)
                        result.modified += 1
                        result.modified_files.append(get_relative_path(change.path))
                    except Exception as e:
                        result.add_error(f"Modify {change.path}: {e}")

            # =============================================================
            # Phase 3: Batch call resolution
            # =============================================================
            self._resolve_calls_batch(
                all_pending_calls, modified_module_qns, result
            )

            # =============================================================
            # Phase 4: Single flush — all buffered writes go to DB at once
            # =============================================================
            self._update_progress(
                80, "flushing", "Flushing changes to database..."
            )
            self.ingestor.disable_deferred_flush()
            self.ingestor.flush_all()

            # =============================================================
            # Phase 5: Post-processing (requires data committed to DB)
            # =============================================================

            # Batch registry update from DB
            if all_module_qns:
                self._batch_update_registry(all_module_qns)

            # Embeddings (requires nodes in DB for source extraction queries)
            if not self.skip_embeddings and all_module_qns:
                self._update_progress(90, "embedding", "Generating embeddings...")
                for change in additions + modifications:
                    module_qn = self._get_module_qn_prefix(change.path)
                    if module_qn:
                        self._collect_nodes_for_embeddings(change.path, module_qn)
                if self._nodes_pending_embeddings:
                    result.embeddings_generated = (
                        self._generate_embeddings_for_nodes()
                    )

            # Rebuild incoming CALLS for dependent files (files that import
            # the modified modules may have stale call targets)
            if all_module_qns:
                self._update_progress(
                    95,
                    "rebuilding_deps",
                    "Rebuilding dependent call relationships...",
                )
                dependent_files = self._find_dependent_files(all_module_qns)
                if dependent_files:
                    logger.info(
                        f"Found {len(dependent_files)} dependent files, "
                        "rebuilding calls..."
                    )
                    result.calls_rebuilt += self._rebuild_calls_for_files(
                        dependent_files
                    )

        finally:
            # Ensure deferred flush is disabled even on error
            self.ingestor._deferred_flush = False
            self.ingestor.stop_background_flusher()

            # Restore storage mode
            if (
                original_storage_mode
                and original_storage_mode != "IN_MEMORY_ANALYTICAL"
            ):
                try:
                    self.ingestor.set_storage_mode(original_storage_mode)
                except Exception as e:
                    logger.warning(f"Failed to restore storage mode: {e}")

            # Re-create constraints if dropped
            if constraints_dropped:
                try:
                    self.ingestor.ensure_constraints()
                except Exception as e:
                    logger.warning(f"Failed to re-create constraints: {e}")

        # Clear AST cache to save memory, preserve registry for next sync
        self._ast_cache.clear()

        result.duration_ms = (time.time() - start_time) * 1000
        self._update_progress(
            100,
            "complete",
            f"Complete: +{result.added} ~{result.modified} -{result.deleted}",
        )

        logger.info("=" * 60)
        logger.info(
            f"INCREMENTAL UPDATE COMPLETE: +{result.added} ~{result.modified} "
            f"-{result.deleted}, {result.calls_rebuilt} calls rebuilt, "
            f"{result.embeddings_generated} embeddings, "
            f"{result.duration_ms:.0f}ms"
        )
        if result.errors:
            for err in result.errors[:10]:
                logger.warning(f"  - {err}")
        logger.info("=" * 60)

        return result

    # =========================================================================
    # Embedding Support
    # =========================================================================

    def _collect_nodes_for_embeddings(
        self, file_path: Path, module_qn: str | None
    ) -> None:
        """Collect nodes from a file that need embeddings."""
        try:
            relative_path = file_path.relative_to(self.repo_path)
        except ValueError:
            return

        if self.embedding_granularity == "class":
            query = """
            MATCH (f:File {path: $path})-[:DEFINES]->(n)
            WHERE n:Function OR n:Class
            RETURN n.qualified_name AS qualified_name,
                   labels(n) AS labels,
                   n.start_line AS start_line,
                   n.end_line AS end_line,
                   f.path AS path
            """
        else:
            query = """
            MATCH (f:File {path: $path})-[:DEFINES]->(n)
            WHERE n:Function OR n:Class
            RETURN n.qualified_name AS qualified_name,
                   labels(n) AS labels,
                   n.start_line AS start_line,
                   n.end_line AS end_line,
                   f.path AS path
            UNION ALL
            MATCH (f:File {path: $path})-[:DEFINES]->(c:Class)-[:DEFINES_METHOD]->(n:Method)
            RETURN n.qualified_name AS qualified_name,
                   labels(n) AS labels,
                   n.start_line AS start_line,
                   n.end_line AS end_line,
                   f.path AS path
            """

        try:
            results = self.ingestor.fetch_all(query, {"path": str(relative_path)})
            for r in results:
                labels = r.get("labels", [])
                node_type = (
                    "Method"
                    if "Method" in labels
                    else ("Class" if "Class" in labels else "Function")
                )
                self._nodes_pending_embeddings.append(
                    {
                        "qualified_name": r.get("qualified_name"),
                        "node_type": node_type,
                        "file_path": r.get("path"),
                        "start_line": r.get("start_line"),
                        "end_line": r.get("end_line"),
                    }
                )
        except Exception as e:
            logger.debug(f"Failed to collect nodes for embeddings: {e}")

    def _generate_embeddings_for_nodes(self) -> int:
        """Generate embeddings for pending nodes."""
        if not self._nodes_pending_embeddings:
            return 0

        if self.async_embeddings and self._embedding_queue:
            queued = self._embedding_queue.enqueue(self._nodes_pending_embeddings)
            self._nodes_pending_embeddings.clear()
            return queued

        return self._generate_embeddings_sync()

    def _generate_embeddings_sync(self) -> int:
        """Generate embeddings synchronously."""
        if not self._nodes_pending_embeddings:
            return 0

        try:
            from core.config import settings
            from graph.embedder import (
                embed_code_batch_for_repo,
                get_embedding_dimension,
            )

            dimension = get_embedding_dimension()
            self.ingestor.setup_vector_index(dimension=dimension)

            def extract_source(node_info: dict) -> tuple[dict, str | None]:
                qn = node_info["qualified_name"]
                start = node_info.get("start_line")
                end = node_info.get("end_line")
                fp = node_info.get("file_path")
                if not fp or not start or not end:
                    return node_info, None
                full_path = self.repo_path / fp
                source = extract_source_with_fallback(full_path, start, end, qn, None)
                return node_info, source

            source_data: list[tuple[dict, str | None]] = []
            with ThreadPoolExecutor(max_workers=self.parallel_workers) as executor:
                futures = [
                    executor.submit(extract_source, n)
                    for n in self._nodes_pending_embeddings
                ]
                for future in as_completed(futures):
                    try:
                        info, source = future.result()
                        if source:
                            source_data.append((info, source))
                    except Exception:
                        pass

            if not source_data:
                return 0

            codes = [s for _, s in source_data]
            embeddings = embed_code_batch_for_repo(
                codes,
                repo_name=self.project_name,
                parallel=True,
                max_concurrent=settings.EMBEDDING_MAX_CONCURRENT,
            )

            embeddings_by_type: dict[str, list[dict]] = {}
            for (info, _), emb in zip(source_data, embeddings):
                nt = info["node_type"]
                embeddings_by_type.setdefault(nt, []).append(
                    {
                        "qualified_name": info["qualified_name"],
                        "embedding": emb,
                    }
                )

            total = 0
            for nt, data in embeddings_by_type.items():
                if data:
                    total += self.ingestor.update_embeddings_batch(nt, data)

            self._nodes_pending_embeddings.clear()
            return total

        except Exception as e:
            logger.warning(f"Failed to generate embeddings: {e}")
            return 0

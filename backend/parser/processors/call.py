# Copyright 2025 Vitali Avagyan.
# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: MIT AND Apache-2.0
#
# This file is derived from code-graph-rag (MIT License).
# Modifications by SiOrigin Co. Ltd. are licensed under Apache-2.0.
# See the LICENSE file in the root directory for details.

import re
import threading
from pathlib import Path
from typing import Any

from core.language_config import LanguageConfig
from graph.service import MemgraphIngestor
from loguru import logger

# No longer need constants import - using Tree-sitter directly
from parser.languages.cpp import (
    convert_operator_symbol_to_name,
    extract_cpp_function_name,
)
from parser.languages.python import resolve_class_name
from tree_sitter import Node, QueryCursor

from .import_ import ImportProcessor
from .local_module_filter import LocalModuleFilter
from .pending_call import PendingCall
from .stdlib_checker import StdlibChecker
from .type_inference import TypeInferenceEngine

# Sentinel value for cache miss (distinguishes "not cached" from "cached as None")
_CACHE_MISS = object()

# Module-level globals for multiprocessing workers (set before fork, inherited by children)
# These are set in the parent process before forking and accessed read-only in children.
_mp_call_processor: "CallProcessor | None" = None
_mp_import_mapping_cache: "dict[str, dict[str, str]] | None" = None
_mp_pending_calls: "list[PendingCall] | None" = None


def _mp_resolve_batch_by_indices(
    index_range: tuple[int, int],
) -> list[tuple[str, str, str, str, str, str | None, str | None]]:
    """Multiprocessing worker function for resolving PendingCall objects by index range.

    Runs in a forked child process with copy-on-write access to the parent's
    CallProcessor state and PendingCall list. Uses indices instead of passing
    PendingCall objects to avoid pickling overhead (~41MB for 100K calls).

    Args:
        index_range: (start_index, end_index) into the global _mp_pending_calls list

    Returns:
        List of resolved call tuples:
        (caller_type, caller_qn, rel_type, callee_type, callee_qn, ext_import_source, ext_root_module)
        Only resolved calls are returned (unresolved calls are omitted).
    """
    processor = _mp_call_processor
    import_cache = _mp_import_mapping_cache
    calls = _mp_pending_calls
    start, end = index_range
    results = []

    for i in range(start, end):
        pc = calls[i]
        call_name = pc.callee_name
        module_qn = pc.module_qn
        import_mapping = import_cache.get(module_qn) if import_cache else None
        if import_mapping is None:
            import_mapping = processor.import_processor.import_mapping.get(
                module_qn, {}
            )

        callee_info = None
        ext_import_source = None
        ext_root_module = None

        # Handle external calls with stored import info (skip side effects)
        if pc.is_imported and pc.import_source:
            root_module = pc.import_source.split(".")[0]
            if processor.local_module_filter.is_tracked_external(root_module):
                callee_info = ("Function", pc.import_source)
                ext_import_source = pc.import_source
                ext_root_module = root_module

        if callee_info is None:
            # Use the standard resolution logic (read-only, no side effects)
            callee_info = processor._resolve_function_call(
                call_name,
                module_qn,
                pc.local_var_types if pc.local_var_types else None,
                pc.class_context,
                import_mapping,
            )

        if callee_info is None:
            # Try built-in resolution
            callee_info = processor._resolve_builtin_call(call_name)

        if callee_info is None:
            # Try C++ operator resolution
            callee_info = processor._resolve_cpp_operator_call(call_name, module_qn)

        if callee_info is not None:
            callee_type, callee_qn = callee_info
            results.append(
                (
                    pc.caller_type,
                    pc.caller_qn,
                    pc.relationship_type,
                    callee_type,
                    callee_qn,
                    ext_import_source,
                    ext_root_module,
                )
            )

    return results


class CallProcessor:
    """Handles processing of function and method calls."""

    # JavaScript built-in types for type inference
    _JS_BUILTIN_TYPES = {
        "Array",
        "Object",
        "String",
        "Number",
        "Date",
        "RegExp",
        "Function",
        "Map",
        "Set",
        "Promise",
        "Error",
        "Boolean",
    }

    # JavaScript built-in patterns for static method calls
    _JS_BUILTIN_PATTERNS = {
        # Object static methods
        "Object.create",
        "Object.keys",
        "Object.values",
        "Object.entries",
        "Object.assign",
        "Object.freeze",
        "Object.seal",
        "Object.defineProperty",
        "Object.getPrototypeOf",
        "Object.setPrototypeOf",
        # Array static methods
        "Array.from",
        "Array.of",
        "Array.isArray",
        # Global functions
        "parseInt",
        "parseFloat",
        "isNaN",
        "isFinite",
        "encodeURIComponent",
        "decodeURIComponent",
        "setTimeout",
        "clearTimeout",
        "setInterval",
        "clearInterval",
        # Console methods
        "console.log",
        "console.error",
        "console.warn",
        "console.info",
        "console.debug",
        # JSON methods
        "JSON.parse",
        "JSON.stringify",
        # Math static methods
        "Math.random",
        "Math.floor",
        "Math.ceil",
        "Math.round",
        "Math.abs",
        "Math.max",
        "Math.min",
        # Date static methods
        "Date.now",
        "Date.parse",
    }

    # Logging interval for progress updates
    _PROGRESS_LOG_INTERVAL = 10000  # Log every 10k calls processed

    # Thresholds for scope processing
    _SCOPE_DEBUG_LOG_THRESHOLD = 100  # Log debug info when call count exceeds this
    _MAX_CALLS_PER_SCOPE = 5000  # Hard limit to prevent hangs on huge files
    _SCOPE_PROGRESS_LOG_INTERVAL = 500  # Log progress every N calls in large scopes
    _MAX_NESTED_CALL_DEPTH = 100  # Max AST traversal depth for nested calls
    _INTERMEDIATE_FLUSH_INTERVAL = 5000  # Flush relationships every N resolved calls

    # Batch size for parallel call resolution to reduce Future object overhead
    _PARALLEL_BATCH_SIZE = 1000

    # Cached built-ins set (loaded from StdlibChecker for completeness)
    _python_builtins_cache: frozenset | None = None

    # Pre-compiled regex patterns for hot paths
    _QUALIFIED_NAME_SPLIT_PATTERN = re.compile(r"[.:]|::")
    _METHOD_NAME_PATTERN = re.compile(r"\.([^.()]+)$")

    def __init__(
        self,
        ingestor: MemgraphIngestor,
        repo_path: Path,
        project_name: str,
        function_registry: Any,
        simple_name_lookup: dict[str, set[str]],
        import_processor: ImportProcessor,
        type_inference: TypeInferenceEngine,
        class_inheritance: dict[str, list[str]],
        callers_index: Any = None,  # Optional: CallersIndex for reverse call tracking
    ):
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = project_name
        self.function_registry = function_registry
        self.simple_name_lookup = simple_name_lookup
        self.import_processor = import_processor
        self.type_inference = type_inference
        self.class_inheritance = class_inheritance
        self.local_module_filter = LocalModuleFilter(repo_path, project_name)

        # Optional: Reverse call index for incremental updates
        self.callers_index = callers_index

        # Track external dependency call counts for project metadata
        self.external_call_counts: dict[str, int] = {}

        # Thread-safety locks for parallel processing
        self._external_counts_lock = threading.Lock()
        self._registry_lock = threading.Lock()
        self._callers_lock = threading.Lock()  # For callers_index writes

        # Track current file path for callers index
        self._current_file_path: str | None = None

        # Cache for _resolve_inherited_method results to avoid repeated BFS traversals
        # Key: (class_qn, method_name), Value: result tuple or None
        # Thread-safe: dict reads are atomic in CPython; writes protected by lock
        self._inheritance_cache: dict[tuple[str, str], tuple[str, str] | None] = {}
        self._inheritance_cache_lock = threading.Lock()

    def set_callers_index(self, callers_index: Any) -> None:
        """Set or update the callers index (for dependency injection).

        Args:
            callers_index: CallersIndex instance
        """
        self.callers_index = callers_index

    def _write_call_to_index(
        self, caller_qn: str, callee_qn: str, line: int = 0
    ) -> None:
        """Thread-safe write to the reverse call index.

        Args:
            caller_qn: Qualified name of the calling function
            callee_qn: Qualified name of the target function
            line: Line number of the call (optional, defaults to 0)
        """
        if self.callers_index is None or self._current_file_path is None:
            return

        # Use lock for thread-safety during parallel processing
        with self._callers_lock:
            self.callers_index.add_call(
                caller_qn=caller_qn,
                target_qn=callee_qn,
                file=self._current_file_path,
                line=line,
            )

    def _create_external_stub_node(
        self,
        qualified_name: str,
        external_module: str,
    ) -> None:
        """Create a stub node for an external library function.

        Thread-safe: Uses lock for registry modifications.

        Args:
            qualified_name: Full qualified name (e.g., "deep_gemm.fp8_gemm_nt")
            external_module: The root package name (e.g., "deep_gemm")
        """
        name = qualified_name.split(".")[-1]

        # ingestor.ensure_node_batch is already thread-safe
        self.ingestor.ensure_node_batch(
            "Function",
            {
                "qualified_name": qualified_name,
                "name": name,
                "is_external": True,
                "is_stub": True,
                "external_module": external_module,
            },
        )

        # Also register in function_registry so resolution works
        # Use lock for thread-safety during parallel processing
        with self._registry_lock:
            self.function_registry[qualified_name] = "Function"
            self.simple_name_lookup[name].add(qualified_name)

        logger.debug(
            f"Created external stub node: {qualified_name} (module: {external_module})"
        )

    def _track_external_call(self, external_module: str) -> None:
        """Track external dependency call for project metadata.

        Thread-safe: Uses lock for count modifications.

        Args:
            external_module: The root package name
        """
        with self._external_counts_lock:
            if external_module not in self.external_call_counts:
                self.external_call_counts[external_module] = 0
            self.external_call_counts[external_module] += 1

    def process_calls_in_file(
        self, file_path: Path, root_node: Node, language: str, queries: dict[str, Any]
    ) -> None:
        """Process all function/method calls in a file and create CALLS relationships.

        Walks the AST to find call expressions within each function/method scope,
        resolves each call to its target definition, and ingests the resulting
        CALLS relationships into the graph database.

        Args:
            file_path: Absolute path to the source file.
            root_node: The Tree-sitter root node of the parsed AST.
            language: The language identifier (e.g., "python", "javascript").
            queries: Dictionary of Tree-sitter query objects keyed by language.
        """
        try:
            relative_path = file_path.relative_to(self.repo_path)
        except ValueError:
            logger.warning(f"File not relative to repo_path, skipping: {file_path}")
            return

        # Set current file path for callers index
        self._current_file_path = str(relative_path)

        logger.debug(f"Processing calls in cached AST for: {relative_path}")

        try:
            # Build module QN, avoiding duplicate project_name prefix
            parts = list(relative_path.with_suffix("").parts)
            if parts and parts[0] == self.project_name:
                parts = parts[1:]
            module_qn = ".".join([self.project_name] + parts)
            if file_path.name in ("__init__.py", "mod.rs"):
                parent_parts = list(relative_path.parent.parts)
                if parent_parts and parent_parts[0] == self.project_name:
                    parent_parts = parent_parts[1:]
                module_qn = ".".join(
                    [self.project_name] + parent_parts
                )

            self._process_calls_in_functions(root_node, module_qn, language, queries)
            self._process_calls_in_classes(root_node, module_qn, language, queries)
            self._process_module_level_calls(root_node, module_qn, language, queries)

        except Exception as e:
            logger.error(f"Failed to process calls in {file_path}: {e}")

    # =========================================================================
    # NEW: PendingCall-based methods for memory-optimized call resolution
    # =========================================================================

    def collect_pending_calls_in_file(
        self, file_path: Path, root_node: Node, language: str, queries: dict[str, Any]
    ) -> list[PendingCall]:
        """Collect unresolved calls from a file's AST as PendingCall objects.

        This method is used in Pass 1 to extract call information without
        immediately resolving them. The AST can be freed after this method
        returns, saving memory.

        Args:
            file_path: Path to the source file
            root_node: Root node of the AST
            language: Language identifier
            queries: Tree-sitter queries for the language

        Returns:
            List of PendingCall objects for deferred resolution in Pass 2
        """
        pending_calls: list[PendingCall] = []

        try:
            relative_path = file_path.relative_to(self.repo_path)
        except ValueError:
            logger.warning(f"File not relative to repo_path, skipping: {file_path}")
            return pending_calls

        logger.debug(f"  [CALLS] START: {relative_path}")

        try:
            # Build module QN, avoiding duplicate project_name prefix
            # (consistent with definition.py:514-517)
            parts = list(relative_path.with_suffix("").parts)
            if parts and parts[0] == self.project_name:
                parts = parts[1:]
            module_qn = ".".join([self.project_name] + parts)
            if file_path.name in ("__init__.py", "mod.rs"):
                parent_parts = list(relative_path.parent.parts)
                if parent_parts and parent_parts[0] == self.project_name:
                    parent_parts = parent_parts[1:]
                module_qn = ".".join(
                    [self.project_name] + parent_parts
                )

            # Collect from functions
            logger.debug(f"  [CALLS] collecting from functions: {relative_path}")
            func_calls = self._collect_calls_in_functions(
                root_node, module_qn, language, queries, str(file_path)
            )
            pending_calls.extend(func_calls)
            logger.debug(f"  [CALLS] functions done: {len(func_calls)} calls")

            # Collect from classes
            logger.debug(f"  [CALLS] collecting from classes: {relative_path}")
            class_calls = self._collect_calls_in_classes(
                root_node, module_qn, language, queries, str(file_path)
            )
            pending_calls.extend(class_calls)
            logger.debug(f"  [CALLS] classes done: {len(class_calls)} calls")

            # Collect module-level calls
            logger.debug(f"  [CALLS] collecting module-level: {relative_path}")
            module_calls = self._collect_module_level_calls(
                root_node, module_qn, language, queries, str(file_path)
            )
            pending_calls.extend(module_calls)
            logger.debug(f"  [CALLS] module-level done: {len(module_calls)} calls")

            logger.debug(
                f"  [CALLS] COMPLETED: {relative_path} ({len(pending_calls)} total)"
            )

        except Exception as e:
            logger.error(f"Failed to collect pending calls from {file_path}: {e}")

        return pending_calls

    def _collect_calls_in_functions(
        self,
        root_node: Node,
        module_qn: str,
        language: str,
        queries: dict[str, Any],
        file_path: str,
    ) -> list[PendingCall]:
        """Collect calls within top-level functions."""
        pending_calls: list[PendingCall] = []
        lang_queries = queries[language]
        lang_config: LanguageConfig = lang_queries["config"]

        query = lang_queries["functions"]
        cursor = QueryCursor(query)
        captures = cursor.captures(root_node)
        func_nodes = captures.get("function", [])

        for func_node in func_nodes:
            if not isinstance(func_node, Node):
                continue
            if self._is_method(func_node, lang_config):
                continue

            # Extract function name
            if language == "cpp":
                func_name = extract_cpp_function_name(func_node)
                if not func_name:
                    continue
            else:
                name_node = func_node.child_by_field_name("name")
                if not name_node or name_node.text is None:
                    continue
                func_name = name_node.text.decode("utf8")

            func_qn = self._build_nested_qualified_name(
                func_node, module_qn, func_name, lang_config
            )

            if func_qn:
                pending_calls.extend(
                    self._collect_calls_in_scope(
                        func_node,
                        func_qn,
                        "Function",
                        module_qn,
                        language,
                        queries,
                        file_path,
                        class_context=None,
                    )
                )

                # OC4: Collect decorator calls (non-parenthesized decorators)
                if language == "python":
                    pending_calls.extend(
                        self._collect_decorator_calls(
                            func_node, func_qn, "Function", module_qn, file_path
                        )
                    )

        return pending_calls

    def _collect_calls_in_classes(
        self,
        root_node: Node,
        module_qn: str,
        language: str,
        queries: dict[str, Any],
        file_path: str,
    ) -> list[PendingCall]:
        """Collect calls within class methods."""
        pending_calls: list[PendingCall] = []
        lang_queries = queries[language]

        if not lang_queries.get("classes"):
            return pending_calls

        query = lang_queries["classes"]
        cursor = QueryCursor(query)
        captures = cursor.captures(root_node)
        class_nodes = captures.get("class", [])

        for class_node in class_nodes:
            if not isinstance(class_node, Node):
                continue

            # Handle Rust impl blocks
            if language == "rust" and class_node.type == "impl_item":
                type_node = class_node.child_by_field_name("type")
                if not type_node:
                    for child in class_node.children:
                        if child.type == "type_identifier" and child.is_named:
                            type_node = child
                            break
                if not type_node or not type_node.text:
                    continue
                class_name = type_node.text.decode("utf8")
                class_qn = f"{module_qn}.{class_name}"
            else:
                name_node = class_node.child_by_field_name("name")
                if not name_node or name_node.text is None:
                    continue
                class_name = name_node.text.decode("utf8")
                class_qn = f"{module_qn}.{class_name}"

            # OC4: Collect decorator calls for class definitions
            if language == "python":
                pending_calls.extend(
                    self._collect_decorator_calls(
                        class_node, class_qn, "Class", module_qn, file_path
                    )
                )

            body_node = class_node.child_by_field_name("body")
            if not body_node:
                continue

            method_query = lang_queries["functions"]
            method_cursor = QueryCursor(method_query)
            method_captures = method_cursor.captures(body_node)
            method_nodes = method_captures.get("function", [])

            for method_node in method_nodes:
                if not isinstance(method_node, Node):
                    continue
                method_name_node = method_node.child_by_field_name("name")
                if not method_name_node or method_name_node.text is None:
                    continue
                method_name = method_name_node.text.decode("utf8")
                method_qn = f"{class_qn}.{method_name}"

                pending_calls.extend(
                    self._collect_calls_in_scope(
                        method_node,
                        method_qn,
                        "Method",
                        module_qn,
                        language,
                        queries,
                        file_path,
                        class_context=class_qn,
                    )
                )

                # OC4: Collect decorator calls for methods
                if language == "python":
                    pending_calls.extend(
                        self._collect_decorator_calls(
                            method_node, method_qn, "Method", module_qn, file_path
                        )
                    )

        return pending_calls

    def _collect_module_level_calls(
        self,
        root_node: Node,
        module_qn: str,
        language: str,
        queries: dict[str, Any],
        file_path: str,
    ) -> list[PendingCall]:
        """Collect top-level calls in the file (not inside functions or classes).

        This only collects calls that are directly at the module level,
        not inside any function or class definition.
        """
        pending_calls: list[PendingCall] = []
        calls_query = queries[language].get("calls")
        if not calls_query:
            return pending_calls

        lang_queries = queries[language]
        lang_config: LanguageConfig = lang_queries["config"]

        # Get sets of node types to exclude
        function_types = set(lang_config.function_node_types)
        class_types = set(lang_config.class_node_types)
        excluded_types = function_types | class_types

        # Query all calls in the file
        cursor = QueryCursor(calls_query)
        captures = cursor.captures(root_node)
        all_call_nodes = captures.get("call", [])

        # Filter to only include module-level calls (not inside functions or classes)
        module_level_calls = []
        for call_node in all_call_nodes:
            if not isinstance(call_node, Node):
                continue

            # Check if this call is inside a function or class
            is_inside_excluded = False
            parent = call_node.parent
            while parent is not None:
                if parent.type in excluded_types:
                    is_inside_excluded = True
                    break
                parent = parent.parent

            if not is_inside_excluded:
                module_level_calls.append(call_node)

        if module_level_calls:
            logger.debug(
                f"    [MODULE] Found {len(module_level_calls)} module-level calls (filtered from {len(all_call_nodes)})"
            )

        import_mapping = self.import_processor.import_mapping.get(module_qn, {})

        for call_node in module_level_calls:
            call_name = self._get_call_target_name(call_node)
            if not call_name:
                continue

            # Check if external call
            is_external_call = False
            import_source = None
            if call_name in import_mapping:
                imported_qn = import_mapping[call_name]
                root_module = imported_qn.split(".")[0]
                if self.local_module_filter.is_tracked_external(root_module):
                    is_external_call = True
                    import_source = imported_qn

            # Apply local module filtering
            should_process = self.local_module_filter.should_process_call(
                call_name, module_qn, import_mapping, language
            )
            if (
                not should_process
                and not self._is_potential_binding_call(call_name)
                and not is_external_call
            ):
                continue

            # Determine call type
            call_type = "function"
            receiver_expr = None
            if "." in call_name or ":" in call_name or "::" in call_name:
                call_type = "method"
                parts = self._QUALIFIED_NAME_SPLIT_PATTERN.split(call_name)
                if len(parts) >= 2:
                    receiver_expr = parts[0]

            pending_call = PendingCall(
                caller_qn=module_qn,
                caller_type="File",
                callee_name=call_name,
                call_type=call_type,
                module_qn=module_qn,
                file_path=file_path,
                line_number=call_node.start_point[0] + 1,
                class_context=None,
                local_var_types={},
                receiver_expr=receiver_expr,
                is_imported=call_name in import_mapping,
                import_source=import_source,
            )
            pending_calls.append(pending_call)

        return pending_calls

    def _collect_calls_in_scope(
        self,
        scope_node: Node,
        caller_qn: str,
        caller_type: str,
        module_qn: str,
        language: str,
        queries: dict[str, Any],
        file_path: str,
        class_context: str | None = None,
    ) -> list[PendingCall]:
        """Collect all calls within a scope (function, method, or module).

        This extracts call information and creates PendingCall objects
        without immediately resolving them.
        """
        pending_calls: list[PendingCall] = []
        calls_query = queries[language].get("calls")
        if not calls_query:
            return pending_calls

        # Pre-compute local variable types for this scope
        # This is computed once per scope and stored in each PendingCall
        local_var_types: dict[str, str] | None = None

        cursor = QueryCursor(calls_query)
        captures = cursor.captures(scope_node)
        call_nodes = captures.get("call", [])

        # Log for debugging - if too many calls, this might be the bottleneck
        if len(call_nodes) > self._SCOPE_DEBUG_LOG_THRESHOLD:
            logger.debug(
                f"    [SCOPE] {caller_type} {caller_qn}: {len(call_nodes)} call nodes to process"
            )

        import_mapping = self.import_processor.import_mapping.get(module_qn, {})

        # Limit processing to prevent hangs on huge files
        if len(call_nodes) > self._MAX_CALLS_PER_SCOPE:
            logger.warning(
                f"    [SCOPE] Limiting {caller_qn}: {len(call_nodes)} calls -> {self._MAX_CALLS_PER_SCOPE}"
            )
            call_nodes = call_nodes[: self._MAX_CALLS_PER_SCOPE]

        for idx, call_node in enumerate(call_nodes):
            if not isinstance(call_node, Node):
                continue

            # Log progress for large scopes
            if (
                len(call_nodes) > self._SCOPE_PROGRESS_LOG_INTERVAL
                and idx > 0
                and idx % self._SCOPE_PROGRESS_LOG_INTERVAL == 0
            ):
                logger.debug(
                    f"    [SCOPE] Processing call {idx}/{len(call_nodes)} in {caller_qn}"
                )

            # Also collect nested calls
            nested_calls = self._collect_nested_calls(
                call_node,
                caller_qn,
                caller_type,
                module_qn,
                language,
                queries,
                file_path,
                class_context,
                local_var_types,
            )
            pending_calls.extend(nested_calls)

            call_name = self._get_call_target_name(call_node)
            if not call_name:
                continue

            # Check if external call
            is_external_call = False
            import_source = None
            if call_name in import_mapping:
                imported_qn = import_mapping[call_name]
                root_module = imported_qn.split(".")[0]
                if self.local_module_filter.is_tracked_external(root_module):
                    is_external_call = True
                    import_source = imported_qn

            # Apply local module filtering
            should_process = self.local_module_filter.should_process_call(
                call_name, module_qn, import_mapping, language
            )
            if (
                not should_process
                and not self._is_potential_binding_call(call_name)
                and not is_external_call
            ):
                continue

            # Lazy compute local_var_types only if needed
            needs_type_inference = (
                "." in call_name
                and call_name.split(".")[0] not in import_mapping
                and not call_name.startswith("self.")
            )
            if needs_type_inference and local_var_types is None:
                local_var_types = self.type_inference.build_local_variable_type_map(
                    scope_node, module_qn, language
                )

            # Determine call type
            call_type = "function"
            receiver_expr = None
            if "." in call_name or ":" in call_name or "::" in call_name:
                call_type = "method"
                parts = self._QUALIFIED_NAME_SPLIT_PATTERN.split(call_name)
                if len(parts) >= 2:
                    receiver_expr = parts[0]

            # Create PendingCall
            pending_call = PendingCall(
                caller_qn=caller_qn,
                caller_type=caller_type,
                callee_name=call_name,
                call_type=call_type,
                module_qn=module_qn,
                file_path=file_path,
                line_number=call_node.start_point[0] + 1,
                class_context=class_context,
                local_var_types=local_var_types.copy() if local_var_types else {},
                receiver_expr=receiver_expr,
                is_imported=call_name in import_mapping,
                import_source=import_source,
            )
            pending_calls.append(pending_call)

        return pending_calls

    def _collect_decorator_calls(
        self,
        decorated_node: Node,
        caller_qn: str,
        caller_type: str,
        module_qn: str,
        file_path: str,
    ) -> list[PendingCall]:
        """Collect calls from decorators that don't have parentheses.

        OC4: Decorators like @my_decorator (without parentheses) are not captured
        by the tree-sitter call query because they don't produce 'call' nodes.
        Decorators with parentheses (@decorator(args)) DO produce 'call' nodes
        and are already captured.

        This method creates PendingCall objects for non-parenthesized decorators.
        """
        pending_calls: list[PendingCall] = []

        # Check if parent is decorated_definition
        parent = decorated_node.parent
        if not parent or parent.type != "decorated_definition":
            return pending_calls

        import_mapping = self.import_processor.import_mapping.get(module_qn, {})

        for child in parent.children:
            if child.type != "decorator":
                continue

            # Check if this decorator has a call node (already captured)
            has_call = False
            decorator_name = None
            for deco_child in child.children:
                if deco_child.type == "call":
                    has_call = True
                    break
                elif deco_child.type == "identifier" and deco_child.text:
                    decorator_name = deco_child.text.decode("utf8")
                elif deco_child.type == "attribute" and deco_child.text:
                    decorator_name = deco_child.text.decode("utf8")

            # Skip decorators with parentheses (already captured as calls)
            if has_call or not decorator_name:
                continue

            # Skip Python builtins like @staticmethod, @classmethod, @property
            if decorator_name in (
                "staticmethod",
                "classmethod",
                "property",
                "abstractmethod",
            ):
                continue

            # Check if this decorator should be processed
            is_external_call = False
            import_source = None
            if decorator_name in import_mapping:
                imported_qn = import_mapping[decorator_name]
                root_module = imported_qn.split(".")[0]
                if self.local_module_filter.is_tracked_external(root_module):
                    is_external_call = True
                    import_source = imported_qn

            should_process = self.local_module_filter.should_process_call(
                decorator_name, module_qn, import_mapping
            )
            if not should_process and not is_external_call:
                continue

            # Determine call type
            call_type = "function"
            receiver_expr = None
            if "." in decorator_name:
                call_type = "method"
                parts = decorator_name.split(".")
                receiver_expr = parts[0] if len(parts) >= 2 else None

            pending_call = PendingCall(
                caller_qn=caller_qn,
                caller_type=caller_type,
                callee_name=decorator_name,
                call_type=call_type,
                module_qn=module_qn,
                file_path=file_path,
                line_number=child.start_point[0] + 1,
                class_context=None,
                local_var_types={},
                receiver_expr=receiver_expr,
                is_imported=decorator_name in import_mapping,
                import_source=import_source,
            )
            pending_calls.append(pending_call)

        return pending_calls

    def _collect_nested_calls(
        self,
        call_node: Node,
        caller_qn: str,
        caller_type: str,
        module_qn: str,
        language: str,
        queries: dict[str, Any],
        file_path: str,
        class_context: str | None,
        local_var_types: dict[str, str] | None,
    ) -> list[PendingCall]:
        """Collect nested call expressions within a call node's function expression."""
        pending_calls: list[PendingCall] = []
        func_child = call_node.child_by_field_name("function")
        if not func_child:
            return pending_calls

        if func_child.type == "attribute":
            self._find_and_collect_nested_calls(
                func_child,
                caller_qn,
                caller_type,
                module_qn,
                language,
                file_path,
                class_context,
                local_var_types,
                pending_calls,
            )

        return pending_calls

    def _find_and_collect_nested_calls(
        self,
        node: Node,
        caller_qn: str,
        caller_type: str,
        module_qn: str,
        language: str,
        file_path: str,
        class_context: str | None,
        local_var_types: dict[str, str] | None,
        pending_calls: list[PendingCall],
        max_depth: int = 100,
    ) -> None:
        """Iteratively find and collect call expressions in a node tree.

        Uses an explicit stack instead of recursion to avoid stack overflow
        on deeply nested ASTs. Also limits traversal depth to prevent
        infinite loops on malformed or circular AST structures.

        Args:
            max_depth: Maximum traversal depth (default 100). Files with deeper
                nesting are truncated to prevent hangs.
        """
        import_mapping = self.import_processor.import_mapping.get(module_qn, {})

        # Use explicit stack for iteration instead of recursion
        # Stack contains (node, depth) tuples
        stack: list[tuple[Node, int]] = [(node, 0)]
        visited_nodes: set[int] = set()  # Track visited nodes by id to prevent cycles

        while stack:
            current_node, depth = stack.pop()

            # Skip if we've already visited this node (prevent cycles)
            node_id = id(current_node)
            if node_id in visited_nodes:
                continue
            visited_nodes.add(node_id)

            # Skip if we've exceeded max depth
            if depth > max_depth:
                continue

            if current_node.type == "call":
                # Process the call node
                func_child = current_node.child_by_field_name("function")
                if func_child and func_child.type == "attribute":
                    # Add attribute node for further processing
                    stack.append((func_child, depth + 1))

                call_name = self._get_call_target_name(current_node)
                if call_name:
                    call_type = "function"
                    receiver_expr = None
                    if "." in call_name:
                        call_type = "method"
                        parts = call_name.split(".")
                        receiver_expr = parts[0] if len(parts) >= 2 else None

                    pending_call = PendingCall(
                        caller_qn=caller_qn,
                        caller_type=caller_type,
                        callee_name=call_name,
                        call_type=call_type,
                        module_qn=module_qn,
                        file_path=file_path,
                        line_number=current_node.start_point[0] + 1,
                        class_context=class_context,
                        local_var_types=local_var_types.copy()
                        if local_var_types
                        else {},
                        receiver_expr=receiver_expr,
                        is_imported=call_name in import_mapping,
                    )
                    pending_calls.append(pending_call)

            # Add children to stack (in reverse order to maintain left-to-right processing)
            for child in reversed(current_node.children):
                stack.append((child, depth + 1))

    def resolve_pending_calls(
        self,
        pending_calls: list[PendingCall],
        parallel: bool = False,
        max_workers: int | None = None,
    ) -> int:
        """Resolve all pending calls and create CALLS relationships.

        This method is called in Pass 2 after function_registry is complete.
        It resolves each PendingCall using the complete function_registry
        and creates the appropriate relationships in the graph.

        Args:
            pending_calls: List of PendingCall objects to resolve
            parallel: If True, use parallel processing (default: False)
            max_workers: Number of worker threads for parallel mode (default: 64)

        Returns:
            Number of successfully resolved calls
        """
        if not pending_calls:
            return 0

        if parallel:
            # Use multiprocessing for true CPU parallelism (bypasses GIL)
            # Falls back to thread-based parallel if multiprocessing fails
            return self._resolve_pending_calls_multiprocess(pending_calls, max_workers)
        else:
            return self._resolve_pending_calls_sequential(pending_calls)

    def _resolve_pending_calls_sequential(
        self, pending_calls: list[PendingCall]
    ) -> int:
        """Resolve pending calls sequentially, one at a time.

        Filters out Python built-in calls before resolution, creates CALLS
        relationships in the graph for each resolved call, and periodically
        flushes relationships to the database for large codebases.

        Args:
            pending_calls: List of PendingCall objects to resolve.

        Returns:
            Number of successfully resolved calls.
        """
        total_calls = len(pending_calls)
        logger.info(f"Resolving {total_calls} pending calls sequentially...")

        # Pre-filter Python built-in calls to reduce work
        # Use cached built-ins for efficiency
        if CallProcessor._python_builtins_cache is None:
            CallProcessor._python_builtins_cache = StdlibChecker.get_python_builtins()
        filtered_calls = [
            pc
            for pc in pending_calls
            if pc.callee_name not in CallProcessor._python_builtins_cache
        ]
        skipped_builtin_count = total_calls - len(filtered_calls)

        if skipped_builtin_count > 0:
            logger.info(
                f"Filtered out {skipped_builtin_count} built-in function calls (len, print, type, etc.)"
            )

        # Pre-compute import mappings per module to avoid redundant lookups (O(n*m) -> O(n+m))
        # Many pending calls share the same module_qn, so this avoids repeated dict.get()
        import_mapping_cache: dict[str, dict[str, str]] = {}
        for pc in filtered_calls:
            if pc.module_qn not in import_mapping_cache:
                import_mapping_cache[pc.module_qn] = (
                    self.import_processor.import_mapping.get(pc.module_qn, {})
                )
        logger.info(
            f"Pre-computed import mappings for {len(import_mapping_cache)} modules"
        )

        resolved_count = 0
        for i, pending in enumerate(filtered_calls):
            callee_info = self._resolve_pending_call(
                pending, import_mapping=import_mapping_cache.get(pending.module_qn)
            )

            if callee_info:
                callee_type, callee_qn = callee_info
                self.ingestor.ensure_relationship_batch(
                    (pending.caller_type, "qualified_name", pending.caller_qn),
                    pending.relationship_type,
                    (callee_type, "qualified_name", callee_qn),
                )
                resolved_count += 1

            # Log progress periodically
            if (i + 1) % self._PROGRESS_LOG_INTERVAL == 0:
                logger.info(
                    f"Progress: {i + 1}/{len(filtered_calls)} calls processed "
                    f"({resolved_count} resolved, "
                    f"{100 * (i + 1) / len(filtered_calls):.1f}%)"
                )

        logger.info(
            f"Resolved {resolved_count}/{total_calls} calls (sequential) "
            f"({skipped_builtin_count} built-ins skipped)"
        )
        return resolved_count

    def _resolve_pending_calls_parallel(
        self,
        pending_calls: list[PendingCall],
        max_workers: int | None = None,
    ) -> int:
        """Resolve pending calls in parallel for improved performance.

        Optimizations:
        - Pre-computes import mappings per module to avoid redundant dict lookups
        - Batches calls into chunks to reduce Future object overhead
        - Thread-safe: All internal methods use appropriate locks.

        Args:
            pending_calls: List of PendingCall objects to resolve
            max_workers: Number of worker threads (default: 64)

        Returns:
            Number of successfully resolved calls
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        workers = max_workers or 64
        total_calls = len(pending_calls)
        logger.info(
            f"Resolving {total_calls} pending calls in parallel ({workers} workers)..."
        )

        resolved_count = 0
        processed_count = 0
        skipped_builtin_count = 0
        count_lock = threading.Lock()

        # Pre-filter Python built-in calls to reduce work
        # Use cached built-ins for efficiency
        if CallProcessor._python_builtins_cache is None:
            CallProcessor._python_builtins_cache = StdlibChecker.get_python_builtins()
        filtered_calls = [
            pc
            for pc in pending_calls
            if pc.callee_name not in CallProcessor._python_builtins_cache
        ]
        skipped_builtin_count = total_calls - len(filtered_calls)

        if skipped_builtin_count > 0:
            logger.info(
                f"Filtered out {skipped_builtin_count} built-in function calls (len, print, type, etc.)"
            )

        # Pre-compute import mappings per module to avoid redundant lookups (O(n*m) -> O(n+m))
        # Many pending calls share the same module_qn, so this avoids repeated dict.get()
        import_mapping_cache: dict[str, dict[str, str]] = {}
        for pc in filtered_calls:
            if pc.module_qn not in import_mapping_cache:
                import_mapping_cache[pc.module_qn] = (
                    self.import_processor.import_mapping.get(pc.module_qn, {})
                )
        logger.info(
            f"Pre-computed import mappings for {len(import_mapping_cache)} modules"
        )

        def resolve_batch(
            batch: list[PendingCall],
        ) -> list[tuple[PendingCall, tuple[str, str] | None]]:
            """Resolve a batch of pending calls. Reduces Future object overhead."""
            results = []
            for pc in batch:
                callee_info = self._resolve_pending_call(
                    pc, import_mapping=import_mapping_cache.get(pc.module_qn)
                )
                results.append((pc, callee_info))
            return results

        # Split filtered_calls into batches to reduce Future object overhead
        # For 100k+ calls, creating individual futures is expensive
        batch_size = self._PARALLEL_BATCH_SIZE
        batches = [
            filtered_calls[i : i + batch_size]
            for i in range(0, len(filtered_calls), batch_size)
        ]
        logger.info(
            f"Split {len(filtered_calls)} calls into {len(batches)} batches of ~{batch_size}"
        )

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(resolve_batch, batch) for batch in batches]

            for future in as_completed(futures):
                try:
                    batch_results = future.result()

                    for pending, callee_info in batch_results:
                        # CRITICAL: Add relationship to buffer FIRST, before any flush decision
                        # This ensures the relationship is included in the next flush
                        if callee_info:
                            callee_type, callee_qn = callee_info
                            # ensure_relationship_batch is already thread-safe
                            self.ingestor.ensure_relationship_batch(
                                (
                                    pending.caller_type,
                                    "qualified_name",
                                    pending.caller_qn,
                                ),
                                pending.relationship_type,
                                (callee_type, "qualified_name", callee_qn),
                            )

                    # Track progress (inside lock)
                    should_log = False
                    current_count = 0
                    current_resolved = 0
                    batch_resolved = sum(1 for _, ci in batch_results if ci is not None)
                    with count_lock:
                        processed_count += len(batch_results)
                        resolved_count += batch_resolved
                        current_count = processed_count
                        current_resolved = resolved_count

                        # Log progress periodically
                        if (
                            current_count // self._PROGRESS_LOG_INTERVAL
                            > (current_count - len(batch_results))
                            // self._PROGRESS_LOG_INTERVAL
                        ):
                            should_log = True

                    # Perform logging OUTSIDE the lock to avoid deadlock
                    if should_log:
                        logger.info(
                            f"Progress: {current_count}/{len(filtered_calls)} calls resolved "
                            f"({current_resolved} successful, "
                            f"{100 * current_count / len(filtered_calls):.1f}%)"
                        )

                except Exception as e:
                    logger.warning(f"Failed to resolve pending call batch: {e}")

        logger.info(
            f"Resolved {resolved_count}/{total_calls} calls (parallel) "
            f"({skipped_builtin_count} built-ins skipped)"
        )
        return resolved_count

    def _resolve_pending_calls_multiprocess(
        self,
        pending_calls: list[PendingCall],
        max_workers: int | None = None,
    ) -> int:
        """Resolve pending calls using multiprocessing for true CPU parallelism.

        This bypasses the GIL by using fork-based multiprocessing. Child processes
        inherit the parent's memory (copy-on-write) including the complete
        function_registry, import_mapping, and simple_name_lookup — no serialization
        needed. Only index ranges (two ints) are passed to workers, avoiding the
        ~41MB pickling overhead that sending PendingCall objects would incur.

        Side effects (external stub node creation, call tracking) are deferred
        to the main process after resolution completes.

        Args:
            pending_calls: List of PendingCall objects to resolve
            max_workers: Number of worker processes (default: min(16, cpu_count))

        Returns:
            Number of successfully resolved calls
        """
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor, as_completed

        workers = max_workers or min(16, multiprocessing.cpu_count() or 4)
        total_calls = len(pending_calls)
        logger.info(
            f"Resolving {total_calls} pending calls with multiprocessing ({workers} workers)..."
        )

        # Pre-filter Python built-in calls to reduce work
        if CallProcessor._python_builtins_cache is None:
            CallProcessor._python_builtins_cache = StdlibChecker.get_python_builtins()
        filtered_calls = [
            pc
            for pc in pending_calls
            if pc.callee_name not in CallProcessor._python_builtins_cache
        ]
        skipped_builtin_count = total_calls - len(filtered_calls)

        if skipped_builtin_count > 0:
            logger.info(
                f"Filtered out {skipped_builtin_count} built-in function calls (len, print, type, etc.)"
            )

        # Pre-compute import mappings per module
        import_mapping_cache: dict[str, dict[str, str]] = {}
        for pc in filtered_calls:
            if pc.module_qn not in import_mapping_cache:
                import_mapping_cache[pc.module_qn] = (
                    self.import_processor.import_mapping.get(pc.module_qn, {})
                )
        logger.info(
            f"Pre-computed import mappings for {len(import_mapping_cache)} modules"
        )

        # Enable suffix cache on function_registry before forking
        # This ensures the cache is populated for common lookups in child processes
        self.function_registry.enable_cache()

        # Set module-level globals that child processes will inherit via fork()
        # Using globals + fork avoids pickling large data structures
        global _mp_call_processor, _mp_import_mapping_cache, _mp_pending_calls
        _mp_call_processor = self
        _mp_import_mapping_cache = import_mapping_cache
        _mp_pending_calls = filtered_calls

        # Create index ranges for batches (only send two ints per batch, not PendingCall objects)
        batch_size = max(500, len(filtered_calls) // (workers * 4))
        index_ranges = [
            (i, min(i + batch_size, len(filtered_calls)))
            for i in range(0, len(filtered_calls), batch_size)
        ]
        logger.info(
            f"Split {len(filtered_calls)} calls into {len(index_ranges)} batches of ~{batch_size}"
        )

        resolved_count = 0
        batches_completed = 0
        external_stubs: list[
            tuple[str, str]
        ] = []  # (import_source, root_module) for deferred creation

        try:
            # Use 'fork' start method for copy-on-write memory sharing
            ctx = multiprocessing.get_context("fork")
            with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as executor:
                futures = {
                    executor.submit(_mp_resolve_batch_by_indices, idx_range): idx_range
                    for idx_range in index_ranges
                }

                for future in as_completed(futures):
                    try:
                        batch_results = future.result()

                        for (
                            caller_type,
                            caller_qn,
                            rel_type,
                            callee_type,
                            callee_qn,
                            ext_src,
                            ext_mod,
                        ) in batch_results:
                            # Add relationship in main process
                            self.ingestor.ensure_relationship_batch(
                                (caller_type, "qualified_name", caller_qn),
                                rel_type,
                                (callee_type, "qualified_name", callee_qn),
                            )
                            resolved_count += 1

                            # Collect external stubs for deferred creation
                            if ext_src and ext_mod:
                                external_stubs.append((ext_src, ext_mod))

                        batches_completed += 1

                        # Log progress periodically
                        calls_so_far = batches_completed * batch_size
                        if (
                            calls_so_far // self._PROGRESS_LOG_INTERVAL
                            > (calls_so_far - batch_size) // self._PROGRESS_LOG_INTERVAL
                        ):
                            logger.info(
                                f"Progress: ~{calls_so_far}/{len(filtered_calls)} calls processed "
                                f"({resolved_count} resolved, "
                                f"{100 * min(calls_so_far, len(filtered_calls)) / len(filtered_calls):.1f}%)"
                            )

                    except Exception as e:
                        logger.warning(f"Multiprocess batch failed: {e}")

        except Exception as e:
            logger.warning(
                f"Multiprocessing failed, falling back to thread-based parallel: {e}"
            )
            # Clean up globals
            _mp_call_processor = None
            _mp_import_mapping_cache = None
            _mp_pending_calls = None
            # Fallback to thread-based parallel resolution
            return self._resolve_pending_calls_parallel(pending_calls, max_workers)
        finally:
            # Clean up module-level globals
            _mp_call_processor = None
            _mp_import_mapping_cache = None
            _mp_pending_calls = None

        # Apply deferred side effects: create external stub nodes
        seen_stubs: set[str] = set()
        for import_source, root_module in external_stubs:
            if import_source not in seen_stubs:
                seen_stubs.add(import_source)
                self._create_external_stub_node(import_source, root_module)
            self._track_external_call(root_module)

        logger.info(
            f"Resolved {resolved_count}/{total_calls} calls (multiprocess, {workers} workers) "
            f"({skipped_builtin_count} built-ins skipped, {len(seen_stubs)} external stubs)"
        )
        return resolved_count

    def _resolve_pending_call(
        self, pending: PendingCall, import_mapping: dict[str, str] | None = None
    ) -> tuple[str, str] | None:
        """Resolve a single PendingCall to its target.

        Uses the stored context (local_var_types, import info) along with
        the now-complete function_registry to resolve the call.

        Args:
            pending: The PendingCall to resolve
            import_mapping: Pre-computed import mapping for this call's module.
                If None, will be looked up from import_processor (slower).
        """
        call_name = pending.callee_name
        module_qn = pending.module_qn
        if import_mapping is None:
            import_mapping = self.import_processor.import_mapping.get(module_qn, {})

        # Handle external calls with stored import info
        if pending.is_imported and pending.import_source:
            root_module = pending.import_source.split(".")[0]
            if self.local_module_filter.is_tracked_external(root_module):
                self._create_external_stub_node(pending.import_source, root_module)
                self._track_external_call(root_module)
                return ("Function", pending.import_source)

        # Use the standard resolution logic with pre-computed type info
        callee_info = self._resolve_function_call(
            call_name,
            module_qn,
            pending.local_var_types if pending.local_var_types else None,
            pending.class_context,
            import_mapping,
        )

        if callee_info:
            return callee_info

        # Try built-in resolution
        builtin_info = self._resolve_builtin_call(call_name)
        if builtin_info:
            return builtin_info

        # Try C++ operator resolution
        operator_info = self._resolve_cpp_operator_call(call_name, module_qn)
        if operator_info:
            return operator_info

        return None

    def _process_calls_in_functions(
        self, root_node: Node, module_qn: str, language: str, queries: dict[str, Any]
    ) -> None:
        """Process calls within top-level functions."""
        lang_queries = queries[language]
        lang_config: LanguageConfig = lang_queries["config"]

        query = lang_queries["functions"]
        cursor = QueryCursor(query)
        captures = cursor.captures(root_node)
        func_nodes = captures.get("function", [])
        for func_node in func_nodes:
            if not isinstance(func_node, Node):
                continue
            if self._is_method(func_node, lang_config):
                continue

            # Extract function name using appropriate method for language
            if language == "cpp":
                # For C++, use utility functions instead of creating a temporary instance
                func_name = extract_cpp_function_name(func_node)
                if not func_name:
                    continue
            else:
                name_node = func_node.child_by_field_name("name")
                if not name_node:
                    continue
                text = name_node.text
                if text is None:
                    continue
                func_name = text.decode("utf8")
            func_qn = self._build_nested_qualified_name(
                func_node, module_qn, func_name, lang_config
            )

            if func_qn:
                self._ingest_function_calls(
                    func_node, func_qn, "Function", module_qn, language, queries
                )
                # OC4: Process decorator calls (non-parenthesized)
                if language == "python":
                    self._ingest_decorator_calls(
                        func_node, func_qn, "Function", module_qn
                    )

    def _process_calls_in_classes(
        self, root_node: Node, module_qn: str, language: str, queries: dict[str, Any]
    ) -> None:
        """Process calls within class methods."""
        lang_queries = queries[language]
        if not lang_queries.get("classes"):
            return

        query = lang_queries["classes"]
        cursor = QueryCursor(query)
        captures = cursor.captures(root_node)
        class_nodes = captures.get("class", [])

        for class_node in class_nodes:
            if not isinstance(class_node, Node):
                continue

            # Rust impl blocks don't have a "name" field, they have a "type" field
            if language == "rust" and class_node.type == "impl_item":
                # For Rust impl blocks, get the type being implemented
                type_node = class_node.child_by_field_name("type")
                if not type_node:
                    # Might be a type_identifier child directly
                    for child in class_node.children:
                        if child.type == "type_identifier" and child.is_named:
                            type_node = child
                            break
                if not type_node or not type_node.text:
                    continue
                class_name = type_node.text.decode("utf8")
                class_qn = f"{module_qn}.{class_name}"
            else:
                # Standard class handling for other languages
                name_node = class_node.child_by_field_name("name")
                if not name_node:
                    continue
                text = name_node.text
                if text is None:
                    continue
                class_name = text.decode("utf8")
                class_qn = f"{module_qn}.{class_name}"

            # OC4: Process decorator calls for class definitions
            if language == "python":
                self._ingest_decorator_calls(class_node, class_qn, "Class", module_qn)

            body_node = class_node.child_by_field_name("body")
            if not body_node:
                continue

            method_query = lang_queries["functions"]
            method_cursor = QueryCursor(method_query)
            method_captures = method_cursor.captures(body_node)
            method_nodes = method_captures.get("function", [])
            for method_node in method_nodes:
                if not isinstance(method_node, Node):
                    continue
                method_name_node = method_node.child_by_field_name("name")
                if not method_name_node:
                    continue
                text = method_name_node.text
                if text is None:
                    continue
                method_name = text.decode("utf8")
                method_qn = f"{class_qn}.{method_name}"

                self._ingest_function_calls(
                    method_node,
                    method_qn,
                    "Method",
                    module_qn,
                    language,
                    queries,
                    class_qn,
                )
                # OC4: Process decorator calls for methods
                if language == "python":
                    self._ingest_decorator_calls(
                        method_node, method_qn, "Method", module_qn
                    )

    def _process_module_level_calls(
        self, root_node: Node, module_qn: str, language: str, queries: dict[str, Any]
    ) -> None:
        """Process top-level calls in the file (like IIFE calls)."""
        # Process calls that are directly at file level, not inside functions/classes
        self._ingest_function_calls(
            root_node, module_qn, "File", module_qn, language, queries
        )

    def _get_call_target_name(self, call_node: Node) -> str | None:
        """Extract the target name from a call expression AST node.

        Handles multiple call patterns: simple calls (foo()), member access
        (obj.method()), C++ scope resolution (ns::func()), Lua method calls
        (obj:method()), and JavaScript IIFE patterns.

        Args:
            call_node: A Tree-sitter Node representing a call expression.

        Returns:
            The extracted call name string, or None if the node cannot be parsed.
        """
        # For 'call' in Python and 'call_expression' in JS/TS/C++
        if func_child := call_node.child_by_field_name("function"):
            if func_child.type == "identifier":
                text = func_child.text
                if text is not None:
                    return str(text.decode("utf8"))
            # Python: obj.method() -> attribute
            elif func_child.type == "attribute":
                # Return the full attribute path
                text = func_child.text
                if text is not None:
                    return str(text.decode("utf8"))
            # Python: kernel[grid](args) -> subscript (Triton kernel calls)
            # This handles cases like: kernel_name[grid_dims](kernel_args)
            elif func_child.type == "subscript":
                # Extract the value part (the actual callable)
                value_node = func_child.child_by_field_name("value")
                if value_node and value_node.text:
                    return str(value_node.text.decode("utf8"))
            # JS/TS: obj.method() -> member_expression
            elif func_child.type == "member_expression":
                # Return the full member expression (e.g., "obj.method")
                text = func_child.text
                if text is not None:
                    return str(text.decode("utf8"))
            # C++: obj.method() -> field_expression
            elif func_child.type == "field_expression":
                # Extract method name from field_expression
                field_node = func_child.child_by_field_name("field")
                if field_node and field_node.text:
                    return str(field_node.text.decode("utf8"))
            # C++: namespace::func() or Class::method() -> qualified_identifier
            elif func_child.type == "qualified_identifier":
                # Return the full qualified name (e.g., "std::cout", "Storage::getInstance")
                # Keep the :: separator for proper resolution in Phase 1
                text = func_child.text
                if text is not None:
                    qname = str(text.decode("utf8"))
                    # Only normalize operator names (e.g., "mlir::triton::operator==" -> "mlir::triton::operator_equal")
                    # Keep everything else unchanged to preserve C++ namespace resolution
                    if "operator" in qname:
                        # Split by :: to handle namespace and operator separately
                        parts = qname.split("::")
                        # The last part should be the operator
                        if parts[-1].startswith("operator"):
                            operator_symbol = parts[-1][8:]  # Remove "operator" prefix
                            normalized_operator = convert_operator_symbol_to_name(
                                operator_symbol
                            )
                            parts[-1] = normalized_operator
                        qname = "::".join(parts)  # Keep :: separator
                    return qname
            # Rust: Type::method() or module::func() -> scoped_identifier
            elif func_child.type == "scoped_identifier":
                # Return the full scoped name (e.g., "Storage::get_instance")
                text = func_child.text
                if text is not None:
                    return str(text.decode("utf8"))
            # C++/JS: Parenthesized expressions
            # C++: Function pointer calls like (*func_ptr)(args)
            # JS/TS: IIFE calls like (function(){})()
            elif func_child.type == "parenthesized_expression":
                # First check for C++ function pointer dereference: (*func_ptr)(args)
                for child in func_child.children:
                    if child.type == "pointer_expression":
                        # Dereferenced function pointer
                        arg_node = child.child_by_field_name("argument")
                        if arg_node:
                            if arg_node.type == "identifier" and arg_node.text:
                                return str(arg_node.text.decode("utf8"))
                            # Could be a more complex expression like (*obj.func_ptr)
                            elif arg_node.text:
                                return str(arg_node.text.decode("utf8"))
                    elif child.type == "identifier" and child.text:
                        # Direct identifier in parentheses (less common but valid)
                        return str(child.text.decode("utf8"))

                # If not a function pointer, try IIFE (JavaScript)
                return self._get_iife_target_name(func_child)

        # C++: Binary operators like obj1 + obj2 -> operator+
        if call_node.type == "binary_expression":
            # Use Tree-sitter field access to get the operator directly
            operator_node = call_node.child_by_field_name("operator")
            if operator_node and operator_node.text:
                operator_text = operator_node.text.decode("utf8")
                return convert_operator_symbol_to_name(operator_text)

        # C++: Unary operators like ++obj, --obj -> operator++, operator--
        if call_node.type in ["unary_expression", "update_expression"]:
            # Use Tree-sitter field access to get the operator directly
            operator_node = call_node.child_by_field_name("operator")
            if operator_node and operator_node.text:
                operator_text = operator_node.text.decode("utf8")
                return convert_operator_symbol_to_name(operator_text)

        # For 'method_invocation' in Java
        if call_node.type == "method_invocation":
            # Get the object (receiver) part
            object_node = call_node.child_by_field_name("object")
            name_node = call_node.child_by_field_name("name")

            if name_node and name_node.text:
                method_name = str(name_node.text.decode("utf8"))

                if object_node and object_node.text:
                    object_text = str(object_node.text.decode("utf8"))
                    return f"{object_text}.{method_name}"
                else:
                    # No object, likely this.method() or static method
                    return method_name

        # General case for other languages
        if name_node := call_node.child_by_field_name("name"):
            text = name_node.text
            if text is not None:
                return str(text.decode("utf8"))

        return None

    def _get_iife_target_name(self, parenthesized_expr: Node) -> str | None:
        """Extract the target name for IIFE calls like (function(){})()."""
        # Look for function_expression or arrow_function inside parentheses
        for child in parenthesized_expr.children:
            if child.type in ["function_expression", "arrow_function"]:
                # Generate the same synthetic name that was used during function detection
                if child.type == "arrow_function":
                    return f"iife_arrow_{child.start_point[0]}_{child.start_point[1]}"
                else:
                    return f"iife_func_{child.start_point[0]}_{child.start_point[1]}"
        return None

    def _is_potential_binding_call(self, call_name: str) -> bool:
        """Check if a call might target a pybind11 binding."""
        if "." not in call_name:
            return False

        # Extract method name
        method_name = call_name.split(".")[-1]

        # Check if any function in registry ends with this name and is a binding
        candidates = self.simple_name_lookup.get(method_name, set()).copy()
        return any("<binding>" in c for c in candidates)

    def _ingest_decorator_calls(
        self,
        decorated_node: Node,
        caller_qn: str,
        caller_type: str,
        module_qn: str,
    ) -> None:
        """Process non-parenthesized decorator calls and create CALLS relationships.

        OC4: Direct processing path for decorators without parentheses.
        """
        parent = decorated_node.parent
        if not parent or parent.type != "decorated_definition":
            return

        import_mapping = self.import_processor.import_mapping.get(module_qn, {})

        for child in parent.children:
            if child.type != "decorator":
                continue

            # Check if this decorator has a call node (already captured)
            has_call = False
            decorator_name = None
            for deco_child in child.children:
                if deco_child.type == "call":
                    has_call = True
                    break
                elif deco_child.type == "identifier" and deco_child.text:
                    decorator_name = deco_child.text.decode("utf8")
                elif deco_child.type == "attribute" and deco_child.text:
                    decorator_name = deco_child.text.decode("utf8")

            if has_call or not decorator_name:
                continue

            # Skip Python builtins
            if decorator_name in (
                "staticmethod",
                "classmethod",
                "property",
                "abstractmethod",
            ):
                continue

            should_process = self.local_module_filter.should_process_call(
                decorator_name, module_qn, import_mapping
            )
            if not should_process:
                continue

            callee_info = self._resolve_function_call(
                decorator_name, module_qn, None, None, import_mapping
            )
            if not callee_info:
                continue

            callee_type, callee_qn = callee_info
            self.ingestor.ensure_relationship_batch(
                (caller_type, "qualified_name", caller_qn),
                "CALLS",
                (callee_type, "qualified_name", callee_qn),
            )

            if self.callers_index is not None and callee_qn:
                self._write_call_to_index(caller_qn, callee_qn)

    def _ingest_function_calls(
        self,
        caller_node: Node,
        caller_qn: str,
        caller_type: str,
        module_qn: str,
        language: str,
        queries: dict[str, Any],
        class_context: str | None = None,
    ) -> None:
        """Find and ingest function calls within a caller node."""
        calls_query = queries[language].get("calls")
        if not calls_query:
            return

        # Don't build local_var_types eagerly - it's expensive!
        # We'll build it lazily only if needed during call resolution
        local_var_types: dict[str, str] | None = None

        cursor = QueryCursor(calls_query)
        captures = cursor.captures(caller_node)
        call_nodes = captures.get("call", [])

        # Cache import mapping lookup (avoid repeated dict lookups)
        import_mapping = self.import_processor.import_mapping.get(module_qn, {})

        for call_node in call_nodes:
            if not isinstance(call_node, Node):
                continue

            # Process nested calls first (inner to outer)
            # Pass local_var_types by reference so it can be lazily initialized
            self._process_nested_calls_in_node(
                call_node,
                caller_qn,
                caller_type,
                module_qn,
                local_var_types,
                class_context,
            )

            call_name = self._get_call_target_name(call_node)
            if not call_name:
                continue

            # Check if this is a tracked external dependency call
            is_external_call = False
            external_module = None

            if call_name in import_mapping:
                imported_qn = import_mapping[call_name]
                root_module = imported_qn.split(".")[0]

                if self.local_module_filter.is_tracked_external(root_module):
                    is_external_call = True
                    external_module = root_module

            # Apply local module filtering to skip external library calls
            # But allow potential binding calls and tracked externals to pass through
            should_process = self.local_module_filter.should_process_call(
                call_name, module_qn, import_mapping, language
            )
            if (
                not should_process
                and not self._is_potential_binding_call(call_name)
                and not is_external_call
            ):
                continue

            # Lazy initialization: only build local_var_types if we need it
            # Check if this call might need type inference (has dots, not in imports)
            needs_type_inference = (
                "." in call_name
                and call_name.split(".")[0] not in import_mapping
                and not call_name.startswith("self.")
            )

            if needs_type_inference and local_var_types is None:
                # Now we actually need type inference, build it
                local_var_types = self.type_inference.build_local_variable_type_map(
                    caller_node, module_qn, language
                )

            # Use Java-specific resolution for Java method calls
            if language == "java" and call_node.type == "method_invocation":
                callee_info = self._resolve_java_method_call(
                    call_node, module_qn, local_var_types
                )
            else:
                callee_info = self._resolve_function_call(
                    call_name, module_qn, local_var_types, class_context, import_mapping
                )

            # Handle external dependency calls that couldn't be resolved locally
            if not callee_info and is_external_call:
                callee_qn = import_mapping[call_name]

                # Create stub node for external function
                self._create_external_stub_node(callee_qn, external_module)

                callee_info = ("Function", callee_qn)

                # Track for project metadata
                self._track_external_call(external_module)

            if not callee_info:
                # Check if it's a built-in JavaScript method
                builtin_info = self._resolve_builtin_call(call_name)
                if not builtin_info:
                    # Check if it's a C++ operator
                    operator_info = self._resolve_cpp_operator_call(
                        call_name, module_qn
                    )
                    if not operator_info:
                        continue
                    callee_type, callee_qn = operator_info
                else:
                    callee_type, callee_qn = builtin_info
            else:
                callee_type, callee_qn = callee_info

            # NOTE: We don't call ensure_node_batch here because all Function/Method/Class
            # nodes are already created in Pass 2 (definition processing) before we reach
            # Pass 3 (call processing). Re-creating nodes here would overwrite their
            # properties (decorators, line numbers, docstrings, etc.) with minimal data.
            self.ingestor.ensure_relationship_batch(
                (caller_type, "qualified_name", caller_qn),
                "CALLS",
                (callee_type, "qualified_name", callee_qn),
            )

            # Write to reverse call index (for incremental updates)
            # Use thread-safe write for parallel processing
            if self.callers_index is not None and callee_qn:
                self._write_call_to_index(caller_qn, callee_qn)

    def _process_nested_calls_in_node(
        self,
        call_node: Node,
        caller_qn: str,
        caller_type: str,
        module_qn: str,
        local_var_types: dict[str, str] | None,
        class_context: str | None,
    ) -> None:
        """Process nested call expressions within a call node's function expression."""
        # Get the function expression of this call
        func_child = call_node.child_by_field_name("function")
        if not func_child:
            return

        # If the function is an attribute (e.g., obj.method), check if obj contains calls
        if func_child.type == "attribute":
            # Recursively search for nested calls in the object part
            self._find_and_process_nested_calls(
                func_child,
                caller_qn,
                caller_type,
                module_qn,
                local_var_types,
                class_context,
            )

    def _find_and_process_nested_calls(
        self,
        node: Node,
        caller_qn: str,
        caller_type: str,
        module_qn: str,
        local_var_types: dict[str, str] | None,
        class_context: str | None,
    ) -> None:
        """Recursively find and process call expressions in a node tree."""
        # If this node is a call expression, process it
        if node.type == "call":
            # First process any nested calls within this call
            self._process_nested_calls_in_node(
                node, caller_qn, caller_type, module_qn, local_var_types, class_context
            )

            # Then process this call itself
            call_name = self._get_call_target_name(node)
            if call_name:
                callee_info = self._resolve_function_call(
                    call_name, module_qn, local_var_types, class_context
                )
                if callee_info:
                    callee_type, callee_qn = callee_info
                    logger.debug(
                        f"      Found nested call from {caller_qn} to {call_name} "
                        f"(resolved as {callee_type}:{callee_qn})"
                    )
                    # NOTE: We don't call ensure_node_batch here - nodes already exist from Pass 2
                    self.ingestor.ensure_relationship_batch(
                        (caller_type, "qualified_name", caller_qn),
                        "CALLS",
                        (callee_type, "qualified_name", callee_qn),
                    )

                    # Write to reverse call index
                    if self.callers_index is not None and callee_qn:
                        self._write_call_to_index(caller_qn, callee_qn)

        # Recursively search in all child nodes
        for child in node.children:
            self._find_and_process_nested_calls(
                child, caller_qn, caller_type, module_qn, local_var_types, class_context
            )

    def _resolve_function_call(
        self,
        call_name: str,
        module_qn: str,
        local_var_types: dict[str, str] | None = None,
        class_context: str | None = None,
        import_mapping: dict[str, str] | None = None,
    ) -> tuple[str, str] | None:
        """Resolve a function call to its qualified name and type.

        Dispatches through a series of resolution strategies in priority order:
        1. Early filtering and binding resolution
        2. Special call patterns (IIFE, super, method chaining)
        3. Import-based resolution (direct imports, qualified calls, wildcards)
        4. Same-module heuristic resolution
        5. Trie-based fallback resolution

        Args:
            call_name: The call expression name (e.g., "foo", "obj.method", "Class::func").
            module_qn: Qualified name of the module containing this call.
            local_var_types: Mapping of local variable names to their inferred types.
            class_context: Qualified name of the enclosing class, if any.
            import_mapping: Pre-fetched import mapping for the module (fetched if None).

        Returns:
            A (type, qualified_name) tuple if resolved, or None if unresolvable.
        """
        # Use provided import_mapping or fetch it (for backward compatibility)
        if import_mapping is None:
            import_mapping = self.import_processor.import_mapping.get(module_qn, {})

        # Early filtering: Skip external library calls
        # But allow potential binding calls to pass through
        should_process = self.local_module_filter.should_process_call(
            call_name, module_qn, import_mapping
        )
        if not should_process and not self._is_potential_binding_call(call_name):
            return None

        # Special handling for bindings that appear as external calls
        if (
            not should_process
        ):  # It's external, but we continued because it's a potential binding
            method_name = call_name.split(".")[-1]
            candidates = self.simple_name_lookup.get(method_name, set()).copy()
            binding_candidates = [c for c in candidates if "<binding>" in c]

            if binding_candidates:
                binding_candidates.sort()
                best_candidate = binding_candidates[0]
                logger.debug(
                    f"Resolved external-looking call to binding: {call_name} -> {best_candidate}"
                )
                return (self.function_registry[best_candidate], best_candidate)

        # Phase -1: Handle IIFE calls specially
        if call_name and (
            call_name.startswith("iife_func_") or call_name.startswith("iife_arrow_")
        ):
            iife_qn = f"{module_qn}.{call_name}"
            if iife_qn in self.function_registry:
                return self.function_registry[iife_qn], iife_qn

        # Phase 0: Handle super calls specially (JavaScript/TypeScript patterns)
        if (
            call_name == "super"
            or call_name.startswith("super.")
            or call_name.startswith("super()")
        ):
            return self._resolve_super_call(call_name, module_qn, class_context)

        # Phase 0.5: Handle method chaining
        if "." in call_name and self._is_method_chain(call_name):
            return self._resolve_chained_call(call_name, module_qn, local_var_types)

        # Phase 1: Import-based resolution (high accuracy)
        result = self._resolve_import_based_call(
            call_name, module_qn, import_mapping, local_var_types, class_context
        )
        if result is not None:
            return result

        # Phase 2: Same-module heuristic resolution
        result = self._resolve_same_module_call(call_name, module_qn)
        if result is not None:
            return result

        # Phase 3: Trie-based fallback resolution
        result = self._resolve_by_trie_fallback(call_name, module_qn)
        if result is not None:
            return result

        logger.debug(f"Could not resolve call: {call_name}")
        return None

    def _resolve_import_based_call(
        self,
        call_name: str,
        module_qn: str,
        import_map: dict[str, str],
        local_var_types: dict[str, str] | None,
        class_context: str | None,
    ) -> tuple[str, str] | None:
        """Resolve a call using import mappings (Phase 1).

        Tries direct import match, qualified method calls (obj.method, Class::func),
        and wildcard imports in order.

        Args:
            call_name: The call expression name.
            module_qn: Qualified name of the module containing this call.
            import_map: Import mapping for the current module.
            local_var_types: Mapping of local variable names to their inferred types.
            class_context: Qualified name of the enclosing class, if any.

        Returns:
            A (type, qualified_name) tuple if resolved, or None.
        """
        if not import_map:
            return None

        # 1a.1. Direct import resolution
        result = self._resolve_direct_import(call_name, import_map)
        if result is not None:
            return result

        # 1a.2. Qualified calls: "Class.method", "self.attr.method", "Class::method", "object:method"
        if "." in call_name or "::" in call_name or ":" in call_name:
            result = self._resolve_qualified_method_call(
                call_name, module_qn, import_map, local_var_types, class_context
            )
            if result is not None:
                return result

        # 1b. Wildcard imports
        return self._resolve_wildcard_import(call_name, import_map)

    def _resolve_direct_import(
        self,
        call_name: str,
        import_map: dict[str, str],
    ) -> tuple[str, str] | None:
        """Resolve a call via direct import name match.

        Checks if call_name appears directly in the import mapping and resolves
        to the imported qualified name. Falls back to __init__.py re-export chains
        if the direct import target is not in the function registry.

        Args:
            call_name: The call expression name.
            import_map: Import mapping for the current module.

        Returns:
            A (type, qualified_name) tuple if resolved, or None.
        """
        if call_name not in import_map:
            return None

        imported_qn = import_map[call_name]
        if imported_qn in self.function_registry:
            # Only log if debug level is enabled
            if logger._core.min_level <= 10:
                logger.debug(f"Direct import resolved: {call_name} -> {imported_qn}")
            return self.function_registry[imported_qn], imported_qn

        # OC4: Follow __init__.py re-export chains
        return self._resolve_init_reexport(imported_qn)

    def _resolve_qualified_method_call(
        self,
        call_name: str,
        module_qn: str,
        import_map: dict[str, str],
        local_var_types: dict[str, str] | None,
        class_context: str | None,
    ) -> tuple[str, str] | None:
        """Resolve qualified calls like obj.method(), Class::func(), or object:method().

        Handles two-part calls (obj.method), self-attribute chains (self.repo.find),
        and multi-part class method calls (Class.method with >2 parts).

        Args:
            call_name: The qualified call expression (contains '.', '::', or ':').
            module_qn: Qualified name of the module containing this call.
            import_map: Import mapping for the current module.
            local_var_types: Mapping of local variable names to their inferred types.
            class_context: Qualified name of the enclosing class, if any.

        Returns:
            A (type, qualified_name) tuple if resolved, or None.
        """
        # Determine the separator for this language
        if "::" in call_name:
            separator = "::"
        elif ":" in call_name:
            separator = ":"
        else:
            separator = "."
        parts = call_name.split(separator)

        # Two-part calls: "obj.method" or "Class::func"
        if len(parts) == 2:
            result = self._resolve_two_part_method_call(
                call_name,
                parts,
                separator,
                module_qn,
                import_map,
                local_var_types,
                class_context,
            )
            if result is not None:
                return result

        # Self-attribute chains: "self.repo.find_by_id"
        if len(parts) >= 3 and parts[0] == "self":
            return self._resolve_self_attribute_call(
                call_name, parts, module_qn, import_map, local_var_types
            )

        # Multi-part class method: "Class.submodule.method" (3+ parts, not self)
        if len(parts) >= 3:
            return self._resolve_multipart_class_call(
                call_name, parts, module_qn, import_map, local_var_types
            )

        return None

    def _resolve_two_part_method_call(
        self,
        call_name: str,
        parts: list[str],
        separator: str,
        module_qn: str,
        import_map: dict[str, str],
        local_var_types: dict[str, str] | None,
        class_context: str | None,
    ) -> tuple[str, str] | None:
        """Resolve two-part qualified calls like 'obj.method' or 'Class::func'.

        Resolution order:
        1. Type-inferred method resolution (local variable type -> class method)
        2. self/cls method resolution via class_context + MRO
        3. Imported class static/instance method resolution
        4. Same-module method fallback

        Args:
            call_name: The original call expression.
            parts: The call split into [object_name, method_name].
            separator: The separator used ('.', '::', or ':').
            module_qn: Qualified name of the module containing this call.
            import_map: Import mapping for the current module.
            local_var_types: Mapping of local variable names to their inferred types.
            class_context: Qualified name of the enclosing class, if any.

        Returns:
            A (type, qualified_name) tuple if resolved, or None.
        """
        object_name, method_name = parts

        # 1. Type-inferred method resolution
        if local_var_types and object_name in local_var_types:
            var_type = local_var_types[object_name]

            class_qn = self._resolve_type_to_qualified_name(
                var_type, import_map, module_qn
            )

            if class_qn:
                # For C++/Rust (:: separator), use . for registry lookup
                # For Lua (: separator), keep : for registry lookup
                registry_sep = separator if separator == ":" else "."
                method_qn = f"{class_qn}{registry_sep}{method_name}"
                if method_qn in self.function_registry:
                    logger.debug(
                        f"Type-inferred object method resolved: "
                        f"{call_name} -> {method_qn} "
                        f"(via {object_name}:{var_type})"
                    )
                    return self.function_registry[method_qn], method_qn

                # Check inheritance for this method
                inherited_method = self._resolve_inherited_method(class_qn, method_name)
                if inherited_method:
                    logger.debug(
                        f"Type-inferred inherited object method resolved: "
                        f"{call_name} -> {inherited_method[1]} "
                        f"(via {object_name}:{var_type})"
                    )
                    return inherited_method

            # Check if this is a built-in JavaScript type
            if var_type in self._JS_BUILTIN_TYPES:
                return (
                    "Function",
                    f"builtin.{var_type}.prototype.{method_name}",
                )

        # 2. self/cls method resolution using class_context + MRO
        if object_name in ("self", "cls") and class_context:
            method_qn = f"{class_context}.{method_name}"
            if method_qn in self.function_registry:
                logger.debug(
                    f"Self/cls method resolved via class_context: "
                    f"{call_name} -> {method_qn}"
                )
                return self.function_registry[method_qn], method_qn

            # Walk the MRO chain for inherited methods
            inherited_method = self._resolve_inherited_method(
                class_context, method_name
            )
            if inherited_method:
                logger.debug(
                    f"Self/cls inherited method resolved: "
                    f"{call_name} -> {inherited_method[1]} "
                    f"(class_context={class_context})"
                )
                return inherited_method

        # 3. Imported class static/instance method resolution
        if object_name in import_map:
            result = self._resolve_imported_class_method(
                call_name, object_name, method_name, separator, import_map
            )
            if result is not None:
                return result

        # 4. Same-module method fallback
        method_qn = f"{module_qn}.{method_name}"
        if method_qn in self.function_registry:
            logger.debug(f"Object method resolved: {call_name} -> {method_qn}")
            return self.function_registry[method_qn], method_qn

        return None

    def _resolve_imported_class_method(
        self,
        call_name: str,
        object_name: str,
        method_name: str,
        separator: str,
        import_map: dict[str, str],
    ) -> tuple[str, str] | None:
        """Resolve a method call on an imported class (e.g., Storage::getInstance).

        Handles Rust :: imports, JavaScript/Lua module-as-class patterns,
        and __init__.py re-export chains.

        Args:
            call_name: The original call expression.
            object_name: The object/class part of the call.
            method_name: The method part of the call.
            separator: The separator used ('.', '::', or ':').
            import_map: Import mapping for the current module.

        Returns:
            A (type, qualified_name) tuple if resolved, or None.
        """
        class_qn = import_map[object_name]

        # For Rust, imports use :: separators (e.g., "controllers::SceneController")
        # Convert to project-qualified names (e.g., "rust_proj.src.controllers.SceneController")
        if "::" in class_qn:
            rust_parts = class_qn.split("::")
            class_name = rust_parts[-1]

            matching_qns = list(self.simple_name_lookup.get(class_name, set()))
            for qn in matching_qns:
                if self.function_registry.get(qn) == "Class":
                    class_qn = qn
                    break

        # For JavaScript/Lua, imports may point to modules but the class/table
        # has the same name inside: e.g., storage.Storage -> storage.Storage.Storage
        potential_class_qn = f"{class_qn}.{object_name}"
        test_method_qn = f"{potential_class_qn}{separator}{method_name}"
        if test_method_qn in self.function_registry:
            class_qn = potential_class_qn

        # Construct method QN: Lua uses :, C++/Rust/others use .
        registry_separator = separator if separator == ":" else "."
        method_qn = f"{class_qn}{registry_separator}{method_name}"
        if method_qn in self.function_registry:
            logger.debug(f"Import-resolved static call: {call_name} -> {method_qn}")
            return self.function_registry[method_qn], method_qn

        # OC4: If class_qn is a re-exported class, resolve through __init__.py
        if class_qn not in self.function_registry:
            reexport_result = self._resolve_init_reexport(class_qn)
            if reexport_result:
                resolved_class_qn = reexport_result[1]
                method_qn = f"{resolved_class_qn}{registry_separator}{method_name}"
                if method_qn in self.function_registry:
                    logger.debug(
                        f"Re-export resolved static call: {call_name} -> {method_qn}"
                    )
                    return self.function_registry[method_qn], method_qn

        return None

    def _resolve_self_attribute_call(
        self,
        call_name: str,
        parts: list[str],
        module_qn: str,
        import_map: dict[str, str],
        local_var_types: dict[str, str] | None,
    ) -> tuple[str, str] | None:
        """Resolve self.attribute.method() patterns via type inference.

        For calls like self.repo.find_by_id(), resolves the type of self.repo
        and looks up find_by_id on that type's class.

        Args:
            call_name: The original call expression (e.g., "self.repo.find_by_id").
            parts: The call split into parts (e.g., ["self", "repo", "find_by_id"]).
            module_qn: Qualified name of the module containing this call.
            import_map: Import mapping for the current module.
            local_var_types: Mapping of local variable names to their inferred types.

        Returns:
            A (type, qualified_name) tuple if resolved, or None.
        """
        attribute_ref = ".".join(parts[:-1])  # "self.repo"
        method_name = parts[-1]  # "find_by_id"

        if not (local_var_types and attribute_ref in local_var_types):
            return None

        var_type = local_var_types[attribute_ref]
        class_qn = self._resolve_type_to_qualified_name(var_type, import_map, module_qn)

        if not class_qn:
            return None

        # For self.attribute.method, the method separator is always .
        method_qn = f"{class_qn}.{method_name}"
        if method_qn in self.function_registry:
            logger.debug(
                f"Instance-resolved self-attribute call: "
                f"{call_name} -> {method_qn} "
                f"(via {attribute_ref}:{var_type})"
            )
            return self.function_registry[method_qn], method_qn

        # Check inheritance for this method
        inherited_method = self._resolve_inherited_method(class_qn, method_name)
        if inherited_method:
            logger.debug(
                f"Instance-resolved inherited self-attribute call: "
                f"{call_name} -> {inherited_method[1]} "
                f"(via {attribute_ref}:{var_type})"
            )
            return inherited_method

        return None

    def _resolve_multipart_class_call(
        self,
        call_name: str,
        parts: list[str],
        module_qn: str,
        import_map: dict[str, str],
        local_var_types: dict[str, str] | None,
    ) -> tuple[str, str] | None:
        """Resolve multi-part qualified calls like Class.submodule.method (3+ parts, not self).

        Resolution order:
        1. Check if the base class is imported and resolve via import + inheritance
        2. Check if the base class is defined in the same module
        3. Fall back to type inference on the base variable

        Args:
            call_name: The original call expression.
            parts: The call split into 3+ parts.
            module_qn: Qualified name of the module containing this call.
            import_map: Import mapping for the current module.
            local_var_types: Mapping of local variable names to their inferred types.

        Returns:
            A (type, qualified_name) tuple if resolved, or None.
        """
        class_name = parts[0]
        method_name = ".".join(parts[1:])

        # 1. Check if the class is imported
        if class_name in import_map:
            class_qn = import_map[class_name]
            method_qn = f"{class_qn}.{method_name}"
            if method_qn in self.function_registry:
                logger.debug(
                    f"Import-resolved qualified call: {call_name} -> {method_qn}"
                )
                return self.function_registry[method_qn], method_qn

            # OC4: Check inheritance for imported class (classmethod/staticmethod)
            inherited_method = self._resolve_inherited_method(class_qn, method_name)
            if inherited_method:
                logger.debug(
                    f"Import-resolved inherited class method: "
                    f"{call_name} -> {inherited_method[1]}"
                )
                return inherited_method

        # 2. Try same-module class for classmethod/staticmethod calls
        same_module_class_qn = f"{module_qn}.{class_name}"
        if same_module_class_qn in self.function_registry:
            reg_type = self.function_registry[same_module_class_qn]
            if reg_type == "Class":
                method_qn = f"{same_module_class_qn}.{method_name}"
                if method_qn in self.function_registry:
                    logger.debug(
                        f"Same-module class method resolved: {call_name} -> {method_qn}"
                    )
                    return self.function_registry[method_qn], method_qn

                # Check inheritance chain
                inherited_method = self._resolve_inherited_method(
                    same_module_class_qn, method_name
                )
                if inherited_method:
                    logger.debug(
                        f"Same-module inherited class method: "
                        f"{call_name} -> {inherited_method[1]}"
                    )
                    return inherited_method

        # 3. Fall back to type inference on the base variable
        if local_var_types and class_name in local_var_types:
            var_type = local_var_types[class_name]
            class_qn = self._resolve_type_to_qualified_name(
                var_type, import_map, module_qn
            )

            if class_qn:
                method_qn = f"{class_qn}.{method_name}"
                if method_qn in self.function_registry:
                    logger.debug(
                        f"Instance-resolved qualified call: "
                        f"{call_name} -> {method_qn} "
                        f"(via {class_name}:{var_type})"
                    )
                    return self.function_registry[method_qn], method_qn

                # Check inheritance chain
                inherited_method = self._resolve_inherited_method(class_qn, method_name)
                if inherited_method:
                    logger.debug(
                        f"Instance-resolved inherited call: "
                        f"{call_name} -> {inherited_method[1]} "
                        f"(via {class_name}:{var_type})"
                    )
                    return inherited_method

        return None

    def _resolve_type_to_qualified_name(
        self,
        var_type: str,
        import_map: dict[str, str],
        module_qn: str,
    ) -> str:
        """Resolve a type name to its fully qualified name.

        Checks in order: already-qualified names (containing dots),
        import mapping, and same-module class resolution.

        Args:
            var_type: The type name to resolve (e.g., "MyClass" or "pkg.MyClass").
            import_map: Import mapping for the current module.
            module_qn: Qualified name of the module for same-module resolution.

        Returns:
            The fully qualified name, or empty string if unresolvable.
        """
        if "." in var_type:
            return var_type
        if var_type in import_map:
            return import_map[var_type]
        resolved = self._resolve_class_name(var_type, module_qn)
        return resolved if resolved else ""

    def _resolve_wildcard_import(
        self,
        call_name: str,
        import_map: dict[str, str],
    ) -> tuple[str, str] | None:
        """Resolve a call via wildcard imports (e.g., 'from module import *').

        Iterates over wildcard entries in the import map and tries to construct
        a qualified name matching the call.

        Args:
            call_name: The call expression name.
            import_map: Import mapping for the current module.

        Returns:
            A (type, qualified_name) tuple if resolved, or None.
        """
        for local_name, imported_qn in import_map.items():
            if not local_name.startswith("*"):
                continue

            potential_qns = []
            if "::" in imported_qn:
                potential_qns.append(f"{imported_qn}::{call_name}")
            else:
                potential_qns.append(f"{imported_qn}.{call_name}")
                potential_qns.append(f"{imported_qn}::{call_name}")

            for wildcard_qn in potential_qns:
                if wildcard_qn in self.function_registry:
                    logger.debug(
                        f"Wildcard-resolved call: {call_name} -> {wildcard_qn}"
                    )
                    return self.function_registry[wildcard_qn], wildcard_qn

        return None

    def _resolve_same_module_call(
        self,
        call_name: str,
        module_qn: str,
    ) -> tuple[str, str] | None:
        """Resolve a call to a function defined in the same module (Phase 2a).

        Tries the full call name (with :: converted to .) and, for C++ namespaced
        calls, also tries just the function name portion.

        Args:
            call_name: The call expression name.
            module_qn: Qualified name of the module containing this call.

        Returns:
            A (type, qualified_name) tuple if resolved, or None.
        """
        # For C++ calls with :: namespace separators, convert to . for registry lookup
        normalized_call_name = call_name.replace("::", ".")
        same_module_func_qn = f"{module_qn}.{normalized_call_name}"
        if same_module_func_qn in self.function_registry:
            logger.debug(
                f"Same-module resolution: {call_name} -> {same_module_func_qn}"
            )
            return (
                self.function_registry[same_module_func_qn],
                same_module_func_qn,
            )

        # For C++ namespaced calls, also try with just the function name
        # e.g., "deep_gemm::einsum::fp8_einsum" -> try "module_qn.fp8_einsum"
        if "::" in call_name:
            func_name_only = call_name.split("::")[-1]
            same_module_func_qn = f"{module_qn}.{func_name_only}"
            if same_module_func_qn in self.function_registry:
                logger.debug(
                    f"Same-module resolution (namespace stripped): {call_name} -> {same_module_func_qn}"
                )
                return (
                    self.function_registry[same_module_func_qn],
                    same_module_func_qn,
                )

        return None

    def _resolve_by_trie_fallback(
        self,
        call_name: str,
        module_qn: str,
    ) -> tuple[str, str] | None:
        """Resolve a call using the trie-based symbol index (Phase 2b fallback).

        Extracts the method/function name from a potentially qualified call and
        searches the function registry trie for matching symbols, ranked by
        import distance (and namespace match for C++ calls).

        Args:
            call_name: The call expression name.
            module_qn: Qualified name of the module containing this call.

        Returns:
            A (type, qualified_name) tuple if resolved, or None.
        """
        # For qualified calls, extract just the method name
        search_name = self._QUALIFIED_NAME_SPLIT_PATTERN.split(call_name)[-1]

        possible_matches = list(self.simple_name_lookup.get(search_name, set()))
        if not possible_matches:
            return None

        # For C++ namespaced calls, try to match namespace parts to narrow down candidates
        if "::" in call_name:
            namespace_parts = call_name.split("::")[:-1]
            if namespace_parts:

                def namespace_match_score(qn: str) -> int:
                    """Higher score = better match. Negative = match found."""
                    qn_lower = qn.lower()
                    score = 0
                    for ns_part in namespace_parts:
                        if ns_part.lower() in qn_lower:
                            score -= 10  # Bonus for matching namespace part
                    return score

                possible_matches.sort(
                    key=lambda qn: (
                        namespace_match_score(qn),
                        self._calculate_import_distance(qn, module_qn),
                    )
                )
        else:
            possible_matches.sort(
                key=lambda qn: self._calculate_import_distance(qn, module_qn)
            )

        best_candidate_qn = possible_matches[0]
        logger.debug(
            f"Trie-based fallback resolution: {call_name} -> {best_candidate_qn}"
        )
        return (
            self.function_registry[best_candidate_qn],
            best_candidate_qn,
        )

    def _resolve_builtin_call(self, call_name: str) -> tuple[str, str] | None:
        """Resolve built-in JavaScript method calls that don't exist in user code."""
        # Common built-in JavaScript objects and their methods
        # Check if the call matches any built-in pattern
        if call_name in self._JS_BUILTIN_PATTERNS:
            return ("Function", f"builtin.{call_name}")

        # Note: Instance method calls on built-in objects (e.g., myArray.push)
        # are now handled via type inference in _resolve_function_call

        # Handle JavaScript function binding methods (.bind, .call, .apply)
        if (
            call_name.endswith(".bind")
            or call_name.endswith(".call")
            or call_name.endswith(".apply")
        ):
            # These are special JavaScript method binding calls
            # Track them as function calls to the binding methods themselves
            if call_name.endswith(".bind"):
                return ("Function", "builtin.Function.prototype.bind")
            elif call_name.endswith(".call"):
                return ("Function", "builtin.Function.prototype.call")
            elif call_name.endswith(".apply"):
                return ("Function", "builtin.Function.prototype.apply")

        # Handle prototype method calls with .call or .apply
        if ".prototype." in call_name and (
            call_name.endswith(".call") or call_name.endswith(".apply")
        ):
            # Extract the prototype method name without .call/.apply
            base_call = call_name.rsplit(".", 1)[0]  # Remove .call or .apply
            return ("Function", base_call)

        return None

    def _resolve_cpp_operator_call(
        self, call_name: str, module_qn: str
    ) -> tuple[str, str] | None:
        """Resolve C++ operator calls to built-in operator functions."""
        if not call_name.startswith("operator"):
            return None

        # Check for user-defined operator overloads FIRST.
        # If a matching definition exists in the codebase, link to it.
        # Otherwise, skip — builtin operators are language primitives
        # (e.g. a + b, i++, x < y) that generate massive noise with zero
        # semantic value for code understanding.

        # Handle custom/overloaded operators in the same module
        # Try to find a matching operator definition (exclude builtin.* stubs)
        possible_matches = [
            qn for qn in self.simple_name_lookup.get(call_name, set())
            if not qn.startswith("builtin.")
        ]
        if possible_matches:
            # Prefer operators from the same module
            same_module_ops = [
                qn
                for qn in possible_matches
                if qn.startswith(module_qn) and call_name in qn
            ]
            if same_module_ops:
                # Sort to ensure deterministic selection, preferring shorter QNs
                same_module_ops.sort(key=lambda qn: (len(qn), qn))
                best_candidate = same_module_ops[0]
                return (self.function_registry[best_candidate], best_candidate)

            # Fallback to any matching operator
            # Sort to ensure deterministic selection
            possible_matches.sort(key=lambda qn: (len(qn), qn))
            best_candidate = possible_matches[0]
            return (self.function_registry[best_candidate], best_candidate)

        return None

    def _is_method_chain(self, call_name: str) -> bool:
        """Check if this appears to be a method chain with parentheses (not just obj.method)."""
        # Look for patterns like: obj.method().other_method or obj.method("arg").other_method
        # But not simple patterns like: obj.method or self.attr
        if "(" in call_name and ")" in call_name:
            # Count method calls - if more than one, it's likely chaining
            parts = call_name.split(".")
            method_calls = sum(1 for part in parts if "(" in part and ")" in part)
            return method_calls >= 1 and len(parts) >= 2
        return False

    def _resolve_chained_call(
        self,
        call_name: str,
        module_qn: str,
        local_var_types: dict[str, str] | None = None,
    ) -> tuple[str, str] | None:
        """Resolve chained method calls like obj.method().other_method()."""
        # For chained calls like "processed_user.update_name('Updated').clone"
        # We need to resolve the return type of the inner call first

        # Handle the case where we have method(args).method format
        # Find the rightmost method that's not in parentheses

        # Pattern to find the final method call: anything.method
        # where method is at the end and not in parentheses
        match = self._METHOD_NAME_PATTERN.search(call_name)
        if not match:
            return None

        final_method = match.group(1)

        # Get the object expression (everything before the final method)
        object_expr = call_name[: match.start()]

        # Try to get the return type of the object expression
        object_type = self.type_inference._infer_expression_return_type(
            object_expr, module_qn, local_var_types
        )

        if object_type:
            # Convert object_type to full qualified name if it's a short name
            full_object_type = object_type
            if "." not in object_type:
                # This is a short class name, resolve to full qualified name
                resolved_class = self._resolve_class_name(object_type, module_qn)
                if resolved_class:
                    full_object_type = resolved_class

            # Now resolve the final method call on that type
            method_qn = f"{full_object_type}.{final_method}"

            if method_qn in self.function_registry:
                logger.debug(
                    f"Resolved chained call: {call_name} -> {method_qn} "
                    f"(via {object_expr}:{object_type})"
                )
                return self.function_registry[method_qn], method_qn

            # Also check inheritance for the final method
            inherited_method = self._resolve_inherited_method(
                full_object_type, final_method
            )
            if inherited_method:
                logger.debug(
                    f"Resolved chained inherited call: {call_name} -> {inherited_method[1]} "
                    f"(via {object_expr}:{object_type})"
                )
                return inherited_method

        return None

    def _resolve_super_call(
        self, call_name: str, _module_qn: str, class_context: str | None = None
    ) -> tuple[str, str] | None:
        """Resolve super calls to parent class methods (JavaScript/TypeScript patterns).

        Args:
            call_name: The call expression (e.g., "super", "super.method").
            _module_qn: Module qualified name (unused, kept for API consistency).
            class_context: The qualified name of the class containing this super call.

        Returns:
            A (type, qualified_name) tuple for the parent method, or None if unresolvable.
        """
        # Extract method name from super call
        # JavaScript patterns:
        # - "super" -> constructor call: super(args)
        # - "super.method" -> parent method call: super.method()
        # - "super().method" -> chained call (less common)

        if call_name == "super":
            # Constructor call: super(args)
            method_name = "constructor"
        elif call_name.startswith("super."):
            # Method call: super.method()
            method_name = call_name.split(".", 1)[1]  # Get part after "super."
        elif "." in call_name:
            # Legacy pattern: super().method()
            method_name = call_name.split(".", 1)[1]  # Get part after "super()."
        else:
            # Unsupported pattern
            return None

        # Use the provided class context
        current_class_qn = class_context
        if not current_class_qn:
            logger.debug(f"No class context provided for super() call: {call_name}")
            return None

        # Look up parent classes for the current class
        if current_class_qn not in self.class_inheritance:
            logger.debug(f"No inheritance info for class {current_class_qn}")
            return None

        parent_classes = self.class_inheritance[current_class_qn]
        if not parent_classes:
            logger.debug(f"No parent classes found for {current_class_qn}")
            return None

        # Use inheritance chain traversal to find the method
        result = self._resolve_inherited_method(current_class_qn, method_name)
        if result:
            callee_type, parent_method_qn = result
            logger.debug(f"Resolved super() call: {call_name} -> {parent_method_qn}")
            return callee_type, parent_method_qn

        logger.debug(
            f"Could not resolve super() call: {call_name} in parents of {current_class_qn}"
        )
        return None

    def _resolve_inherited_method(
        self, class_qn: str, method_name: str
    ) -> tuple[str, str] | None:
        """Resolve a method by walking the MRO (Method Resolution Order) chain.

        Performs a breadth-first search through the class inheritance hierarchy
        to find the nearest parent class that defines the given method.

        Uses a thread-safe cache to avoid repeated BFS traversals for the same
        (class_qn, method_name) pair. This is especially beneficial during parallel
        call resolution where many calls may target the same inherited methods.

        Args:
            class_qn: Fully qualified name of the class to start searching from.
            method_name: The method name to look up in parent classes.

        Returns:
            A (type, qualified_name) tuple for the first matching parent method,
            or None if the method is not found in any ancestor.
        """
        cache_key = (class_qn, method_name)

        # Fast path: single dict lookup with sentinel to distinguish miss from cached None
        cached = self._inheritance_cache.get(cache_key, _CACHE_MISS)
        if cached is not _CACHE_MISS:
            return cached

        # Check if we have inheritance information for this class
        if class_qn not in self.class_inheritance:
            # Cache the negative result
            with self._inheritance_cache_lock:
                self._inheritance_cache[cache_key] = None
            return None

        # Use a queue for breadth-first search through the inheritance hierarchy
        queue = list(self.class_inheritance.get(class_qn, []))
        visited = set(queue)

        while queue:
            parent_class_qn = queue.pop(0)
            parent_method_qn = f"{parent_class_qn}.{method_name}"

            # Check if the method exists in the current parent class
            if parent_method_qn in self.function_registry:
                result = (
                    self.function_registry[parent_method_qn],
                    parent_method_qn,
                )
                # Cache the positive result
                with self._inheritance_cache_lock:
                    self._inheritance_cache[cache_key] = result
                return result

            # Add the parent's parents to the queue for further searching
            if parent_class_qn in self.class_inheritance:
                for grandparent_qn in self.class_inheritance[parent_class_qn]:
                    if grandparent_qn not in visited:
                        visited.add(grandparent_qn)
                        queue.append(grandparent_qn)

        # Cache the negative result
        with self._inheritance_cache_lock:
            self._inheritance_cache[cache_key] = None
        return None

    def clear_resolution_caches(self) -> None:
        """Clear all resolution caches.

        Should be called between builds to free memory and prevent stale results.
        """
        with self._inheritance_cache_lock:
            cache_size = len(self._inheritance_cache)
            self._inheritance_cache.clear()
        if cache_size > 0:
            logger.info(f"Cleared inheritance cache ({cache_size} entries)")

    def _resolve_init_reexport(
        self, imported_qn: str, max_depth: int = 3
    ) -> tuple[str, str] | None:
        """Follow __init__.py re-export chains to find the actual definition.

        When 'from package import Symbol' maps to 'project.package.Symbol' but
        Symbol is actually defined in 'project.package.submodule', the __init__.py
        of 'project.package' typically has 'from .submodule import Symbol'. This
        method follows that chain through the import_mapping.

        Args:
            imported_qn: The qualified name that wasn't found in function_registry
                (e.g., "project.package.Symbol")
            max_depth: Maximum chain depth to prevent infinite loops

        Returns:
            (node_type, resolved_qn) if found, None otherwise
        """
        # Extract parent module and symbol name
        # e.g., "project.package.Symbol" -> parent="project.package", symbol="Symbol"
        parts = imported_qn.rsplit(".", 1)
        if len(parts) != 2:
            return None

        current_module_qn = parts[0]
        symbol_name = parts[1]

        for _ in range(max_depth):
            # Check if the parent module has import mappings (i.e., it's an __init__.py)
            init_imports = self.import_processor.import_mapping.get(current_module_qn)
            if not init_imports:
                break

            # Check if the symbol is re-exported in this module's imports
            if symbol_name not in init_imports:
                break

            reexported_qn = init_imports[symbol_name]

            # Check if this resolved QN exists in the function registry
            if reexported_qn in self.function_registry:
                logger.debug(
                    f"Re-export chain resolved: {imported_qn} -> {reexported_qn} "
                    f"(via {current_module_qn})"
                )
                return self.function_registry[reexported_qn], reexported_qn

            # Continue following the chain
            chain_parts = reexported_qn.rsplit(".", 1)
            if len(chain_parts) != 2:
                break
            current_module_qn = chain_parts[0]
            symbol_name = chain_parts[1]

        return None

    def _calculate_import_distance(
        self, candidate_qn: str, caller_module_qn: str
    ) -> int:
        """
        Calculate the 'distance' between a candidate function and the calling module.
        Lower values indicate more likely imports (closer modules, common prefixes).
        """
        caller_parts = caller_module_qn.split(".")
        candidate_parts = candidate_qn.split(".")

        # Find common prefix length (how many package levels they share)
        common_prefix = 0
        for i in range(min(len(caller_parts), len(candidate_parts))):
            if caller_parts[i] == candidate_parts[i]:
                common_prefix += 1
            else:
                break

        # Calculate base distance (inverse of common prefix)
        base_distance = max(len(caller_parts), len(candidate_parts)) - common_prefix

        # Bonus for candidates that are "close" in the module hierarchy
        if candidate_qn.startswith(".".join(caller_parts[:-1]) + "."):
            base_distance -= 1

        return base_distance

    def _resolve_class_name(self, class_name: str, module_qn: str) -> str | None:
        """Convert a simple class name to its fully qualified name."""
        return resolve_class_name(
            class_name,
            module_qn,
            self.import_processor,
            self.function_registry,
            simple_name_lookup=self.simple_name_lookup,
        )

    def _build_nested_qualified_name(
        self,
        func_node: Node,
        module_qn: str,
        func_name: str,
        lang_config: LanguageConfig,
    ) -> str | None:
        """Build qualified name for nested functions."""
        path_parts = []
        current = func_node.parent

        if not isinstance(current, Node):
            logger.warning(
                f"Unexpected parent type for node {func_node}: {type(current)}. "
                f"Skipping."
            )
            return None

        while current and current.type not in lang_config.module_node_types:
            if current.type in lang_config.function_node_types:
                if name_node := current.child_by_field_name("name"):
                    text = name_node.text
                    if text is not None:
                        path_parts.append(text.decode("utf8"))
            elif current.type in lang_config.class_node_types:
                return None  # This is a method

            current = current.parent

        path_parts.reverse()
        if path_parts:
            return f"{module_qn}.{'.'.join(path_parts)}.{func_name}"
        else:
            return f"{module_qn}.{func_name}"

    def _is_method(self, func_node: Node, lang_config: LanguageConfig) -> bool:
        """Check if a function is actually a method inside a class."""
        current = func_node.parent
        if not isinstance(current, Node):
            return False

        while current and current.type not in lang_config.module_node_types:
            if current.type in lang_config.class_node_types:
                return True
            current = current.parent
        return False

    def _resolve_java_method_call(
        self,
        call_node: Node,
        module_qn: str,
        local_var_types: dict[str, str],
    ) -> tuple[str, str] | None:
        """Resolve Java method calls using the JavaTypeInferenceEngine."""
        # Get the Java type inference engine from the main type inference engine
        java_engine = self.type_inference.java_type_inference

        # Use the Java engine to resolve the method call
        result = java_engine.resolve_java_method_call(
            call_node, local_var_types, module_qn
        )

        if result:
            logger.debug(
                f"Java method call resolved: {call_node.text.decode('utf8') if call_node.text else 'unknown'} -> {result[1]}"
            )

        return result

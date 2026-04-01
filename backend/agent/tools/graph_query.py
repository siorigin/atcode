# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import json
import re
from collections.abc import Callable
from functools import wraps
from typing import Any, Literal

from graph.service import MemgraphIngestor
from langchain_core.tools import BaseTool, StructuredTool
from loguru import logger
from pydantic import BaseModel, Field

from .tool_registry import TOOL_DESCRIPTIONS


def _filter_null_values(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Filter out null/None values from query results to reduce token usage.

    This is applied automatically to all QueryResult instances.
    Keys with None, null, or empty list values are removed.

    Args:
        results: List of result dictionaries from graph queries

    Returns:
        Cleaned results with null values removed
    """
    cleaned = []
    for item in results:
        if not isinstance(item, dict):
            cleaned.append(item)
            continue
        # Filter out None values and empty lists (but keep False and 0)
        cleaned_item = {
            k: v for k, v in item.items() if v is not None and v != [] and v != ""
        }
        cleaned.append(cleaned_item)
    return cleaned


def _normalize_list_like_field(value: Any) -> list[str] | None:
    """Normalize DB fields that may be stored as list or plain string.

    Historical graph data may store list-like properties such as ``decorators``
    either as a real list or as a single string. Normalize both forms so
    callers can rely on a consistent ``list[str]`` shape.
    """
    if value is None:
        return None
    if isinstance(value, list):
        return [str(v) for v in value if v is not None and str(v) != ""]
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                return [str(v) for v in parsed if v is not None and str(v) != ""]
        except (ValueError, TypeError):
            pass
        return [stripped]
    return [str(value)]


def _normalize_find_node_results(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize shape of ``find_nodes`` results for downstream consumers."""
    normalized = []
    for item in results:
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        new_item = dict(item)
        if "decorators" in new_item:
            decorators = _normalize_list_like_field(new_item.get("decorators"))
            if decorators:
                new_item["decorators"] = decorators
            else:
                new_item.pop("decorators", None)
        normalized.append(new_item)
    return normalized


class QueryResult(BaseModel):
    """Result from a graph query operation."""

    success: bool
    results: list[dict[str, Any]]
    count: int
    summary: str
    query_used: str = ""

    # Relationship mapping (populated when count > 1 and auto_map_relationships=True)
    entry_points: list[str] = Field(
        default_factory=list
    )  # Qualified names of top-level nodes
    hierarchy_tree: str = (
        ""  # Hierarchical tree structure (e.g., "- A\n  - B\n    - C")
    )
    has_relationship_mapping: bool = (
        False  # Whether relationship analysis was performed
    )

    def __init__(self, **data):
        """Initialize QueryResult with automatic null filtering on results."""
        # Filter null values from results before validation
        if "results" in data and data["results"]:
            data["results"] = _filter_null_values(data["results"])
        super().__init__(**data)


def handle_query_errors(func: Callable[..., QueryResult]) -> Callable[..., QueryResult]:
    """Decorator to handle common query errors and return consistent QueryResult."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> QueryResult:
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {func.__name__}: {e}")
            return QueryResult(
                success=False,
                results=[],
                count=0,
                summary=f"Error: {e}",
                query_used="N/A",
            )

    return wrapper


class GraphQueryTools:
    """Collection of specialized tools for querying the Memgraph knowledge graph."""

    def __init__(self, ingestor: MemgraphIngestor, project_name: str):
        """
        Initialize graph query tools.

        Args:
            ingestor: MemgraphIngestor instance for database operations
            project_name: Project name for scoping queries
        """
        self.ingestor = ingestor
        self.project_name = project_name
        self.project_prefix = f"{project_name}."

    @handle_query_errors
    def list_repos(self) -> QueryResult:
        """
        List all available repositories in the database.

        Use this tool to discover what code repositories are available for exploration.
        This is especially useful when:
        - You detect external library calls (e.g., deep_gemm.fp8_gemm_nt(), flash_attn_func())
        - You want to trace implementation across multiple repositories
        - The user asks about cross-repo dependencies

        Common external libraries that might be available:
        - DeepGEMM: FP8 matrix multiplication kernels (e.g., fp8_gemm_nt)
        - flash_attn: Flash Attention implementations
        - vllm: Inference engine operators
        - triton: GPU kernel compiler
        - sglang: Serving framework

        After calling list_repos, use find_nodes(query, repo_name="RepoName") to
        search within a specific repository.

        Returns:
            QueryResult with list of available repositories (names only, lightweight).
            NOTE: This tool returns only repository names to minimize database load.
            Statistics (node/relationship counts) are not included to avoid expensive queries.
        """
        # Use lightweight method - only get project names, no statistics
        # This avoids expensive per-project queries that calculate node counts
        project_names = self.ingestor.list_project_names()

        # Return lightweight results with just names
        results = [{"name": name} for name in project_names]

        return QueryResult(
            success=True,
            results=results,
            count=len(results),
            summary=f"Found {len(results)} repositories: {', '.join(project_names)}",
            query_used="list_project_names",
        )

    def _detect_search_strategy(
        self, query: str
    ) -> tuple[Literal["exact", "pattern", "regex", "and"], str]:
        """
        Intelligently detect the best search strategy and normalize the query.

        Detection Rules (in order of priority):
        1. AND LOGIC (&): Explicit AND for precise matching
           - "triton&att" → AND search (must contain both)
           - "flash&attn&kernel" → AND search (must contain all three)
        2. OR LOGIC (|): Explicit OR for broader matching
           - "unified|attention" → OR search (contains either)
           - "flash|attn" → OR search (parallel search)
        3. GLOB→REGEX: Convert glob wildcards to regex
           - "unified*att" → "unified.*att"
           - "flash*attn*" → "flash.*attn.*"
        4. MULTI-WORD→AND: Split spaces into AND for precision (changed from OR)
           - "unified attention" → AND search (contains both words)
           - "flash attention kernel" → AND (all three words)
        5. REGEX: Already contains regex special chars (no & or |)
           - ".*flash.*attn.*", "FusedMoE.*"
        6. EXACT: Complete qualified_name with 3+ dot parts
           - "sglang.srt.layers.moe.fused_moe"
        7. PATTERN: Default for simple names

        Args:
            query: The search query string

        Returns:
            Tuple of (strategy, normalized_query)
            Strategy can be: "exact", "pattern", "regex", or "and"
        """
        original_query = query
        query = query.strip()

        # 1. Explicit AND logic (&) - highest priority for precision
        if "&" in query and not query.startswith(".*"):
            # Split by & and trim
            and_terms = [t.strip() for t in query.split("&") if t.strip()]
            if len(and_terms) > 1:
                logger.debug(
                    f"[_detect_search_strategy] AND query: '{original_query}' → terms: {and_terms}"
                )
                return "and", "&".join(and_terms)

        # 2. Explicit OR logic (|) - already in pattern format
        if "|" in query and not query.startswith(".*"):
            # Validate it's not a regex pattern
            or_terms = [t.strip() for t in query.split("|") if t.strip()]
            if len(or_terms) > 1:
                # Simple OR without regex chars
                if not any(
                    c in query
                    for c in ["*", "+", "?", "^", "$", "[", "]", "\\", "(", ")"]
                ):
                    normalized = "|".join(or_terms)
                    logger.debug(
                        f"[_detect_search_strategy] OR query: '{original_query}' → '{normalized}'"
                    )
                    return "pattern", normalized

        # 3. Convert glob-style wildcards to regex
        # "unified*att" → "unified.*att"
        # Handle cases where * is NOT followed by regex chars (avoid breaking existing regex)
        # IMPORTANT: Skip if ".*" already exists (it's already regex, not glob)
        if (
            "*" in query
            and ".*" not in query
            and not any(c in query for c in ["?", "[", "]", "^", "$", "\\"])
        ):
            # Check if this looks like glob (has * but not other regex chars)
            if "&" not in query and "|" not in query and "+" not in query:
                # Convert glob to regex: * → .*
                normalized = query.replace("*", ".*")
                logger.debug(
                    f"[_detect_search_strategy] Glob→Regex: '{original_query}' → '{normalized}'"
                )
                return "regex", normalized

        # 4. Multi-word query → AND for precision (changed from OR)
        # "unified attention" → AND (must contain both words) - more intuitive
        if " " in query:
            # Split by spaces
            words = [w.strip() for w in query.split() if w.strip()]
            if len(words) > 1:
                # Check if any word is a stop word that should be ignored
                stop_words = {"the", "a", "an", "in", "on", "at", "for", "to", "of"}
                filtered_words = [w for w in words if w.lower() not in stop_words]
                if filtered_words:
                    normalized = "&".join(filtered_words)
                    logger.debug(
                        f"[_detect_search_strategy] Multi-word→AND: '{original_query}' → '{normalized}'"
                    )
                    return "and", normalized

        # 5. Check for existing regex special characters
        regex_chars = set("*+?^$|[]\\()")
        if any(c in query for c in regex_chars):
            logger.debug(f"[_detect_search_strategy] Detected regex pattern: '{query}'")
            return "regex", query

        # 6. Check if this looks like a complete qualified_name
        if "." in query:
            parts = query.split(".")
            identifier_pattern = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
            all_valid = all(identifier_pattern.match(part) for part in parts if part)

            if all_valid and len(parts) >= 3:
                logger.debug(
                    f"[_detect_search_strategy] Detected qualified_name (exact): '{query}'"
                )
                return "exact", query
            elif all_valid and len(parts) == 2:
                logger.debug(
                    f"[_detect_search_strategy] Two-part query, using pattern: '{query}'"
                )
                return "pattern", query

        # 7. Default: pattern search for simple names
        logger.debug(f"[_detect_search_strategy] Using pattern search for: '{query}'")
        return "pattern", query

    @handle_query_errors
    def find_nodes(
        self,
        query: str | list[str],
        search_strategy: Literal["exact", "pattern", "regex", "and", "auto"] = "auto",
        node_type: Literal["Code", "All"] | None = None,
        case_sensitive: bool = False,
        decorator_filter: str | None = None,
        auto_map_relationships: bool = True,
        repo_name: str | None = None,
    ) -> QueryResult:
        """
        Unified node search with configurable search strategy.

        Args:
            query: Search query - supports multiple syntaxes:
                - Single keyword (RECOMMENDED): "attention", "flops", "compute"
                - OR logic (RECOMMENDED): "flash|attn" → contains EITHER (parallel search)
                - Glob patterns: "flash*attn" → "flash.*attn" (regex)
                - Regex: ".*fused.*moe.*"
                - List of queries: ["attention", "moe"] → searches each independently
                - AVOID space-separated phrases! "flops calculation" → AND search (often fails)
                - Use | for variants: "torch|cuda|gpu" instead of "graphics processing"
            search_strategy: "auto" (recommended), "pattern", "regex", "and", or "exact"
            node_type: Simplified type filter:
                - "Code" (default/recommended): Function + Method + Class (source code elements)
                - "All" or None: No filtering (searches all node types)
            repo_name: Target repository name. Special values:
                - None: Search in current project (default)
                - "all": Search across ALL repositories (global search)
                - "<repo_name>": Search in specific repository

        **SEARCH TIPS:**
        - Use single keywords: "flops" NOT "flops calculation"
        - Use | for alternatives: "torch|cuda" NOT "torch cuda"
        - Use * for wildcards: "*flops*" to match flops anywhere in name
        """
        # Normalize node_type: "Code" → ":Function|Method|Class", "All"/None → ""
        # This simplifies the API while maintaining full flexibility internally
        if node_type == "Code" or node_type is None:
            # Default to Code nodes (most common use case)
            label_filter = ":Function|Method|Class"
            effective_node_type = "Code"
        elif node_type == "All":
            label_filter = ""
            effective_node_type = "All"
        else:
            # Fallback for any unexpected value
            label_filter = ":Function|Method|Class"
            effective_node_type = "Code"
            logger.warning(
                f"[find_nodes] Unknown node_type '{node_type}', defaulting to 'Code'"
            )

        # Determine target project - support cross-repo search
        if repo_name and repo_name.lower() == "all":
            # Global search: no project prefix filtering (DISCOURAGED - very slow)
            project_prefix = None
            logger.warning(
                "[find_nodes] ⚠️ Global search (repo_name='all') is SLOW! "
                "Consider using list_repos() to find the specific repo, then search with repo_name='<specific_repo>'"
            )
        elif repo_name:
            # Cross-repo search: search in SPECIFIC repository (RECOMMENDED)
            target_project = repo_name
            project_prefix = f"{target_project}."
            logger.info(f"[find_nodes] Cross-repo search in '{repo_name}' repository")
        else:
            # Default: search in current project
            target_project = self.project_name
            project_prefix = f"{target_project}."

        # Handle empty input
        queries = query if isinstance(query, list) else [query]
        if not queries or (len(queries) == 1 and not queries[0]):
            return QueryResult(
                success=False,
                results=[],
                count=0,
                summary="Error: Empty query provided",
            )

        # OPTIMIZATION: Limit batch queries to prevent memory issues
        MAX_BATCH_SIZE = 50
        if len(queries) > MAX_BATCH_SIZE:
            logger.warning(
                f"Large batch query ({len(queries)} items) truncated to {MAX_BATCH_SIZE}"
            )
            queries = queries[:MAX_BATCH_SIZE]

        # Auto-detect strategy if needed
        if search_strategy == "auto":
            first_query = queries[0]
            detected_strategy, normalized_query = self._detect_search_strategy(
                first_query
            )
            search_strategy = detected_strategy
            queries[0] = normalized_query
            logger.debug(
                f"Auto-detected search strategy: {search_strategy}, normalized query: '{normalized_query}'"
            )

        # Route to appropriate search implementation
        if search_strategy == "exact":
            result = self._search_exact(queries, label_filter, project_prefix)
            # SMART FALLBACK: If exact search finds nothing, try pattern search
            # This handles cases where user provides a name that's not a full qualified_name
            if result.count == 0:
                original_query = (
                    queries[0] if len(queries) == 1 else f"{len(queries)} queries"
                )
                logger.info(
                    f"[find_nodes] Exact search found nothing for '{original_query}', falling back to pattern search"
                )
                fallback_result = self._search_pattern(
                    queries,
                    use_regex=False,
                    label_filter=label_filter,
                    effective_node_type=effective_node_type,
                    case_sensitive=case_sensitive,
                    decorator_filter=decorator_filter,
                    auto_map_relationships=auto_map_relationships,
                    project_prefix=project_prefix,
                )
                if fallback_result.count > 0:
                    fallback_result.summary = (
                        f"[Fallback from exact→pattern] {fallback_result.summary}"
                    )
                    return fallback_result
            return result
        elif search_strategy in ("pattern", "regex"):
            return self._search_pattern(
                queries,
                use_regex=(search_strategy == "regex"),
                label_filter=label_filter,
                effective_node_type=effective_node_type,
                case_sensitive=case_sensitive,
                decorator_filter=decorator_filter,
                auto_map_relationships=auto_map_relationships,
                project_prefix=project_prefix,
            )
        elif search_strategy == "and":
            return self._search_pattern(
                queries,
                use_regex=False,
                use_and=True,
                label_filter=label_filter,
                effective_node_type=effective_node_type,
                case_sensitive=case_sensitive,
                decorator_filter=decorator_filter,
                auto_map_relationships=auto_map_relationships,
                project_prefix=project_prefix,
            )
        else:
            return QueryResult(
                success=False,
                results=[],
                count=0,
                summary=f"Error: Invalid search_strategy '{search_strategy}'. Must be 'exact', 'pattern', 'regex', 'and', or 'auto'.",
                query_used="N/A",
            )

    def _search_exact(
        self,
        queries: list[str],
        label_filter: str = ":Function|Method|Class",
        project_prefix: str | None = None,
    ) -> QueryResult:
        """Internal: Exact qualified name search.

        Args:
            label_filter: Cypher label filter string (e.g., ":Function|Method|Class" or "")
            project_prefix: Project prefix for filtering.
                - If None: Global search, use queries as-is without adding prefix
                - If string: Add prefix if query doesn't already have a valid repo prefix

        OPTIMIZATION: Uses IN clause for batch queries, caches repo validation,
        and applies label filter to leverage indexes.
        """
        # Check if this is a global search (no project filtering)
        is_global_search = project_prefix is None

        # OPTIMIZATION: Pre-fetch all valid project names once (cached)
        # instead of querying for each potential repo prefix
        valid_repos_query = "MATCH (p:Project) RETURN p.name AS name"
        valid_repos_result = self.ingestor.fetch_all(
            valid_repos_query,
            {},
            use_cache=True,
            cache_ttl=300.0,  # Cache for 5 minutes
        )
        # Create both exact and case-insensitive lookup maps
        # This handles cases where qualified_name prefix is "sglang" but Project.name is "SGLang"
        valid_repo_names = (
            {r["name"] for r in valid_repos_result} if valid_repos_result else set()
        )
        valid_repo_names_lower = (
            {r["name"].lower(): r["name"] for r in valid_repos_result}
            if valid_repos_result
            else {}
        )

        processed_queries = []
        for q in queries:
            parts = q.split(".")
            if len(parts) >= 2:
                potential_repo = parts[0]
                # Check both exact match and case-insensitive match
                if potential_repo in valid_repo_names:
                    # Exact match - use query as-is
                    processed_queries.append(q)
                    if not is_global_search:
                        logger.debug(
                            f"[_search_exact] Detected repo prefix '{potential_repo}' in: {q}"
                        )
                    continue
                elif potential_repo.lower() in valid_repo_names_lower:
                    # Case-insensitive match - use query as-is (qualified_name in DB uses this casing)
                    processed_queries.append(q)
                    if not is_global_search:
                        actual_repo = valid_repo_names_lower[potential_repo.lower()]
                        logger.debug(
                            f"[_search_exact] Detected repo prefix '{potential_repo}' (matches '{actual_repo}') in: {q}"
                        )
                    continue

            # No valid repo prefix found
            if is_global_search:
                # Global search: use as-is
                processed_queries.append(q)
            else:
                # Scoped search: add default prefix if needed
                prefix = project_prefix if project_prefix else self.project_prefix
                if not q.startswith(prefix) and "." in q:
                    processed_queries.append(f"{prefix}{q}")
                else:
                    processed_queries.append(q)

        # label_filter is passed as parameter, already formatted (e.g., ":Function|Method|Class")
        # OPTIMIZATION: Always use IN clause even for single queries (consistent execution plan)
        # This allows Memgraph to use ScanAllByLabelPropertyValue efficiently
        query = f"""
        MATCH (n{label_filter})
        WHERE n.qualified_name IN $qn_list
        OPTIONAL MATCH (f:File)-[:DEFINES]->(n)
        RETURN DISTINCT n.qualified_name AS qualified_name,
               n.name AS name,
               labels(n) AS type,
               COALESCE(n.path, f.path) AS path,
               n.decorators AS decorators,
               n.docstring AS docstring,
               n.start_line AS start_line,
               n.end_line AS end_line
        LIMIT 50
        """
        results = _normalize_find_node_results(
            self.ingestor.fetch_all(query, {"qn_list": processed_queries})
        )

        if results:
            return QueryResult(
                success=True,
                results=results,
                count=len(results),
                summary=f"Found {len(results)} exact match(es) for {len(queries)} qualified name(s)",
                query_used=query,
            )

        # No matches found
        original_query = queries[0]
        searched_as = processed_queries[0] if processed_queries else original_query
        logger.info(
            f"No exact match for '{original_query}' (searched as '{searched_as}')"
        )
        return QueryResult(
            success=True,
            results=[],
            count=0,
            summary=f"No match for '{original_query}'. Try search_strategy='pattern' or 'regex' for fuzzy search.",
        )

    def _search_pattern(
        self,
        queries: list[str],
        use_regex: bool,
        label_filter: str,
        effective_node_type: str,
        case_sensitive: bool,
        decorator_filter: str | None,
        auto_map_relationships: bool,
        project_prefix: str | None = None,
        use_and: bool = False,
    ) -> QueryResult:
        """Internal: Pattern/regex/AND search.

        Args:
            use_and: If True, use AND logic (all terms must match).
                Query format: "term1&term2" splits into multiple AND conditions.
                Example: "triton&att" → CONTAINS "triton" AND CONTAINS "att"
            label_filter: Cypher label filter string (e.g., ":Function|Method|Class" or "")
            effective_node_type: Display name for the node type ("Code" or "All")
            project_prefix: Project prefix for filtering.
                - If None: Global search across all repos
                - If string: Filter to nodes starting with this prefix
            auto_map_relationships: If True, analyze CALLS relationships between results
                to identify entry points and build dependency hierarchy.

        OPTIMIZATION NOTES:
        - Label filters are CRITICAL for index usage
        - STARTS WITH on qualified_name uses label-property index
        - CONTAINS/regex on n.name requires full scan within filtered set
        - AND logic uses multiple CONTAINS with AND (faster than regex)
        """
        is_global_search = project_prefix is None

        # Use provided prefix or default to self.project_prefix (only if not global)
        if is_global_search:
            prefix = None
        else:
            prefix = project_prefix if project_prefix else self.project_prefix

        # Build name condition - support multiple patterns with OR/AND logic
        # OPTIMIZATION: Pre-lowercase patterns in Python to avoid repeated toLower() in DB

        # AND LOGIC: Split by & and require all terms to match
        if use_and and len(queries) == 1 and "&" in queries[0]:
            and_terms = [t.strip() for t in queries[0].split("&") if t.strip()]
            if and_terms:
                logger.debug(
                    f"[_search_pattern] AND search with {len(and_terms)} terms: {and_terms}"
                )
                # Build AND conditions: each term must be contained
                and_conditions = []
                for i, term in enumerate(and_terms):
                    if not case_sensitive:
                        and_conditions.append(f"toLower(n.name) CONTAINS $and_term{i}")
                    else:
                        and_conditions.append(f"n.name CONTAINS $and_term{i}")
                name_condition = " AND ".join(and_conditions)

                # Build params for AND terms
                params = {}
                for i, term in enumerate(and_terms):
                    params[f"and_term{i}"] = (
                        term.lower() if not case_sensitive else term
                    )
        else:
            # SMART PATTERN HANDLING: Detect OR patterns like "qwen|video" and optimize
            # If pattern contains "|" and we're in pattern mode, split into multiple CONTAINS (faster than regex)
            if not use_regex and len(queries) == 1 and "|" in queries[0]:
                or_patterns = [p.strip() for p in queries[0].split("|") if p.strip()]
                if or_patterns:
                    queries = or_patterns
                    logger.debug(
                        f"[_search_pattern] Optimized OR pattern into {len(queries)} CONTAINS conditions"
                    )

            if use_regex:
                # REGEX MODE: Use =~ operator (ECMAScript syntax)
                # NOTE: Memgraph uses ECMAScript regex, NOT Java regex like Neo4j
                # (?i) flag may not work - use toLower() approach for case-insensitive matching
                if len(queries) == 1:
                    pattern = queries[0]
                    if not case_sensitive:
                        name_condition = "toLower(n.name) =~ $pattern"
                        # Also lowercase the pattern for case-insensitive regex
                        params_pattern = pattern.lower()
                    else:
                        name_condition = "n.name =~ $pattern"
                        params_pattern = pattern
                else:
                    if not case_sensitive:
                        name_condition = (
                            "ANY(p IN $patterns WHERE toLower(n.name) =~ p)"
                        )
                    else:
                        name_condition = "ANY(p IN $patterns WHERE n.name =~ p)"
            else:
                # CONTAINS MODE - faster than regex, supports multiple patterns for OR logic
                if len(queries) == 1:
                    name_condition = (
                        "toLower(n.name) CONTAINS $pattern"
                        if not case_sensitive
                        else "n.name CONTAINS $pattern"
                    )
                else:
                    # Multiple patterns = OR logic (match any pattern)
                    name_condition = (
                        "ANY(p IN $patterns WHERE toLower(n.name) CONTAINS p)"
                        if not case_sensitive
                        else "ANY(p IN $patterns WHERE n.name CONTAINS p)"
                    )

            # Build params - pre-process patterns for case-insensitivity
            params = {}
            if use_regex and len(queries) == 1:
                params["pattern"] = params_pattern
            elif len(queries) == 1:
                params["pattern"] = (
                    queries[0].lower() if not case_sensitive else queries[0]
                )
            else:
                params["patterns"] = (
                    [q.lower() for q in queries] if not case_sensitive else queries
                )

        # Build qualified_name filter for project scoping
        if is_global_search:
            # Global search: for code nodes, ensure qualified_name exists (uses index)
            qn_filter = "AND n.qualified_name IS NOT NULL"
        else:
            # Scoped search: filter by project prefix using qualified_name STARTS WITH (uses index)
            qn_filter = "AND n.qualified_name STARTS WITH $project_prefix"
            params["project_prefix"] = prefix

        # Build decorator filter if provided
        decorator_condition = ""
        if decorator_filter:
            params["decorator"] = decorator_filter.lower()
            # Historical graph data may store decorators either as list[str]
            # or as a single string. Use toString() so both shapes match.
            decorator_condition = (
                "AND n.decorators IS NOT NULL "
                "AND toLower(toString(n.decorators)) CONTAINS $decorator"
            )

        # Build WHERE clause - put indexed conditions FIRST
        if is_global_search:
            where_clause = f"{name_condition} {qn_filter} {decorator_condition}".strip()
        else:
            # CRITICAL: Put STARTS WITH first - it uses the label-property index
            where_clause = f"n.qualified_name STARTS WITH $project_prefix AND {name_condition} {decorator_condition}".strip()

        # OPTIMIZATION: Skip OPTIONAL MATCH for Module path when we have label filter
        # Function/Method/Class nodes often have path directly or we can derive from qualified_name
        query = f"""
        MATCH (n{label_filter})
        WHERE {where_clause}
        RETURN DISTINCT n.qualified_name AS qualified_name,
               n.name AS name,
               labels(n) AS type,
               n.path AS path,
               n.decorators AS decorators,
               n.docstring AS docstring,
               n.start_line AS start_line,
               n.end_line AS end_line
        LIMIT 50
        """

        results = _normalize_find_node_results(self.ingestor.fetch_all(query, params))
        return self._build_pattern_result(
            results,
            queries,
            use_regex,
            effective_node_type,
            decorator_filter,
            auto_map_relationships,
            use_and=use_and,
        )

    def _build_pattern_result(
        self,
        results: list[dict[str, Any]],
        queries: list[str],
        use_regex: bool,
        effective_node_type: str,
        decorator_filter: str | None,
        auto_map_relationships: bool = False,
        use_and: bool = False,
    ) -> QueryResult:
        """Build QueryResult for pattern searches.

        Args:
            effective_node_type: Display name for the node type ("Code" or "All")
            auto_map_relationships: If True and results > 1, analyze CALLS relationships
                between results to identify entry points and build dependency hierarchy.
            use_and: If True, this was an AND search (all terms must match)
        """
        pattern_display = (
            queries[0] if len(queries) == 1 else f"{len(queries)} patterns"
        )
        if use_and:
            mode_display = "AND"
            # Split for display
            if "&" in pattern_display:
                terms = [t.strip() for t in pattern_display.split("&")]
                pattern_display = " & ".join(terms)
        else:
            mode_display = (
                "regex" if use_regex else "OR" if "|" in pattern_display else "pattern"
            )

        summary_parts = [f"Found {len(results)} node(s) using {mode_display} search"]
        if len(queries) == 1:
            summary_parts.append(f"for pattern '{pattern_display}'")
        else:
            summary_parts.append(f"for {len(queries)} patterns")
        if effective_node_type:
            summary_parts.append(f"(type: {effective_node_type})")
        if decorator_filter:
            summary_parts.append(f"with decorator '{decorator_filter}'")

        query_result = QueryResult(
            success=True,
            results=results,
            count=len(results),
            summary=" ".join(summary_parts),
        )

        # Enrich with relationship mapping if requested and we have multiple results
        if auto_map_relationships and len(results) > 1:
            query_result = self.enrich_query_result_with_relationships(query_result)

        return query_result

    @handle_query_errors
    def find_calls(
        self,
        qualified_name: str,
        direction: Literal["outgoing", "incoming"] = "outgoing",
        depth: int = 1,
    ) -> QueryResult:
        """Find call relationships for a function/method.

        Args:
            qualified_name: The function/method to analyze
            direction:
                - "outgoing": Find functions called BY this function (what does it call?)
                - "incoming": Find functions that CALL this function (who calls it?)
            depth: Traversal depth (1-5, default=1)

        OPTIMIZATION:
        - Bounded depth (max 5) to prevent runaway traversals
        - Uses label filter (Function|Method) to leverage indexes
        """
        # Add project prefix if not present
        if not qualified_name.startswith(self.project_prefix):
            qualified_name = f"{self.project_prefix}{qualified_name}"

        # OPTIMIZATION: Bound depth to prevent exponential explosion
        MAX_DEPTH = 5
        if depth > MAX_DEPTH:
            logger.warning(
                f"[find_calls] Depth {depth} exceeds max {MAX_DEPTH}, capping"
            )
            depth = MAX_DEPTH

        depth_spec = f"*1..{depth}" if depth > 1 else ""

        if direction == "outgoing":
            # Find what this function calls
            query = f"""
            MATCH (caller)-[:CALLS{depth_spec}]->(callee)
            WHERE caller.qualified_name = $qn
            RETURN DISTINCT callee.qualified_name AS qualified_name,
                   callee.name AS name,
                   labels(callee) AS type,
                   callee.path AS path,
                   callee.docstring AS docstring,
                   callee.start_line AS start_line,
                   callee.end_line AS end_line
            LIMIT 100
            """
            summary_template = (
                "Found {count} function(s) called by '{qn}' (depth={depth})"
            )
        else:
            # Find what calls this function
            query = f"""
            MATCH (caller)-[:CALLS{depth_spec}]->(callee)
            WHERE callee.qualified_name = $qn
            RETURN DISTINCT caller.qualified_name AS qualified_name,
                   caller.name AS name,
                   labels(caller) AS type,
                   caller.path AS path,
                   caller.docstring AS docstring,
                   caller.start_line AS start_line,
                   caller.end_line AS end_line
            LIMIT 100
            """
            summary_template = (
                "Found {count} function(s) that call '{qn}' (depth={depth})"
            )

        results = self.ingestor.fetch_all(query, {"qn": qualified_name})
        return QueryResult(
            success=True,
            results=results,
            count=len(results),
            summary=summary_template.format(
                count=len(results), qn=qualified_name, depth=depth
            ),
            query_used=query,
        )

    @handle_query_errors
    def get_children(
        self,
        identifier: str,
        identifier_type: str = "auto",
        depth: int = 1,
        child_types: str | list[str] | None = None,
    ) -> QueryResult:
        """
        Get children of any node type with intelligent output based on parent type.

        This unified tool handles ALL node types in the knowledge graph:

        STRUCTURAL NODES (return file system structure):
        - Project: returns Folder, File
        - Folder: returns Folder, File
          - Note: Package folders (is_package=true) are just Folder nodes with qualified_name

        CODE NODES (return source code elements):
        - File: returns Class, Function (source code files have qualified_name property)
        - Class: returns Method

        SPECIAL IDENTIFIERS:
        - "." or "current": Resolves to current project (self.project_name)

        Note: The "module" type is an alias for "file" since Module was merged into File.
        Source code files have qualified_name and module_context properties.

        Args:
            identifier_type: Type of the parent node:
                - "auto": Auto-detect (recommended)
                - "project": Query Project node by name
                - "folder": Query Folder node by path or qualified_name
                - "file": Query File node by path or qualified_name
                - "class": Query Class node by qualified_name
            child_types: Filter by type. Can be:
                - Comma-separated string: "Folder,File" or "Class,Function"
                - List of strings: ["Folder", "File"]
                - None: No filtering (return all types)
        """
        # Parse child_types: support both comma-separated string and list
        child_types_list: list[str] | None = None
        if child_types:
            if isinstance(child_types, str):
                # Parse comma-separated or JSON-like string
                cleaned = child_types.strip()
                if cleaned.startswith("["):
                    # Handle JSON array string like '["Folder", "File"]'
                    try:
                        child_types_list = json.loads(cleaned)
                    except json.JSONDecodeError:
                        child_types_list = [
                            t.strip().strip("\"'")
                            for t in cleaned.split(",")
                            if t.strip()
                        ]
                else:
                    # Handle comma-separated string like "Folder,File"
                    child_types_list = [
                        t.strip() for t in cleaned.split(",") if t.strip()
                    ]
            else:
                child_types_list = child_types
        try:
            children = []
            query_used = "get_children"

            # Handle special identifiers for current project
            if identifier in (".", "current"):
                identifier = self.project_name
                identifier_type = "project"
                logger.debug(
                    f"[get_children] Resolved '.' to current project: {self.project_name}"
                )

            # Handle structural node types directly
            if identifier_type == "project":
                children = self._query_project_children(identifier, depth)
                query_used = "project_children"
            elif identifier_type == "folder":
                # Try both path-based and qualified_name-based queries
                children = self._query_folder_children(identifier, depth)
                if not children:
                    # Fallback: try as qualified_name for package folders
                    children = self._query_folder_by_qualified_name(identifier, depth)
                query_used = "folder_children"
            elif identifier_type in ("file", "module", "class"):
                # Delegate to existing ingestor method for code nodes
                children = self.ingestor.get_children(
                    identifier=identifier,
                    identifier_type=identifier_type,
                    depth=depth,
                    project_name=self.project_name,  # Pass project_name for proper isolation
                )
            elif identifier_type == "auto":
                # Auto-detect: try structural first, then code nodes
                children = self._auto_detect_and_query(identifier, depth)
            else:
                return QueryResult(
                    success=False,
                    results=[],
                    count=0,
                    summary=f"Invalid identifier_type: {identifier_type}",
                    query_used="get_children",
                )

            # Filter by child_types if specified
            if child_types_list and children:
                children = [
                    c
                    for c in children
                    if any(t in c.get("type", []) for t in child_types_list)
                ]

            if not children:
                return QueryResult(
                    success=True,
                    results=[],
                    count=0,
                    summary=f"No children found for {identifier_type}: {identifier}",
                    query_used=query_used,
                )

            # Format children for consistency with other query results
            formatted_results = []
            for child in children:
                result = {
                    "name": child.get("name", "N/A"),
                    "type": child.get("type", ["Unknown"]),
                    "depth": child.get("depth", 1),
                }
                # Add qualified_name for code nodes, path for structural nodes
                if child.get("qualified_name"):
                    result["qualified_name"] = child["qualified_name"]
                if child.get("path"):
                    result["path"] = child["path"]
                # Add optional fields
                for field in [
                    "start_line",
                    "end_line",
                    "decorators",
                    "docstring",
                    "extension",
                ]:
                    if child.get(field) is not None:
                        result[field] = child[field]
                formatted_results.append(result)

            # Count by type
            type_counts = {}
            for child in children:
                node_type = (
                    child.get("type", ["Unknown"])[0]
                    if child.get("type")
                    else "Unknown"
                )
                type_counts[node_type] = type_counts.get(node_type, 0) + 1

            type_summary = ", ".join(
                [f"{count} {typ}" for typ, count in type_counts.items()]
            )
            summary = (
                f"Found {len(children)} child node(s) in '{identifier}': {type_summary}"
            )

            # Build hierarchy tree for structural nodes
            hierarchy_tree = ""
            is_structural_query = identifier_type in (
                "project",
                "package",
                "folder",
                "auto",
            )
            if is_structural_query and formatted_results:
                hierarchy_tree = self._build_hierarchy_tree(
                    identifier, formatted_results
                )

            # For structural queries with tree output, omit detailed results (tree is more readable)
            # For code queries (file/module/class), keep full results with line numbers etc.
            if hierarchy_tree:
                # Return tree only - results would be redundant
                return QueryResult(
                    success=True,
                    results=[],  # Tree replaces detailed list
                    count=len(formatted_results),
                    summary=summary,
                    query_used=query_used,
                    hierarchy_tree=hierarchy_tree,
                )
            else:
                # Return detailed results for code nodes
                return QueryResult(
                    success=True,
                    results=formatted_results,
                    count=len(formatted_results),
                    summary=summary,
                    query_used=query_used,
                )

        except Exception as e:
            logger.error(f"Error getting children: {e}")
            return QueryResult(
                success=False,
                results=[],
                count=0,
                summary=f"Error: {e}",
                query_used="get_children",
            )

    def _auto_detect_and_query(
        self, identifier: str, depth: int
    ) -> list[dict[str, Any]]:
        """Auto-detect node type and query children."""
        # Handle special identifiers first
        if identifier in (".", "current"):
            logger.debug(
                f"[_auto_detect_and_query] Resolved '{identifier}' to current project: {self.project_name}"
            )
            return self._query_project_children(self.project_name, depth)

        # Try project name first (simple name without / or .)
        if "/" not in identifier and "." not in identifier.split("/")[-1]:
            children = self._query_project_children(identifier, depth)
            if children:
                logger.debug(
                    f"[_auto_detect_and_query] Matched as project: {identifier}"
                )
                return children

        # Try folder path (contains /)
        if "/" in identifier:
            # First try with project prefix for qualified_name
            qualified_path = f"{self.project_name}/{identifier}"
            children = self._query_folder_children(qualified_path, depth)
            if children:
                logger.debug(
                    f"[_auto_detect_and_query] Matched as folder with qualified_name: {qualified_path}"
                )
                return children

            # Then try raw path
            children = self._query_folder_children(identifier, depth)
            if children:
                logger.debug(
                    f"[_auto_detect_and_query] Matched as folder with path: {identifier}"
                )
                return children

        # Try folder by qualified_name (for package folders with is_package=true)
        children = self._query_folder_by_qualified_name(identifier, depth)
        if children:
            logger.debug(
                f"[_auto_detect_and_query] Matched as folder by qualified_name: {identifier}"
            )
            return children

        # Try file path (has extension) - delegate to ingestor
        children = self.ingestor.get_children(
            identifier, "auto", depth, self.project_name
        )
        if children:
            logger.debug(
                f"[_auto_detect_and_query] Matched as file/code node: {identifier}"
            )
            return children

        logger.debug(f"[_auto_detect_and_query] No match found for: {identifier}")
        return []

    def _build_hierarchy_tree(
        self, root_name: str, results: list[dict[str, Any]]
    ) -> str:
        """Build an ASCII tree representation from flat results.

        Args:
            root_name: Name of the root node
            results: Flat list of child nodes with 'path' and 'depth' fields

        Returns:
            ASCII tree string like:
            DeepGEMM/
            ├── deep_gemm/
            │   ├── __init__.py
            │   └── utils/
            ├── csrc/
            └── README.md
        """
        # Build nested tree structure from paths
        tree = {}
        for item in results:
            path = item.get("path", item.get("name", ""))
            node_type = (
                item.get("type", ["Unknown"])[0]
                if isinstance(item.get("type"), list)
                else item.get("type", "Unknown")
            )

            # Skip if no path or path is just "." (root itself)
            if not path or path == "." or path == "./":
                continue

            # Normalize path: remove leading "./" and split
            path = path.lstrip("./")
            if not path:
                continue

            # Split path into parts and filter empty
            parts = [p for p in path.replace("\\", "/").split("/") if p and p != "."]
            if not parts:
                continue

            # Navigate/create tree structure
            current = tree
            for i, part in enumerate(parts):
                if part not in current:
                    is_last = i == len(parts) - 1
                    current[part] = {
                        "_is_dir": not is_last or node_type == "Folder",
                        "_type": node_type if is_last else "Folder",
                        "_children": {},
                    }
                current = current[part]["_children"]

        # Generate ASCII tree
        lines = [f"{root_name}/"]

        def _render_tree(node: dict, prefix: str = "") -> list[str]:
            """Recursively render tree with proper ASCII connectors."""
            result = []
            # Sort: directories first, then files, alphabetically
            items = sorted(
                node.items(),
                key=lambda x: (not x[1].get("_is_dir", False), x[0].lower()),
            )

            for i, (name, info) in enumerate(items):
                is_last = i == len(items) - 1
                connector = "└── " if is_last else "├── "
                suffix = "/" if info.get("_is_dir") else ""

                result.append(f"{prefix}{connector}{name}{suffix}")

                # Recurse into children
                if info.get("_children"):
                    child_prefix = prefix + ("    " if is_last else "│   ")
                    result.extend(_render_tree(info["_children"], child_prefix))

            return result

        lines.extend(_render_tree(tree))

        # Limit output to prevent overwhelming responses
        MAX_LINES = 1000
        if len(lines) > MAX_LINES:
            lines = lines[:MAX_LINES]
            lines.append(f"... and {len(results) - MAX_LINES + 1} more items")

        return "\n".join(lines)

    def _query_project_children(
        self, project_name: str, depth: int
    ) -> list[dict[str, Any]]:
        """Query children of a Project node (returns Folder, File)."""
        max_depth = min(depth, 10) if depth > 0 else 10

        if depth == 1:
            query = """
            MATCH (p:Project {name: $project_name})-[:CONTAINS_FOLDER|CONTAINS_FILE]->(child)
            RETURN properties(child) AS props, labels(child) AS type
            """
        else:
            query = f"""
            MATCH path = (p:Project {{name: $project_name}})-[:CONTAINS_FOLDER|CONTAINS_FILE*1..{max_depth}]->(child)
            WHERE child:Folder OR child:File
            RETURN properties(child) AS props, labels(child) AS type, length(path) AS depth
            """

        results = self.ingestor.fetch_all(query, {"project_name": project_name})
        return self._format_structural_results(results, project_name, "Project")

    def _query_folder_by_qualified_name(
        self, folder_qn: str, depth: int
    ) -> list[dict[str, Any]]:
        """Query children of a Folder node by qualified_name (for package folders with is_package=true)."""
        max_depth = min(depth, 10) if depth > 0 else 10

        if depth == 1:
            query = """
            MATCH (pkg:Folder {qualified_name: $folder_qn})-[:CONTAINS_FOLDER|CONTAINS_FILE]->(child)
            RETURN properties(child) AS props, labels(child) AS type
            """
        else:
            query = f"""
            MATCH path = (pkg:Folder {{qualified_name: $folder_qn}})-[:CONTAINS_FOLDER|CONTAINS_FILE*1..{max_depth}]->(child)
            WHERE child:Folder OR child:File
            RETURN properties(child) AS props, labels(child) AS type, length(path) AS depth
            """

        results = self.ingestor.fetch_all(query, {"folder_qn": folder_qn})
        return self._format_structural_results(results, folder_qn, "Folder")

    def _query_folder_children(
        self, folder_path: str, depth: int
    ) -> list[dict[str, Any]]:
        """Query children of a Folder node (returns Folder, File).

        Uses qualified_name as the primary identifier for project isolation.
        Falls back to path matching with STARTS WITH for broader matches.

        Args:
            folder_path: Path to query (can be qualified_name like "atcode/backend"
                         or relative path like "backend")
            depth: Depth to traverse (1 = direct children only)
        """
        max_depth = min(depth, 10) if depth > 0 else 10

        # Normalize path: ensure consistent format
        normalized_path = folder_path.rstrip("/")

        if depth == 1:
            # For depth=1, try exact match first, then STARTS WITH for subfolders
            query = """
            MATCH (f:Folder)
            WHERE f.qualified_name = $folder_path
                 OR f.path = $folder_path
                 OR f.qualified_name = $folder_path_with_slash
                 OR f.path = $folder_path_with_slash
            MATCH (f)-[:CONTAINS_FOLDER|CONTAINS_FILE]->(child)
            RETURN properties(child) AS props, labels(child) AS type
            """
        else:
            query = f"""
            MATCH (f:Folder)
            WHERE f.qualified_name = $folder_path
                 OR f.path = $folder_path
                 OR f.qualified_name = $folder_path_with_slash
                 OR f.path = $folder_path_with_slash
            MATCH path = (f)-[:CONTAINS_FOLDER|CONTAINS_FILE*1..{max_depth}]->(child)
            WHERE child:Folder OR child:File
            RETURN properties(child) AS props, labels(child) AS type, length(path) AS depth
            """

        params = {
            "folder_path": normalized_path,
            "folder_path_with_slash": normalized_path + "/",
        }
        results = self.ingestor.fetch_all(query, params)

        # If no results with exact match, try STARTS WITH for broader matching
        if not results:
            logger.debug(
                f"[_query_folder_children] No exact match for {folder_path}, trying STARTS WITH"
            )
            if depth == 1:
                query = """
                MATCH (f:Folder)
                WHERE f.qualified_name STARTS WITH $folder_path_prefix
                     OR f.path STARTS WITH $folder_path_prefix
                MATCH (f)-[:CONTAINS_FOLDER|CONTAINS_FILE]->(child)
                RETURN properties(child) AS props, labels(child) AS type
                LIMIT 100
                """
            else:
                query = f"""
                MATCH (f:Folder)
                WHERE f.qualified_name STARTS WITH $folder_path_prefix
                     OR f.path STARTS WITH $folder_path_prefix
                MATCH path = (f)-[:CONTAINS_FOLDER|CONTAINS_FILE*1..{max_depth}]->(child)
                WHERE child:Folder OR child:File
                RETURN properties(child) AS props, labels(child) AS type, length(path) AS depth
                LIMIT 200
                """
            results = self.ingestor.fetch_all(
                query, {"folder_path_prefix": normalized_path + "/"}
            )
            if results:
                logger.debug(
                    f"[_query_folder_children] Found {len(results)} children with STARTS WITH match"
                )

        return self._format_structural_results(results, folder_path, "Folder")

    def _format_structural_results(
        self, results: list[dict[str, Any]], parent_id: str, parent_type: str
    ) -> list[dict[str, Any]]:
        """Format structural query results into consistent structure."""
        formatted = []
        for result in results:
            props = result.get("props", {}) or {}
            node_dict = {
                "name": props.get("name", "N/A"),
                "type": result.get("type", ["Unknown"]),
                "depth": result.get("depth", 1),
                "parent_type": parent_type,
                "parent_identifier": parent_id,
            }
            # Add path or qualified_name based on node type
            if props.get("path"):
                node_dict["path"] = props["path"]
            if props.get("qualified_name"):
                node_dict["qualified_name"] = props["qualified_name"]
            if props.get("extension"):
                node_dict["extension"] = props["extension"]
            formatted.append(node_dict)
        return formatted

    @handle_query_errors
    def find_class_hierarchy(self, class_qualified_name: str) -> QueryResult:
        """Find the inheritance hierarchy of a class (parents and children)."""
        # Add project prefix if not present
        if not class_qualified_name.startswith(self.project_prefix):
            class_qualified_name = f"{self.project_prefix}{class_qualified_name}"

        query = """
        MATCH (c:Class)
        WHERE c.qualified_name = $qn
        OPTIONAL MATCH (c)-[:INHERITS]->(parent:Class)
        OPTIONAL MATCH (child:Class)-[:INHERITS]->(c)
        RETURN c.qualified_name AS class_name,
               COLLECT(DISTINCT parent.qualified_name) AS parents,
               COLLECT(DISTINCT child.qualified_name) AS children
        """

        results = self.ingestor.fetch_all(query, {"qn": class_qualified_name})
        return QueryResult(
            success=True,
            results=results,
            count=len(results),
            summary=f"Found inheritance hierarchy for '{class_qualified_name}'",
            query_used=query,
        )

    @handle_query_errors
    def find_module_imports(self, module_path_or_qn: str) -> QueryResult:
        """Find all files imported by a specific file."""
        query = """
        MATCH (f:File)-[:IMPORTS]->(imported:File)
        WHERE f.path CONTAINS $identifier OR f.qualified_name CONTAINS $identifier
        RETURN imported.qualified_name AS qualified_name,
               imported.name AS name,
               imported.path AS path,
               imported.start_line AS start_line,
               imported.end_line AS end_line
        LIMIT 100
        """

        results = self.ingestor.fetch_all(query, {"identifier": module_path_or_qn})
        return QueryResult(
            success=True,
            results=results,
            count=len(results),
            summary=f"Found {len(results)} import(s) for file matching '{module_path_or_qn}'",
            query_used=query,
        )

    @handle_query_errors
    def find_external_dependencies(self) -> QueryResult:
        """Find all external package dependencies of the project."""
        query = """
        MATCH (p:Project)-[:DEPENDS_ON_EXTERNAL]->(ext:ExternalPackage)
        WHERE p.name = $project_name
        RETURN ext.name AS name,
               ext.version_spec AS version
        ORDER BY ext.name
        """

        results = self.ingestor.fetch_all(query, {"project_name": self.project_name})
        return QueryResult(
            success=True,
            results=results,
            count=len(results),
            summary=f"Found {len(results)} external dependencies",
            query_used=query,
        )

    def map_relationships_between_results(
        self, qualified_names: list[str], relationship_types: list[str] | None = None
    ) -> dict[str, Any]:
        """Map relationships between a set of code elements to identify entry points vs dependencies.

        OPTIMIZATION:
        - Limited to 100 qualified names to prevent memory issues
        - Uses CALLS relationship by default (most common use case)
        - Additional relationship types only checked when explicitly requested
        """
        # Default to CALLS and BINDS_TO for performance while preserving pybind functionality
        # BINDS_TO is critical for Python-C++ binding tracing
        if relationship_types is None:
            relationship_types = ["CALLS", "BINDS_TO"]

        if not qualified_names or len(qualified_names) < 2:
            return {
                "entry_points": qualified_names if qualified_names else [],
                "hierarchy_tree": "",
                "summary": "Single element - no relationship mapping needed.",
            }

        # OPTIMIZATION: Limit number of qualified names to prevent memory issues
        MAX_QN_COUNT = 100
        if len(qualified_names) > MAX_QN_COUNT:
            logger.warning(
                f"[RelationshipMapper] Truncating {len(qualified_names)} to {MAX_QN_COUNT} qualified names"
            )
            qualified_names = qualified_names[:MAX_QN_COUNT]

        logger.info(
            f"[RelationshipMapper] Mapping relationships between {len(qualified_names)} elements"
        )

        # Build query - use specific relationship types for better performance
        rel_types_str = "|".join(relationship_types)
        query = f"""
        MATCH (source)-[r:{rel_types_str}]->(target)
        WHERE source.qualified_name IN $qn_list
          AND target.qualified_name IN $qn_list
        RETURN DISTINCT source.qualified_name AS from_qn,
               target.qualified_name AS to_qn,
               type(r) AS rel_type
        """

        try:
            results = self.ingestor.fetch_all(query, {"qn_list": qualified_names})

            # Build dependency graph (internal use only)
            dependency_graph = {qn: [] for qn in qualified_names}
            targets_set = set()  # Nodes that are targets (dependencies)

            for result in results:
                from_qn = result.get("from_qn")
                to_qn = result.get("to_qn")

                if from_qn and to_qn:
                    dependency_graph[from_qn].append(to_qn)
                    targets_set.add(to_qn)

            # Entry points are nodes that are NOT targets of any relationship
            entry_points = [qn for qn in qualified_names if qn not in targets_set]

            # Build hierarchy display in simplified format: - root : [child1, child2, ..., childx]
            def get_parent_base_name(qualified_name: str) -> str:
                """Extract parent.base_name format from qualified_name."""
                parts = qualified_name.split(".")
                if len(parts) >= 2:
                    return ".".join(parts[-2:])  # parent.base_name
                return parts[-1] if parts else qualified_name

            tree_lines = []
            tree_lines.append("DEPENDENCY HIERARCHY:")

            # Display all nodes to ensure nothing is missing
            displayed_nodes = set()

            # Show entry points first (nodes that are not targets of any relationship)
            if entry_points:
                for ep in entry_points:
                    parent_base_name = get_parent_base_name(ep)
                    direct_children = dependency_graph.get(ep, [])

                    if direct_children:
                        # Format: - parent.base_name : [child1, child2, ..., childx]
                        child_names = [
                            get_parent_base_name(child) for child in direct_children
                        ]
                        tree_lines.append(
                            f"- {parent_base_name} : [{', '.join(child_names)}]"
                        )
                    else:
                        # No dependencies
                        tree_lines.append(f"- {parent_base_name} : []")

                    displayed_nodes.add(ep)

            # Show any remaining nodes that weren't displayed as entry points
            # These are nodes that are dependencies but not entry points
            remaining_nodes = [
                qn for qn in qualified_names if qn not in displayed_nodes
            ]
            if remaining_nodes:
                logger.info(
                    f"[RelationshipMapper] Found {len(remaining_nodes)} nodes not yet displayed, adding them to hierarchy"
                )
                for qn in remaining_nodes:
                    parent_base_name = get_parent_base_name(qn)
                    direct_children = dependency_graph.get(qn, [])

                    if direct_children:
                        child_names = [
                            get_parent_base_name(child) for child in direct_children
                        ]
                        tree_lines.append(
                            f"- {parent_base_name} : [{', '.join(child_names)}]"
                        )
                    else:
                        tree_lines.append(f"- {parent_base_name} : []")

                    displayed_nodes.add(qn)

            # Verify all nodes are displayed
            if len(displayed_nodes) != len(qualified_names):
                missing = set(qualified_names) - displayed_nodes
                logger.warning(
                    f"[RelationshipMapper] WARNING: {len(missing)} nodes not displayed in hierarchy: {missing}"
                )
            else:
                logger.info(
                    f"[RelationshipMapper] All {len(qualified_names)} nodes successfully displayed in hierarchy"
                )

            hierarchy_tree = "\n".join(tree_lines)

            # Build concise, actionable summary
            if not results:
                summary = (
                    f"✓ Found {len(qualified_names)} elements with NO direct relationships. "
                    f"All elements are independent - analyze each separately."
                )
            else:
                entry_point_names = [ep.split(".")[-1] for ep in entry_points[:]]

                summary = (
                    f"✓ Found {len(entry_points)} ENTRY POINT(S): {', '.join(entry_point_names)}. "
                    f"These are the top-level functions - others are implementation details. "
                    f"RECOMMENDATION: Focus your analysis ONLY on the entry point(s) listed above. "
                    f"They provide the complete interface; dependencies will be explored automatically."
                )

            logger.info(f"[RelationshipMapper] {summary}")

            return {
                "entry_points": entry_points,
                "hierarchy_tree": hierarchy_tree,
                "summary": summary,
            }

        except Exception as e:
            logger.error(f"[RelationshipMapper] Error mapping relationships: {e}")
            return {
                "entry_points": qualified_names,
                "hierarchy_tree": "",
                "summary": f"Error mapping relationships: {e}. Analyze all elements.",
            }

    _DEFAULT_ENRICHMENT_REL_TYPES: list[str] = [
        "CALLS",
        "IMPORTS",
        "INHERITS",
        "DEFINES",
        "DEFINES_METHOD",
        "CONTAINS",
        "BINDS_TO",
    ]

    def enrich_query_result_with_relationships(
        self,
        query_result: QueryResult,
        relationship_types: list[str] | None = None,
    ) -> QueryResult:
        """Enrich a QueryResult with relationship mapping."""
        if relationship_types is None:
            relationship_types = self._DEFAULT_ENRICHMENT_REL_TYPES

        # Only enrich if we have multiple results
        if query_result.count < 2:
            return query_result

        # Extract qualified names from results
        qualified_names = [
            result.get("qualified_name")
            for result in query_result.results
            if result.get("qualified_name")
        ]

        if len(qualified_names) < 2:
            return query_result

        # Map relationships
        mapping = self.map_relationships_between_results(
            qualified_names, relationship_types
        )

        # Update query result
        query_result.entry_points = mapping["entry_points"]
        query_result.hierarchy_tree = mapping["hierarchy_tree"]
        query_result.has_relationship_mapping = True

        # Enhance summary
        if mapping["entry_points"]:
            query_result.summary += f" | {mapping['summary']}"

        return query_result


# ======================================================================================
#  INPUT SCHEMAS FOR STRUCTURED TOOLS
# ======================================================================================


class FindNodesInput(BaseModel):
    """Input schema for find_nodes tool - simplified for better usability."""

    query: str = Field(
        description="""Search query. Supports multiple syntaxes:
- Single keyword: 'attention'
- AND (space): 'triton attention' → must contain BOTH (more precise)
- AND (explicit &): 'triton&att' → must contain BOTH
- OR (explicit |): 'flash|attn' → contains EITHER (parallel/broader)
- Glob: 'flash*attn' → regex 'flash.*attn'
- Regex: '.*fused.*moe.*'

💡 Tip: Use AND (& or space) for precision, OR (|) for broader search."""
    )
    search_strategy: Literal["exact", "pattern", "regex", "and", "auto"] = Field(
        default="auto",
        description="Search strategy. 'auto' (recommended) detects: space→AND, &→AND, |→OR, glob→regex, qualified_name→exact",
    )
    node_type: Literal["Code", "All"] | None = Field(
        default=None,
        description="Node type filter: 'Code' (default) = Function+Method+Class, 'All' = no filter. Omit for Code.",
    )
    case_sensitive: bool = Field(default=False, description="Case sensitive search")
    decorator_filter: str | None = Field(
        default=None, description="Filter by decorator name"
    )
    auto_map_relationships: bool = Field(
        default=True, description="Auto-map relationships between results"
    )
    repo_name: str | None = Field(
        default=None,
        description="Repository scope: None=current repo, '<name>'=specific repo, 'all'=all repos (slow!)",
    )


class FindCallsInput(BaseModel):
    """Input schema for unified find_calls tool."""

    qualified_name: str = Field(description="Qualified name of the function/method")
    direction: Literal["outgoing", "incoming"] = Field(
        default="outgoing",
        description="Direction: 'outgoing' = what this function calls, 'incoming' = what calls this function",
    )
    depth: int = Field(default=1, ge=1, le=5, description="Search depth (1-5)")


class GetChildrenInput(BaseModel):
    """Input schema for get_children tool."""

    identifier: str = Field(
        description="Identifier for the parent node: project name, folder path/qualified_name, file path/qualified_name, or class qualified_name"
    )
    identifier_type: Literal["auto", "project", "folder", "file", "class"] = Field(
        default="auto",
        description="Type of the parent node. Use 'auto' to detect automatically. Note: Package folders are Folder nodes with is_package=true; source code files (modules) are File nodes with qualified_name.",
    )
    depth: int = Field(
        default=1, ge=1, le=5, description="Depth of children to retrieve (1-5)"
    )
    child_types: str | None = Field(
        default=None,
        description="Filter by type (comma-separated): 'Folder,File' or 'Class,Function'. Options: Folder, File, Class, Function, Method",
    )


class FindClassHierarchyInput(BaseModel):
    """Input schema for find_class_hierarchy tool."""

    class_qualified_name: str = Field(description="Qualified name of the class")


class TraceDependenciesInput(BaseModel):
    """Input schema for trace_dependencies tool."""

    start_qualified_name: str = Field(description="Starting function/class qualified name")
    end_qualified_name: str | None = Field(
        default=None, description="Optional target qualified name"
    )
    max_depth: int = Field(default=5, description="Max traversal depth (1-10)")
    relationship_type: str = Field(
        default="CALLS",
        description='Relationship type: "CALLS", "IMPORTS", or "INHERITS"',
    )


class FindFileImportsInput(BaseModel):
    """Input schema for find_module_imports tool."""

    module_path_or_qn: str = Field(description="File path or qualified name")


class MapRelationshipsInput(BaseModel):
    """Input schema for map_relationships_between_results tool."""

    qualified_names: list[str] = Field(
        description="List of qualified names to analyze (2-100 items)"
    )
    relationship_types: list[str] | None = Field(
        default=None,
        description="Relationship types to check. Default: ['CALLS', 'BINDS_TO']",
    )


# ======================================================================================
#  TOOL CREATION FUNCTIONS (Native LangChain Format)
# ======================================================================================


def create_all_graph_query_tools(
    ingestor: MemgraphIngestor, project_name: str
) -> list[BaseTool]:
    """
    Create ALL graph query tools in native LangChain format.

    This function returns ALL available tools. Orchestrators should select
    the tools they need from this list.

    Args:
        ingestor: MemgraphIngestor instance for database operations
        project_name: Project name for scoping queries

    Returns:
        List of LangChain BaseTool objects (StructuredTool)
    """
    query_tools = GraphQueryTools(ingestor, project_name)
    tools: list[BaseTool] = []

    # Tool 0: list_repos
    tools.append(
        StructuredTool.from_function(
            func=query_tools.list_repos,
            name="list_repos",
            description=TOOL_DESCRIPTIONS["list_repos"],
        )
    )

    # Tool 1: find_nodes (UNIFIED SEARCH - Simplified)
    tools.append(
        StructuredTool.from_function(
            func=query_tools.find_nodes,
            name="find_nodes",
            description=TOOL_DESCRIPTIONS["find_nodes"],
            args_schema=FindNodesInput,
        )
    )

    # Tool 2: find_calls
    tools.append(
        StructuredTool.from_function(
            func=query_tools.find_calls,
            name="find_calls",
            description=TOOL_DESCRIPTIONS["find_calls"],
            args_schema=FindCallsInput,
        )
    )

    # Tool 3: get_children
    tools.append(
        StructuredTool.from_function(
            func=query_tools.get_children,
            name="get_children",
            description=TOOL_DESCRIPTIONS["get_children"],
            args_schema=GetChildrenInput,
        )
    )

    # Tool 4: find_class_hierarchy
    tools.append(
        StructuredTool.from_function(
            func=query_tools.find_class_hierarchy,
            name="find_class_hierarchy",
            description=TOOL_DESCRIPTIONS["find_class_hierarchy"],
            args_schema=FindClassHierarchyInput,
        )
    )

    # Tool 5: find_module_imports
    tools.append(
        StructuredTool.from_function(
            func=query_tools.find_module_imports,
            name="find_module_imports",
            description=TOOL_DESCRIPTIONS["find_module_imports"],
            args_schema=FindFileImportsInput,
        )
    )

    # Tool 6: find_external_dependencies
    tools.append(
        StructuredTool.from_function(
            func=query_tools.find_external_dependencies,
            name="find_external_dependencies",
            description=TOOL_DESCRIPTIONS["find_external_dependencies"],
        )
    )

    # Tool 7: trace_dependencies
    from .code_tools import CodeExplorer

    explorer = CodeExplorer(ingestor, project_name)

    def trace_dependencies(
        start_qualified_name: str,
        end_qualified_name: str | None = None,
        max_depth: int = 5,
        relationship_type: str = "CALLS",
    ) -> str:
        """Trace dependency path between two code elements."""
        # Ensure qualified names have project prefix
        start_qn = start_qualified_name
        if not start_qn.startswith(f"{project_name}."):
            start_qn = f"{project_name}.{start_qn}"
        end_qn = None
        if end_qualified_name:
            end_qn = end_qualified_name
            if not end_qn.startswith(f"{project_name}."):
                end_qn = f"{project_name}.{end_qn}"

        try:
            result = explorer.trace_dependency_chain(
                from_qualified_name=start_qn,
                to_qualified_name=end_qn,
                max_depth=min(max_depth, 10),
                relationship_type=relationship_type,
                detect_circular=True,
            )
            # Serialize dataclass-like result
            if hasattr(result, "__dict__"):
                return json.dumps(result.__dict__, default=str)
            return json.dumps(result, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})

    tools.append(
        StructuredTool.from_function(
            func=trace_dependencies,
            name="trace_dependencies",
            description=TOOL_DESCRIPTIONS["trace_dependencies"],
            args_schema=TraceDependenciesInput,
        )
    )

    return tools

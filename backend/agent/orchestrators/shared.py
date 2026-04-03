# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from loguru import logger

# =============================================================================
# SHARED REDUCERS
# =============================================================================


def max_value(left: Any, right: Any) -> Any:
    """Reducer that takes the maximum value (for progress tracking)."""
    return max(left, right)


def last_value(left: Any, right: Any) -> Any:
    """Reducer that takes the last value (for fields that shouldn't conflict)."""
    return right


# =============================================================================
# CONSTANTS
# =============================================================================

# Tool names that return structured QueryResult objects (for node exploration)
# Consolidated to 6 core graph query tools + utility tools
GRAPH_QUERY_TOOLS = {
    # Core 6 graph query tools
    "find_nodes",  # Universal search with filters
    "find_calls",  # Unified call graph (direction: outgoing/incoming)
    "get_children",  # Unified structure/code navigator
    "find_class_hierarchy",  # Inheritance chain
    "find_module_imports",  # Import relationships
    "find_external_dependencies",  # Third-party dependencies
    # Utility tools
    "list_repos",
}

# File patterns to exclude from code block results
# These are typically bundled/minified files that shouldn't be in code blocks
EXCLUDED_FILE_PATTERNS = {
    # Bundled JavaScript
    ".bundle.js",
    "-bundle.js",
    ".min.js",
    # Bundled CSS
    ".bundle.css",
    ".min.css",
    # Static assets directories
    "/static/",
    "/assets/",
    "/vendor/",
    "/dist/",
    # Common bundled libraries
    "swagger-ui",
    "jquery",
    "bootstrap",
    "react.production",
    # Source maps
    ".map",
}


# =============================================================================
# NODE UTILITIES
# =============================================================================


def is_excluded_file(file_path: str | None) -> bool:
    """Check if a file path matches any excluded patterns (bundled/minified files)."""
    if not file_path:
        return False
    file_path_lower = file_path.lower()
    return any(pattern in file_path_lower for pattern in EXCLUDED_FILE_PATTERNS)


def is_valid_node(node) -> bool:
    """Check if node is a valid dictionary with qualified_name."""
    return isinstance(node, dict) and bool(node.get("qualified_name"))


def extract_qualified_names(nodes: list) -> set[str]:
    """Extract qualified names from a list of nodes."""
    return {
        node.get("qualified_name")
        for node in nodes
        if is_valid_node(node) and node.get("qualified_name")
    }


def normalize_node_type(node_type) -> str:
    """Normalize node type to string format."""
    if isinstance(node_type, list):
        return node_type[0] if node_type else "Unknown"
    return str(node_type) if node_type else "Unknown"


def resolve_node_path(node: dict, ingestor) -> tuple[str | None, bool]:
    """Resolve and validate node path with fallback to database lookup."""
    node_path = node.get("path")
    start_line = node.get("start_line")
    end_line = node.get("end_line")

    has_source_location = bool(
        node_path and start_line is not None and end_line is not None
    )

    # Try to find path from database if missing but we have line numbers
    if not has_source_location and start_line is not None and end_line is not None:
        try:
            path_results = ingestor.fetch_all(
                "MATCH (n) WHERE n.qualified_name = $qn OPTIONAL MATCH (f:File)-[:DEFINES]->(n) RETURN COALESCE(n.path, f.path) AS path LIMIT 1",
                {"qn": node["qualified_name"]},
            )
            if path_results and path_results[0].get("path"):
                node_path = path_results[0]["path"]
                has_source_location = True
        except Exception:
            pass

    return node_path, has_source_location


# =============================================================================
# CODE RETRIEVAL
# =============================================================================


def retrieve_code_from_file(
    repo_path: Path, file_path: str, start_line: int, end_line: int
) -> str | None:
    """Retrieve code from file system using path and line numbers."""
    try:
        if not Path(file_path).is_absolute():
            full_path = repo_path / file_path
        else:
            full_path = Path(file_path)

        if not full_path.exists():
            return None

        with open(full_path, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        if start_line > 0 and end_line <= len(lines):
            return "".join(lines[start_line - 1 : end_line])
    except Exception:
        pass
    return None


def retrieve_code_cross_repo(
    repo_path: Path,
    qualified_name: str | None,
    file_path: str | None,
    start_line: int | None,
    end_line: int | None,
    ingestor=None,
) -> str | None:
    """
    Retrieve code with cross-repo support.

    Extracts repo name from qualified_name and uses the correct repo path.
    Qualified names follow format: RepoName.module.submodule.ClassName.method_name

    Args:
        repo_path: Current repository path
        qualified_name: Qualified name of the node
        file_path: Relative file path
        start_line: Start line number
        end_line: End line number
        ingestor: Optional MemgraphIngestor for looking up project paths
    """
    if not file_path or not start_line or not end_line:
        return None

    # Determine which repo to use
    actual_repo_path = repo_path
    if qualified_name and "." in qualified_name:
        repo_name_from_qn = qualified_name.split(".")[0]
        current_repo_name = repo_path.name

        # Check if it's a cross-repo reference
        if repo_name_from_qn != current_repo_name:
            # Try to get other repo path from database first (supports local paths)
            if ingestor:
                try:
                    other_project_path = ingestor.get_project_path(repo_name_from_qn)
                    if other_project_path:
                        actual_repo_path = Path(other_project_path)
                        # Check if path exists, if not try new data directory
                        if not actual_repo_path.exists():
                            from core.config import get_wiki_repos_dir

                            new_path = get_wiki_repos_dir() / repo_name_from_qn
                            if new_path.exists():
                                logger.debug(
                                    f"Old path {actual_repo_path} not found, using new path {new_path}"
                                )
                                actual_repo_path = new_path
                        logger.debug(
                            f"Cross-repo code retrieval (from db): {current_repo_name} -> {repo_name_from_qn}"
                        )
                except Exception:
                    pass

            # Fallback to sibling directory or centralized data directory
            if actual_repo_path == repo_path:
                # First try sibling directory
                other_repo_path = repo_path.parent / repo_name_from_qn
                if other_repo_path.exists():
                    actual_repo_path = other_repo_path
                    logger.debug(
                        f"Cross-repo code retrieval (fallback sibling): {current_repo_name} -> {repo_name_from_qn}"
                    )
                else:
                    # Try centralized data directory
                    from core.config import get_wiki_repos_dir

                    data_repo_path = get_wiki_repos_dir() / repo_name_from_qn
                    if data_repo_path.exists():
                        actual_repo_path = data_repo_path
                        logger.debug(
                            f"Cross-repo code retrieval (fallback data dir): {current_repo_name} -> {repo_name_from_qn}"
                        )

    return retrieve_code_from_file(actual_repo_path, file_path, start_line, end_line)


# =============================================================================
# NODE PROCESSING
# =============================================================================


def process_nodes_to_explored(
    nodes: list[dict[str, Any]], tool_name: str, ingestor, repo_path: Path
) -> list[dict[str, Any]]:
    """Process raw nodes into explored nodes with code retrieval."""
    valid_nodes = [node for node in nodes if is_valid_node(node)]

    def _create_explored_node(node):
        qualified_name = node["qualified_name"]
        node_path, has_source_location = resolve_node_path(node, ingestor)
        start_line = node.get("start_line")
        end_line = node.get("end_line")

        # Retrieve code if possible
        code_content = None
        if has_source_location and node_path:
            try:
                code_content = retrieve_code_from_file(
                    repo_path, node_path, start_line, end_line
                )
            except Exception:
                pass

        return {
            "qualified_name": qualified_name,
            "type": normalize_node_type(node.get("type")),
            "path": node_path or node.get("path", ""),
            "start_line": start_line,
            "end_line": end_line,
            "has_code": has_source_location and node_path is not None,
            "has_docstring": bool(node.get("docstring")),
            "docstring": node.get("docstring"),
            "decorators": node.get("decorators", []),
            "code": code_content,
            "tool_used": tool_name,
            "timestamp": None,
        }

    return [_create_explored_node(node) for node in valid_nodes]


# =============================================================================
# NODE MATCHING
# =============================================================================


def match_node_flexible(
    node_id: str, explored_nodes: list, ingestor, repo_path: Path
) -> dict | None:
    """
    Flexible node matching strategy shared between chat and documentation generation.

    Matching strategies in priority order:
    1. Exact parent.basename match (e.g., "cache.reshape_and_cache_flash")
    2. Basename suffix match (e.g., "reshape_and_cache_flash")
    3. Exact qualified_name match
    4. Graph database query with regex/pattern matching

    Note: If a matched node from explored_nodes is missing path/start_line/end_line,
    we fall back to database query to get complete information.

    Args:
        node_id: Node identifier to match (can be parent.basename or qualified_name)
        explored_nodes: List of explored node dictionaries
        ingestor: MemgraphIngestor for database queries
        repo_path: Repository root path for code retrieval

    Returns:
        Matched node dictionary with code, or None if not found
    """
    # Normalize node_id: remove common file extensions if present
    # Users often reference files as [[module.py]] but qualified_name doesn't include .py
    original_node_id = node_id
    for ext in [".py", ".pyx", ".so", ".cpp", ".h", ".hpp", ".cc", ".c"]:
        if node_id.endswith(ext):
            node_id = node_id[: -len(ext)]
            logger.debug(
                f"Stripped file extension: '{original_node_id}' -> '{node_id}'"
            )
            break

    # Normalize path-style identifiers: "release/" → "release", "testcase/sitest/" → "testcase/sitest"
    node_id = node_id.strip("/")

    # Convert path separators to dots for qualified_name matching:
    # "testcase/sitest" → "testcase.sitest"
    if "/" in node_id and "." not in node_id:
        node_id = node_id.replace("/", ".")
        logger.debug(
            f"Converted path to qualified_name: '{original_node_id}' -> '{node_id}'"
        )

    def _is_node_complete(node: dict) -> bool:
        """Check if node has essential location information."""
        return bool(
            node.get("path")
            and node.get("start_line") is not None
            and node.get("end_line") is not None
        )

    def _query_node_from_db(qualified_name: str) -> dict | None:
        """Query node from database with complete information."""
        try:
            cypher_query = """
            MATCH (n)
            WHERE n.qualified_name = $qn
            OPTIONAL MATCH (f:File)-[:DEFINES]->(n)
            RETURN n.qualified_name AS qualified_name,
                   n.name AS name,
                   labels(n) AS type,
                   COALESCE(n.path, f.path) AS path,
                   n.start_line AS start_line,
                   n.end_line AS end_line,
                   n.code AS code,
                   n.docstring AS docstring
            LIMIT 1
            """
            results = ingestor.fetch_all(cypher_query, {"qn": qualified_name})
            if results:
                node = results[0]
                # Retrieve code if not present but we have location info (cross-repo aware)
                if (
                    not node.get("code")
                    and node.get("path")
                    and node.get("start_line")
                    and node.get("end_line")
                ):
                    code = retrieve_code_cross_repo(
                        repo_path,
                        node.get("qualified_name"),
                        node.get("path"),
                        node.get("start_line"),
                        node.get("end_line"),
                    )
                    if code:
                        node["code"] = code
                return node
        except Exception as e:
            logger.warning(f"Failed to query node '{qualified_name}' from DB: {e}")
        return None

    # Create mapping for quick lookup
    qualified_name_to_node = {
        node.get("qualified_name"): node
        for node in explored_nodes
        if isinstance(node, dict) and node.get("qualified_name")
    }

    # For parent.basename matching
    basename_to_nodes = {}
    for node in explored_nodes:
        if isinstance(node, dict) and node.get("qualified_name"):
            qualified_name = node["qualified_name"]
            parts = qualified_name.split(".")
            if len(parts) >= 2:
                parent_basename = f"{parts[-2]}.{parts[-1]}"
                if parent_basename not in basename_to_nodes:
                    basename_to_nodes[parent_basename] = []
                basename_to_nodes[parent_basename].append(node)

    # Strategy 1: Exact parent.basename match (highest priority)
    if "." in node_id and node_id in basename_to_nodes:
        candidates = basename_to_nodes[node_id]
        if len(candidates) == 1:
            matched = candidates[0]
            logger.debug(
                f"Strategy 1: Exact parent.basename match for '{node_id}' -> '{matched.get('qualified_name')}'"
            )
            # If node is incomplete, query from DB
            if not _is_node_complete(matched):
                db_node = _query_node_from_db(matched.get("qualified_name"))
                if db_node and _is_node_complete(db_node):
                    logger.debug("  -> Enriched from DB (was missing path/line info)")
                    return db_node
            return matched
        elif len(candidates) > 1:
            # Choose the shortest qualified_name (usually most relevant)
            best_candidate = min(
                candidates, key=lambda x: len(x.get("qualified_name", ""))
            )
            logger.debug(
                f"Strategy 1: Multiple matches for '{node_id}', using shortest: '{best_candidate.get('qualified_name')}'"
            )
            if not _is_node_complete(best_candidate):
                db_node = _query_node_from_db(best_candidate.get("qualified_name"))
                if db_node and _is_node_complete(db_node):
                    logger.debug("  -> Enriched from DB (was missing path/line info)")
                    return db_node
            return best_candidate

    # Strategy 2: Basename suffix match
    basename_matches = []
    for qn, node in qualified_name_to_node.items():
        if qn.endswith(f".{node_id}") or qn == node_id:
            basename_matches.append(node)

    if basename_matches:
        if len(basename_matches) == 1:
            matched = basename_matches[0]
            matched_qn = matched.get("qualified_name")
            logger.debug(
                f"Strategy 2: Basename suffix match for '{node_id}' -> '{matched_qn}'"
            )
            if not _is_node_complete(matched):
                db_node = _query_node_from_db(matched_qn)
                if db_node and _is_node_complete(db_node):
                    logger.debug("  -> Enriched from DB (was missing path/line info)")
                    return db_node
            return matched
        else:
            best_match = min(
                basename_matches, key=lambda x: len(x.get("qualified_name", ""))
            )
            logger.debug(
                f"Strategy 2: Multiple basename matches for '{node_id}', using shortest: '{best_match.get('qualified_name')}'"
            )
            if not _is_node_complete(best_match):
                db_node = _query_node_from_db(best_match.get("qualified_name"))
                if db_node and _is_node_complete(db_node):
                    logger.debug("  -> Enriched from DB (was missing path/line info)")
                    return db_node
            return best_match

    # Strategy 3: Exact qualified_name match
    if node_id in qualified_name_to_node:
        matched = qualified_name_to_node[node_id]
        logger.debug(f"Strategy 3: Exact qualified_name match for '{node_id}'")
        if not _is_node_complete(matched):
            db_node = _query_node_from_db(node_id)
            if db_node and _is_node_complete(db_node):
                logger.debug("  -> Enriched from DB (was missing path/line info)")
                return db_node
        return matched

    # Strategy 4: Graph database query
    try:
        if "." in node_id:
            parts = node_id.split(".")
            if len(parts) == 2:
                # Build regex pattern: .*parent\.basename$
                regex_pattern = f".*{re.escape(parts[0])}\\.{re.escape(parts[1])}$"
                logger.debug(
                    f"Strategy 4: Querying graph with regex: '{regex_pattern}'"
                )

                cypher_query = """
                MATCH (n)
                WHERE n.qualified_name IS NOT NULL AND n.qualified_name =~ $pattern
                OPTIONAL MATCH (f:File)-[:DEFINES]->(n)
                RETURN n.qualified_name AS qualified_name,
                       n.name AS name,
                       labels(n) AS type,
                       COALESCE(n.path, f.path) AS path,
                       n.start_line AS start_line,
                       n.end_line AS end_line,
                       n.code AS code,
                       n.docstring AS docstring
                LIMIT 10
                """
                results = ingestor.fetch_all(cypher_query, {"pattern": regex_pattern})

                # Filter to ensure last two parts match exactly AND exclude bundled files
                if results:
                    filtered_nodes = [
                        node
                        for node in results
                        if node.get("qualified_name", "").split(".")[-2:] == parts
                        and not is_excluded_file(node.get("path"))
                    ]
                    if filtered_nodes:
                        best_node = min(
                            filtered_nodes,
                            key=lambda x: len(x.get("qualified_name", "")),
                        )

                        # Retrieve code if not present (cross-repo aware)
                        if not best_node.get("code") and best_node.get("path"):
                            code = retrieve_code_cross_repo(
                                repo_path,
                                best_node.get("qualified_name"),
                                best_node.get("path"),
                                best_node.get("start_line"),
                                best_node.get("end_line"),
                            )
                            if code:
                                best_node["code"] = code

                        logger.debug(
                            f"Strategy 4: Found via graph query: '{best_node.get('qualified_name')}'"
                        )
                        return best_node
            else:
                # Full qualified name - try exact match first, then suffix match
                # Strategy 4a: Exact match
                cypher_query = """
                MATCH (n)
                WHERE n.qualified_name = $qn
                OPTIONAL MATCH (f:File)-[:DEFINES]->(n)
                RETURN n.qualified_name AS qualified_name,
                       n.name AS name,
                       labels(n) AS type,
                       COALESCE(n.path, f.path) AS path,
                       n.start_line AS start_line,
                       n.end_line AS end_line,
                       n.code AS code,
                       n.docstring AS docstring
                LIMIT 1
                """
                results = ingestor.fetch_all(cypher_query, {"qn": node_id})
                if results:
                    best_node = results[0]
                    if not best_node.get("code") and best_node.get("path"):
                        code = retrieve_code_cross_repo(
                            repo_path,
                            best_node.get("qualified_name"),
                            best_node.get("path"),
                            best_node.get("start_line"),
                            best_node.get("end_line"),
                        )
                        if code:
                            best_node["code"] = code
                    logger.debug(
                        f"Strategy 4a: Exact qualified_name match for '{node_id}'"
                    )
                    return best_node

                # Strategy 4b: Suffix match
                logger.debug(f"Strategy 4b: Trying suffix match for '{node_id}'")
                suffix_query = """
                MATCH (n)
                WHERE n.qualified_name IS NOT NULL AND n.qualified_name ENDS WITH $suffix
                OPTIONAL MATCH (f:File)-[:DEFINES]->(n)
                RETURN n.qualified_name AS qualified_name,
                       n.name AS name,
                       labels(n) AS type,
                       COALESCE(n.path, f.path) AS path,
                       n.start_line AS start_line,
                       n.end_line AS end_line,
                       n.code AS code,
                       n.docstring AS docstring
                LIMIT 10
                """
                suffix_results = ingestor.fetch_all(suffix_query, {"suffix": node_id})

                # Filter out excluded files (bundled/minified)
                if suffix_results:
                    suffix_results = [
                        n for n in suffix_results if not is_excluded_file(n.get("path"))
                    ]

                if suffix_results:
                    # Prefer shortest qualified_name (most specific match)
                    best_node = min(
                        suffix_results, key=lambda x: len(x.get("qualified_name", ""))
                    )
                    if not best_node.get("code") and best_node.get("path"):
                        code = retrieve_code_cross_repo(
                            repo_path,
                            best_node.get("qualified_name"),
                            best_node.get("path"),
                            best_node.get("start_line"),
                            best_node.get("end_line"),
                        )
                        if code:
                            best_node["code"] = code
                    logger.debug(
                        f"Strategy 4b: Suffix match for '{node_id}' -> '{best_node.get('qualified_name')}'"
                    )
                    return best_node
        else:
            # Single name - pattern search (use ENDS WITH for more precise matching)
            # Add dot prefix to ensure we match the end of a qualified name segment
            suffix_pattern = f".{node_id}"
            cypher_query = """
            MATCH (n)
            WHERE n.qualified_name IS NOT NULL AND n.qualified_name ENDS WITH $pattern
            OPTIONAL MATCH (f:File)-[:DEFINES]->(n)
            RETURN n.qualified_name AS qualified_name,
                   n.name AS name,
                   labels(n) AS type,
                   COALESCE(n.path, f.path) AS path,
                   n.start_line AS start_line,
                   n.end_line AS end_line,
                   n.code AS code,
                   n.docstring AS docstring
            LIMIT 10
            """
            results = ingestor.fetch_all(cypher_query, {"pattern": suffix_pattern})

            # Filter out excluded files (bundled/minified)
            if results:
                results = [n for n in results if not is_excluded_file(n.get("path"))]

            if results:
                # Prefer shortest qualified_name (most specific match)
                best_node = min(results, key=lambda x: len(x.get("qualified_name", "")))
                if not best_node.get("code") and best_node.get("path"):
                    code = retrieve_code_cross_repo(
                        repo_path,
                        best_node.get("qualified_name"),
                        best_node.get("path"),
                        best_node.get("start_line"),
                        best_node.get("end_line"),
                    )
                    if code:
                        best_node["code"] = code
                logger.debug(
                    f"Strategy 4c: Single name suffix match for '{node_id}' -> '{best_node.get('qualified_name')}'"
                )
                return best_node
    except Exception as e:
        logger.warning(f"Strategy 4 failed for '{node_id}': {e}")

    # Strategy 5: Path-based lookup (for folder/file references like "release/", "scripts/")
    # Convert dots back to slashes to try matching against node.path
    path_candidate = original_node_id.strip("/")
    if path_candidate:
        try:
            path_query = """
            MATCH (n)
            WHERE n.path IS NOT NULL AND (n.path = $path OR n.path = $path_slash)
            RETURN n.qualified_name AS qualified_name,
                   n.name AS name,
                   labels(n) AS type,
                   n.path AS path,
                   n.start_line AS start_line,
                   n.end_line AS end_line,
                   n.code AS code,
                   n.docstring AS docstring
            LIMIT 5
            """
            path_results = ingestor.fetch_all(
                path_query, {"path": path_candidate, "path_slash": path_candidate + "/"}
            )
            if path_results:
                best_node = min(path_results, key=lambda x: len(x.get("qualified_name", "")))
                logger.debug(
                    f"Strategy 5: Path match for '{original_node_id}' -> '{best_node.get('qualified_name')}'"
                )
                return best_node
        except Exception as e:
            logger.warning(f"Strategy 5 failed for '{original_node_id}': {e}")

    # Strategy 6: Basename-only DB search (for LLM-hallucinated qualified names)
    # Extract the last segment (basename) and search by n.name exact match.
    # This handles cases like "grpo_trainer" or "GRPOTrainer.fit" where the
    # LLM invented a qualified path but the basename itself exists in the graph.
    basename = node_id.rsplit(".", 1)[-1] if "." in node_id else node_id
    if basename and len(basename) >= 3:
        try:
            basename_query = """
            MATCH (n)
            WHERE n.name = $name
            OPTIONAL MATCH (f:File)-[:DEFINES]->(n)
            RETURN n.qualified_name AS qualified_name,
                   n.name AS name,
                   labels(n) AS type,
                   COALESCE(n.path, f.path) AS path,
                   n.start_line AS start_line,
                   n.end_line AS end_line,
                   n.code AS code,
                   n.docstring AS docstring
            LIMIT 5
            """
            basename_results = ingestor.fetch_all(basename_query, {"name": basename})
            if basename_results:
                basename_results = [
                    n for n in basename_results if not is_excluded_file(n.get("path"))
                ]
            if basename_results:
                best_node = min(
                    basename_results, key=lambda x: len(x.get("qualified_name") or "")
                )
                if not best_node.get("code") and best_node.get("path"):
                    code = retrieve_code_cross_repo(
                        repo_path,
                        best_node.get("qualified_name"),
                        best_node.get("path"),
                        best_node.get("start_line"),
                        best_node.get("end_line"),
                    )
                    if code:
                        best_node["code"] = code
                logger.debug(
                    f"Strategy 6: Basename match for '{node_id}' (basename='{basename}') -> '{best_node.get('qualified_name')}'"
                )
                return best_node
        except Exception as e:
            logger.warning(f"Strategy 6 failed for '{node_id}': {e}")

    logger.debug(f"No match found for node identifier: '{node_id}'")
    return None


# =============================================================================
# TOOL MESSAGE PROCESSING
# =============================================================================


def get_recent_tool_messages(messages: list) -> list[ToolMessage]:
    """
    Get ToolMessages that come after the most recent AIMessage.

    This is useful for processing tool results in LangGraph workflows
    where we need to extract results from the latest batch of tool calls.

    Args:
        messages: List of BaseMessage objects

    Returns:
        List of ToolMessage objects after the last AIMessage
    """
    last_ai_idx = None
    for i, msg in enumerate(reversed(messages)):
        if isinstance(msg, AIMessage):
            last_ai_idx = len(messages) - 1 - i
            break
    if last_ai_idx is None:
        return []
    return [msg for msg in messages[last_ai_idx + 1 :] if isinstance(msg, ToolMessage)]


def parse_tool_result(content: str) -> dict | None:
    """
    Parse JSON tool result from ToolMessage content.

    Args:
        content: The content string from a ToolMessage

    Returns:
        Parsed dict if valid JSON, None otherwise
    """
    try:
        result = json.loads(content.strip())
        return result if isinstance(result, dict) else None
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


# =============================================================================
# MESSAGE SANITIZATION
# =============================================================================


def _get_tool_call_id(tc) -> str | None:
    """
    Helper to robustly extract a tool_call id from either dicts or objects.

    LangChain / OpenAI tool_calls can be dicts or objects with .id / .tool_call_id.
    """
    if tc is None:
        return None
    if isinstance(tc, dict):
        return tc.get("id") or tc.get("tool_call_id")
    return getattr(tc, "id", None) or getattr(tc, "tool_call_id", None)


def clean_messages_for_inheritance(
    messages: list[BaseMessage],
    remove_system_messages: bool = True,
) -> list[BaseMessage]:
    """
    Clean messages before passing to child agents or for LLM calls.

    This method:
    1. Removes AIMessages with unanswered tool_calls (prevents LLM parsing errors)
    2. Optionally filters out SystemMessages (child agents get their own)
    3. Preserves all ToolMessages as-is

    Args:
        messages: List of messages to clean
        remove_system_messages: If True, filter out SystemMessages

    Returns:
        List of cleaned messages ready for use
    """
    from langchain_core.messages import SystemMessage

    # First, collect all answered tool_call_ids
    answered_ids = set()
    for msg in messages:
        if isinstance(msg, ToolMessage):
            tool_id = getattr(msg, "tool_call_id", None)
            if tool_id:
                answered_ids.add(tool_id)

    cleaned = []
    for msg in messages:
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            msg_tool_ids = {_get_tool_call_id(tc) for tc in msg.tool_calls if tc}
            msg_tool_ids.discard(None)
            unanswered = msg_tool_ids - answered_ids

            if unanswered:
                # Some tool_calls not answered - create clean AIMessage with content only
                if msg.content:
                    cleaned.append(AIMessage(content=msg.content))
                continue

        # Skip SystemMessages if requested
        if remove_system_messages and isinstance(msg, SystemMessage):
            continue

        cleaned.append(msg)

    return cleaned


# =============================================================================
# ANCHOR AND HEADING UTILITIES
# =============================================================================


def generate_anchor(title: str, seen_anchors: set | None = None) -> str:
    """
    Generate a URL-safe anchor from a title, handling non-ASCII characters.

    Args:
        title: The heading title to convert to an anchor
        seen_anchors: Optional set of already-used anchors for deduplication

    Returns:
        URL-safe anchor string
    """
    # Keep Chinese characters and common word characters
    anchor = title.lower()
    anchor = re.sub(
        r"[^\u4e00-\u9fa5\w\s-]", "", anchor
    )  # Keep Chinese, word chars, spaces, hyphens
    anchor = re.sub(r"\s+", "-", anchor)  # Replace spaces with hyphens
    anchor = re.sub(r"-+", "-", anchor)  # Collapse multiple hyphens
    anchor = re.sub(r"^-+|-+$", "", anchor)  # Trim leading/trailing hyphens

    # Hash-based anchor if empty (e.g., all special characters removed)
    if not anchor:
        hash_val = hashlib.md5(title.encode("utf-8")).hexdigest()[:8]
        anchor = f"section-{hash_val}"

    # Ensure uniqueness if seen_anchors provided
    if seen_anchors is not None:
        original_anchor = anchor
        counter = 1
        while anchor in seen_anchors:
            anchor = f"{original_anchor}-{counter}"
            counter += 1
        seen_anchors.add(anchor)

    return anchor


def extract_markdown_headings(
    content: str,
    min_level: int = 2,
    max_level: int = 6,
    base_depth: int = 0,
) -> list[dict]:
    """
    Extract hierarchical headings from markdown content.

    Returns a nested structure suitable for navigation menus.

    Args:
        content: Markdown content to parse
        min_level: Minimum heading level to extract (default: 2 for ##)
        max_level: Maximum heading level to extract (default: 6 for ######)
        base_depth: Base depth level for calculating relative depths

    Returns:
        List of heading dicts with nested 'children' arrays
    """
    heading_pattern = re.compile(
        rf"^(#{{{min_level},{max_level}}})\s+(.+)$", re.MULTILINE
    )
    headings = []
    heading_stack = []  # [(level, item_dict)]
    seen_titles = set()
    seen_anchors = set()

    for match in heading_pattern.finditer(content):
        level = len(match.group(1))  # Number of # symbols
        title = match.group(2).strip()

        # Skip common non-content headings
        if title.lower() in ["table of contents", "目录", "contents"]:
            continue

        # Skip duplicates
        if title in seen_titles:
            continue
        seen_titles.add(title)

        # Generate anchor
        anchor = generate_anchor(title, seen_anchors)

        item = {
            "name": title,
            "anchor": anchor,
            "level": level,
            "depth": level - min_level + base_depth,
            "children": [],
        }

        # Build hierarchy
        while heading_stack and heading_stack[-1][0] >= level:
            heading_stack.pop()

        if heading_stack:
            parent = heading_stack[-1][1]
            parent["children"].append(item)
        else:
            headings.append(item)

        heading_stack.append((level, item))

    return headings


def generate_code_block_id(file_path: str, start_line: int, end_line: int) -> str:
    """
    Generate a stable unique ID for a code block based on file path and line range.

    This ensures the same code block always gets the same ID across
    different sessions and matches IDs from get_accumulated_code_blocks.

    Args:
        file_path: Path to the file containing the code
        start_line: Starting line number
        end_line: Ending line number

    Returns:
        Block ID in format "block-{hash}"
    """
    block_id_content = f"{file_path}:{start_line}:{end_line}"
    block_id = hashlib.md5(block_id_content.encode()).hexdigest()[:12]
    return f"block-{block_id}"


# =============================================================================
# TOOL NAME CONCATENATION FIX
# =============================================================================


def greedy_split_tool_names(
    concatenated: str, valid_names: set[str]
) -> list[str] | None:
    """Split a concatenated tool name string into valid tool names.

    Uses greedy matching — tries the longest valid name at each position.
    Returns None if the string cannot be fully decomposed.

    Example:
        "find_nodesfind_nodes" → ["find_nodes", "find_nodes"]
    """
    sorted_names = sorted(valid_names, key=len, reverse=True)
    result: list[str] = []
    remaining = concatenated

    while remaining:
        matched = False
        for name in sorted_names:
            if remaining.startswith(name):
                result.append(name)
                remaining = remaining[len(name):]
                matched = True
                break
        if not matched:
            return None

    return result if len(result) >= 2 else None


# =============================================================================
# LLM RETRY WITH AUTO-FALLBACK
# =============================================================================

_DEFAULT_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "8"))
_DEFAULT_RETRY_DELAYS = [
    int(x) for x in os.getenv("LLM_RETRY_DELAYS", "10,15,30,45,60,60,60").split(",")
]  # seconds — ~280s total, configurable via env

# Transient error keywords that warrant retry
_TRANSIENT_KEYWORDS = [
    "incomplete chunked read",
    "peer closed connection",
    "connection reset",
    "connection aborted",
    "timed out",
    "timeout",
    "server disconnected",
    "502",
    "503",
    "504",
    "overloaded",
    "rate limit",
]


def _is_transient_error(error: Exception) -> bool:
    """Check whether *error* is a transient / connection issue worth retrying."""
    err_str = str(error).lower()
    return any(kw in err_str for kw in _TRANSIENT_KEYWORDS)


# ---------------------------------------------------------------------------
# Global LLM concurrency limiter
# ---------------------------------------------------------------------------
# When multiple child agents run in parallel (via LangGraph Send()), they can
# saturate the LLM API proxy.  A module-level semaphore caps the number of
# concurrent LLM invocations across all agents in this process.
_LLM_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", "10"))
_llm_semaphore = asyncio.Semaphore(_LLM_CONCURRENCY)


async def _try_invoke(
    llm,
    messages,
    *,
    label: str,
    max_retries: int,
    retry_delays: list[int],
) -> Any:
    """Try invoking *llm* up to *max_retries* times with back-off.

    Returns the response on success, raises on final failure.
    """
    timeout_seconds = max(
        0.01, float(os.getenv("LLM_INVOKE_TIMEOUT_SECONDS", "180"))
    )
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            logger.debug(f"[{label}] Waiting for LLM semaphore (concurrency limit: {_LLM_CONCURRENCY})...")
            async with _llm_semaphore:
                logger.debug(f"[{label}] Acquired semaphore, invoking LLM (timeout: {timeout_seconds}s)...")
                result = await asyncio.wait_for(
                    llm.ainvoke(messages), timeout=timeout_seconds
                )
                logger.debug(f"[{label}] LLM invocation completed successfully")
                return result
        except asyncio.TimeoutError:
            last_error = TimeoutError(
                f"{label} request timed out after {timeout_seconds:g}s"
            )
        except Exception as e:
            last_error = e
            if not _is_transient_error(e):
                raise  # Non-transient — don't retry

        if not _is_transient_error(last_error):
            raise last_error

        delay = retry_delays[min(attempt, len(retry_delays) - 1)]
        logger.warning(
            f"[{label}] Attempt {attempt + 1}/{max_retries} failed: {last_error}. "
            f"Retrying in {delay}s..."
        )
        await asyncio.sleep(delay)
    raise last_error  # type: ignore[misc]


async def invoke_with_retry(
    llm,
    messages,
    *,
    label: str = "LLM",
    max_retries: int = _DEFAULT_MAX_RETRIES,
    retry_delays: list[int] | None = None,
    config: Any | None = None,
    tools: list | None = None,
) -> Any:
    """Invoke an LLM with retry and automatic model fallback.

    Flow
    ----
    1. Try the current *llm* up to *max_retries* times (with exponential backoff).
    2. On exhaustion **and** if *config* is provided, query ``/v1/models`` for
       available chat models, pick alternatives, and retry each fallback model.
    3. Raise the last error only if every model has been exhausted.

    Parameters
    ----------
    llm:
        LangChain chat model instance to invoke first.
    messages:
        List of LangChain messages.
    label:
        Human-readable label for log messages.
    max_retries:
        Number of retry attempts per model (default 8, env: LLM_MAX_RETRIES).
    retry_delays:
        Delay (seconds) between retries.  Defaults to ``[10, 15, 30, 45, 60, 60, 60]``
        (env: LLM_RETRY_DELAYS).
    config:
        A ``ModelConfig`` (from ``core.config``) with ``endpoint`` and ``api_key``.
        Needed to create fallback LLM instances and to query the model list.
        If ``None``, no fallback is attempted.
    tools:
        Optional list of LangChain tools to bind to fallback models.
        When the primary LLM has tools bound (via ``bind_tools``), pass the
        original tool list here so fallback models get the same binding.
    """
    if retry_delays is None:
        retry_delays = list(_DEFAULT_RETRY_DELAYS)

    # --- Phase 1: try the primary model ---
    primary_error: Exception | None = None
    try:
        return await _try_invoke(
            llm, messages, label=label, max_retries=max_retries, retry_delays=retry_delays
        )
    except Exception as e:
        primary_error = e
        if config is None:
            raise  # No config → can't fallback

        logger.warning(
            f"[{label}] Primary model exhausted after {max_retries} retries. "
            f"Attempting auto-fallback via /v1/models..."
        )

    # --- Phase 2: query available fallback models ---
    from agent.llm import create_model
    from agent.model_registry import ModelRegistry

    base_url = config.endpoint or "https://api.openai.com/v1"
    current_model_id = config.model_id

    try:
        fallback_ids = await ModelRegistry.get_fallback_model_ids(
            base_url, config.api_key, exclude_model=current_model_id
        )
    except Exception as registry_err:
        logger.warning(f"[{label}] Failed to fetch fallback models: {registry_err}")
        if primary_error is not None:
            raise primary_error  # noqa: B904
        raise

    if not fallback_ids:
        logger.warning(f"[{label}] No fallback models available from {base_url}")
        if primary_error is not None:
            raise primary_error
        raise RuntimeError(f"[{label}] No fallback models available")

    logger.info(
        f"[{label}] Found {len(fallback_ids)} fallback model(s): {fallback_ids[:5]}"
    )

    # --- Phase 3: try each fallback model ---
    last_error: Exception = primary_error or RuntimeError(
        f"[{label}] Fallback invoked without a primary error"
    )
    for fb_model_id in fallback_ids:
        try:
            from dataclasses import replace

            fb_config = replace(config, model_id=fb_model_id)
            fb_llm = create_model(fb_config)
            if tools:
                fb_llm = fb_llm.bind_tools(tools)
            logger.info(f"[{label}] Trying fallback model: {fb_model_id} (tools={'yes' if tools else 'no'})")
            return await _try_invoke(
                fb_llm,
                messages,
                label=f"{label}|fallback:{fb_model_id}",
                max_retries=max_retries,
                retry_delays=retry_delays,
            )
        except Exception as e:
            last_error = e
            logger.warning(f"[{label}] Fallback model {fb_model_id} also failed: {e}")

    # All models exhausted
    raise last_error

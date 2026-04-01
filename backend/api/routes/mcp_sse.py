# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
AtCode MCP SSE Server

Provides MCP tools via HTTP/SSE transport, allowing direct connection via:
    claude mcp add --transport sse atcode http://localhost:8001/mcp

This integrates AtCode knowledge graph capabilities directly into the
FastAPI backend, eliminating the need for a separate stdio MCP server process.
"""

import json
from pathlib import Path
from typing import Any

import redis
from agent.tools.code_tools import (
    CodeExplorer,
    CodeRetriever,
    FileReader,
    FolderSearchResult,
)
from agent.tools.graph_query import GraphQueryTools
from agent.tools.tool_registry import TOOL_DESCRIPTIONS
from core.config import settings
from fastmcp import FastMCP
from graph.service import MemgraphIngestor
from loguru import logger

# =============================================================================
# Helper: user-friendly database error messages
# =============================================================================


def _db_error_message(e: Exception) -> str:
    """Convert raw database exceptions into actionable user-friendly messages."""
    err = str(e)
    if (
        "Not connected" in err
        or "Connection refused" in err
        or isinstance(e, (ConnectionError, OSError))
    ):
        return (
            f"Cannot connect to Memgraph database at {settings.MEMGRAPH_HOST}:{settings.MEMGRAPH_PORT}. "
            "Please ensure: (1) Memgraph is running (check with 'docker ps | grep memgraph'), "
            "(2) MEMGRAPH_HOST and MEMGRAPH_PORT environment variables are correct. "
            f"Original error: {err}"
        )
    return err


def _serialize(obj) -> dict | list:
    """Convert Pydantic models or other objects to JSON-serializable dicts.

    Handles:
    - Pydantic v2 models (model_dump)
    - Pydantic v1 models (dict)
    - Plain dicts/lists (passthrough)
    """
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    return obj


def _ensure_project_prefix(name: str, project: str) -> str:
    """Ensure qualified_name has project prefix.

    This helper function adds the project prefix to a qualified_name
    if it doesn't already have one. This allows users to pass short
    names from find_nodes() results without manually adding the prefix.

    Args:
        name: The qualified_name (may or may not have project prefix)
        project: The current project name

    Returns:
        The qualified_name with project prefix

    Examples:
        >>> _ensure_project_prefix("attention.PagedAttention", "vllm")
        "vllm.attention.PagedAttention"
        >>> _ensure_project_prefix("vllm.attention.PagedAttention", "vllm")
        "vllm.attention.PagedAttention"
    """
    if not name:
        return name
    prefix = f"{project}."
    if not name.startswith(prefix):
        return f"{prefix}{name}"
    return name


# =============================================================================
# MCP Server Instance
# =============================================================================

mcp = FastMCP(
    name="AtCode",
    auth=None,  # Disable OAuth authentication for local development
    instructions="""AtCode: AI-powered code knowledge graph for exploring and understanding codebases.

## PROHIBITIONS
- **NEVER use Task tool / sub-agents / background agents for code exploration.** Sub-agents do NOT have AtCode tools and will fall back to Bash/Read/grep. You MUST call AtCode tools yourself.
- **NEVER use Bash/Read/grep/cat to explore code.** The ONLY acceptable Bash call is `sleep 60` while waiting for a build. Always use AtCode tools instead.

## GETTING STARTED
1. **list_repos()** → discover available projects (supports multi-repo graphs and cross-repo queries)
2. **set_project(name)** → set context for a specific project
3. **find_nodes("keyword1|keyword2")** → search with `|` for OR variants, broad stems ("sampl") or `*` for glob
4. **explore_code(qualified_name)** → returns source code + callers list + callees list (each with name+docstring+path)

## TOOL TIPS
- **find_nodes**: Combine related terms with `|` in a single call: `find_nodes("attention|attn|flash|paged")`
- **explore_code**: Returns the target's source + lists of callers and callees with their names, docstrings, and paths. Often sufficient to understand a component without further exploration.
- **get_code_snippet**: Lightweight source retrieval. Use explore_code if you also need callers/callees.
- **get_children**: Browse a module's contents. Use find_nodes for name-based search instead of chaining get_children.
- **find_calls**: Find call relationships. explore_code already includes this information.
- **trace_dependencies**: Trace call/import/inheritance chains between two elements, or find all reachable elements from a starting point.
- **read_file**: Read file/folder content. For code exploration prefer find_nodes → explore_code → get_code_snippet.
- **manage_graph**: Build/refresh graph or check job status via `action` param ("build"/"refresh"/"job_status").
- **git**: All git ops via `action` param ("checkout"/"fetch"/"list_refs"/"pull").
- **sync**: File monitoring via `action` param ("start"/"stop"/"now"/"status").
- **manage_repo**: Repo lifecycle via `action` param ("add"/"remove"/"clean").
- **search_papers**: PREFERRED for topic queries. Supports `|` for multiple keywords (e.g. "attention|MoE|sparse"). Searches locally cached papers. Results include `is_processed: true` for papers already in the library — use `get_paper_doc` directly for those, do NOT call `read_paper` again.
- **browse_papers**: Browse/crawl HF papers via `mode` param ("daily"/"range"/"crawl"). Use search_papers first if the user asks about a specific topic.

## WORKFLOW BY QUESTION TYPE

**For "structure", "overview", "architecture", "modules" questions:**
1. get_children(".") → discover top-level packages/modules
2. get_children("package_name") on ALL important packages (at least 5-8) → discover sub-modules
3. For key packages, drill one more level: get_children("package.submodule") to show depth
4. Use find_nodes or explore_code on 2-3 key entry points for richer descriptions
5. **MINIMUM 6-10 tool calls** for structure questions — shallow answers are insufficient
6. Output should include architecture diagrams, detailed tables with links, and prose descriptions

**For code understanding / "how does X work" questions:**
1. find_nodes("kw1|kw2|kw3") → broad OR query covering related concepts
2. explore_code(top_result) → returns source + callers + callees (each with name+docstring)
3. explore_code on 2-4 additional key components to build comprehensive understanding
4. **MINIMUM 5-8 tool calls** for "how does X work" questions

**For dependency / "what calls X" / "what does X call" questions:**
1. find_nodes("X") → locate the element
2. explore_code(X) → get callers and callees in one call
3. trace_dependencies(X) → for deeper chain analysis (multi-hop)
4. find_calls(X, direction="incoming") → if you need ONLY relationship data

**For cross-repo exploration:**
1. list_repos() → discover available projects
2. find_nodes("query", repo_name="OtherRepo") → search in a different repo
3. set_project("OtherRepo") → switch context for deeper exploration

## TOOL COMBINATION STRATEGIES
- **Deep dive**: find_nodes → explore_code (primary) → explore_code on key callees
- **Broad survey**: get_children(".") → get_children on each top package → find_nodes for specifics
- **Tracing**: find_nodes → trace_dependencies (start→end) or explore_code for immediate neighbors
- **File content**: read_file for config/README/non-code files; explore_code for code entities

## EFFICIENCY GUIDANCE
- Prefer `explore_code` over `get_code_snippet` + `find_calls` — it combines both in one call.
- When explore_code returns a `called` list, you can describe callees from their name+docstring without exploring each one separately.
- Use find_nodes with `|` to search for multiple related terms in one call.
- For understanding code architecture, use `find_nodes → explore_code` rather than reading multiple source files with read_file.
- The explore_code response includes callers and callees with docstrings — use this information to explain relationships without additional tool calls.
- **Never read the same file twice** — synthesize from what you've already retrieved.
- **Write your answer once you can cover the main concepts.** For complex topics (e.g., "attention mechanisms"), focus on the core abstraction + 1-2 key implementations rather than reading every backend file.

## BUILD GRAPH FLOW (when list_repos() returns empty)
1. manage_graph(action="build", project_path="...") → if "project_already_exists": skip to step 3
2. **WHILE BUILDING:** `sleep 60` → manage_graph(action="job_status", job_id="...") → repeat until completed. Do NOT use Bash/Read/sub-agents during builds.
3. set_project(name) → find_nodes() → explore_code() → write answer.

## FORMATTING (MANDATORY)
- **Linking**: Use `[[qualified_name]]` for EVERY code entity found via tools. Example: `[[vllm.engine.LLMEngine]]`, `[[module.ClassName.method]]`.
  - Use backticks only for things NOT in the codebase (variables, constants, types).
- Use mermaid diagrams for architecture/flow. Prefer tables for structured comparisons.

## KEY CONCEPTS
- **qualified_name**: `project.module.ClassName.method_name` — get from find_nodes() before using other tools.
""",
)


# =============================================================================
# Server State
# =============================================================================


class MCPState:
    """Manages MCP server state with per-project caching to avoid conflicts between sessions."""

    # Redis key for storing project context (used as default, but each tool can override)
    REDIS_PROJECT_KEY = "mcp:project_context"

    def __init__(self):
        self._ingestor: MemgraphIngestor | None = None
        # Per-project caches to avoid conflicts between different Claude Code sessions
        self._query_tools_cache: dict[str, GraphQueryTools] = {}
        self._code_explorer_cache: dict[str, CodeExplorer] = {}
        # Track recently read files to prevent duplicate reads
        self._recent_reads: dict[str, str] = {}  # resolved_path -> summary
        self._code_retriever_cache: dict[str, CodeRetriever] = {}
        self._file_reader_cache: dict[str, FileReader] = {}
        self._project_roots: dict[str, str] = {}  # project_name -> project_root
        # Track active sync managers by project name
        self._sync_managers: dict[str, Any] = {}
        # Redis client for cross-process state sharing (synchronous)
        self._redis: redis.Redis | None = None

    @property
    def redis_client(self) -> redis.Redis:
        """Get Redis client (lazy initialization)."""
        if self._redis is None:
            redis_url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
            self._redis = redis.from_url(redis_url, decode_responses=True)
        return self._redis

    @property
    def current_project(self) -> str | None:
        """Get current project from Redis (used as default when project_name not specified)."""
        try:
            value = self.redis_client.hget(self.REDIS_PROJECT_KEY, "project_name")
            return value if value else None
        except Exception as e:
            logger.warning(f"Failed to get project from Redis: {e}")
            return None

    @current_project.setter
    def current_project(self, value: str | None):
        """Set current project in Redis (used as default when project_name not specified)."""
        try:
            if value is None:
                self.redis_client.hdel(self.REDIS_PROJECT_KEY, "project_name")
            else:
                self.redis_client.hset(self.REDIS_PROJECT_KEY, "project_name", value)
        except Exception as e:
            logger.warning(f"Failed to set project in Redis: {e}")

    @property
    def current_project_root(self) -> str | None:
        """Get current project root from Redis."""
        try:
            value = self.redis_client.hget(self.REDIS_PROJECT_KEY, "project_root")
            return value if value else None
        except Exception as e:
            logger.warning(f"Failed to get project root from Redis: {e}")
            return None

    @current_project_root.setter
    def current_project_root(self, value: str | None):
        """Set current project root in Redis."""
        try:
            if value is None:
                self.redis_client.hdel(self.REDIS_PROJECT_KEY, "project_root")
            else:
                self.redis_client.hset(self.REDIS_PROJECT_KEY, "project_root", value)
        except Exception as e:
            logger.warning(f"Failed to set project root in Redis: {e}")

    def set_project(self, project_name: str, project_root: str | None = None):
        """Set project context (both default and cached root)."""
        self.current_project = project_name
        if project_root:
            self.current_project_root = project_root
            self._project_roots[project_name] = project_root
        # Clear read dedup cache on project change
        self._recent_reads.clear()

    def get_project_root(self, project_name: str) -> str | None:
        """Get project root for a specific project."""
        # First check local cache
        if project_name in self._project_roots:
            return self._project_roots[project_name]
        # Then check if it's the current project
        if project_name == self.current_project:
            return self.current_project_root
        # Finally, query from database
        try:
            results = self.ingestor.fetch_all(
                "MATCH (p:Project {name: $name}) RETURN p.path AS path",
                {"name": project_name},
                use_cache=False,
            )
            if results and results[0].get("path"):
                root = results[0]["path"]
                self._project_roots[project_name] = root
                return root
        except Exception as e:
            logger.warning(f"Failed to get project root from DB: {e}")
        return None

    @property
    def ingestor(self) -> MemgraphIngestor:
        if self._ingestor is None:
            try:
                self._ingestor = MemgraphIngestor(
                    host=settings.MEMGRAPH_HOST,
                    port=settings.MEMGRAPH_PORT,
                )
                self._ingestor.__enter__()
                logger.info(
                    f"MCP MemgraphIngestor connected: {settings.MEMGRAPH_HOST}:{settings.MEMGRAPH_PORT}"
                )
            except Exception as e:
                self._ingestor = None
                raise ConnectionError(
                    f"Cannot connect to Memgraph at {settings.MEMGRAPH_HOST}:{settings.MEMGRAPH_PORT}. "
                    f"Ensure Memgraph is running. Error: {e}"
                ) from e
        return self._ingestor

    def get_query_tools(self, project_name: str) -> GraphQueryTools:
        """Get or create query tools for a specific project."""
        if project_name not in self._query_tools_cache:
            self._query_tools_cache[project_name] = GraphQueryTools(
                self.ingestor, project_name=project_name
            )
        return self._query_tools_cache[project_name]

    def get_code_explorer(self, project_name: str) -> CodeExplorer:
        """Get or create code explorer for a specific project."""
        if project_name not in self._code_explorer_cache:
            project_root = self.get_project_root(project_name)
            self._code_explorer_cache[project_name] = CodeExplorer(
                self.ingestor,
                project_name=project_name,
                project_root=project_root,
            )
        return self._code_explorer_cache[project_name]

    def get_code_retriever(self, project_name: str) -> CodeRetriever:
        """Get or create code retriever for a specific project."""
        if project_name not in self._code_retriever_cache:
            project_root = self.get_project_root(project_name)
            self._code_retriever_cache[project_name] = CodeRetriever(
                self.ingestor,
                project_name=project_name,
                project_root=project_root,
            )
        return self._code_retriever_cache[project_name]

    def get_file_reader(self, project_name: str) -> FileReader:
        """Get or create file reader for a specific project."""
        if project_name not in self._file_reader_cache:
            project_root = self.get_project_root(project_name)
            self._file_reader_cache[project_name] = FileReader(
                project_root=project_root,
                ingestor=self.ingestor,
                project_name=project_name,
            )
        return self._file_reader_cache[project_name]

    def invalidate_cache(self, project_name: str):
        """Invalidate cached tools for a project after sync changes."""
        self._query_tools_cache.pop(project_name, None)
        self._code_explorer_cache.pop(project_name, None)
        self._code_retriever_cache.pop(project_name, None)
        self._file_reader_cache.pop(project_name, None)

    # Keep old properties for backward compatibility
    @property
    def query_tools(self) -> GraphQueryTools:
        """Get query tools for current project (backward compatibility)."""
        if not self.current_project:
            raise ValueError("No project context. Call set_project() first.")
        return self.get_query_tools(self.current_project)

    @property
    def code_explorer(self) -> CodeExplorer:
        """Get code explorer for current project (backward compatibility)."""
        if not self.current_project:
            raise ValueError("No project context. Call set_project() first.")
        return self.get_code_explorer(self.current_project)

    @property
    def code_retriever(self) -> CodeRetriever:
        """Get code retriever for current project (backward compatibility)."""
        if not self.current_project:
            raise ValueError("No project context. Call set_project() first.")
        return self.get_code_retriever(self.current_project)


state = MCPState()


# =============================================================================
# Project Management Tools
# =============================================================================


@mcp.tool(description=TOOL_DESCRIPTIONS["set_project"])
def set_project(project_name: str, project_root: str | None = None) -> str:
    """Set active project context."""
    if not project_root:
        # Look up project path from database
        try:
            results = state.ingestor.fetch_all(
                "MATCH (p:Project {name: $name}) RETURN p.path AS path",
                {"name": project_name},
                use_cache=False,
            )
            if results and results[0].get("path"):
                project_root = results[0]["path"]
        except Exception as e:
            logger.warning(f"Could not fetch project path: {e}")

    state.set_project(project_name, project_root)

    return json.dumps(
        {
            "success": True,
            "project_name": project_name,
            "project_root": project_root,
            "message": f"Project context set to '{project_name}'",
        }
    )


@mcp.tool(description=TOOL_DESCRIPTIONS["list_repos"])
def list_repos() -> str:
    """List all available projects."""
    try:
        # Query for both name and path to enable deduplication by path
        query = (
            "MATCH (p:Project) RETURN p.name AS name, p.path AS path ORDER BY p.name"
        )
        results = state.ingestor.fetch_all(query, use_cache=False)

        repos = [
            {"name": r["name"], "path": r.get("path")} for r in results if r.get("name")
        ]

        # Provide different guidance based on whether repos exist
        if repos:
            # Check if user's project path matches any existing repo
            hint = (
                "Use set_project(project_name='...') with the exact name from this list, "
                "then find_nodes() to search code."
            )
        else:
            hint = (
                "No projects found. Use build_graph(project_path='/absolute/path/to/project') to create a graph. "
                "CRITICAL: After build_graph(), WAIT for completion (sleep 60 + get_job_status). "
                "Do NOT use Bash/Read to explore code — wait for the graph, then use AtCode tools exclusively."
            )

        return json.dumps(
            {
                "success": True,
                "count": len(repos),
                "repositories": repos,
                "hint": hint,
            }
        )
    except Exception as e:
        return json.dumps({"error": _db_error_message(e)})


# =============================================================================
# Code Search & Discovery Tools
# =============================================================================


@mcp.tool(description=TOOL_DESCRIPTIONS["find_nodes"])
def find_nodes(
    query: str,
    search_strategy: str = "auto",
    node_type: str = "Code",
    repo_name: str | None = None,
) -> str:
    """Search for code elements by keyword."""
    if not state.current_project and not repo_name:
        return json.dumps({"error": "No project context. Call set_project() first."})

    repo = repo_name or state.current_project

    try:
        results = state.get_query_tools(repo).find_nodes(
            query=query,
            repo_name=repo,
            search_strategy=search_strategy,
            node_type=node_type,
        )
        return json.dumps(_serialize(results))
    except Exception as e:
        return json.dumps({"error": _db_error_message(e)})


@mcp.tool(description=TOOL_DESCRIPTIONS["find_calls"])
def find_calls(
    qualified_name: str,
    direction: str = "outgoing",
    depth: int = 1,
    repo_name: str | None = None,
) -> str:
    """Find call relationships for a function/method."""
    if not state.current_project and not repo_name:
        return json.dumps({"error": "No project context. Call set_project() first."})

    try:
        repo = repo_name or state.current_project
        # Ensure qualified_name has project prefix (allows short names from find_nodes results)
        qn = _ensure_project_prefix(qualified_name, repo)

        results = state.get_query_tools(repo).find_calls(
            qualified_name=qn, direction=direction, depth=depth
        )
        return json.dumps(_serialize(results))
    except Exception as e:
        return json.dumps({"error": _db_error_message(e)})


@mcp.tool(description=TOOL_DESCRIPTIONS["get_children"])
def get_children(
    identifier: str,
    identifier_type: str = "auto",
    depth: int = 1,
    child_types: str | None = None,
    repo_name: str | None = None,
) -> str:
    """List children of a parent node."""
    if not state.current_project and not repo_name:
        return json.dumps({"error": "No project context. Call set_project() first."})

    repo = repo_name or state.current_project

    # Handle special identifiers
    actual_identifier = identifier
    if identifier in (".", "current"):
        actual_identifier = repo
        identifier_type = "project"

    try:
        results = state.get_query_tools(repo).get_children(
            identifier=actual_identifier,
            identifier_type=identifier_type,
            depth=depth,
            child_types=child_types.split(",") if child_types else None,
        )
        return json.dumps(_serialize(results))
    except Exception as e:
        return json.dumps({"error": _db_error_message(e)})


# =============================================================================
# Code Retrieval Tools
# =============================================================================


@mcp.tool(description=TOOL_DESCRIPTIONS["get_code_snippet"])
async def get_code_snippet(qualified_name: str, repo_name: str | None = None) -> str:
    """Lightweight source-code retrieval via CodeRetriever service."""
    if not state.current_project and not repo_name:
        return json.dumps({"error": "No project context. Call set_project() first."})

    repo = repo_name or state.current_project

    try:
        retriever = state.get_code_retriever(repo)
        result = await retriever.find_code_snippet(qualified_name, repo_name)
        return json.dumps(_serialize(result), default=str)
    except Exception as e:
        return json.dumps({"error": _db_error_message(e)})


@mcp.tool(description=TOOL_DESCRIPTIONS["explore_code"])
def explore_code(
    identifier: str,
    repo_name: str | None = None,
    max_dependency_depth: int = 5,
    include_dependency_source_code: bool = True,
) -> str:
    """Explore a code element in depth with fuzzy match support."""
    if not state.current_project and not repo_name:
        return json.dumps({"error": "No project context. Call set_project() first."})

    repo = repo_name or state.current_project

    try:
        explorer = state.get_code_explorer(repo)

        # Ensure identifier has project prefix
        ident = identifier
        if not ident.startswith(f"{repo}."):
            ident = f"{repo}.{ident}"

        # Try exact match first
        exact_result = explorer.query_tools.find_nodes(ident, search_strategy="auto")

        if not exact_result.success or not exact_result.results:
            # Fuzzy: search by basename
            basename = ident.split(".")[-1] if "." in ident else ident
            pattern_result = explorer.query_tools.find_nodes(
                query=basename, search_strategy="pattern", case_sensitive=False
            )

            if not pattern_result.success or not pattern_result.results:
                return json.dumps(
                    {
                        "error": f"No elements found for: '{identifier}'. "
                        "Use find_nodes() first to discover valid qualified_names."
                    }
                )

            # Auto-select single result
            if pattern_result.count == 1:
                ident = pattern_result.results[0].get("qualified_name", ident)
            else:
                # Multiple results — return suggestions
                suggestions = [
                    r.get("qualified_name", "?") for r in pattern_result.results[:15]
                ]
                return json.dumps(
                    {
                        "error": f"Ambiguous: found {pattern_result.count} matches.",
                        "suggestions": suggestions,
                        "hint": "Call explore_code with an exact qualified_name from above.",
                    }
                )

        result = explorer.explore_code_context(
            identifier=ident,
            include_code=include_dependency_source_code,
            call_depth=max_dependency_depth,
        )
        return json.dumps(_serialize(result))
    except Exception as e:
        return json.dumps({"error": _db_error_message(e)})


@mcp.tool(description=TOOL_DESCRIPTIONS["read"])
async def read_file(
    identifier: str,
    pattern: str | None = None,
    match_mode: str = "full",
    start_line: int | None = None,
    end_line: int | None = None,
    max_lines: int = 500,
) -> str:
    """Read file/folder content via FileReader service. Supports file read, folder listing, and pattern search."""
    if not state.current_project:
        return json.dumps({"error": "No project context. Call set_project() first."})

    repo = state.current_project

    try:
        file_reader = state.get_file_reader(repo)

        # Deduplication for full file reads (no pattern, no line range)
        if pattern is None and start_line is None and end_line is None:
            project_root = state.current_project_root or ""
            root = Path(project_root) if project_root else None
            if root:
                candidate = root / identifier
                path_key = str(candidate)
                if path_key in state._recent_reads:
                    return json.dumps(
                        {
                            "found": True,
                            "duplicate": True,
                            "message": f"You already read this file. {state._recent_reads[path_key]} Use the content from your previous read_file call instead of re-reading.",
                        }
                    )

        # For simple file read with line range (legacy behavior), use max_chars based on max_lines
        max_chars = max_lines * 120  # Approximate chars per line

        result = await file_reader.read(
            identifier=identifier,
            pattern=pattern,
            match_mode=match_mode if pattern else "full",
            max_chars=max_chars,
        )

        # Handle FolderSearchResult
        if isinstance(result, FolderSearchResult):
            return json.dumps(
                {
                    "found": result.found,
                    "type": "folder_search",
                    "content": result.format_output(),
                    "files_searched": result.files_searched,
                    "files_matched": result.files_matched,
                    "total_matches": result.total_matches,
                }
            )

        # Handle FileContent
        if not result.found:
            return json.dumps(
                {"found": False, "error": result.error_message or f"File not found: {identifier}"}
            )

        # Track this read for deduplication
        project_root = state.current_project_root or ""
        if project_root:
            root = Path(project_root)
            try:
                resolved = root / result.file_path
                state._recent_reads[str(resolved)] = (
                    f"File: {result.file_path} ({result.total_lines} lines)."
                )
            except Exception:
                pass

        # Apply line range if requested (for backward compat with MCP callers)
        content = result.content
        if start_line is not None or end_line is not None:
            lines = content.splitlines()
            actual_start = max(1, start_line or 1)
            actual_end = min(len(lines), end_line or len(lines))
            content = "\n".join(lines[actual_start - 1 : actual_end])

        return json.dumps(
            {
                "found": True,
                "file_path": result.file_path,
                "total_lines": result.total_lines,
                "content": content,
                "truncated": result.truncated,
                "match_count": result.match_count,
            }
        )

    except Exception as e:
        return json.dumps({"error": _db_error_message(e)})


# =============================================================================
# Graph Management Tools
# =============================================================================


@mcp.tool(description=TOOL_DESCRIPTIONS["manage_graph"])
async def manage_graph(
    action: str,
    project_path: str | None = None,
    project_name: str | None = None,
    fast_mode: bool = True,
    job_id: str | None = None,
) -> str:
    """Knowledge graph build, refresh, and job status.

    For 'build' action, performs extra checks via MCP state (project existence, name conflicts)
    before delegating to ManagementTools.
    """

    if action == "build":
        if not project_path:
            return json.dumps({"error": "project_path is required for build action"})

        path = Path(project_path)
        if not path.exists():
            return json.dumps({"error": f"Path does not exist: {project_path}"})

        # Determine project name
        name = project_name or path.name
        if not name.endswith("_claude"):
            name = f"{name}_claude"

        # Check if a project already exists for this path
        try:
            existing_by_path = state.ingestor.fetch_all(
                "MATCH (p:Project) WHERE p.path = $path RETURN p.name AS name",
                {"path": str(path)},
                use_cache=False,
            )
            if existing_by_path:
                existing_name = existing_by_path[0].get("name")
                state.set_project(existing_name, str(path))
                return json.dumps(
                    {
                        "success": True,
                        "status": "project_already_exists",
                        "message": f"Project '{existing_name}' already exists and project context is set. "
                        f"Proceed immediately with find_nodes() → explore_code(). "
                        f"Do NOT use Bash/Read — use AtCode tools exclusively.",
                        "project_name": existing_name,
                        "project_path": str(path),
                        "action_taken": "set_project_context",
                        "next_step": "Call find_nodes('keyword') to search the codebase. Do NOT use Bash, Read, or grep.",
                    }
                )
        except Exception as e:
            logger.warning(f"Failed to check existing project by path: {e}")

        # Check name conflict
        try:
            existing_by_name = state.ingestor.fetch_all(
                "MATCH (p:Project {name: $name}) RETURN p.path AS path",
                {"name": name},
                use_cache=False,
            )
            if existing_by_name:
                existing_path = existing_by_name[0].get("path")
                if existing_path and existing_path != str(path):
                    return json.dumps(
                        {
                            "success": False,
                            "error": "name_conflict",
                            "message": f"Project name '{name}' already exists but points to a different path: {existing_path}. "
                            f"Please specify a different project_name parameter.",
                            "existing_path": existing_path,
                            "requested_path": str(path),
                        }
                    )
        except Exception as e:
            logger.warning(f"Failed to check existing project by name: {e}")

        # Delegate actual build to ManagementTools
        return await _get_mgmt().abuild_graph(str(path), project_name=name, fast_mode=fast_mode)

    elif action == "refresh":
        if not project_name:
            return json.dumps({"error": "project_name is required for refresh action"})
        return await _get_mgmt().arefresh_graph(project_name, fast_mode=fast_mode)

    elif action == "job_status":
        if not job_id:
            return json.dumps({"error": "job_id is required for job_status action"})
        return await _get_mgmt().aget_task_status(job_id)

    return json.dumps({"error": f"Unknown manage_graph action: {action}. Use: build, refresh, job_status"})


@mcp.tool(description=TOOL_DESCRIPTIONS["check_health"])
def check_health() -> str:
    """Check database and project context health."""
    health = {
        "database": {"status": "unknown"},
        "project_context": {"status": "unknown"},
        "config": {
            "memgraph_host": settings.MEMGRAPH_HOST,
            "memgraph_port": settings.MEMGRAPH_PORT,
        },
    }

    # Check database
    try:
        projects = state.ingestor.get_all_projects()
        health["database"] = {
            "status": "healthy",
            "project_count": len(projects),
        }
    except Exception as e:
        health["database"] = {
            "status": "error",
            "error": _db_error_message(e),
            "fix": "Ensure Memgraph is running: 'docker ps | grep memgraph'. "
            "If not running: 'docker compose -f docker/compose.yaml up -d memgraph'",
        }

    # Check project context
    if state.current_project:
        health["project_context"] = {
            "status": "set",
            "project": state.current_project,
            "root": state.current_project_root,
        }
    else:
        health["project_context"] = {
            "status": "not_set",
            "message": "Call set_project() to set project context",
        }

    return json.dumps(health, indent=2)




# =============================================================================
# Advanced Analysis Tools
# =============================================================================


@mcp.tool(description=TOOL_DESCRIPTIONS["find_class_hierarchy"])
def find_class_hierarchy(
    class_qualified_name: str, repo_name: str | None = None
) -> str:
    """Find inheritance hierarchy of a class."""
    if not state.current_project and not repo_name:
        return json.dumps({"error": "No project context. Call set_project() first."})

    try:
        repo = repo_name or state.current_project
        # Ensure qualified_name has project prefix
        qn = _ensure_project_prefix(class_qualified_name, repo)

        results = state.get_query_tools(repo).find_class_hierarchy(
            class_qualified_name=qn
        )
        return json.dumps(_serialize(results))
    except Exception as e:
        return json.dumps({"error": _db_error_message(e)})


@mcp.tool(description=TOOL_DESCRIPTIONS["trace_dependencies"])
def trace_dependencies(
    start_qualified_name: str,
    end_qualified_name: str | None = None,
    max_depth: int = 5,
    relationship_type: str = "CALLS",
    repo_name: str | None = None,
) -> str:
    """Trace dependency path between code elements."""
    if not state.current_project and not repo_name:
        return json.dumps({"error": "No project context. Call set_project() first."})

    try:
        repo = repo_name or state.current_project
        # Ensure qualified_names have project prefix
        start_qn = _ensure_project_prefix(start_qualified_name, repo)
        end_qn = (
            _ensure_project_prefix(end_qualified_name, repo)
            if end_qualified_name
            else None
        )

        # Use CodeExplorer's trace_dependency_chain method
        result = state.get_code_explorer(repo).trace_dependency_chain(
            from_qualified_name=start_qn,
            to_qualified_name=end_qn,
            max_depth=min(max_depth, 10),  # Cap at 10 for safety
            relationship_type=relationship_type,
            detect_circular=True,
        )
        return json.dumps(_serialize(result))
    except Exception as e:
        return json.dumps({"error": _db_error_message(e)})


# =============================================================================
# Sync (compound tool)
# =============================================================================


@mcp.tool(description=TOOL_DESCRIPTIONS["sync"])
async def sync(
    project_name: str,
    action: str,
    repo_path: str | None = None,
    subdirs: str | None = None,
) -> str:
    """Sync operations: start, stop, now, status."""
    return await _get_mgmt().async_sync(project_name, action, repo_path=repo_path, subdirs=subdirs)


# =============================================================================
# Repository & Git Management Tools (compound)
# =============================================================================


def _get_mgmt() -> "ManagementTools":
    """Lazy-initialize ManagementTools."""
    from agent.tools.management_tools import ManagementTools

    if not hasattr(_get_mgmt, "_instance"):
        _get_mgmt._instance = ManagementTools()
    return _get_mgmt._instance


@mcp.tool(description=TOOL_DESCRIPTIONS["manage_repo"])
async def manage_repo(
    action: str,
    repo_url: str | None = None,
    local_path: str | None = None,
    project_name: str | None = None,
    repo_name: str | None = None,
) -> str:
    """Repository lifecycle: add, remove, clean."""
    return await _get_mgmt().amanage_repo(action, repo_url=repo_url, local_path=local_path, project_name=project_name, repo_name=repo_name)


@mcp.tool(description=TOOL_DESCRIPTIONS["git"])
async def git(
    project_name: str,
    action: str,
    ref: str | None = None,
    remote: str = "origin",
    branch: str | None = None,
) -> str:
    """Git operations: checkout, fetch, list_refs, pull."""
    return await _get_mgmt().agit(project_name, action, ref=ref, remote=remote, branch=branch)


# =============================================================================
# Paper Reading Tools (compound browse_papers)
# =============================================================================

def _get_paper_tools():
    """Get or create PaperTools instance."""
    from agent.tools.paper_tools import PaperTools
    return PaperTools()


@mcp.tool(description=TOOL_DESCRIPTIONS["search_papers"])
async def search_papers(
    query: str,
    start_date: str | None = None,
    end_date: str | None = None,
    max_results: int = 20,
) -> str:
    """Search locally cached daily papers and processed library by keyword."""
    return await _get_paper_tools().asearch_papers(query, start_date=start_date, end_date=end_date, max_results=max_results)


@mcp.tool(description=TOOL_DESCRIPTIONS["read_paper"])
async def read_paper(
    query: str | None = None,
    paper_url: str | None = None,
    arxiv_id: str | None = None,
    auto_build_repos: bool = True,
    max_papers: int = 1,
) -> str:
    """Start complete paper reading pipeline."""
    return await _get_paper_tools().aread_paper(query, paper_url, arxiv_id, auto_build_repos, max_papers)


@mcp.tool(description=TOOL_DESCRIPTIONS["get_paper_doc"])
async def get_paper_doc(paper_id: str, sections: str | None = None) -> str:
    """Get paper doc skeleton, or specific sections' full content."""
    return await _get_paper_tools().aget_paper_doc(paper_id, sections=sections)


@mcp.tool(description=TOOL_DESCRIPTIONS["list_papers"])
async def list_papers() -> str:
    """List all processed papers."""
    return await _get_paper_tools().alist_papers()


@mcp.tool(description=TOOL_DESCRIPTIONS["browse_papers"])
async def browse_papers(
    mode: str = "daily",
    date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    min_upvotes: int = 0,
) -> str:
    """Browse HuggingFace daily papers."""
    return await _get_paper_tools().abrowse_papers(mode, date=date, start_date=start_date, end_date=end_date, min_upvotes=min_upvotes)


# =============================================================================
# Create MCP ASGI App for FastAPI Integration
# =============================================================================


def create_mcp_app():
    """Create the MCP ASGI application for mounting in FastAPI.

    Note: Using streamable-http transport with stateless_http=True for multi-worker support.
    - streamable-http: Modern MCP transport that supports stateless mode
    - stateless_http=True: Creates new transport per request, no session state needed

    Client connection command:
        claude mcp add --transport http atcode http://localhost:8008/mcp
    """
    return mcp.http_app(path="/mcp", transport="streamable-http", stateless_http=True)


# Export for use in main.py
mcp_app = create_mcp_app()

# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
AtCode MCP Server - Pure HTTP Mode

This MCP server provides Claude with access to AtCode knowledge graph
functionality through HTTP API calls only. No direct database connections
are required.

Architecture:
    Claude Desktop ←(stdio)→ MCP Server ←(HTTP)→ Backend API ←→ Memgraph
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from loguru import logger
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    TextContent,
    Tool,
)

# =============================================================================
# Configuration
# =============================================================================

# Use API_PORT from environment, fallback to 8001
API_PORT = os.getenv("API_PORT", "8001")
ATCODE_API_URL = f"http://localhost:{API_PORT}"
API_BASE_URL = f"{ATCODE_API_URL}/api/"

logger.info(f"AtCode API URL: {ATCODE_API_URL} (port from API_PORT={API_PORT})")


# =============================================================================
# MCP Server State (Pure HTTP Mode)
# =============================================================================


class ServerState:
    """Manages server state for HTTP-only mode."""

    def __init__(self):
        self.current_project: str | None = None
        self.current_project_root: str | None = None
        self.http_client: httpx.AsyncClient | None = None

    async def initialize(self) -> bool:
        """Initialize HTTP client and verify API connection."""
        try:
            logger.info("=== MCP Server Configuration (HTTP Mode) ===")
            logger.info(f"AtCode API: {ATCODE_API_URL} (API_PORT={API_PORT})")
            logger.info("=============================================")

            # Initialize HTTP client with long timeout for graph operations
            self.http_client = httpx.AsyncClient(timeout=300.0)

            # Test API connection
            try:
                test_result = await self.http_client.get(
                    f"{ATCODE_API_URL}/api/health", timeout=5.0
                )
                if test_result.status_code == 200:
                    logger.info(f"✓ Connected to AtCode API at {ATCODE_API_URL}")
                else:
                    logger.warning(
                        f"⚠ AtCode API returned status {test_result.status_code}"
                    )
                    logger.warning("  Some tools may not work correctly")
            except httpx.ConnectError as e:
                logger.warning(
                    f"⚠ Cannot connect to AtCode API at {ATCODE_API_URL}: {e}"
                )
                logger.warning(f"  Make sure the backend is running on port {API_PORT}")
                logger.warning("  Start with: uv run ./scripts/start_api.sh")
            except Exception as e:
                logger.warning(f"⚠ Error testing API connection: {e}")

            return True
        except Exception as e:
            logger.error(f"Failed to initialize: {e}")
            return False

    def set_project(self, project_name: str, project_root: str | None = None) -> None:
        """Set the current project context."""
        self.current_project = project_name
        self.current_project_root = project_root
        logger.info(f"Project context set: {project_name} (root: {project_root})")

    async def close(self) -> None:
        """Close HTTP client."""
        if self.http_client:
            await self.http_client.aclose()
            self.http_client = None


# Global server state
state = ServerState()


# =============================================================================
# HTTP API Helpers
# =============================================================================


async def _api_post(
    endpoint: str, data: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Send POST request to AtCode API."""
    url = f"{API_BASE_URL.rstrip('/')}/{endpoint.lstrip('/')}"
    try:
        logger.debug(f"POST {url} with data: {data}")
        response = await state.http_client.post(url, json=data)
        response.raise_for_status()
        return response.json()
    except httpx.ConnectError as e:
        logger.error(f"Failed to connect to AtCode API at {url}: {e}")
        return {
            "success": False,
            "error": f"Cannot connect to AtCode API at {ATCODE_API_URL}. Is the backend running on port {API_PORT}?",
        }
    except httpx.HTTPStatusError as e:
        error_detail = e.response.json() if e.response.content else {"detail": str(e)}
        if isinstance(error_detail, dict):
            error_detail = error_detail.get("detail", str(error_detail))
        logger.error(f"HTTP error {e.response.status_code} from {url}: {error_detail}")
        return {"success": False, "error": error_detail}
    except Exception as e:
        logger.error(f"Unexpected error calling {url}: {e}")
        return {"success": False, "error": str(e)}


async def _api_get(
    endpoint: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Send GET request to AtCode API."""
    url = f"{API_BASE_URL.rstrip('/')}/{endpoint.lstrip('/')}"
    try:
        logger.debug(f"GET {url} with params: {params}")
        response = await state.http_client.get(url, params=params)
        response.raise_for_status()
        return response.json()
    except httpx.ConnectError as e:
        logger.error(f"Failed to connect to AtCode API at {url}: {e}")
        return {
            "success": False,
            "error": f"Cannot connect to AtCode API at {ATCODE_API_URL}. Is the backend running on port {API_PORT}?",
        }
    except httpx.HTTPStatusError as e:
        error_detail = e.response.json() if e.response.content else {"detail": str(e)}
        if isinstance(error_detail, dict):
            error_detail = error_detail.get("detail", str(error_detail))
        logger.error(f"HTTP error {e.response.status_code} from {url}: {error_detail}")
        return {"success": False, "error": error_detail}
    except Exception as e:
        logger.error(f"Unexpected error calling {url}: {e}")
        return {"success": False, "error": str(e)}


async def _api_delete(
    endpoint: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Send DELETE request to AtCode API."""
    url = f"{API_BASE_URL.rstrip('/')}/{endpoint.lstrip('/')}"
    try:
        logger.debug(f"DELETE {url} with params: {params}")
        response = await state.http_client.delete(url, params=params)
        response.raise_for_status()
        return response.json()
    except httpx.ConnectError as e:
        logger.error(f"Failed to connect to AtCode API at {url}: {e}")
        return {
            "success": False,
            "error": f"Cannot connect to AtCode API at {ATCODE_API_URL}. Is the backend running on port {API_PORT}?",
        }
    except httpx.HTTPStatusError as e:
        error_detail = e.response.json() if e.response.content else {"detail": str(e)}
        if isinstance(error_detail, dict):
            error_detail = error_detail.get("detail", str(error_detail))
        logger.error(f"HTTP error {e.response.status_code} from {url}: {error_detail}")
        return {"success": False, "error": error_detail}
    except Exception as e:
        logger.error(f"Unexpected error calling {url}: {e}")
        return {"success": False, "error": str(e)}


# =============================================================================
# Tool Definitions
# =============================================================================

TOOLS: list[Tool] = [
    # Project Management
    Tool(
        name="set_project",
        description="""Set the active project context for subsequent queries.

IMPORTANT: Call this FIRST before using any other AtCode tools.

The project_name will be used as a prefix for all qualified_name queries.
For example, if project_name="vllm", all qualified_name searches will be prefixed with "vllm."

Args:
    project_name: Name of the project/repository in the knowledge graph
    project_root: (Optional) Absolute path to the project root directory

Example:
    set_project(project_name="vllm_claude", project_root="/path/to/vllm")
""",
        inputSchema={
            "type": "object",
            "properties": {
                "project_name": {
                    "type": "string",
                    "description": "Name of the project in the knowledge graph",
                },
                "project_root": {
                    "type": "string",
                    "description": "Absolute path to the project root directory (optional)",
                },
            },
            "required": ["project_name"],
        },
    ),
    Tool(
        name="list_repos",
        description="""List all available repositories in the knowledge graph.

Use this to discover what projects are available for exploration.
Returns a list of repository names that can be used with set_project().

Note: Claude-built projects have "_claude" suffix (e.g., "vllm_claude").

No arguments required.
""",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    # Code Search & Discovery
    Tool(
        name="find_nodes",
        description="""Search for code elements (functions, classes, methods) in the knowledge graph.

This is the PRIMARY discovery tool - START HERE when exploring code.

**IMPORTANT - qualified_name Format:**
All results return qualified_name in format: "project_name.module.path.ClassName.method_name"
- Example: "vllm_claude.attention.PagedAttention.forward"
- The project_name prefix is automatically added based on set_project()

**SEARCH SYNTAX (Priority Order):**
- Single keyword: "attention", "flops", "cuda" (RECOMMENDED)
- OR logic (|): "flash|attn" finds EITHER word
- Glob patterns: "flash*attn" → regex "flash.*attn"
- Regex: ".*fused.*moe.*" for complex patterns

**TIPS:**
- PREFER single keywords: "flops" NOT "flops calculation"
- Use | for variants: "torch|cuda" instead of "torch cuda"

**WORKFLOW:**
1. Use find_nodes() to find your target
2. Use the returned qualified_name with get_code_snippet(), explore_code(), or find_calls()

Args:
    query: Search query (single word recommended, or use | for OR)
    search_strategy: "auto" (recommended), "pattern", "regex", "and", "exact"
    node_type: "Code" (default) = Function+Method+Class, "All" = no filter
    repo_name: Search in specific repo (use list_repos() to find names)

Returns: List of matching nodes with qualified_name, type, path, etc.
""",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (single keyword recommended)",
                },
                "search_strategy": {
                    "type": "string",
                    "enum": ["auto", "exact", "pattern", "regex", "and"],
                    "default": "auto",
                    "description": "Search strategy",
                },
                "node_type": {
                    "type": "string",
                    "enum": ["Code", "All"],
                    "default": "Code",
                    "description": "Node type filter",
                },
                "repo_name": {
                    "type": "string",
                    "description": "Target repository name (optional)",
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="find_calls",
        description="""Find call relationships for a function/method.

**CRITICAL - qualified_name Format:**
You MUST use the FULL qualified_name from find_nodes() results.
Format: "project_name.module.ClassName.method_name"
Example: "vllm_claude.worker.worker.execute_model"

Get qualified_name from find_nodes() results FIRST.

**WORKFLOW:**
1. find_nodes("function_name") → get qualified_name
2. find_calls(qualified_name="...", direction="incoming") → get callers
3. get_code_snippet(qualified_name="...") → get source code

Args:
    qualified_name: FULL qualified_name from find_nodes() results
        Example: "project.module.ClassName.method_name"
    direction:
        - "outgoing": What this function CALLS (dependencies)
        - "incoming": What CALLS this function (callers)
    depth: Traversal depth 1-5 (default: 1)
    repo_name: Repository name override when project context is not set

Example (after find_nodes):
    find_calls(
        qualified_name="vllm_claude.modeling_models.llama_llm_LlamaForCausalLM.forward",
        direction="outgoing",
        depth=2
    )
""",
        inputSchema={
            "type": "object",
            "properties": {
                "qualified_name": {
                    "type": "string",
                    "description": "FULL qualified_name from find_nodes() results. Format: 'project.module.ClassName.method'",
                },
                "direction": {
                    "type": "string",
                    "enum": ["outgoing", "incoming"],
                    "default": "outgoing",
                    "description": "Call direction: 'outgoing' = what this calls, 'incoming' = what calls this",
                },
                "depth": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "default": 1,
                    "description": "Search depth (how many levels to trace)",
                },
                "repo_name": {
                    "type": "string",
                    "description": "Target repository name (optional)",
                },
            },
            "required": ["qualified_name"],
        },
    ),
    Tool(
        name="get_children",
        description="""Get children of a node in the knowledge graph with intelligent type detection.

**UNDERSTANDING identifier_type:**
- "auto" (RECOMMENDED): Auto-detects based on identifier format
- "project": Query by project name (e.g., "vllm_claude")
- "folder": Query by file system path (e.g., "/path/to/project/src")
- "file": Query by file path or qualified_name (e.g., "src/main.py" or "project.module")
- "class": Query by qualified_name (e.g., "project.module.ClassName")

**BEHAVIOR BY PARENT TYPE:**
- identifier_type="project": Returns Folder, File (directory structure)
- identifier_type="folder": Returns sub-Folder, File children
- identifier_type="file": Returns Class, Function defined in this file
- identifier_type="class": Returns Method members of the class

**SPECIAL VALUES:**
- identifier="." or identifier="current": Uses current project from set_project()

**EXAMPLES:**
1. Get project structure:
   get_children(identifier=".", identifier_type="auto", depth=2)

2. Get class methods:
   get_children(identifier="project.module.ClassName", identifier_type="class")

3. Get file contents:
   get_children(identifier="src/models/llama.py", identifier_type="file")

Args:
    identifier: Project name, file path, or qualified_name depending on identifier_type
    identifier_type: "auto" (recommended), "project", "folder", "file", "class"
    depth: 1-5 (default: 1), how many levels to descend
    child_types: Filter e.g. "Class,Function" or "Folder,File"
    repo_name: Repository name override when project context is not set
""",
        inputSchema={
            "type": "object",
            "properties": {
                "identifier": {
                    "type": "string",
                    "description": "Project name, file path, or qualified_name. Use '.' for current project.",
                },
                "identifier_type": {
                    "type": "string",
                    "enum": ["auto", "project", "folder", "file", "class"],
                    "default": "auto",
                    "description": "Type of identifier: 'auto'=detect, 'project'=name, 'folder'=path, 'file'=path/qn, 'class'=qualified_name",
                },
                "depth": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "default": 1,
                    "description": "Depth of children to retrieve (1-5 levels)",
                },
                "child_types": {
                    "type": "string",
                    "description": "Filter by type (comma-separated), e.g., 'Class,Function' or 'Folder,File'",
                },
                "repo_name": {
                    "type": "string",
                    "description": "Target repository name (optional)",
                },
            },
            "required": ["identifier"],
        },
    ),
    # Code Retrieval
    Tool(
        name="get_code_snippet",
        description="""Retrieve source code for a specific function, class, or method.

**CRITICAL - qualified_name Format:**
You MUST use the FULL qualified_name from find_nodes() results.
Format: "project_name.module.path.ClassName.method_name"
Example: "vllm_claude.attention.PagedAttention.forward"

**WORKFLOW:**
1. find_nodes("attention") → returns list with qualified_name
2. get_code_snippet(qualified_name="vllm_claude.attention.PagedAttention.forward")

Args:
    qualified_name: FULL qualified_name from find_nodes() results
        Format: "project.module.ClassName.method_name"
        Example: "vllm_claude.modeling_models.llama_llm_LlamaForCausalLM.forward"
    repo_name: Repository name for cross-repo queries (optional)

Returns: Source code with line numbers, file path, docstring if available.
""",
        inputSchema={
            "type": "object",
            "properties": {
                "qualified_name": {
                    "type": "string",
                    "description": "FULL qualified_name from find_nodes(). Format: 'project.module.ClassName.method'",
                },
                "repo_name": {
                    "type": "string",
                    "description": "Repository name for cross-repo queries (optional)",
                },
            },
            "required": ["qualified_name"],
        },
    ),
    # Comprehensive Analysis
    Tool(
        name="explore_code",
        description="""ONE-STOP DEEP ANALYSIS - Get comprehensive context about a code element.

**CRITICAL - identifier (qualified_name) Format:**
You MUST use the FULL qualified_name from find_nodes() results.
Format: "project_name.module.path.ClassName.method_name"

**WHAT THIS RETURNS (in ONE call):**
- Source code with line numbers
- All functions that CALL this (incoming calls)
- All functions that THIS CALLS (outgoing dependencies)
- Dependency tree visualization
- Source code of dependencies

**WHEN TO USE:**
- You need complete understanding of a specific function
- Analyzing the MAIN target of a question
- Want to understand both callers and dependencies
- Need to see the full context before making changes

**WORKFLOW:**
1. find_nodes("function_name") → get qualified_name
2. explore_code(identifier="project.module.ClassName.method")

Args:
    identifier: FULL qualified_name from find_nodes() results
        Format: "project.module.ClassName.method_name"
        Example: "vllm_claude.worker.worker.execute_model"
    repo_name: Repository name for cross-repo queries (optional)
    max_dependency_depth: Depth for dependency tree (1-10, default: 5)
    include_dependency_source_code: Fetch source for dependencies (default: true)
""",
        inputSchema={
            "type": "object",
            "properties": {
                "identifier": {
                    "type": "string",
                    "description": "FULL qualified_name from find_nodes(). Format: 'project.module.ClassName.method'",
                },
                "repo_name": {
                    "type": "string",
                    "description": "Repository name for cross-repo queries (optional)",
                },
                "max_dependency_depth": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 5,
                    "description": "Depth for dependency tree (1-10, default: 5)",
                },
                "include_dependency_source_code": {
                    "type": "boolean",
                    "default": True,
                    "description": "Include source code for dependencies (default: true)",
                },
            },
            "required": ["identifier"],
        },
    ),
    # Graph Management (via HTTP API)
    Tool(
        name="build_graph",
        description="""Build a knowledge graph for the current project (Claude-assisted analysis).

IMPORTANT: This tool is for MEDIUM to LARGE projects where standard exploration
would benefit from a knowledge graph. Small projects (<100 files) typically don't need this.

**The graph will be automatically named "{project_name}_claude"** (e.g., "atcode" → "atcode_claude")
This suffix distinguishes Claude-built graphs from manual builds.

This is a LONG-RUNNING operation that runs asynchronously on the server.
The tool returns immediately with a job_id that can be used to track progress.

This operation:
- Scans all source code files in the project
- Builds a knowledge graph of functions, classes, and call relationships
- Enables powerful code exploration via other AtCode tools

Use this when:
- The project is medium-to-large (100+ files)
- User asks to "analyze this project" or "understand the codebase"
- Multiple complex code exploration questions are expected

DO NOT use this when:
- Project is small (<100 files)
- User has a simple one-off question

Args:
    project_path: Absolute path to the project root directory
    project_name: (Optional) Base project name (auto-appends "_claude" suffix, defaults to directory name)
        Example: project_name="atcode" creates "atcode_claude"
    fast_mode: (Optional) Skip semantic embeddings for faster build (default: false)

Returns: Job ID for tracking build progress.

Note: The build runs asynchronously in the background. Use the job_id to check status.
""",
        inputSchema={
            "type": "object",
            "properties": {
                "project_path": {
                    "type": "string",
                    "description": "Absolute path to the project root directory",
                },
                "project_name": {
                    "type": "string",
                    "description": "Base project name (will auto-append '_claude' suffix). Example: 'myproject' creates 'myproject_claude'",
                },
                "fast_mode": {
                    "type": "boolean",
                    "default": False,
                    "description": "Skip semantic embeddings for faster build",
                },
            },
            "required": ["project_path"],
        },
    ),
    Tool(
        name="refresh_graph",
        description="""Refresh (rebuild) an existing knowledge graph for a project.

Use this to update the graph after code changes. The project must have been
previously built with build_graph().

**Note:** The project_name will auto-append "_claude" suffix if not present.
Example: project_name="atcode" will refresh "atcode_claude"

This is a LONG-RUNNING operation that runs asynchronously on the server.
The tool returns immediately with a job_id that can be used to track progress.

Args:
    project_name: Base project name (auto-appends "_claude" if not present)
        Example: "atcode" refreshes "atcode_claude"
    fast_mode: (Optional) Skip semantic embeddings for faster build (default: false)

Returns: Job ID for tracking refresh progress.

Note: The refresh runs asynchronously in the background. Use the job_id to check status.
""",
        inputSchema={
            "type": "object",
            "properties": {
                "project_name": {
                    "type": "string",
                    "description": "Base project name (auto-appends '_claude' suffix). Example: 'myproject' targets 'myproject_claude'",
                },
                "fast_mode": {
                    "type": "boolean",
                    "default": False,
                    "description": "Skip semantic embeddings for faster build",
                },
            },
            "required": ["project_name"],
        },
    ),
    Tool(
        name="clean_graph",
        description="""Clean (delete) the knowledge graph for a project.

Use this to free up space or before rebuilding a project's graph from scratch.

**Note:** The project_name will auto-append "_claude" suffix if not present.
Example: project_name="atcode" will delete "atcode_claude"

Args:
    project_name: Base project name (auto-appends "_claude" if not present)
        Example: "atcode" deletes "atcode_claude"

Returns: Deletion statistics.
""",
        inputSchema={
            "type": "object",
            "properties": {
                "project_name": {
                    "type": "string",
                    "description": "Base project name (auto-appends '_claude' suffix). Example: 'myproject' deletes 'myproject_claude'",
                }
            },
            "required": ["project_name"],
        },
    ),
    Tool(
        name="get_job_status",
        description="""Get the status of a background graph build/refresh job.

Use this to track the progress of long-running operations started by build_graph()
or refresh_graph().

Args:
    job_id: The job ID returned by build_graph() or refresh_graph()

Returns: Job status including progress, current step, and completion status.
""",
        inputSchema={
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Job ID from build_graph() or refresh_graph()",
                }
            },
            "required": ["job_id"],
        },
    ),
    # Diagnostics
    Tool(
        name="check_health",
        description="""Check the health of MCP server connections.

This diagnostic tool verifies:
- AtCode HTTP API availability
- Current project context

Use this to diagnose connection issues when tools are not working as expected.

No arguments required.

Returns: Health status for all components with detailed error messages if any.
""",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    # Realtime Sync Control
    Tool(
        name="start_sync",
        description="""Start real-time file monitoring for a project's knowledge graph.

Watches for file changes (add/modify/delete) and automatically updates the
knowledge graph incrementally. Uses definition-level diff for efficient updates.

IMPORTANT: The project must already have a built knowledge graph.

Args:
    project_name: Name of the project in the knowledge graph
    repo_path: Absolute path to the project root directory
    skip_embeddings: Skip embedding generation for faster updates (default: false)
    track_variables: Track module/class-level variables in the graph (default: true)
    auto_watch: Start file monitoring immediately (default: true). If false, only initializes.
    subdirs: Comma-separated list of subdirectory names to monitor (e.g. "backend,frontend").
             If not provided, monitors the entire repo_path.
    initial_sync: Perform initial sync to detect offline changes (default: true).
                 When enabled, any file changes made while monitoring was stopped will be
                 detected and synced immediately when monitoring starts.

Returns: Status of the sync manager (started, initialized, or already_watching).
         If initial_sync detected changes, includes initial_sync stats.
""",
        inputSchema={
            "type": "object",
            "properties": {
                "project_name": {
                    "type": "string",
                    "description": "Name of the project in the knowledge graph",
                },
                "repo_path": {
                    "type": "string",
                    "description": "Absolute path to the project root directory",
                },
                "skip_embeddings": {
                    "type": "boolean",
                    "default": False,
                    "description": "Skip embedding generation for faster updates",
                },
                "track_variables": {
                    "type": "boolean",
                    "default": True,
                    "description": "Track module/class-level variables",
                },
                "auto_watch": {
                    "type": "boolean",
                    "default": True,
                    "description": "Start file monitoring immediately",
                },
                "subdirs": {
                    "type": "string",
                    "description": "Comma-separated subdirectory names to monitor (e.g. 'backend,frontend'). If omitted, monitors entire repo.",
                },
                "initial_sync": {
                    "type": "boolean",
                    "default": True,
                    "description": "Perform initial sync to detect offline changes (default: true)",
                },
            },
            "required": ["project_name", "repo_path"],
        },
    ),
    Tool(
        name="stop_sync",
        description="""Stop real-time file monitoring for a project.

Stops the file watcher and update queue. The knowledge graph remains
unchanged until sync is started again or a manual sync is triggered.

Args:
    project_name: Name of the project to stop monitoring

Returns: Status confirmation.
""",
        inputSchema={
            "type": "object",
            "properties": {
                "project_name": {
                    "type": "string",
                    "description": "Name of the project to stop monitoring",
                }
            },
            "required": ["project_name"],
        },
    ),
    Tool(
        name="sync_now",
        description="""Manually trigger an incremental sync for a project.

Detects all file changes since the last sync and updates the knowledge graph.
This is a one-shot operation - it does NOT start continuous monitoring.

Use this when you want to update the graph after making code changes,
without enabling continuous file watching.

Args:
    project_name: Name of the project in the knowledge graph
    repo_path: Absolute path to the project root directory (required for first call)
    skip_embeddings: Skip embedding generation for faster updates (default: false)

Returns: Update statistics (files added/modified/deleted, duration).
""",
        inputSchema={
            "type": "object",
            "properties": {
                "project_name": {
                    "type": "string",
                    "description": "Name of the project in the knowledge graph",
                },
                "repo_path": {
                    "type": "string",
                    "description": "Absolute path to the project root directory",
                },
                "skip_embeddings": {
                    "type": "boolean",
                    "default": False,
                    "description": "Skip embedding generation for faster updates",
                },
            },
            "required": ["project_name"],
        },
    ),
    Tool(
        name="get_sync_status",
        description="""Get the current sync status for a project.

Shows whether file monitoring is active, whether updates are being processed,
Git repository status, and the result of the latest update.

Args:
    project_name: Name of the project to check

Returns: Sync status including watching state, processing state, and latest update result.
""",
        inputSchema={
            "type": "object",
            "properties": {
                "project_name": {
                    "type": "string",
                    "description": "Name of the project to check",
                }
            },
            "required": ["project_name"],
        },
    ),
]


# =============================================================================
# Tool Handlers (Pure HTTP Mode)
# =============================================================================


async def handle_set_project(args: dict[str, Any]) -> str:
    """Handle set_project tool call."""
    project_name = args.get("project_name")
    project_root = args.get("project_root")

    if not project_name:
        return json.dumps({"error": "project_name is required"})

    # If no project_root provided, try to look it up from API
    if not project_root:
        try:
            result = await _api_get("/graph/projects")
            if result.get("projects"):
                for p in result["projects"]:
                    if p.get("name") == project_name:
                        project_root = p.get("path")
                        break
        except Exception as e:
            logger.warning(f"Could not fetch project path from API: {e}")

    state.set_project(project_name, project_root)

    return json.dumps(
        {
            "success": True,
            "project_name": project_name,
            "project_root": project_root,
            "message": f"Project context set to '{project_name}'",
        }
    )


async def handle_list_repos(args: dict[str, Any]) -> str:
    """Handle list_repos tool call via HTTP API."""
    try:
        result = await _api_get("/graph/projects")

        if result.get("success") is False:
            return json.dumps({"error": result.get("error", "Unknown error")})

        repos = [
            {"name": p.get("name")} for p in result.get("projects", []) if p.get("name")
        ]

        return json.dumps(
            {
                "success": True,
                "count": len(repos),
                "repositories": repos,
                "hint": "Use set_project(project_name='...') to select a repository",
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


async def handle_find_nodes(args: dict[str, Any]) -> str:
    """Handle find_nodes tool call via HTTP API."""
    if not state.current_project and not args.get("repo_name"):
        return json.dumps({"error": "No project context. Call set_project() first."})

    repo = args.get("repo_name") or state.current_project

    try:
        result = await _api_post(
            f"/graph/node/{repo}/find",
            {
                "query": args.get("query", ""),
                "search_strategy": args.get("search_strategy", "auto"),
                "node_type": args.get("node_type", "Code"),
            },
        )

        return json.dumps(result, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


async def handle_find_calls(args: dict[str, Any]) -> str:
    """Handle find_calls tool call via HTTP API."""
    if not state.current_project and not args.get("repo_name"):
        return json.dumps({"error": "No project context. Call set_project() first."})

    repo = args.get("repo_name") or state.current_project

    try:
        result = await _api_get(
            f"/graph/node/{repo}/calls",
            {
                "qualified_name": args.get("qualified_name", ""),
                "direction": args.get("direction", "outgoing"),
                "depth": args.get("depth", 1),
            },
        )

        return json.dumps(result, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


async def handle_get_children(args: dict[str, Any]) -> str:
    """Handle get_children tool call via HTTP API."""
    if not state.current_project and not args.get("repo_name"):
        return json.dumps({"error": "No project context. Call set_project() first."})

    repo = args.get("repo_name") or state.current_project

    try:
        params = {
            "identifier": args.get("identifier", ""),
            "identifier_type": args.get("identifier_type", "auto"),
            "depth": args.get("depth", 1),
        }
        if params["identifier"] in (".", "current"):
            params["identifier"] = repo
            params["identifier_type"] = "project"
        if args.get("child_types"):
            params["child_types"] = args.get("child_types")

        result = await _api_get(f"/graph/node/{repo}/children/enhanced", params)

        return json.dumps(result, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


async def handle_get_code_snippet(args: dict[str, Any]) -> str:
    """Handle get_code_snippet tool call via HTTP API."""
    if not state.current_project and not args.get("repo_name"):
        return json.dumps({"error": "No project context. Call set_project() first."})

    repo = args.get("repo_name") or state.current_project
    qualified_name = args.get("qualified_name", "")

    try:
        result = await _api_get(
            f"/graph/node/{repo}/code", {"qualified_name": qualified_name}
        )

        if result.get("success") is False:
            return json.dumps(result)

        return json.dumps(
            {
                "found": True,
                "qualified_name": result.get("qualified_name"),
                "file_path": result.get("file"),
                "line_start": result.get("start_line"),
                "line_end": result.get("end_line"),
                "source_code": result.get("code"),
                "docstring": result.get("docstring"),
            },
            default=str,
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


async def handle_explore_code(args: dict[str, Any]) -> str:
    """Handle explore_code tool call via HTTP API."""
    if not state.current_project and not args.get("repo_name"):
        return json.dumps({"error": "No project context. Call set_project() first."})

    repo = args.get("repo_name") or state.current_project

    try:
        result = await _api_post(
            f"/graph/node/{repo}/explore",
            {
                "identifier": args.get("identifier", ""),
                "max_dependency_depth": args.get("max_dependency_depth", 5),
                "include_dependency_source_code": args.get(
                    "include_dependency_source_code", True
                ),
            },
        )

        return json.dumps(result, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


def _normalize_claude_project_name(project_name: str) -> str:
    """Normalize project name to include _claude suffix if not present."""
    if not project_name.endswith("_claude"):
        return f"{project_name}_claude"
    return project_name


async def handle_build_graph(args: dict[str, Any]) -> str:
    """Handle build_graph tool call via HTTP API."""
    project_path = args.get("project_path")
    if not project_path:
        return json.dumps({"error": "project_path is required"})

    path = Path(project_path)
    if not path.exists():
        return json.dumps({"error": f"Path does not exist: {project_path}"})

    # Determine project name (auto-add _claude suffix)
    project_name = args.get("project_name") or path.name
    project_name = _normalize_claude_project_name(project_name)

    fast_mode = args.get("fast_mode", False)

    try:
        result = await _api_post(
            "/repos/add-local",
            {
                "local_path": str(path),
                "project_name": project_name,
                "skip_embeddings": fast_mode,
            },
        )

        if result.get("success"):
            return json.dumps(
                {
                    "success": True,
                    "project_name": project_name,
                    "job_id": result.get("task_id"),
                    "message": f"Graph build started for '{project_name}'. Use get_job_status(job_id='{result.get('task_id')}') to track progress.",
                    "fast_mode": fast_mode,
                }
            )
        else:
            return json.dumps(
                {
                    "success": False,
                    "project_name": project_name,
                    "error": result.get("error", "Unknown error"),
                }
            )
    except Exception as e:
        logger.error(f"Failed to build graph for '{project_name}': {e}")
        return json.dumps({"error": str(e)})


async def handle_refresh_graph(args: dict[str, Any]) -> str:
    """Handle refresh_graph tool call via HTTP API."""
    project_name = args.get("project_name")
    if not project_name:
        return json.dumps({"error": "project_name is required"})

    project_name = _normalize_claude_project_name(project_name)
    fast_mode = args.get("fast_mode", False)

    try:
        result = await _api_post(
            f"/graph/projects/{project_name}/refresh", {"fast_mode": fast_mode}
        )

        if result.get("success"):
            return json.dumps(
                {
                    "success": True,
                    "project_name": project_name,
                    "job_id": result.get("job_id"),
                    "message": f"Graph refresh started for '{project_name}'. Use get_job_status(job_id='{result.get('job_id')}') to track progress.",
                    "fast_mode": fast_mode,
                }
            )
        else:
            return json.dumps(
                {
                    "success": False,
                    "project_name": project_name,
                    "error": result.get("error", "Unknown error"),
                }
            )
    except Exception as e:
        logger.error(f"Failed to refresh graph for '{project_name}': {e}")
        return json.dumps({"error": str(e)})


async def handle_clean_graph(args: dict[str, Any]) -> str:
    """Handle clean_graph tool call via HTTP API."""
    project_name = args.get("project_name")
    if not project_name:
        return json.dumps({"error": "project_name is required"})

    project_name = _normalize_claude_project_name(project_name)

    try:
        result = await _api_delete(
            f"/repos/{project_name}", params={"delete_graph": "true"}
        )

        return json.dumps(
            {
                "success": result.get("success", True),
                "project_name": project_name,
                "deleted": result.get("deleted", []),
                "message": result.get("message", f"Cleaned project '{project_name}'"),
                "error": result.get("error"),
            },
            default=str,
        )
    except Exception as e:
        logger.error(f"Failed to clean project '{project_name}': {e}")
        return json.dumps({"error": str(e)})


async def handle_get_job_status(args: dict[str, Any]) -> str:
    """Handle get_job_status tool call via HTTP API."""
    job_id = args.get("job_id")
    if not job_id:
        return json.dumps({"error": "job_id is required"})

    try:
        result = await _api_get(f"/tasks/{job_id}")

        if result.get("success") is False:
            return json.dumps(
                {
                    "success": False,
                    "error": result.get("error", "Unknown error"),
                }
            )

        if result.get("task_id"):
            return json.dumps(
                {
                    "success": True,
                    "job_id": result.get("task_id"),
                    "status": result.get("status"),
                    "progress": result.get("progress", 0),
                    "step": result.get("step", ""),
                    "status_message": result.get("status_message", ""),
                    "error": result.get("error"),
                    "task_type": result.get("task_type", ""),
                    "repo_name": result.get("repo_name", ""),
                    "created_at": result.get("created_at"),
                    "started_at": result.get("started_at"),
                    "completed_at": result.get("completed_at"),
                    "queue_position": result.get("queue_position", 0),
                }
            )
        else:
            return json.dumps(
                {
                    "success": False,
                    "error": result.get("error", "Job not found"),
                }
            )
    except Exception as e:
        logger.error(f"Failed to get job status for '{job_id}': {e}")
        return json.dumps({"error": str(e)})


async def handle_check_health(args: dict[str, Any]) -> str:
    """Handle check_health tool call."""
    health = {
        "http_api": {"status": "unknown"},
        "project_context": {"status": "unknown"},
    }

    # Check HTTP API
    if state.http_client:
        try:
            result = await state.http_client.get(
                f"{ATCODE_API_URL}/api/health", timeout=5.0
            )
            if result.status_code == 200:
                health["http_api"] = {
                    "status": "healthy",
                    "url": ATCODE_API_URL,
                    "port": API_PORT,
                }
            else:
                health["http_api"] = {
                    "status": "error",
                    "url": ATCODE_API_URL,
                    "port": API_PORT,
                    "error": f"HTTP {result.status_code}",
                }
        except httpx.ConnectError as e:
            health["http_api"] = {
                "status": "error",
                "url": ATCODE_API_URL,
                "port": API_PORT,
                "error": f"Connection failed: {e}",
            }
        except Exception as e:
            health["http_api"] = {
                "status": "error",
                "url": ATCODE_API_URL,
                "port": API_PORT,
                "error": str(e),
            }
    else:
        health["http_api"]["status"] = "not_initialized"

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
# Sync Tool Handlers
# =============================================================================


async def handle_start_sync(args: dict[str, Any]) -> str:
    """Handle start_sync tool call via HTTP API."""
    project_name = args.get("project_name")
    repo_path = args.get("repo_path")

    if not project_name:
        return json.dumps({"error": "project_name is required"})
    if not repo_path:
        return json.dumps({"error": "repo_path is required"})

    path = Path(repo_path)
    if not path.exists():
        return json.dumps({"error": f"Path does not exist: {repo_path}"})

    skip_embeddings = args.get("skip_embeddings", False)
    track_variables = args.get("track_variables", True)
    auto_watch = args.get("auto_watch", True)
    subdirs = args.get("subdirs", "")
    initial_sync = args.get("initial_sync", True)

    try:
        url = (
            f"/api/sync/{project_name}/start"
            f"?repo_path={repo_path}"
            f"&skip_embeddings={str(skip_embeddings).lower()}"
            f"&track_variables={str(track_variables).lower()}"
            f"&auto_watch={str(auto_watch).lower()}"
            f"&initial_sync={str(initial_sync).lower()}"
        )
        if subdirs:
            url += f"&subdirs={subdirs}"

        result = await _api_post(url)

        if "error" in result and result.get("success") is False:
            return json.dumps({"success": False, "error": result["error"]})

        response = {
            "success": True,
            "project_name": project_name,
            "status": result.get("status", "unknown"),
            "message": result.get("message", "Sync started"),
        }
        # Include initial_sync result if present
        if "initial_sync" in result:
            response["initial_sync"] = result["initial_sync"]

        return json.dumps(response)
    except Exception as e:
        logger.error(f"Failed to start sync: {e}")
        return json.dumps({"error": str(e)})


async def handle_stop_sync(args: dict[str, Any]) -> str:
    """Handle stop_sync tool call via HTTP API."""
    project_name = args.get("project_name")
    if not project_name:
        return json.dumps({"error": "project_name is required"})

    try:
        result = await _api_post(f"/api/sync/{project_name}/stop")

        if "error" in result and result.get("success") is False:
            return json.dumps({"success": False, "error": result["error"]})

        return json.dumps(
            {
                "success": True,
                "project_name": project_name,
                "status": result.get("status", "stopped"),
                "message": result.get("message", "Sync stopped"),
            }
        )
    except Exception as e:
        logger.error(f"Failed to stop sync: {e}")
        return json.dumps({"error": str(e)})


async def handle_sync_now(args: dict[str, Any]) -> str:
    """Handle sync_now tool call via HTTP API."""
    project_name = args.get("project_name")
    if not project_name:
        return json.dumps({"error": "project_name is required"})

    repo_path = args.get("repo_path")
    skip_embeddings = args.get("skip_embeddings", False)

    try:
        params = f"?skip_embeddings={str(skip_embeddings).lower()}"
        if repo_path:
            params += f"&repo_path={repo_path}"

        result = await _api_post(f"/api/sync/{project_name}/now{params}")

        if "error" in result and result.get("success") is False:
            return json.dumps({"success": False, "error": result["error"]})

        return json.dumps(
            {
                "success": result.get("success", True),
                "project_name": project_name,
                "added": result.get("added", 0),
                "modified": result.get("modified", 0),
                "deleted": result.get("deleted", 0),
                "calls_rebuilt": result.get("calls_rebuilt", 0),
                "duration_ms": result.get("duration_ms", 0),
                "total_changes": result.get("total_changes", 0),
                "errors": result.get("errors", []),
            }
        )
    except Exception as e:
        logger.error(f"Failed to sync now: {e}")
        return json.dumps({"error": str(e)})


async def handle_get_sync_status(args: dict[str, Any]) -> str:
    """Handle get_sync_status tool call via HTTP API."""
    project_name = args.get("project_name")
    if not project_name:
        return json.dumps({"error": "project_name is required"})

    try:
        result = await _api_get(f"/api/sync/{project_name}/status")

        if "error" in result and result.get("success") is False:
            return json.dumps({"success": False, "error": result["error"]})

        return json.dumps(
            {
                "success": True,
                "project_name": project_name,
                "is_watching": result.get("is_watching", False),
                "is_processing": result.get("is_processing", False),
                "is_git_repo": result.get("is_git_repo", False),
                "current_ref": result.get("current_ref"),
                "pending_changes": result.get("pending_changes", 0),
                "latest_result": result.get("latest_result"),
            }
        )
    except Exception as e:
        logger.error(f"Failed to get sync status: {e}")
        return json.dumps({"error": str(e)})


# Tool handler dispatch map
TOOL_HANDLERS = {
    "set_project": handle_set_project,
    "list_repos": handle_list_repos,
    "find_nodes": handle_find_nodes,
    "find_calls": handle_find_calls,
    "get_children": handle_get_children,
    "get_code_snippet": handle_get_code_snippet,
    "explore_code": handle_explore_code,
    "build_graph": handle_build_graph,
    "refresh_graph": handle_refresh_graph,
    "clean_graph": handle_clean_graph,
    "get_job_status": handle_get_job_status,
    "check_health": handle_check_health,
    "start_sync": handle_start_sync,
    "stop_sync": handle_stop_sync,
    "sync_now": handle_sync_now,
    "get_sync_status": handle_get_sync_status,
}


# =============================================================================
# MCP Server
# =============================================================================


def create_server() -> Server:
    """Create and configure the MCP server."""
    server = Server("atcode-mcp")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """Return list of available tools."""
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        """Handle tool calls."""
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            return [
                TextContent(
                    type="text", text=json.dumps({"error": f"Unknown tool: {name}"})
                )
            ]

        try:
            result = await handler(arguments)
            return [TextContent(type="text", text=result)]
        except Exception as e:
            logger.error(f"Error calling tool {name}: {e}")
            return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    return server


async def run_server():
    """Run the MCP server."""
    # Initialize connections
    if not await state.initialize():
        logger.error("Failed to initialize server state")
        sys.exit(1)

    # Create server
    server = create_server()

    logger.info("Starting AtCode MCP Server (HTTP Mode)...")
    logger.info(f"API URL: {ATCODE_API_URL}")

    # Run with stdio transport
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


def main():
    """Main entry point."""
    import sys

    # Configure logging
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="<level>{level}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    )

    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    finally:
        # Close connections
        if state.http_client:
            try:
                asyncio.run(state.close())
            except Exception:
                pass


if __name__ == "__main__":
    main()

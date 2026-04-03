# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import operator
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
import uuid

from agent.llm import create_model
from agent.tools.code_tools import (
    CodeRetriever,
    FileReader,
    create_code_explorer_tool,
    create_code_retrieval_tool,
    create_file_reader_tool,
)
from agent.tools.graph_query import create_all_graph_query_tools
from agent.tools.semantic_search import create_semantic_search_tool
from core.config import settings
from core.prompts import (
    RECURSIVE_DOC_SYSTEM_CONTEXT,
    RESEARCH_SYSTEM_CONTEXT,
    DocMode,
    get_recursive_doc_prompt,
    get_research_doc_prompt,
)
from graph.service import MemgraphIngestor
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import Send
from loguru import logger
from typing_extensions import TypedDict

from .shared import (
    _get_tool_call_id,
    clean_messages_for_inheritance,
    extract_markdown_headings,
    generate_anchor,
    greedy_split_tool_names,
    invoke_with_retry,
    last_value,
    max_value,
)

# ============================================================================
# CONSTANTS
# ============================================================================

# Map depth to markdown heading level
DEPTH_TO_HEADING = {
    0: "#",  # Repository title (H1)
    1: "##",  # Major sections (H2)
    2: "###",  # Subsections (H3)
    3: "####",  # Sub-subsections (H4)
    4: "#####",  # Rarely used (H5)
}

# =============================================================================
# DOCUMENTATION MODE CONFIGURATIONS
# =============================================================================
# Two modes: "overview" (fast, architecture-focused) vs "detailed" (comprehensive)

# Overview mode: Fast, architecture-focused documentation
# - Focus on structure, design patterns, and key abstractions
# - Minimal code reading, mostly graph exploration
# - Chat model handles detailed user questions
OVERVIEW_MODE_TOOL_BUDGETS = {
    0: 100,  # Root level: discover structure and major components
    1: 80,  # Section level: understand key relationships
    2: 40,  # Subsection level: focused exploration
    3: 20,  # Deep level: minimal exploration
}

# Detailed mode: Comprehensive documentation (original behavior)
DETAILED_MODE_TOOL_BUDGETS = {
    0: 100,  # Root level: thorough exploration
    1: 80,  # Section level: moderate exploration
    2: 40,  # Subsection level: focused exploration
    3: 20,  # Deep level: minimal exploration
}

# Default to overview mode for efficiency
DEFAULT_TOOL_BUDGETS = OVERVIEW_MODE_TOOL_BUDGETS

# Context extraction settings
# Trigger extraction after this many tool calls
TOOL_CALL_EXTRACTION_THRESHOLD = 20
# Keep the most recent N tool messages uncompressed (for continuity)
KEEP_RECENT_TOOL_MESSAGES = 5
# Number of old tool messages to process in each extraction round
MESSAGES_TO_EXTRACT_PER_ROUND = 15
# Token threshold for auto-keep (small outputs not worth extracting)
AUTO_KEEP_TOKEN_THRESHOLD = 300
TOOL_CALL_XML_PATTERN = re.compile(
    r"<tool_call\b[^>]*>[\s\S]*?</tool_call>", re.IGNORECASE
)
TOOL_TAG_PATTERN = re.compile(
    r"</?(tool_call|tool_name|parameters)>", re.IGNORECASE
)
DOC_STRUCTURE_PATTERN = re.compile(r"(?m)^(#{1,6}\s+|[-*]\s+|\d+\.\s+|```|\|)")


def _truncate_preview(text: str, limit: int = 200) -> str:
    """Trim long text for trajectory/result previews."""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


async def _maybe_await(result: Any) -> Any:
    """Await callback results when needed."""
    if asyncio.iscoroutine(result):
        return await result
    return result


def _safe_json_dumps(value: Any) -> str:
    """Serialize arbitrary tool args deterministically for dedupe/debug."""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(value)


def _extract_tool_call_key(tool_name: str, tool_args: Any, tool_call_id: str | None) -> str:
    """Build a stable identity for a tool call."""
    if tool_call_id:
        return str(tool_call_id)
    return f"{tool_name}:{_safe_json_dumps(tool_args)}"


def _summarize_tool_args(tool_args: Any, max_items: int = 3, max_value_len: int = 150) -> str:
    """Render a compact tool argument preview."""
    if not isinstance(tool_args, dict) or not tool_args:
        return ""

    parts = []
    for key, value in list(tool_args.items())[:max_items]:
        value_str = str(value)
        if len(value_str) > max_value_len:
            value_str = value_str[:max_value_len].rstrip() + "..."
        parts.append(f"{key}={value_str}")
    return ", ".join(parts)


def _collect_new_tool_calls(
    messages: list[BaseMessage],
    seen_tool_call_keys: set[str],
) -> list[dict[str, Any]]:
    """Extract newly observed tool calls from the streamed LangGraph state."""
    tool_results: dict[str, str] = {}
    for msg in messages:
        if isinstance(msg, ToolMessage):
            tool_call_id = getattr(msg, "tool_call_id", None)
            if tool_call_id:
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                tool_results[str(tool_call_id)] = _truncate_preview(content, 220)

    new_calls: list[dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, AIMessage) or not getattr(msg, "tool_calls", None):
            continue

        for tc in msg.tool_calls:
            if isinstance(tc, dict):
                tool_name = tc.get("name", "unknown")
                tool_args = tc.get("args", {}) or {}
                tool_call_id = tc.get("id")
            else:
                tool_name = getattr(tc, "name", "unknown")
                tool_args = getattr(tc, "args", {}) or {}
                tool_call_id = getattr(tc, "id", None)

            call_key = _extract_tool_call_key(tool_name, tool_args, tool_call_id)
            if call_key in seen_tool_call_keys:
                continue

            seen_tool_call_keys.add(call_key)
            args_summary = _summarize_tool_args(tool_args)
            display = f"{tool_name}({args_summary})" if args_summary else f"{tool_name}()"
            new_calls.append(
                {
                    "key": call_key,
                    "name": tool_name,
                    "args_summary": args_summary,
                    "display": display,
                    "result_preview": tool_results.get(str(tool_call_id))
                    if tool_call_id
                    else None,
                }
            )

    return new_calls


def _sanitize_generated_markdown(markdown: str) -> str:
    """Remove tool-call scratchpad artifacts before saving user-facing docs."""
    clean = markdown.strip()
    if not clean:
        return ""

    if clean.startswith("<Doc>True</Doc>"):
        clean = clean[len("<Doc>True</Doc>") :].strip()

    had_tool_markup = bool(
        TOOL_CALL_XML_PATTERN.search(clean) or TOOL_TAG_PATTERN.search(clean)
    )
    clean = TOOL_CALL_XML_PATTERN.sub("", clean)
    clean = TOOL_TAG_PATTERN.sub("", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()

    if had_tool_markup:
        structure_match = DOC_STRUCTURE_PATTERN.search(clean)
        if structure_match:
            clean = clean[structure_match.start() :].lstrip()
        else:
            clean = ""

    return clean


# =============================================================================
# TOOL CREATION
# =============================================================================


def _create_tools(
    repo_path: str, ingestor: MemgraphIngestor, project_name: str
) -> tuple[list, list]:
    """
    Create all tools for the orchestrator, separated into two categories.

    This function creates a flattened tool architecture where tools are separated into:
    - update_tools: Node exploration tools that route to update_state_with_tool_results
    - retrieval_tools: Code retrieval tools that don't explore new nodes

    Tools are native LangChain BaseTool objects.

    Args:
        repo_path: Path to the repository being analyzed
        ingestor: MemgraphIngestor instance for database operations
        project_name: Name of the project

    Returns:
        Tuple of (update_tools, retrieval_tools) lists of LangChain BaseTool objects
    """
    # Initialize service components
    code_retriever = CodeRetriever(
        project_root=repo_path, ingestor=ingestor, project_name=project_name
    )
    file_reader = FileReader(
        project_root=repo_path, ingestor=ingestor, project_name=project_name
    )

    # Node exploration tools (route to update_state_with_tool_results)
    update_tools = [
        # Individual graph query tools (return QueryResult, explore new nodes)
        *create_all_graph_query_tools(ingestor, project_name=project_name),
        # Semantic search (returns QueryResult, explores nodes by semantic similarity)
        create_semantic_search_tool(repo_basename=project_name, ingestor=ingestor),
    ]

    # Code retrieval tools (don't explore nodes, just retrieve existing code)
    retrieval_tools = [
        # Advanced exploration tools (comprehensive code retrieval)
        create_code_explorer_tool(
            ingestor, project_name=project_name, project_root=repo_path
        ),
        # Code inspection (basic code snippet retrieval)
        create_code_retrieval_tool(code_retriever),
        # File content reader with pattern matching (read raw files, search within files)
        create_file_reader_tool(file_reader),
    ]

    return update_tools, retrieval_tools


# ============================================================================
# STATE DEFINITIONS
# ============================================================================


def merge_child_results(left: list[dict], right: list[dict]) -> list[dict]:
    """Reducer to merge results from parallel child agents."""
    return left + right


# ============================================================================
# CONTEXT COMPRESSION HELPERS
# ============================================================================


def extract_headings_from_content(content: str, base_depth: int = 0) -> list[dict]:
    """
    Extract headings from markdown content for right-nav.

    Args:
        content: Markdown content
        base_depth: Base depth level (0 for root sections)

    Returns:
        List of SectionHeading dicts with nested children
    """
    # Use shared utility for heading extraction
    return extract_markdown_headings(
        content, min_level=1, max_level=6, base_depth=base_depth
    )


def generate_section_filename(title: str, order: int, depth: int) -> str:
    """
    Generate a safe filename for a section.

    Args:
        title: Section title
        order: Section order (0-indexed)
        depth: Current depth level

    Returns:
        Safe filename like "001_architecture.md"
    """
    # Clean title for filename
    safe_title = re.sub(r"[^\w\s-]", "", title.lower())
    safe_title = re.sub(r"\s+", "_", safe_title)
    safe_title = safe_title[:40]  # Limit length

    if not safe_title:
        safe_title = f"section_{order}"

    # Format: depth prefix + order + title
    return f"{order + 1:03d}_{safe_title}.md"


class OutlineItem(TypedDict):
    """A single item in the documentation outline."""

    title: str
    description: str
    key_components: list[str]  # Qualified names from exploration
    suggested_children: list[str]  # Titles for potential sub-items
    order: int


class SectionHeading(TypedDict):
    """A heading extracted from section content for right-nav."""

    name: str
    anchor: str
    level: int  # 2 = H2, 3 = H3, etc.
    children: list  # Nested headings


class DocResult(TypedDict):
    """Result from generating documentation at any depth."""

    title: str
    content: str  # Markdown content (without heading - added by parent)
    depth: int
    order: int
    child_results: list[dict]  # Results from child agents
    explored_nodes: int
    error: str | None
    # New fields for multi-file support
    file_path: str | None  # Relative path to saved file (e.g., "sections/001_arch.md")
    headings: list[SectionHeading]  # Extracted headings for right-nav


class DocAgentState(TypedDict):
    """
    Unified state for documentation agent at any depth.

    This single state definition works for all depths:
    - depth=0: Root agent analyzing entire repository
    - depth=1: Section agent handling a major topic
    - depth=2+: Subsection agents handling specific areas

    The agent's behavior is controlled by:
    - current_depth: What level this agent is at
    - max_depth: Maximum recursion depth allowed
    - scope_*: What this agent is responsible for documenting

    IMPORTANT: Fields that can receive updates from parallel child agents
    MUST have reducers to handle concurrent updates.
    """

    # === Depth Control ===
    # These use max_value reducer to handle parallel child outputs
    current_depth: Annotated[int, max_value]
    max_depth: Annotated[int, max_value]

    # === Scope Definition ===
    # What this agent is responsible for documenting
    # These use last_value reducer since each agent has its own scope
    scope_title: Annotated[str, last_value]
    scope_description: Annotated[str, last_value]
    scope_key_components: Annotated[list[str], last_value]
    scope_suggested_children: Annotated[list[str], last_value]
    scope_order: Annotated[int, max_value]

    # === Context from Parent ===
    repo_name: Annotated[str, last_value]
    language: Annotated[str, last_value]
    wiki_doc_path: Annotated[str, last_value]
    parent_analysis: Annotated[str, last_value]

    # === Shared Context (available to all agents) ===
    # README content for repository context
    readme_content: Annotated[str, last_value]
    # Full outline from root agent (so child agents know the big picture)
    full_outline: Annotated[list[OutlineItem], last_value]
    # Whether this agent inherited parent's messages
    has_inherited_context: Annotated[bool, last_value]
    # User-specified focus areas for documentation emphasis
    focus_areas: Annotated[str, last_value]

    # === Inherited Context for Child Agents ===
    # Raw messages from parent agent (before scope-specific extraction)
    # These are passed to child agents and processed by extract_inherited_context node
    inherited_raw_messages: Annotated[list[BaseMessage], last_value]
    # Flag indicating if inherited context has been extracted
    inherited_context_extracted: Annotated[bool, last_value]

    # === Agent Working State ===
    messages: Annotated[list[BaseMessage], add_messages]
    explored_nodes: Annotated[list[dict[str, Any]], operator.add]

    # === Decision State ===
    # Whether this agent decided to delegate to children
    should_delegate: Annotated[bool, last_value]
    outline: Annotated[list[OutlineItem], last_value]

    # === Output ===
    # Final content if generating directly, or empty if delegating
    generated_content: Annotated[str, last_value]

    # Results from child agents (populated via reducer)
    child_results: Annotated[list[DocResult], merge_child_results]

    # === Control ===
    tool_call_count: Annotated[int, max_value]
    max_tool_calls: Annotated[int, max_value]
    current_step: Annotated[str, last_value]
    progress: Annotated[int, max_value]

    # === Context Extraction Control ===
    # Flag to trigger context extraction node
    need_extraction: Annotated[bool, last_value]
    # Track extraction rounds to avoid repeated extractions
    extraction_round: Annotated[int, max_value]
    # Store accumulated extraction summaries for continuity
    extraction_summaries: Annotated[list[str], operator.add]


# ============================================================================
# UNIFIED DOCUMENT ORCHESTRATOR
# ============================================================================


class DocOrchestrator:
    """
    Unified orchestrator for mode-based documentation generation.

    Supports multiple documentation modes:
    - REPOSITORY: Full repository documentation with hierarchical structure
    - RESEARCH: Deep topic/function research documentation

    Both modes use the same underlying workflow:
        Agent → Explore → Extract Context → Decide (delegate or generate) → Output

    Key Features:
    1. Single workflow definition that works at any depth
    2. MODE-BASED prompts - different prompts for repository vs research
    3. Configurable max_depth (doc_depth) for controlling detail level
    4. Agents autonomously decide whether to delegate or generate
    5. Proper heading hierarchy based on depth
    6. Two thoroughness levels: "overview" (fast) and "detailed" (comprehensive)
    7. State inheritance: child agents can inherit parent's exploration context

    Example Usage:
        # Repository documentation (default)
        orchestrator = DocOrchestrator(doc_mode=DocMode.REPOSITORY, doc_depth=2)

        # Research documentation
        orchestrator = DocOrchestrator(doc_mode=DocMode.RESEARCH, doc_depth=2)

        # Fast overview mode
        orchestrator = DocOrchestrator(doc_depth=2, mode="overview")

        # Detailed mode
        orchestrator = DocOrchestrator(doc_depth=2, mode="detailed")

        async for event in orchestrator.stream_generate(repo_name, ingestor, wiki_path):
            print(event)
    """

    def __init__(
        self,
        doc_depth: int = 2,
        mode: str = "overview",
        inherit_from_depth: int = 1,
        doc_mode: DocMode | str = DocMode.REPOSITORY,
        model_config: Any | None = None,
    ):
        """
        Initialize the unified document orchestrator.

        Args:
            doc_depth: Maximum depth for documentation hierarchy.
                       0 = only overview, 1 = sections, 2 = subsections, etc.
            mode: Thoroughness mode - "overview" (fast, architecture-focused) or
                  "detailed" (comprehensive, implementation-focused).
                  Default: "overview"
            inherit_from_depth: From which depth level child agents inherit parent's messages.
                       0 = no inheritance (each agent starts fresh)
                       1 = section agents (depth=1) inherit root's exploration context
                       2 = subsection agents (depth=2) inherit parent's context
                       Default: 1 (recommended - section agents benefit from root's exploration)
            doc_mode: Documentation mode - DocMode.REPOSITORY for full repo docs,
                      DocMode.RESEARCH for topic research.
                      Default: DocMode.REPOSITORY
            model_config: Optional model configuration. If provided, uses this instead
                         of settings.active_orchestrator_config. Allows per-request
                         model selection.
        """
        self.doc_depth = doc_depth
        self.mode = mode
        self.inherit_from_depth = inherit_from_depth
        self._model_config = model_config  # Store for LLM initialization

        # Handle string or enum for doc_mode
        if isinstance(doc_mode, str):
            self.doc_mode = (
                DocMode(doc_mode)
                if doc_mode in [m.value for m in DocMode]
                else DocMode.REPOSITORY
            )
        else:
            self.doc_mode = doc_mode

        self._init_llm()
        self._tool_cache: dict[str, tuple[list, list, list, datetime]] = {}
        self._tool_cache_ttl = 3600

        # Select tool budgets based on mode
        if mode == "detailed":
            self._tool_budgets = DETAILED_MODE_TOOL_BUDGETS
        else:
            self._tool_budgets = OVERVIEW_MODE_TOOL_BUDGETS

        logger.info(
            f"DocOrchestrator initialized: doc_mode={self.doc_mode.value}, "
            f"mode={mode}, depth={doc_depth}, inherit_from_depth={inherit_from_depth}"
        )

    def _init_llm(self):
        """Initialize LLM from settings or provided model_config."""
        # Use provided model_config if available, otherwise fall back to settings
        config = (
            self._model_config
            if self._model_config
            else settings.active_orchestrator_config
        )
        self.llm = create_model(config)
        self.model_name = config.model_id  # Store model name for metadata
        logger.info(
            f"DocOrchestrator LLM initialized: {config.model_id} ({config.provider})"
        )

    def _get_or_create_tools(
        self,
        repo_name: str,
        ingestor: MemgraphIngestor,
    ) -> tuple[list[Any], list[Any], list[Any]]:
        """Get cached tools or create new ones."""
        if repo_name in self._tool_cache:
            update_tools, retrieval_tools, all_tools, created_at = self._tool_cache[
                repo_name
            ]
            age = (datetime.now(UTC) - created_at).total_seconds()
            if age < self._tool_cache_ttl:
                return update_tools, retrieval_tools, all_tools

        # Get repository path from graph database
        repo_path = self._get_repo_path_from_graph(repo_name, ingestor)

        # Create tools (now native LangChain BaseTool objects)
        update_langchain, retrieval_langchain = _create_tools(
            repo_path, ingestor, repo_name
        )
        all_tools = update_langchain + retrieval_langchain

        self._tool_cache[repo_name] = (
            update_langchain,
            retrieval_langchain,
            all_tools,
            datetime.now(UTC),
        )
        logger.info(
            f"Created {len(all_tools)} tools for {repo_name} (path: {repo_path})"
        )

        return update_langchain, retrieval_langchain, all_tools

    def _get_repo_path_from_graph(
        self, repo_name: str, ingestor: MemgraphIngestor
    ) -> str:
        """Query the Project node to get the repository root path.

        Falls back to wiki_repos directory if path is not found in graph.
        """
        try:
            query = """
            MATCH (p:Project {name: $repo_name})
            RETURN p.path AS path
            LIMIT 1
            """
            results = ingestor.fetch_all(query, {"repo_name": repo_name})
            if results and results[0].get("path"):
                path = results[0]["path"]
                # Ensure path is absolute
                if path and not Path(path).is_absolute():
                    # Relative path - resolve against wiki_repos
                    from core.config import get_wiki_repos_dir

                    path = str(get_wiki_repos_dir() / repo_name)
                    logger.info(f"Resolved relative path to: {path}")
                else:
                    # Check if path exists, if not try new data directory
                    if not Path(path).exists():
                        from core.config import get_wiki_repos_dir

                        new_path = get_wiki_repos_dir() / repo_name
                        if new_path.exists():
                            logger.info(
                                f"Old path {path} not found, using new path {new_path}"
                            )
                            path = str(new_path)
                    else:
                        logger.info(f"Found repo path for {repo_name}: {path}")
                return path
        except Exception as e:
            logger.warning(f"Failed to get repo path for {repo_name}: {e}")

        # Fallback: use wiki_repos directory
        from core.config import get_wiki_repos_dir

        fallback_path = get_wiki_repos_dir() / repo_name

        if fallback_path.exists():
            logger.info(
                f"Using fallback wiki_repos path for {repo_name}: {fallback_path}"
            )
            return str(fallback_path)

        logger.warning(
            f"No repo path found for {repo_name}, wiki_repos fallback also not found"
        )
        return str(fallback_path)  # Return anyway, let caller handle missing path

    def _get_readme_content(
        self,
        repo_name: str,
        max_length: int = 8000,
        ingestor: MemgraphIngestor = None,
    ) -> str | None:
        """
        Get README.md content for the repository.

        Attempts to get the project path from the graph database first,
        falls back to wiki_repos directory if not found.

        Args:
            repo_name: Name of the repository
            max_length: Maximum content length to return (default: 8000 chars)
            ingestor: Optional ingestor to query project path from database

        Returns:
            README content if found, None otherwise
        """
        repo_path = None

        # Try to get path from database first
        if ingestor:
            project_path = ingestor.get_project_path(repo_name)
            if project_path:
                repo_path = Path(project_path)

        # Fallback to wiki_repos
        if not repo_path or not repo_path.exists():
            from core.config import get_wiki_repos_dir

            repo_path = get_wiki_repos_dir() / repo_name

        if not repo_path.exists():
            logger.warning(f"Repository path does not exist: {repo_path}")
            return None

        # Common README file names in priority order
        readme_candidates = [
            "README.md",
            "readme.md",
            "README.MD",
            "Readme.md",
            "README.rst",
            "readme.rst",
            "README.txt",
            "readme.txt",
            "README",
        ]

        readme_content = None

        for candidate in readme_candidates:
            readme_file = repo_path / candidate
            if readme_file.exists():
                try:
                    readme_content = readme_file.read_text(encoding="utf-8")
                    logger.info(
                        f"Read README from: {readme_file} ({len(readme_content)} chars)"
                    )
                    break
                except Exception as e:
                    logger.warning(f"Failed to read {readme_file}: {e}")

        if not readme_content:
            logger.info(f"No README found in {repo_path}")
            return None

        # Truncate if too long
        if len(readme_content) > max_length:
            readme_content = readme_content[:max_length] + "\n\n... (README truncated)"
            logger.info(f"README truncated to {max_length} chars")

        return readme_content

    def _get_tool_budget(self, depth: int) -> int:
        """Get the tool budget for a given depth level based on current mode."""
        return self._tool_budgets.get(depth, 5)

    def _get_heading_prefix(self, depth: int) -> str:
        """Get the markdown heading prefix for a given depth."""
        return DEPTH_TO_HEADING.get(depth, "#" * (depth + 1))

    def _compress_messages_for_inheritance(
        self,
        messages: list[BaseMessage],
        token_threshold: int = 500,
    ) -> list[BaseMessage]:
        """
        [DEPRECATED] Compress long ToolMessages using rule-based extraction.

        This method is deprecated in favor of LLM-based scope-specific extraction.
        The new approach uses _extract_relevant_context_for_scope() which:
        1. Extracts context specific to each child's documentation scope
        2. Uses LLM to understand semantic relevance
        3. Preserves important details that rules might miss

        This method is kept for backward compatibility but is no longer used
        in the main workflow.

        Args:
            messages: List of messages to compress
            token_threshold: Messages with more tokens than this will be compressed

        Returns:
            List of messages with long ToolMessages compressed
        """
        compressed = []

        for msg in messages:
            if isinstance(msg, ToolMessage):
                content = (
                    msg.content if isinstance(msg.content, str) else str(msg.content)
                )
                estimated_tokens = len(content) // 4

                # Skip already compressed messages
                if content.startswith("[Extracted]") or content.startswith(
                    "[Compressed]"
                ):
                    compressed.append(msg)
                    continue

                # Compress long messages
                if estimated_tokens > token_threshold:
                    # Extract key information from the tool output
                    compressed_content = self._extract_key_info_from_tool_output(
                        content
                    )
                    # Create new ToolMessage with compressed content
                    # Note: We create a NEW message (different ID) to avoid conflicts
                    new_msg = ToolMessage(
                        content=compressed_content,
                        tool_call_id=getattr(msg, "tool_call_id", "unknown"),
                    )
                    compressed.append(new_msg)
                    logger.debug(
                        f"Compressed ToolMessage from {estimated_tokens} to ~{len(compressed_content) // 4} tokens"
                    )
                else:
                    compressed.append(msg)
            else:
                compressed.append(msg)

        return compressed

    def _extract_key_info_from_tool_output(
        self, content: str, max_length: int = 800
    ) -> str:
        """
        [DEPRECATED] Extract key information from tool output using regex rules.

        This method is deprecated in favor of LLM-based scope-specific extraction.
        See _extract_relevant_context_for_scope() for the new approach.

        The rule-based approach has limitations:
        - Cannot understand semantic relevance to specific documentation scope
        - May extract irrelevant information or miss important context
        - All children get the same extraction regardless of their focus

        Args:
            content: Original tool output content
            max_length: Maximum length of compressed output

        Returns:
            Compressed content with key information
        """
        key_info: list[str] = []

        # Extract qualified names (e.g., package.module.Class)
        qualified_names = re.findall(r"[\w]+(?:\.[\w]+){1,5}", content)
        if qualified_names:
            unique_names = list(
                dict.fromkeys(qualified_names[:20])
            )  # Dedupe, limit to 20
            key_info.append(f"Found nodes: {', '.join(unique_names[:10])}")
            if len(unique_names) > 10:
                key_info.append(f"  ... and {len(unique_names) - 10} more")

        # Extract file paths
        file_paths = re.findall(r"[\w/\\]+\.(?:py|ts|js|java|go|rs|cpp|h)", content)
        if file_paths:
            unique_paths = list(dict.fromkeys(file_paths[:10]))
            key_info.append(f"Files: {', '.join(unique_paths[:5])}")

        # Look for function/class definitions
        definitions = re.findall(r"(?:def|class|function|interface)\s+(\w+)", content)
        if definitions:
            unique_defs = list(dict.fromkeys(definitions[:10]))
            key_info.append(f"Definitions: {', '.join(unique_defs)}")

        # Look for relationship indicators
        if "calls" in content.lower() or "imports" in content.lower():
            # Try to extract call/import relationships
            calls = re.findall(
                r"(\w+)\s*(?:calls|imports|uses)\s*(\w+)", content, re.IGNORECASE
            )
            if calls:
                key_info.append(f"Relationships: {calls[:5]}")

        # If we found structured info, use it
        if key_info:
            result = "[Compressed] " + "; ".join(key_info)
        else:
            # Fallback: just truncate with first and last parts
            if len(content) > max_length:
                half = max_length // 2
                result = f"[Compressed] {content[:half]}...\n...[truncated]...\n...{content[-half:]}"
            else:
                result = f"[Compressed] {content}"

        # Ensure we don't exceed max_length
        if len(result) > max_length:
            result = result[:max_length] + "..."

        return result

    async def _extract_relevant_context_for_scope(
        self,
        inherited_messages: list[BaseMessage],
        scope_title: str,
        scope_description: str,
        key_components: list[str],
        max_output_tokens: int = 2000,
    ) -> str:
        """
        Use LLM to extract relevant context from parent's exploration for this specific scope.

        Unlike rule-based extraction, this method:
        1. Understands the semantic relationship between parent's exploration and child's scope
        2. Extracts information specifically relevant to this child's documentation task
        3. Preserves important context that rules might miss

        Args:
            inherited_messages: Raw messages from parent agent
            scope_title: Title of this child agent's section
            scope_description: Description of what this section covers
            key_components: Key components this section should focus on
            max_output_tokens: Maximum tokens for extracted context

        Returns:
            Extracted context string relevant to this scope
        """
        if not inherited_messages:
            return ""

        # Build a summary of the parent's exploration for the LLM to analyze
        exploration_summary_parts = []

        for msg in inherited_messages:
            if isinstance(msg, AIMessage):
                content = (
                    msg.content if isinstance(msg.content, str) else str(msg.content)
                )
                # Include AI's reasoning/decisions (truncated if too long)
                if content and len(content) > 50:
                    truncated = (
                        content[:1500] + "..." if len(content) > 1500 else content
                    )
                    exploration_summary_parts.append(f"[Agent Decision]\n{truncated}")

            elif isinstance(msg, ToolMessage):
                content = (
                    msg.content if isinstance(msg.content, str) else str(msg.content)
                )
                tool_call_id = getattr(msg, "tool_call_id", "unknown")

                # Include tool results (truncated)
                if content:
                    truncated = (
                        content[:2000] + "..." if len(content) > 2000 else content
                    )
                    exploration_summary_parts.append(
                        f"[Tool Result: {tool_call_id}]\n{truncated}"
                    )

            elif isinstance(msg, HumanMessage):
                content = (
                    msg.content if isinstance(msg.content, str) else str(msg.content)
                )
                if content and len(content) > 50:
                    truncated = content[:500] + "..." if len(content) > 500 else content
                    exploration_summary_parts.append(f"[Task]\n{truncated}")

        if not exploration_summary_parts:
            return ""

        # Limit total input size
        exploration_text = "\n\n---\n\n".join(exploration_summary_parts)
        if len(exploration_text) > 30000:
            # Take first and last parts if too long
            exploration_text = (
                exploration_text[:15000]
                + "\n\n...[middle truncated]...\n\n"
                + exploration_text[-15000:]
            )

        # Convert key_components to strings (handle case where they might be dicts)
        key_components_str = "Not specified"
        if key_components:
            components_str_list = []
            for c in key_components[:10]:
                if isinstance(c, dict):
                    components_str_list.append(
                        c.get("name") or c.get("title") or str(c)
                    )
                else:
                    components_str_list.append(str(c))
            key_components_str = ", ".join(components_str_list)

        # Build extraction prompt
        extraction_prompt = f"""You are helping to extract relevant information from a parent agent's code exploration results.

## Your Task
A parent agent explored a code repository and gathered information. Now a child agent needs to write documentation for a SPECIFIC section. Extract ONLY the information relevant to that section.

## Child Agent's Section
- **Title:** {scope_title}
- **Description:** {scope_description}
- **Key Components:** {key_components_str}

## Parent Agent's Exploration Results
{exploration_text}

## Instructions
Extract and summarize the information from the parent's exploration that is RELEVANT to "{scope_title}":

1. **Relevant Nodes/Classes/Functions**: List any discovered code elements related to this section
2. **File Locations**: Note file paths where relevant code was found
3. **Relationships**: Describe any call relationships, inheritance, or dependencies relevant to this section
4. **Key Insights**: Summarize any architectural patterns or design decisions discovered
5. **Code Snippets**: Include any important code snippets that were found (if directly relevant)

**Important:**
- Focus ONLY on information relevant to "{scope_title}"
- Ignore exploration results about other sections/topics
- If no relevant information was found, say "No directly relevant information found in parent exploration."
- Be concise but preserve important technical details
- Include qualified names (e.g., package.module.ClassName) when available

## Extracted Context for "{scope_title}":
"""

        try:
            response = await invoke_with_retry(
                self.llm, [HumanMessage(content=extraction_prompt)], label="extraction",
                config=settings.active_llm_config,
            )
            extracted = (
                response.content if hasattr(response, "content") else str(response)
            )

            # Validate extraction
            if not extracted or len(extracted) < 20:
                logger.warning(
                    f"LLM extraction returned minimal content for scope '{scope_title}'"
                )
                return ""

            logger.info(
                f"Extracted {len(extracted)} chars of relevant context for scope '{scope_title}'"
            )
            return extracted

        except Exception as e:
            logger.error(f"Failed to extract context for scope '{scope_title}': {e}")
            return ""

    def _clean_messages_for_inheritance(
        self,
        messages: list[BaseMessage],
    ) -> list[BaseMessage]:
        """
        Clean messages before passing to child agents (synchronous version).

        This method:
        1. Removes AIMessages with unanswered tool_calls (prevents LLM parsing errors)
        2. Filters out SystemMessages (child will get its own)
        3. Preserves all ToolMessages as-is (extraction happens in extract_inherited_context_node)

        The actual scope-specific extraction is done by extract_inherited_context_node
        using LLM, which runs at the start of each child agent's workflow.

        Args:
            messages: List of messages to clean

        Returns:
            List of cleaned messages ready for inheritance
        """
        # Delegate to shared utility
        return clean_messages_for_inheritance(messages, remove_system_messages=True)

    def _build_extraction_evaluation_prompt(
        self,
        msgs_to_process: list[tuple[int, Any]],
        scope_title: str,
        scope_description: str,
        depth: int,
        extraction_round: int,
        previous_summaries: list[str],
    ) -> str:
        """Build the prompt for agent to evaluate tool outputs."""

        # Prepare tool outputs for evaluation
        tool_outputs = []
        for local_idx, (global_idx, msg) in enumerate(msgs_to_process):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            tool_call_id = getattr(msg, "tool_call_id", "unknown")

            # Estimate token count (rough: 1 token ≈ 4 chars)
            estimated_tokens = len(content) // 4

            tool_outputs.append(
                {
                    "index": local_idx,
                    "tool_call_id": tool_call_id,
                    "content": content,
                    "estimated_tokens": estimated_tokens,
                }
            )

        prompt = f"""You are reviewing your previous tool exploration results to extract key information for documentation.

**Current Documentation Context:**
- Scope: {scope_title}
- Description: {scope_description}
- Depth Level: {depth}
- Extraction Round: {extraction_round + 1}

"""
        if previous_summaries:
            prompt += "**Previous Extraction Summaries:**\n"
            for i, summary in enumerate(previous_summaries[-2:], 1):
                truncated = summary[:300] + "..." if len(summary) > 300 else summary
                prompt += f"Round {i}: {truncated}\n"
            prompt += "\n"

        prompt += f"""**Task:** Review the following {len(tool_outputs)} tool results and classify each one.

**Classification Options:**
- **KEEP_FULL**: This is core implementation code directly relevant to "{scope_title}".
  Keep the full content because:
  * It contains the main logic/algorithm being documented
  * It will be referenced in the final documentation
  * Losing this code would harm documentation quality

- **EXTRACT_INFO**: Extract key information and discard the raw output.
  Use this when:
  * The result contains useful node discoveries (classes, functions found)
  * The result shows important relationships or patterns
  * The full content is not needed, but the findings are valuable

- **MINIMAL_RECORD**: Just note what was explored.
  Use this when:
  * The exploration didn't find directly relevant results
  * It was exploratory/discovery work that informed your search direction
  * The specific content is not needed for documentation

**Tool Results to Evaluate:**

"""
        for item in tool_outputs:
            prompt += f"""
--- Tool Result [{item["index"]}] (≈{item["estimated_tokens"]} tokens) ---
{item["content"]}
"""

        prompt += """

**Response Format (JSON):**
```json
{
  "decisions": [
    {
      "index": 0,
      "decision": "KEEP_FULL" | "EXTRACT_INFO" | "MINIMAL_RECORD",
      "reason": "Brief explanation",
      "extracted_info": "If EXTRACT_INFO/MINIMAL_RECORD: key findings to preserve"
    },
    ...
  ],
  "exploration_summary": "Brief summary of overall exploration progress and key discoveries"
}
```

**Guidelines:**
1. For code exploration results with source code: Only KEEP_FULL if it's core implementation for this scope
2. For node discovery results (find_nodes, find_callers, etc.): Usually EXTRACT_INFO - capture the node names and relationships
3. For small outputs (<500 tokens): Consider KEEP_FULL if potentially useful
4. Be selective with KEEP_FULL - too many will bloat context
5. For EXTRACT_INFO: Capture qualified names, file paths, key relationships, patterns discovered
6. **CRITICAL**: Keep extracted_info concise (max 500 tokens / ~2000 chars). Focus on: discovered node names, file paths, key relationships. Omit verbose descriptions and full code snippets.

Please provide your evaluation:"""

        return prompt

    def _parse_extraction_decisions(
        self, response_content: str, expected_count: int
    ) -> dict:
        """Parse the LLM's extraction decisions from JSON response."""

        # Try to extract JSON from response
        try:
            # Look for JSON block
            json_match = re.search(r"```json\s*([\s\S]*?)\s*```", response_content)
            if json_match:
                json_str = json_match.group(1)
            else:
                # Try to find raw JSON object
                json_match = re.search(
                    r'\{[\s\S]*"decisions"[\s\S]*\}', response_content
                )
                if json_match:
                    json_str = json_match.group(0)
                else:
                    raise ValueError("No JSON found in response")

            decisions = json.loads(json_str)

            # Validate structure
            if "decisions" not in decisions:
                decisions = {"decisions": [], "exploration_summary": ""}

            # Ensure we have decisions for all messages
            while len(decisions["decisions"]) < expected_count:
                decisions["decisions"].append(
                    {
                        "index": len(decisions["decisions"]),
                        "decision": "EXTRACT_INFO",
                        "extracted_info": "Tool exploration result",
                    }
                )

            return decisions

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse extraction decisions: {e}")
            # Return default decisions
            return {
                "decisions": [
                    {
                        "index": i,
                        "decision": "EXTRACT_INFO",
                        "extracted_info": f"Tool result {i + 1}",
                    }
                    for i in range(expected_count)
                ],
                "exploration_summary": "Extraction parsing failed, using defaults.",
            }

    def _build_condensed_context_for_extraction(
        self,
        messages: list[BaseMessage],
        scope_title: str,
        scope_description: str,
        depth: int,
    ) -> str:
        """
        Build a condensed context for extraction LLM call.

        Instead of sending the full message history (expensive), we extract:
        1. Scope information
        2. Key exploration decisions from AIMessages (what the agent was looking for)
        3. Tool calls made (what tools were called and with what parameters)
        4. Previously extracted summaries

        This significantly reduces input tokens while preserving decision context.
        """
        context_parts = [
            "You are an assistant helping to extract key information from code exploration results.",
            "",
            "## Current Documentation Scope",
            f"- Title: {scope_title}",
            f"- Description: {scope_description}",
            f"- Depth Level: {depth}",
            "",
            "## Exploration Context",
            "The following summarizes the exploration journey so far:",
            "",
        ]

        # Build a map from tool_call_id to ToolMessage content for result lookup
        tool_results_map = {}
        for msg in messages:
            if isinstance(msg, ToolMessage):
                tool_call_id = getattr(msg, "tool_call_id", None)
                if tool_call_id:
                    content = (
                        msg.content
                        if isinstance(msg.content, str)
                        else str(msg.content)
                    )
                    tool_results_map[tool_call_id] = content

        # Extract key decisions/thoughts from AI messages AND their tool calls (last 5)
        ai_messages = [msg for msg in messages if isinstance(msg, AIMessage)]
        recent_ai = ai_messages[-5:] if len(ai_messages) > 5 else ai_messages

        for i, msg in enumerate(recent_ai):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            # Truncate long AI responses to key parts
            if len(content) > 400:
                content = content[:400] + "..."
            context_parts.append(f"[Agent thought {i + 1}]: {content}")

            # Also include tool calls AND their results for context
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                tool_summaries = []
                for tc in msg.tool_calls[:10]:  # Limit to 10 tool calls per message
                    if isinstance(tc, dict):
                        tool_name = tc.get("name", "unknown")
                        tool_args = tc.get("args", {})
                        tool_call_id = tc.get("id", None)
                    else:
                        tool_name = getattr(tc, "name", "unknown")
                        tool_args = getattr(tc, "args", {})
                        tool_call_id = getattr(tc, "id", None)

                    # Summarize args (keep it short)
                    args_summary = []
                    for k, v in list(tool_args.items())[:3]:
                        v_str = str(v)[:50] + "..." if len(str(v)) > 50 else str(v)
                        args_summary.append(f"{k}={v_str}")
                    args_str = ", ".join(args_summary) if args_summary else ""
                    tool_summaries.append(f"  - Called `{tool_name}({args_str})`")

                    # Add truncated result if available
                    if tool_call_id and tool_call_id in tool_results_map:
                        result = tool_results_map[tool_call_id]
                        # Truncate result to first 200 chars for context
                        if len(result) > 200:
                            result = result[:200] + "..."
                        tool_summaries.append(f"    → Result: {result}")

                if tool_summaries:
                    context_parts.extend(tool_summaries)

        context_parts.append("")
        context_parts.append("## Your Task")
        context_parts.append(
            "Review the tool outputs provided and extract key information."
        )
        context_parts.append(
            "Focus on: qualified names, file paths, relationships, patterns discovered."
        )
        context_parts.append(
            "Use the tool call context above to understand what the agent was searching for."
        )

        return "\n".join(context_parts)

    def _build_doc_agent_workflow(
        self,
        all_tools: list,
        current_depth: int,
        max_depth: int,
    ):
        """
        Build a workflow for a document agent at the specified depth.

        This is the core recursive builder. For non-leaf depths, it creates
        a child workflow and embeds it as a subgraph.

        Workflow Structure:
            agent → tools (loop) → decide → [delegate | generate]
                                      ↓
                            if delegate: dispatch → child_agent → aggregate
                            if generate: finalize
        """
        llm_with_tools = self.llm.bind_tools(all_tools)
        tool_node = ToolNode(all_tools)

        graph = StateGraph(DocAgentState)

        # =====================================================================
        # NODE DEFINITIONS
        # =====================================================================

        async def extract_inherited_context_node(state: DocAgentState) -> dict:
            """
            Extract relevant context from inherited parent messages for this specific scope.

            This node runs at the START of child agent workflows when they have
            inherited_raw_messages from their parent. It uses LLM to extract only
            the information relevant to this child's specific documentation scope.

            Key benefits over rule-based compression:
            1. Semantic understanding - LLM understands what's relevant to "LoRA Backend" vs "Scheduler"
            2. Scope-specific - Different children get different extracted context
            3. Lossless for relevant info - Important details are preserved, only irrelevant data is dropped
            """
            inherited_messages = state.get("inherited_raw_messages", [])
            already_extracted = state.get("inherited_context_extracted", False)

            # Skip if no inherited messages or already extracted
            if not inherited_messages or already_extracted:
                return {"inherited_context_extracted": True}

            scope_title = state.get("scope_title", "")
            scope_description = state.get("scope_description", "")
            key_components = state.get("scope_key_components", [])
            depth = state.get("current_depth", 0)

            logger.info(
                f"Extracting inherited context for scope '{scope_title}' at depth {depth}"
            )
            logger.info(f"Processing {len(inherited_messages)} inherited messages")

            # Use LLM to extract relevant context for this scope
            logger.info(f"Depth {depth}: '{scope_title}' - Calling LLM for context extraction (may wait for semaphore)...")
            extracted_context = await self._extract_relevant_context_for_scope(
                inherited_messages=inherited_messages,
                scope_title=scope_title,
                scope_description=scope_description,
                key_components=key_components,
            )
            logger.info(f"Depth {depth}: '{scope_title}' - LLM context extraction completed")

            # Build the context message to add to the conversation
            if extracted_context and len(extracted_context) > 50:
                context_message = HumanMessage(
                    content=f"""
## Relevant Context from Parent Exploration

The parent agent explored the repository and found the following information relevant to your section "{scope_title}":

{extracted_context}

---

Now, use this context along with your own exploration to generate comprehensive documentation for "{scope_title}".
You may need to explore further to fill in gaps or verify details.
"""
                )
                logger.info(
                    f"Added {len(extracted_context)} chars of extracted context for '{scope_title}'"
                )
                return {
                    "messages": [context_message],
                    "inherited_context_extracted": True,
                    "inherited_raw_messages": [],  # Clear raw messages to free memory
                }
            else:
                logger.info(
                    f"No relevant context extracted for '{scope_title}', starting fresh exploration"
                )
                return {
                    "inherited_context_extracted": True,
                    "inherited_raw_messages": [],  # Clear raw messages
                }

        async def agent_node(state: DocAgentState) -> dict:
            """
            Main agent node - explores and decides on documentation strategy.

            At any depth, the agent:
            1. Receives scope information (what to document)
            2. Uses tools to explore the codebase
            3. Decides whether to delegate (create outline) or generate content
            """
            messages = state["messages"]
            depth = state["current_depth"]
            max_d = state["max_depth"]
            has_inherited = state.get("has_inherited_context", False)

            # Build system prompt if not present
            if not any(isinstance(msg, SystemMessage) for msg in messages):
                # Get depth-appropriate prompt based on doc_mode and thoroughness mode
                focus = state.get("focus_areas", "")
                scope_title = state.get("scope_title", "")

                # Use unified prompt selection based on doc_mode
                if self.doc_mode == DocMode.RESEARCH:
                    # Research mode - use research prompts
                    system_prompt = get_research_doc_prompt(
                        depth=depth,
                        max_depth=max_d,
                        can_delegate=(depth < max_d),
                        has_inherited_context=has_inherited,
                        research_topic=scope_title,
                        research_description=focus,
                    )
                    # Use research system context
                    system_prompt += f"\n\n---\n{RESEARCH_SYSTEM_CONTEXT}"
                else:
                    # Repository mode - always use detailed prompts (overview mode removed)
                    system_prompt = get_recursive_doc_prompt(
                        depth=depth,
                        max_depth=max_d,
                        can_delegate=(depth < max_d),
                        has_inherited_context=has_inherited,
                        section_title=scope_title,
                        focus_areas=focus,
                    )
                    system_prompt += f"\n\n---\n{RECURSIVE_DOC_SYSTEM_CONTEXT}"

                # Add tool usage best practices to prevent hallucinated tool names
                system_prompt += "\n\n**TOOL USAGE RULES:**"
                system_prompt += "\n- Call ONE tool at a time, wait for result, then decide next action"
                system_prompt += (
                    "\n- Use EXACT tool names from the available tools list"
                )
                system_prompt += "\n- NEVER combine tool names (e.g., don't write 'find_nodesget_code_snippet')"
                system_prompt += "\n- Each tool call must be separate and complete"

                # Quality targets for generated documentation - reinforce link density,
                # diagram quality, and content structure at system-prompt level so
                # these requirements are visible throughout the entire conversation.
                system_prompt += "\n\n**QUALITY TARGETS (when generating <Doc>True</Doc>):**"
                system_prompt += "\n- **Link Density**: Aim for 30+ distinct [[qualified_name]] links. Convert EVERY class, function, method, module, and file name to [[qualified_name]] format. Use backticks ONLY for items NOT in the codebase."
                system_prompt += "\n- **Diagrams**: Include 2-3 small mermaid diagrams (4-6 nodes each, HARD MAX 8). Use SHORT labels (≤12 chars, last segment only). After each diagram, add prose mapping short labels to full [[qualified.name]] links."
                system_prompt += "\n- **Tables**: Use markdown tables for structured data (methods, parameters, backends, comparisons). Reference code entities with [[links]] in table cells."
                system_prompt += "\n- **Word Count**: Target 2500+ words with comprehensive coverage of architecture, components, data flow, and caller/callee relationships."

                # Add scope context
                system_prompt += f"\n**Current Depth:** {depth} / {max_d}"
                system_prompt += f"\n**Scope Title:** {state['scope_title']}"
                system_prompt += (
                    f"\n**Scope Description:** {state['scope_description']}"
                )
                system_prompt += f"\n**Repository:** {state['repo_name']}"

                # Add README context ONLY for root agent (depth=0)
                # Child agents already have context from parent_analysis and inherited messages
                if depth == 0:
                    readme = state.get("readme_content", "")
                    if readme:
                        # Truncate README if too long
                        max_readme_len = 6000
                        if len(readme) > max_readme_len:
                            readme = (
                                readme[:max_readme_len] + "\n\n... (README truncated)"
                            )
                        system_prompt += f"\n\n**Repository README (for context):**\n<readme>\n{readme}\n</readme>"
                        system_prompt += "\n\nUse this README to understand:"
                        system_prompt += "\n- Project purpose and goals"
                        system_prompt += "\n- Key features and capabilities"
                        system_prompt += "\n- Installation and usage patterns"
                        system_prompt += "\n- Architecture overview (if mentioned)"

                # Add full outline context for child agents (so they know the big picture)
                full_outline = state.get("full_outline", [])
                if full_outline and depth > 0:
                    outline_summary = "\n".join(
                        [
                            f"- **{item['title']}**: {item['description'][:100]}..."
                            for item in full_outline[:8]
                        ]
                    )
                    system_prompt += f"\n\n**Full Documentation Outline (your section is highlighted):**\n{outline_summary}"
                    system_prompt += f'\n\n**YOUR FOCUS:** You are responsible for "{state["scope_title"]}" - focus ONLY on this section\'s content.'

                # Add language instruction for output, not exploration
                lang = state.get("language", "en")
                if lang == "zh":
                    system_prompt += "\n**Output Language:** Please write the final documentation in Chinese (中文). Use English for code references and technical terms."

                # Show current tool usage and remaining budget to help model adjust strategy
                tool_count = state.get("tool_call_count", 0)
                max_tools = state.get("max_tool_calls", 40)
                remaining = max(0, max_tools - tool_count)
                system_prompt += f"\n**Tool Calls:** {tool_count} / {max_tools} used ({remaining} remaining)"
                if remaining < max_tools * 0.3:
                    system_prompt += (
                        " ⚠️ LOW BUDGET - focus on synthesizing existing results!"
                    )

                if state.get("scope_key_components"):
                    components = state["scope_key_components"][:15]
                    # Convert to strings (handle case where components might be dicts)
                    components_str = []
                    for c in components:
                        if isinstance(c, dict):
                            components_str.append(
                                c.get("name") or c.get("title") or str(c)
                            )
                        else:
                            components_str.append(str(c))
                    system_prompt += (
                        f"\n**Key Components to Explore:** {', '.join(components_str)}"
                    )

                if state.get("scope_suggested_children"):
                    children = state["scope_suggested_children"][:6]
                    # Convert to strings (handle case where children might be dicts)
                    children_str = []
                    for c in children:
                        if isinstance(c, dict):
                            children_str.append(
                                c.get("name") or c.get("title") or str(c)
                            )
                        else:
                            children_str.append(str(c))
                    system_prompt += (
                        f"\n**Suggested Sub-topics:** {', '.join(children_str)}"
                    )

                if state.get("parent_analysis") and not has_inherited:
                    # Only include parent_analysis if we didn't inherit full context
                    analysis = state["parent_analysis"][:1500]
                    system_prompt += f"\n\n**Parent Analysis Context:**\n{analysis}"

                # Add delegation guidance
                if depth < max_d:
                    system_prompt += "\n\n**DELEGATION OPTION:**"
                    system_prompt += "\nYou can choose to DELEGATE by generating an outline with sub-topics."
                    system_prompt += "\nOr you can GENERATE content directly if the scope is focused enough."
                    system_prompt += (
                        f"\nIf you delegate, sub-agents will handle depth={depth + 1}."
                    )
                else:
                    system_prompt += "\n\n**LEAF NODE:**"
                    system_prompt += "\nYou are at maximum depth. You MUST generate content directly."
                    system_prompt += (
                        "\nDo NOT create an outline - generate the final documentation."
                    )

                # Add inherited context notice
                if has_inherited:
                    system_prompt += "\n\n**NOTE:** You have inherited exploration context from your parent agent."
                    system_prompt += "\nThe previous messages contain valuable exploration results - use them!"
                    system_prompt += "\nFocus on deepening the exploration for YOUR specific section, not re-exploring the whole repo."

                messages = [SystemMessage(content=system_prompt)] + messages

            try:
                response = await invoke_with_retry(
                    llm_with_tools, messages, label=f"agent_node(depth={depth})",
                    config=settings.active_llm_config,
                    tools=all_tools,
                )
                # IMPORTANT: If we added a SystemMessage, include it in the return
                # so it gets persisted to state for force_generate_node to use
                if not any(isinstance(msg, SystemMessage) for msg in state["messages"]):
                    # SystemMessage was added locally, persist it to state
                    return {
                        "messages": [messages[0], response]
                    }  # [SystemMessage, AIMessage]
                return {"messages": [response]}
            except Exception as e:
                logger.error(f"Agent node failed at depth {depth} (after retries): {e}")
                tool_count = state.get("tool_call_count", 0)
                if tool_count > 0:
                    # We have exploration context — return a clean AIMessage
                    # so the router sends us to force_generate, which will
                    # synthesise documentation from the existing tool results.
                    logger.info(
                        f"Agent node failed but has {tool_count} tool calls of context, "
                        f"routing to force_generate to salvage exploration results"
                    )
                    return {
                        "messages": [
                            AIMessage(
                                content=(
                                    f"LLM invocation failed ({e}). "
                                    "I have exploration context from previous tool calls. "
                                    "Proceeding to generate documentation from existing results."
                                )
                            )
                        ]
                    }
                # No exploration context at all — nothing to salvage
                return {
                    "messages": [
                        AIMessage(
                            content=f"<Doc>True</Doc>\n\n*Error generating documentation: {e}*"
                        )
                    ]
                }

        async def tools_node(state: DocAgentState) -> dict:
            """
            Execute tools called by the agent.

            Only tracks tool calls and sets need_extraction flag.
            Actual context extraction is handled by extract_context_node via routing.
            """
            try:
                # Fix concatenated tool names (e.g. "find_nodesfind_nodes")
                # before execution, instead of just rejecting them
                messages = state.get("messages", [])
                last_msg = messages[-1] if messages else None
                if isinstance(last_msg, AIMessage) and getattr(last_msg, "tool_calls", None):
                    valid_tool_names = {t.name for t in all_tools}
                    fixed_calls: list[dict[str, Any]] = []
                    changed = False

                    for tc in last_msg.tool_calls:
                        if tc["name"] in valid_tool_names:
                            fixed_calls.append(tc)
                            continue

                        # Try splitting concatenated name
                        split_names = greedy_split_tool_names(tc["name"], valid_tool_names)
                        if split_names:
                            changed = True
                            merged_args = tc.get("args", {})
                            logger.info(
                                f"Splitting concatenated tool call '{tc['name']}' → {split_names}"
                            )
                            # Build param sets per tool from schemas
                            tool_schemas: dict[str, set[str]] = {}
                            for t in all_tools:
                                if t.name in split_names:
                                    schema = t.args_schema.schema() if hasattr(t, "args_schema") and t.args_schema else {}
                                    tool_schemas[t.name] = set(schema.get("properties", {}).keys())

                            for sub_name in split_names:
                                expected = tool_schemas.get(sub_name, set())
                                fixed_calls.append({
                                    "name": sub_name,
                                    "args": {k: v for k, v in merged_args.items() if k in expected},
                                    "id": f"call_{uuid.uuid4().hex[:24]}",
                                    "type": "tool_call",
                                })
                        else:
                            logger.warning(
                                f"Invalid tool call: '{tc['name']}' (not in {sorted(valid_tool_names)}), returning error"
                            )
                            return {
                                "messages": [
                                    ToolMessage(
                                        content=(
                                            f"Error: '{tc['name']}' is not a valid tool. "
                                            f"Available tools: {', '.join(sorted(valid_tool_names))}"
                                        ),
                                        tool_call_id=tc.get("id", "invalid_tool"),
                                    )
                                ],
                                "tool_call_count": state.get("tool_call_count", 0) + 1,
                                "need_extraction": False,
                            }

                    if changed:
                        # Rebuild the AIMessage with corrected tool_calls
                        new_msg = AIMessage(
                            content=last_msg.content,
                            tool_calls=fixed_calls,
                            response_metadata=getattr(last_msg, "response_metadata", {}),
                            id=last_msg.id,
                        )
                        state = {**state, "messages": messages[:-1] + [new_msg]}

                result = await tool_node.ainvoke(state)

                # If we fixed the AIMessage, include it in the result so the
                # graph state gets the corrected tool_calls (same ID = in-place
                # replacement via add_messages reducer).  Without this, the
                # graph keeps the original concatenated AIMessage while the
                # ToolMessages carry the new split tool_call_ids, causing
                # "unexpected tool_use_id" errors on the next LLM call.
                if changed:
                    result_messages = result.get("messages", [])
                    result["messages"] = [new_msg] + result_messages

                # Track tool calls
                tool_count = state.get("tool_call_count", 0)
                new_tool_messages = sum(
                    1
                    for msg in result.get("messages", [])
                    if isinstance(msg, ToolMessage)
                )
                new_total = tool_count + new_tool_messages
                result["tool_call_count"] = new_total

                # Check if we need to trigger context extraction
                # Only trigger if we haven't just done an extraction (check extraction_round)
                extraction_round = state.get("extraction_round", 0)
                # Calculate which extraction round we should be on based on tool count
                expected_round = new_total // TOOL_CALL_EXTRACTION_THRESHOLD

                if expected_round > extraction_round:
                    # We've crossed a threshold boundary, need extraction
                    result["need_extraction"] = True
                    logger.info(
                        f"Tool count {new_total} crossed threshold, marking for extraction (round {expected_round})"
                    )
                else:
                    result["need_extraction"] = False

                return result
            except Exception as e:
                logger.error(f"Tools node failed: {e}")
                return {
                    "messages": [
                        ToolMessage(content=f"Tool error: {e}", tool_call_id="error")
                    ]
                }

        async def extract_context_node(state: DocAgentState) -> dict:
            """
            Extract key insights from accumulated tool messages using hybrid approach:
            1. Heuristic pre-filtering: Auto-keep small outputs (< 500 tokens)
            2. LLM extraction: Process larger outputs to extract key information

            This approach minimizes LLM token usage while ensuring lossless extraction:
            - Small outputs: KEEP_FULL automatically (extraction cost > keeping cost)
            - Larger outputs: LLM extracts key findings (nodes, relationships, code patterns)

            CRITICAL: We use same-ID replacement to update messages in place.
            The `add_messages` reducer replaces messages when they have the same ID,
            which maintains message order and AIMessage <-> ToolMessage pairing.
            """
            messages = list(state.get("messages", []))
            depth = state.get("current_depth", 0)
            scope_title = state.get("scope_title", "")
            scope_description = state.get("scope_description", "")
            extraction_round = state.get("extraction_round", 0)
            previous_summaries = state.get("extraction_summaries", [])

            logger.info(
                f"Extract context node: depth={depth}, scope='{scope_title}', round={extraction_round + 1}"
            )

            # Find all ToolMessages with their indices and IDs
            tool_messages = [
                (i, msg)
                for i, msg in enumerate(messages)
                if isinstance(msg, ToolMessage)
            ]

            if len(tool_messages) <= KEEP_RECENT_TOOL_MESSAGES:
                # Not enough to process, just update state
                logger.info(
                    f"Only {len(tool_messages)} tool messages, skipping extraction"
                )
                return {
                    "need_extraction": False,
                    "extraction_round": extraction_round + 1,
                }

            # Determine which messages to process
            # Keep the most recent KEEP_RECENT_TOOL_MESSAGES unchanged
            if KEEP_RECENT_TOOL_MESSAGES > 0:
                older_tool_msgs = tool_messages[:-KEEP_RECENT_TOOL_MESSAGES]
            else:
                older_tool_msgs = tool_messages

            # Only process a batch of older messages per round
            # IMPORTANT: Skip messages that have already been extracted (start with "[Extracted]")
            unprocessed_older_msgs = [
                (idx, msg)
                for idx, msg in older_tool_msgs
                if not (
                    isinstance(msg.content, str)
                    and msg.content.startswith("[Extracted]")
                )
            ]
            msgs_to_process = unprocessed_older_msgs[:MESSAGES_TO_EXTRACT_PER_ROUND]

            if not msgs_to_process:
                logger.info("No old tool messages to process")
                return {
                    "need_extraction": False,
                    "extraction_round": extraction_round + 1,
                }

            # =========================================================
            # STEP 1: Heuristic Pre-filtering
            # =========================================================
            # Separate small outputs (auto-keep) from larger ones (need LLM extraction)
            # Use global_idx as the primary key for all mappings
            auto_keep_indices = set()  # global_idx of small outputs (auto KEEP_FULL)
            needs_extraction_list = []  # [(global_idx, msg)] for LLM processing

            for global_idx, msg in msgs_to_process:
                content = (
                    msg.content if isinstance(msg.content, str) else str(msg.content)
                )
                # Estimate tokens (rough: 1 token ≈ 4 chars)
                estimated_tokens = len(content) // 4

                if estimated_tokens < AUTO_KEEP_TOKEN_THRESHOLD:
                    # Small output: not worth extracting, auto keep
                    auto_keep_indices.add(global_idx)
                else:
                    # Larger output: needs LLM extraction
                    needs_extraction_list.append((global_idx, msg))

            logger.info(
                f"Pre-filtering: {len(auto_keep_indices)} auto-keep (small), "
                f"{len(needs_extraction_list)} need extraction"
            )

            # =========================================================
            # STEP 2: LLM Extraction (only for larger outputs)
            # =========================================================
            # Map: global_idx -> decision_info (directly use global_idx as key)
            llm_decisions = {}

            if needs_extraction_list:
                # Build prompt only for messages that need extraction
                evaluation_prompt = self._build_extraction_evaluation_prompt(
                    msgs_to_process=needs_extraction_list,  # [(global_idx, msg), ...]
                    scope_title=scope_title,
                    scope_description=scope_description,
                    depth=depth,
                    extraction_round=extraction_round,
                    previous_summaries=previous_summaries,
                )

                # Build condensed context for LLM (avoid sending full history)
                condensed_context = self._build_condensed_context_for_extraction(
                    messages=messages,
                    scope_title=scope_title,
                    scope_description=scope_description,
                    depth=depth,
                )

                eval_messages = [
                    SystemMessage(content=condensed_context),
                    HumanMessage(content=evaluation_prompt),
                ]

                try:
                    eval_response = await invoke_with_retry(
                        self.llm, eval_messages, label="eval_extraction",
                        config=settings.active_llm_config,
                    )
                    eval_content = (
                        eval_response.content
                        if hasattr(eval_response, "content")
                        else str(eval_response)
                    )
                    parsed_decisions = self._parse_extraction_decisions(
                        eval_content, len(needs_extraction_list)
                    )

                    # Map decisions back using global_idx
                    # parsed_decisions["decisions"][i] corresponds to needs_extraction_list[i]
                    for i, (global_idx, msg) in enumerate(needs_extraction_list):
                        llm_decisions[global_idx] = parsed_decisions["decisions"][i]

                    exploration_summary = parsed_decisions.get(
                        "exploration_summary", ""
                    )

                except Exception as e:
                    logger.warning(f"Failed to get extraction decisions: {e}")
                    # Fallback: extract basic info for all
                    for global_idx, msg in needs_extraction_list:
                        llm_decisions[global_idx] = {
                            "decision": "EXTRACT_INFO",
                            "extracted_info": "Tool exploration result (fallback)",
                        }
                    exploration_summary = (
                        f"Explored {len(needs_extraction_list)} code locations."
                    )
            else:
                exploration_summary = f"Processed {len(auto_keep_indices)} small tool outputs (auto-kept)."

            # =========================================================
            # STEP 3: Build message updates using same-ID replacement
            # =========================================================
            # CRITICAL: The `messages` field uses `add_messages` reducer.
            # According to the add_messages documentation:
            # "If a message in `right` has the same ID as a message in `left`,
            #  the message from `right` will replace the message from `left`."
            #
            # Strategy:
            # - For messages that need compression (EXTRACT_INFO or MINIMAL_RECORD):
            #   Create a new ToolMessage with the SAME ID as the original.
            #   This will replace the original message IN PLACE, maintaining order.
            # - For messages that should be kept (KEEP_FULL or auto_keep): do nothing

            message_updates = []
            kept_full_count = 0
            extracted_count = 0

            # Process only the messages in msgs_to_process (we're not touching recent ones)
            for global_idx, msg in msgs_to_process:
                # Check if it's an auto-keep (small output)
                if global_idx in auto_keep_indices:
                    # Keep as-is, don't add any update
                    kept_full_count += 1
                    continue

                # Check LLM decision
                decision_info = llm_decisions.get(global_idx)
                if decision_info is None:
                    # Safety fallback: keep original if no decision found
                    kept_full_count += 1
                    continue

                decision = decision_info.get("decision", "EXTRACT_INFO")

                if decision == "KEEP_FULL":
                    # Keep original ToolMessage, don't add any update
                    kept_full_count += 1
                else:
                    # EXTRACT_INFO or MINIMAL_RECORD:
                    # We need to replace this message with a compressed version
                    extracted_info = decision_info.get(
                        "extracted_info", "Tool exploration result"
                    )
                    original_tool_call_id = getattr(msg, "tool_call_id", "unknown")
                    compressed_content = f"[Extracted] {extracted_info}"

                    # Get the message ID for in-place replacement
                    # LangChain messages have an 'id' attribute that uniquely identifies them
                    msg_id = getattr(msg, "id", None)

                    if msg_id:
                        # Create a new ToolMessage with the SAME ID
                        # This will REPLACE the original message in place (not append)
                        message_updates.append(
                            ToolMessage(
                                content=compressed_content,
                                tool_call_id=original_tool_call_id,
                                id=msg_id,  # Same ID ensures in-place replacement!
                            )
                        )
                        extracted_count += 1
                    else:
                        # No ID available - this shouldn't happen but fallback to keeping original
                        logger.warning(
                            f"ToolMessage at index {global_idx} has no ID, keeping original"
                        )
                        kept_full_count += 1

            logger.info(
                f"Context extraction complete: processing {len(msgs_to_process)} messages, "
                f"kept_full={kept_full_count} (auto={len(auto_keep_indices)}), "
                f"compressed={extracted_count}"
            )

            return {
                "messages": message_updates,  # add_messages reducer will replace messages with same ID
                "need_extraction": False,
                "extraction_round": extraction_round + 1,
                "extraction_summaries": [exploration_summary]
                if exploration_summary
                else [],
            }

        def decide_node(state: DocAgentState) -> dict:
            """
            Analyze agent's output and decide: delegate or generate.

            Detection logic:
            - <Doc>True</Doc> marker → should_delegate = False (generate content)
            - <Doc>False</Doc> marker OR valid JSON outline array → should_delegate = True (delegate)
            - JSON array with title/description items → parse as outline for delegation
            """
            messages = state["messages"]
            depth = state["current_depth"]
            max_d = state["max_depth"]

            # At max depth, always generate
            if depth >= max_d:
                logger.info(f"Depth {depth}: At max_depth, forcing generate mode")
                return {"should_delegate": False}

            # Find the last AI message
            last_ai_content = ""
            for msg in reversed(messages):
                if isinstance(msg, AIMessage) and msg.content:
                    last_ai_content = msg.content
                    break

            # Detect markers
            has_doc_true = "<Doc>True</Doc>" in last_ai_content
            has_doc_false = "<Doc>False</Doc>" in last_ai_content

            # Log the decision-making process
            logger.info(
                f"Depth {depth}: Analyzing decision - has_doc_true={has_doc_true}, has_doc_false={has_doc_false}, content_len={len(last_ai_content)}, mode={self.mode}"
            )

            # Log first 500 chars of the content for debugging
            if last_ai_content:
                preview = last_ai_content[:500].replace("\n", " ")
                logger.debug(f"Depth {depth}: Content preview: {preview}...")

            # <Doc>True</Doc> means generate directly - check this first
            if has_doc_true and not has_doc_false:
                # In DETAILED mode at depth=0, check if there's also JSON (agent might have included both)
                if self.mode == "detailed" and depth == 0 and max_d > 0:
                    # Try to find JSON - if found, prefer delegation
                    try:
                        json_match = re.search(r"\[\s*\{", last_ai_content)
                        if json_match:
                            logger.info(
                                f"Depth {depth}: DETAILED mode - agent output both Doc marker and JSON, preferring DELEGATE"
                            )
                            # Fall through to JSON parsing
                        else:
                            logger.info(
                                f"Depth {depth}: Found <Doc>True</Doc> marker, choosing GENERATE mode"
                            )
                            return {"should_delegate": False}
                    except Exception:
                        pass
                else:
                    logger.info(
                        f"Depth {depth}: Found <Doc>True</Doc> marker, choosing GENERATE mode"
                    )
                    return {"should_delegate": False}

            # <Doc>False</Doc> explicitly means delegate - try to parse JSON outline
            if has_doc_false:
                logger.info(
                    f"Depth {depth}: Found <Doc>False</Doc> marker, agent chose to DELEGATE"
                )

            # Try to parse outline (delegate mode)
            outline = []
            try:
                # Find all JSON arrays that look like outline items (contain objects with "title")
                # Use a more specific pattern to find JSON array start
                json_start = last_ai_content.find("[")
                if json_start != -1:
                    # Try to find matching closing bracket
                    bracket_count = 0
                    json_end = -1
                    for i, char in enumerate(
                        last_ai_content[json_start:], start=json_start
                    ):
                        if char == "[":
                            bracket_count += 1
                        elif char == "]":
                            bracket_count -= 1
                            if bracket_count == 0:
                                json_end = i + 1
                                break

                    if json_end > json_start:
                        json_str = last_ai_content[json_start:json_end]
                        data = json.loads(json_str)
                        if isinstance(data, list) and len(data) > 0:
                            for i, item in enumerate(data):
                                if isinstance(item, dict) and item.get("title"):
                                    outline.append(
                                        OutlineItem(
                                            title=item.get("title", f"Section {i + 1}"),
                                            description=item.get("description", ""),
                                            key_components=item.get(
                                                "key_components", []
                                            ),
                                            suggested_children=item.get(
                                                "subsections",
                                                item.get("suggested_children", []),
                                            ),
                                            order=i,
                                        )
                                    )
                            logger.info(
                                f"Depth {depth}: Parsed {len(outline)} outline items from JSON"
                            )
            except json.JSONDecodeError as e:
                logger.warning(f"Depth {depth}: JSON parse error: {e}")
                # Try to extract just the first valid JSON array
                try:
                    # Find code block with json
                    code_match = re.search(
                        r"```json\s*([\s\S]*?)\s*```", last_ai_content
                    )
                    if code_match:
                        data = json.loads(code_match.group(1))
                        if isinstance(data, list) and len(data) > 0:
                            for i, item in enumerate(data):
                                if isinstance(item, dict) and item.get("title"):
                                    outline.append(
                                        OutlineItem(
                                            title=item.get("title", f"Section {i + 1}"),
                                            description=item.get("description", ""),
                                            key_components=item.get(
                                                "key_components", []
                                            ),
                                            suggested_children=item.get(
                                                "subsections",
                                                item.get("suggested_children", []),
                                            ),
                                            order=i,
                                        )
                                    )
                            logger.info(
                                f"Depth {depth}: Parsed {len(outline)} outline items from code block"
                            )
                except Exception as e2:
                    logger.debug(
                        f"Depth {depth}: Code block JSON parse also failed: {e2}"
                    )
            except Exception as e:
                logger.debug(f"Depth {depth}: No valid outline JSON found: {e}")

            if outline:
                logger.info(f"Depth {depth}: Delegating to {len(outline)} child agents")
                return {
                    "should_delegate": True,
                    "outline": outline,
                    "parent_analysis": last_ai_content[:],  # Pass analysis to children
                }

            # No outline found
            if has_doc_false:
                logger.warning(
                    f"Depth {depth}: Agent indicated delegation (<Doc>False</Doc>) but no valid JSON outline found"
                )

            # No outline and no doc marker - default to not delegating
            logger.info(
                f"Depth {depth}: No delegation outline found, defaulting to GENERATE mode"
            )
            return {"should_delegate": False}

        def finalize_node(state: DocAgentState) -> dict:
            """
            Extract final content when generating directly, save to file.

            Called when should_delegate = False.
            Now saves each section to its own file and returns metadata.
            """
            messages = state["messages"]
            depth = state["current_depth"]
            title = state["scope_title"]
            order = state["scope_order"]
            wiki_doc_path_str = state.get("wiki_doc_path", "")
            repo_name = state.get("repo_name", "")

            logger.info(f"Finalize node at depth {depth} for '{title}'")

            # Find content after <Doc>True</Doc>
            content = ""
            for msg in reversed(messages):
                if isinstance(msg, AIMessage) and msg.content:
                    if getattr(msg, "tool_calls", None):
                        # Tool-request messages are scratchpad, not final documentation.
                        continue
                    raw = msg.content
                    if "<Doc>True</Doc>" in raw:
                        parts = raw.split("<Doc>True</Doc>", 1)
                        if len(parts) > 1:
                            candidate = _sanitize_generated_markdown(parts[1])
                            if candidate:
                                content = candidate
                                break
                    elif len(raw) > 200:
                        # Use content even without marker if substantial,
                        # but strip any tool-call scratchpad artifacts first.
                        candidate = _sanitize_generated_markdown(raw)
                        if candidate:
                            content = candidate
                            break

            if not content:
                content = f"*Documentation for {title} could not be generated.*"

            logger.info(
                f"Finalize node at depth {depth}: extracted {len(content)} chars"
            )

            # Clean up heading levels - ensure they're appropriate for this depth
            # Content should use headings at depth+1 level
            child_heading = self._get_heading_prefix(depth + 1)

            # Replace any ## at start of line with appropriate level
            # But preserve deeper headings
            def fix_headings(match):
                heading = match.group(0)
                level = len(heading.strip().split()[0])  # Count #s
                # Adjust to be relative to current depth
                new_level = depth + 1 + (level - 2)  # -2 because ## is typically used
                new_level = min(new_level, 5)  # Cap at H5
                return "#" * new_level + " "

            content = re.sub(r"^(#{2,5})\s+", fix_headings, content, flags=re.MULTILINE)

            # Remove duplicate title if agent included it
            content = re.sub(
                rf"^{re.escape(child_heading)}\s*{re.escape(title)}\s*\n+",
                "",
                content,
                flags=re.MULTILINE | re.IGNORECASE,
            )

            explored_count = len(state.get("explored_nodes", []))

            # Extract headings for right-nav
            headings = extract_headings_from_content(content, base_depth=depth)

            # Generate filename and save section file (for depth >= 1)
            file_path = None
            if depth >= 1 and wiki_doc_path_str and repo_name:
                try:
                    wiki_doc_path = Path(wiki_doc_path_str)
                    sections_dir = wiki_doc_path / repo_name / "sections"
                    sections_dir.mkdir(parents=True, exist_ok=True)

                    filename = generate_section_filename(title, order, depth)
                    file_path = f"sections/{filename}"
                    full_path = wiki_doc_path / repo_name / file_path

                    # Add title heading to content for standalone file
                    heading_prefix = self._get_heading_prefix(depth)
                    full_content = f"{heading_prefix} {title}\n\n{content}"

                    full_path.write_text(full_content, encoding="utf-8")
                    logger.info(
                        f"Saved section file: {file_path} ({len(full_content)} chars)"
                    )

                    # Save messages history for regeneration support
                    try:
                        messages_filename = filename.replace(".md", ".messages.json")
                        messages_file_path = sections_dir / messages_filename

                        # Serialize messages to dict format
                        from langchain_core.messages import messages_to_dict

                        serialized_messages = messages_to_dict(messages)

                        # Get scope information from state
                        scope_info = {
                            "title": title,
                            "description": state.get("scope_description", ""),
                            "key_components": state.get("scope_key_components", []),
                            "suggested_children": state.get("outline", []),
                        }

                        # Get extraction summaries if available
                        extraction_summaries = state.get("extraction_summaries", [])

                        # Build messages metadata
                        messages_data = {
                            "section_id": filename.replace(".md", ""),
                            "section_title": title,
                            "depth": depth,
                            "order": order,
                            "generated_at": datetime.now(UTC).isoformat(),
                            "scope": scope_info,
                            "messages": serialized_messages,
                            "explored_nodes": state.get("explored_nodes", []),
                            "extraction_summaries": extraction_summaries,
                            "metadata": {
                                "tool_call_count": sum(
                                    1
                                    for msg in messages
                                    if isinstance(msg, ToolMessage)
                                ),
                                "extraction_round": len(extraction_summaries),
                                "parent_context_inherited": bool(
                                    state.get("parent_context")
                                ),
                            },
                        }

                        messages_file_path.write_text(
                            json.dumps(messages_data, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        logger.info(
                            f"Saved messages file: sections/{messages_filename}"
                        )
                    except Exception as e:
                        logger.error(f"Failed to save messages file for '{title}': {e}")
                        # Don't fail the whole operation if messages save fails

                except Exception as e:
                    logger.error(f"Failed to save section file for '{title}': {e}")
                    file_path = None

            return {
                "generated_content": content,
                "child_results": [
                    DocResult(
                        title=title,
                        content=content,
                        depth=depth,
                        order=order,
                        child_results=[],
                        explored_nodes=explored_count,
                        error=None,
                        file_path=file_path,
                        headings=headings,
                    )
                ],
            }

        def dispatch_node(state: DocAgentState) -> dict:
            """
            Prepare dispatch information (node returns dict, routing function returns Send list).

            Called when should_delegate = True.
            This node just updates state - the actual Send list is returned by the routing function.
            """
            outline = state.get("outline", [])
            depth = state["current_depth"]

            if not outline:
                logger.warning(f"Dispatch called but no outline at depth {depth}")
                return {"current_step": "dispatch_failed"}

            logger.info(
                f"Depth {depth}: Preparing dispatch for {len(outline)} child agents"
            )

            # Return dict to update state - the actual Send list will be in routing function
            return {
                "current_step": "dispatching",
                "progress": 40,
            }

        def aggregate_node(state: DocAgentState) -> dict:
            """
            Aggregate results from child agents.

            Called after all child agents complete.

            IMPORTANT: Only return fields that need updating.
            Don't return fields that would conflict across multiple inputs.
            """
            child_results = state.get("child_results", [])
            depth = state["current_depth"]
            title = state["scope_title"]

            logger.info(
                f"=== Aggregate node at depth {depth} ('{title}'): received {len(child_results)} child_results ==="
            )
            # Log each received result with its nested structure
            for i, r in enumerate(child_results):
                if isinstance(r, dict):
                    r_title = r.get("title", "?")
                    r_depth = r.get("depth", -1)
                    r_has_file = bool(r.get("file_path"))
                    r_children = r.get("child_results", [])
                    logger.info(
                        f"  Received[{i}]: title='{r_title}', depth={r_depth}, file={r_has_file}, children={len(r_children)}"
                    )
                    # Log nested children
                    if r_children:
                        for j, c in enumerate(r_children):
                            if isinstance(c, dict):
                                c_title = c.get("title", "?")
                                c_depth = c.get("depth", -1)
                                c_has_file = bool(c.get("file_path"))
                                c_children = c.get("child_results", [])
                                logger.info(
                                    f"    Child[{j}]: title='{c_title}', depth={c_depth}, file={c_has_file}, children={len(c_children)}"
                                )

            if not child_results:
                logger.warning(f"No child results to aggregate at depth {depth}")
                return {
                    "generated_content": f"*No content generated for {title}*",
                }

            # Deduplicate child results by title FIRST to avoid processing duplicates
            seen_titles = set()
            unique_results = []
            for r in child_results:
                if isinstance(r, dict):
                    result_title = r.get("title", "")
                else:
                    result_title = getattr(r, "title", "")

                if result_title and result_title not in seen_titles:
                    seen_titles.add(result_title)
                    unique_results.append(r)
                elif not result_title:
                    unique_results.append(r)  # Keep results without titles

            # Sort by order
            sorted_results = sorted(
                unique_results,
                key=lambda r: r.get("order", 0) if isinstance(r, dict) else 0,
            )

            # Build combined content
            lines = []
            child_heading = self._get_heading_prefix(depth + 1)

            for result in sorted_results:
                if isinstance(result, dict):
                    content = result.get("content", "")
                    result_title = result.get("title", "")
                else:
                    # DocResult object
                    content = result.content
                    result_title = result.title

                if content:
                    # Check if content already starts with the expected heading
                    # to avoid duplicate headings
                    expected_heading_pattern = (
                        rf"^{re.escape(child_heading)}\s+{re.escape(result_title)}\s*$"
                    )
                    content_already_has_heading = re.match(
                        expected_heading_pattern,
                        content.strip(),
                        re.MULTILINE | re.IGNORECASE,
                    )

                    if not content_already_has_heading:
                        # Add heading for this child only if not already present
                        lines.append(f"{child_heading} {result_title}")
                        lines.append("")

                    lines.append(content)
                    lines.append("")

            combined_content = "\n".join(lines)

            # This node's result (includes children's content)
            explored_count = len(state.get("explored_nodes", []))
            for result in unique_results:  # Use deduplicated results
                # DocResult is a TypedDict, so we check for dict with expected keys
                if isinstance(result, dict):
                    explored_count += result.get("explored_nodes", 0) or 0

            logger.info(
                f"Aggregate node at depth {depth}: generated {len(combined_content)} chars from {len(unique_results)} unique results (deduped from {len(child_results)})"
            )

            # Log the structure of unique_results for debugging
            for i, r in enumerate(unique_results):
                if isinstance(r, dict):
                    r_title = r.get("title", "?")
                    r_depth = r.get("depth", -1)
                    r_has_file = bool(r.get("file_path"))
                    r_children = r.get("child_results", [])
                    logger.info(
                        f"  Aggregate input[{i}]: title='{r_title}', depth={r_depth}, file={r_has_file}, children={len(r_children)}"
                    )

            # Collect all headings from children for navigation
            all_headings = []
            for result in sorted_results:
                if isinstance(result, dict):
                    child_headings = result.get("headings", [])
                    file_path = result.get("file_path")
                    child_title = result.get("title", "")
                    if child_headings or file_path:
                        # Create nav entry for this child section
                        anchor = generate_anchor(child_title)

                        all_headings.append(
                            {
                                "name": child_title,
                                "anchor": anchor,
                                "level": depth + 2,  # H2 for depth=0 children
                                "file_path": file_path,
                                "children": child_headings,
                            }
                        )

            # Only return fields that aggregate properly
            # Don't return fields that would be set by multiple child agents
            # IMPORTANT: Don't include combined_content in child_results to avoid duplication
            # The combined_content will be used by the parent's aggregate_node

            aggregate_result = DocResult(
                title=title,
                content="",  # Empty content - content is in child_results children instead
                depth=depth,
                order=state["scope_order"],
                # Store the deduplicated child results for building hierarchies
                child_results=unique_results,
                explored_nodes=explored_count,
                error=None,
                file_path=None,  # Parent sections don't have their own file
                headings=all_headings,  # Aggregated headings from children
            )

            logger.info(
                f"Aggregate node at depth {depth}: returning DocResult with title='{title}', {len(unique_results)} nested children"
            )

            return {
                "generated_content": combined_content,
                "child_results": [aggregate_result],
            }

        # =====================================================================
        # ROUTING LOGIC
        # =====================================================================

        def should_continue_agent(state: DocAgentState) -> str:
            """Route from agent node."""
            messages = state["messages"]
            last_msg = messages[-1] if messages else None
            tool_count = state.get("tool_call_count", 0)
            max_tools = state.get("max_tool_calls", 40)
            depth = state.get("current_depth", 0)

            if not last_msg:
                return "decide"

            if isinstance(last_msg, AIMessage):
                content = last_msg.content or ""

                # Check for doc marker or outline
                if "<Doc>True</Doc>" in content:
                    return "decide"

                # Check for JSON array (outline)
                if re.search(r"\[\s*\{", content):
                    return "decide"

                # Check for tool calls
                if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                    # Reserve last few calls for documentation generation.
                    # With aggressive context extraction, we can safely use ~94%
                    # of the budget for exploration before forcing generation.
                    budget_threshold = max_tools - 5

                    if tool_count < max_tools:
                        # If approaching budget limit, check if we should force doc generation
                        if tool_count >= budget_threshold:
                            logger.warning(
                                f"Tool budget near limit ({tool_count}/{max_tools}, threshold={budget_threshold}) at depth {depth}, forcing documentation generation"
                            )
                            return "force_generate"
                        return "tools"
                    else:
                        logger.info(
                            f"Tool budget exhausted ({tool_count}/{max_tools}) at depth {depth}"
                        )
                        return "force_generate"

                # If substantial content, go to decide
                if len(content) > 500:
                    return "decide"

                # LLM failed but we have exploration context — salvage it
                if tool_count > 0:
                    logger.info(
                        f"Agent returned short content with {tool_count} tool calls, routing to force_generate"
                    )
                    return "force_generate"

            # After tool execution, back to agent
            if isinstance(last_msg, ToolMessage):
                return "agent"

            return "decide"

        async def force_generate_node(state: DocAgentState) -> dict:
            """
            Force a FINAL DECISION (outline vs documentation) when tool budget is exhausted.

            Key changes:
            - No longer "forcing documentation", but forcing model to choose between outline (JSON) and final documentation (<Doc>True</Doc>)
            - Still prohibits further tool usage (budget exhausted), only lets model wrap up based on existing exploration results

            This node reuses the existing message history (which already contains
            the SystemMessage with all format rules, language settings, mermaid requirements)
            and simply adds a HumanMessage to force the LLM to make a final choice:
            - Either return an OUTLINE JSON array for delegation
            - Or return FINAL DOCUMENTATION starting with <Doc>True</Doc>
            """
            messages = list(state["messages"])
            depth = state["current_depth"]
            title = state["scope_title"]

            logger.info(
                f"Force generating documentation for '{title}' at depth {depth}"
            )

            # Clean up messages: Remove AIMessages with unanswered tool_calls
            # This prevents LLM parsing errors when the last AIMessage has tool_calls
            # but no corresponding ToolMessage response
            cleaned_messages = []

            for i, msg in enumerate(messages):
                if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                    # Check if this AIMessage's tool_calls have corresponding ToolMessages
                    tool_call_ids = {
                        _get_tool_call_id(tc)
                        for tc in msg.tool_calls
                        if _get_tool_call_id(tc) is not None
                    }

                    # Look for ToolMessages that answer these tool_calls
                    answered_ids = set()
                    if tool_call_ids:
                        for j in range(i + 1, len(messages)):
                            if isinstance(messages[j], ToolMessage):
                                tool_id = getattr(messages[j], "tool_call_id", None)
                                if tool_id in tool_call_ids:
                                    answered_ids.add(tool_id)

                    if tool_call_ids and answered_ids != tool_call_ids:
                        # Some tool_calls were not answered - create a clean AIMessage without tool_calls
                        # but keep the content if any
                        content = msg.content if msg.content else ""
                        if content:
                            cleaned_messages.append(AIMessage(content=content))
                        # Skip the original message with unanswered tool_calls
                        continue

                cleaned_messages.append(msg)

            # Reinforce key quality requirements at the critical generation moment.
            # After many tool calls, the system prompt may be far back in context,
            # so we re-emphasize the scoring-critical dimensions: links, diagrams, structure.
            force_prompt = f"""**Tool budget exhausted for "{title}".**

Stop exploring. Based on what you've learned, either:
1. Return a JSON outline array to delegate, OR
2. Start with `<Doc>True</Doc>` and generate the documentation now.

**If generating documentation, remember these CRITICAL quality requirements:**

**Links (MOST IMPORTANT):** Use [[qualified_name]] for EVERY class, function, method, module, and file you discovered. Aim for 40+ distinct [[links]]. Scan your output and convert any unlinked code entity names to [[qualified_name]] format. Use backticks ONLY for items NOT in the codebase (constants, parameters, config values).

**Mermaid Diagrams:** Keep diagrams to 4-6 nodes (HARD MAX 8). Use SHORT labels (≤12 chars, last segment only). Close ``` immediately after the last edge. After each diagram, add a prose section mapping short labels to full [[qualified.name]] links. For complex systems, use 2-3 small separate diagrams rather than one large one.

**Tables:** Prefer tables for structured data (methods, parameters, comparisons). Each table cell referencing code should use [[links]].

**Content:** Be comprehensive - cover architecture, core components, data flow, callers/callees analysis. Target 2500+ words for research documentation.

Make your choice:"""

            cleaned_messages.append(HumanMessage(content=force_prompt))

            try:
                # Invoke base LLM WITHOUT tools to force a final decision (outline JSON or final doc).
                # We then strip any accidental tool_calls and let decide_node interpret the result.
                response = await invoke_with_retry(
                    self.llm, cleaned_messages, label=f"force_generate(depth={depth})",
                    config=settings.active_llm_config,
                )
                response_content = getattr(response, "content", None)
                if response_content is None:
                    response_content = str(response)

                # Always return a clean AIMessage without tool_calls, even if the model tried to call tools
                clean_response = AIMessage(content=response_content)

                return {"messages": [clean_response]}
            except Exception as e:
                logger.error(f"Force generate failed at depth {depth} (after retries): {e}")
                fallback_content = f"<Doc>True</Doc>\n\n*Documentation for {title} could not be fully generated due to resource constraints. Please use the chat feature to explore this section in detail.*"
                return {"messages": [AIMessage(content=fallback_content)]}

        def should_continue_after_decide(state: DocAgentState) -> str:
            """Route after decide node - either delegate or finalize."""
            if state.get("should_delegate") and state.get("outline"):
                return "dispatch"
            return "finalize"

        def should_extract_after_tools(state: DocAgentState) -> str:
            """
            Route after tools node - either extract context or continue to agent.

            If need_extraction is True, route to extract_context node.
            Otherwise, go back to agent node to continue exploration.
            """
            if state.get("need_extraction", False):
                return "extract_context"
            return "agent"

        # =====================================================================
        # BUILD GRAPH
        # =====================================================================

        graph.add_node(
            "extract_inherited_context", extract_inherited_context_node
        )  # Scope-specific extraction
        graph.add_node("agent", agent_node)
        graph.add_node("tools", tools_node)
        graph.add_node(
            "extract_context", extract_context_node
        )  # Tool output extraction node
        graph.add_node("decide", decide_node)
        graph.add_node("finalize", finalize_node)
        graph.add_node(
            "force_generate", force_generate_node
        )  # Force doc generation node

        # If not at max depth, add delegation nodes
        if current_depth < max_depth:
            # Build child workflow recursively
            child_workflow = self._build_doc_agent_workflow(
                all_tools=all_tools,
                current_depth=current_depth + 1,
                max_depth=max_depth,
            )

            graph.add_node("dispatch", dispatch_node)
            graph.add_node("child_agent", child_workflow)
            graph.add_node("aggregate", aggregate_node)

        # =====================================================================
        # EDGES
        # =====================================================================

        def should_extract_inherited(state: DocAgentState) -> str:
            """
            Route from START: check if we need to extract inherited context first.

            Child agents with inherited_raw_messages need to extract scope-specific
            context before starting their exploration.
            """
            inherited_messages = state.get("inherited_raw_messages", [])
            already_extracted = state.get("inherited_context_extracted", False)

            if inherited_messages and not already_extracted:
                return "extract_inherited_context"
            return "agent"

        # START: either extract inherited context or go directly to agent
        graph.add_conditional_edges(
            START,
            should_extract_inherited,
            {
                "extract_inherited_context": "extract_inherited_context",
                "agent": "agent",
            },
        )

        # After extracting inherited context, proceed to agent
        graph.add_edge("extract_inherited_context", "agent")

        graph.add_conditional_edges(
            "agent",
            should_continue_agent,
            {
                "tools": "tools",
                "decide": "decide",
                "agent": "agent",
                "force_generate": "force_generate",
            },
        )

        # After force_generate, always go to decide to finalize
        graph.add_edge("force_generate", "decide")

        # After tools: check if extraction is needed, otherwise back to agent
        graph.add_conditional_edges(
            "tools",
            should_extract_after_tools,
            {
                "extract_context": "extract_context",
                "agent": "agent",
            },
        )

        # After extraction: always go back to agent to continue
        graph.add_edge("extract_context", "agent")

        if current_depth < max_depth:
            graph.add_conditional_edges(
                "decide",
                should_continue_after_decide,
                {
                    "dispatch": "dispatch",
                    "finalize": "finalize",
                },
            )

            def dispatch_routing(state: DocAgentState) -> list[Send] | str:
                """
                Routing function that returns Send list for parallel child agents.
                This is called after dispatch_node updates the state.

                NEW APPROACH - Scope-specific context extraction:
                - Pass raw parent messages to child agents via inherited_raw_messages
                - Child agents will extract relevant context for their specific scope
                - This ensures each child gets context tailored to its documentation task
                """
                outline = state.get("outline", [])
                depth = state["current_depth"]
                child_depth = depth + 1

                if not outline:
                    logger.warning(
                        f"Dispatch routing called but no outline at depth {depth}"
                    )
                    return "finalize"

                logger.info(f"Depth {depth}: Dispatching {len(outline)} child agents")

                # Determine if child agents should inherit parent's messages
                should_inherit = child_depth >= self.inherit_from_depth

                # Prepare raw parent messages for inheritance (cleaned but not compressed)
                inherited_raw_messages = []
                if should_inherit:
                    # Clean messages: remove unanswered tool_calls and SystemMessages
                    # But do NOT compress - let each child extract what it needs
                    inherited_raw_messages = self._clean_messages_for_inheritance(
                        state.get("messages", [])
                    )
                    logger.info(
                        f"Child agents at depth {child_depth} will receive {len(inherited_raw_messages)} "
                        f"raw messages for scope-specific extraction"
                    )

                send_tasks = []
                for item in outline:
                    # Child starts with a simple task message
                    # The extract_inherited_context node will add relevant context
                    child_messages = [
                        HumanMessage(
                            content=f"Generate documentation for: {item['title']}\n\nDescription: {item['description']}"
                        )
                    ]

                    child_state = DocAgentState(
                        # Depth control
                        current_depth=child_depth,
                        max_depth=state["max_depth"],
                        # Scope
                        scope_title=item["title"],
                        scope_description=item["description"],
                        scope_key_components=item.get("key_components", []),
                        scope_suggested_children=item.get("suggested_children", []),
                        scope_order=item["order"],
                        # Context
                        repo_name=state["repo_name"],
                        language=state["language"],
                        wiki_doc_path=state["wiki_doc_path"],
                        parent_analysis=state.get("parent_analysis", ""),
                        # Shared context (available to all agents)
                        readme_content=state.get("readme_content", ""),
                        full_outline=outline,  # Pass the full outline so child knows the big picture
                        has_inherited_context=should_inherit,
                        focus_areas=state.get(
                            "focus_areas", ""
                        ),  # Pass focus areas to children
                        # NEW: Raw inherited messages for scope-specific extraction
                        inherited_raw_messages=inherited_raw_messages
                        if should_inherit
                        else [],
                        inherited_context_extracted=False,  # Will be set to True after extraction
                        # Working state
                        messages=child_messages,
                        explored_nodes=[],
                        # Decision (will be determined by child)
                        should_delegate=False,
                        outline=[],
                        generated_content="",
                        child_results=[],
                        # Control
                        tool_call_count=0,
                        max_tool_calls=self._get_tool_budget(child_depth),
                        current_step="init",
                        progress=0,
                        # Context extraction control (fresh for each child)
                        need_extraction=False,
                        extraction_round=0,
                        extraction_summaries=[],
                    )

                    send_tasks.append(Send("child_agent", child_state))

                return send_tasks

            # Wrapper to extract only the hierarchical result from child_agent output
            def child_agent_wrapper(state: DocAgentState) -> dict:
                """
                Extract the HIERARCHICAL result from child_agent output.

                The child workflow's child_results may contain:
                - If delegated: Results from grandchildren (depth=child_depth+1) AND an aggregate result
                  at child_depth that CONTAINS these grandchildren in its child_results field
                - If generated: A single finalize_result(file_path=...) at child_depth

                Key insight: When an agent delegates, aggregate_node creates a result at
                the delegating agent's depth that WRAPS all the grandchild results.
                We need to return this aggregate result (which preserves the hierarchy)
                rather than filtering by depth (which loses nested children).

                IMPORTANT: We use scope_title to match results, NOT current_depth, because
                current_depth uses max_value reducer and may be corrupted when child agents
                further delegate (e.g., depth=1 agent delegates to depth=2, causing
                current_depth to become 2 due to max_value reducer).

                Strategy:
                1. Find result matching scope_title (most reliable)
                2. If found with child_results, it's an aggregate (delegated)
                3. If found with file_path, it's a finalize (generated directly)
                4. Fallback: return all results
                """
                child_results = state.get("child_results", [])
                scope_title = state.get("scope_title", "?")
                # Note: current_depth may be corrupted by max_value reducer, log it but don't rely on it
                state_depth = state.get("current_depth", 0)

                if not child_results:
                    # Enhanced diagnostics: log why child produced nothing
                    generated_content = state.get("generated_content", "")
                    msgs = state.get("messages", [])
                    last_msg = msgs[-1] if msgs else None
                    tool_calls = state.get("tool_call_count", 0)
                    logger.error(
                        f"child_agent_wrapper ('{scope_title}'): child produced NO results. "
                        f"messages={len(msgs)}, last_msg_type={type(last_msg).__name__ if last_msg else 'None'}, "
                        f"tool_calls={tool_calls}, generated_content_len={len(generated_content)}, "
                        f"current_step={state.get('current_step', '?')}"
                    )
                    return {"child_results": []}

                # Log what we received for debugging - with more detail
                logger.info(
                    f"=== child_agent_wrapper ('{scope_title}', state_depth={state_depth}): received {len(child_results)} results ==="
                )
                for i, r in enumerate(child_results):
                    if isinstance(r, dict):
                        r_title = r.get("title", "?")
                        r_depth = r.get("depth", -1)
                        r_children = r.get("child_results", [])
                        r_file_path = r.get("file_path")
                        logger.info(
                            f"  [{i}] title='{r_title}', depth={r_depth}, file={bool(r_file_path)}, children={len(r_children)}"
                        )

                # Find result matching this agent's scope_title
                # This is more reliable than depth because depth uses max_value reducer
                matching_result = None
                for r in child_results:
                    if isinstance(r, dict):
                        r_title = r.get("title", "")
                        if r_title == scope_title:
                            matching_result = r
                            r_children = r.get("child_results", [])
                            r_file_path = r.get("file_path")
                            if r_children:
                                logger.info(
                                    f"child_agent_wrapper: found matching AGGREGATE result for '{scope_title}' with {len(r_children)} children"
                                )
                            elif r_file_path:
                                logger.info(
                                    f"child_agent_wrapper: found matching FINALIZE result for '{scope_title}' with file {r_file_path}"
                                )
                            break

                # Return the matching result if found
                if matching_result:
                    r_children = matching_result.get("child_results", [])
                    r_file_path = matching_result.get("file_path")
                    if r_children:
                        logger.info(
                            f"child_agent_wrapper ('{scope_title}'): returning AGGREGATE result with {len(r_children)} nested children"
                        )
                    else:
                        logger.info(
                            f"child_agent_wrapper ('{scope_title}'): returning FINALIZE result (file: {r_file_path})"
                        )
                    return {"child_results": [matching_result]}
                else:
                    # Fallback: No matching result found, return all and let parent handle it
                    logger.warning(
                        f"child_agent_wrapper ('{scope_title}'): no matching result found, returning all {len(child_results)} results"
                    )
                    return {"child_results": child_results}

            # Use add_conditional_edges with Send - the routing function returns Send list directly
            # When Send list is returned, LangGraph dispatches to child_agent nodes in parallel
            graph.add_conditional_edges(
                "dispatch", dispatch_routing, ["child_agent", "finalize"]
            )
            graph.add_node("child_agent_wrapper", child_agent_wrapper)
            graph.add_edge("child_agent", "child_agent_wrapper")
            graph.add_edge("child_agent_wrapper", "aggregate")
            graph.add_edge("aggregate", END)
        else:
            graph.add_edge("decide", "finalize")

        graph.add_edge("finalize", END)

        return graph.compile()

    def _build_workflow(self, ingestor: MemgraphIngestor, repo_name: str):
        """Build the complete workflow starting at depth 0."""
        _, _, all_tools = self._get_or_create_tools(repo_name, ingestor)

        # Build recursive workflow starting at depth 0
        return self._build_doc_agent_workflow(
            all_tools=all_tools,
            current_depth=0,
            max_depth=self.doc_depth,
        )

    def _create_root_initial_state(
        self,
        *,
        repo_name: str,
        wiki_doc_path: Path,
        language: str,
        focus_areas: str | None,
        readme_content: str,
    ) -> DocAgentState:
        initial_message = f"""Analyze the '{repo_name}' repository and create comprehensive documentation.

1. Use tools to explore the repository structure
2. Identify main packages, classes, and architectural patterns
3. Decide whether to:
   - DELEGATE: Create an outline with {{3}}-{{6}} sections for sub-agents to handle
   - GENERATE: Write the documentation directly (if scope is focused)

If delegating, return a JSON array:
[
  {{"title": "Section Title", "description": "What it covers", "key_components": ["pkg.Class"], "subsections": ["Sub1", "Sub2"]}}
]

If generating, start with <Doc>True</Doc> followed by markdown content.
"""

        return DocAgentState(
            current_depth=0,
            max_depth=self.doc_depth,
            scope_title=repo_name,
            scope_description=f"Complete documentation for the {repo_name} repository",
            scope_key_components=[],
            scope_suggested_children=[],
            scope_order=0,
            repo_name=repo_name,
            language=language,
            wiki_doc_path=str(wiki_doc_path),
            parent_analysis="",
            readme_content=readme_content or "",
            full_outline=[],
            has_inherited_context=False,
            focus_areas=focus_areas or "",
            inherited_raw_messages=[],
            inherited_context_extracted=True,
            messages=[HumanMessage(content=initial_message)],
            explored_nodes=[],
            should_delegate=False,
            outline=[],
            generated_content="",
            child_results=[],
            tool_call_count=0,
            max_tool_calls=self._get_tool_budget(0),
            current_step="init",
            progress=0,
            need_extraction=False,
            extraction_round=0,
            extraction_summaries=[],
        )

    @staticmethod
    def _extract_hierarchical_results(results: list[dict]) -> list[dict]:
        """Return the top-level hierarchical results for documentation writing."""
        if not results:
            return []

        results_by_depth: dict[int, list[dict]] = {}
        for result in results:
            if not isinstance(result, dict):
                continue
            depth = int(result.get("depth", 0))
            results_by_depth.setdefault(depth, []).append(result)

        if not results_by_depth:
            return []

        min_depth = min(results_by_depth)
        if min_depth == 0:
            for result in results_by_depth[0]:
                child_results = result.get("child_results", [])
                if child_results:
                    return child_results
            return results_by_depth[0]

        return results_by_depth[min_depth]

    def _build_child_state_from_parent(
        self,
        *,
        parent_state: dict[str, Any],
        outline_item: dict[str, Any],
    ) -> DocAgentState:
        depth = int(parent_state.get("current_depth", 0))
        child_depth = depth + 1
        outline = parent_state.get("outline", [])
        should_inherit = child_depth >= self.inherit_from_depth
        inherited_raw_messages: list[BaseMessage] = []

        if should_inherit:
            inherited_raw_messages = self._clean_messages_for_inheritance(
                parent_state.get("messages", [])
            )

        return DocAgentState(
            current_depth=child_depth,
            max_depth=parent_state["max_depth"],
            scope_title=outline_item["title"],
            scope_description=outline_item["description"],
            scope_key_components=outline_item.get("key_components", []),
            scope_suggested_children=outline_item.get("suggested_children", []),
            scope_order=outline_item["order"],
            repo_name=parent_state["repo_name"],
            language=parent_state["language"],
            wiki_doc_path=parent_state["wiki_doc_path"],
            parent_analysis=parent_state.get("parent_analysis", ""),
            readme_content=parent_state.get("readme_content", ""),
            full_outline=outline,
            has_inherited_context=should_inherit,
            focus_areas=parent_state.get("focus_areas", ""),
            inherited_raw_messages=inherited_raw_messages if should_inherit else [],
            inherited_context_extracted=False,
            messages=[
                HumanMessage(
                    content=(
                        f"Generate documentation for: {outline_item['title']}\n\n"
                        f"Description: {outline_item['description']}"
                    )
                )
            ],
            explored_nodes=[],
            should_delegate=False,
            outline=[],
            generated_content="",
            child_results=[],
            tool_call_count=0,
            max_tool_calls=self._get_tool_budget(child_depth),
            current_step="init",
            progress=0,
            need_extraction=False,
            extraction_round=0,
            extraction_summaries=[],
        )

    @staticmethod
    def _extract_child_result(
        child_state: dict[str, Any],
        scope_title: str,
    ) -> dict[str, Any] | None:
        """Extract the hierarchical result produced for one child section."""
        child_results = child_state.get("child_results", [])
        if not child_results:
            return None

        for result in child_results:
            if isinstance(result, dict) and result.get("title") == scope_title:
                return result

        for result in child_results:
            if isinstance(result, dict):
                return result
        return None

    @staticmethod
    def _merge_results_by_title(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Deduplicate top-level results while preserving section order."""
        merged: dict[str, dict[str, Any]] = {}
        for result in results:
            if not isinstance(result, dict):
                continue
            title = result.get("title")
            if not title:
                continue
            existing = merged.get(title)
            if existing is None:
                merged[title] = result
                continue

            prefer_result = bool(result.get("child_results")) or bool(
                result.get("file_path")
            )
            prefer_existing = bool(existing.get("child_results")) or bool(
                existing.get("file_path")
            )
            if prefer_result and not prefer_existing:
                merged[title] = result
            elif prefer_result == prefer_existing:
                merged[title] = result

        return sorted(merged.values(), key=lambda item: item.get("order", 0))

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    async def stream_generate(
        self,
        repo_name: str,
        ingestor: MemgraphIngestor,
        wiki_doc_path: Path,
        language: str = "en",
        focus_areas: str | None = None,
        checkpoint_callback: Callable[[dict[str, Any]], Awaitable[None] | None]
        | None = None,
        preserve_existing_sections: bool = False,
        initial_state_override: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Stream hierarchical documentation generation.

        Args:
            repo_name: Name of the repository
            ingestor: MemgraphIngestor instance
            wiki_doc_path: Path to write documentation
            language: Language for documentation (default: "en")
            focus_areas: Optional user-specified focus areas for emphasis
            checkpoint_callback: Optional callback invoked with the latest full state
            preserve_existing_sections: Keep existing partial sections on disk
            initial_state_override: Optional saved root state to resume from

        Yields:
            Status events during generation
        """
        try:
            def _extract_titles(items: list[Any], limit: int = 6) -> list[str]:
                titles = []
                for item in items:
                    if isinstance(item, dict):
                        title = item.get("title", "")
                    else:
                        title = getattr(item, "title", "")
                    if title:
                        titles.append(title)
                    if len(titles) >= limit:
                        break
                return titles

            yield {
                "type": "status",
                "content": (
                    "Resuming recursive documentation from checkpoint..."
                    if initial_state_override
                    else f"Initializing recursive documentation (depth={self.doc_depth})..."
                ),
                "progress": 0,
                "step": "resume_init" if initial_state_override else "init",
                "details": {
                    "phase": "resume_init" if initial_state_override else "init",
                    "doc_depth": self.doc_depth,
                    "repo_name": repo_name,
                    "focus_provided": bool(focus_areas),
                    "resume_mode": bool(initial_state_override),
                },
            }

            import shutil

            sections_dir = wiki_doc_path / repo_name / "sections"
            if sections_dir.exists() and not preserve_existing_sections:
                try:
                    shutil.rmtree(sections_dir)
                    logger.info(f"Cleaned up old sections directory: {sections_dir}")
                except Exception as e:
                    logger.warning(f"Failed to clean up sections directory: {e}")

            workflow = self._build_workflow(ingestor, repo_name)

            # Get README content from project path (database or wiki_repos fallback)
            # README will be added to system prompt in agent_node (only for root agent)
            readme_content = self._get_readme_content(repo_name, ingestor=ingestor)
            if readme_content:
                logger.info(
                    f"README loaded for root agent ({len(readme_content)} chars)"
                )

            initial_state = (
                dict(initial_state_override)
                if initial_state_override
                else self._create_root_initial_state(
                    repo_name=repo_name,
                    wiki_doc_path=wiki_doc_path,
                    language=language,
                    focus_areas=focus_areas,
                    readme_content=readme_content or "",
                )
            )

            initial_state["repo_name"] = repo_name
            initial_state["language"] = language
            initial_state["wiki_doc_path"] = str(wiki_doc_path)
            initial_state["readme_content"] = (
                initial_state.get("readme_content") or readme_content or ""
            )
            initial_state["focus_areas"] = focus_areas or initial_state.get(
                "focus_areas", ""
            )
            initial_state["need_extraction"] = False
            initial_state["inherited_context_extracted"] = initial_state.get(
                "inherited_context_extracted", True
            )

            yield {
                "type": "status",
                "content": (
                    "Root agent resuming repository exploration..."
                    if initial_state_override
                    else "Root agent exploring repository..."
                ),
                "progress": 5,
                "step": "resuming" if initial_state_override else "exploring",
                "details": {
                    "phase": "resuming" if initial_state_override else "exploring",
                    "tool_call_count": initial_state.get("tool_call_count", 0),
                    "explored_node_count": len(initial_state.get("explored_nodes", [])),
                    "resume_mode": bool(initial_state_override),
                },
            }

            if checkpoint_callback is not None:
                await _maybe_await(checkpoint_callback(dict(initial_state)))

            final_state = None
            previous_step = ""
            last_tool_report = 0
            last_outline_count = 0
            last_child_results_count = 0
            seen_tool_call_keys: set[str] = set()
            # Track child agent progress: {scope_title: {tool_count, explored_count, step}}
            child_agent_progress: dict[str, dict[str, Any]] = {}
            child_seen_tool_call_keys: dict[str, set[str]] = {}
            # Use stream_mode="values" with subgraphs=True to see child agent state changes
            async for namespace, state in workflow.astream(
                initial_state,
                {"recursion_limit": settings.LANGGRAPH_RECURSION_LIMIT},
                stream_mode="values",
                subgraphs=True,
            ):
                # namespace is a tuple: () for root, ("child_agent:...",) for child agents
                is_child = len(namespace) > 0

                if not is_child:
                    # === ROOT AGENT STATE ===
                    if checkpoint_callback is not None:
                        await _maybe_await(checkpoint_callback(dict(state)))
                    current_step = state.get("current_step", "")
                    outline = state.get("outline", [])
                    child_results = state.get("child_results", [])
                    tool_count = state.get("tool_call_count", 0)
                    explored_count = len(state.get("explored_nodes", []))
                    outline_count = len(outline)
                    child_results_count = len(child_results)
                    messages = state.get("messages", [])
                    new_tool_calls = _collect_new_tool_calls(messages, seen_tool_call_keys)
                    common_details = {
                        "phase": current_step or "running",
                        "tool_call_count": tool_count,
                        "explored_node_count": explored_count,
                        "outline_count": outline_count,
                        "outline_titles": _extract_titles(outline),
                        "completed_section_count": child_results_count,
                        "completed_section_titles": _extract_titles(child_results),
                    }

                    if current_step != previous_step and current_step == "init":
                        yield {
                            "type": "status",
                            "content": "Agent exploring knowledge graph...",
                            "progress": 15,
                            "step": "agent_exploring",
                            "details": common_details,
                        }
                    elif current_step != previous_step and current_step == "dispatching":
                        yield {
                            "type": "status",
                            "content": "Child agents generating sections...",
                            "progress": 65,
                            "step": "children_working",
                            "details": common_details,
                        }

                    if new_tool_calls:
                        latest_displays = [tc["display"] for tc in new_tool_calls[:3]]
                        latest_summary = "; ".join(latest_displays)
                        if len(new_tool_calls) == 1:
                            content = f"Tool call: {latest_summary}"
                        else:
                            content = (
                                f"Tool calls ({len(new_tool_calls)}): {latest_summary}"
                            )
                        yield {
                            "type": "status",
                            "content": content,
                            "progress": min(55, max(18, 15 + tool_count)),
                            "step": "tool_call",
                            "details": {
                                **common_details,
                                "recent_tool_calls": [
                                    {
                                        "name": tc["name"],
                                        "display": tc["display"],
                                        "args_summary": tc["args_summary"],
                                        "result_preview": tc["result_preview"],
                                    }
                                    for tc in new_tool_calls[:5]
                                ],
                            },
                        }

                    if tool_count >= last_tool_report + 5:
                        yield {
                            "type": "status",
                            "content": (
                                f"Exploring knowledge graph... "
                                f"{tool_count} tool calls, {explored_count} nodes collected"
                            ),
                            "progress": min(45, 15 + tool_count),
                            "step": "agent_exploring",
                            "details": {
                                **common_details,
                                "recent_tool_calls": [
                                    {
                                        "name": tc["name"],
                                        "display": tc["display"],
                                        "args_summary": tc["args_summary"],
                                        "result_preview": tc["result_preview"],
                                    }
                                    for tc in new_tool_calls[-3:]
                                ],
                            },
                        }
                        last_tool_report = tool_count

                    if state.get("should_delegate") and outline_count and (
                        outline_count != last_outline_count
                    ):
                        yield {
                            "type": "status",
                            "content": f"Planned {outline_count} documentation sections for sub-agents...",
                            "progress": 60,
                            "step": "delegating",
                            "details": common_details,
                        }
                        last_outline_count = outline_count

                    if child_results_count and (
                        child_results_count != last_child_results_count
                    ):
                        total_sections = max(outline_count, last_outline_count, child_results_count)
                        aggregate_progress = min(
                            90,
                            70 + int(20 * (child_results_count / max(total_sections, 1))),
                        )
                        yield {
                            "type": "status",
                            "content": (
                                f"Aggregating documentation sections "
                                f"({child_results_count}/{total_sections})..."
                            ),
                            "progress": aggregate_progress,
                            "step": "aggregating",
                            "details": common_details,
                        }
                        last_child_results_count = child_results_count

                    final_state = state
                    previous_step = current_step

                    logger.debug(
                        f"Stream state: step={current_step}, delegate={state.get('should_delegate')}, "
                        f"outline_len={outline_count}, child_results_len={child_results_count}, "
                        f"tool_call_count={tool_count}, explored_nodes={explored_count}"
                    )
                else:
                    # === CHILD AGENT STATE ===
                    child_scope = state.get("scope_title", "")
                    if not child_scope:
                        continue
                    child_tool_count = state.get("tool_call_count", 0)
                    child_explored = len(state.get("explored_nodes", []))
                    child_step = state.get("current_step", "")
                    child_messages = state.get("messages", [])

                    # Track per-child seen tool calls
                    if child_scope not in child_seen_tool_call_keys:
                        child_seen_tool_call_keys[child_scope] = set()
                    child_new_tool_calls = _collect_new_tool_calls(
                        child_messages, child_seen_tool_call_keys[child_scope]
                    )

                    prev = child_agent_progress.get(child_scope, {})
                    prev_tool_count = prev.get("tool_count", 0)
                    prev_step = prev.get("step", "")

                    # Update tracking
                    child_agent_progress[child_scope] = {
                        "tool_count": child_tool_count,
                        "explored_count": child_explored,
                        "step": child_step,
                    }

                    # Emit child tool calls
                    if child_new_tool_calls:
                        latest_displays = [tc["display"] for tc in child_new_tool_calls[:3]]
                        latest_summary = "; ".join(latest_displays)
                        total_sections = max(last_outline_count, 1)
                        completed = last_child_results_count
                        child_base_progress = 65 + int(
                            20 * (completed / total_sections)
                        )
                        yield {
                            "type": "status",
                            "content": f"[{child_scope}] Tool calls ({len(child_new_tool_calls)}): {latest_summary}",
                            "progress": min(89, child_base_progress),
                            "step": "child_tool_call",
                            "details": {
                                "phase": "child_working",
                                "child_scope": child_scope,
                                "child_tool_count": child_tool_count,
                                "child_explored_count": child_explored,
                                "child_step": child_step,
                                "child_agent_progress": {
                                    k: v for k, v in child_agent_progress.items()
                                },
                                "recent_tool_calls": [
                                    {
                                        "name": tc["name"],
                                        "display": tc["display"],
                                        "args_summary": tc["args_summary"],
                                        "result_preview": tc["result_preview"],
                                    }
                                    for tc in child_new_tool_calls[:5]
                                ],
                            },
                        }
                    # Emit child step transitions (e.g. exploring -> deciding -> generating)
                    elif child_step and child_step != prev_step:
                        yield {
                            "type": "status",
                            "content": f"[{child_scope}] {child_step}",
                            "progress": min(89, 65 + int(20 * (last_child_results_count / max(last_outline_count, 1)))),
                            "step": "child_step_change",
                            "details": {
                                "phase": "child_working",
                                "child_scope": child_scope,
                                "child_step": child_step,
                                "child_tool_count": child_tool_count,
                                "child_agent_progress": {
                                    k: v for k, v in child_agent_progress.items()
                                },
                            },
                        }

            # Write documentation
            if final_state:
                logger.info(
                    f"Final state: child_results={len(final_state.get('child_results', []))}, "
                    f"generated_content_len={len(final_state.get('generated_content', ''))}"
                )

                index_data = await self._write_documentation(
                    final_state,
                    repo_name,
                    wiki_doc_path,
                )

                yield {
                    "type": "complete",
                    "content": index_data,
                    "progress": 100,
                    "step": "complete",
                    "details": {
                        "phase": "complete",
                        "sections_generated": index_data.get("statistics", {}).get(
                            "sections_generated"
                        ),
                        "files_generated": index_data.get("statistics", {}).get(
                            "total_files"
                        ),
                        "max_depth_reached": index_data.get("statistics", {}).get(
                            "max_depth_reached"
                        ),
                    },
                }
            else:
                yield {
                    "type": "error",
                    "content": "No final state produced",
                }

        except TimeoutError:
            raise
        except Exception as e:
            logger.exception(f"Documentation generation failed: {e}")
            # Try to salvage partial results if we have any
            if final_state and (
                final_state.get("child_results")
                or final_state.get("generated_content")
            ):
                logger.info(
                    "Attempting to save partial results from crashed generation"
                )
                try:
                    index_data = await self._write_documentation(
                        final_state,
                        repo_name,
                        wiki_doc_path,
                    )
                    yield {
                        "type": "partial",
                        "content": index_data,
                        "progress": 90,
                        "step": "partial_save",
                        "details": {
                            "phase": "partial_save",
                            "error": str(e),
                        },
                    }
                except Exception as write_err:
                    logger.error(f"Failed to save partial results: {write_err}")
            yield {"type": "error", "content": str(e)}

    async def stream_resume(
        self,
        repo_name: str,
        ingestor: MemgraphIngestor,
        wiki_doc_path: Path,
        checkpoint_state: dict[str, Any],
        language: str = "en",
        focus_areas: str | None = None,
        checkpoint_callback: Callable[[dict[str, Any]], Awaitable[None] | None]
        | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Resume documentation generation from a locally persisted checkpoint."""
        try:
            if not checkpoint_state:
                raise ValueError("Checkpoint state is empty")

            outline = checkpoint_state.get("outline", [])
            should_delegate = bool(checkpoint_state.get("should_delegate"))

            if not outline or not should_delegate:
                async for event in self.stream_generate(
                    repo_name=repo_name,
                    ingestor=ingestor,
                    wiki_doc_path=wiki_doc_path,
                    language=language,
                    focus_areas=focus_areas,
                    checkpoint_callback=checkpoint_callback,
                    preserve_existing_sections=True,
                    initial_state_override=checkpoint_state,
                ):
                    yield event
                return

            readme_content = checkpoint_state.get("readme_content") or self._get_readme_content(
                repo_name, ingestor=ingestor
            )
            root_state = dict(checkpoint_state)
            root_state["repo_name"] = repo_name
            root_state["language"] = language
            root_state["wiki_doc_path"] = str(wiki_doc_path)
            root_state["readme_content"] = readme_content or ""
            root_state["focus_areas"] = focus_areas or root_state.get("focus_areas", "")
            root_state["current_depth"] = 0
            root_state["max_depth"] = self.doc_depth
            root_state["outline"] = outline
            root_state["should_delegate"] = True
            root_state["child_results"] = self._merge_results_by_title(
                self._extract_hierarchical_results(root_state.get("child_results", []))
            )

            if checkpoint_callback is not None:
                await _maybe_await(checkpoint_callback(dict(root_state)))

            completed_titles = {
                result.get("title")
                for result in root_state.get("child_results", [])
                if isinstance(result, dict) and result.get("title")
            }
            missing_items = [
                item
                for item in sorted(outline, key=lambda entry: entry.get("order", 0))
                if isinstance(item, dict) and item.get("title") not in completed_titles
            ]
            total_sections = max(len(outline), len(root_state.get("child_results", [])))

            yield {
                "type": "status",
                "content": (
                    f"Resuming documentation from checkpoint "
                    f"({len(root_state.get('child_results', []))}/{total_sections} sections already completed)..."
                ),
                "progress": min(
                    88,
                    60
                    + int(
                        25
                        * (
                            len(root_state.get("child_results", []))
                            / max(total_sections, 1)
                        )
                    ),
                ),
                "step": "resuming_sections",
                "details": {
                    "phase": "resuming_sections",
                    "outline_count": len(outline),
                    "completed_section_count": len(root_state.get("child_results", [])),
                    "completed_section_titles": [
                        result.get("title", "")
                        for result in root_state.get("child_results", [])
                        if isinstance(result, dict)
                    ],
                },
            }

            if missing_items:
                _, _, all_tools = self._get_or_create_tools(repo_name, ingestor)
                child_workflow = self._build_doc_agent_workflow(
                    all_tools=all_tools,
                    current_depth=1,
                    max_depth=self.doc_depth,
                )

                for item in missing_items:
                    yield {
                        "type": "status",
                        "content": f"Resuming section: {item['title']}",
                        "progress": min(
                            92,
                            68
                            + int(
                                20
                                * (
                                    len(root_state.get("child_results", []))
                                    / max(total_sections, 1)
                                )
                            ),
                        ),
                        "step": "resuming_section",
                        "details": {
                            "phase": "resuming_section",
                            "outline_count": len(outline),
                            "completed_section_count": len(
                                root_state.get("child_results", [])
                            ),
                            "current_section_title": item["title"],
                        },
                    }

                    child_state = self._build_child_state_from_parent(
                        parent_state=root_state,
                        outline_item=item,
                    )
                    child_final_state = None

                    async for state in child_workflow.astream(
                        child_state,
                        {"recursion_limit": settings.LANGGRAPH_RECURSION_LIMIT},
                        stream_mode="values",
                    ):
                        child_final_state = state

                    if not child_final_state:
                        raise RuntimeError(
                            f"Resume failed before producing state for section {item['title']}"
                        )

                    child_result = self._extract_child_result(
                        child_final_state, item["title"]
                    )
                    if not child_result:
                        raise RuntimeError(
                            f"Resume failed to recover section result for {item['title']}"
                        )

                    root_state["child_results"] = self._merge_results_by_title(
                        root_state.get("child_results", []) + [child_result]
                    )
                    if checkpoint_callback is not None:
                        await _maybe_await(checkpoint_callback(dict(root_state)))

                    yield {
                        "type": "status",
                        "content": (
                            f"Recovered section {item['title']} "
                            f"({len(root_state.get('child_results', []))}/{total_sections})"
                        ),
                        "progress": min(
                            94,
                            70
                            + int(
                                22
                                * (
                                    len(root_state.get("child_results", []))
                                    / max(total_sections, 1)
                                )
                            ),
                        ),
                        "step": "resumed_section_complete",
                        "details": {
                            "phase": "resumed_section_complete",
                            "outline_count": len(outline),
                            "completed_section_count": len(
                                root_state.get("child_results", [])
                            ),
                            "current_section_title": item["title"],
                        },
                    }

            index_data = await self._write_documentation(
                root_state,
                repo_name,
                wiki_doc_path,
            )

            yield {
                "type": "complete",
                "content": index_data,
                "progress": 100,
                "step": "complete",
                "details": {
                    "phase": "complete",
                    "sections_generated": index_data.get("statistics", {}).get(
                        "sections_generated"
                    ),
                    "files_generated": index_data.get("statistics", {}).get(
                        "total_files"
                    ),
                    "max_depth_reached": index_data.get("statistics", {}).get(
                        "max_depth_reached"
                    ),
                    "resume_mode": True,
                },
            }
        except TimeoutError:
            raise
        except Exception as e:
            logger.exception(f"Documentation resume failed: {e}")
            yield {"type": "error", "content": str(e)}

    async def run(
        self,
        query: str,
        message_history: list | None = None,
        repo_name: str | None = None,
        ingestor: MemgraphIngestor | None = None,
    ) -> Any:
        """
        Run a single research/documentation query (non-streaming).

        This is the primary API for research mode, providing a simple interface
        for generating documentation about a specific topic.

        Args:
            query: The research query or topic to investigate
            message_history: Optional previous message history
            repo_name: Repository name (uses stored _repo_path if not provided)
            ingestor: MemgraphIngestor instance (uses stored _default_ingestor if not provided)

        Returns:
            A response object with:
                - output: The generated documentation text
                - documentation_data: Structured documentation data
                - is_documentation: Whether this is a documentation response
        """
        # Use stored values if not provided
        if repo_name is None and hasattr(self, "_repo_path"):
            repo_name = Path(self._repo_path).name
        if ingestor is None and hasattr(self, "_default_ingestor"):
            ingestor = self._default_ingestor

        if repo_name is None or ingestor is None:
            raise ValueError(
                "repo_name and ingestor must be provided or set via initialize_doc_agent()"
            )

        try:
            # Build message history
            messages = []
            if message_history:
                for msg in message_history:
                    if hasattr(msg, "role") and hasattr(msg, "content"):
                        role_map = {
                            "user": HumanMessage,
                            "assistant": AIMessage,
                            "system": SystemMessage,
                        }
                        msg_class = role_map.get(msg.role, HumanMessage)
                        messages.append(msg_class(content=msg.content))
                    elif isinstance(msg, BaseMessage):
                        messages.append(msg)
            messages.append(HumanMessage(content=query))

            # Extract scope from user query
            scope_title = query[:50] + "..." if len(query) > 50 else query
            scope_description = (
                f"Research: {query[:200]}..."
                if len(query) > 200
                else f"Research: {query}"
            )

            # Build workflow
            workflow = self._build_workflow(ingestor, repo_name)

            # Initial state
            initial_state = {
                "messages": messages,
                "explored_nodes": [],
                "current_depth": 0,
                "max_depth": self.doc_depth,
                "scope_title": scope_title,
                "scope_description": scope_description,
                "repo_name": repo_name,
                "focus_areas": query,
                "parent_analysis": "",
                "full_outline": [],
                "child_results": [],
                "tool_call_count": 0,
                "max_tool_calls": self._tool_budgets.get(0, 40),
                "current_step": "exploration",
                "progress": 0,
                "need_extraction": False,
                "extraction_round": 0,
                "extraction_summaries": [],
            }

            # Run workflow
            config = {"recursion_limit": settings.LANGGRAPH_RECURSION_LIMIT}

            # Accumulate state updates from streaming
            # stream_mode="updates" returns incremental updates, not full state
            accumulated_state = dict(initial_state)

            async for chunk in workflow.astream(
                initial_state, config=config, stream_mode="updates"
            ):
                # Each chunk is {node_name: state_update}
                for node_name, state_update in chunk.items():
                    if isinstance(state_update, dict):
                        # Merge updates into accumulated state
                        for key, value in state_update.items():
                            if key == "messages" and isinstance(value, list):
                                # Append new messages (don't replace)
                                existing = accumulated_state.get("messages", [])
                                accumulated_state["messages"] = existing + value
                            elif key == "child_results" and isinstance(value, list):
                                # Append child results
                                existing = accumulated_state.get("child_results", [])
                                accumulated_state["child_results"] = existing + value
                            elif key == "explored_nodes" and isinstance(value, list):
                                # Append explored nodes
                                existing = accumulated_state.get("explored_nodes", [])
                                accumulated_state["explored_nodes"] = existing + value
                            else:
                                # Replace other values
                                accumulated_state[key] = value

            final_state = accumulated_state

            if final_state is None:
                final_state = initial_state

            # Extract final response - prioritize generated_content over messages
            # The workflow stores generated documentation in generated_content field,
            # while messages may only contain the user's original query
            output_text = ""
            final_messages = final_state.get("messages", messages)

            # First, check for generated_content (from finalize_node or aggregate_node)
            if final_state.get("generated_content"):
                output_text = final_state["generated_content"]
                logger.info(f"Using generated_content: {len(output_text)} chars")

            # If no generated_content, try to extract from child_results
            elif final_state.get("child_results"):
                child_results = final_state["child_results"]
                # Combine content from child results
                contents = []
                for result in child_results:
                    if isinstance(result, dict):
                        if result.get("content"):
                            contents.append(result["content"])
                        # Also check nested child_results
                        for child in result.get("child_results", []):
                            if isinstance(child, dict) and child.get("content"):
                                contents.append(child["content"])
                if contents:
                    output_text = "\n\n".join(contents)
                    logger.info(
                        f"Using combined child_results: {len(output_text)} chars from {len(contents)} sections"
                    )

            # Fallback to last AI message content
            if not output_text:
                # Find the last AI message (not user message)
                ai_message = None
                for msg in reversed(final_messages):
                    if hasattr(msg, "content") and hasattr(msg, "type"):
                        # AIMessage has type="ai"
                        if getattr(msg, "type", None) == "ai":
                            ai_message = msg
                            break
                    elif msg.__class__.__name__ == "AIMessage":
                        ai_message = msg
                        break

                if ai_message and ai_message.content:
                    output_text = ai_message.content
                    logger.info(
                        f"Using last AI message content: {len(output_text)} chars"
                    )
                elif final_messages:
                    # Ultimate fallback - use last message regardless of type
                    last_message = final_messages[-1]
                    if hasattr(last_message, "content"):
                        output_text = last_message.content
                        logger.warning(
                            f"Using last message (any type): {len(output_text)} chars"
                        )

            # Create response object compatible with existing code
            class DocResponse:
                def __init__(
                    self,
                    output: str,
                    messages: list,
                    documentation_data: dict = None,
                    explored_nodes: list = None,
                ):
                    self.output = output
                    self._messages = messages
                    self.documentation_data = documentation_data
                    self.explored_nodes = explored_nodes or []
                    self.is_documentation = documentation_data is not None

                def new_messages(self) -> list:
                    return self._messages

            # Always create documentation data for research mode
            # Even if the LLM doesn't output the <Doc>True</Doc> marker,
            # we still want to save the generated content as documentation
            documentation_data = self._create_research_documentation_data(
                output_text, final_state, repo_name
            )

            return DocResponse(
                output=output_text,
                messages=final_messages,
                documentation_data=documentation_data,
                explored_nodes=final_state.get("explored_nodes", []),
            )

        except Exception as e:
            logger.error(f"DocOrchestrator run error: {e}")
            raise

    def _create_research_documentation_data(
        self,
        markdown: str,
        final_state: dict,
        repo_name: str,
    ) -> dict:
        """
        Create structured documentation data for research output.

        Args:
            markdown: The generated markdown content
            final_state: The final workflow state
            repo_name: Repository name

        Returns:
            Structured documentation data dict
        """
        import hashlib

        # Remove documentation declaration marker
        clean_markdown = _sanitize_generated_markdown(markdown)

        # Generate document ID
        content_hash = hashlib.md5(clean_markdown.encode()).hexdigest()[:8]
        doc_id = f"research-{content_hash}"

        # Extract title from first heading
        title = "Research Documentation"
        title_match = re.search(r"^#\s+(.+)$", clean_markdown, re.MULTILINE)
        if title_match:
            title = title_match.group(1).strip()

        # Serialize messages for regeneration support
        from langchain_core.messages import messages_to_dict

        messages = final_state.get("messages", [])
        serialized_messages = messages_to_dict(messages) if messages else []

        # Get extraction summaries
        extraction_summaries = final_state.get("extraction_summaries", [])

        # Pre-embed code references from explored nodes
        explored_nodes = final_state.get("explored_nodes", [])
        references = []
        for node in explored_nodes:
            node_path = node.get("path")
            start_line = node.get("start_line")
            end_line = node.get("end_line")
            code = node.get("code")
            if node_path and start_line and end_line and code:
                from core.language_detection import detect_language_from_path

                qn = node.get("qualified_name", "")
                references.append({
                    "name": qn.split(".")[-1] if qn else "",
                    "ref": qn,
                    "qualified_name": qn,
                    "file": node_path,
                    "startLine": start_line,
                    "endLine": end_line,
                    "start_line": start_line,
                    "end_line": end_line,
                    "code": code,
                    "language": detect_language_from_path(node_path),
                    "nodeType": node.get("type", ""),
                })

        # Build documentation data
        documentation_data = {
            "id": doc_id,
            "title": title,
            "markdown": clean_markdown,
            "repo_name": repo_name,
            "type": "research",
            "explored_nodes": explored_nodes,
            "code_blocks": [],
            "references": references,
            "url": f"/docs/{doc_id}",
            "generated_at": datetime.now(UTC).isoformat(),
            # New fields for regeneration support
            "messages": serialized_messages,
            "extraction_summaries": extraction_summaries,
            "original_query": final_state.get("scope_description", ""),
            "metadata": {
                "tool_call_count": sum(
                    1 for msg in messages if isinstance(msg, ToolMessage)
                ),
                "extraction_round": len(extraction_summaries),
                "model": self.model_name,
                "mode": self.mode.value
                if hasattr(self.mode, "value")
                else str(self.mode),
            },
        }

        return documentation_data

    async def _write_documentation(
        self,
        final_state: dict,
        repo_name: str,
        wiki_doc_path: Path,
    ) -> dict:
        """
        Write the generated documentation to versioned files.

        New folder structure:
        wiki_doc/{repo}/
            _meta.json              # Repository metadata with version list
            versions/
                {version_id}/       # e.g., 20251201_143022_overview
                    _index.json     # This version's index
                    overview.md     # This version's overview
                    sections/       # This version's sections
            current -> versions/{latest_version_id}  # Symlink to current version
        """
        raw_child_results = final_state.get("child_results", [])

        # After the child_agent_wrapper fix, raw_child_results should now contain
        # properly hierarchical results from depth=1 agents:
        # - Each result is either:
        #   - An aggregate result (delegated): depth=1, child_results=[depth=2 results with their own children]
        #   - A finalize result (generated): depth=1, file_path=..., child_results=[]
        #
        # For cases where root (depth=0) delegated, we also need to check if there's
        # a root aggregate result wrapping the depth=1 results.

        def log_result_structure(
            results: list, prefix: str = "", max_depth: int = 3
        ) -> None:
            """Recursively log the structure of results for debugging."""
            if max_depth <= 0:
                return
            for i, r in enumerate(results):
                if isinstance(r, dict):
                    title = r.get("title", "?")
                    depth = r.get("depth", -1)
                    has_file = bool(r.get("file_path"))
                    children = r.get("child_results", [])
                    logger.info(
                        f"{prefix}[{i}] title='{title}', depth={depth}, file={has_file}, children={len(children)}"
                    )
                    if children:
                        log_result_structure(children, prefix + "  ", max_depth - 1)

        child_results = self._extract_hierarchical_results(raw_child_results)
        logger.info(
            f"Extracted {len(child_results)} hierarchical results from {len(raw_child_results)} raw results"
        )
        log_result_structure(child_results, "  ")
        generated_content = final_state.get("generated_content", "")

        # Clean up generated_content to remove duplicate headings
        if generated_content:
            # Remove duplicate consecutive headings (same heading appearing twice in a row)
            # This pattern matches a heading followed by blank lines and the same heading again
            def remove_duplicate_headings(content: str) -> str:
                # Pattern: heading line, optional blank lines, same heading line
                pattern = r"^(#{2,5}\s+[^\n]+)\n+\1(?=\n)"
                prev_content = ""
                while prev_content != content:
                    prev_content = content
                    content = re.sub(pattern, r"\1", content, flags=re.MULTILINE)
                return content

            generated_content = remove_duplicate_headings(generated_content)

            # Also remove excessive blank lines (more than 2 consecutive)
            generated_content = re.sub(r"\n{4,}", "\n\n\n", generated_content)

        # Build document
        lines = [f"# {repo_name}"]
        lines.append("")

        # Use generated_content which is already properly aggregated at this level
        if generated_content:
            lines.append(generated_content)
        elif child_results:
            # Fallback: build from child_results if no generated_content
            sorted_results = sorted(child_results, key=lambda r: r.get("order", 0))

            # Table of contents
            if len(sorted_results) > 1:
                lines.append("## Table of Contents")
                lines.append("")
                for result in sorted_results:
                    anchor = (
                        result["title"].lower().replace(" ", "-").replace("&", "and")
                    )
                    anchor = re.sub(r"[^a-z0-9-]", "", anchor)
                    lines.append(f"- [{result['title']}](#{anchor})")
                lines.append("")

            # Add sections - only if they have content
            for result in sorted_results:
                content = result.get("content", "")
                # Only add section heading if there's actual content
                if content:
                    lines.append(f"## {result['title']}")
                    lines.append("")
                    lines.append(content)
                    lines.append("")

        # Footer
        lines.append("---")
        lines.append(
            f"*Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}*"
        )
        lines.append(f"*Documentation depth: {self.doc_depth}*")

        overview_content = "\n".join(lines)

        # Build index
        explored_count = sum(r.get("explored_nodes", 0) for r in child_results)

        def build_nav_tree(results: list, depth: int = 0) -> list:
            """Recursively build navigation tree."""
            nav_items = []
            # Deduplicate results by title to avoid showing the same section twice
            seen_titles = set()
            seen_anchors = set()

            for result in sorted(results, key=lambda r: r.get("order", 0)):
                title = result.get("title", "")

                # Skip empty titles
                if not title:
                    continue

                # Skip duplicate titles at the same level
                if title in seen_titles:
                    continue
                seen_titles.add(title)

                # Generate anchor - handle non-ASCII characters
                anchor = title.lower()
                # Replace Chinese and other non-ASCII characters with pinyin or transliteration if possible
                # For now, use a hash-based approach for non-ASCII
                import hashlib

                ascii_anchor = anchor.replace(" ", "-").replace("&", "and")
                ascii_anchor = re.sub(r"[^a-z0-9-]", "", ascii_anchor)

                # If anchor is empty (e.g., Chinese-only title), use a hash-based anchor
                if not ascii_anchor:
                    # Create a stable hash-based anchor from the title
                    hash_val = hashlib.md5(title.encode("utf-8")).hexdigest()[:8]
                    ascii_anchor = f"section-{hash_val}"

                # Ensure anchor is unique
                original_anchor = ascii_anchor
                counter = 1
                while ascii_anchor in seen_anchors:
                    ascii_anchor = f"{original_anchor}-{counter}"
                    counter += 1
                seen_anchors.add(ascii_anchor)

                item = {
                    "name": title,
                    "anchor": ascii_anchor,
                    "depth": depth,
                }

                if result.get("child_results"):
                    item["children"] = build_nav_tree(
                        result["child_results"], depth + 1
                    )

                nav_items.append(item)

            return nav_items

        def extract_headings_from_markdown_local(content: str) -> list:
            """
            Extract hierarchical headings from markdown content.
            Returns a nested structure of RightNavItem compatible dicts.
            """
            # Use shared utility - min_level=2 for H2, max_level=4 for H4
            return extract_markdown_headings(
                content, min_level=2, max_level=4, base_depth=0
            )

        # Build right_nav from child_results if available, otherwise extract from markdown
        if child_results:
            right_nav_items = build_nav_tree(child_results)
        else:
            right_nav_items = extract_headings_from_markdown_local(overview_content)

        # If right_nav is still empty, try extracting from content
        if not right_nav_items and overview_content:
            right_nav_items = extract_headings_from_markdown_local(overview_content)

        right_nav = {"overview": right_nav_items}

        # Build enhanced tree with file paths for multi-file mode
        def build_tree_with_files(results: list, depth: int = 0) -> list:
            """
            Build tree structure with file paths for lazy loading.

            Key logic:
            - If a result has file_path AND no child_results: it's a leaf node (has document)
            - If a result has child_results but no file_path: it's a parent node (delegated)
            - If a result has both: it's a parent with its own document (rare)

            This creates the proper hierarchy:
            - Parent sections that delegated show as text (no path)
            - Child sections with documents are indented and clickable
            """
            tree_items = []
            seen_titles = set()

            for result in sorted(results, key=lambda r: r.get("order", 0)):
                title = result.get("title", "")
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)

                file_path = result.get("file_path")
                child_results = result.get("child_results", [])

                # Build the item
                # Note: headings are NOT included here to keep _index.json small
                # Headings are extracted on-demand when loading individual sections
                item = {
                    "name": title,
                    "path": file_path,  # None for parent sections that delegated
                    "type": "section",
                    "order": result.get("order", 0),
                }

                # Recursively add children if this node delegated
                if child_results:
                    children = build_tree_with_files(child_results, depth + 1)
                    if children:
                        item["children"] = children

                tree_items.append(item)

            return tree_items

        # Count section files that were actually generated by the agent
        section_files = []

        def count_files(results):
            for r in results:
                if r.get("file_path"):
                    section_files.append(r.get("file_path"))
                if r.get("child_results"):
                    count_files(r.get("child_results"))

        if child_results:
            count_files(child_results)

        # If no section files were generated, we need to create overview.md as fallback
        # This happens when agent generates content directly without delegating
        should_generate_overview = not section_files

        if should_generate_overview:
            # When generating overview.md as fallback, don't include child_results
            # (they have no actual files, just empty nodes with path=null)
            # Note: headings are NOT included - extracted on-demand when loading
            tree_items = [
                {
                    "name": "Overview",
                    "path": "overview.md",
                    "type": "overview",
                    "node_count": explored_count,
                }
            ]
        else:
            # Build the tree structure from what was actually generated
            # Only include files that actually exist
            tree_items = build_tree_with_files(child_results) if child_results else []

        # Collect sections with messages for regeneration support
        sections_with_messages = []
        for section_file in section_files:
            # Check if corresponding .messages.json file exists
            messages_file = section_file.replace(".md", ".messages.json")
            sections_with_messages.append(section_file)

        index_data = {
            "repo": repo_name,
            "generated_at": datetime.now(UTC).isoformat(),
            "version": "5.0",  # Multi-file doc version
            "generation_mode": "multi-file-recursive",
            "doc_depth": self.doc_depth,
            "statistics": {
                "sections_generated": len(child_results)
                if child_results
                else len(right_nav_items),
                "total_files": len(section_files)
                + (1 if should_generate_overview else 0),
                "total_explored_nodes": explored_count,
                "max_depth_reached": self._find_max_depth(child_results)
                if child_results
                else 0,
            },
            "tree": tree_items,
            "right_nav": right_nav,
            # New field: list of all section files for prefetching
            "section_files": section_files,
            # New fields for regeneration support
            "regeneration_enabled": True,
            "sections_with_messages": sections_with_messages,
        }

        # =================================================================
        # VERSIONED STORAGE
        # =================================================================
        # Structure:
        # wiki_doc/{repo}/
        #     _meta.json              # Version list
        #     versions/
        #         {version_id}/       # e.g., 20251201_143022_overview
        #             _index.json     # This version's index
        #             overview.md     # Only for doc_depth=0 or single-file mode
        #             sections/       # Section md files (for multi-file mode)

        import shutil

        now = datetime.now(UTC)
        version_id = f"{now.strftime('%Y%m%d_%H%M%S')}_{self.mode}"

        # Setup paths
        repo_base_path = wiki_doc_path / repo_name
        versions_path = repo_base_path / "versions"
        version_path = versions_path / version_id

        # Create version directory
        version_path.mkdir(parents=True, exist_ok=True)

        # Add version_id to index_data
        index_data["version_id"] = version_id
        index_data["mode"] = self.mode

        # Move section files from repo_base_path/sections to version_path/sections
        old_sections_path = repo_base_path / "sections"
        if old_sections_path.exists():
            new_sections_path = version_path / "sections"
            new_sections_path.mkdir(parents=True, exist_ok=True)
            referenced_filenames = {
                Path(section_file).name for section_file in section_files if section_file
            }
            for filename in sorted(referenced_filenames):
                section_file = old_sections_path / filename
                if section_file.exists():
                    shutil.move(str(section_file), str(new_sections_path / filename))

                messages_filename = filename.replace(".md", ".messages.json")
                messages_file = old_sections_path / messages_filename
                if messages_file.exists():
                    shutil.move(
                        str(messages_file),
                        str(new_sections_path / messages_filename),
                    )

            # Remove any stale partial files left from interrupted attempts
            shutil.rmtree(old_sections_path, ignore_errors=True)

        # Update tree paths to be relative to version directory
        # (paths like "sections/001_xxx.md" are already correct)

        # Write overview.md if needed (for doc_depth=0 or single-file mode)
        if should_generate_overview:
            overview_path = version_path / "overview.md"
            overview_path.write_text(overview_content, encoding="utf-8")
            logger.info(
                f"Written overview.md ({len(overview_content)} chars) to {overview_path}"
            )

        # Write _index.json to version directory
        with open(version_path / "_index.json", "w", encoding="utf-8") as f:
            json.dump(index_data, f, indent=2, ensure_ascii=False)

        # Update _meta.json with version list
        meta_path = repo_base_path / "_meta.json"
        if meta_path.exists():
            with open(meta_path, encoding="utf-8") as f:
                meta_data = json.load(f)
        else:
            meta_data = {
                "repo": repo_name,
                "versions": [],
                "current_version": None,
            }

        # Add this version to the list (avoid duplicates)
        version_info = {
            "version_id": version_id,
            "mode": self.mode,
            "doc_depth": self.doc_depth,
            "generated_at": now.isoformat(),
            "statistics": index_data.get("statistics", {}),
        }

        # Check if version already exists
        existing_version_ids = {
            v.get("version_id") for v in meta_data.get("versions", [])
        }
        if version_id not in existing_version_ids:
            meta_data["versions"].append(version_info)
            logger.info(f"Added new version {version_id} to _meta.json")
        else:
            # Update existing version info
            for i, v in enumerate(meta_data["versions"]):
                if v.get("version_id") == version_id:
                    meta_data["versions"][i] = version_info
                    logger.info(f"Updated existing version {version_id} in _meta.json")
                    break

        # Always set current version to the latest
        meta_data["current_version"] = version_id

        # Sort versions by generated_at (newest first)
        meta_data["versions"].sort(
            key=lambda v: v.get("generated_at", ""), reverse=True
        )

        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta_data, f, indent=2, ensure_ascii=False)

        logger.info(f"Written version {version_id} to {version_path}")
        logger.info(f"Generated {len(section_files)} section files")

        # Return index_data with version info
        return index_data

    def _find_max_depth(self, results: list, current: int = 0) -> int:
        """Find the maximum depth reached in results tree."""
        if not results:
            return current

        max_depth = current
        for result in results:
            if result.get("child_results"):
                child_max = self._find_max_depth(result["child_results"], current + 1)
                max_depth = max(max_depth, child_max)

        return max_depth

    def get_overview_status(
        self, repo_name: str, wiki_doc_path: Path
    ) -> dict[str, Any]:
        """Check if overview documentation exists."""
        index_path = wiki_doc_path / repo_name / "_index.json"
        overview_path = wiki_doc_path / repo_name / "overview.md"

        if not index_path.exists():
            return {"exists": False, "repo": repo_name, "version": None}

        try:
            with open(index_path) as f:
                index_data = json.load(f)
            return {
                "exists": True,
                "repo": repo_name,
                "version": index_data.get("version", "1.0"),
                "doc_depth": index_data.get("doc_depth"),
                "generated_at": index_data.get("generated_at"),
                "statistics": index_data.get("statistics", {}),
                "has_overview_md": overview_path.exists(),
            }
        except Exception as e:
            return {"exists": False, "repo": repo_name, "error": str(e)}

    async def regenerate_section(
        self,
        repo_name: str,
        version_id: str,
        section_id: str,
        feedback: str,
        wiki_doc_path: Path,
        ingestor: MemgraphIngestor,
        preserve_structure: bool = True,
    ) -> dict[str, Any]:
        """
        Regenerate a specific section with user feedback.

        Args:
            repo_name: Repository name
            version_id: Version ID of the documentation
            section_id: Section ID (e.g., "001_architecture")
            feedback: User feedback for regeneration
            wiki_doc_path: Path to wiki_doc directory
            ingestor: Memgraph ingestor instance
            preserve_structure: Whether to preserve the same heading structure

        Returns:
            Dict with regeneration result
        """
        from langchain_core.messages import messages_from_dict

        logger.info(
            f"Regenerating section {section_id} for {repo_name} (version: {version_id})"
        )

        # Load messages file
        version_path = wiki_doc_path / repo_name / "versions" / version_id
        messages_file = version_path / "sections" / f"{section_id}.messages.json"

        if not messages_file.exists():
            raise FileNotFoundError(f"Messages file not found: {messages_file}")

        try:
            with open(messages_file, encoding="utf-8") as f:
                messages_data = json.load(f)
        except Exception as e:
            raise ValueError(f"Failed to load messages file: {e}")

        # Deserialize messages
        try:
            serialized_messages = messages_data.get("messages", [])
            messages = messages_from_dict(serialized_messages)
        except Exception as e:
            raise ValueError(f"Failed to deserialize messages: {e}")

        # Extract section metadata
        section_title = messages_data.get("section_title", "")
        depth = messages_data.get("depth", 1)
        order = messages_data.get("order", 0)
        scope = messages_data.get("scope", {})
        explored_nodes = messages_data.get("explored_nodes", [])
        extraction_summaries = messages_data.get("extraction_summaries", [])

        # Add user feedback as a new HumanMessage
        feedback_prompt = f"""
User feedback for regeneration:
{feedback}

Please regenerate the documentation for "{section_title}" taking this feedback into account.
"""
        if preserve_structure:
            feedback_prompt += (
                "\nPlease maintain the same heading structure and organization."
            )

        messages.append(HumanMessage(content=feedback_prompt))

        # Build initial state for regeneration
        initial_state = {
            "messages": messages,
            "current_depth": depth,
            "scope_title": section_title,
            "scope_description": scope.get("description", ""),
            "scope_key_components": scope.get("key_components", []),
            "scope_order": order,
            "explored_nodes": explored_nodes,
            "extraction_summaries": extraction_summaries,
            "repo_name": repo_name,
            "wiki_doc_path": str(wiki_doc_path / repo_name / "sections"),
            "current_step": "regenerate",
            "progress": 0,
        }

        # Build and run workflow
        workflow = self._build_doc_agent_workflow(ingestor, repo_name)
        app = workflow.compile()

        # Run the workflow
        try:
            final_state = await app.ainvoke(initial_state)
        except Exception as e:
            logger.error(f"Failed to regenerate section: {e}")
            raise

        # Extract regenerated content
        regenerated_messages = final_state.get("messages", [])
        content = ""
        for msg in reversed(regenerated_messages):
            if isinstance(msg, AIMessage) and msg.content:
                raw = msg.content
                if "<Doc>True</Doc>" in raw:
                    parts = raw.split("<Doc>True</Doc>", 1)
                    if len(parts) > 1:
                        content = parts[1].strip()
                        break
                elif len(raw) > 200:
                    content = raw
                    break

        if not content:
            content = f"*Regenerated documentation for {section_title} could not be generated.*"

        # Save regenerated content
        section_file = version_path / "sections" / f"{section_id}.md"
        heading_prefix = self._get_heading_prefix(depth)
        full_content = f"{heading_prefix} {section_title}\n\n{content}"

        try:
            section_file.write_text(full_content, encoding="utf-8")
            logger.info(f"Saved regenerated section: {section_file}")
        except Exception as e:
            logger.error(f"Failed to save regenerated section: {e}")
            raise

        # Update messages file with new messages
        try:
            from langchain_core.messages import messages_to_dict

            serialized_messages = messages_to_dict(regenerated_messages)

            messages_data["messages"] = serialized_messages
            messages_data["explored_nodes"] = final_state.get(
                "explored_nodes", explored_nodes
            )
            messages_data["extraction_summaries"] = final_state.get(
                "extraction_summaries", extraction_summaries
            )
            messages_data["metadata"]["tool_call_count"] = sum(
                1 for msg in regenerated_messages if isinstance(msg, ToolMessage)
            )
            messages_data["metadata"]["extraction_round"] = len(
                final_state.get("extraction_summaries", extraction_summaries)
            )
            messages_data["regenerated_at"] = datetime.now(UTC).isoformat()

            messages_file.write_text(
                json.dumps(messages_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info(f"Updated messages file: {messages_file}")
        except Exception as e:
            logger.error(f"Failed to update messages file: {e}")
            # Don't fail the whole operation if messages update fails

        return {
            "success": True,
            "section_id": section_id,
            "section_title": section_title,
            "content_length": len(content),
            "regenerated_at": datetime.now(UTC).isoformat(),
        }


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================


def save_documentation_with_operator_name(
    documentation_data: dict, repo_name: str, operator_name: str
) -> str:
    """
    Save documentation with operator-based naming in hierarchical structure.

    Args:
        documentation_data: The documentation data dict
        repo_name: Name of the repository
        operator_name: Name of the operator/function

    Returns:
        Path to the saved file
    """
    from core.config import get_wiki_doc_dir

    # Get wiki_doc directory for this repo using centralized config
    wiki_doc_dir = get_wiki_doc_dir(repo_name)

    # Add timestamp and metadata
    documentation_data["timestamp"] = datetime.now(UTC).isoformat()
    documentation_data["saved_at"] = datetime.now(UTC).isoformat()
    documentation_data["repo_name"] = repo_name
    documentation_data["operator_name"] = operator_name

    # Save as <operator_name>.json
    file_path = wiki_doc_dir / f"{operator_name}.json"

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(documentation_data, f, ensure_ascii=False, indent=2)
        logger.info(f"Documentation saved to: {file_path}")
    except Exception as e:
        logger.error(f"Failed to save documentation to {file_path}: {e}")
        raise

    return str(file_path)


# Backward compatibility alias
_save_documentation_with_operator_name = save_documentation_with_operator_name


# ============================================================================
# FACTORY FUNCTIONS
# ============================================================================


def create_doc_orchestrator(
    doc_depth: int = 2,
    mode: str = "overview",
    inherit_from_depth: int = 1,
    doc_mode: DocMode | str = DocMode.REPOSITORY,
) -> DocOrchestrator:
    """
    Factory function to create the unified document orchestrator.

    Args:
        doc_depth: Maximum depth for documentation hierarchy.
                   0 = single page, 1 = with sections, 2 = with subsections, etc.
        mode: Thoroughness mode:
              - "overview" (default): Fast, architecture-focused. ~100k-300k tokens.
                Good for understanding structure, design patterns, and key abstractions.
                Users can ask chat for implementation details.
              - "detailed": Comprehensive, implementation-focused. ~500k-2M tokens.
                Includes detailed code analysis and implementation notes.
        inherit_from_depth: From which depth level child agents inherit parent's messages.
                   0 = no inheritance (each agent starts fresh, original behavior)
                   1 = section agents (depth=1) inherit root's exploration context (recommended)
                   2 = only subsection agents (depth=2+) inherit parent's context
                   Default: 1 - section agents benefit from root's exploration without re-exploring
        doc_mode: Documentation mode - DocMode.REPOSITORY for full repo docs,
                  DocMode.RESEARCH for topic research. Default: DocMode.REPOSITORY

    Returns:
        Configured DocOrchestrator instance
    """
    logger.info(
        f"Creating DocOrchestrator (doc_mode={doc_mode}, doc_depth={doc_depth}, "
        f"mode={mode}, inherit_from_depth={inherit_from_depth})..."
    )
    orchestrator = DocOrchestrator(
        doc_depth=doc_depth,
        mode=mode,
        inherit_from_depth=inherit_from_depth,
        doc_mode=doc_mode,
    )
    logger.info("DocOrchestrator created successfully")
    return orchestrator


def initialize_doc_agent(
    repo_path: str,
    ingestor: MemgraphIngestor,
    doc_mode: DocMode | str = DocMode.RESEARCH,
    doc_depth: int = 2,
    mode: str = "detailed",
    model: str | None = None,
) -> DocOrchestrator:
    """
    Initialize a document agent for research or documentation.

    This is the replacement for initialize_langgraph_agent from langgraph_orchestrator.
    It creates a DocOrchestrator configured for the specified mode.

    Args:
        repo_path: Path to the repository being analyzed
        ingestor: MemgraphIngestor instance for database operations
        doc_mode: Documentation mode - DocMode.RESEARCH for topic research (default),
                  DocMode.REPOSITORY for full repo documentation.
        doc_depth: Maximum depth for documentation hierarchy. Default: 2
        mode: Thoroughness mode - "detailed" (default for research) or "overview"
        model: Optional model ID to use. If provided, overrides default model.

    Returns:
        Configured DocOrchestrator instance

    Example:
        # Research a specific topic
        agent = initialize_doc_agent(repo_path, ingestor, doc_mode=DocMode.RESEARCH)
        result = await agent.generate_single_doc(
            repo_name="my-repo",
            ingestor=ingestor,
            topic="attention mechanisms",
            description="How attention is implemented in this codebase"
        )
    """
    logger.info(
        f"Initializing doc agent: doc_mode={doc_mode}, depth={doc_depth}, mode={mode}, model={model or 'default'}"
    )

    # Build model_config if a specific model was requested
    model_config = None
    if model:
        from core.config import ModelConfig, settings

        base_config = settings.active_orchestrator_config
        model_config = ModelConfig(
            provider=base_config.provider,
            model_id=model,
            api_key=base_config.api_key,
            endpoint=base_config.endpoint,
            project_id=base_config.project_id,
            region=base_config.region,
            provider_type=base_config.provider_type,
            thinking_budget=base_config.thinking_budget,
            service_account_file=base_config.service_account_file,
        )
        logger.info(f"Using custom model for research: {model}")

    orchestrator = DocOrchestrator(
        doc_depth=doc_depth,
        mode=mode,
        inherit_from_depth=1,
        doc_mode=doc_mode,
        model_config=model_config,
    )

    # Store repo_path and ingestor for later use
    orchestrator._repo_path = repo_path
    orchestrator._default_ingestor = ingestor

    return orchestrator


# ============================================================================
# BACKWARD COMPATIBILITY ALIASES
# ============================================================================

# Alias for backward compatibility with existing code
RecursiveDocOrchestrator = DocOrchestrator

# Alias factory function for backward compatibility
create_recursive_doc_orchestrator = create_doc_orchestrator

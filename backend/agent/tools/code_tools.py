# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import re
from pathlib import Path
from typing import Any, Literal

from core.schemas import CodeSnippet
from graph.service import MemgraphIngestor
from langchain_core.tools import BaseTool, StructuredTool
from loguru import logger
from pydantic import BaseModel, Field

from .graph_query import GraphQueryTools
from .tool_registry import TOOL_DESCRIPTIONS

# =============================================================================
# Data Models
# =============================================================================


class CodeContext(BaseModel):
    """Comprehensive context about a code element."""

    qualified_name: str
    element_type: str  # Function, Method, Class, File
    definition: dict[str, Any] = Field(default_factory=dict)  # Basic info from graph
    code_snippet: str | None = None  # Source code if available
    callers: list[dict[str, Any]] = Field(default_factory=list)  # What calls this
    called: list[dict[str, Any]] = Field(default_factory=list)  # What this calls
    imports: list[dict[str, Any]] = Field(default_factory=list)  # What this imports
    imported_by: list[dict[str, Any]] = Field(default_factory=list)  # What imports this
    defined_in: dict[str, Any] = Field(default_factory=dict)  # Parent module/file
    children: list[dict[str, Any]] = Field(default_factory=list)  # For classes/modules
    inheritance: dict[str, Any] = Field(default_factory=dict)  # For classes
    parameters: list[dict[str, Any]] = Field(
        default_factory=list
    )  # Function/method parameters
    return_type: str | None = None  # Return type annotation
    decorators: list[str] = Field(default_factory=list)  # Decorators
    docstring: str | None = None  # Docstring
    line_range: dict[str, int] = Field(default_factory=dict)  # start_line, end_line
    statistics: dict[str, Any] = Field(default_factory=dict)  # Statistics about usage
    summary: str = ""


class DependencyChain(BaseModel):
    """A chain of dependencies between code elements."""

    chain: list[dict[str, Any]]  # List of (from, to, relationship_type) dicts
    depth: int
    total_elements: int
    summary: str
    has_circular: bool = False  # Whether circular dependencies detected
    circular_paths: list[list[str]] = Field(
        default_factory=list
    )  # Circular dependency paths


class DependencyTree(BaseModel):
    """A tree structure representing dependencies from a root node."""

    root: str
    relationship_type: str
    tree: dict[str, Any]  # Nested tree structure
    total_nodes: int
    max_depth: int
    summary: str
    has_circular: bool = False
    circular_paths: list[list[str]] = Field(default_factory=list)


class FileContent(BaseModel):
    """Result of file read operation."""

    identifier: str  # The input identifier (path or qualified_name)
    file_path: str  # Resolved file path
    content: str  # File content (possibly truncated or filtered)
    total_chars: int  # Total characters in file
    total_lines: int  # Total lines in file
    truncated: bool = False  # Whether content was truncated
    matches: list[dict[str, Any]] | None = None  # Pattern match results
    match_count: int = 0  # Number of matches found
    found: bool = True
    error_message: str | None = None


class PatternMatch(BaseModel):
    """A single pattern match result."""

    line_number: int
    line_content: str
    context_before: list[str] = Field(default_factory=list)
    context_after: list[str] = Field(default_factory=list)


class MatchRegion(BaseModel):
    """A merged contiguous match region."""

    start_line: int  # 1-indexed
    end_line: int  # inclusive
    content: str  # Formatted content with line numbers and > markers
    match_count: int  # Number of actual matches in this region


class FileSearchResult(BaseModel):
    """Search results for a single file."""

    file_path: str  # Relative path
    regions: list[MatchRegion] = Field(default_factory=list)
    total_matches: int = 0
    truncated: bool = False  # Whether regions were truncated


class FolderSearchResult(BaseModel):
    """Search results for a folder."""

    identifier: str  # The input identifier
    folder_path: str  # Resolved folder path
    pattern: str | None = None  # Search pattern (None for directory listing)
    results: list[FileSearchResult] = Field(default_factory=list)

    # Statistics
    files_searched: int = 0
    files_matched: int = 0
    total_matches: int = 0

    # Adaptive info
    context_lines_used: int = 5
    truncated: bool = False
    found: bool = True
    error_message: str | None = None

    # For directory listing mode (no pattern)
    directory_tree: str | None = None

    def format_output(self) -> str:
        """Format results for display."""
        if self.error_message:
            return f"Error: {self.error_message}"

        if self.directory_tree:
            # Directory listing mode
            return self.directory_tree

        # Search results mode
        if not self.results:
            return f"No matches found for pattern: {self.pattern}"

        parts = []
        for file_result in self.results:
            parts.append(file_result.file_path)
            for region in file_result.regions:
                parts.append(f"[{region.start_line}-{region.end_line}]")
                parts.append(region.content)
            if file_result.truncated:
                parts.append("... (more matches truncated)")
            parts.append("")  # Blank line between files

        # Add footer with stats
        footer = f"---\nSearched: {self.files_searched} files | Matched: {self.files_matched} files | Total: {self.total_matches} matches"
        if self.context_lines_used < 5:
            footer += f" | Context: {self.context_lines_used} lines (adapted)"
        if self.truncated:
            footer += " | (results truncated)"
        parts.append(footer)

        return "\n".join(parts)


# =============================================================================
# Shared Utilities
# =============================================================================


def _resolve_repo_root(
    ingestor: MemgraphIngestor,
    target_repo: str,
    project_name: str,
    project_root: Path,
) -> Path:
    """Resolve the root path for a given repository.

    Args:
        ingestor: MemgraphIngestor instance for database queries.
        target_repo: Name of the target repository.
        project_name: Name of the current project.
        project_root: Root path of the current project.

    Returns:
        Resolved root path for the target repository.
    """
    if target_repo != project_name:
        project_query = """
            MATCH (p:Project {name: $repo_name})
            RETURN p.path AS path
            LIMIT 1
        """
        project_results = ingestor.fetch_all(project_query, {"repo_name": target_repo})
        if project_results and project_results[0].get("path"):
            return Path(project_results[0]["path"])
        return project_root.parent / target_repo
    return project_root


# =============================================================================
# CodeRetriever - Get source code snippets by qualified_name
# =============================================================================


class CodeRetriever:
    """Service to retrieve code snippets using the graph and filesystem."""

    def __init__(
        self,
        project_root: str,
        ingestor: MemgraphIngestor,
        project_name: str | None = None,
    ):
        self.project_root = Path(project_root).resolve()
        self.ingestor = ingestor
        self.project_name = project_name or self.project_root.name
        logger.info(
            f"CodeRetriever initialized with root: {self.project_root}, project: {self.project_name}"
        )

    async def find_code_snippet(
        self, qualified_name: str, repo_name: str | None = None
    ) -> CodeSnippet:
        """
        Finds a code snippet by querying the graph for its location.

        Args:
            qualified_name: The qualified name of the code element
            repo_name: Optional explicit repo name

        Returns:
            CodeSnippet with the source code and metadata
        """
        logger.info(
            f"[CodeRetriever] Searching for: {qualified_name} (repo: {repo_name})"
        )

        qn_to_search = qualified_name
        target_repo = repo_name

        if not target_repo:
            parts = qualified_name.split(".")
            if len(parts) >= 2:
                potential_repo = parts[0]
                repo_check_query = """
                    MATCH (p:Project {name: $repo_name})
                    RETURN p.name AS name
                    LIMIT 1
                """
                repo_results = self.ingestor.fetch_all(
                    repo_check_query, {"repo_name": potential_repo}
                )
                if repo_results:
                    target_repo = potential_repo
                    logger.info(
                        f"[CodeRetriever] Detected repo from qualified_name: {target_repo}"
                    )
                else:
                    target_repo = self.project_name
                    qn_to_search = f"{self.project_name}.{qualified_name}"
            else:
                target_repo = self.project_name
                qn_to_search = f"{self.project_name}.{qualified_name}"
        else:
            if not qualified_name.startswith(f"{target_repo}."):
                qn_to_search = f"{target_repo}.{qualified_name}"

        logger.info(
            f"[CodeRetriever] Searching in repo '{target_repo}' for: {qn_to_search}"
        )

        query = """
            MATCH (n)
            WHERE n.qualified_name = $qn
            OPTIONAL MATCH (f:File)-[:DEFINES]->(n)
            RETURN n.name AS name, n.start_line AS start, n.end_line AS end,
                   COALESCE(n.path, f.path) AS path, n.docstring AS docstring,
                   f.module_context AS module_context
            LIMIT 1
        """
        params = {"qn": qn_to_search}
        try:
            results = self.ingestor.fetch_all(query, params)

            if not results:
                return CodeSnippet(
                    qualified_name=qualified_name,
                    source_code="",
                    file_path="",
                    line_start=0,
                    line_end=0,
                    found=False,
                    error_message=f"Entity not found in graph. Searched for: {qn_to_search} in repo: {target_repo}",
                )

            res = results[0]
            file_path_str = res.get("path")
            start_line = res.get("start")
            end_line = res.get("end")

            if not all([file_path_str, start_line, end_line]):
                return CodeSnippet(
                    qualified_name=qualified_name,
                    source_code="",
                    file_path=file_path_str or "",
                    line_start=0,
                    line_end=0,
                    found=False,
                    error_message="Graph entry is missing location data.",
                )

            repo_root = _resolve_repo_root(
                self.ingestor, target_repo, self.project_name, self.project_root
            )
            full_path = repo_root / file_path_str

            logger.info(f"[CodeRetriever] Reading from: {full_path}")

            with full_path.open("r", encoding="utf-8") as f:
                all_lines = f.readlines()

            snippet_lines = all_lines[start_line - 1 : end_line]
            source_code = "".join(snippet_lines)

            return CodeSnippet(
                qualified_name=qualified_name,
                source_code=source_code,
                file_path=file_path_str,
                line_start=start_line,
                line_end=end_line,
                docstring=res.get("docstring"),
                module_context=res.get("module_context"),
            )
        except Exception as e:
            logger.error(f"[CodeRetriever] Error: {e}", exc_info=True)
            return CodeSnippet(
                qualified_name=qualified_name,
                source_code="",
                file_path="",
                line_start=0,
                line_end=0,
                found=False,
                error_message=str(e),
            )


# =============================================================================
# FileReader - Read file/folder content with optional pattern matching
# =============================================================================

# Default exclude patterns for folder search
DEFAULT_EXCLUDE_PATTERNS = [
    # Directories (matched against path)
    r"\.git/",
    r"node_modules/",
    r"__pycache__/",
    r"\.venv/",
    r"venv/",
    r"\.idea/",
    r"\.vscode/",
    r"dist/",
    r"build/",
    r"\.next/",
    r"\.cache/",
    r"\.mypy_cache/",
    r"\.pytest_cache/",
    r"\.ruff_cache/",
    r"egg-info/",
    r"\.eggs/",
    # File patterns (matched against filename or path)
    r"cache",
    r"\.log$",
    r"\.lock$",
    # Binary/large files
    r"\.(png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot|pdf|zip|tar|gz|bz2|7z|rar)$",
    r"\.(pyc|pyo|so|dll|exe|bin|obj|o|a)$",
    r"\.(min\.js|min\.css)$",
]

# Max file size to search (1MB)
MAX_SEARCHABLE_FILE_SIZE = 1 * 1024 * 1024


class FileReader:
    """
    Read file/folder content by path or qualified_name with optional pattern matching.

    Features:
    - Read file by path (relative to project root) or by File/Folder's qualified_name
    - Full content read with configurable max character limit
    - Pattern matching (regex or literal) with context lines
    - Folder search with recursive file matching
    - Directory structure listing
    - Adaptive context line reduction
    - Cross-repository support
    """

    def __init__(
        self,
        project_root: str,
        ingestor: MemgraphIngestor,
        project_name: str | None = None,
    ):
        self.project_root = Path(project_root).resolve()
        self.ingestor = ingestor
        self.project_name = project_name or self.project_root.name
        self._exclude_patterns = [
            re.compile(p, re.IGNORECASE) for p in DEFAULT_EXCLUDE_PATTERNS
        ]
        self._recent_reads: dict[str, str] = {}  # resolved_path -> short summary
        logger.info(
            f"FileReader initialized with root: {self.project_root}, project: {self.project_name}"
        )

    def clear_reads(self) -> None:
        """Clear the recent-reads cache (call on session/project switch)."""
        self._recent_reads.clear()

    def _resolve_file_path(
        self, identifier: str, repo_name: str | None = None
    ) -> tuple[Path | None, str | None]:
        """
        Resolve identifier to actual file path.

        Args:
            identifier: File path (relative) or File's qualified_name
            repo_name: Optional repository name

        Returns:
            Tuple of (resolved_path, error_message)
        """
        target_repo = repo_name or self.project_name

        # First, try to find by path directly
        # Check if identifier looks like a path (contains / or ends with file extension)
        if "/" in identifier or "\\" in identifier or "." in identifier.split("/")[-1]:
            # Try as relative path first
            repo_root = _resolve_repo_root(
                self.ingestor, target_repo, self.project_name, self.project_root
            )
            full_path = repo_root / identifier
            if full_path.exists() and full_path.is_file():
                return full_path, None

        # Try to find by qualified_name in the graph
        qn_to_search = identifier
        if not identifier.startswith(f"{target_repo}."):
            qn_to_search = f"{target_repo}.{identifier}"

        query = """
            MATCH (f:File)
            WHERE f.qualified_name = $qn OR f.path = $path
            RETURN f.path AS path
            LIMIT 1
        """
        results = self.ingestor.fetch_all(
            query, {"qn": qn_to_search, "path": identifier}
        )

        if results and results[0].get("path"):
            file_path_str = results[0]["path"]

            # Handle virtual paths
            if file_path_str.startswith("<"):
                return None, f"Cannot read virtual file: {file_path_str}"

            repo_root = _resolve_repo_root(
                self.ingestor, target_repo, self.project_name, self.project_root
            )
            full_path = repo_root / file_path_str
            if full_path.exists():
                return full_path, None
            return None, f"File not found on disk: {full_path}"

        return (
            None,
            f"File not found: {identifier} (searched as path and qualified_name)",
        )

    async def read_file(
        self,
        identifier: str,
        pattern: str | None = None,
        match_mode: Literal["full", "regex", "literal"] = "full",
        max_chars: int = 20000,
        context_lines: int = 5,
        repo_name: str | None = None,
    ) -> FileContent:
        """
        Read file content with optional pattern matching.

        Args:
            identifier: File path (relative to project root) or File's qualified_name
            pattern: Optional pattern to search for in the file
            match_mode:
                - "full": Read entire file (default)
                - "regex": Search using regex pattern
                - "literal": Search for literal string
            max_chars: Maximum characters to return (default: 20000)
            context_lines: Lines of context around each match (default: 5)
            repo_name: Optional repository name for cross-repo access

        Returns:
            FileContent with file content and optional match results
        """
        logger.info(
            f"[FileReader] Reading: {identifier} (mode: {match_mode}, pattern: {pattern})"
        )

        # Resolve file path
        full_path, error = self._resolve_file_path(identifier, repo_name)
        if error:
            return FileContent(
                identifier=identifier,
                file_path="",
                content="",
                total_chars=0,
                total_lines=0,
                found=False,
                error_message=error,
            )

        try:
            with full_path.open("r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            total_chars = len(content)
            lines = content.split("\n")
            total_lines = len(lines)

            # Full read mode
            if match_mode == "full" or not pattern:
                truncated = total_chars > max_chars
                return_content = content[:max_chars] if truncated else content

                return FileContent(
                    identifier=identifier,
                    file_path=str(full_path.relative_to(self.project_root))
                    if full_path.is_relative_to(self.project_root)
                    else str(full_path),
                    content=return_content,
                    total_chars=total_chars,
                    total_lines=total_lines,
                    truncated=truncated,
                    found=True,
                )

            # Pattern matching mode
            matches = []
            try:
                if match_mode == "regex":
                    compiled_pattern = re.compile(pattern, re.IGNORECASE)
                else:  # literal
                    compiled_pattern = re.compile(re.escape(pattern), re.IGNORECASE)

                for i, line in enumerate(lines):
                    if compiled_pattern.search(line):
                        # Get context
                        start_ctx = max(0, i - context_lines)
                        end_ctx = min(len(lines), i + context_lines + 1)

                        matches.append(
                            {
                                "line_number": i + 1,  # 1-indexed
                                "line_content": line,
                                "context_before": lines[start_ctx:i],
                                "context_after": lines[i + 1 : end_ctx],
                            }
                        )

            except re.error as e:
                return FileContent(
                    identifier=identifier,
                    file_path=str(full_path),
                    content="",
                    total_chars=total_chars,
                    total_lines=total_lines,
                    found=True,
                    error_message=f"Invalid regex pattern: {e}",
                )

            # Build content from matches
            if matches:
                content_parts = []
                for match in matches:
                    content_parts.append(f"--- Line {match['line_number']} ---")
                    for j, ctx_line in enumerate(match["context_before"]):
                        line_no = (
                            match["line_number"] - len(match["context_before"]) + j
                        )
                        content_parts.append(f"{line_no:4d}  {ctx_line}")
                    content_parts.append(
                        f"{match['line_number']:4d}> {match['line_content']}"
                    )  # Highlight match line
                    for j, ctx_line in enumerate(match["context_after"]):
                        line_no = match["line_number"] + j + 1
                        content_parts.append(f"{line_no:4d}  {ctx_line}")
                    content_parts.append("")  # Blank line between matches

                return_content = "\n".join(content_parts)
                truncated = len(return_content) > max_chars
                if truncated:
                    return_content = return_content[:max_chars] + "\n... (truncated)"
            else:
                return_content = f"No matches found for pattern: {pattern}"

            return FileContent(
                identifier=identifier,
                file_path=str(full_path.relative_to(self.project_root))
                if full_path.is_relative_to(self.project_root)
                else str(full_path),
                content=return_content,
                total_chars=total_chars,
                total_lines=total_lines,
                truncated=len(return_content) > max_chars,
                matches=matches,
                match_count=len(matches),
                found=True,
            )

        except Exception as e:
            logger.error(f"[FileReader] Error reading file: {e}", exc_info=True)
            return FileContent(
                identifier=identifier,
                file_path=str(full_path) if full_path else "",
                content="",
                total_chars=0,
                total_lines=0,
                found=False,
                error_message=str(e),
            )

    def _should_exclude(
        self, path: Path, extra_excludes: list[str] | None = None
    ) -> bool:
        """Check if a path should be excluded from search."""
        path_str = str(path)

        # For directories, add trailing / to match directory patterns
        if path.is_dir():
            path_str_with_slash = path_str + "/"
        else:
            path_str_with_slash = path_str

        # Check default patterns (try both with and without trailing slash for dirs)
        for pattern in self._exclude_patterns:
            if pattern.search(path_str) or pattern.search(path_str_with_slash):
                return True

        # Check extra patterns
        if extra_excludes:
            for pattern_str in extra_excludes:
                try:
                    if re.search(pattern_str, path_str, re.IGNORECASE):
                        return True
                    if path.is_dir() and re.search(
                        pattern_str, path_str_with_slash, re.IGNORECASE
                    ):
                        return True
                except re.error:
                    pass

        return False

    def _should_include_extension(
        self, path: Path, include_extensions: list[str] | None
    ) -> bool:
        """Check if file extension should be included."""
        if not include_extensions:
            return True

        suffix = path.suffix.lower()
        for ext in include_extensions:
            # Normalize extension format
            ext_normalized = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
            if suffix == ext_normalized:
                return True
        return False

    def _resolve_identifier_type(
        self, identifier: str, repo_name: str | None = None
    ) -> tuple[Literal["file", "folder", "unknown"], Path | None, str | None]:
        """
        Determine if identifier is a file or folder.

        Returns:
            Tuple of (type, resolved_path, error_message)
        """
        target_repo = repo_name or self.project_name

        # Get repo root
        repo_root = _resolve_repo_root(
            self.ingestor, target_repo, self.project_name, self.project_root
        )

        # Try as direct path first
        full_path = repo_root / identifier
        if full_path.exists():
            if full_path.is_dir():
                return "folder", full_path, None
            elif full_path.is_file():
                return "file", full_path, None

        # Try to find in graph by qualified_name
        qn_to_search = identifier
        if not identifier.startswith(f"{target_repo}."):
            qn_to_search = f"{target_repo}.{identifier}"

        # Check if it's a Folder node
        folder_query = """
            MATCH (n)
            WHERE n.qualified_name = $qn AND (n:Folder OR n:Directory)
            RETURN n.path AS path, labels(n) AS labels
            LIMIT 1
        """
        folder_results = self.ingestor.fetch_all(folder_query, {"qn": qn_to_search})
        if folder_results and folder_results[0].get("path"):
            folder_path = repo_root / folder_results[0]["path"]
            if folder_path.exists() and folder_path.is_dir():
                return "folder", folder_path, None

        # Check if it's a File node
        file_query = """
            MATCH (f:File)
            WHERE f.qualified_name = $qn OR f.path = $path
            RETURN f.path AS path
            LIMIT 1
        """
        file_results = self.ingestor.fetch_all(
            file_query, {"qn": qn_to_search, "path": identifier}
        )
        if file_results and file_results[0].get("path"):
            file_path_str = file_results[0]["path"]
            if not file_path_str.startswith("<"):  # Skip virtual files
                file_path = repo_root / file_path_str
                if file_path.exists() and file_path.is_file():
                    return "file", file_path, None

        return "unknown", None, f"Path not found: {identifier}"

    def _collect_files(
        self,
        folder_path: Path,
        include_extensions: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        max_files: int = 100,
    ) -> list[Path]:
        """Collect files from folder recursively with filters."""
        files = []

        try:
            for item in folder_path.rglob("*"):
                if len(files) >= max_files:
                    break

                if not item.is_file():
                    continue

                # Check size
                try:
                    if item.stat().st_size > MAX_SEARCHABLE_FILE_SIZE:
                        continue
                except OSError:
                    continue

                # Check exclusions
                if self._should_exclude(item, exclude_patterns):
                    continue

                # Check extensions
                if not self._should_include_extension(item, include_extensions):
                    continue

                files.append(item)

        except PermissionError:
            logger.warning(f"Permission denied accessing: {folder_path}")
        except Exception as e:
            logger.warning(f"Error collecting files from {folder_path}: {e}")

        return files

    def _merge_match_regions(
        self, matches: list[dict], lines: list[str], context_lines: int
    ) -> list[MatchRegion]:
        """
        Merge adjacent matches into contiguous regions.

        Args:
            matches: List of match dicts with 'line_number' (1-indexed)
            lines: All lines of the file
            context_lines: Number of context lines around each match

        Returns:
            List of merged MatchRegion objects
        """
        if not matches:
            return []

        # Sort by line number
        sorted_matches = sorted(matches, key=lambda m: m["line_number"])

        regions = []
        current_region_start = None
        current_region_end = None
        current_match_lines = []

        for match in sorted_matches:
            line_num = match["line_number"]  # 1-indexed
            region_start = max(1, line_num - context_lines)
            region_end = min(len(lines), line_num + context_lines)

            if current_region_start is None:
                # First match
                current_region_start = region_start
                current_region_end = region_end
                current_match_lines = [line_num]
            elif region_start <= current_region_end + 1:
                # Overlapping or adjacent, merge
                current_region_end = max(current_region_end, region_end)
                current_match_lines.append(line_num)
            else:
                # Gap, emit current region and start new one
                regions.append(
                    self._build_region(
                        current_region_start,
                        current_region_end,
                        current_match_lines,
                        lines,
                    )
                )
                current_region_start = region_start
                current_region_end = region_end
                current_match_lines = [line_num]

        # Emit last region
        if current_region_start is not None:
            regions.append(
                self._build_region(
                    current_region_start, current_region_end, current_match_lines, lines
                )
            )

        return regions

    def _build_region(
        self, start_line: int, end_line: int, match_lines: list[int], lines: list[str]
    ) -> MatchRegion:
        """Build a MatchRegion with formatted content."""
        content_parts = []
        match_line_set = set(match_lines)

        for line_num in range(start_line, end_line + 1):
            line_idx = line_num - 1  # Convert to 0-indexed
            if 0 <= line_idx < len(lines):
                line_content = lines[line_idx]
                if line_num in match_line_set:
                    content_parts.append(f">{line_num:4d} │ {line_content}")
                else:
                    content_parts.append(f" {line_num:4d} │ {line_content}")

        return MatchRegion(
            start_line=start_line,
            end_line=end_line,
            content="\n".join(content_parts),
            match_count=len(match_lines),
        )

    def _search_file_for_pattern(
        self,
        file_path: Path,
        compiled_pattern: re.Pattern,
        context_lines: int,
        max_matches: int = 20,
    ) -> tuple[list[dict], list[str]]:
        """
        Search a file for pattern matches.

        Returns:
            Tuple of (matches_list, lines_list)
        """
        try:
            with file_path.open("r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            lines = content.split("\n")
            matches = []

            for i, line in enumerate(lines):
                if len(matches) >= max_matches:
                    break
                if compiled_pattern.search(line):
                    matches.append(
                        {
                            "line_number": i + 1,  # 1-indexed
                            "line_content": line,
                        }
                    )

            return matches, lines

        except Exception as e:
            logger.debug(f"Error searching file {file_path}: {e}")
            return [], []

    def _list_folder_structure(
        self, folder_path: Path, max_depth: int = 3, max_items_per_level: int = 20
    ) -> str:
        """
        Generate a tree structure of the folder.

        Returns:
            Formatted tree string
        """

        def build_tree(path: Path, prefix: str = "", depth: int = 0) -> list[str]:
            if depth > max_depth:
                return [f"{prefix}..."]

            items = []
            try:
                entries = sorted(
                    path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
                )
            except PermissionError:
                return [f"{prefix}[permission denied]"]
            except Exception:
                return []

            # Filter out excluded items
            filtered_entries = [e for e in entries if not self._should_exclude(e, None)]

            shown = filtered_entries[:max_items_per_level]
            remaining = len(filtered_entries) - len(shown)

            for i, entry in enumerate(shown):
                is_last = (i == len(shown) - 1) and (remaining == 0)
                connector = "└── " if is_last else "├── "
                child_prefix = prefix + ("    " if is_last else "│   ")

                if entry.is_dir():
                    items.append(f"{prefix}{connector}{entry.name}/")
                    items.extend(build_tree(entry, child_prefix, depth + 1))
                else:
                    # Show file size
                    try:
                        size = entry.stat().st_size
                        size_str = self._format_size(size)
                        items.append(f"{prefix}{connector}{entry.name} ({size_str})")
                    except OSError:
                        items.append(f"{prefix}{connector}{entry.name}")

            if remaining > 0:
                items.append(f"{prefix}└── ... ({remaining} more items)")

            return items

        try:
            rel_path = folder_path.relative_to(self.project_root)
        except ValueError:
            rel_path = folder_path

        tree_lines = [f"{rel_path}/"]
        tree_lines.extend(build_tree(folder_path))
        return "\n".join(tree_lines)

    @staticmethod
    def _format_size(size: int) -> str:
        """Format file size in human-readable form."""
        size_f = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if size_f < 1024:
                return f"{size_f:.1f}{unit}" if unit != "B" else f"{int(size_f)}{unit}"
            size_f /= 1024
        return f"{size_f:.1f}TB"

    async def search_folder(
        self,
        folder_path: Path,
        pattern: str,
        match_mode: Literal["regex", "literal"] = "regex",
        include_extensions: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        max_total_chars: int = 30000,
        max_files: int = 50,
        max_matches_per_file: int = 10,
        preferred_context_lines: int = 5,
        min_context_lines: int = 1,
    ) -> FolderSearchResult:
        """
        Search folder recursively for pattern matches.

        Args:
            folder_path: Path to folder
            pattern: Search pattern
            match_mode: "regex" or "literal"
            include_extensions: Optional list of extensions to include (e.g., [".py", ".ts"])
            exclude_patterns: Additional patterns to exclude
            max_total_chars: Maximum total output characters
            max_files: Maximum files to search
            max_matches_per_file: Maximum matches per file
            preferred_context_lines: Desired context lines
            min_context_lines: Minimum context lines for adaptive reduction

        Returns:
            FolderSearchResult with search results
        """
        logger.info(
            f"[FileReader] Searching folder: {folder_path} for pattern: {pattern}"
        )

        try:
            rel_folder = folder_path.relative_to(self.project_root)
        except ValueError:
            rel_folder = folder_path

        # Compile pattern
        try:
            if match_mode == "regex":
                compiled_pattern = re.compile(pattern, re.IGNORECASE)
            else:
                compiled_pattern = re.compile(re.escape(pattern), re.IGNORECASE)
        except re.error as e:
            return FolderSearchResult(
                identifier=str(rel_folder),
                folder_path=str(rel_folder),
                pattern=pattern,
                found=True,
                error_message=f"Invalid regex pattern: {e}",
            )

        # Collect files
        files = self._collect_files(
            folder_path, include_extensions, exclude_patterns, max_files
        )

        if not files:
            return FolderSearchResult(
                identifier=str(rel_folder),
                folder_path=str(rel_folder),
                pattern=pattern,
                files_searched=0,
                found=True,
                error_message="No searchable files found in folder",
            )

        # Adaptive context strategy
        context_lines = preferred_context_lines
        results = []
        total_matches = 0
        files_matched = 0
        total_chars = 0
        truncated = False

        while context_lines >= min_context_lines:
            results = []
            total_matches = 0
            files_matched = 0
            total_chars = 0
            truncated = False

            for file_path in files:
                if total_chars >= max_total_chars:
                    truncated = True
                    break

                matches, lines = self._search_file_for_pattern(
                    file_path, compiled_pattern, context_lines, max_matches_per_file
                )

                if not matches:
                    continue

                files_matched += 1
                total_matches += len(matches)

                # Merge regions
                regions = self._merge_match_regions(matches, lines, context_lines)

                # Calculate chars
                try:
                    file_rel_path = file_path.relative_to(self.project_root)
                except ValueError:
                    file_rel_path = file_path

                file_result = FileSearchResult(
                    file_path=str(file_rel_path),
                    regions=regions,
                    total_matches=len(matches),
                    truncated=len(matches) >= max_matches_per_file,
                )

                # Estimate output size
                file_chars = len(str(file_rel_path)) + sum(
                    len(f"[{r.start_line}-{r.end_line}]") + len(r.content) + 2
                    for r in regions
                )

                if total_chars + file_chars > max_total_chars:
                    truncated = True
                    # Add partial results if space
                    if total_chars < max_total_chars * 0.9:
                        results.append(file_result)
                        total_chars += file_chars
                    break

                results.append(file_result)
                total_chars += file_chars

            # Check if we need to reduce context
            if total_chars <= max_total_chars or context_lines <= min_context_lines:
                break

            # Reduce context and retry
            context_lines -= 1
            logger.debug(f"Reducing context lines to {context_lines} due to size limit")

        return FolderSearchResult(
            identifier=str(rel_folder),
            folder_path=str(rel_folder),
            pattern=pattern,
            results=results,
            files_searched=len(files),
            files_matched=files_matched,
            total_matches=total_matches,
            context_lines_used=context_lines,
            truncated=truncated,
            found=True,
        )

    async def read(
        self,
        identifier: str,
        pattern: str | None = None,
        match_mode: Literal["full", "regex", "literal"] = "full",
        max_chars: int = 20000,
        context_lines: int = 5,
        repo_name: str | None = None,
        # Folder-specific options
        include_extensions: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        max_files: int = 50,
        max_matches_per_file: int = 10,
    ) -> FileContent | FolderSearchResult:
        """
        Unified entry point for reading files or searching folders.

        Automatically detects whether identifier is a file or folder and routes
        to appropriate handler.

        Args:
            identifier: File path, folder path, or qualified_name
            pattern: Optional pattern for searching (required for folders)
            match_mode: "full" (file only), "regex", or "literal"
            max_chars: Maximum characters to return
            context_lines: Lines of context around each match
            repo_name: Optional repository name for cross-repo access
            include_extensions: For folders, filter by extensions (e.g., [".py"])
            exclude_patterns: For folders, additional exclude patterns
            max_files: For folders, max files to search
            max_matches_per_file: For folders, max matches per file

        Returns:
            FileContent for files, FolderSearchResult for folders
        """
        logger.info(
            f"[FileReader] read() called with: {identifier} (pattern: {pattern}, mode: {match_mode})"
        )

        # Dedup: for full reads (no pattern), check if we already read this file
        if match_mode == "full" and not pattern:
            id_type_check, resolved_check, _ = self._resolve_identifier_type(
                identifier, repo_name
            )
            if id_type_check == "file" and resolved_check:
                cache_key = str(resolved_check)
                if cache_key in self._recent_reads:
                    summary = self._recent_reads[cache_key]
                    return FileContent(
                        identifier=identifier,
                        file_path=cache_key,
                        content=f"[Already read] {summary}\nUse pattern search if you need specific sections.",
                        total_chars=0,
                        total_lines=0,
                        found=True,
                        truncated=False,
                    )

        # Determine identifier type
        id_type, resolved_path, error = self._resolve_identifier_type(
            identifier, repo_name
        )

        if error and id_type == "unknown":
            # Return appropriate error type
            return FileContent(
                identifier=identifier,
                file_path="",
                content="",
                total_chars=0,
                total_lines=0,
                found=False,
                error_message=error,
            )

        if id_type == "file":
            # Route to file reading
            result = await self.read_file(
                identifier=identifier,
                pattern=pattern,
                match_mode=match_mode,
                max_chars=max_chars,
                context_lines=context_lines,
                repo_name=repo_name,
            )
            # Record successful full reads for dedup
            if (
                match_mode == "full"
                and not pattern
                and isinstance(result, FileContent)
                and result.found
                and resolved_path
            ):
                cache_key = str(resolved_path)
                summary = f"{result.total_lines} lines, {result.total_chars} chars"
                self._recent_reads[cache_key] = summary
            return result

        elif id_type == "folder":
            if not pattern:
                # List folder structure
                try:
                    folder_rel = resolved_path.relative_to(self.project_root)
                except ValueError:
                    folder_rel = resolved_path

                tree = self._list_folder_structure(resolved_path)
                return FolderSearchResult(
                    identifier=identifier,
                    folder_path=str(folder_rel),
                    pattern=None,
                    directory_tree=tree,
                    found=True,
                )
            else:
                # Search folder
                search_mode = "regex" if match_mode == "regex" else "literal"
                return await self.search_folder(
                    folder_path=resolved_path,
                    pattern=pattern,
                    match_mode=search_mode,
                    include_extensions=include_extensions,
                    exclude_patterns=exclude_patterns,
                    max_total_chars=max_chars,
                    max_files=max_files,
                    max_matches_per_file=max_matches_per_file,
                    preferred_context_lines=context_lines,
                    min_context_lines=1,
                )

        # Fallback (shouldn't reach here)
        return FileContent(
            identifier=identifier,
            file_path="",
            content="",
            total_chars=0,
            total_lines=0,
            found=False,
            error_message=f"Could not determine type of: {identifier}",
        )


# =============================================================================
# CodeExplorer - Comprehensive code exploration with dependency analysis
# =============================================================================


class CodeExplorer:
    """Service for comprehensive code exploration."""

    def __init__(
        self,
        ingestor: MemgraphIngestor,
        project_name: str,
        project_root: str | None = None,
    ):
        self.ingestor = ingestor
        self.project_name = project_name
        self.project_root = project_root
        self.query_tools = GraphQueryTools(ingestor, project_name)

    def explore_code_context(
        self,
        identifier: str,
        include_code: bool = True,
        include_callers: bool = True,
        include_called: bool = True,
        include_imports: bool = True,
        max_results_per_category: int = 30,
        call_depth: int = 2,
        include_statistics: bool = True,
    ) -> CodeContext:
        """
        Get comprehensive context about a code element.

        This combines multiple queries to provide a full picture:
        - Basic definition information
        - Source code (if available)
        - What calls this element
        - What this element calls
        - Import relationships
        - Inheritance (for classes)
        - Children (for classes/modules)
        """
        logger.info(f"[CodeExplorer] Exploring context for: {identifier}")

        context = CodeContext(
            qualified_name=identifier,
            element_type="Unknown",
            summary=f"Context exploration for: {identifier}",
        )

        # Step 1: Find the element
        find_result = self.query_tools.find_nodes(identifier, search_strategy="auto")

        if not find_result.success or not find_result.results:
            # Try as file path - use find_nodes with File type filter
            file_result = self.query_tools.find_nodes(
                query=identifier, search_strategy="pattern", node_type="File"
            )
            if file_result.success and file_result.results:
                file_info = file_result.results[0]
                context.qualified_name = file_info.get("path", identifier)
                context.element_type = "File"
                context.definition = file_info

                children_result = self.query_tools.get_children(
                    identifier=identifier, identifier_type="file", depth=2
                )
                if children_result.success:
                    context.children = children_result.results[
                        :max_results_per_category
                    ]

                context.summary = f"Found file: {identifier}. Contains {len(context.children)} elements."
                return context

            # Try name pattern search
            pattern_result = self.query_tools.find_nodes(
                query=identifier.split(".")[-1],
                search_strategy="pattern",
                node_type=None,
            )
            if pattern_result.success and pattern_result.results:
                match = pattern_result.results[0]
                identifier = match.get("qualified_name", identifier)
                find_result = self.query_tools.find_nodes(
                    identifier, search_strategy="exact"
                )

        if not find_result.success or not find_result.results:
            context.summary = f"Element not found: {identifier}"
            return context

        # Extract element info
        element_info = find_result.results[0]
        context.qualified_name = element_info.get("qualified_name", identifier)
        context.element_type = (
            element_info.get("type", ["Unknown"])[0]
            if isinstance(element_info.get("type"), list)
            else "Unknown"
        )
        context.definition = element_info

        context.docstring = element_info.get("docstring")
        context.decorators = (
            element_info.get("decorators", []) if element_info.get("decorators") else []
        )
        if isinstance(context.decorators, str):
            context.decorators = [context.decorators]

        if element_info.get("start_line") is not None:
            context.line_range["start_line"] = element_info.get("start_line")
        if element_info.get("end_line") is not None:
            context.line_range["end_line"] = element_info.get("end_line")

        if element_info.get("parameters"):
            context.parameters = element_info.get("parameters", [])
        if element_info.get("return_type"):
            context.return_type = element_info.get("return_type")

        # Step 2: Find containing file
        try:
            containing_query = """
            MATCH (f:File)-[:DEFINES]->(n)
            WHERE n.qualified_name = $qn
            RETURN f.qualified_name AS qualified_name,
                   f.name AS name,
                   f.path AS path,
                   labels(f) AS type
            LIMIT 1
            """
            containing_results = self.ingestor.fetch_all(
                containing_query, {"qn": context.qualified_name}
            )
            if containing_results:
                context.defined_in = containing_results[0]
        except Exception as e:
            logger.debug(f"Could not find containing file: {e}")

        # Step 3: Retrieve source code for the main element
        context.code_snippet = None
        if self.project_root:
            file_path = element_info.get("path") or (context.defined_in or {}).get(
                "path"
            )
            start_line = context.line_range.get("start_line")
            end_line = context.line_range.get("end_line")
            if file_path and start_line and end_line:
                try:
                    full_path = Path(self.project_root) / file_path
                    if full_path.exists():
                        lines = full_path.read_text(
                            encoding="utf-8", errors="replace"
                        ).splitlines()
                        context.code_snippet = "\n".join(
                            lines[max(0, start_line - 1) : end_line]
                        )
                except Exception as e:
                    logger.debug(f"Could not read source code: {e}")

        # Step 4: Find callers
        if include_callers and context.element_type in ["Function", "Method"]:
            callers_result = self.query_tools.find_calls(
                qualified_name=context.qualified_name,
                direction="incoming",
                depth=call_depth,
            )
            if callers_result.success:
                context.callers = sorted(
                    callers_result.results[:max_results_per_category],
                    key=lambda x: x.get("qualified_name", ""),
                )

        # Step 5: Find what this calls
        if include_called and context.element_type in ["Function", "Method"]:
            called_result = self.query_tools.find_calls(
                qualified_name=context.qualified_name,
                direction="outgoing",
                depth=call_depth,
            )
            if called_result.success:
                seen = set()
                unique_called = []
                for callee in called_result.results:
                    qn = callee.get("qualified_name")
                    if qn and qn not in seen:
                        seen.add(qn)
                        unique_called.append(callee)
                context.called = unique_called[:max_results_per_category]

        # Step 6: Find import relationships
        if include_imports:
            if context.element_type in ["File"]:
                imports_result = self.query_tools.find_module_imports(
                    module_path_or_qn=identifier
                )
                if imports_result.success:
                    context.imports = imports_result.results[:max_results_per_category]

            elif context.element_type in ["Function", "Method"]:
                if context.defined_in:
                    module_qn = context.defined_in.get("qualified_name")
                    if module_qn:
                        imports_result = self.query_tools.find_module_imports(
                            module_path_or_qn=module_qn
                        )
                        if imports_result.success:
                            context.imports = imports_result.results[
                                :max_results_per_category
                            ]

            if context.element_type in ["File"]:
                reverse_imports_query = """
                MATCH (f:File)-[:IMPORTS]->(target:File)
                WHERE target.qualified_name = $qn OR target.path = $path
                RETURN DISTINCT f.qualified_name AS qualified_name,
                       f.name AS name,
                       f.path AS path
                LIMIT $limit
                """
                try:
                    reverse_results = self.ingestor.fetch_all(
                        reverse_imports_query,
                        {
                            "qn": context.qualified_name,
                            "path": context.definition.get("path", ""),
                            "limit": max_results_per_category,
                        },
                    )
                    context.imported_by = reverse_results
                except Exception as e:
                    logger.warning(f"Failed to find reverse imports: {e}")

        # Step 7: Get children
        if context.element_type in ["Class", "File"]:
            children_result = self.query_tools.get_children(
                identifier=context.qualified_name, identifier_type="auto", depth=2
            )
            if children_result.success:
                context.children = children_result.results[:max_results_per_category]

        # Step 8: Get inheritance
        if context.element_type == "Class":
            hierarchy_result = self.query_tools.find_class_hierarchy(
                class_qualified_name=context.qualified_name
            )
            if hierarchy_result.success and hierarchy_result.results:
                hierarchy_info = hierarchy_result.results[0]
                context.inheritance = {
                    "parents": hierarchy_info.get("parents", []),
                    "children": hierarchy_info.get("children", []),
                }

        # Step 9: Calculate statistics
        if include_statistics:
            stats = {}
            stats["total_callers"] = len(context.callers)
            stats["total_called"] = len(context.called)
            stats["total_imports"] = len(context.imports)
            stats["total_imported_by"] = len(context.imported_by)
            stats["total_children"] = len(context.children)

            if context.element_type == "Class":
                methods = [
                    c
                    for c in context.children
                    if isinstance(c.get("type"), list) and "Method" in c.get("type", [])
                ]
                attributes = [
                    c
                    for c in context.children
                    if isinstance(c.get("type"), list)
                    and "Attribute" in c.get("type", [])
                ]
                stats["methods_count"] = len(methods)
                stats["attributes_count"] = len(attributes)

            context.statistics = stats

        # Build summary
        parts = [f"Explored {context.element_type}: {context.qualified_name}"]

        if context.line_range.get("start_line"):
            parts.append(
                f"lines {context.line_range['start_line']}-{context.line_range.get('end_line', '?')}"
            )
        if context.decorators:
            parts.append(f"decorators: {', '.join(context.decorators)}")
        if context.parameters:
            parts.append(f"{len(context.parameters)} parameters")
        if context.return_type:
            parts.append(f"returns: {context.return_type}")
        if context.callers:
            parts.append(f"{len(context.callers)} caller(s)")
        if context.called:
            parts.append(f"calls {len(context.called)} function(s)")
        if context.imports:
            parts.append(f"imports {len(context.imports)} module(s)")
        if context.imported_by:
            parts.append(f"imported by {len(context.imported_by)} module(s)")
        if context.children:
            parts.append(f"defines {len(context.children)} element(s)")
        if context.inheritance.get("parents") or context.inheritance.get("children"):
            parent_count = len(context.inheritance.get("parents", []))
            child_count = len(context.inheritance.get("children", []))
            inheritance_parts = []
            if parent_count:
                inheritance_parts.append(f"{parent_count} parent(s)")
            if child_count:
                inheritance_parts.append(f"{child_count} child class(es)")
            parts.append(f"inheritance: {', '.join(inheritance_parts)}")

        context.summary = ". ".join(parts) + "."

        logger.info(f"[CodeExplorer] Context exploration complete: {context.summary}")
        return context

    def trace_dependency_chain(
        self,
        from_qualified_name: str,
        to_qualified_name: str | None = None,
        max_depth: int = 5,
        relationship_type: str = "CALLS",
        detect_circular: bool = True,
    ) -> DependencyChain:
        """
        Trace a dependency chain between code elements.
        """
        logger.info(
            f"[CodeExplorer] Tracing {relationship_type} chain from {from_qualified_name}"
        )

        circular_paths = []
        has_circular = False

        if detect_circular:
            try:
                circular_query = f"""
                MATCH path = (start)-[:{relationship_type}*2..{max_depth}]->(start)
                WHERE start.qualified_name = $from_qn
                WITH path,
                     [node in nodes(path) | node.qualified_name] AS node_names,
                     length(path) AS path_length
                RETURN DISTINCT node_names, path_length
                ORDER BY path_length
                LIMIT 10
                """
                circular_results = self.ingestor.fetch_all(
                    circular_query, {"from_qn": from_qualified_name}
                )

                if circular_results:
                    has_circular = True
                    for result in circular_results:
                        node_names = result.get("node_names", [])
                        if node_names:
                            circular_paths.append(node_names)
                    logger.warning(
                        f"[CodeExplorer] Found {len(circular_paths)} circular dependency path(s)"
                    )
            except Exception as e:
                logger.debug(f"Circular dependency check failed: {e}")

        if to_qualified_name:
            path_query = f"""
            MATCH path = (start)-[:{relationship_type}*1..{max_depth}]->(end)
            WHERE start.qualified_name = $from_qn
              AND end.qualified_name = $to_qn
            WITH path,
                 [node in nodes(path) | node.qualified_name] AS node_names,
                 [rel in relationships(path) | type(rel)] AS rel_types,
                 length(path) AS path_length
            RETURN node_names, rel_types, path_length
            ORDER BY path_length
            LIMIT 1
            """
            try:
                results = self.ingestor.fetch_all(
                    path_query,
                    {"from_qn": from_qualified_name, "to_qn": to_qualified_name},
                )

                chain = []
                if results:
                    result = results[0]
                    node_names = result.get("node_names", [])
                    rel_types = result.get("rel_types", [])

                    for i in range(len(node_names) - 1):
                        chain.append(
                            {
                                "from": node_names[i],
                                "to": node_names[i + 1],
                                "type": rel_types[i]
                                if i < len(rel_types)
                                else relationship_type,
                                "step": i + 1,
                            }
                        )

                    if not chain:
                        chain = [
                            {
                                "from": from_qualified_name,
                                "to": to_qualified_name,
                                "type": relationship_type,
                                "step": 1,
                            }
                        ]

                summary = f"Found path from {from_qualified_name} to {to_qualified_name} via {relationship_type} (length: {len(chain)})"
                if has_circular:
                    summary += f". WARNING: {len(circular_paths)} circular dependency path(s) detected!"

                return DependencyChain(
                    chain=chain,
                    depth=len(chain),
                    total_elements=len(chain) + 1,
                    summary=summary,
                    has_circular=has_circular,
                    circular_paths=circular_paths,
                )
            except Exception as e:
                logger.error(f"Error tracing path: {e}")
                return DependencyChain(
                    chain=[],
                    depth=0,
                    total_elements=0,
                    summary=f"Error: {e}",
                    has_circular=has_circular,
                    circular_paths=circular_paths,
                )
        else:
            reachable_query = f"""
            MATCH path = (start)-[:{relationship_type}*1..{max_depth}]->(target)
            WHERE start.qualified_name = $from_qn
            RETURN DISTINCT target.qualified_name AS qualified_name,
                   target.name AS name,
                   labels(target) AS type,
                   length(path) AS depth
            ORDER BY depth, target.name
            LIMIT 100
            """
            try:
                results = self.ingestor.fetch_all(
                    reachable_query, {"from_qn": from_qualified_name}
                )

                chain = []
                for result in results:
                    chain.append(
                        {
                            "qualified_name": result.get("qualified_name"),
                            "name": result.get("name"),
                            "type": result.get("type"),
                            "depth": result.get("depth", 1),
                        }
                    )

                summary = f"Found {len(chain)} elements reachable from {from_qualified_name} via {relationship_type} (max depth: {max_depth})"
                if has_circular:
                    summary += f". WARNING: {len(circular_paths)} circular dependency path(s) detected!"

                return DependencyChain(
                    chain=chain,
                    depth=max_depth,
                    total_elements=len(chain),
                    summary=summary,
                    has_circular=has_circular,
                    circular_paths=circular_paths,
                )
            except Exception as e:
                logger.error(f"Error tracing reachable elements: {e}")
                return DependencyChain(
                    chain=[],
                    depth=0,
                    total_elements=0,
                    summary=f"Error: {e}",
                    has_circular=has_circular,
                    circular_paths=circular_paths,
                )

    def build_dependency_tree(
        self,
        from_qualified_name: str,
        max_depth: int = 5,
        relationship_type: str = "CALLS",
        detect_circular: bool = True,
    ) -> DependencyTree:
        """
        Build a tree structure of all dependencies from a starting node.
        """
        logger.info(
            f"[CodeExplorer] Building {relationship_type} tree from {from_qualified_name}"
        )

        circular_paths = []
        has_circular = False

        if detect_circular:
            try:
                effective_rel_type = relationship_type
                if relationship_type == "CALLS":
                    effective_rel_type = "CALLS|BINDS_TO"

                circular_query = f"""
                MATCH path = (start)-[:{effective_rel_type}*2..{max_depth + 1}]->(start)
                WHERE start.qualified_name = $from_qn
                WITH [node in nodes(path) | node.qualified_name] AS node_names
                RETURN DISTINCT node_names
                LIMIT 10
                """
                circular_results = self.ingestor.fetch_all(
                    circular_query, {"from_qn": from_qualified_name}
                )

                if circular_results:
                    has_circular = True
                    for result in circular_results:
                        node_names = result.get("node_names", [])
                        if node_names:
                            circular_paths.append(node_names)
            except Exception as e:
                logger.debug(f"Circular dependency check failed: {e}")

        tree = {"name": from_qualified_name, "children": [], "depth": 0}
        visited = {from_qualified_name}
        total_nodes = 1
        actual_max_depth = 0

        def build_subtree(node_qn: str, current_depth: int, parent_node: dict):
            nonlocal total_nodes, actual_max_depth

            if current_depth >= max_depth:
                return

            effective_rel_type = relationship_type
            if relationship_type == "CALLS":
                effective_rel_type = "CALLS|BINDS_TO"

            query = f"""
            MATCH (parent)-[r:{effective_rel_type}]->(child)
            WHERE parent.qualified_name = $parent_qn
            RETURN DISTINCT child.qualified_name AS qualified_name,
                   child.name AS name,
                   labels(child) AS type,
                   type(r) AS rel_type
            LIMIT 50
            """

            try:
                results = self.ingestor.fetch_all(query, {"parent_qn": node_qn})

                for result in results:
                    child_qn = result.get("qualified_name")
                    if not child_qn:
                        continue

                    child_name = result.get("name", child_qn.split(".")[-1])
                    child_type = result.get("type", ["Unknown"])
                    if isinstance(child_type, list):
                        child_type = child_type[0] if child_type else "Unknown"

                    rel_type = result.get("rel_type", relationship_type)
                    is_circular = child_qn in visited

                    child_node = {
                        "name": child_qn,
                        "display_name": child_name,
                        "type": child_type,
                        "rel_type": rel_type,
                        "depth": current_depth + 1,
                        "children": [],
                        "is_circular": is_circular,
                    }

                    parent_node["children"].append(child_node)
                    total_nodes += 1
                    actual_max_depth = max(actual_max_depth, current_depth + 1)

                    if not is_circular:
                        visited.add(child_qn)
                        build_subtree(child_qn, current_depth + 1, child_node)

            except Exception as e:
                logger.debug(f"Error building subtree for {node_qn}: {e}")

        try:
            build_subtree(from_qualified_name, 0, tree)
        except Exception as e:
            logger.error(f"Error building dependency tree: {e}")

        summary = f"Built {relationship_type} tree from {from_qualified_name}: {total_nodes} nodes, max depth {actual_max_depth}"
        if has_circular:
            summary += f". WARNING: {len(circular_paths)} circular dependency path(s) detected!"

        return DependencyTree(
            root=from_qualified_name,
            relationship_type=relationship_type,
            tree=tree,
            total_nodes=total_nodes,
            max_depth=actual_max_depth,
            summary=summary,
            has_circular=has_circular,
            circular_paths=circular_paths,
        )


# =============================================================================
# Tool Factory Functions
# =============================================================================


class CodeSnippetInput(BaseModel):
    """Input schema for get_code_snippet tool."""

    qualified_name: str = Field(
        description="The fully qualified name of the code element"
    )
    repo_name: str | None = Field(
        default=None, description="Repository name for cross-repo queries"
    )


def create_code_retrieval_tool(code_retriever: CodeRetriever) -> BaseTool:
    """Factory function to create the code snippet retrieval tool."""

    async def get_code_snippet(
        qualified_name: str, repo_name: str | None = None
    ) -> CodeSnippet:
        """Retrieves the source code for a given qualified name."""
        logger.info(
            f"[Tool:GetCode] Retrieving code for: {qualified_name} (repo: {repo_name})"
        )
        return await code_retriever.find_code_snippet(qualified_name, repo_name)

    return StructuredTool.from_function(
        coroutine=get_code_snippet,
        name="get_code_snippet",
        description=TOOL_DESCRIPTIONS["get_code_snippet"],
        args_schema=CodeSnippetInput,
    )


class ReadInput(BaseModel):
    """Input schema for unified read tool (files and folders)."""

    identifier: str = Field(
        description="File path, folder path (relative to project root), or qualified_name. "
        "For folders, provide pattern to search; without pattern returns directory structure."
    )
    pattern: str | None = Field(
        default=None,
        description="Pattern to search. Required for folder search. "
        "For files, optional (omit to read entire file).",
    )
    match_mode: Literal["full", "regex", "literal"] = Field(
        default="full",
        description="'full': read entire file (files only), 'regex': regex search, 'literal': literal string search",
    )
    max_chars: int = Field(
        default=20000,
        ge=1000,
        le=100000,
        description="Maximum characters to return (default: 20000)",
    )
    context_lines: int = Field(
        default=5,
        ge=0,
        le=20,
        description="Lines of context around each match (default: 5)",
    )
    repo_name: str | None = Field(
        default=None, description="Repository name for cross-repo access"
    )
    # Folder-specific options
    include_extensions: list[str] | None = Field(
        default=None,
        description="For folders: filter by extensions, e.g., ['.py', '.ts']. None means all files.",
    )
    exclude_patterns: list[str] | None = Field(
        default=None,
        description="For folders: additional regex patterns to exclude files/paths",
    )
    max_files: int = Field(
        default=50,
        ge=1,
        le=200,
        description="For folders: maximum number of files to search (default: 50)",
    )
    max_matches_per_file: int = Field(
        default=10,
        ge=1,
        le=50,
        description="For folders: maximum matches per file (default: 10)",
    )


# Keep old name for backward compatibility
ReadFileInput = ReadInput


def create_file_reader_tool(file_reader: FileReader) -> BaseTool:
    """Factory function to create the unified file/folder reader tool."""

    async def read(
        identifier: str,
        pattern: str | None = None,
        match_mode: Literal["full", "regex", "literal"] = "full",
        max_chars: int = 20000,
        context_lines: int = 5,
        repo_name: str | None = None,
        include_extensions: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        max_files: int = 50,
        max_matches_per_file: int = 10,
    ) -> str:
        """
        Read file/folder content with optional pattern matching.

        This tool supports:
        - Reading single files (full content or with pattern search)
        - Listing folder structure (when no pattern provided)
        - Searching folders recursively (with pattern)

        Args:
            identifier: File path, folder path, or qualified_name
            pattern: Optional pattern (required for folder search)
            match_mode: "full" (files only), "regex", or "literal"
            max_chars: Maximum characters to return
            context_lines: Lines of context around matches
            repo_name: Repository name for cross-repo access
            include_extensions: For folders, filter by extensions
            exclude_patterns: For folders, additional exclude patterns
            max_files: For folders, max files to search
            max_matches_per_file: For folders, max matches per file

        Returns:
            Formatted string with content or search results
        """
        logger.info(
            f"[Tool:Read] Reading: {identifier} (mode: {match_mode}, pattern: {pattern})"
        )

        result = await file_reader.read(
            identifier=identifier,
            pattern=pattern,
            match_mode=match_mode,
            max_chars=max_chars,
            context_lines=context_lines,
            repo_name=repo_name,
            include_extensions=include_extensions,
            exclude_patterns=exclude_patterns,
            max_files=max_files,
            max_matches_per_file=max_matches_per_file,
        )

        # Handle FolderSearchResult
        if isinstance(result, FolderSearchResult):
            return result.format_output()

        # Handle FileContent
        if not result.found:
            return f"Error: {result.error_message}"

        # Format file output
        output_parts = [f"File: {result.file_path}"]
        output_parts.append(
            f"Total: {result.total_lines} lines, {result.total_chars} chars"
        )

        if result.match_count > 0:
            output_parts.append(f"Matches: {result.match_count} found")

        if result.truncated:
            output_parts.append("(Content truncated)")

        output_parts.append("")
        output_parts.append(result.content)

        return "\n".join(output_parts)

    return StructuredTool.from_function(
        coroutine=read,
        name="read",
        description=TOOL_DESCRIPTIONS["read"],
        args_schema=ReadInput,
    )


class ExploreCodeInput(BaseModel):
    """Input schema for explore_code tool (unified name)."""

    identifier: str = Field(description="Qualified name of the code element")
    repo_name: str | None = Field(
        default=None, description="Repository name for cross-repo queries"
    )
    max_dependency_depth: int = Field(
        default=5, ge=1, le=10, description="Dependency tree depth (1-10)"
    )
    include_dependency_source_code: bool = Field(
        default=True, description="Fetch source code for dependencies"
    )
    max_dependencies_with_code: int = Field(
        default=10, ge=1, le=50, description="Max dependencies to fetch source for"
    )


def create_code_explorer_tool(
    ingestor: MemgraphIngestor, project_name: str, project_root: str | None = None
) -> BaseTool:
    """Create a single comprehensive code exploration tool."""
    explorer = CodeExplorer(ingestor, project_name, project_root)

    async def explore_code(
        identifier: str,
        repo_name: str | None = None,
        max_dependency_depth: int = 5,
        include_dependency_source_code: bool = True,
        max_dependencies_with_code: int = 10,
    ) -> str:
        """Comprehensive code exploration with full context."""
        logger.info(
            f"[explore_code] Starting exploration for: {identifier} (repo: {repo_name})"
        )

        search_identifier = identifier
        if repo_name and not identifier.startswith(f"{repo_name}."):
            search_identifier = f"{repo_name}.{identifier}"

        exact_result = explorer.query_tools.find_nodes(
            search_identifier, search_strategy="auto"
        )

        # If exact match failed, try fuzzy search
        if not exact_result.success or not exact_result.results:
            basename = (
                search_identifier.split(".")[-1]
                if "." in search_identifier
                else search_identifier
            )
            pattern_result = explorer.query_tools.find_nodes(
                query=basename, search_strategy="pattern", case_sensitive=False
            )

            if not pattern_result.success or not pattern_result.results:
                return f"No elements found for: '{search_identifier}'\nTip: For cross-repo queries, include repo prefix or use repo_name parameter."

            # UX IMPROVEMENT: If only 1 similar result found, automatically use it
            if pattern_result.count == 1:
                similar_item = pattern_result.results[0]
                similar_qn = similar_item.get("qualified_name", "N/A")
                logger.info(
                    f"[explore_code] Auto-selected single similar result: {similar_qn} (input was: {identifier})"
                )
                # Update identifier to the matched qualified_name and continue
                search_identifier = similar_qn
                exact_result = pattern_result
            else:
                # Multiple results - ask user to choose
                output_parts = [f"Found {pattern_result.count} similar elements:"]
                by_type = {}
                for item in pattern_result.results:
                    item_type = item.get("type", "Unknown")
                    if isinstance(item_type, list):
                        item_type = item_type[0] if item_type else "Unknown"
                    if item_type not in by_type:
                        by_type[item_type] = []
                    by_type[item_type].append(item)

                for item_type, items in sorted(by_type.items()):
                    output_parts.append(f"\n{item_type}:")
                    for item in items[:10]:
                        qn = item.get("qualified_name", "N/A")
                        output_parts.append(f"  - {qn}")
                    if len(items) > 10:
                        output_parts.append(f"  ... and {len(items) - 10} more")

                output_parts.append(
                    "\nCall this tool again with an exact qualified name from above."
                )
                return "\n".join(output_parts)

        target_element = exact_result.results[0]
        target_qn = target_element.get("qualified_name")

        context = explorer.explore_code_context(
            identifier=target_qn,
            include_code=False,
            include_callers=True,
            include_called=True,
            include_imports=True,
            call_depth=1,
            include_statistics=True,
        )

        if explorer.project_root:
            try:
                retriever = CodeRetriever(
                    explorer.project_root, explorer.ingestor, explorer.project_name
                )
                code_snippet = await retriever.find_code_snippet(context.qualified_name)
                if code_snippet.found:
                    context.code_snippet = code_snippet.source_code
            except Exception as e:
                logger.warning(f"Failed to get code snippet: {e}")

        dependency_tree = None
        visited_in_tree = []

        if context.element_type in ["Function", "Method", "File", "Class"]:
            try:
                dependency_tree = explorer.build_dependency_tree(
                    from_qualified_name=context.qualified_name,
                    max_depth=max_dependency_depth,
                    relationship_type="CALLS",
                    detect_circular=True,
                )

                def collect_nodes(
                    node: dict[str, Any], visited: list[dict[str, Any]]
                ) -> None:
                    qn = node.get("qualified_name") or node.get("name")
                    if qn:
                        if not any(v.get("qualified_name") == qn for v in visited):
                            visited.append(
                                {
                                    "name": node.get("name"),
                                    "qualified_name": qn,
                                    "display_name": node.get("display_name"),
                                    "type": node.get("type"),
                                }
                            )
                        for child in node.get("children", []):
                            if not child.get("is_circular", False):
                                collect_nodes(child, visited)

                if dependency_tree:
                    collect_nodes(dependency_tree.tree, visited_in_tree)

            except Exception as e:
                logger.warning(f"Failed to build dependency tree: {e}")

        output_parts = []
        output_parts.append(f"{context.qualified_name}")
        output_parts.append(f"Type: {context.element_type}")

        if context.defined_in:
            file_path = context.defined_in.get(
                "path", context.defined_in.get("qualified_name", "N/A")
            )
            output_parts.append(f"File: {file_path}")

        if context.line_range.get("start_line"):
            line_info = f"Lines: {context.line_range['start_line']}"
            if context.line_range.get("end_line"):
                line_info += f"-{context.line_range['end_line']}"
            output_parts.append(line_info)

        module_context = None
        if context.defined_in:
            try:
                file_qn = context.defined_in.get("qualified_name")
                if file_qn:
                    module_context_query = """
                    MATCH (f:File {qualified_name: $file_qn})
                    RETURN f.module_context AS module_context
                    LIMIT 1
                    """
                    module_results = explorer.ingestor.fetch_all(
                        module_context_query, {"file_qn": file_qn}
                    )
                    if module_results and module_results[0].get("module_context"):
                        module_context = module_results[0]["module_context"]
            except Exception as e:
                logger.debug(f"Failed to fetch module_context: {e}")

        if module_context and module_context.strip():
            output_parts.append("\nModule Context (imports & configurations):")
            output_parts.append("```python")
            output_parts.append(module_context)
            output_parts.append("```")

        if context.decorators:
            output_parts.append(f"Decorators: {', '.join(context.decorators)}")

        if context.parameters:
            param_list = []
            for param in context.parameters[:10]:
                param_str = param.get("name", "N/A")
                if param.get("type"):
                    param_str += f": {param.get('type')}"
                param_list.append(param_str)
            output_parts.append(f"Parameters: {', '.join(param_list)}")
            if len(context.parameters) > 10:
                output_parts.append(f"  ... and {len(context.parameters) - 10} more")

        if context.return_type:
            output_parts.append(f"Returns: {context.return_type}")

        if context.docstring:
            doc_preview = context.docstring[:300].replace("\n", " ")
            if len(context.docstring) > 300:
                doc_preview += "..."
            output_parts.append(f"Docstring: {doc_preview}")

        if context.element_type in ["Function", "Method"]:
            try:
                callers_result = explorer.query_tools.find_calls(
                    context.qualified_name, direction="incoming", depth=1
                )

                if callers_result.success and callers_result.results:
                    caller_names = [
                        caller.get("qualified_name", "").split(".")[-1]
                        for caller in callers_result.results[:10]
                    ]
                    output_parts.append(
                        f"\nCalled by ({len(callers_result.results)} callers): {', '.join(caller_names)}"
                    )
                    if len(callers_result.results) > 10:
                        output_parts.append(
                            f"  ... and {len(callers_result.results) - 10} more"
                        )
                else:
                    output_parts.append(
                        "\nCalled by: None (this is likely an entry point or unused)"
                    )
            except Exception as e:
                logger.debug(f"Failed to fetch caller count: {e}")

        if context.code_snippet:
            output_parts.append("\nSource Code:")
            code_lines = context.code_snippet.split("\n")
            start_line = context.line_range.get("start_line", 1)
            if len(code_lines) > 100:
                # Truncate: keep first 50 + last 30 lines
                head = code_lines[:50]
                tail = code_lines[-30:]
                truncated_count = len(code_lines) - 80
                for i, line in enumerate(head):
                    output_parts.append(f"{start_line + i:4d} {line}")
                output_parts.append(f"     ... ({truncated_count} lines truncated) ...")
                tail_start = start_line + len(code_lines) - 30
                for i, line in enumerate(tail):
                    output_parts.append(f"{tail_start + i:4d} {line}")
            else:
                for i, line in enumerate(code_lines, start=0):
                    line_num = start_line + i
                    output_parts.append(f"{line_num:4d} {line}")

        if dependency_tree and dependency_tree.total_nodes > 1:
            output_parts.append(
                f"\nDependency Tree (calls {dependency_tree.total_nodes - 1} functions):"
            )

            def format_tree_node(
                node: dict[str, Any],
                prefix: str = "",
                is_last: bool = True,
                current_depth: int = 0,
            ) -> list[str]:
                lines = []
                connector = "└── " if is_last else "├── "
                node_name = node.get("display_name", node.get("name", "Unknown"))
                rel_type = node.get("rel_type")
                is_circular = node.get("is_circular", False)

                binding_indicator = " [C++ Binding]" if rel_type == "BINDS_TO" else ""

                node_line = f"{prefix}{connector}{node_name}{binding_indicator}"
                if is_circular:
                    node_line += " [circular]"
                lines.append(node_line)

                if not is_circular and current_depth < max_dependency_depth:
                    children = node.get("children", [])
                    for i, child in enumerate(children):
                        is_last_child = i == len(children) - 1
                        child_prefix = prefix + ("    " if is_last else "│   ")
                        lines.extend(
                            format_tree_node(
                                child, child_prefix, is_last_child, current_depth + 1
                            )
                        )

                return lines

            root_node = dependency_tree.tree
            children = root_node.get("children", [])
            for i, child in enumerate(children):
                is_last_child = i == len(children) - 1
                output_parts.extend(format_tree_node(child, "", is_last_child, 0))

        if context.element_type in ["Function", "Method"] and explorer.project_root:
            try:
                callers_result = explorer.query_tools.find_calls(
                    context.qualified_name, direction="incoming", depth=1
                )

                if callers_result.success and callers_result.results:
                    output_parts.append(
                        f"\nCallers ({len(callers_result.results)} functions call this):"
                    )

                    retriever = CodeRetriever(
                        explorer.project_root, explorer.ingestor, explorer.project_name
                    )

                    max_callers_to_show = 2
                    for i, caller in enumerate(
                        callers_result.results[:max_callers_to_show]
                    ):
                        caller_qn = caller.get("qualified_name", "")
                        caller_path = caller.get("path", "")

                        if caller_qn:
                            output_parts.append(f"\n{i + 1}. {caller_qn}")
                            if caller_path:
                                output_parts.append(f"   File: {caller_path}")

                            try:
                                caller_snippet = await retriever.find_code_snippet(
                                    caller_qn
                                )
                                if caller_snippet.found and caller_snippet.source_code:
                                    caller_lines = caller_snippet.source_code.split(
                                        "\n"
                                    )
                                    caller_start = caller_snippet.line_start or 1

                                    max_caller_lines = 50
                                    output_parts.append("   Source:")
                                    for j, line in enumerate(
                                        caller_lines[:max_caller_lines], start=0
                                    ):
                                        line_num = caller_start + j
                                        output_parts.append(f"   {line_num:4d} {line}")
                                    if len(caller_lines) > max_caller_lines:
                                        output_parts.append(
                                            f"   ... ({len(caller_lines) - max_caller_lines} more lines)"
                                        )
                            except Exception as e:
                                logger.debug(
                                    f"Failed to fetch source for caller {caller_qn}: {e}"
                                )

            except Exception as e:
                logger.warning(f"Failed to fetch callers: {e}")

        if include_dependency_source_code and visited_in_tree and explorer.project_root:
            output_parts.append("\nDependency Source Code:")

            try:
                retriever = CodeRetriever(
                    explorer.project_root, explorer.ingestor, explorer.project_name
                )

                dependency_qns = {
                    node["qualified_name"]
                    for node in visited_in_tree
                    if node.get("qualified_name")
                    and node.get("qualified_name") != context.qualified_name
                }

                for dep_qn in dependency_qns:
                    try:
                        dep_snippet = await retriever.find_code_snippet(dep_qn)
                        if dep_snippet.found and dep_snippet.source_code:
                            output_parts.append(f"\n{dep_qn}:")

                            dep_lines = dep_snippet.source_code.split("\n")
                            dep_start = dep_snippet.line_start or 1

                            max_lines = 60
                            lines_to_show = dep_lines[:max_lines]

                            for i, line in enumerate(lines_to_show, start=0):
                                line_num = dep_start + i
                                output_parts.append(f"{line_num:4d} {line}")

                            if len(dep_lines) > max_lines:
                                remaining = len(dep_lines) - max_lines
                                output_parts.append(f"... ({remaining} more lines)")

                    except Exception as e:
                        logger.debug(f"Failed to fetch source for {dep_qn}: {e}")

            except Exception as e:
                logger.warning(f"Failed to fetch dependency source code: {e}")

        # Final size guard: if total output exceeds 50000 chars, truncate
        # the largest source code sections to fit
        full_output = "\n".join(output_parts)
        if len(full_output) > 50000:
            # Re-truncate: keep relationship info, trim code sections
            trimmed_parts = []
            for part in output_parts:
                trimmed_parts.append(part)
            # Simple strategy: just hard-truncate with a note
            full_output = full_output[:50000]
            full_output += "\n... (output truncated to 50000 chars)"

        return full_output

    return StructuredTool.from_function(
        coroutine=explore_code,
        name="explore_code",
        description=TOOL_DESCRIPTIONS["explore_code"],
        args_schema=ExploreCodeInput,
    )


# =============================================================================
# Document Editing Tools
# =============================================================================


class ReadDocTraceInput(BaseModel):
    repo_name: str = Field(..., description="Repository name")
    section_id: str = Field(
        ...,
        description="Section filename stem, e.g. '001_核心架构与_hybridflow_设计'",
    )
    version: str = Field("latest", description="Version ID or 'latest'")


class ReadDocFileInput(BaseModel):
    file_path: str = Field(..., description="Absolute path to the .md file")


class EditDocFileInput(BaseModel):
    file_path: str = Field(..., description="Absolute path to the .md file")
    start_line: int = Field(..., description="Start line number (1-based, inclusive)")
    end_line: int = Field(
        ...,
        description="End line number (1-based, inclusive). -1 = end of file",
    )
    new_content: str = Field(
        ..., description="New content to replace the specified line range"
    )


def _resolve_version_path(repo_name: str, version: str) -> Path | None:
    """Resolve a version string to the actual version directory path."""
    from core.config import get_wiki_doc_dir

    wiki_doc_dir = get_wiki_doc_dir()
    repo_doc_path = wiki_doc_dir / repo_name

    if version == "latest":
        meta_path = repo_doc_path / "_meta.json"
        if not meta_path.exists():
            return None
        import json

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        version = meta.get("current_version", "")
        if not version:
            return None

    version_path = repo_doc_path / "versions" / version
    return version_path if version_path.exists() else None


def _find_section_file(version_path: Path, section_id: str) -> Path | None:
    """Find the section .md file matching the section_id prefix."""
    sections_dir = version_path / "sections"
    if not sections_dir.exists():
        return None
    # Try exact match first
    md_file = sections_dir / f"{section_id}.md"
    if md_file.exists():
        return md_file
    # Fuzzy match: find files starting with the section_id
    for f in sections_dir.glob("*.md"):
        if f.stem == section_id or f.stem.startswith(section_id):
            return f
    return None


def create_read_doc_trace_tool() -> BaseTool:
    """Create tool to read documentation generation trace."""

    async def read_doc_trace(
        repo_name: str,
        section_id: str,
        version: str = "latest",
    ) -> str:
        import json

        version_path = _resolve_version_path(repo_name, version)
        if not version_path:
            return f"Error: Could not resolve version '{version}' for repo '{repo_name}'"

        sections_dir = version_path / "sections"
        # Find the messages.json file
        msg_file = sections_dir / f"{section_id}.messages.json"
        if not msg_file.exists():
            # Try fuzzy match
            for f in sections_dir.glob("*.messages.json"):
                if f.stem.replace(".messages", "") == section_id or f.stem.startswith(
                    section_id
                ):
                    msg_file = f
                    break
            else:
                available = [
                    f.stem.replace(".messages", "")
                    for f in sections_dir.glob("*.messages.json")
                ]
                return (
                    f"Error: No trace found for section '{section_id}'.\n"
                    f"Available sections: {available}"
                )

        try:
            data = json.loads(msg_file.read_text(encoding="utf-8"))
        except Exception as e:
            return f"Error reading trace: {e}"

        # Build summary
        parts = []
        parts.append(f"## Section: {data.get('section_title', section_id)}")
        parts.append(f"Generated at: {data.get('generated_at', 'unknown')}")

        scope = data.get("scope", {})
        if scope:
            parts.append(f"\n### Scope")
            parts.append(f"Title: {scope.get('title', '')}")
            parts.append(f"Description: {scope.get('description', '')}")
            components = scope.get("key_components", [])
            if components:
                parts.append(f"Key components: {', '.join(components[:20])}")

        # Extract AI messages and tool calls (skip system messages)
        messages = data.get("messages", [])
        ai_count = 0
        tool_count = 0
        for msg in messages:
            msg_type = msg.get("type", "")
            msg_data = msg.get("data", {})
            content = msg_data.get("content", "")

            if msg_type == "ai" and content:
                ai_count += 1
                parts.append(f"\n### AI Analysis #{ai_count}")
                # Truncate long content
                if len(content) > 2000:
                    parts.append(content[:2000] + "\n... (truncated)")
                else:
                    parts.append(content)

                # Show tool calls
                tool_calls = msg_data.get("tool_calls", [])
                for tc in tool_calls:
                    tool_count += 1
                    args_summary = json.dumps(tc.get("args", {}), ensure_ascii=False)
                    if len(args_summary) > 200:
                        args_summary = args_summary[:200] + "..."
                    parts.append(f"  -> Tool: {tc.get('name', '?')}({args_summary})")

        parts.append(f"\nSummary: {ai_count} AI responses, {tool_count} tool calls")
        return "\n".join(parts)

    return StructuredTool.from_function(
        coroutine=read_doc_trace,
        name="read_doc_trace",
        description=TOOL_DESCRIPTIONS["read_doc_trace"],
        args_schema=ReadDocTraceInput,
    )


def create_read_doc_file_tool() -> BaseTool:
    """Create tool to read a documentation file with line numbers."""

    async def read_doc_file(file_path: str) -> str:
        import json as _json

        p = Path(file_path)
        if not p.exists():
            return f"Error: File not found: {file_path}"
        if not p.is_file():
            return f"Error: Not a file: {file_path}"

        try:
            raw = p.read_text(encoding="utf-8")
        except Exception as e:
            return f"Error reading file: {e}"

        # For legacy .json docs, extract the markdown field
        if p.suffix == ".json":
            try:
                data = _json.loads(raw)
                md = data.get("markdown", "")
                if not md:
                    return f"Error: No 'markdown' field in {file_path}"
                lines = md.split("\n")
                numbered = [f"{i:4d} | {line}" for i, line in enumerate(lines, 1)]
                return (
                    f"File: {file_path} (JSON doc, markdown: {len(lines)} lines)\n"
                    + "\n".join(numbered)
                )
            except _json.JSONDecodeError as e:
                return f"Error parsing JSON: {e}"

        # Normal .md file
        lines = raw.split("\n")
        numbered = [f"{i:4d} | {line}" for i, line in enumerate(lines, 1)]
        return f"File: {file_path} ({len(lines)} lines)\n" + "\n".join(numbered)

    return StructuredTool.from_function(
        coroutine=read_doc_file,
        name="read_doc_file",
        description=TOOL_DESCRIPTIONS["read_doc_file"],
        args_schema=ReadDocFileInput,
    )


def create_edit_doc_file_tool() -> BaseTool:
    """Create tool to edit a documentation file by line range."""

    async def edit_doc_file(
        file_path: str,
        start_line: int,
        end_line: int,
        new_content: str,
    ) -> str:
        import json as _json

        p = Path(file_path)
        if not p.exists():
            return f"Error: File not found: {file_path}"
        if p.suffix not in (".md", ".json"):
            return f"Error: Only .md and .json files can be edited (got {p.suffix})"

        try:
            raw = p.read_text(encoding="utf-8")
        except Exception as e:
            return f"Error reading file: {e}"

        # --- Legacy .json docs: edit the embedded markdown field ---
        if p.suffix == ".json":
            try:
                data = _json.loads(raw)
            except _json.JSONDecodeError as e:
                return f"Error parsing JSON: {e}"

            md = data.get("markdown", "")
            if not md:
                return f"Error: No 'markdown' field in {file_path}"

            lines = md.split("\n")
            total_lines = len(lines)

            if start_line < 1:
                return f"Error: start_line must be >= 1 (got {start_line})"
            if end_line == -1:
                end_line = total_lines
            if end_line < start_line:
                return f"Error: end_line ({end_line}) < start_line ({start_line})"
            if start_line > total_lines:
                return f"Error: start_line ({start_line}) exceeds markdown length ({total_lines})"

            new_lines = new_content.split("\n")
            result_lines = lines[: start_line - 1] + new_lines + lines[end_line:]
            data["markdown"] = "\n".join(result_lines)

            try:
                p.write_text(
                    _json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
                )
            except Exception as e:
                return f"Error writing file: {e}"

            replaced = end_line - start_line + 1
            return (
                f"Success: Edited JSON doc markdown, replaced lines {start_line}-{end_line} "
                f"({replaced} lines) with {len(new_lines)} lines. "
                f"New markdown has {len(result_lines)} lines.\nFile: {file_path}"
            )

        # --- Normal .md file ---
        lines = raw.split("\n")
        total_lines = len(lines)

        if start_line < 1:
            return f"Error: start_line must be >= 1 (got {start_line})"
        if end_line == -1:
            end_line = total_lines
        if end_line < start_line:
            return f"Error: end_line ({end_line}) < start_line ({start_line})"
        if start_line > total_lines:
            return f"Error: start_line ({start_line}) exceeds file length ({total_lines})"

        new_lines = new_content.split("\n")
        result_lines = lines[: start_line - 1] + new_lines + lines[end_line:]
        new_text = "\n".join(result_lines)

        try:
            p.write_text(new_text, encoding="utf-8")
        except Exception as e:
            return f"Error writing file: {e}"

        # Update _index.json right_nav headings if the file is in a versioned sections dir
        _update_index_headings(p)

        replaced = end_line - start_line + 1
        return (
            f"Success: Replaced lines {start_line}-{end_line} ({replaced} lines) "
            f"with {len(new_lines)} lines. New file has {len(result_lines)} lines.\n"
            f"File: {file_path}"
        )

    return StructuredTool.from_function(
        coroutine=edit_doc_file,
        name="edit_doc_file",
        description=TOOL_DESCRIPTIONS["edit_doc_file"],
        args_schema=EditDocFileInput,
    )


def _update_index_headings(md_file: Path) -> None:
    """Update _index.json right_nav headings after editing a section .md file."""
    import json

    # Expect: .../versions/{version_id}/sections/{section}.md
    sections_dir = md_file.parent
    if sections_dir.name != "sections":
        return
    version_dir = sections_dir.parent
    index_path = version_dir / "_index.json"
    if not index_path.exists():
        return

    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return

    # Extract headings from the edited markdown
    content = md_file.read_text(encoding="utf-8")
    headings = []
    for line in content.split("\n"):
        m = re.match(r"^(#{1,4})\s+(.+)$", line)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            anchor = re.sub(r"[^\w\s-]", "", title.lower())
            anchor = re.sub(r"[\s]+", "-", anchor).strip("-")
            headings.append({"name": title, "anchor": anchor, "depth": level - 1})

    # Find and update the matching section in right_nav
    section_stem = md_file.stem
    section_path = f"sections/{md_file.name}"

    # Update right_nav overview with new headings for this section
    right_nav = index.get("right_nav", {})
    if headings:
        right_nav["overview"] = headings
        index["right_nav"] = right_nav

    # Also update tree node name if the first heading changed
    tree = index.get("tree", [])
    for node in tree:
        if node.get("path") == section_path:
            if headings:
                node["name"] = headings[0]["name"]
            break

    try:
        index_path.write_text(
            json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:
        logger.warning(f"Failed to update _index.json: {e}")

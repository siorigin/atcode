# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
GitIgnore parser for filtering files during code analysis.

This module provides functionality to parse .gitignore files and check
if paths should be ignored based on gitignore rules.
"""

from __future__ import annotations

import re
from pathlib import Path

from loguru import logger


class GitIgnoreRule:
    """Represents a single gitignore rule/pattern."""

    def __init__(
        self, pattern: str, negation: bool = False, directory_only: bool = False
    ):
        """Initialize a gitignore rule.

        Args:
            pattern: The gitignore pattern (without leading ! or trailing /)
            negation: True if this is a negation pattern (starts with !)
            directory_only: True if pattern only matches directories (ends with /)
        """
        self.original_pattern = pattern
        self.negation = negation
        self.directory_only = directory_only
        self.regex = self._compile_pattern(pattern)

    def _compile_pattern(self, pattern: str) -> re.Pattern:
        """Convert gitignore pattern to regex.

        Gitignore pattern rules:
        - * matches anything except /
        - ** matches anything including /
        - ? matches any single character except /
        - [abc] matches any character in brackets
        - Pattern with / matches relative to .gitignore location
        - Pattern without / can match at any level
        """
        # Handle leading slash (anchored to root)
        anchored = pattern.startswith("/")
        if anchored:
            pattern = pattern[1:]

        # Build regex pattern
        regex_parts = []
        i = 0
        while i < len(pattern):
            c = pattern[i]

            if c == "*":
                if i + 1 < len(pattern) and pattern[i + 1] == "*":
                    # ** matches everything including /
                    if i + 2 < len(pattern) and pattern[i + 2] == "/":
                        # **/ matches zero or more directories
                        regex_parts.append("(?:.*/)?")
                        i += 3
                    else:
                        regex_parts.append(".*")
                        i += 2
                else:
                    # * matches anything except /
                    regex_parts.append("[^/]*")
                    i += 1
            elif c == "?":
                # ? matches any single character except /
                regex_parts.append("[^/]")
                i += 1
            elif c == "[":
                # Character class - find closing bracket
                j = i + 1
                if j < len(pattern) and pattern[j] == "!":
                    j += 1
                if j < len(pattern) and pattern[j] == "]":
                    j += 1
                while j < len(pattern) and pattern[j] != "]":
                    j += 1
                if j < len(pattern):
                    # Valid character class
                    regex_parts.append(pattern[i : j + 1])
                    i = j + 1
                else:
                    # No closing bracket, treat as literal
                    regex_parts.append(re.escape(c))
                    i += 1
            else:
                regex_parts.append(re.escape(c))
                i += 1

        regex_str = "".join(regex_parts)

        # If pattern contains /, it's anchored to the base
        # Otherwise, it can match at any directory level
        if "/" in self.original_pattern and not anchored:
            # Contains slash but not anchored - match from base
            regex_str = f"^{regex_str}$"
        elif anchored or "/" in self.original_pattern:
            regex_str = f"^{regex_str}$"
        else:
            # Can match at any level
            regex_str = f"(?:^|/){regex_str}$"

        return re.compile(regex_str)

    def matches(self, path: str, is_dir: bool = False) -> bool:
        """Check if the path matches this rule.

        Args:
            path: Relative path to check (using forward slashes)
            is_dir: True if the path is a directory

        Returns:
            True if the path matches the pattern
        """
        if self.directory_only and not is_dir:
            return False

        # Normalize path (remove leading/trailing slashes)
        path = path.strip("/")

        # Try to match
        if self.regex.search(path):
            return True

        # Also try matching just the basename for non-anchored patterns
        if "/" not in self.original_pattern:
            basename = path.rsplit("/", 1)[-1]
            if self.regex.search(basename):
                return True

        return False


class GitIgnoreParser:
    """Parser for .gitignore files with rule matching.

    Example:
        parser = GitIgnoreParser(repo_path)

        if parser.should_ignore(some_file_path):
            print("File should be ignored")
    """

    def __init__(self, repo_path: Path, gitignore_path: Path | None = None):
        """Initialize the gitignore parser.

        Args:
            repo_path: Root path of the repository
            gitignore_path: Path to .gitignore file (defaults to repo_path/.gitignore)
        """
        self.repo_path = Path(repo_path)
        self.gitignore_path = gitignore_path or (self.repo_path / ".gitignore")
        self.rules: list[GitIgnoreRule] = []
        self._loaded = False

    def load(self) -> bool:
        """Load and parse the .gitignore file.

        Returns:
            True if successfully loaded, False if file doesn't exist or failed
        """
        if self._loaded:
            return True

        if not self.gitignore_path.exists():
            logger.debug(f"No .gitignore found at {self.gitignore_path}")
            self._loaded = True
            return False

        try:
            content = self.gitignore_path.read_text(encoding="utf-8")
            self._parse_content(content)
            self._loaded = True
            logger.debug(f"Loaded {len(self.rules)} rules from {self.gitignore_path}")
            return True
        except Exception as e:
            logger.warning(f"Failed to load .gitignore: {e}")
            self._loaded = True
            return False

    def _parse_content(self, content: str) -> None:
        """Parse gitignore content into rules.

        Args:
            content: Content of the .gitignore file
        """
        for line in content.splitlines():
            rule = self._parse_line(line)
            if rule:
                self.rules.append(rule)

    def _parse_line(self, line: str) -> GitIgnoreRule | None:
        """Parse a single line from gitignore.

        Args:
            line: A single line from the gitignore file

        Returns:
            GitIgnoreRule if valid pattern, None otherwise
        """
        # Strip trailing whitespace (but preserve leading for patterns)
        line = line.rstrip()

        # Skip empty lines
        if not line:
            return None

        # Skip comments
        if line.startswith("#"):
            return None

        # Handle escaped hash at start
        if line.startswith("\\#"):
            line = line[1:]

        # Check for negation
        negation = False
        if line.startswith("!"):
            negation = True
            line = line[1:]

        # Check for directory-only pattern
        directory_only = False
        if line.endswith("/"):
            directory_only = True
            line = line[:-1]

        # Skip if pattern is empty after processing
        if not line:
            return None

        return GitIgnoreRule(line, negation=negation, directory_only=directory_only)

    def should_ignore(self, path: Path | str, is_dir: bool | None = None) -> bool:
        """Check if a path should be ignored based on gitignore rules.

        Args:
            path: Absolute or relative path to check
            is_dir: Whether the path is a directory. If None, will be auto-detected.

        Returns:
            True if the path should be ignored
        """
        # Ensure rules are loaded
        if not self._loaded:
            self.load()

        # No rules means nothing to ignore
        if not self.rules:
            return False

        # Convert to Path and get relative path
        path = Path(path)

        # Auto-detect if directory (with error handling)
        if is_dir is None:
            try:
                is_dir = path.is_dir()
            except (PermissionError, OSError):
                # If we can't check, assume it's not a directory
                is_dir = False

        # Get relative path from repo root
        try:
            if path.is_absolute():
                rel_path = path.relative_to(self.repo_path)
            else:
                rel_path = path
        except ValueError:
            # Path is not under repo_path
            return False

        # Convert to forward-slash string for pattern matching
        path_str = str(rel_path).replace("\\", "/")

        # Apply rules in order, last matching rule wins
        ignored = False
        for rule in self.rules:
            if rule.matches(path_str, is_dir):
                ignored = not rule.negation

        if ignored:
            return True

        # If not matched directly, check if any parent directory is ignored
        # by a directory-only rule (e.g., "build/" should ignore "build/foo.txt")
        parts = rel_path.parts
        for i in range(1, len(parts)):
            parent_str = "/".join(parts[:i])
            parent_ignored = False
            for rule in self.rules:
                if rule.matches(parent_str, is_dir=True):
                    parent_ignored = not rule.negation
            if parent_ignored:
                return True

        return False

    def add_rule(self, pattern: str) -> None:
        """Add a custom rule pattern.

        Args:
            pattern: Gitignore-style pattern to add
        """
        rule = self._parse_line(pattern)
        if rule:
            self.rules.append(rule)


def create_gitignore_parser(repo_path: Path | str) -> GitIgnoreParser:
    """Create and load a GitIgnoreParser for a repository.

    Convenience function that creates a parser and loads the rules.

    Args:
        repo_path: Path to the repository root

    Returns:
        Loaded GitIgnoreParser instance
    """
    parser = GitIgnoreParser(Path(repo_path))
    parser.load()
    return parser

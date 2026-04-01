# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal


@dataclass
class FileChange:
    """Represents a file change for incremental updates.

    Attributes:
        path: Absolute path to the file
        action: Type of change - add, modify, or delete
        old_hash: Previous MD5 hash (for modify/delete)
        new_hash: New MD5 hash (for add/modify)
    """

    path: Path
    action: Literal["add", "modify", "delete"]
    old_hash: str | None = None
    new_hash: str | None = None

    def __post_init__(self):
        """Ensure path is a Path object."""
        if isinstance(self.path, str):
            object.__setattr__(self, "path", Path(self.path))


@dataclass
class UpdateResult:
    """Result of an incremental update operation.

    Attributes:
        added: Number of files added
        modified: Number of files modified
        deleted: Number of files deleted
        added_files: List of added file paths (relative to repo root)
        modified_files: List of modified file paths (relative to repo root)
        deleted_files: List of deleted file paths (relative to repo root)
        calls_created: Number of CALLS relationships created
        calls_rebuilt: Number of CALLS relationships rebuilt
        embeddings_generated: Number of embeddings generated for new nodes
        duration_ms: Duration of the update in milliseconds
        errors: List of error messages encountered
        timestamp: ISO timestamp of when the update completed
    """

    added: int = 0
    modified: int = 0
    deleted: int = 0
    added_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    calls_created: int = 0
    calls_rebuilt: int = 0
    embeddings_generated: int = 0
    duration_ms: float = 0
    errors: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def total_changes(self) -> int:
        """Total number of file changes."""
        return self.added + self.modified + self.deleted

    @property
    def success(self) -> bool:
        """Whether the update completed without errors."""
        return len(self.errors) == 0

    @property
    def all_changed_files(self) -> list[str]:
        """All changed files (added + modified + deleted)."""
        return self.added_files + self.modified_files + self.deleted_files

    def add_error(self, error: str) -> None:
        """Add an error message to the result."""
        self.errors.append(error)

    def merge(self, other: "UpdateResult") -> None:
        """Merge another UpdateResult into this one."""
        self.added += other.added
        self.modified += other.modified
        self.deleted += other.deleted
        self.added_files.extend(other.added_files)
        self.modified_files.extend(other.modified_files)
        self.deleted_files.extend(other.deleted_files)
        self.calls_created += other.calls_created
        self.calls_rebuilt += other.calls_rebuilt
        self.duration_ms += other.duration_ms
        self.errors.extend(other.errors)


@dataclass
class GitRef:
    """Git reference information.

    Attributes:
        name: Reference name (branch/tag name or commit sha)
        ref_type: Type of reference - branch, tag, or commit
        commit_sha: Full commit SHA
        is_current: Whether this is the currently checked-out ref
    """

    name: str
    ref_type: Literal["branch", "tag", "commit"]
    commit_sha: str
    is_current: bool = False

    @property
    def short_sha(self) -> str:
        """Get short (7-character) commit SHA."""
        return self.commit_sha[:7]

    def __str__(self) -> str:
        """String representation of the Git reference."""
        if self.is_current:
            return f"{self.name} ({self.ref_type}) - {self.short_sha} *"
        return f"{self.name} ({self.ref_type}) - {self.short_sha}"

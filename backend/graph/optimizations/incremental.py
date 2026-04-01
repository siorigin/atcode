# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import json
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class FileState:
    """State information for a single file."""

    path: str
    content_hash: str
    last_modified: float
    size: int
    language: str | None = None
    node_count: int = 0  # Number of nodes created from this file


@dataclass
class ProjectState:
    """Complete state of a project for incremental builds."""

    project_name: str
    repo_path: str
    last_build_time: str
    files: dict[str, FileState] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "project_name": self.project_name,
            "repo_path": self.repo_path,
            "last_build_time": self.last_build_time,
            "files": {path: asdict(state) for path, state in self.files.items()},
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectState":
        """Create from dictionary."""
        files = {
            path: FileState(**state_data)
            for path, state_data in data.get("files", {}).items()
        }
        return cls(
            project_name=data["project_name"],
            repo_path=data["repo_path"],
            last_build_time=data["last_build_time"],
            files=files,
            metadata=data.get("metadata", {}),
        )


@dataclass
class IncrementalDiff:
    """Difference between current state and previous build."""

    added_files: list[Path] = field(default_factory=list)
    modified_files: list[Path] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        """Check if there are any changes."""
        return bool(self.added_files or self.modified_files or self.deleted_files)

    @property
    def files_to_process(self) -> list[Path]:
        """Get all files that need processing."""
        return self.added_files + self.modified_files

    def __repr__(self) -> str:
        return (
            f"IncrementalDiff(added={len(self.added_files)}, "
            f"modified={len(self.modified_files)}, "
            f"deleted={len(self.deleted_files)})"
        )


class IncrementalBuilder:
    """Manages incremental graph builds by tracking file changes.

    Key features:
    1. File hash tracking for change detection
    2. Persistent state storage
    3. Efficient diff computation
    4. Clean removal of deleted file nodes
    """

    def __init__(
        self,
        repo_path: Path,
        project_name: str,
        state_dir: Path | str | None = None,
        ignore_patterns: set[str] | None = None,
    ):
        """Initialize the incremental builder.

        Args:
            repo_path: Repository root path
            project_name: Project name
            state_dir: Directory for state storage (overrides auto-detection with CacheRegistry)
            ignore_patterns: Directory/file patterns to ignore
        """
        from graph.sync.cache_registry import get_cache_registry

        self.repo_path = repo_path
        self.project_name = project_name
        self.ignore_patterns = ignore_patterns or {
            ".git",
            "__pycache__",
            "node_modules",
            ".venv",
            "venv",
            ".env",
            "dist",
            "build",
        }

        # State directory with fallback support:
        # 1. Explicit state_dir parameter (highest priority)
        # 2. CacheRegistry with fallback (uses project_name)
        if state_dir:
            self.state_dir = Path(state_dir)
        else:
            registry = get_cache_registry()
            self.state_dir = registry.get_cache_dir(project_name, repo_path)

        # Track if state directory is writable (already handled by CacheRegistry,
        # but keep for backward compatibility)
        self._writable = True

        # Try to create state directory (best-effort)
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            logger.debug(
                f"Cannot create state directory (permission denied): {self.state_dir}"
            )
            self._writable = False
        except Exception as e:
            logger.debug(f"Cannot create state directory: {e}")
            self._writable = False

        # Previous state (loaded on demand)
        self._previous_state: ProjectState | None = None

        # Lock for thread-safe state updates
        self._lock = threading.Lock()

    @property
    def state_file(self) -> Path:
        """Path to the state file."""
        return self.state_dir / f"{self.project_name}_state.json"

    def _compute_file_hash(self, file_path: Path) -> str:
        """Compute MD5 hash of file content."""
        try:
            content = file_path.read_bytes()
            return hashlib.md5(content).hexdigest()
        except Exception:
            return ""

    def _should_skip_path(self, path: Path) -> bool:
        """Check if path should be skipped based on ignore patterns."""
        try:
            parts = path.relative_to(self.repo_path).parts
            return any(part in self.ignore_patterns for part in parts)
        except ValueError:
            return True

    def load_previous_state(self) -> ProjectState | None:
        """Load previous build state from disk.

        Returns:
            Previous ProjectState or None if not found
        """
        if self._previous_state is not None:
            return self._previous_state

        if not self.state_file.exists():
            logger.info("No previous build state found")
            return None

        try:
            with open(self.state_file) as f:
                data = json.load(f)
            self._previous_state = ProjectState.from_dict(data)
            logger.info(
                f"Loaded previous state: {len(self._previous_state.files)} files, "
                f"built at {self._previous_state.last_build_time}"
            )
            return self._previous_state
        except Exception as e:
            logger.warning(f"Failed to load previous state: {e}")
            return None

    def save_state(self, state: ProjectState) -> None:
        """Save current state to disk.

        Note: This operation is best-effort. If the state directory is not writable
        (e.g., system package directories), the save is silently skipped.

        Args:
            state: Current project state
        """
        if not self._writable:
            return

        try:
            with open(self.state_file, "w") as f:
                json.dump(state.to_dict(), f, indent=2)
            logger.info(f"Saved build state: {len(state.files)} files")
        except PermissionError:
            logger.debug(f"Cannot save state (permission denied): {self.state_file}")
        except Exception as e:
            logger.warning(f"Failed to save state: {e}")

    def compute_current_state(
        self,
        file_filter: Callable[[Path], bool] | None = None,
        parallel_workers: int | None = None,
        previous_state: "ProjectState | None" = None,
    ) -> ProjectState:
        """Compute current state of all files in the repository.

        Uses parallel thread pool for hashing files to speed up computation.
        When previous_state is provided, uses mtime+size as a fast filter:
        files whose mtime and size are unchanged reuse their previous hash,
        avoiding expensive content reads.

        Args:
            file_filter: Optional filter function (path -> bool)
            parallel_workers: Number of parallel workers for hashing (default: os.cpu_count())
            previous_state: Previous ProjectState for mtime+size fast filtering

        Returns:
            Current ProjectState
        """
        if parallel_workers is None:
            parallel_workers = os.cpu_count() or 4

        # Build lookup for fast mtime+size comparison
        prev_files = previous_state.files if previous_state else {}

        # First pass: collect all file paths (fast, no I/O for reading content)
        file_paths: list[tuple[Path, str]] = []
        for file_path in self.repo_path.rglob("*"):
            if not file_path.is_file():
                continue

            if self._should_skip_path(file_path):
                continue

            if file_filter and not file_filter(file_path):
                continue

            try:
                rel_path = str(file_path.relative_to(self.repo_path))
                file_paths.append((file_path, rel_path))
            except Exception as e:
                logger.debug(f"Error processing {file_path}: {e}")

        # Second pass: parallel compute file hashes (the slow part)
        # With mtime+size fast filter, unchanged files reuse previous hash
        files: dict[str, FileState] = {}
        files_needing_hash: list[tuple[Path, str]] = []
        skipped_count = 0

        for file_path, rel_path in file_paths:
            try:
                stat = file_path.stat()
                prev = prev_files.get(rel_path)
                if (
                    prev
                    and prev.last_modified == stat.st_mtime
                    and prev.size == stat.st_size
                ):
                    # mtime+size unchanged: reuse previous hash (no I/O needed)
                    files[rel_path] = FileState(
                        path=rel_path,
                        content_hash=prev.content_hash,
                        last_modified=stat.st_mtime,
                        size=stat.st_size,
                        language=prev.language,
                        node_count=prev.node_count,
                    )
                    skipped_count += 1
                else:
                    files_needing_hash.append((file_path, rel_path))
            except Exception as e:
                logger.debug(f"Error stat {file_path}: {e}")

        if skipped_count > 0:
            logger.info(
                f"mtime+size fast filter: {skipped_count} files unchanged, {len(files_needing_hash)} need hashing"
            )

        def compute_file_state(
            file_path: Path, rel_path: str
        ) -> tuple[str, FileState] | None:
            """Compute FileState for a single file."""
            try:
                stat = file_path.stat()
                return (
                    rel_path,
                    FileState(
                        path=rel_path,
                        content_hash=_compute_file_hash_static(file_path),
                        last_modified=stat.st_mtime,
                        size=stat.st_size,
                    ),
                )
            except Exception as e:
                logger.debug(f"Error processing {file_path}: {e}")
                return None

        # Use ThreadPoolExecutor for parallel hashing (I/O bound, threads help)
        with ThreadPoolExecutor(
            max_workers=parallel_workers, thread_name_prefix="hash"
        ) as executor:
            futures = {
                executor.submit(compute_file_state, fp, rp): (fp, rp)
                for fp, rp in files_needing_hash
            }

            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    rel_path, file_state = result
                    files[rel_path] = file_state

        return ProjectState(
            project_name=self.project_name,
            repo_path=str(self.repo_path),
            last_build_time=datetime.now(UTC).isoformat(),
            files=files,
        )

    def compute_diff(
        self,
        current_state: ProjectState | None = None,
    ) -> IncrementalDiff:
        """Compute difference between current and previous state.

        Args:
            current_state: Current state (computed if not provided)

        Returns:
            IncrementalDiff with changes
        """
        previous = self.load_previous_state()

        if current_state is None:
            current_state = self.compute_current_state(previous_state=previous)

        diff = IncrementalDiff()

        if previous is None:
            # No previous state - all files are new
            diff.added_files = [
                self.repo_path / path for path in current_state.files.keys()
            ]
            logger.info(f"First build: {len(diff.added_files)} files to process")
            return diff

        # Find added and modified files
        for path, file_state in current_state.files.items():
            full_path = self.repo_path / path

            if path not in previous.files:
                diff.added_files.append(full_path)
            elif file_state.content_hash != previous.files[path].content_hash:
                diff.modified_files.append(full_path)

        # Find deleted files
        for path in previous.files.keys():
            if path not in current_state.files:
                diff.deleted_files.append(path)

        logger.info(
            f"Incremental diff: {len(diff.added_files)} added, "
            f"{len(diff.modified_files)} modified, "
            f"{len(diff.deleted_files)} deleted"
        )

        return diff

    def update_file_state(
        self,
        file_path: Path,
        language: str | None = None,
        node_count: int = 0,
    ) -> None:
        """Update state for a single file after processing. Thread-safe.

        Args:
            file_path: Path to the processed file
            language: Detected language
            node_count: Number of nodes created
        """
        try:
            rel_path = str(file_path.relative_to(self.repo_path))
            stat = file_path.stat()
            content_hash = self._compute_file_hash(file_path)

            with self._lock:
                if self._previous_state is None:
                    self._previous_state = ProjectState(
                        project_name=self.project_name,
                        repo_path=str(self.repo_path),
                        last_build_time=datetime.now(UTC).isoformat(),
                    )

                self._previous_state.files[rel_path] = FileState(
                    path=rel_path,
                    content_hash=content_hash,
                    last_modified=stat.st_mtime,
                    size=stat.st_size,
                    language=language,
                    node_count=node_count,
                )
        except Exception as e:
            logger.debug(f"Error updating state for {file_path}: {e}")

    def mark_file_deleted(self, rel_path: str) -> None:
        """Mark a file as deleted in the state.

        Args:
            rel_path: Relative path of the deleted file
        """
        if self._previous_state and rel_path in self._previous_state.files:
            del self._previous_state.files[rel_path]

    def set_metadata(self, key: str, value: Any) -> None:
        """Set a metadata key-value pair on the project state."""
        if self._previous_state is None:
            # Create state if it doesn't exist yet (e.g., first build edge case)
            self._previous_state = ProjectState(
                project_name=self.project_name,
                repo_path=str(self.repo_path),
                last_build_time=datetime.now(UTC).isoformat(),
            )
        self._previous_state.metadata[key] = value

    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Get a metadata value from the project state."""
        state = self._previous_state or self.load_previous_state()
        if state:
            return state.metadata.get(key, default)
        return default

    def finalize_build(self) -> None:
        """Finalize the build by saving state."""
        if self._previous_state:
            self._previous_state.last_build_time = datetime.now(UTC).isoformat()
            self.save_state(self._previous_state)


def _compute_file_hash_static(file_path: Path) -> str:
    """Compute hash of file content using xxhash (fast) with md5 fallback."""
    try:
        content = file_path.read_bytes()
        try:
            import xxhash

            return xxhash.xxh64(content).hexdigest()
        except ImportError:
            return hashlib.md5(content).hexdigest()
    except Exception:
        return ""

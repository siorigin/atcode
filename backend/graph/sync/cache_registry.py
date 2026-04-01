# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Cache Registry for managing fallback cache directories.

When users don't have write permission to repo/.atcode, the cache is stored
in data/cache/{project_name}_{hash}/ instead. This registry maintains a mapping
from repo_path to cache directory for management and lookup.
"""

import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

# Relative to backend directory: backend/../data/cache -> data/cache
_BACKEND_DIR = Path(__file__).parent.parent.parent  # backend/
_DATA_CACHE_DIR = _BACKEND_DIR.parent / "data" / "cache"


class CacheRegistry:
    """
    Manages cache directory resolution with fallback support.

    Priority:
    1. repo/.atcode (if writable)
    2. data/cache/{project_name}_{hash}/ (fallback)

    The registry maintains a mapping from repo_path to cache directory
    for management operations (listing, cleanup, etc.).

    Thread-safe: All operations are protected by a lock.
    """

    _instance: "CacheRegistry | None" = None
    _lock = threading.Lock()

    def __new__(cls) -> "CacheRegistry":
        """Singleton pattern for global registry access."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize the registry (only once due to singleton)."""
        if getattr(self, "_initialized", False):
            return

        self._registry_file = _DATA_CACHE_DIR / "registry.json"
        self._entries: dict[str, dict[str, Any]] = {}
        self._file_lock = threading.Lock()
        self._load()
        self._initialized = True

    def _load(self) -> None:
        """Load registry from disk."""
        if not self._registry_file.exists():
            logger.debug(f"No registry file found at {self._registry_file}")
            return

        try:
            with open(self._registry_file, encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                logger.warning("Invalid registry format, starting fresh")
                return

            self._entries = data.get("entries", {})
            logger.debug(f"Loaded cache registry: {len(self._entries)} entries")

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse registry file: {e}")
        except Exception as e:
            logger.warning(f"Failed to load registry: {e}")

    def _save(self) -> None:
        """Save registry to disk (best-effort)."""
        try:
            _DATA_CACHE_DIR.mkdir(parents=True, exist_ok=True)

            data = {
                "version": 1,
                "updated_at": datetime.now(UTC).isoformat(),
                "entries": self._entries,
            }

            # Atomic write via temp file
            temp_file = self._registry_file.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            temp_file.rename(self._registry_file)
            logger.debug(f"Saved cache registry: {len(self._entries)} entries")

        except Exception as e:
            logger.warning(f"Failed to save registry: {e}")

    @staticmethod
    def compute_cache_dir_name(project_name: str, repo_path: str) -> str:
        """
        Compute deterministic cache directory name.

        Format: {project_name}_{8-char-hash-of-repo_path}

        Args:
            project_name: Project name (for readability)
            repo_path: Absolute path to repository

        Returns:
            Cache directory name (not full path)
        """
        # Normalize path for consistent hashing
        normalized_path = str(Path(repo_path).resolve())
        path_hash = hashlib.md5(normalized_path.encode()).hexdigest()[:8]

        # Sanitize project_name for filesystem
        safe_name = "".join(
            c if c.isalnum() or c in "-_" else "_" for c in project_name
        )

        return f"{safe_name}_{path_hash}"

    @staticmethod
    def _can_write(path: Path) -> bool:
        """Check if path is writable (by attempting to create a test file)."""
        try:
            path.mkdir(parents=True, exist_ok=True)
            test_file = path / ".write_test"
            test_file.touch()
            test_file.unlink()
            return True
        except (PermissionError, OSError):
            return False

    @staticmethod
    def _ensure_git_exclude(repo_path: Path, entry: str = ".atcode") -> None:
        """Add an entry to .git/info/exclude if not already present.

        This prevents the cache directory from interfering with git operations
        (pull, checkout, branch switch) without modifying the repo's .gitignore.

        Args:
            repo_path: Repository root path
            entry: Entry to add to the exclude file
        """
        git_info_dir = repo_path / ".git" / "info"
        exclude_file = git_info_dir / "exclude"

        try:
            # Check if .git/info exists (confirms it's a git repo)
            if not git_info_dir.exists():
                return

            # Read existing entries
            existing_content = ""
            if exclude_file.exists():
                existing_content = exclude_file.read_text(encoding="utf-8")

            # Check if already excluded
            for line in existing_content.splitlines():
                stripped = line.strip()
                if stripped == entry or stripped == f"/{entry}":
                    return  # Already excluded

            # Append the entry
            with open(exclude_file, "a", encoding="utf-8") as f:
                if existing_content and not existing_content.endswith("\n"):
                    f.write("\n")
                f.write(f"{entry}\n")
            logger.debug(f"Added '{entry}' to {exclude_file}")

        except Exception as e:
            logger.debug(f"Could not update git exclude: {e}")

    def get_cache_dir(
        self,
        project_name: str,
        repo_path: str | Path,
        prefer_fallback: bool = False,
    ) -> Path:
        """
        Get the cache directory for a project, with automatic fallback.

        Priority:
        1. repo/.atcode (if writable and not prefer_fallback)
        2. data/cache/{project_name}_{hash}/ (fallback)

        Args:
            project_name: Project name
            repo_path: Repository root path
            prefer_fallback: If True, always use fallback directory

        Returns:
            Path to cache directory
        """
        repo_path = Path(repo_path).resolve()
        repo_path_str = str(repo_path)

        with self._file_lock:
            # 1. Try repo/.atcode first (unless prefer_fallback)
            if not prefer_fallback:
                repo_cache = repo_path / ".atcode"
                if self._can_write(repo_cache):
                    # Ensure .atcode is in .git/info/exclude so it doesn't
                    # interfere with git pull/checkout/branch operations
                    self._ensure_git_exclude(repo_path)
                    logger.debug(f"Using repo cache: {repo_cache}")
                    return repo_cache

            # 2. Fallback to data/cache
            cache_dir_name = self.compute_cache_dir_name(project_name, repo_path_str)
            cache_dir = _DATA_CACHE_DIR / cache_dir_name

            # Ensure directory exists
            try:
                cache_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.error(f"Failed to create fallback cache dir: {e}")
                # Return it anyway, let caller handle the error

            # Update registry
            self._register(project_name, repo_path_str, cache_dir_name)

            logger.info(f"Using fallback cache: {cache_dir}")
            return cache_dir

    def _register(self, project_name: str, repo_path: str, cache_dir_name: str) -> None:
        """Register or update a cache entry."""
        now = datetime.now(UTC).isoformat()

        if repo_path in self._entries:
            # Update existing entry
            self._entries[repo_path]["last_accessed"] = now
            # Update project_name if different (repo_path is the unique key)
            if self._entries[repo_path]["project_name"] != project_name:
                logger.info(
                    f"Project name changed for {repo_path}: "
                    f"{self._entries[repo_path]['project_name']} -> {project_name}"
                )
                self._entries[repo_path]["project_name"] = project_name
        else:
            # New entry
            self._entries[repo_path] = {
                "project_name": project_name,
                "cache_dir": cache_dir_name,
                "created_at": now,
                "last_accessed": now,
            }

        self._save()

    def lookup_by_repo_path(self, repo_path: str | Path) -> Path | None:
        """
        Look up cache directory by repo_path.

        This does NOT check writability - use get_cache_dir() for that.

        Args:
            repo_path: Repository path

        Returns:
            Path to fallback cache directory, or None if not in registry
        """
        repo_path_str = str(Path(repo_path).resolve())

        with self._file_lock:
            entry = self._entries.get(repo_path_str)
            if entry:
                return _DATA_CACHE_DIR / entry["cache_dir"]
            return None

    def list_all(self) -> list[dict[str, Any]]:
        """
        List all registered cache entries.

        Returns:
            List of entry dicts with repo_path, project_name, cache_dir, etc.
        """
        with self._file_lock:
            return [
                {
                    "repo_path": repo_path,
                    "cache_dir": str(_DATA_CACHE_DIR / entry["cache_dir"]),
                    **entry,
                }
                for repo_path, entry in self._entries.items()
            ]

    def remove(self, repo_path: str | Path, delete_files: bool = False) -> bool:
        """
        Remove an entry from the registry.

        Args:
            repo_path: Repository path
            delete_files: If True, also delete the cache directory

        Returns:
            True if entry was found and removed
        """
        repo_path_str = str(Path(repo_path).resolve())

        with self._file_lock:
            entry = self._entries.pop(repo_path_str, None)
            if entry is None:
                return False

            if delete_files:
                cache_dir = _DATA_CACHE_DIR / entry["cache_dir"]
                try:
                    import shutil

                    if cache_dir.exists():
                        shutil.rmtree(cache_dir)
                        logger.info(f"Deleted cache directory: {cache_dir}")
                except Exception as e:
                    logger.warning(f"Failed to delete cache directory: {e}")

            self._save()
            return True

    def cleanup_stale(self, days: int = 30) -> list[str]:
        """
        Remove entries not accessed in the last N days.

        Args:
            days: Number of days of inactivity

        Returns:
            List of removed repo_paths
        """
        from datetime import timedelta

        cutoff = datetime.now(UTC) - timedelta(days=days)
        removed = []

        with self._file_lock:
            for repo_path, entry in list(self._entries.items()):
                try:
                    last_accessed = datetime.fromisoformat(
                        entry["last_accessed"].replace("Z", "+00:00")
                    )
                    if last_accessed < cutoff:
                        self._entries.pop(repo_path)
                        removed.append(repo_path)

                        # Delete cache directory
                        cache_dir = _DATA_CACHE_DIR / entry["cache_dir"]
                        try:
                            import shutil

                            if cache_dir.exists():
                                shutil.rmtree(cache_dir)
                        except Exception:
                            pass
                except Exception:
                    continue

            if removed:
                self._save()
                logger.info(f"Cleaned up {len(removed)} stale cache entries")

        return removed


# Global instance getter
def get_cache_registry() -> CacheRegistry:
    """Get the global CacheRegistry instance."""
    return CacheRegistry()

# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import json
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    import xxhash

    _USE_XXHASH = True
except ImportError:
    _USE_XXHASH = False

from core.config import BINARY_FILE_EXTENSIONS
from core.fs_utils import safe_rglob_iter
from core.gitignore_parser import GitIgnoreParser
from loguru import logger
from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirModifiedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from .models import FileChange


class GitHeadWatcher(FileSystemEventHandler):
    """Monitors .git/HEAD file for branch switches.

    This is a lightweight watcher that only monitors the .git/HEAD file
    to detect when the user switches branches via command line git.
    """

    def __init__(
        self,
        git_dir: Path,
        on_ref_change: Callable[[], None],
        debounce_delay: float = 0.5,
    ):
        """Initialize the Git HEAD watcher.

        Args:
            git_dir: Path to the .git directory
            on_ref_change: Callback when HEAD changes (branch switch detected)
            debounce_delay: Delay before triggering callback (to batch rapid changes)
        """
        self.git_dir = git_dir
        self.on_ref_change = on_ref_change
        self.debounce_delay = debounce_delay

        self._last_head_content: str | None = None
        self._debounce_timer: threading.Timer | None = None
        self._lock = threading.Lock()

        # Read initial HEAD content
        self._read_head_content()

    def _read_head_content(self) -> str | None:
        """Read current HEAD file content."""
        head_file = self.git_dir / "HEAD"
        try:
            if head_file.exists():
                content = head_file.read_text().strip()
                self._last_head_content = content
                return content
        except Exception as e:
            logger.debug(f"Failed to read .git/HEAD: {e}")
        return None

    def _schedule_callback(self) -> None:
        """Schedule the callback with debounce."""
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()

            self._debounce_timer = threading.Timer(
                self.debounce_delay,
                self._trigger_callback,
            )
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def _trigger_callback(self) -> None:
        """Trigger the ref change callback."""
        try:
            logger.info("Git HEAD change detected - branch switch")
            self.on_ref_change()
        except Exception as e:
            logger.error(f"Error in git ref change callback: {e}")

    def on_modified(self, event: FileModifiedEvent) -> None:
        """Handle .git/HEAD modification."""
        if event.is_directory:
            return

        # Only react to HEAD file changes
        src_path = Path(event.src_path)
        if src_path.name != "HEAD":
            return

        # Check if content actually changed
        new_content = self._read_head_content()
        if new_content and new_content != self._last_head_content:
            old_content = self._last_head_content
            self._last_head_content = new_content
            logger.debug(f"Git HEAD changed: {old_content} -> {new_content}")
            self._schedule_callback()

    def cancel(self) -> None:
        """Cancel any pending timer."""
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
                self._debounce_timer = None


class ChangeWatcher(FileSystemEventHandler):
    """File change monitoring with watchdog and hash comparison.

    Features:
    - Real-time file system monitoring via watchdog
    - Hash-based change detection (MD5)
    - Debouncing to batch rapid changes
    - Pause/resume support for Git operations

    Example:
        def on_changes(changes: list[FileChange]):
            for change in changes:
                print(f"{change.action}: {change.path}")

        watcher = ChangeWatcher(
            repo_path=Path("/path/to/repo"),
            on_changes=on_changes,
            debounce_delay=2.0,
        )
        watcher.start()
    """

    # Default ignore patterns
    DEFAULT_IGNORE_PATTERNS = {
        ".git",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        ".env",
        "dist",
        "build",
        ".pytest_cache",
        ".mypy_cache",
        ".tox",
        "*.egg-info",
        ".atcode",
    }

    # Cache file name
    CACHE_FILE_NAME = "hash_cache.json"

    @staticmethod
    def _compute_hash_static(file_path: Path) -> str:
        """Compute hash of file content using xxhash (fast) or MD5 (fallback).

        Static version for use without instance.
        """
        try:
            data = file_path.read_bytes()
            return ChangeWatcher._hash_bytes_static(data)
        except Exception as e:
            logger.debug(f"Failed to compute hash for {file_path}: {e}")
            return ""

    @staticmethod
    def _hash_bytes_static(data: bytes) -> str:
        """Compute a digest for already-loaded file bytes."""
        if _USE_XXHASH:
            return xxhash.xxh64(data).hexdigest()
        return hashlib.md5(data).hexdigest()

    @staticmethod
    def _compute_hash_profiled_static(file_path: Path) -> tuple[str, int, float]:
        """Compute hash and return (digest, bytes_read, duration_ms)."""
        start = time.perf_counter()
        try:
            data = file_path.read_bytes()
            digest = ChangeWatcher._hash_bytes_static(data)
            duration_ms = (time.perf_counter() - start) * 1000
            return digest, len(data), duration_ms
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.debug(f"Failed to compute hash for {file_path}: {e}")
            return "", 0, duration_ms

    @staticmethod
    def _hash_files_with_stats(
        files_to_hash: list[Path],
        repo_path: Path,
    ) -> tuple[dict[str, str], dict[str, float | int], list[tuple[float, int, str]]]:
        """Hash files in parallel and collect timing stats."""
        cache_data: dict[str, str] = {}
        total_bytes = 0
        slowest: list[tuple[float, int, str]] = []
        max_workers = min(8, len(files_to_hash) or 1)
        hash_start = time.perf_counter()

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_path = {
                executor.submit(ChangeWatcher._compute_hash_profiled_static, fp): fp
                for fp in files_to_hash
            }
            for future in as_completed(future_to_path):
                fp = future_to_path[future]
                try:
                    file_hash, bytes_read, duration_ms = future.result()
                    rel_path = str(fp.relative_to(repo_path))
                    total_bytes += bytes_read
                    if file_hash:
                        cache_data[rel_path] = file_hash
                        slowest.append((duration_ms, bytes_read, rel_path))
                except Exception as e:
                    logger.debug(f"Error hashing {fp}: {e}")

        hash_duration_ms = (time.perf_counter() - hash_start) * 1000
        slowest.sort(reverse=True)
        return (
            cache_data,
            {
                "hash_ms": hash_duration_ms,
                "bytes_hashed": total_bytes,
                "max_workers": max_workers,
            },
            slowest[:5],
        )

    @staticmethod
    def _log_hash_cache_timing(
        operation: str,
        *,
        gitignore_load_ms: float,
        discover_ms: float,
        filter_ms: float,
        hash_ms: float,
        write_ms: float,
        total_ms: float,
        candidate_count: int,
        hashed_count: int,
        bytes_hashed: int,
        max_workers: int,
        slowest: list[tuple[float, int, str]],
    ) -> None:
        """Log per-phase timings for hash cache operations."""
        mib_hashed = bytes_hashed / (1024 * 1024)
        throughput_mib_s = mib_hashed / (hash_ms / 1000) if hash_ms > 0 else 0.0
        logger.info(
            f"{operation} timings: gitignore_load={gitignore_load_ms:.1f}ms, "
            f"discover={discover_ms:.1f}ms, filter={filter_ms:.1f}ms, "
            f"hash={hash_ms:.1f}ms, write={write_ms:.1f}ms, total={total_ms:.1f}ms, "
            f"files={hashed_count}/{candidate_count}, bytes={mib_hashed:.1f}MiB, "
            f"workers={max_workers}, throughput={throughput_mib_s:.1f}MiB/s"
        )
        for duration_ms, bytes_read, rel_path in slowest:
            logger.info(
                f"{operation} slow file: {rel_path} "
                f"({bytes_read / 1024:.1f}KiB, {duration_ms:.1f}ms)"
            )

    @staticmethod
    def _resolve_cache_dir(repo_path: Path, project_name: str | None) -> Path:
        """Resolve the directory used for hash cache persistence."""
        if project_name:
            from .cache_registry import get_cache_registry

            registry = get_cache_registry()
            return registry.get_cache_dir(project_name, repo_path)
        return repo_path / ".atcode"

    @staticmethod
    def write_initial_cache(
        repo_path: Path | str,
        cache_data: dict[str, str],
        project_name: str | None = None,
        source: str = "precomputed full build",
    ) -> None:
        """Persist a precomputed hash cache without rescanning the repository."""
        repo_path = Path(repo_path)
        cache_dir = ChangeWatcher._resolve_cache_dir(repo_path, project_name)
        cache_file = cache_dir / ChangeWatcher.CACHE_FILE_NAME
        start = time.perf_counter()

        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            data = {
                "version": 1,
                "repo_path": str(repo_path),
                "file_count": len(cache_data),
                "updated_at": time.time(),
                "cache": cache_data,
            }

            temp_file = cache_file.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
            temp_file.rename(cache_file)

            duration_ms = (time.perf_counter() - start) * 1000
            logger.info(
                f"Initial hash cache written from {source}: "
                f"{len(cache_data)} files in {duration_ms:.1f}ms"
            )
        except PermissionError:
            logger.debug(
                f"Cannot save initial hash cache (permission denied): {cache_dir}"
            )
        except Exception as e:
            logger.warning(f"Failed to save initial hash cache: {e}")

    @staticmethod
    def _should_ignore_static(
        path: Path,
        repo_path: Path,
        ignore_patterns: set[str],
        gitignore_parser: GitIgnoreParser,
        subdirs: set[str] | None = None,
    ) -> bool:
        """Check if path should be ignored.

        Static version for use without instance.
        """
        try:
            parts = path.relative_to(repo_path).parts

            # Skip hidden directories (starting with .) except .github, .gitlab
            if any(
                part.startswith(".") and part not in {".github", ".gitlab"}
                for part in parts
            ):
                return True

            # Check subdirs filter: if set, only allow files under those subdirs
            if subdirs and parts:
                if parts[0] not in subdirs:
                    return True

            # Check directory patterns from config
            for part in parts:
                if part in ignore_patterns:
                    return True
                # Check glob patterns like *.egg-info
                for pattern in ignore_patterns:
                    if pattern.startswith("*") and part.endswith(pattern[1:]):
                        return True

            # Check binary file extensions
            if path.suffix.lower() in BINARY_FILE_EXTENSIONS:
                return True

            # Check gitignore rules
            # Callers only use this helper for file paths; passing is_dir=False
            # avoids an extra per-path stat in GitIgnoreParser.should_ignore().
            if gitignore_parser.should_ignore(path, is_dir=False):
                return True

            return False
        except ValueError:
            # Path is not relative to repo_path
            return True

    @staticmethod
    def _iter_files_static(repo_path: Path, subdirs: set[str] | None = None):
        """Iterate over all files respecting subdirs filter.

        Static version for use without instance.
        Safely handles permission errors by skipping inaccessible files/directories.
        """
        if subdirs:
            for subdir_name in subdirs:
                subdir_path = repo_path / subdir_name
                try:
                    if subdir_path.exists() and subdir_path.is_dir():
                        yield from safe_rglob_iter(subdir_path)
                except (PermissionError, OSError):
                    continue
        else:
            yield from safe_rglob_iter(repo_path)

    @staticmethod
    def build_initial_cache(
        repo_path: Path | str,
        subdirs: set[str] | None = None,
        project_name: str | None = None,
        precomputed_cache: dict[str, str] | None = None,
    ) -> None:
        """Build initial hash cache after graph build completes.

        Uses walk_files() for fast file discovery (git ls-files fast path
        + os.walk with directory pruning), avoiding the slow safe_rglob_iter
        that traverses .git and other ignored directories.

        Args:
            repo_path: Repository root path
            subdirs: Optional set of subdirectory names to scan.
            project_name: Project name (required for fallback cache resolution)
        """
        from core.config import IGNORE_PATTERNS
        from core.fs_utils import walk_files

        repo_path = Path(repo_path)
        cache_dir = ChangeWatcher._resolve_cache_dir(repo_path, project_name)

        cache_file = cache_dir / ChangeWatcher.CACHE_FILE_NAME

        logger.info(f"Building initial hash cache for {repo_path}...")
        total_start = time.perf_counter()
        cache_data = dict(precomputed_cache or {})

        parser_start = time.perf_counter()
        gitignore_parser = GitIgnoreParser(repo_path)
        gitignore_parser.load()
        gitignore_load_ms = (time.perf_counter() - parser_start) * 1000

        # walk_files uses git ls-files (fast, no stat) or os.walk with pruning.
        # Apply watcher-level filtering afterwards to preserve binary/gitignore
        # behavior while avoiding traversal into ignored directories.
        discover_start = time.perf_counter()
        candidate_files = walk_files(
            repo_path, IGNORE_PATTERNS, gitignore_parser=gitignore_parser, subdirs=subdirs
        )
        discover_ms = (time.perf_counter() - discover_start) * 1000

        filter_start = time.perf_counter()
        files_to_hash = [
            fp
            for fp in candidate_files
            if str(fp.relative_to(repo_path)) not in cache_data
            and not ChangeWatcher._should_ignore_static(
                fp, repo_path, IGNORE_PATTERNS, gitignore_parser, subdirs
            )
        ]
        filter_ms = (time.perf_counter() - filter_start) * 1000

        hashed_cache, hash_stats, slowest = ChangeWatcher._hash_files_with_stats(
            files_to_hash, repo_path
        )
        cache_data.update(hashed_cache)

        # Write cache to disk
        try:
            write_start = time.perf_counter()
            ChangeWatcher.write_initial_cache(
                repo_path,
                cache_data,
                project_name=project_name,
                source="scan",
            )
            write_ms = (time.perf_counter() - write_start) * 1000
            total_ms = (time.perf_counter() - total_start) * 1000

            logger.info(
                f"Initial hash cache built: {len(cache_data)} files in {total_ms:.1f}ms"
            )
            ChangeWatcher._log_hash_cache_timing(
                "Initial hash cache",
                gitignore_load_ms=gitignore_load_ms,
                discover_ms=discover_ms,
                filter_ms=filter_ms,
                hash_ms=float(hash_stats["hash_ms"]),
                write_ms=write_ms,
                total_ms=total_ms,
                candidate_count=len(candidate_files),
                hashed_count=len(cache_data),
                bytes_hashed=int(hash_stats["bytes_hashed"]),
                max_workers=int(hash_stats["max_workers"]),
                slowest=slowest,
            )

        except PermissionError:
            logger.debug(
                f"Cannot save initial hash cache (permission denied): {cache_dir}"
            )
        except Exception as e:
            logger.warning(f"Failed to save initial hash cache: {e}")

    def __init__(
        self,
        repo_path: Path,
        on_changes: Callable[[list[FileChange]], None],
        debounce_delay: float = 2.0,
        ignore_patterns: set[str] | None = None,
        cache_dir: Path | None = None,
        on_git_ref_change: Callable[[], None] | None = None,
        subdirs: set[str] | None = None,
        use_polling: bool = False,
        project_name: str | None = None,
    ):
        """Initialize the change watcher.

        Args:
            repo_path: Repository root path to monitor
            on_changes: Callback function when changes are detected
            debounce_delay: Delay in seconds to batch changes
            ignore_patterns: Directory/file patterns to ignore
            cache_dir: Directory to store hash cache (overrides auto-detection)
            on_git_ref_change: Callback when git branch/ref changes (for CLI git operations)
            subdirs: Optional set of subdirectory names to monitor. If provided,
                     only these subdirectories under repo_path are watched.
                     If None, the entire repo_path is watched.
            use_polling: Use polling observer instead of inotify (for NFS/network filesystems)
            project_name: Project name (used for fallback cache resolution via CacheRegistry)
        """
        from .cache_registry import get_cache_registry

        self.repo_path = Path(repo_path)
        self.on_changes = on_changes
        self.debounce_delay = debounce_delay
        self.ignore_patterns = ignore_patterns or self.DEFAULT_IGNORE_PATTERNS
        self.on_git_ref_change = on_git_ref_change
        self.subdirs = subdirs  # Only watch these subdirs if set
        self.use_polling = (
            use_polling  # Use PollingObserver for NFS/network filesystems
        )
        self.project_name = project_name

        # Cache directory resolution with fallback support:
        # 1. Explicit cache_dir parameter (highest priority)
        # 2. CacheRegistry with fallback (if project_name provided)
        # 3. repo/.atcode (legacy fallback)
        if cache_dir:
            self.cache_dir = cache_dir
        elif project_name:
            registry = get_cache_registry()
            self.cache_dir = registry.get_cache_dir(project_name, self.repo_path)
        else:
            self.cache_dir = self.repo_path / ".atcode"

        self.cache_file = self.cache_dir / self.CACHE_FILE_NAME

        # Hash cache: {relative_path: md5_hash}
        self._hash_cache: dict[str, str] = {}
        self._hash_cache_lock = threading.Lock()

        # Pending changes during debounce period
        self._pending_changes: dict[str, FileChange] = {}
        self._pending_lock = threading.Lock()
        self._debounce_timer: threading.Timer | None = None

        # Watchdog observer
        self._observer: Observer | None = None
        self._is_running = False
        self._is_paused = False

        # Track if cache needs saving (dirty flag)
        self._cache_dirty = False
        self._save_timer: threading.Timer | None = None

        # Lazy initialization flag
        self._cache_initialized = False
        self._cache_init_lock = threading.Lock()

        # Git HEAD watcher for detecting branch switches via CLI
        self._git_head_watcher: GitHeadWatcher | None = None
        self._git_observer: Observer | None = None

        # Initialize gitignore parser
        self._gitignore_parser = GitIgnoreParser(self.repo_path)
        self._gitignore_parser.load()

    def _should_ignore(self, path: Path) -> bool:
        """Check if path should be ignored based on patterns, hidden dirs, gitignore, and subdirs filter."""
        return self._should_ignore_static(
            path,
            self.repo_path,
            self.ignore_patterns,
            self._gitignore_parser,
            self.subdirs,
        )

    def _compute_hash(self, file_path: Path) -> str:
        """Compute hash of file content using xxhash (fast) or MD5 (fallback)."""
        return self._compute_hash_static(file_path)

    def _get_relative_path(self, path: Path) -> str:
        """Get relative path from repo_path."""
        try:
            return str(path.relative_to(self.repo_path))
        except ValueError:
            return str(path)

    def _schedule_flush(self) -> None:
        """Schedule a flush of pending changes after debounce delay."""
        if self._debounce_timer is not None:
            self._debounce_timer.cancel()

        self._debounce_timer = threading.Timer(self.debounce_delay, self._flush_pending)
        self._debounce_timer.start()

    def _flush_pending(self) -> None:
        """Flush pending changes to the callback."""
        with self._pending_lock:
            if not self._pending_changes:
                return
            changes = list(self._pending_changes.values())
            self._pending_changes.clear()

        if changes:
            logger.info(f"Detected {len(changes)} file changes")
            self.on_changes(changes)

    def _update_hash_cache(self, relative_path: str, file_hash: str) -> None:
        """Update hash cache with new value."""
        with self._hash_cache_lock:
            if file_hash:  # Only cache non-empty hashes
                self._hash_cache[relative_path] = file_hash
            elif relative_path in self._hash_cache:
                del self._hash_cache[relative_path]
            self._cache_dirty = True

        # Schedule delayed save (debounced)
        self._schedule_cache_save()

    def _get_cached_hash(self, relative_path: str) -> str | None:
        """Get cached hash for a file."""
        with self._hash_cache_lock:
            return self._hash_cache.get(relative_path)

    def _ensure_cache_initialized(self) -> None:
        """Ensure hash cache is initialized (lazy loading).

        This method is thread-safe and will only initialize once.
        """
        if self._cache_initialized:
            return

        with self._cache_init_lock:
            # Double-check after acquiring lock
            if self._cache_initialized:
                return

            # Try to load from disk first
            if not self._load_cache_from_disk():
                # If loading fails, rebuild from scratch
                self.rebuild_hash_cache()

            self._cache_initialized = True

    def _schedule_cache_save(self, delay: float = 5.0) -> None:
        """Schedule a delayed save of the cache to disk."""
        if self._save_timer is not None:
            self._save_timer.cancel()

        self._save_timer = threading.Timer(delay, self._save_cache_to_disk)
        self._save_timer.daemon = True
        self._save_timer.start()

    def _load_cache_from_disk(self) -> bool:
        """Load hash cache from disk.

        Returns:
            True if cache was loaded successfully, False otherwise
        """
        if not self.cache_file.exists():
            logger.info(f"No cache file found at {self.cache_file}")
            return False

        try:
            start_time = time.time()
            with open(self.cache_file, encoding="utf-8") as f:
                data = json.load(f)

            # Validate cache format
            if not isinstance(data, dict):
                logger.warning("Invalid cache format, rebuilding...")
                return False

            cache_data = data.get("cache", {})
            cached_repo_path = data.get("repo_path", "")

            # Verify repo path matches
            if cached_repo_path and cached_repo_path != str(self.repo_path):
                logger.warning(
                    f"Cache repo path mismatch: {cached_repo_path} != {self.repo_path}"
                )
                return False

            with self._hash_cache_lock:
                self._hash_cache = cache_data
                self._cache_dirty = False

            duration_ms = (time.time() - start_time) * 1000
            logger.info(
                f"Loaded hash cache from disk: {len(cache_data)} files in {duration_ms:.1f}ms"
            )
            return True

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse cache file: {e}")
            return False
        except Exception as e:
            logger.warning(f"Failed to load cache from disk: {e}")
            return False

    def _save_cache_to_disk(self) -> None:
        """Save hash cache to disk.

        Note: This operation is best-effort. If the cache directory is not writable
        (e.g., system package directories), the save is silently skipped.
        """
        if not self._cache_dirty:
            return

        try:
            # Ensure cache directory exists (may fail for read-only directories)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            logger.debug(
                f"Cannot create cache directory (permission denied): {self.cache_dir}"
            )
            return
        except Exception as e:
            logger.debug(f"Cannot create cache directory: {e}")
            return

        try:
            with self._hash_cache_lock:
                cache_data = self._hash_cache.copy()
                self._cache_dirty = False

            data = {
                "version": 1,
                "repo_path": str(self.repo_path),
                "file_count": len(cache_data),
                "updated_at": time.time(),
                "cache": cache_data,
            }

            # Ensure cache directory exists
            self.cache_dir.mkdir(parents=True, exist_ok=True)

            # Write to temp file first, then rename for atomicity
            temp_file = self.cache_file.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f)

            temp_file.rename(self.cache_file)
            logger.debug(f"Saved hash cache to disk: {len(cache_data)} files")

        except PermissionError:
            # Can't write to repo directory (e.g., system packages) - skip silently
            logger.debug(
                f"Cannot save hash cache (permission denied): {self.cache_dir}"
            )
        except Exception as e:
            logger.warning(f"Failed to save cache to disk: {e}")

    def on_created(self, event: FileCreatedEvent | DirCreatedEvent) -> None:
        """Handle file/directory creation events."""
        if event.is_directory:
            return

        file_path = Path(event.src_path)
        if self._should_ignore(file_path):
            return

        rel_path = self._get_relative_path(file_path)
        file_hash = self._compute_hash(file_path)
        self._update_hash_cache(rel_path, file_hash)

        with self._pending_lock:
            self._pending_changes[rel_path] = FileChange(
                path=file_path,
                action="add",
                new_hash=file_hash,
            )

        self._schedule_flush()

    def on_modified(self, event: FileModifiedEvent | DirModifiedEvent) -> None:
        """Handle file/directory modification events."""
        logger.debug(f"on_modified event received: {event.src_path}")
        if event.is_directory:
            return

        file_path = Path(event.src_path)
        logger.debug(f"Processing modification for: {file_path}")
        if self._should_ignore(file_path):
            logger.debug(f"Ignoring file (matches ignore pattern): {file_path}")
            return

        # Ensure cache is initialized before comparing hashes
        self._ensure_cache_initialized()

        rel_path = self._get_relative_path(file_path)
        old_hash = self._get_cached_hash(rel_path)
        new_hash = self._compute_hash(file_path)

        # Only report if hash actually changed
        if old_hash and old_hash == new_hash:
            return

        self._update_hash_cache(rel_path, new_hash)

        with self._pending_lock:
            self._pending_changes[rel_path] = FileChange(
                path=file_path,
                action="modify" if old_hash else "add",
                old_hash=old_hash,
                new_hash=new_hash,
            )

        self._schedule_flush()

    def on_deleted(self, event: FileDeletedEvent | DirDeletedEvent) -> None:
        """Handle file/directory deletion events."""
        if event.is_directory:
            return

        file_path = Path(event.src_path)
        if self._should_ignore(file_path):
            return

        # Ensure cache is initialized before getting old hash
        self._ensure_cache_initialized()

        rel_path = self._get_relative_path(file_path)
        old_hash = self._get_cached_hash(rel_path)
        self._update_hash_cache(rel_path, "")  # Remove from cache

        with self._pending_lock:
            self._pending_changes[rel_path] = FileChange(
                path=file_path,
                action="delete",
                old_hash=old_hash,
            )

        self._schedule_flush()

    def on_moved(self, event: FileMovedEvent | DirMovedEvent) -> None:
        """Handle file/directory move events."""
        if event.is_directory:
            return

        src_path = Path(event.src_path)
        dst_path = Path(event.dest_path)

        # Ensure cache is initialized before getting old hash
        self._ensure_cache_initialized()

        # Treat as delete of old location + create of new location
        if not self._should_ignore(src_path):
            src_rel = self._get_relative_path(src_path)
            old_hash = self._get_cached_hash(src_rel)
            self._update_hash_cache(src_rel, "")

            with self._pending_lock:
                self._pending_changes[src_rel] = FileChange(
                    path=src_path,
                    action="delete",
                    old_hash=old_hash,
                )

        if not self._should_ignore(dst_path):
            dst_rel = self._get_relative_path(dst_path)
            new_hash = self._compute_hash(dst_path)
            self._update_hash_cache(dst_rel, new_hash)

            with self._pending_lock:
                self._pending_changes[dst_rel] = FileChange(
                    path=dst_path,
                    action="add",
                    new_hash=new_hash,
                )

        self._schedule_flush()

    def start(self) -> None:
        """Start monitoring the file system.

        If subdirs is set, only those subdirectories are watched.
        Otherwise, the entire repo_path is watched recursively.

        Note: Hash cache is lazily initialized when first needed (e.g., when
        file changes are detected). This allows fast startup when only Git
        operations are needed.
        """
        if self._is_running:
            logger.warning("Watcher is already running")
            return

        logger.info(f"Starting file watcher for {self.repo_path}")
        if self.use_polling:
            logger.info("Using PollingObserver (for NFS/network filesystems)")
            self._observer = PollingObserver(timeout=2.0)
        else:
            self._observer = Observer()

        if self.subdirs:
            # Only watch specified subdirectories
            for subdir_name in self.subdirs:
                subdir_path = self.repo_path / subdir_name
                try:
                    if subdir_path.exists() and subdir_path.is_dir():
                        self._observer.schedule(self, str(subdir_path), recursive=True)
                        logger.info(f"  Watching subdir: {subdir_name}")
                    else:
                        logger.warning(f"  Subdir not found, skipping: {subdir_name}")
                except (PermissionError, OSError) as e:
                    logger.warning(f"  Cannot access subdir {subdir_name}: {e}")
        else:
            # Watch entire repo
            self._observer.schedule(self, str(self.repo_path), recursive=True)

        self._observer.start()
        self._is_running = True
        self._is_paused = False
        logger.info(
            f"Observer started: type={type(self._observer).__name__}, is_alive={self._observer.is_alive()}"
        )
        # Hash cache will be lazily initialized when first needed

        # Start Git HEAD watcher if callback is provided and .git exists
        self._start_git_head_watcher()

    def stop(self) -> None:
        """Stop monitoring the file system."""
        if not self._is_running:
            return

        logger.info("Stopping file watcher")
        self._is_running = False

        # Cancel any pending debounce timer
        if self._debounce_timer is not None:
            self._debounce_timer.cancel()
            self._debounce_timer = None

        # Cancel any pending save timer and save immediately (if cache was initialized)
        if self._save_timer is not None:
            self._save_timer.cancel()
            self._save_timer = None

        # Save cache to disk before stopping (only if initialized)
        if self._cache_initialized:
            self._cache_dirty = True  # Force save
            self._save_cache_to_disk()

        # Flush any pending changes
        self._flush_pending()

        # Stop observer
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5.0)
            self._observer = None

        # Stop Git HEAD watcher
        self._stop_git_head_watcher()

    def pause(self) -> None:
        """Pause monitoring (used during Git checkout)."""
        if not self._is_running:
            return
        logger.debug("Pausing file watcher")
        self._is_paused = True

    def resume(self) -> None:
        """Resume monitoring after pause."""
        if not self._is_running:
            return
        logger.debug("Resuming file watcher")
        self._is_paused = False

    def _iter_files(self):
        """Iterate over all files respecting subdirs filter.

        If subdirs is set, only iterate files within those subdirectories.
        Otherwise, iterate all files under repo_path.
        """
        return self._iter_files_static(self.repo_path, self.subdirs)

    def rebuild_hash_cache(self) -> None:
        """Rebuild hash cache by scanning all files.

        Uses walk_files() for fast file discovery (git ls-files or os.walk
        with pruning) instead of safe_rglob_iter which traverses .git etc.
        """
        from core.fs_utils import walk_files

        logger.info("Rebuilding hash cache...")
        total_start = time.perf_counter()

        # walk_files prunes obviously ignored directories; _should_ignore keeps
        # watcher semantics for binary files and custom ignore patterns.
        discover_start = time.perf_counter()
        candidate_files = walk_files(
            self.repo_path,
            self.ignore_patterns,
            gitignore_parser=self._gitignore_parser,
            subdirs=self.subdirs,
        )
        discover_ms = (time.perf_counter() - discover_start) * 1000

        filter_start = time.perf_counter()
        files_to_hash = [fp for fp in candidate_files if not self._should_ignore(fp)]
        filter_ms = (time.perf_counter() - filter_start) * 1000

        new_cache, hash_stats, slowest = self._hash_files_with_stats(
            files_to_hash, self.repo_path
        )

        with self._hash_cache_lock:
            self._hash_cache = new_cache
            self._cache_dirty = True

        # Mark as initialized
        self._cache_initialized = True

        save_start = time.perf_counter()

        # Save to disk immediately after rebuild
        self._save_cache_to_disk()
        write_ms = (time.perf_counter() - save_start) * 1000
        total_ms = (time.perf_counter() - total_start) * 1000
        logger.info(f"Hash cache built: {len(new_cache)} files in {total_ms:.1f}ms")
        self._log_hash_cache_timing(
            "Hash cache rebuild",
            gitignore_load_ms=0.0,
            discover_ms=discover_ms,
            filter_ms=filter_ms,
            hash_ms=float(hash_stats["hash_ms"]),
            write_ms=write_ms,
            total_ms=total_ms,
            candidate_count=len(candidate_files),
            hashed_count=len(new_cache),
            bytes_hashed=int(hash_stats["bytes_hashed"]),
            max_workers=int(hash_stats["max_workers"]),
            slowest=slowest,
        )

    def detect_all_changes(self) -> list[FileChange]:
        """Detect all changes by comparing current state with hash cache.

        Uses walk_files() for fast file discovery instead of safe_rglob_iter.

        Returns:
            List of FileChange objects representing all detected changes
        """
        from core.fs_utils import walk_files

        logger.info("detect_all_changes: starting...")
        # Ensure cache is initialized before comparing
        self._ensure_cache_initialized()
        logger.info("detect_all_changes: cache initialized")

        changes: list[FileChange] = []
        current_files: set[str] = set()

        # Scan current files using walk_files (git ls-files or os.walk with pruning)
        logger.info("detect_all_changes: scanning files...")
        scan_start_time = time.time()
        candidate_files = walk_files(
            self.repo_path,
            self.ignore_patterns,
            gitignore_parser=self._gitignore_parser,
            subdirs=self.subdirs,
        )
        all_files = [fp for fp in candidate_files if not self._should_ignore(fp)]

        for file_path in all_files:
            rel_path = self._get_relative_path(file_path)
            current_files.add(rel_path)

            old_hash = self._get_cached_hash(rel_path)
            new_hash = self._compute_hash(file_path)

            if old_hash is None:
                # New file
                changes.append(
                    FileChange(path=file_path, action="add", new_hash=new_hash)
                )
                self._update_hash_cache(rel_path, new_hash)
            elif old_hash != new_hash:
                # Modified file
                changes.append(
                    FileChange(
                        path=file_path,
                        action="modify",
                        old_hash=old_hash,
                        new_hash=new_hash,
                    )
                )
                self._update_hash_cache(rel_path, new_hash)

        scan_duration = time.time() - scan_start_time
        logger.info(
            f"detect_all_changes: scanned {len(all_files)} files in {scan_duration:.2f}s, found {len(current_files)} valid files, {len(changes)} changes"
        )

        # Check for deleted files
        deleted_count = 0
        with self._hash_cache_lock:
            for rel_path in list(self._hash_cache.keys()):
                if rel_path not in current_files:
                    file_path = self.repo_path / rel_path
                    old_hash = self._get_cached_hash(rel_path)
                    changes.append(
                        FileChange(path=file_path, action="delete", old_hash=old_hash)
                    )
                    self._update_hash_cache(rel_path, "")  # Remove from cache
                    deleted_count += 1

        logger.info(
            f"detect_all_changes: completed with {len(changes)} total changes ({deleted_count} deleted)"
        )
        return changes

    @property
    def is_running(self) -> bool:
        """Whether the watcher is currently running."""
        return self._is_running

    @property
    def is_paused(self) -> bool:
        """Whether the watcher is currently paused."""
        return self._is_paused

    def _start_git_head_watcher(self) -> None:
        """Start the Git HEAD watcher if conditions are met."""
        if self.on_git_ref_change is None:
            return

        git_dir = self.repo_path / ".git"
        if not git_dir.exists() or not git_dir.is_dir():
            logger.debug("No .git directory found, skipping Git HEAD watcher")
            return

        try:
            self._git_head_watcher = GitHeadWatcher(
                git_dir=git_dir,
                on_ref_change=self.on_git_ref_change,
                debounce_delay=0.5,  # Short delay for quick detection
            )

            self._git_observer = Observer()
            self._git_observer.schedule(
                self._git_head_watcher,
                str(git_dir),
                recursive=False,  # Only watch .git directory, not subdirs
            )
            self._git_observer.start()
            logger.info("Git HEAD watcher started for branch switch detection")
        except Exception as e:
            logger.warning(f"Failed to start Git HEAD watcher: {e}")
            self._git_head_watcher = None
            self._git_observer = None

    def _stop_git_head_watcher(self) -> None:
        """Stop the Git HEAD watcher."""
        if self._git_head_watcher is not None:
            self._git_head_watcher.cancel()
            self._git_head_watcher = None

        if self._git_observer is not None:
            try:
                self._git_observer.stop()
                self._git_observer.join(timeout=2.0)
            except Exception as e:
                logger.debug(f"Error stopping Git HEAD observer: {e}")
            self._git_observer = None

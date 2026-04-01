# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.config import IGNORE_PATTERNS
from loguru import logger
from tree_sitter import Parser

from .git_manager import GitManager
from .models import FileChange, GitRef, UpdateResult
from .queue import UpdateQueue
from .updater import IncrementalUpdater, ProgressCallback
from .watcher import ChangeWatcher


class RepoSyncManager:
    """Repository sync manager that integrates all sync components.

    This is the main entry point for incremental sync functionality.
    It coordinates file watching, Git operations, and incremental updates.

    Example:
        sync_mgr = RepoSyncManager(
            repo_path=Path("/path/to/repo"),
            project_name="myproject",
            ingestor=MemgraphIngestor(...),
            parsers={"python": parser, ...},
            queries={"python": {...}, ...},
            auto_start=True,
        )

        # Manual sync
        result = sync_mgr.sync_now()

        # Git operations
        branches = sync_mgr.list_branches()
        result = sync_mgr.checkout("develop")
    """

    def __init__(
        self,
        repo_path: Path,
        project_name: str,
        ingestor: Any,  # MemgraphIngestor
        parsers: dict[str, Parser],
        queries: dict[str, Any],
        language_objects: dict[str, Any] | None = None,
        auto_start: bool = True,
        debounce_delay: float = 2.0,
        ignore_patterns: set[str] | None = None,
        progress_callback: ProgressCallback = None,
        skip_embeddings: bool = False,
        embedding_granularity: str = "class",
        track_variables: bool = True,
        subdirs: set[str] | None = None,
        async_embeddings: bool = False,
        use_polling: bool = False,
    ):
        """Initialize the repository sync manager.

        Args:
            repo_path: Repository root path
            project_name: Project name for qualified names
            ingestor: MemgraphIngestor instance for database operations
            parsers: Tree-sitter parser dictionary
            queries: Language query configuration dictionary
            language_objects: Optional Tree-sitter Language objects for ParserPool-backed full rebuilds
            auto_start: Whether to auto-start file watching
            debounce_delay: Delay in seconds for change debouncing
            ignore_patterns: Directory/file patterns to ignore
            progress_callback: Optional callback for progress updates
            skip_embeddings: If True, skip embedding generation for new nodes
            embedding_granularity: "class" (Class+Function only) or "method" (all methods)
            track_variables: If True, track module/class-level variables in the graph
            subdirs: Optional set of subdirectory names to watch. If provided,
                     only these subdirectories under repo_path are monitored.
            async_embeddings: If True, generate embeddings asynchronously in background
            use_polling: If True, use polling observer instead of inotify (for NFS/network filesystems)
        """
        self.repo_path = Path(repo_path)
        self.project_name = project_name
        self.ingestor = ingestor
        self.parsers = parsers
        self.queries = queries
        self.language_objects = language_objects
        self.progress_callback = progress_callback

        # Initialize components
        self.git_manager = GitManager(repo_path)

        self.updater = IncrementalUpdater(
            ingestor=ingestor,
            repo_path=repo_path,
            project_name=project_name,
            parsers=parsers,
            queries=queries,
            progress_callback=progress_callback,
            skip_embeddings=skip_embeddings,
            embedding_granularity=embedding_granularity,
            track_variables=track_variables,
            async_embeddings=async_embeddings,
        )

        self.update_queue = UpdateQueue(
            self.updater, on_complete=self._handle_update_complete
        )

        ignore_patterns = ignore_patterns or IGNORE_PATTERNS

        self.watcher = ChangeWatcher(
            repo_path=repo_path,
            on_changes=self._handle_watcher_changes,
            debounce_delay=debounce_delay,
            ignore_patterns=ignore_patterns,
            on_git_ref_change=None,
            subdirs=subdirs,
            use_polling=use_polling,
            project_name=project_name,
        )

        # Track current git ref for change detection (graceful degradation if git fails)
        try:
            self._last_known_ref = self.git_manager.get_current_ref()
        except Exception as e:
            logger.warning(f"Could not get git ref (git may not be accessible): {e}")
            self._last_known_ref = None

        # Sync history (most recent 50 results)
        self._sync_history: deque[UpdateResult] = deque(maxlen=50)

        # Current file being processed (set by updater progress)
        self._current_processing_file: str | None = None

        # Status change callback (for WebSocket broadcasting)
        self._on_status_change: Callable[[RepoSyncManager], None] | None = None

        # Auto-start if requested
        if auto_start:
            self.start_watching()

    def set_progress_callback(self, callback: ProgressCallback) -> None:
        """Set or update the progress callback.

        Args:
            callback: Callback function for progress updates
        """
        self.progress_callback = callback
        self.updater.progress_callback = callback

    def set_on_status_change(
        self, callback: Callable[["RepoSyncManager"], None] | None
    ) -> None:
        """Set callback triggered when sync status changes (for WebSocket broadcasting).

        Args:
            callback: Callback receiving the manager instance, or None to clear
        """
        self._on_status_change = callback

    def _notify_status_change(self) -> None:
        """Notify listeners of a status change."""
        if self._on_status_change:
            try:
                self._on_status_change(self)
            except Exception as e:
                logger.debug(f"Status change callback error: {e}")

    def _handle_watcher_changes(self, changes: list[FileChange]) -> None:
        """Enqueue debounced watcher changes for incremental processing."""
        if not changes:
            return

        logger.info(f"Watcher produced {len(changes)} debounced changes")
        self.update_queue.enqueue(changes)
        self._notify_status_change()

    def _handle_update_complete(self, result: UpdateResult) -> None:
        """Persist queue results in manager history."""
        self._sync_history.append(result)
        self._notify_status_change()

    @property
    def current_processing_file(self) -> str | None:
        """File currently being processed."""
        return self._current_processing_file

    @current_processing_file.setter
    def current_processing_file(self, value: str | None) -> None:
        self._current_processing_file = value

    def get_pending_files(self) -> list[dict[str, str]]:
        """Get list of pending file changes.

        Returns:
            List of dicts with 'path' and 'action' keys
        """
        changes = self.watcher.detect_all_changes()
        result = []
        for change in changes:
            try:
                rel_path = str(change.path.relative_to(self.repo_path))
            except ValueError:
                rel_path = str(change.path)
            result.append(
                {
                    "path": rel_path,
                    "action": change.action,
                }
            )
        return result

    def get_history(self, limit: int = 20) -> list[UpdateResult]:
        """Get sync history (most recent first).

        Args:
            limit: Maximum number of history items to return

        Returns:
            List of UpdateResult objects, newest first
        """
        items = list(self._sync_history)
        items.reverse()
        return items[:limit]

    def _fallback_sync(self) -> None:
        """Fallback sync when git diff is not available."""
        logger.info("Using fallback sync (detect all changes)")
        self.watcher.rebuild_hash_cache()
        changes = self.watcher.detect_all_changes()
        if changes:
            logger.info(f"Fallback sync detected {len(changes)} changes")
            self.update_queue.enqueue(changes)

    # === Monitoring Control ===

    def start_watching(self, initial_sync: bool = False) -> None:
        """Start real-time file monitoring.

        Args:
            initial_sync: If True, detect and sync offline changes in background
        """
        if self.watcher.is_running:
            logger.info("Watcher is already running")
            return

        logger.info(f"Starting file watcher for {self.repo_path}")
        self.watcher.start()
        self.update_queue.start()
        logger.info(f"File watcher started. is_watching={self.is_watching}")

        if initial_sync:
            # Run initial sync in background thread to avoid blocking
            import threading

            def _do_initial_sync():
                logger.info("Performing initial sync to detect offline changes...")
                result = self.sync_now()
                if result.total_changes > 0:
                    logger.info(
                        f"Initial sync completed: {result.total_changes} changes applied"
                    )
                else:
                    logger.info("Initial sync completed: no offline changes detected")

            thread = threading.Thread(target=_do_initial_sync, daemon=True)
            thread.start()
            logger.info("Initial sync started in background thread")

        self._notify_status_change()

    def stop_watching(self) -> None:
        """Stop file monitoring."""
        if not self.watcher.is_running:
            logger.debug("stop_watching called but watcher is not running")
            return

        logger.info(f"Stopping file watcher for {self.repo_path}")
        self.watcher.stop()
        self.update_queue.stop()
        logger.info(f"File watcher stopped. is_watching={self.is_watching}")
        self._notify_status_change()

    # === Sync Operations ===

    def sync_now(self) -> UpdateResult:
        """Manually trigger sync (detect all changes and update).

        Returns:
            UpdateResult with statistics about the sync
        """
        logger.info("Manual sync triggered")

        logger.info("Performing full scan...")
        changes = self.watcher.detect_all_changes()

        if not changes:
            logger.info("No changes detected")
            return UpdateResult()

        logger.info(f"Applying {len(changes)} changes...")
        # Apply changes directly (bypass queue for immediate sync)
        result = self.updater.apply_changes(changes)

        # Store in history
        self._sync_history.append(result)
        self._notify_status_change()

        logger.info(f"Sync completed: {result}")
        return result

    def checkout(self, ref: str, force: bool = False) -> UpdateResult:
        """Switch Git version with incremental graph update.

        Process:
        1. Pause watcher
        2. Get diff and execute checkout
        3. If changes are large (>500 files), use full rebuild instead of incremental
        4. Apply changes to graph
        5. Rebuild hash cache
        6. Resume watcher

        Args:
            ref: Branch name, tag name, or commit SHA
            force: If True, discard local changes before checkout

        Returns:
            UpdateResult with statistics about the update
        """
        if not self.git_manager.is_git_repo:
            logger.warning("Cannot checkout: not a Git repository")
            return UpdateResult(errors=["Not a Git repository"])

        logger.info(f"Checking out Git ref: {ref} (force={force})")

        # Pause watcher
        self.watcher.pause()

        try:
            # Create a wrapper progress callback that also updates after graph operations
            def checkout_progress_callback(progress: int, step: str, message: str):
                if self.progress_callback:
                    self.progress_callback(progress, step, message)

            # Get diff and checkout (with progress callback)
            try:
                changes = self.git_manager.checkout(
                    ref, force=force, progress_callback=checkout_progress_callback
                )
            except RuntimeError as e:
                # If checkout failed due to local changes, try with force
                error_msg = str(e)
                if "local changes" in error_msg or "would be overwritten" in error_msg:
                    logger.info("Local changes detected, retrying with force=True")
                    if self.progress_callback:
                        self.progress_callback(
                            15, "git_reset", "Local changes detected, discarding..."
                        )
                    changes = self.git_manager.checkout(
                        ref, force=True, progress_callback=checkout_progress_callback
                    )
                else:
                    raise

            if not changes:
                logger.info("No file changes from Git checkout")
                # No changes, cache should still be valid (no rebuild needed)
                self.watcher.resume()
                result = UpdateResult()
                self._sync_history.append(result)
                self._notify_status_change()
                return result

            # Threshold for switching to full rebuild
            # When >500 files change, incremental update is slower than full rebuild
            LARGE_CHANGE_THRESHOLD = 500
            change_count = len(changes)

            if change_count > LARGE_CHANGE_THRESHOLD:
                logger.info(
                    f"Large change detected ({change_count} files), using full rebuild instead of incremental update"
                )
                if self.progress_callback:
                    self.progress_callback(
                        60,
                        "full_rebuild",
                        f"Large change ({change_count} files), doing full rebuild...",
                    )

                result = self._full_rebuild()

                # Full rebuild - need complete cache rebuild
                if self.progress_callback:
                    self.progress_callback(
                        95, "rebuilding_cache", "Rebuilding file cache..."
                    )

                self.watcher.rebuild_hash_cache()

                self._sync_history.append(result)
                self._notify_status_change()
                return result

            # Apply graph updates (these have their own progress tracking)
            if self.progress_callback:
                self.progress_callback(
                    70, "updating_graph", "Updating knowledge graph..."
                )

            result = self.updater.apply_changes(changes)

            if self.progress_callback:
                self.progress_callback(95, "syncing_cache", "Syncing file cache...")

            self.watcher.rebuild_hash_cache()

            self._sync_history.append(result)
            self._notify_status_change()
            return result

        except Exception as e:
            error_msg = f"Failed to checkout '{ref}': {e}"
            logger.error(error_msg)
            err_result = UpdateResult(errors=[error_msg])
            self._sync_history.append(err_result)
            self._notify_status_change()
            return err_result

        finally:
            # Always resume watcher
            self.watcher.resume()
            # Update last known ref to prevent duplicate detection by GitHeadWatcher
            self._last_known_ref = self.git_manager.get_current_ref()

    def _full_rebuild(self) -> UpdateResult:
        """Perform a full graph rebuild.

        This is more efficient than incremental update when many files change.

        Returns:
            UpdateResult with statistics about the rebuild
        """
        import time as time_module

        from graph.updater import GraphUpdater

        logger.info("Starting full graph rebuild...")

        start_time = time_module.time()

        # Clear existing project data using Cypher
        logger.info(f"Clearing existing graph for project '{self.project_name}'...")
        clear_query = """
        MATCH (p:Project {name: $project_name})
        DETACH DELETE p
        """
        self.ingestor.execute_query(clear_query, {"project_name": self.project_name})
        logger.info(f"Cleared existing graph for project '{self.project_name}'")

        if self.progress_callback:
            self.progress_callback(65, "building_graph", "Building knowledge graph...")

        # Create a wrapper callback that adapts from 2-param to 3-param signature
        # GraphUpdater expects: (progress: int, message: str)
        # Our callback expects: (progress: int, step: str, message: str)
        def _progress_wrapper(progress: int, message: str):
            if self.progress_callback:
                # Extract step from message or use a default
                step = "Building"
                if "parsing" in message.lower():
                    step = "Parsing files"
                elif "embedding" in message.lower():
                    step = "Generating embeddings"
                elif "calls" in message.lower():
                    step = "Processing calls"
                self.progress_callback(progress, step, message)

        # Create a new GraphUpdater for this rebuild
        updater = GraphUpdater(
            ingestor=self.ingestor,
            repo_path=self.repo_path,
            project_name=self.project_name,
            parsers=self.parsers,
            queries=self.queries,
            skip_embeddings=self.updater.skip_embeddings,
            embedding_granularity=self.updater.embedding_granularity,
            progress_callback=_progress_wrapper,
            language_objects=self.language_objects,
            enable_parallel_parsing=True,
        )

        # Run full rebuild (use run() method, not build_full_graph)
        updater.run(force_full_build=True)

        # Get statistics from ingestor
        duration_ms = (time_module.time() - start_time) * 1000

        # Query the result stats
        stats_query = """
        MATCH (p:Project {name: $project_name})-[:CONTAINS_FILE]->(f:File)
        RETURN count(f) as file_count
        """
        result = self.ingestor.fetch_all(
            stats_query, {"project_name": self.project_name}
        )
        file_count = result[0]["file_count"] if result else 0

        return UpdateResult(
            added=file_count,
            modified=0,
            deleted=0,
            duration_ms=duration_ms,
        )

    # === Git Information Queries ===

    def list_branches(self, include_remote: bool = False) -> list[GitRef]:
        """List all Git branches.

        Args:
            include_remote: Whether to include remote branches

        Returns:
            List of GitRef objects for each branch
        """
        return self.git_manager.list_branches(include_remote=include_remote)

    def list_tags(self) -> list[GitRef]:
        """List all Git tags.

        Returns:
            List of GitRef objects for each tag
        """
        return self.git_manager.list_tags()

    def get_current_ref(self) -> GitRef | None:
        """Get the currently checked-out Git reference.

        Returns:
            GitRef for current branch/commit, or None
        """
        return self.git_manager.get_current_ref()

    def fetch_remote(self, remote: str = "origin") -> None:
        """Fetch updates from a remote.

        Args:
            remote: Remote name to fetch from
        """
        self.git_manager.fetch(remote)

    def pull(
        self, remote: str = "origin", branch: str | None = None
    ) -> UpdateResult:
        """Pull updates from remote and apply incremental graph update.

        Process:
        1. Pause watcher
        2. Fetch + merge from remote
        3. If changes are large (>500 files), use full rebuild
        4. Apply changes to graph
        5. Rebuild hash cache
        6. Resume watcher

        Args:
            remote: Remote name to pull from
            branch: Branch to pull (defaults to current branch)

        Returns:
            UpdateResult with statistics about the update
        """
        if not self.git_manager.is_git_repo:
            logger.warning("Cannot pull: not a Git repository")
            return UpdateResult(errors=["Not a Git repository"])

        logger.info(f"Pulling from {remote}/{branch or 'current branch'}")

        # Pause watcher
        self.watcher.pause()

        try:
            changes = self.git_manager.pull(
                remote=remote,
                branch=branch,
                progress_callback=self.progress_callback,
            )

            if not changes:
                logger.info("Pull: already up to date")
                self.watcher.resume()
                result = UpdateResult()
                self._sync_history.append(result)
                self._notify_status_change()
                return result

            # Threshold for switching to full rebuild
            LARGE_CHANGE_THRESHOLD = 500
            change_count = len(changes)

            if change_count > LARGE_CHANGE_THRESHOLD:
                logger.info(
                    f"Large pull ({change_count} files), using full rebuild"
                )
                if self.progress_callback:
                    self.progress_callback(
                        60,
                        "full_rebuild",
                        f"Large change ({change_count} files), doing full rebuild...",
                    )

                result = self._full_rebuild()

                if self.progress_callback:
                    self.progress_callback(
                        95, "rebuilding_cache", "Rebuilding file cache..."
                    )

                self.watcher.rebuild_hash_cache()

                self._sync_history.append(result)
                self._notify_status_change()
                return result

            # Apply incremental graph updates
            if self.progress_callback:
                self.progress_callback(
                    70, "updating_graph", "Updating knowledge graph..."
                )

            result = self.updater.apply_changes(changes)

            if self.progress_callback:
                self.progress_callback(95, "syncing_cache", "Syncing file cache...")

            self.watcher.rebuild_hash_cache()

            self._sync_history.append(result)
            self._notify_status_change()
            return result

        except Exception as e:
            error_msg = f"Failed to pull from '{remote}': {e}"
            logger.error(error_msg)
            err_result = UpdateResult(errors=[error_msg])
            self._sync_history.append(err_result)
            self._notify_status_change()
            return err_result

        finally:
            self.watcher.resume()
            self._last_known_ref = self.git_manager.get_current_ref()

    # === Status Queries ===

    @property
    def is_watching(self) -> bool:
        """Whether currently monitoring file changes."""
        return self.watcher.is_running

    @property
    def is_git_repo(self) -> bool:
        """Whether this is a Git repository."""
        return self.git_manager.is_git_repo

    @property
    def latest_update_result(self) -> UpdateResult | None:
        """Get the result of the most recent update."""
        return self.update_queue.latest_result

    def get_status(self) -> dict[str, Any]:
        """Get comprehensive sync status.

        Returns:
            Dictionary with status information
        """
        current_ref = self.get_current_ref()

        return {
            "is_watching": self.is_watching,
            "is_processing": self.update_queue.is_processing,
            "is_git_repo": self.is_git_repo,
            "current_ref": current_ref.name if current_ref else None,
            "current_ref_type": current_ref.ref_type if current_ref else None,
            "pending_changes": self.update_queue.pending_count,
            "latest_result": {
                "total_changes": self.latest_update_result.total_changes
                if self.latest_update_result
                else 0,
                "success": self.latest_update_result.success
                if self.latest_update_result
                else True,
                "duration_ms": self.latest_update_result.duration_ms
                if self.latest_update_result
                else 0,
            }
            if self.latest_update_result
            else None,
        }

    def __del__(self):
        """Cleanup on deletion."""
        try:
            self.stop_watching()
        except Exception:
            pass

# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import queue
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from loguru import logger

from .models import FileChange, UpdateResult

if TYPE_CHECKING:
    from .updater import IncrementalUpdater


class UpdateQueue:
    """Update queue with serial execution and change merging.

    Features:
    - Single background worker thread
    - Serial execution to avoid concurrent writes
    - Automatic change merging (if new changes arrive while processing)
    - Graceful shutdown support
    - Optional on_complete callback for history tracking

    Example:
        updater = IncrementalUpdater(...)
        update_queue = UpdateQueue(updater)

        # Enqueue changes
        changes = [FileChange(...), ...]
        update_queue.enqueue(changes)

        # Wait for completion
        update_queue.wait_completion(timeout=30.0)
    """

    def __init__(
        self,
        updater: "IncrementalUpdater",
        on_complete: "Callable[[UpdateResult], None] | None" = None,
    ):
        """Initialize the update queue.

        Args:
            updater: IncrementalUpdater instance to process changes
            on_complete: Optional callback called with UpdateResult after each update
        """
        self.updater = updater
        self.on_complete = on_complete
        self._queue: queue.Queue[list[FileChange] | None] = queue.Queue()
        self._worker_thread: threading.Thread | None = None
        self._is_running = False
        self._is_processing = False
        self._pending_count = 0
        self._lock = threading.Lock()
        self._latest_result: UpdateResult | None = None

    def _worker(self) -> None:
        """Worker thread that processes changes from the queue."""
        logger.info("Update queue worker started")

        while self._is_running:
            try:
                # Wait for changes with timeout to allow graceful shutdown
                changes = self._queue.get(timeout=0.5)

                # None is the shutdown signal
                if changes is None:
                    break

                # Process the changes
                self._process_changes(changes)

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error in update queue worker: {e}", exc_info=True)

        logger.info("Update queue worker stopped")

    def _process_changes(self, changes: list[FileChange]) -> None:
        """Process a batch of changes with merge support.

        If new changes arrive while processing, they will be merged
        and processed together in the next iteration.
        """
        with self._lock:
            self._is_processing = True
            self._pending_count = len(changes)

        try:
            # Merge with any additional changes that arrived
            additional_changes = self._drain_additional_changes()
            if additional_changes:
                changes = self._merge_changes(changes, additional_changes)
                logger.info(f"Merged {len(additional_changes)} additional changes")

            # Apply changes via updater
            result = self.updater.apply_changes(changes)

            with self._lock:
                self._latest_result = result
                self._pending_count = 0

            # Invoke on_complete callback for history tracking
            if self.on_complete:
                try:
                    self.on_complete(result)
                except Exception as e:
                    logger.warning(f"on_complete callback error: {e}")

            logger.info(
                f"Update completed: {result.added} added, "
                f"{result.modified} modified, {result.deleted} deleted, "
                f"in {result.duration_ms:.0f}ms"
            )

            if result.errors:
                logger.warning(f"Update completed with {len(result.errors)} errors")

        except Exception as e:
            logger.error(f"Error processing changes: {e}", exc_info=True)
        finally:
            with self._lock:
                self._is_processing = False

    def _drain_additional_changes(self) -> list[FileChange]:
        """Drain any additional changes that arrived while processing."""
        additional: list[FileChange] = []

        while not self._queue.empty():
            try:
                changes = self._queue.get_nowait()
                if changes is not None:
                    additional.extend(changes)
            except queue.Empty:
                break

        return additional

    def _merge_changes(
        self, base: list[FileChange], additional: list[FileChange]
    ) -> list[FileChange]:
        """Merge two lists of changes, with newer changes taking precedence.

        Args:
            base: Original list of changes
            additional: New changes that arrived while processing

        Returns:
            Merged list of changes
        """
        merged_dict: dict[str, FileChange] = {}

        # First add base changes
        for change in base:
            key = str(change.path)
            merged_dict[key] = change

        # Then override with additional changes (newer wins)
        for change in additional:
            key = str(change.path)
            merged_dict[key] = change

        return list(merged_dict.values())

    def enqueue(self, changes: list[FileChange]) -> None:
        """Enqueue a batch of changes for processing.

        Args:
            changes: List of FileChange objects to process
        """
        if not changes:
            return

        if not self._is_running:
            logger.warning("Cannot enqueue changes: queue is not running")
            return

        with self._lock:
            self._pending_count += len(changes)

        self._queue.put(changes)

    def start(self) -> None:
        """Start the worker thread."""
        if self._is_running:
            logger.warning("Update queue is already running")
            return

        self._is_running = True
        self._worker_thread = threading.Thread(
            target=self._worker,
            name="UpdateQueueWorker",
            daemon=True,
        )
        self._worker_thread.start()

    def stop(self) -> None:
        """Stop the worker thread gracefully.

        Waits for current processing to complete before stopping.
        """
        if not self._is_running:
            return

        logger.info("Stopping update queue...")
        self._is_running = False

        # Send shutdown signal
        self._queue.put(None)

        # Wait for worker to finish
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=30.0)
            if self._worker_thread.is_alive():
                logger.warning("Update queue worker did not stop gracefully")
            self._worker_thread = None

    @property
    def is_processing(self) -> bool:
        """Whether the queue is currently processing changes."""
        with self._lock:
            return self._is_processing

    @property
    def pending_count(self) -> int:
        """Number of changes waiting to be processed."""
        with self._lock:
            return self._pending_count

    @property
    def is_running(self) -> bool:
        """Whether the queue is running."""
        return self._is_running

    @property
    def latest_result(self) -> UpdateResult | None:
        """Get the result of the most recent update."""
        with self._lock:
            return self._latest_result

# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Asynchronous embedding queue for non-blocking embedding generation.

This module provides a background worker that generates embeddings without
blocking the main incremental update flow. Nodes are marked with
embedding_status: pending and updated to 'complete' when embeddings are ready.
"""

import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class EmbeddingTask:
    """Task for embedding generation."""

    qualified_name: str
    node_type: str  # "Function", "Method", "Class"
    file_path: str  # Relative path
    start_line: int
    end_line: int
    priority: int = 0  # Lower = higher priority
    created_at: float = field(default_factory=time.time)

    def __lt__(self, other: "EmbeddingTask") -> bool:
        """Compare by priority for priority queue."""
        return self.priority < other.priority


class EmbeddingQueue:
    """Background queue for asynchronous embedding generation.

    This class decouples embedding generation from the main update flow:
    1. Main flow adds nodes to the queue (non-blocking)
    2. Background worker processes queue in batches
    3. Nodes are updated with embeddings when ready

    Usage:
        queue = EmbeddingQueue(
            ingestor=ingestor,
            repo_path=repo_path,
            project_name="myproject",
        )
        queue.start()

        # Add nodes for embedding (non-blocking)
        queue.enqueue([
            {"qualified_name": "pkg.module.func", "node_type": "Function", ...},
        ])

        # Later: stop and wait for completion
        queue.stop()
    """

    # Default batch size for embedding API calls
    DEFAULT_BATCH_SIZE = 100

    # Interval between batch processing attempts (seconds)
    PROCESS_INTERVAL = 2.0

    def __init__(
        self,
        ingestor: Any,  # MemgraphIngestor
        repo_path: Path,
        project_name: str,
        batch_size: int = DEFAULT_BATCH_SIZE,
        parallel_workers: int = 4,
        on_batch_complete: Callable[[int], None] | None = None,
    ):
        """Initialize the embedding queue.

        Args:
            ingestor: MemgraphIngestor instance for database operations
            repo_path: Repository root path
            project_name: Project name
            batch_size: Number of nodes to process per batch
            parallel_workers: Workers for source extraction
            on_batch_complete: Optional callback(count) when a batch completes
        """
        self.ingestor = ingestor
        self.repo_path = Path(repo_path)
        self.project_name = project_name
        self.batch_size = batch_size
        self.parallel_workers = parallel_workers
        self.on_batch_complete = on_batch_complete

        # Thread-safe queue for pending tasks
        self._queue: queue.Queue[EmbeddingTask] = queue.Queue()
        self._pending_count = 0
        self._pending_lock = threading.Lock()

        # Background worker
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._is_running = False

        # Statistics
        self._total_processed = 0
        self._total_failed = 0
        self._stats_lock = threading.Lock()

    def start(self) -> None:
        """Start the background worker thread."""
        if self._is_running:
            logger.warning("EmbeddingQueue is already running")
            return

        self._stop_event.clear()
        self._is_running = True

        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="EmbeddingQueueWorker",
            daemon=True,
        )
        self._worker_thread.start()
        logger.info("EmbeddingQueue started")

    def stop(self, wait: bool = True, timeout: float = 30.0) -> None:
        """Stop the background worker.

        Args:
            wait: If True, wait for pending items to be processed
            timeout: Maximum time to wait (seconds)
        """
        if not self._is_running:
            return

        logger.info(
            f"Stopping EmbeddingQueue (wait={wait}, pending={self._pending_count})"
        )

        self._stop_event.set()
        self._is_running = False

        if wait and self._worker_thread:
            self._worker_thread.join(timeout=timeout)
            if self._worker_thread.is_alive():
                logger.warning("EmbeddingQueue worker did not stop in time")

        self._worker_thread = None
        logger.info("EmbeddingQueue stopped")

    def enqueue(self, nodes: list[dict[str, Any]], priority: int = 0) -> int:
        """Add nodes to the embedding queue (non-blocking).

        Args:
            nodes: List of node info dicts with keys:
                   - qualified_name, node_type, file_path, start_line, end_line
            priority: Priority level (lower = higher priority)

        Returns:
            Number of nodes successfully queued
        """
        queued = 0
        for node in nodes:
            try:
                task = EmbeddingTask(
                    qualified_name=node["qualified_name"],
                    node_type=node.get("node_type", "Function"),
                    file_path=node.get("file_path", ""),
                    start_line=node.get("start_line", 0),
                    end_line=node.get("end_line", 0),
                    priority=priority,
                )
                self._queue.put(task)
                queued += 1
            except KeyError as e:
                logger.debug(f"Invalid node for embedding queue: missing {e}")

        with self._pending_lock:
            self._pending_count += queued

        if queued > 0:
            logger.debug(f"Queued {queued} nodes for embedding generation")

        return queued

    @property
    def pending_count(self) -> int:
        """Number of nodes waiting for embedding generation."""
        with self._pending_lock:
            return self._pending_count

    @property
    def is_running(self) -> bool:
        """Whether the background worker is running."""
        return self._is_running

    def get_stats(self) -> dict[str, int]:
        """Get queue statistics.

        Returns:
            Dict with pending, processed, and failed counts
        """
        with self._stats_lock:
            return {
                "pending": self.pending_count,
                "processed": self._total_processed,
                "failed": self._total_failed,
            }

    def _worker_loop(self) -> None:
        """Background worker loop that processes embedding batches."""
        logger.info("EmbeddingQueue worker started")

        while not self._stop_event.is_set():
            try:
                # Collect a batch of tasks
                batch: list[EmbeddingTask] = []
                deadline = time.time() + self.PROCESS_INTERVAL

                while len(batch) < self.batch_size and time.time() < deadline:
                    try:
                        task = self._queue.get(timeout=0.5)
                        batch.append(task)
                    except queue.Empty:
                        break

                    # Check stop event between gets
                    if self._stop_event.is_set():
                        break

                # Process the batch
                if batch:
                    processed, failed = self._process_batch(batch)

                    with self._pending_lock:
                        self._pending_count -= len(batch)

                    with self._stats_lock:
                        self._total_processed += processed
                        self._total_failed += failed

                    if self.on_batch_complete:
                        try:
                            self.on_batch_complete(processed)
                        except Exception as e:
                            logger.debug(f"on_batch_complete callback failed: {e}")

            except Exception as e:
                logger.error(f"EmbeddingQueue worker error: {e}")
                time.sleep(1.0)  # Backoff on error

        # Process remaining items before shutdown
        remaining: list[EmbeddingTask] = []
        while True:
            try:
                task = self._queue.get_nowait()
                remaining.append(task)
            except queue.Empty:
                break

        if remaining:
            logger.info(
                f"Processing {len(remaining)} remaining embedding tasks before shutdown"
            )
            processed, failed = self._process_batch(remaining)
            with self._stats_lock:
                self._total_processed += processed
                self._total_failed += failed

        logger.info("EmbeddingQueue worker stopped")

    def _process_batch(self, batch: list[EmbeddingTask]) -> tuple[int, int]:
        """Process a batch of embedding tasks.

        Args:
            batch: List of EmbeddingTask to process

        Returns:
            Tuple of (processed_count, failed_count)
        """
        if not batch:
            return 0, 0

        logger.debug(f"Processing embedding batch of {len(batch)} tasks")

        try:
            from core.config import settings
            from core.source_extraction import extract_source_with_fallback
            from graph.embedder import (
                embed_code_batch_for_repo,
            )

            # Phase 1: Extract source code in parallel
            def extract_source(task: EmbeddingTask) -> tuple[EmbeddingTask, str | None]:
                if not task.file_path or not task.start_line or not task.end_line:
                    return task, None

                full_path = self.repo_path / task.file_path
                if not full_path.exists():
                    return task, None

                source = extract_source_with_fallback(
                    full_path,
                    task.start_line,
                    task.end_line,
                    task.qualified_name,
                    None,
                )
                return task, source

            source_data: list[tuple[EmbeddingTask, str]] = []
            with ThreadPoolExecutor(max_workers=self.parallel_workers) as executor:
                futures = [executor.submit(extract_source, t) for t in batch]
                for future in as_completed(futures):
                    try:
                        task, source = future.result()
                        if source:
                            source_data.append((task, source))
                    except Exception as e:
                        logger.debug(f"Failed to extract source: {e}")

            if not source_data:
                logger.debug("No source code extracted for embedding batch")
                return 0, len(batch)

            # Phase 2: Generate embeddings
            codes = [source for _, source in source_data]
            embeddings = embed_code_batch_for_repo(
                codes,
                repo_name=self.project_name,
                parallel=True,
                max_concurrent=getattr(settings, "EMBEDDING_MAX_CONCURRENT", 10),
            )

            # Phase 3: Store embeddings by node type
            embeddings_by_type: dict[str, list[dict]] = {}
            for (task, _), embedding in zip(source_data, embeddings):
                if task.node_type not in embeddings_by_type:
                    embeddings_by_type[task.node_type] = []
                embeddings_by_type[task.node_type].append(
                    {
                        "qualified_name": task.qualified_name,
                        "embedding": embedding,
                    }
                )

            total_stored = 0
            for node_type, embeddings_data in embeddings_by_type.items():
                if embeddings_data:
                    stored = self.ingestor.update_embeddings_batch(
                        node_type, embeddings_data
                    )
                    total_stored += stored

            failed = len(batch) - len(source_data)
            logger.debug(
                f"Embedding batch complete: {total_stored} stored, {failed} failed"
            )

            return total_stored, failed

        except Exception as e:
            logger.warning(f"Failed to process embedding batch: {e}")
            return 0, len(batch)

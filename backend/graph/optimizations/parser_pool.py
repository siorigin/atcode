# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from loguru import logger
from tree_sitter import Language, Parser


class ParserPool:
    """Thread-safe pool of Tree-sitter parsers for parallel parsing.

    Tree-sitter parsers are not thread-safe, so this class creates multiple
    parser instances (one per worker thread) to enable safe parallel parsing.

    Usage:
        pool = ParserPool(language_objects, pool_size=8)

        # In a thread:
        with pool.get_parser("python") as parser:
            tree = parser.parse(source_bytes)
    """

    def __init__(
        self,
        language_objects: dict[str, Language],
        pool_size: int | None = None,
    ):
        """Initialize the parser pool.

        Args:
            language_objects: Dict mapping language names to Language objects
            pool_size: Number of parser instances per language (defaults to CPU count)
        """
        self.pool_size = pool_size or min(64, (os.cpu_count() or 4) * 2)
        self._languages = language_objects

        # Create parser pools: {language: [Parser, Parser, ...]}
        self._parsers: dict[str, list[Parser]] = {}
        # Locks for each parser slot: {language: [Lock, Lock, ...]}
        self._locks: dict[str, list[threading.Lock]] = {}
        # Track which slots are in use (for round-robin allocation)
        self._next_slot: dict[str, int] = {}
        self._slot_lock = threading.Lock()

        # Thread-local storage for thread-to-slot mapping
        self._thread_slots: threading.local = threading.local()

        self._initialize_pools()

    def _initialize_pools(self) -> None:
        """Initialize parser pools for all languages."""
        for lang_name, language in self._languages.items():
            self._parsers[lang_name] = []
            self._locks[lang_name] = []
            self._next_slot[lang_name] = 0

            for _ in range(self.pool_size):
                parser = Parser(language)
                self._parsers[lang_name].append(parser)
                self._locks[lang_name].append(threading.Lock())

            logger.debug(
                f"Created parser pool for {lang_name} with {self.pool_size} instances"
            )

    def _get_thread_slot(self, language: str) -> int:
        """Get or assign a slot for the current thread.

        Uses thread ID modulo pool size for deterministic assignment,
        which reduces lock contention compared to round-robin.
        """
        thread_id = threading.current_thread().ident or 0
        return thread_id % self.pool_size

    @contextmanager
    def get_parser(self, language: str) -> Iterator[Parser]:
        """Get a parser from the pool for the specified language.

        This context manager acquires a lock on a parser slot and yields
        the parser for use. The lock is released when the context exits.

        Args:
            language: The language name (e.g., "python", "javascript")

        Yields:
            A Parser instance for the specified language

        Raises:
            KeyError: If the language is not supported
        """
        if language not in self._parsers:
            raise KeyError(f"Language '{language}' not available in parser pool")

        # Get slot for this thread (deterministic based on thread ID)
        slot = self._get_thread_slot(language)
        lock = self._locks[language][slot]

        # Acquire the lock and yield the parser
        with lock:
            yield self._parsers[language][slot]

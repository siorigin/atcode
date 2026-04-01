# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

from loguru import logger


class FileLock:
    """
    Cross-platform file lock implementation.

    Usage:
        with FileLock('/path/to/file.json'):
            # File is locked, safe to read/write
            ...
        # Lock is automatically released
    """

    def __init__(
        self, file_path: str | Path, timeout: float = 10.0, check_interval: float = 0.1
    ):
        """
        Initialize file lock.

        Args:
            file_path: Path to the file to lock
            timeout: Maximum time to wait for lock (seconds)
            check_interval: Time between lock attempts (seconds)
        """
        self.file_path = Path(file_path)
        self.timeout = timeout
        self.check_interval = check_interval
        self.lock_file_path = self.file_path.with_suffix(
            self.file_path.suffix + ".lock"
        )
        self.lock_file = None
        self._is_locked = False

    def acquire(self) -> bool:
        """
        Acquire the file lock.

        Returns:
            True if lock acquired, False if timeout
        """
        start_time = time.time()
        attempt = 0

        while time.time() - start_time < self.timeout:
            attempt += 1
            try:
                # Try to create lock file exclusively
                # Use 'x' mode which fails if file exists
                self.lock_file = open(self.lock_file_path, "x")
                self.lock_file.write(f"{os.getpid()}\n{time.time()}\n")
                self.lock_file.flush()
                self._is_locked = True

                if attempt > 1:
                    logger.debug(
                        f"Acquired lock after {attempt} attempts: {self.lock_file_path}"
                    )
                else:
                    logger.debug(f"Acquired lock: {self.lock_file_path}")
                return True
            except FileExistsError:
                # Lock file exists, check if it's stale
                if self._is_stale_lock():
                    logger.warning(
                        f"Removing stale lock (attempt {attempt}): {self.lock_file_path}"
                    )
                    self._remove_lock_file()
                    # Immediately retry after removing stale lock
                    continue

                # Wait with exponential backoff (but cap at check_interval)
                wait_time = min(
                    self.check_interval * (1.1 ** min(attempt, 10)),
                    self.check_interval * 2,
                )
                time.sleep(wait_time)
            except Exception as e:
                logger.error(f"Unexpected error acquiring lock: {e}")
                time.sleep(self.check_interval)

        logger.error(
            f"Failed to acquire lock after {self.timeout}s ({attempt} attempts): {self.lock_file_path}"
        )
        return False

    def release(self) -> None:
        """Release the file lock."""
        if self._is_locked:
            try:
                if self.lock_file:
                    self.lock_file.close()
                self._remove_lock_file()
                self._is_locked = False
                logger.debug(f"Released lock: {self.lock_file_path}")
            except OSError as e:
                logger.error(f"Error releasing lock: {e}")

    def _is_stale_lock(self, max_age: float = 300.0) -> bool:
        """
        Check if lock file is stale (older than max_age seconds).

        Args:
            max_age: Maximum age of lock file in seconds

        Returns:
            True if lock is stale
        """
        try:
            if not self.lock_file_path.exists():
                return False

            # Check file modification time
            mtime = self.lock_file_path.stat().st_mtime
            age = time.time() - mtime

            if age > max_age:
                return True

            # Try to read PID and check if process exists
            with open(self.lock_file_path) as f:
                lines = f.readlines()
                if lines:
                    try:
                        pid = int(lines[0].strip())
                        # Check if process exists (Unix only)
                        if sys.platform != "win32":
                            try:
                                os.kill(pid, 0)  # Signal 0 just checks existence
                                return False  # Process exists, lock is valid
                            except OSError:
                                return True  # Process doesn't exist, lock is stale
                    except (ValueError, IndexError):
                        pass

            return False
        except Exception as e:
            logger.warning(f"Error checking stale lock: {e}")
            return False

    def _remove_lock_file(self) -> None:
        """Remove the lock file."""
        try:
            if self.lock_file_path.exists():
                self.lock_file_path.unlink()
        except OSError as e:
            logger.error(f"Error removing lock file: {e}")

    def __enter__(self) -> "FileLock":
        """Context manager entry."""
        if not self.acquire():
            raise TimeoutError(f"Could not acquire lock for {self.file_path}")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> bool:
        """Context manager exit."""
        self.release()
        return False


@contextmanager
def file_lock(
    file_path: str | Path, timeout: float = 10.0
) -> Generator[FileLock, None, None]:
    """Context manager for file locking.

    Usage:
        with file_lock('/path/to/file.json'):
            # File is locked
            ...

    Args:
        file_path: Path to file to lock
        timeout: Lock timeout in seconds

    Yields:
        The acquired FileLock instance.

    Raises:
        TimeoutError: If lock cannot be acquired
    """
    lock = FileLock(file_path, timeout=timeout)
    try:
        lock.acquire()
        yield lock
    finally:
        lock.release()


class AtomicFileWriter:
    """
    Atomic file writer with locking.

    Writes to a temporary file first, then atomically renames it.
    This prevents partial writes and corruption.
    """

    def __init__(self, file_path: str | Path, lock_timeout: float = 10.0):
        """
        Initialize atomic writer.

        Args:
            file_path: Target file path
            lock_timeout: Lock timeout in seconds
        """
        self.file_path = Path(file_path)
        self.temp_path = self.file_path.with_suffix(self.file_path.suffix + ".tmp")
        self.lock_timeout = lock_timeout

    @contextmanager
    def write(self) -> Generator[IO[str], None, None]:
        """Context manager for atomic writing.

        Usage:
            writer = AtomicFileWriter('/path/to/file.json')
            with writer.write() as f:
                json.dump(data, f)

        Yields:
            A file handle for writing content.
        """
        # Ensure parent directory exists
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

        # Acquire lock
        with FileLock(self.file_path, timeout=self.lock_timeout):
            try:
                # Write to temporary file
                with open(self.temp_path, "w", encoding="utf-8") as f:
                    yield f

                # Atomic rename
                self.temp_path.replace(self.file_path)
                logger.debug(f"Atomically wrote file: {self.file_path}")
            except Exception as e:
                # Clean up temp file on error
                if self.temp_path.exists():
                    self.temp_path.unlink()
                logger.error(f"Error in atomic write: {e}")
                raise

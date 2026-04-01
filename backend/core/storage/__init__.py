# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from .base import (
    AccessDeniedError,
    ConcurrentModificationError,
    SessionNotFoundError,
    StorageError,
    StorageInterface,
)
from .file_storage import FileStorage
from .user_manager import SessionValidator, UserManager

__all__ = [
    "StorageInterface",
    "StorageError",
    "SessionNotFoundError",
    "AccessDeniedError",
    "ConcurrentModificationError",
    "FileStorage",
    "UserManager",
    "SessionValidator",
]


def create_storage(backend: str = "file", **kwargs: str) -> StorageInterface:
    """Factory function to create storage backend.

    Args:
        backend: Storage backend type ('file', 'database', etc.)
        **kwargs: Backend-specific configuration

    Returns:
        Storage instance

    Raises:
        NotImplementedError: If the requested backend is not yet implemented.
        ValueError: If an unknown backend type is specified.

    Example:
        storage = create_storage('file', base_path='./wiki_chat')
    """
    if backend == "file":
        return FileStorage(**kwargs)
    if backend == "database":
        raise NotImplementedError("Database storage not yet implemented")
    raise ValueError(f"Unknown storage backend: {backend}")

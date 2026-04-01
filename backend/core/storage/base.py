# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from abc import ABC, abstractmethod
from typing import Any


class StorageInterface(ABC):
    """
    Abstract base class for storage backends.

    All storage implementations must inherit from this class and implement
    all abstract methods.
    """

    @abstractmethod
    async def save_session(
        self, session_id: str, user_id: str, repo_name: str, data: dict[str, Any]
    ) -> bool:
        """
        Save a chat session.

        Args:
            session_id: Unique session identifier
            user_id: User identifier
            repo_name: Repository name
            data: Session data (turns, metadata, etc.)

        Returns:
            True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def load_session(
        self, session_id: str, user_id: str
    ) -> dict[str, Any] | None:
        """
        Load a chat session.

        Args:
            session_id: Session identifier
            user_id: User identifier (for access control)

        Returns:
            Session data dict or None if not found
        """
        pass

    @abstractmethod
    async def append_turn(
        self, session_id: str, user_id: str, turn_data: dict[str, Any]
    ) -> bool:
        """
        Append a new turn to an existing session.

        Args:
            session_id: Session identifier
            user_id: User identifier
            turn_data: Turn data (query, response, references, etc.)

        Returns:
            True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def list_sessions(
        self,
        user_id: str,
        repo_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        List sessions for a user.

        Args:
            user_id: User identifier
            repo_name: Optional filter by repository
            limit: Maximum number of sessions to return
            offset: Pagination offset

        Returns:
            List of session metadata dicts
        """
        pass

    @abstractmethod
    async def delete_session(self, session_id: str, user_id: str) -> bool:
        """
        Delete a session.

        Args:
            session_id: Session identifier
            user_id: User identifier (for access control)

        Returns:
            True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def session_exists(self, session_id: str, user_id: str) -> bool:
        """
        Check if a session exists.

        Args:
            session_id: Session identifier
            user_id: User identifier

        Returns:
            True if session exists, False otherwise
        """
        pass

    @abstractmethod
    async def update_session_metadata(
        self, session_id: str, user_id: str, metadata: dict[str, Any]
    ) -> bool:
        """
        Update session metadata (e.g., updated_at timestamp).

        Args:
            session_id: Session identifier
            user_id: User identifier
            metadata: Metadata to update

        Returns:
            True if successful, False otherwise
        """
        pass


class StorageError(Exception):
    """Base exception for storage errors."""

    pass


class SessionNotFoundError(StorageError):
    """Raised when a session is not found."""

    pass


class AccessDeniedError(StorageError):
    """Raised when access to a session is denied."""

    pass


class ConcurrentModificationError(StorageError):
    """Raised when concurrent modification is detected."""

    pass

# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import uuid
from datetime import UTC, datetime


class UserManager:
    """
    Manages user identification and session ownership.

    Features:
    - Generate anonymous user IDs
    - Validate user-session associations
    - Support for future authentication integration
    """

    @staticmethod
    def generate_anonymous_id(request_info: dict | None = None) -> str:
        """
        Generate a stable anonymous user ID.

        Args:
            request_info: Optional dict with IP, user-agent, etc.

        Returns:
            Anonymous user ID (e.g., "anon-abc123def456")
        """
        if request_info:
            # Generate stable ID based on IP + User-Agent
            ip = request_info.get("ip", "")
            user_agent = request_info.get("user_agent", "")

            # Hash to create stable anonymous ID
            content = f"{ip}:{user_agent}"
            hash_value = hashlib.sha256(content.encode()).hexdigest()[:12]
            return f"anon-{hash_value}"
        else:
            # Generate random anonymous ID
            return f"anon-{uuid.uuid4().hex[:12]}"

    @staticmethod
    def generate_user_id(username: str) -> str:
        """
        Generate user ID from username (for future auth integration).

        Args:
            username: User's username

        Returns:
            User ID (e.g., "user-abc123")
        """
        hash_value = hashlib.sha256(username.encode()).hexdigest()[:12]
        return f"user-{hash_value}"

    @staticmethod
    def create_user_session_id(user_id: str, session_id: str) -> str:
        """
        Create a user-scoped session ID.

        Args:
            user_id: User identifier
            session_id: Original session ID

        Returns:
            User-scoped session ID (e.g., "user-abc123__session-xyz789")
        """
        return f"{user_id}__{session_id}"

    @staticmethod
    def parse_user_session_id(user_session_id: str) -> tuple[str, str]:
        """
        Parse user-scoped session ID back to components.

        Args:
            user_session_id: User-scoped session ID

        Returns:
            Tuple of (user_id, session_id)

        Raises:
            ValueError: If format is invalid
        """
        if "__" not in user_session_id:
            raise ValueError(f"Invalid user session ID format: {user_session_id}")

        parts = user_session_id.split("__", 1)
        return parts[0], parts[1]

    @staticmethod
    def validate_session_ownership(user_session_id: str, user_id: str) -> bool:
        """
        Validate that a session belongs to a user.

        Args:
            user_session_id: User-scoped session ID
            user_id: User identifier to validate

        Returns:
            True if session belongs to user, False otherwise
        """
        try:
            session_user_id, _ = UserManager.parse_user_session_id(user_session_id)
            return session_user_id == user_id
        except ValueError:
            return False


class SessionValidator:
    """
    Validates session access and permissions.
    """

    @staticmethod
    def can_access_session(
        user_id: str, session_id: str, allow_anonymous: bool = True
    ) -> tuple[bool, str | None]:
        """
        Check if a user can access a session.

        Args:
            user_id: User identifier
            session_id: Session ID to access
            allow_anonymous: Whether to allow anonymous users

        Returns:
            Tuple of (can_access, error_message)
        """
        # Check if anonymous access is allowed
        if user_id.startswith("anon-") and not allow_anonymous:
            return False, "Anonymous access not allowed"

        # Validate session ownership
        if "__" in session_id:
            # User-scoped session
            if not UserManager.validate_session_ownership(session_id, user_id):
                return False, "Session does not belong to user"

        return True, None

    @staticmethod
    def create_session_metadata(user_id: str, session_id: str, repo_name: str) -> dict:
        """
        Create metadata for a new session.

        Args:
            user_id: User identifier
            session_id: Session ID
            repo_name: Repository name

        Returns:
            Session metadata dict
        """
        return {
            "user_id": user_id,
            "session_id": session_id,
            "repo_name": repo_name,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "is_anonymous": user_id.startswith("anon-"),
        }

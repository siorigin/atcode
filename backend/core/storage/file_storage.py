# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.file_lock import AtomicFileWriter, FileLock
from loguru import logger

from .base import (
    AccessDeniedError,
    ConcurrentModificationError,
    SessionNotFoundError,
    StorageInterface,
)


class FileStorage(StorageInterface):
    """
    File-based storage with atomic writes and locking.

    Directory structure:
        base_path/
            {repo_name}/
                {user_id}__{session_id}.json
                .sessions/
                    metadata.json
    """

    def __init__(self, base_path: str | Path = "./wiki_chat"):
        """
        Initialize file storage.

        Args:
            base_path: Base directory for storing sessions
        """
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"FileStorage initialized: {self.base_path}")

    def _get_session_path(self, session_id: str, user_id: str, repo_name: str) -> Path:
        """Get the file path for a session."""
        repo_dir = self.base_path / repo_name
        repo_dir.mkdir(parents=True, exist_ok=True)

        # Use user-scoped filename
        filename = f"{user_id}__{session_id}.json"
        return repo_dir / filename

    def _parse_session_filename(self, filename: str) -> tuple[str, str] | None:
        """
        Parse session filename to extract user_id and session_id.

        Returns:
            Tuple of (user_id, session_id) or None if invalid format
        """
        if not filename.endswith(".json"):
            return None

        name = filename[:-5]  # Remove .json
        if "__" not in name:
            # Old format without user_id, treat as anonymous
            return "anon-legacy", name

        parts = name.split("__", 1)
        return parts[0], parts[1]

    async def save_session(
        self, session_id: str, user_id: str, repo_name: str, data: dict[str, Any]
    ) -> bool:
        """Save a complete session."""
        try:
            file_path = self._get_session_path(session_id, user_id, repo_name)

            # Add metadata
            data["id"] = session_id
            data["user_id"] = user_id
            data["repo_name"] = repo_name
            data["updated_at"] = datetime.now(UTC).isoformat()

            if "created_at" not in data:
                data["created_at"] = data["updated_at"]

            # Atomic write with locking
            writer = AtomicFileWriter(file_path)
            await asyncio.to_thread(self._write_json, writer, data)

            logger.info(f"Saved session: {session_id} for user: {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error saving session {session_id}: {e}")
            return False

    def _write_json(self, writer: AtomicFileWriter, data: dict[str, Any]):
        """Helper to write JSON with atomic writer."""
        with writer.write() as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    async def load_session(
        self, session_id: str, user_id: str, use_lock: bool = True
    ) -> dict[str, Any] | None:
        """
        Load a session.

        Args:
            session_id: Session identifier
            user_id: User identifier (for access control)
            use_lock: Whether to use file lock (default True)

        Returns:
            Session data dict or None if not found
        """
        try:
            # Try to find the session file
            # We need to search across all repos since we don't know the repo_name
            for repo_dir in self.base_path.iterdir():
                if not repo_dir.is_dir():
                    continue

                file_path = repo_dir / f"{user_id}__{session_id}.json"
                if file_path.exists():
                    # Read with optional lock
                    if use_lock:
                        with FileLock(file_path, timeout=5.0):
                            with open(file_path, encoding="utf-8") as f:
                                data = json.load(f)
                    else:
                        with open(file_path, encoding="utf-8") as f:
                            data = json.load(f)

                    # Validate ownership
                    if data.get("user_id") != user_id:
                        raise AccessDeniedError(
                            f"Session {session_id} does not belong to user {user_id}"
                        )

                    logger.debug(f"Loaded session: {session_id} for user: {user_id}")
                    return data

            logger.debug(f"Session not found: {session_id} for user: {user_id}")
            return None
        except AccessDeniedError:
            raise
        except Exception as e:
            logger.error(f"Error loading session {session_id}: {e}")
            return None

    async def append_turn(
        self, session_id: str, user_id: str, turn_data: dict[str, Any]
    ) -> bool:
        """Append a turn to an existing session."""
        try:
            # Load existing session WITHOUT lock (we'll lock later)
            session_data = await self.load_session(session_id, user_id, use_lock=False)
            if not session_data:
                raise SessionNotFoundError(f"Session {session_id} not found")

            repo_name = session_data.get("repo_name")
            if not repo_name:
                raise ValueError("Session missing repo_name")

            file_path = self._get_session_path(session_id, user_id, repo_name)

            # Add timestamp to turn
            turn_data["timestamp"] = datetime.now(UTC).isoformat()

            # Append turn with locking - use longer timeout for concurrent scenarios
            try:
                with FileLock(file_path, timeout=30.0):
                    # Re-read file inside lock to get latest data
                    with open(file_path, encoding="utf-8") as f:
                        session_data = json.load(f)

                    # Ensure turns array exists
                    if "turns" not in session_data:
                        session_data["turns"] = []

                    # Append new turn
                    session_data["turns"].append(turn_data)
                    session_data["updated_at"] = datetime.now(UTC).isoformat()

                    # Write back atomically
                    temp_path = file_path.with_suffix(".tmp")
                    with open(temp_path, "w", encoding="utf-8") as f:
                        json.dump(session_data, f, ensure_ascii=False, indent=2)

                    # Atomic rename
                    temp_path.replace(file_path)

                logger.info(f"Appended turn to session: {session_id}")
                return True
            except TimeoutError as e:
                logger.error(
                    f"Lock timeout when appending to session {session_id}: {e}"
                )
                raise ConcurrentModificationError(
                    f"Could not acquire lock for session {session_id}"
                )
        except (SessionNotFoundError, AccessDeniedError):
            raise
        except Exception as e:
            logger.error(f"Error appending turn to session {session_id}: {e}")
            return False

    async def list_sessions(
        self,
        user_id: str,
        repo_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List sessions for a user."""
        try:
            sessions = []

            # Determine which directories to search
            if repo_name:
                repo_dirs = [self.base_path / repo_name]
            else:
                repo_dirs = [d for d in self.base_path.iterdir() if d.is_dir()]

            # Scan directories
            for repo_dir in repo_dirs:
                if not repo_dir.exists():
                    continue

                for file_path in repo_dir.glob(f"{user_id}__*.json"):
                    try:
                        # Quick metadata read (without full content)
                        with open(file_path, encoding="utf-8") as f:
                            data = json.load(f)

                        # Extract metadata
                        metadata = {
                            "id": data.get("id"),
                            "user_id": data.get("user_id"),
                            "repo_name": data.get("repo_name"),
                            "created_at": data.get("created_at"),
                            "updated_at": data.get("updated_at"),
                            "turns_count": len(data.get("turns", [])),
                        }

                        # Add first query as preview
                        turns = data.get("turns", [])
                        if turns:
                            metadata["first_query"] = turns[0].get("query", "")[:100]
                            metadata["last_query"] = turns[-1].get("query", "")[:100]

                        sessions.append(metadata)
                    except Exception as e:
                        logger.warning(f"Error reading session file {file_path}: {e}")
                        continue

            # Sort by updated_at (newest first)
            sessions.sort(key=lambda x: x.get("updated_at", ""), reverse=True)

            # Apply pagination
            return sessions[offset : offset + limit]
        except Exception as e:
            logger.error(f"Error listing sessions for user {user_id}: {e}")
            return []

    async def delete_session(self, session_id: str, user_id: str) -> bool:
        """Delete a session."""
        try:
            # Find and delete the session file
            for repo_dir in self.base_path.iterdir():
                if not repo_dir.is_dir():
                    continue

                file_path = repo_dir / f"{user_id}__{session_id}.json"
                if file_path.exists():
                    # Verify ownership before deleting
                    with open(file_path, encoding="utf-8") as f:
                        data = json.load(f)

                    if data.get("user_id") != user_id:
                        raise AccessDeniedError(f"Cannot delete session {session_id}")

                    # Delete file
                    file_path.unlink()
                    logger.info(f"Deleted session: {session_id}")
                    return True

            logger.warning(f"Session not found for deletion: {session_id}")
            return False
        except AccessDeniedError:
            raise
        except Exception as e:
            logger.error(f"Error deleting session {session_id}: {e}")
            return False

    async def session_exists(self, session_id: str, user_id: str) -> bool:
        """Check if a session exists."""
        for repo_dir in self.base_path.iterdir():
            if not repo_dir.is_dir():
                continue

            file_path = repo_dir / f"{user_id}__{session_id}.json"
            if file_path.exists():
                return True

        return False

    async def update_session_metadata(
        self, session_id: str, user_id: str, metadata: dict[str, Any]
    ) -> bool:
        """Update session metadata."""
        try:
            session_data = await self.load_session(session_id, user_id)
            if not session_data:
                raise SessionNotFoundError(f"Session {session_id} not found")

            repo_name = session_data.get("repo_name")
            file_path = self._get_session_path(session_id, user_id, repo_name)

            # Update metadata with locking
            with FileLock(file_path, timeout=5.0):
                with open(file_path, encoding="utf-8") as f:
                    session_data = json.load(f)

                # Update metadata fields
                session_data.update(metadata)
                session_data["updated_at"] = datetime.now(UTC).isoformat()

                # Write back
                writer = AtomicFileWriter(file_path)
                with writer.write() as f:
                    json.dump(session_data, f, ensure_ascii=False, indent=2)

            logger.debug(f"Updated metadata for session: {session_id}")
            return True
        except (SessionNotFoundError, AccessDeniedError):
            raise
        except Exception as e:
            logger.error(f"Error updating session metadata {session_id}: {e}")
            return False

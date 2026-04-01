# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import json
import uuid
from datetime import datetime
from typing import Any

from api.models.folders import (
    Folder,
    FolderResponse,
    FolderStructure,
    FolderStructureResponse,
)
from core.config import get_wiki_doc_dir
from filelock import FileLock


class FolderService:
    """Service for managing folder structures."""

    FOLDERS_FILE = "_folders.json"

    def __init__(self, repo_name: str):
        """Initialize folder service for a repository.

        Args:
            repo_name: Name of the repository
        """
        self.repo_name = repo_name
        self.repo_dir = get_wiki_doc_dir(repo_name)
        self.folders_file = self.repo_dir / self.FOLDERS_FILE
        self.lock_file = self.repo_dir / f"{self.FOLDERS_FILE}.lock"

    def _load_structure(self) -> FolderStructure:
        """Load folder structure from file.

        Returns:
            FolderStructure object
        """
        if not self.folders_file.exists():
            return FolderStructure()

        try:
            with open(self.folders_file, encoding="utf-8") as f:
                data = json.load(f)
                return FolderStructure(**data)
        except (json.JSONDecodeError, ValueError):
            # If file is corrupted, return empty structure
            return FolderStructure()

    def _save_structure(self, structure: FolderStructure) -> None:
        """Save folder structure to file with file locking.

        Args:
            structure: FolderStructure to save
        """
        # Ensure directory exists
        self.repo_dir.mkdir(parents=True, exist_ok=True)

        # Use file lock to prevent concurrent writes
        lock = FileLock(str(self.lock_file), timeout=10)
        with lock:
            with open(self.folders_file, "w", encoding="utf-8") as f:
                json.dump(
                    structure.model_dump(), f, indent=2, ensure_ascii=False, default=str
                )

    def get_structure(self) -> FolderStructureResponse:
        """Get the complete folder structure.

        Returns:
            FolderStructureResponse
        """
        structure = self._load_structure()
        return FolderStructureResponse(
            version=structure.version,
            folders=[
                FolderResponse(
                    id=folder.id,
                    name=folder.name,
                    parent_id=folder.parent_id,
                    created_at=folder.created_at.isoformat(),
                    updated_at=folder.updated_at.isoformat(),
                )
                for folder in structure.folders
            ],
            document_folders=structure.document_folders,
        )

    def create_folder(self, name: str, parent_id: str | None = None) -> FolderResponse:
        """Create a new folder.

        Args:
            name: Folder name
            parent_id: Parent folder ID (None for root level)

        Returns:
            FolderResponse

        Raises:
            ValueError: If parent folder doesn't exist or would create a cycle
        """
        structure = self._load_structure()

        # Validate parent exists if specified
        if parent_id:
            if not any(f.id == parent_id for f in structure.folders):
                raise ValueError(f"Parent folder {parent_id} not found")

        # Check for duplicate name at same level
        existing = [
            f for f in structure.folders if f.name == name and f.parent_id == parent_id
        ]
        if existing:
            raise ValueError(f"Folder '{name}' already exists at this level")

        # Create new folder
        now = datetime.now()
        folder = Folder(
            id=str(uuid.uuid4()),
            name=name,
            parent_id=parent_id,
            created_at=now,
            updated_at=now,
        )

        structure.folders.append(folder)
        self._save_structure(structure)

        return FolderResponse(
            id=folder.id,
            name=folder.name,
            parent_id=folder.parent_id,
            created_at=folder.created_at.isoformat(),
            updated_at=folder.updated_at.isoformat(),
        )

    def update_folder(
        self,
        folder_id: str,
        name: str | None = None,
        parent_id: str | None = None,
    ) -> FolderResponse:
        """Update a folder.

        Args:
            folder_id: Folder ID to update
            name: New name (optional)
            parent_id: New parent ID (optional)

        Returns:
            FolderResponse

        Raises:
            ValueError: If folder not found or update would create a cycle
        """
        structure = self._load_structure()

        # Find folder
        folder = next((f for f in structure.folders if f.id == folder_id), None)
        if not folder:
            raise ValueError(f"Folder {folder_id} not found")

        # Update name if provided
        if name is not None:
            # Check for duplicate name at same level
            existing = [
                f
                for f in structure.folders
                if f.name == name
                and f.parent_id == folder.parent_id
                and f.id != folder_id
            ]
            if existing:
                raise ValueError(f"Folder '{name}' already exists at this level")
            folder.name = name

        # Update parent if provided
        if parent_id is not None:
            # Validate parent exists
            if parent_id and not any(f.id == parent_id for f in structure.folders):
                raise ValueError(f"Parent folder {parent_id} not found")

            # Check for cycles
            if parent_id and self._would_create_cycle(structure, folder_id, parent_id):
                raise ValueError("Moving folder would create a cycle")

            folder.parent_id = parent_id

        folder.updated_at = datetime.now()
        self._save_structure(structure)

        return FolderResponse(
            id=folder.id,
            name=folder.name,
            parent_id=folder.parent_id,
            created_at=folder.created_at.isoformat(),
            updated_at=folder.updated_at.isoformat(),
        )

    def delete_folder(self, folder_id: str) -> dict[str, Any]:
        """Delete a folder and move its documents to root.

        Args:
            folder_id: Folder ID to delete

        Returns:
            Dict with success status and moved documents

        Raises:
            ValueError: If folder not found
        """
        structure = self._load_structure()

        # Find folder
        folder = next((f for f in structure.folders if f.id == folder_id), None)
        if not folder:
            raise ValueError(f"Folder {folder_id} not found")

        # Move all documents in this folder to root
        moved_docs = []
        for doc_name, doc_folder_id in list(structure.document_folders.items()):
            if doc_folder_id == folder_id:
                structure.document_folders[doc_name] = None
                moved_docs.append(doc_name)

        # Move all child folders to the parent of deleted folder
        for child_folder in structure.folders:
            if child_folder.parent_id == folder_id:
                child_folder.parent_id = folder.parent_id
                child_folder.updated_at = datetime.now()

        # Remove folder
        structure.folders = [f for f in structure.folders if f.id != folder_id]
        self._save_structure(structure)

        return {
            "success": True,
            "message": f"Folder '{folder.name}' deleted",
            "moved_documents": moved_docs,
        }

    def move_document(self, doc_name: str, folder_id: str | None) -> None:
        """Move a document to a folder.

        Args:
            doc_name: Document name
            folder_id: Target folder ID (None for root)

        Raises:
            ValueError: If folder doesn't exist
        """
        structure = self._load_structure()

        # Validate folder exists if specified
        if folder_id:
            if not any(f.id == folder_id for f in structure.folders):
                raise ValueError(f"Folder {folder_id} not found")

        structure.document_folders[doc_name] = folder_id
        self._save_structure(structure)

    def batch_move_documents(
        self,
        doc_names: list[str],
        folder_id: str | None,
    ) -> None:
        """Batch move documents to a folder.

        Args:
            doc_names: List of document names
            folder_id: Target folder ID (None for root)

        Raises:
            ValueError: If folder doesn't exist
        """
        structure = self._load_structure()

        # Validate folder exists if specified
        if folder_id:
            if not any(f.id == folder_id for f in structure.folders):
                raise ValueError(f"Folder {folder_id} not found")

        for doc_name in doc_names:
            structure.document_folders[doc_name] = folder_id

        self._save_structure(structure)

    def _would_create_cycle(
        self,
        structure: FolderStructure,
        folder_id: str,
        new_parent_id: str,
    ) -> bool:
        """Check if moving a folder would create a cycle.

        Args:
            structure: Current folder structure
            folder_id: Folder being moved
            new_parent_id: Proposed new parent

        Returns:
            True if would create a cycle
        """
        # Build parent map
        parent_map = {f.id: f.parent_id for f in structure.folders}

        # Traverse up from new_parent_id to see if we reach folder_id
        current = new_parent_id
        visited = set()
        while current:
            if current == folder_id:
                return True
            if current in visited:
                # Already visited, break to avoid infinite loop
                break
            visited.add(current)
            current = parent_map.get(current)

        return False

    def get_folder_path(self, folder_id: str) -> list[FolderResponse]:
        """Get the path from root to a folder.

        Args:
            folder_id: Folder ID

        Returns:
            List of folders from root to target

        Raises:
            ValueError: If folder not found
        """
        structure = self._load_structure()

        # Build parent map
        folder_map = {f.id: f for f in structure.folders}

        if folder_id not in folder_map:
            raise ValueError(f"Folder {folder_id} not found")

        # Build path
        path = []
        current_id = folder_id
        while current_id:
            folder = folder_map[current_id]
            path.insert(
                0,
                FolderResponse(
                    id=folder.id,
                    name=folder.name,
                    parent_id=folder.parent_id,
                    created_at=folder.created_at.isoformat(),
                    updated_at=folder.updated_at.isoformat(),
                ),
            )
            current_id = folder.parent_id

        return path

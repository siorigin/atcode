# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from datetime import datetime

from pydantic import BaseModel, Field


class Folder(BaseModel):
    """Folder model."""

    id: str
    name: str
    parent_id: str | None = None
    created_at: datetime
    updated_at: datetime


class FolderStructure(BaseModel):
    """Complete folder structure for a repository."""

    version: int = 1
    folders: list[Folder] = Field(default_factory=list)
    document_folders: dict[str, str | None] = Field(
        default_factory=dict
    )  # doc_name -> folder_id


class CreateFolderRequest(BaseModel):
    """Request to create a new folder."""

    name: str = Field(..., min_length=1, max_length=100)
    parent_id: str | None = None


class UpdateFolderRequest(BaseModel):
    """Request to update a folder."""

    name: str | None = Field(None, min_length=1, max_length=100)
    parent_id: str | None = None


class MoveDocumentRequest(BaseModel):
    """Request to move a document to a folder."""

    folder_id: str | None = None  # None means move to root


class BatchMoveDocumentsRequest(BaseModel):
    """Request to batch move documents to a folder."""

    document_names: list[str]
    folder_id: str | None = None


class FolderResponse(BaseModel):
    """Response for folder operations."""

    id: str
    name: str
    parent_id: str | None
    created_at: str
    updated_at: str


class FolderStructureResponse(BaseModel):
    """Response containing the complete folder structure."""

    version: int
    folders: list[FolderResponse]
    document_folders: dict[str, str | None]


class DeleteFolderResponse(BaseModel):
    """Response for folder deletion."""

    success: bool
    message: str
    moved_documents: list[str] = Field(default_factory=list)

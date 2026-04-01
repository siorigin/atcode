# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from api.models.folders import (
    BatchMoveDocumentsRequest,
    CreateFolderRequest,
    DeleteFolderResponse,
    FolderResponse,
    FolderStructureResponse,
    MoveDocumentRequest,
    UpdateFolderRequest,
)
from api.services.folder_service import FolderService
from fastapi import APIRouter, HTTPException
from fastapi import Path as PathParam
from loguru import logger

router = APIRouter()


@router.get("/repos/{repo}/folders", response_model=FolderStructureResponse)
async def get_folder_structure(
    repo: str = PathParam(..., description="Repository name"),
):
    """Get the complete folder structure for a repository.

    Args:
        repo: Repository name

    Returns:
        FolderStructureResponse with all folders and document mappings
    """
    try:
        service = FolderService(repo)
        return service.get_structure()
    except Exception as e:
        logger.error(f"Failed to get folder structure for {repo}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/repos/{repo}/folders", response_model=FolderResponse)
async def create_folder(
    request: CreateFolderRequest,
    repo: str = PathParam(..., description="Repository name"),
):
    """Create a new folder.

    Args:
        repo: Repository name
        request: CreateFolderRequest with folder name and optional parent_id

    Returns:
        FolderResponse with created folder details

    Raises:
        HTTPException: If parent folder doesn't exist or folder name is duplicate
    """
    try:
        service = FolderService(repo)
        return service.create_folder(
            name=request.name,
            parent_id=request.parent_id,
        )
    except ValueError as e:
        logger.warning(f"Failed to create folder in {repo}: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create folder in {repo}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/repos/{repo}/folders/{folder_id}", response_model=FolderResponse)
async def update_folder(
    request: UpdateFolderRequest,
    repo: str = PathParam(..., description="Repository name"),
    folder_id: str = PathParam(..., description="Folder ID"),
):
    """Update a folder (rename or move).

    Args:
        repo: Repository name
        folder_id: Folder ID to update
        request: UpdateFolderRequest with optional name and parent_id

    Returns:
        FolderResponse with updated folder details

    Raises:
        HTTPException: If folder not found or update would create a cycle
    """
    try:
        service = FolderService(repo)
        return service.update_folder(
            folder_id=folder_id,
            name=request.name,
            parent_id=request.parent_id,
        )
    except ValueError as e:
        logger.warning(f"Failed to update folder {folder_id} in {repo}: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to update folder {folder_id} in {repo}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/repos/{repo}/folders/{folder_id}", response_model=DeleteFolderResponse)
async def delete_folder(
    repo: str = PathParam(..., description="Repository name"),
    folder_id: str = PathParam(..., description="Folder ID"),
):
    """Delete a folder.

    Documents in the folder will be moved to root.
    Child folders will be moved to the parent of the deleted folder.

    Args:
        repo: Repository name
        folder_id: Folder ID to delete

    Returns:
        DeleteFolderResponse with success status and moved documents

    Raises:
        HTTPException: If folder not found
    """
    try:
        service = FolderService(repo)
        result = service.delete_folder(folder_id)
        return DeleteFolderResponse(**result)
    except ValueError as e:
        logger.warning(f"Failed to delete folder {folder_id} in {repo}: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to delete folder {folder_id} in {repo}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/repos/{repo}/documents/{doc_name}/folder")
async def move_document(
    request: MoveDocumentRequest,
    repo: str = PathParam(..., description="Repository name"),
    doc_name: str = PathParam(..., description="Document name"),
):
    """Move a document to a folder.

    Args:
        repo: Repository name
        doc_name: Document name
        request: MoveDocumentRequest with target folder_id (None for root)

    Returns:
        Success message

    Raises:
        HTTPException: If folder doesn't exist
    """
    try:
        service = FolderService(repo)
        service.move_document(doc_name, request.folder_id)
        return {
            "success": True,
            "message": f"Document '{doc_name}' moved successfully",
        }
    except ValueError as e:
        logger.warning(f"Failed to move document {doc_name} in {repo}: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to move document {doc_name} in {repo}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/repos/{repo}/documents/batch-move")
async def batch_move_documents(
    request: BatchMoveDocumentsRequest,
    repo: str = PathParam(..., description="Repository name"),
):
    """Batch move documents to a folder.

    Args:
        repo: Repository name
        request: BatchMoveDocumentsRequest with document names and target folder_id

    Returns:
        Success message with count

    Raises:
        HTTPException: If folder doesn't exist
    """
    try:
        service = FolderService(repo)
        service.batch_move_documents(request.document_names, request.folder_id)
        return {
            "success": True,
            "message": f"Moved {len(request.document_names)} documents successfully",
            "count": len(request.document_names),
        }
    except ValueError as e:
        logger.warning(f"Failed to batch move documents in {repo}: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to batch move documents in {repo}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/repos/{repo}/folders/{folder_id}/path", response_model=list[FolderResponse]
)
async def get_folder_path(
    repo: str = PathParam(..., description="Repository name"),
    folder_id: str = PathParam(..., description="Folder ID"),
):
    """Get the path from root to a folder (for breadcrumb navigation).

    Args:
        repo: Repository name
        folder_id: Folder ID

    Returns:
        List of folders from root to target

    Raises:
        HTTPException: If folder not found
    """
    try:
        service = FolderService(repo)
        return service.get_folder_path(folder_id)
    except ValueError as e:
        logger.warning(f"Failed to get folder path for {folder_id} in {repo}: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to get folder path for {folder_id} in {repo}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Folder management types for organizing research documents.
 */

export interface Folder {
  id: string;
  name: string;
  parentId: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface FolderStructure {
  version: number;
  folders: Folder[];
  documentFolders: Record<string, string | null>; // doc_name -> folder_id
}

export interface CreateFolderRequest {
  name: string;
  parentId?: string | null;
}

export interface UpdateFolderRequest {
  name?: string;
  parentId?: string | null;
}

export interface MoveDocumentRequest {
  folderId: string | null;
}

export interface BatchMoveDocumentsRequest {
  documentNames: string[];
  folderId?: string | null;
}

export interface DeleteFolderResponse {
  success: boolean;
  message: string;
  movedDocuments: string[];
}

/**
 * Extended Research type with folder information
 */
export interface ResearchWithFolder {
  name: string;
  lastUpdated: string;
  folderId: string | null;
  metadata?: {
    id?: string;
    referencesCount?: number;
    codeBlocksCount?: number;
    query?: string;
  };
}

/**
 * Folder tree node for hierarchical display
 */
export interface FolderTreeNode extends Folder {
  children: FolderTreeNode[];
  documentCount: number;
}

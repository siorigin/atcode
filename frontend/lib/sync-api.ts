// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Sync API client for incremental sync and Git operations.
 * Connects directly to FastAPI backend.
 */

import { apiFetch } from '@/lib/api-client';

// ============== Types ==============

export interface SyncStatus {
  is_watching: boolean;
  is_processing: boolean;
  is_git_repo: boolean;
  current_ref: string | null;
  current_ref_type: 'branch' | 'tag' | 'commit' | null;
  pending_changes: number;
  latest_result: {
    total_changes: number;
    success: boolean;
    duration_ms: number;
  } | null;
  built_commit_sha: string | null;
}

export interface GitRef {
  name: string;
  ref_type: 'branch' | 'tag' | 'commit';
  commit_sha: string;
  short_sha: string;
  is_current: boolean;
}

export interface UpdateResult {
  added: number;
  modified: number;
  deleted: number;
  calls_created: number;
  calls_rebuilt: number;
  duration_ms: number;
  errors: string[];
  total_changes: number;
  success: boolean;
}

export interface CheckoutTaskResponse {
  task_id: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
  message: string;
}

export interface PendingFile {
  path: string;
  action: 'add' | 'modify' | 'delete';
}

export interface SyncHistoryItem {
  timestamp: string;
  total_changes: number;
  added: number;
  modified: number;
  deleted: number;
  added_files: string[];
  modified_files: string[];
  deleted_files: string[];
  duration_ms: number;
  success: boolean;
  errors: string[];
}

// ============== API Functions ==============

/**
 * Embedding mode options for sync operations.
 */
export type EmbeddingMode = 'skip' | 'async' | 'sync';

/**
 * Start real-time file monitoring for a project.
 * @param autoWatch If false, only initialize sync manager without starting file monitoring
 * @param subdirs Optional list of subdirectory names to monitor (only these dirs will be watched)
 * @param embeddingMode Embedding generation mode: 'skip' (no embeddings), 'async' (background), 'sync' (immediate)
 */
export async function startWatching(
  projectName: string,
  repoPath?: string,
  autoWatch: boolean = true,
  subdirs?: string[],
  embeddingMode: EmbeddingMode = 'sync'
): Promise<{ status: string; message: string }> {
  const params = new URLSearchParams();
  if (repoPath) {
    params.set('repo_path', repoPath);
  }
  if (!autoWatch) {
    params.set('auto_watch', 'false');
  }
  if (subdirs && subdirs.length > 0) {
    params.set('subdirs', subdirs.join(','));
  }
  // Handle embedding mode
  if (embeddingMode === 'skip') {
    params.set('skip_embeddings', 'true');
  } else if (embeddingMode === 'async') {
    params.set('async_embeddings', 'true');
  }
  // 'sync' mode is the default (no additional params needed)

  const url = `/api/sync/${encodeURIComponent(projectName)}/start${params.toString() ? '?' + params.toString() : ''}`;

  const response = await apiFetch(url, {
    method: 'POST',
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to start watching' }));
    throw new Error(error.detail || 'Failed to start watching');
  }

  return response.json();
}

/**
 * Stop file monitoring for a project.
 */
export async function stopWatching(
  projectName: string
): Promise<{ status: string; message: string }> {
  const response = await apiFetch(
    `/api/sync/${encodeURIComponent(projectName)}/stop`,
    { method: 'POST' }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to stop watching' }));
    throw new Error(error.detail || 'Failed to stop watching');
  }

  return response.json();
}

/**
 * Manually trigger sync for a project.
 *
 * If the backend detects no existing knowledge graph, it returns a
 * CheckoutTaskResponse (with task_id) instead of an UpdateResult so the
 * frontend can poll for graph-build progress.
 *
 * @param embeddingMode Embedding generation mode: 'skip' (no embeddings), 'async' (background), 'sync' (immediate)
 */
export async function syncNow(
  projectName: string,
  repoPath?: string,
  embeddingMode: EmbeddingMode = 'sync'
): Promise<UpdateResult | CheckoutTaskResponse> {
  const params = new URLSearchParams();
  if (repoPath) {
    params.set('repo_path', repoPath);
  }
  // Handle embedding mode
  if (embeddingMode === 'skip') {
    params.set('skip_embeddings', 'true');
  }
  // Note: async_embeddings for sync_now would need backend support
  // For now, 'async' falls back to 'sync' behavior in syncNow

  const url = `/api/sync/${encodeURIComponent(projectName)}/now${params.toString() ? '?' + params.toString() : ''}`;

  const response = await apiFetch(url, {
    method: 'POST',
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to sync' }));
    throw new Error(error.detail || 'Failed to sync');
  }

  return response.json();
}

/**
 * Type guard to check if a sync response is a background task (graph build).
 */
export function isSyncTaskResponse(
  response: UpdateResult | CheckoutTaskResponse
): response is CheckoutTaskResponse {
  return 'task_id' in response && !!(response as CheckoutTaskResponse).task_id;
}

/**
 * Get sync status for a project.
 */
export async function getSyncStatus(projectName: string): Promise<SyncStatus> {
  const response = await apiFetch(
    `/api/sync/${encodeURIComponent(projectName)}/status`
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to get sync status' }));
    throw new Error(error.detail || 'Failed to get sync status');
  }

  return response.json();
}

/**
 * List Git branches for a project.
 */
export async function listBranches(
  projectName: string,
  includeRemote: boolean = false
): Promise<GitRef[]> {
  const params = new URLSearchParams();
  if (includeRemote) {
    params.set('include_remote', 'true');
  }

  const response = await apiFetch(
    `/api/sync/${encodeURIComponent(projectName)}/git/branches?${params.toString()}`
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to list branches' }));
    throw new Error(error.detail || 'Failed to list branches');
  }

  return response.json();
}

/**
 * List Git tags for a project.
 */
export async function listTags(projectName: string): Promise<GitRef[]> {
  const response = await apiFetch(
    `/api/sync/${encodeURIComponent(projectName)}/git/tags`
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to list tags' }));
    throw new Error(error.detail || 'Failed to list tags');
  }

  return response.json();
}

/**
 * Get current Git reference for a project.
 */
export async function getCurrentRef(projectName: string): Promise<GitRef | null> {
  const response = await apiFetch(
    `/api/sync/${encodeURIComponent(projectName)}/git/current`
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to get current ref' }));
    throw new Error(error.detail || 'Failed to get current ref');
  }

  return response.json();
}

/**
 * Fetch updates from a remote.
 */
export async function fetchRemote(
  projectName: string,
  remote: string = 'origin'
): Promise<{ status: string; message: string }> {
  const response = await apiFetch(
    `/api/sync/${encodeURIComponent(projectName)}/git/fetch`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ remote }),
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to fetch' }));
    throw new Error(error.detail || 'Failed to fetch');
  }

  return response.json();
}

/**
 * Pull updates from remote (fetch + merge) and update graph.
 * Returns a background task ID for tracking progress.
 */
export async function pullRemote(
  projectName: string,
  remote: string = 'origin',
  branch?: string,
  background: boolean = true
): Promise<CheckoutTaskResponse> {
  const params = background ? '?background=true' : '';
  const response = await apiFetch(
    `/api/sync/${encodeURIComponent(projectName)}/git/pull${params}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ remote, branch: branch || null }),
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to pull' }));
    throw new Error(error.detail || 'Failed to pull');
  }

  return response.json();
}

/**
 * Checkout a Git reference (branch, tag, or commit).
 * Returns a background task ID for tracking progress.
 */
export async function checkoutRef(
  projectName: string,
  ref: string,
  background: boolean = true
): Promise<CheckoutTaskResponse> {
  const params = background ? '?background=true' : '';
  const response = await apiFetch(
    `/api/sync/${encodeURIComponent(projectName)}/git/checkout${params}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ref }),
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to checkout' }));
    throw new Error(error.detail || 'Failed to checkout');
  }

  return response.json();
}

/**
 * Cleanup sync manager for a project.
 */
export async function cleanupSyncManager(
  projectName: string
): Promise<{ status: string; message: string }> {
  const response = await apiFetch(
    `/api/sync/${encodeURIComponent(projectName)}/cleanup`,
    { method: 'POST' }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to cleanup' }));
    throw new Error(error.detail || 'Failed to cleanup');
  }

  return response.json();
}

/**
 * Get pending files for a project.
 */
export async function getPendingFiles(
  projectName: string
): Promise<PendingFile[]> {
  const response = await apiFetch(
    `/api/sync/${encodeURIComponent(projectName)}/pending`
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to get pending files' }));
    throw new Error(error.detail || 'Failed to get pending files');
  }

  return response.json();
}

/**
 * Get sync history for a project.
 */
export async function getSyncHistory(
  projectName: string,
  limit: number = 20
): Promise<SyncHistoryItem[]> {
  const params = new URLSearchParams();
  params.set('limit', limit.toString());

  const response = await apiFetch(
    `/api/sync/${encodeURIComponent(projectName)}/history?${params.toString()}`
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to get sync history' }));
    throw new Error(error.detail || 'Failed to get sync history');
  }

  return response.json();
}

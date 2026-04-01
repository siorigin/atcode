// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * AtCode Knowledge Graph API Client
 *
 * Provides API methods for managing knowledge graph data.
 * Connects directly to FastAPI backend.
 *
 * Includes caching mechanism to avoid repeated API calls for graph data
 * that rarely changes after initial build.
 */

import { apiFetch } from '@/lib/api-client';
import {
  getCachedProjects,
  getCachedStats,
  setCachedProjects,
  setCachedStats,
  invalidateGraphCache,
  updateProjectInCache,
  removeProjectFromCache,
} from './graph-cache';

// ============== Types ==============

export interface GraphProject {
  name: string;
  node_count: number;
  relationship_count: number;
  has_graph: boolean;
  path?: string;  // Local repository path from Memgraph
  sync_enabled?: boolean;  // Whether incremental sync is enabled (persisted in Memgraph)
  node_types?: Record<string, number>;  // Node count by label type (e.g. File, Function, Class)
}

export interface GraphProjectListResponse {
  projects: GraphProject[];
  total: number;
  connected: boolean;
  cache_epoch?: string | null;
}

export interface CleanProjectResponse {
  success: boolean;
  project_name: string;
  deleted_nodes: number;
  message: string;
}

export interface RefreshProjectResponse {
  success: boolean;
  project_name: string;
  job_id?: string;
  message: string;
}

export interface GraphStatsResponse {
  total_projects: number;
  total_nodes: number;
  total_relationships: number;
  node_types: Record<string, number>;
  connected: boolean;
  cache_epoch?: string | null;
}

export interface GraphJobStatus {
  job_id: string;
  project_name: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  progress: number;
  message: string;
  error?: string;
  created_at: string;
  updated_at: string;
  fast_mode?: boolean;
}

export interface RefreshOptions {
  repoPath?: string;
  fastMode?: boolean;  // Skip semantic embeddings for faster builds
}

export interface GraphCacheSyncResponse {
  success: boolean;
  total_projects: number;
  total_nodes: number;
  total_relationships: number;
  message: string;
}

// ============== API Functions ==============

// Timeout for graph API requests (longer timeout for reliability)
const GRAPH_API_TIMEOUT = 30000; // 30 seconds - increased for slow connections/large graphs

/**
 * List all projects in the backend graph cache
 * Uses cached data if available, otherwise fetches from API
 *
 * @param forceRefresh - If true, bypasses cache and fetches fresh data
 */
export async function listGraphProjects(forceRefresh: boolean = false): Promise<GraphProjectListResponse> {
  // Check cache first (unless force refresh)
  if (!forceRefresh) {
    const cached = getCachedProjects();
    if (cached) {
      return cached;
    }
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), GRAPH_API_TIMEOUT);

  try {
    const response = await apiFetch('/api/graph/projects', {
      method: 'GET',
      signal: controller.signal,
      cache: 'no-store' as RequestCache,  // Bypass browser cache on force refresh
    });

    if (!response.ok) {
      throw new Error(`Failed to list graph projects: ${response.statusText}`);
    }

    const data = await response.json();
    if (data.connected !== false) {
      setCachedProjects(data);
    }
    return data;
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * Get graph statistics
 * Uses cached data if available, otherwise fetches from API
 *
 * @param forceRefresh - If true, bypasses cache and fetches fresh data
 */
export async function getGraphStats(forceRefresh: boolean = false): Promise<GraphStatsResponse> {
  // Check cache first (unless force refresh)
  if (!forceRefresh) {
    const cached = getCachedStats();
    if (cached) {
      return cached;
    }
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), GRAPH_API_TIMEOUT);

  try {
    const response = await apiFetch('/api/graph/stats', {
      method: 'GET',
      signal: controller.signal,
      cache: 'no-store' as RequestCache,  // Bypass browser cache on force refresh
    });

    if (!response.ok) {
      throw new Error(`Failed to get graph stats: ${response.statusText}`);
    }

    const data = await response.json();
    // Only cache when backend is actually connected to Memgraph —
    // avoid caching { connected: false, total_nodes: 0, ... } on transient errors
    if (data.connected !== false) {
      setCachedStats(data);
    }
    return data;
  } catch (error) {
    // Return a "disconnected" response on timeout
    if (error instanceof Error && error.name === 'AbortError') {
      return {
        total_projects: 0,
        total_nodes: 0,
        total_relationships: 0,
        node_types: {},
        connected: false,
      };
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * Explicitly refresh backend Redis graph cache from Memgraph.
 * This is a manual recovery/sync path, not part of normal page loading.
 */
export async function syncGraphCache(): Promise<GraphCacheSyncResponse> {
  const response = await apiFetch('/api/graph/cache/sync', {
    method: 'POST',
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || error.error || `Failed to sync graph cache: ${response.statusText}`);
  }

  const data = await response.json();
  invalidateGraphCache();
  return data;
}

/**
 * Get statistics for a single project
 * Used to update cache after graph operations
 */
export async function getProjectStats(projectName: string): Promise<GraphProject> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), GRAPH_API_TIMEOUT);

  try {
    const response = await apiFetch(`/api/graph/projects/${encodeURIComponent(projectName)}/stats`, {
      method: 'GET',
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new Error(`Failed to get project stats: ${response.statusText}`);
    }

    return await response.json();
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * Clean (delete) a project's knowledge graph
 * Updates cache for the specific project instead of invalidating all cache
 */
export async function cleanProjectGraph(projectName: string): Promise<CleanProjectResponse> {
  const response = await apiFetch(`/api/graph/projects/${encodeURIComponent(projectName)}`, {
    method: 'DELETE',
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || error.error || `Failed to clean project graph: ${response.statusText}`);
  }

  const data = await response.json();
  // Update cache for this specific project (mark as no graph)
  removeProjectFromCache(projectName);
  return data;
}

export interface CleanAllResponse {
  success: boolean;
  message: string;
}

/**
 * Clean (delete) ALL knowledge graph data from the database
 * This is a destructive operation that removes all projects' graph data
 */
export async function cleanAllGraphs(): Promise<CleanAllResponse> {
  const response = await apiFetch('/api/graph/database?confirm=true', {
    method: 'DELETE',
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || error.error || `Failed to clean database: ${response.statusText}`);
  }

  const data = await response.json();
  // Invalidate all cache since everything is deleted
  invalidateGraphCache();
  return data;
}

/**
 * Refresh (rebuild) a project's knowledge graph
 * Note: Cache update happens via updateProjectGraphCache() after job completes
 *
 * @param projectName - Name of the project to refresh
 * @param options - Optional refresh options
 * @param options.repoPath - Custom repository path
 * @param options.fastMode - Skip semantic embeddings for faster builds
 */
export async function refreshProjectGraph(
  projectName: string,
  options?: RefreshOptions
): Promise<RefreshProjectResponse> {
  const params = new URLSearchParams();
  if (options?.repoPath) {
    params.set('repo_path', options.repoPath);
  }
  if (options?.fastMode) {
    params.set('fast_mode', 'true');
  }

  const url = `/api/graph/projects/${encodeURIComponent(projectName)}/refresh${params.toString() ? '?' + params.toString() : ''}`;

  const response = await apiFetch(url, {
    method: 'POST',
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || error.error || `Failed to refresh project graph: ${response.statusText}`);
  }

  const data = await response.json();
  // Don't invalidate cache here - cache will be updated when job completes
  // via updateProjectGraphCache()
  return data;
}

/**
 * Update cache for a specific project after graph operation completes
 * Fetches fresh stats from the server and updates local cache
 */
export async function updateProjectGraphCache(projectName: string): Promise<void> {
  try {
    const stats = await getProjectStats(projectName);
    updateProjectInCache(
      projectName,
      stats.node_count,
      stats.relationship_count,
      stats.has_graph,
      stats.node_types
    );
  } catch (error) {
    console.warn(`[GraphAPI] Failed to update cache for ${projectName}:`, error);
    // If we can't get fresh stats, invalidate entire cache as fallback
    invalidateGraphCache();
  }
}

/**
 * Get the status of a graph operation job
 */
export async function getGraphJobStatus(jobId: string): Promise<GraphJobStatus> {
  const response = await apiFetch(`/api/graph/jobs/${jobId}`, {
    method: 'GET',
  });

  if (!response.ok) {
    if (response.status === 404) {
      throw new Error('Job not found');
    }
    throw new Error(`Failed to get job status: ${response.statusText}`);
  }

  return response.json();
}

/**
 * Build knowledge graph from a repository
 * Note: Cache update happens via updateProjectGraphCache() after job completes
 */
export async function buildGraphFromRepo(
  projectName: string,
  repoPath?: string
): Promise<RefreshProjectResponse> {
  const response = await apiFetch('/api/graph/build', {
    method: 'POST',
    body: JSON.stringify({
      project_name: projectName,
      repo_path: repoPath,
    }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || error.error || `Failed to build graph: ${response.statusText}`);
  }

  const data = await response.json();
  // Don't invalidate cache here - cache will be updated when job completes
  return data;
}

/**
 * Check if the graph API is available
 */
export async function isGraphApiAvailable(): Promise<boolean> {
  try {
    const response = await apiFetch('/api/graph/stats', {
      method: 'GET',
      signal: AbortSignal.timeout(3000),
    });
    return response.ok;
  } catch {
    return false;
  }
}

// Re-export cache invalidation for manual refresh
export { invalidateGraphCache } from './graph-cache';

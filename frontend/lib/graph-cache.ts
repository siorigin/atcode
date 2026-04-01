// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Graph Data Cache
 *
 * Persistent cache for knowledge graph data (projects and stats).
 *
 * Design Philosophy:
 * - Graph data is essentially static after creation/rebuild
 * - Cache has a TTL to avoid showing stale data
 * - Only refresh/clean/build operations update the cache immediately
 * - Cache expires after CACHE_TTL_MS, forcing a refresh on next access
 */

// Define cache types locally to avoid circular dependency with graph-api.ts
interface CachedGraphProject {
  name: string;
  node_count: number;
  relationship_count: number;
  has_graph: boolean;
  path?: string;
  sync_enabled?: boolean;
  node_types?: Record<string, number>;
}

interface CachedGraphProjectListResponse {
  projects: CachedGraphProject[];
  total: number;
  connected: boolean;
  cache_epoch?: string | null;
}

interface CachedGraphStatsResponse {
  total_projects: number;
  total_nodes: number;
  total_relationships: number;
  node_types: Record<string, number>;
  connected: boolean;
  cache_epoch?: string | null;
}

interface CacheEntry<T> {
  data: T;
  timestamp: number;
  version: number;  // Cache version for future migrations
}

// Storage keys — bump version when TTL or schema changes to invalidate old caches
const CACHE_KEY_PROJECTS = 'atcode_graph_projects_v5';
const CACHE_KEY_STATS = 'atcode_graph_stats_v5';
const CACHE_VERSION = 7;

// Cache TTL: 5 minutes (in milliseconds)
// Short TTL ensures stats stay reasonably fresh while still avoiding
// redundant API calls during normal browsing
const CACHE_TTL_MS = 5 * 60 * 1000;

// Old keys to migrate from
const OLD_CACHE_KEYS = ['atcode_graph_projects', 'atcode_graph_stats', 'atcode_graph_projects_v2', 'atcode_graph_stats_v2', 'atcode_graph_projects_v3', 'atcode_graph_stats_v3', 'atcode_graph_projects_v4', 'atcode_graph_stats_v4'];

/**
 * Check if running in browser
 */
function isBrowser(): boolean {
  return typeof window !== 'undefined' && typeof localStorage !== 'undefined';
}

/**
 * Get item from localStorage with type safety
 */
function getStorageItem<T>(key: string): CacheEntry<T> | null {
  if (!isBrowser()) return null;
  try {
    const item = localStorage.getItem(key);
    if (!item) return null;
    return JSON.parse(item) as CacheEntry<T>;
  } catch {
    return null;
  }
}

/**
 * Set item in localStorage with version
 */
function setStorageItem<T>(key: string, data: T): void {
  if (!isBrowser()) return;
  try {
    const entry: CacheEntry<T> = {
      data,
      timestamp: Date.now(),
      version: CACHE_VERSION,
    };
    localStorage.setItem(key, JSON.stringify(entry));
  } catch (e) {
    console.warn('[GraphCache] Failed to save to localStorage:', e);
  }
}

/**
 * Clean up old cache keys on first load
 */
function migrateOldCache(): void {
  if (!isBrowser()) return;
  try {
    OLD_CACHE_KEYS.forEach(key => {
      localStorage.removeItem(key);
    });
  } catch {
    // Ignore errors
  }
}

// Run migration on module load
migrateOldCache();

/**
 * Remove item from localStorage
 */
function removeStorageItem(key: string): void {
  if (!isBrowser()) return;
  try {
    localStorage.removeItem(key);
  } catch {
    // Ignore errors
  }
}

/**
 * Check if a cache entry is valid
 *
 * Uses TTL to determine if cache is still fresh.
 * If cache is older than CACHE_TTL_MS, it's considered stale.
 */
function isEntryValid<T>(entry: CacheEntry<T> | null): entry is CacheEntry<T> {
  if (!entry || entry.data === null || entry.data === undefined) return false;
  // Check version compatibility
  if (entry.version !== undefined && entry.version !== CACHE_VERSION) {
    return false;
  }
  // Check TTL - cache is only valid for CACHE_TTL_MS
  const now = Date.now();
  const age = now - entry.timestamp;
  return age <= CACHE_TTL_MS;
}

function recalculateStatsFromProjects(
  projects: CachedGraphProject[],
  cacheEpoch?: string | null,
  connected: boolean = true,
): CachedGraphStatsResponse {
  let totalProjects = 0;
  let totalNodes = 0;
  let totalRelationships = 0;
  const nodeTypes: Record<string, number> = {};

  for (const project of projects) {
    totalNodes += project.node_count || 0;
    totalRelationships += project.relationship_count || 0;
    if (project.has_graph) totalProjects += 1;

    if (project.node_types) {
      for (const [label, count] of Object.entries(project.node_types)) {
        nodeTypes[label] = (nodeTypes[label] || 0) + count;
      }
    }
  }

  return {
    total_projects: totalProjects,
    total_nodes: totalNodes,
    total_relationships: totalRelationships,
    node_types: nodeTypes,
    connected,
    cache_epoch: cacheEpoch,
  };
}

/**
 * Get cached projects
 */
export function getCachedProjects(): CachedGraphProjectListResponse | null {
  const entry = getStorageItem<CachedGraphProjectListResponse>(CACHE_KEY_PROJECTS);
  const valid = isEntryValid(entry);
  if (!valid) return null;
  return entry.data;
}

/**
 * Get cached stats
 */
export function getCachedStats(): CachedGraphStatsResponse | null {
  const entry = getStorageItem<CachedGraphStatsResponse>(CACHE_KEY_STATS);
  const valid = isEntryValid(entry);
  if (!valid) return null;

  const stats = entry.data;
  const projectsEntry = getStorageItem<CachedGraphProjectListResponse>(CACHE_KEY_PROJECTS);
  if (isEntryValid(projectsEntry)) {
    const projects = projectsEntry.data.projects || [];
    const cachedStatsAreZero =
      stats.total_projects === 0 &&
      stats.total_nodes === 0 &&
      stats.total_relationships === 0;
    const projectsHaveGraphData = projects.some(
      project =>
        project.has_graph ||
        (project.node_count || 0) > 0 ||
        (project.relationship_count || 0) > 0
    );

    if (cachedStatsAreZero && projectsHaveGraphData) {
      const rebuiltStats = recalculateStatsFromProjects(
        projects,
        projectsEntry.data.cache_epoch,
        projectsEntry.data.connected !== false
      );
      setCachedStats(rebuiltStats);
      return rebuiltStats;
    }
  }

  return stats;
}

/**
 * Set cached projects
 */
export function setCachedProjects(data: CachedGraphProjectListResponse): void {
  setStorageItem(CACHE_KEY_PROJECTS, data);
}

/**
 * Set cached stats
 */
export function setCachedStats(data: CachedGraphStatsResponse): void {
  setStorageItem(CACHE_KEY_STATS, data);
}

/**
 * Invalidate the entire cache
 * Call this after graph operations (refresh, clean, etc.)
 */
export function invalidateGraphCache(): void {
  removeStorageItem(CACHE_KEY_PROJECTS);
  removeStorageItem(CACHE_KEY_STATS);
}

/**
 * Update a single project's graph data in the cache
 * This avoids fetching all projects again when only one project changed
 */
export function updateProjectInCache(
  projectName: string,
  nodeCount: number,
  relationshipCount: number,
  hasGraph: boolean,
  nodeTypes?: Record<string, number>
): void {
  const cachedProjects = getCachedProjects();
  if (!cachedProjects) return;

  const projectIndex = cachedProjects.projects.findIndex(p => p.name === projectName);
  if (projectIndex >= 0) {
    // Update existing project
    cachedProjects.projects[projectIndex] = {
      ...cachedProjects.projects[projectIndex],
      node_count: nodeCount,
      relationship_count: relationshipCount,
      has_graph: hasGraph,
      node_types: nodeTypes,
    };
  } else if (hasGraph) {
    // Add new project if it has a graph
    cachedProjects.projects.push({
      name: projectName,
      node_count: nodeCount,
      relationship_count: relationshipCount,
      has_graph: hasGraph,
      node_types: nodeTypes,
    });
    cachedProjects.total = cachedProjects.projects.length;
  }

  setCachedProjects(cachedProjects);

  // Also update global stats — recalculate from all projects
  const cachedStats = getCachedStats();
  if (cachedStats) {
    let totalNodes = 0;
    let totalRelationships = 0;
    const globalNodeTypes: Record<string, number> = {};
    cachedProjects.projects.forEach(p => {
      totalNodes += p.node_count;
      totalRelationships += p.relationship_count;
      if (p.node_types) {
        for (const [label, count] of Object.entries(p.node_types)) {
          globalNodeTypes[label] = (globalNodeTypes[label] || 0) + count;
        }
      }
    });
    cachedStats.total_nodes = totalNodes;
    cachedStats.total_relationships = totalRelationships;
    cachedStats.total_projects = cachedProjects.projects.filter(p => p.has_graph).length;
    // Only update node_types if we have per-project data
    if (Object.keys(globalNodeTypes).length > 0) {
      cachedStats.node_types = globalNodeTypes;
    }
    setCachedStats(cachedStats);
  }
}

/**
 * Remove a project from the cache (when its graph is cleaned)
 */
export function removeProjectFromCache(projectName: string): void {
  const cachedProjects = getCachedProjects();
  if (!cachedProjects) return;

  const projectIndex = cachedProjects.projects.findIndex(p => p.name === projectName);
  if (projectIndex >= 0) {
    // Mark project as having no graph instead of removing
    cachedProjects.projects[projectIndex] = {
      ...cachedProjects.projects[projectIndex],
      node_count: 0,
      relationship_count: 0,
      has_graph: false,
      node_types: undefined,
    };
    setCachedProjects(cachedProjects);

    // Recalculate global stats from remaining projects
    const cachedStats = getCachedStats();
    if (cachedStats) {
      let totalNodes = 0;
      let totalRelationships = 0;
      const globalNodeTypes: Record<string, number> = {};
      cachedProjects.projects.forEach(p => {
        totalNodes += p.node_count;
        totalRelationships += p.relationship_count;
        if (p.node_types) {
          for (const [label, count] of Object.entries(p.node_types)) {
            globalNodeTypes[label] = (globalNodeTypes[label] || 0) + count;
          }
        }
      });
      cachedStats.total_nodes = totalNodes;
      cachedStats.total_relationships = totalRelationships;
      cachedStats.total_projects = cachedProjects.projects.filter(p => p.has_graph).length;
      if (Object.keys(globalNodeTypes).length > 0) {
        cachedStats.node_types = globalNodeTypes;
      }
      setCachedStats(cachedStats);
    }
  }
}

/**
 * Check if cache has been initialized (data has been fetched at least once)
 */
export function isCacheInitialized(): boolean {
  return getCachedProjects() !== null || getCachedStats() !== null;
}

/**
 * Get cache status for debugging
 */
export function getCacheStatus(): {
  projectsValid: boolean;
  statsValid: boolean;
  projectsAge: number;
  statsAge: number;
  projectsFresh: boolean;  // True if age < 50% of TTL
  statsFresh: boolean;     // True if age < 50% of TTL
  ttl: number;             // TTL in milliseconds
} {
  const now = Date.now();
  const projectsEntry = getStorageItem<CachedGraphProjectListResponse>(CACHE_KEY_PROJECTS);
  const statsEntry = getStorageItem<CachedGraphStatsResponse>(CACHE_KEY_STATS);
  const projectsAge = projectsEntry ? now - projectsEntry.timestamp : -1;
  const statsAge = statsEntry ? now - statsEntry.timestamp : -1;

  return {
    projectsValid: isEntryValid(projectsEntry),
    statsValid: isEntryValid(statsEntry),
    projectsAge,
    statsAge,
    projectsFresh: projectsAge >= 0 && projectsAge < (CACHE_TTL_MS / 2),
    statsFresh: statsAge >= 0 && statsAge < (CACHE_TTL_MS / 2),
    ttl: CACHE_TTL_MS,
  };
}

/**
 * Export the TTL constant for use in other modules
 */
export { CACHE_TTL_MS };

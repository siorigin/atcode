'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState, useCallback, useRef, useMemo, lazy, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';

function fallbackCopyText(text: string): boolean {
  try {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    textarea.style.pointerEvents = 'none';
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(textarea);
    return ok;
  } catch {
    return false;
  }
}

/**
 * Handles redirect from /repos?repo=xxx&paper_id=yyy to /repos/xxx?paper_id=yyy.
 * Must be wrapped in Suspense because useSearchParams() requires it in App Router.
 */
function RepoRedirectHandler() {
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    const repoParam = searchParams.get('repo');
    if (repoParam) {
      const paperId = searchParams.get('paper_id');
      const target = paperId
        ? `/repos/${encodeURIComponent(repoParam)}?paper_id=${encodeURIComponent(paperId)}`
        : `/repos/${encodeURIComponent(repoParam)}`;
      router.replace(target);
    }
  }, [searchParams, router]);

  return null;
}
import { useTheme } from '@/lib/theme-context';
import { useTranslation } from '@/lib/i18n';
import { useGenerationStore } from '@/lib/store';
import { RepoCardSkeleton } from '@/components/Skeleton';
import { RepoEmptyState } from '@/components/EmptyState';
import { useToast } from '@/components/Toast';
import { Card } from '@/components/Card';
import { LanguageSwitcher } from '@/components/LanguageSwitcher';
// Import commonly used components directly (no lazy loading)
import { Modal } from '@/components/Modal';
import { DropdownMenu } from '@/components/DropdownMenu';
import {
  listGraphProjects,
  syncGraphCache,
  cleanProjectGraph,
  cleanAllGraphs,
  refreshProjectGraph,
  invalidateGraphCache,
  updateProjectGraphCache,
  getGraphJobStatus,
  GraphProject,
  GraphStatsResponse,
} from '@/lib/graph-api';
import { getThemeColors } from '@/lib/theme-colors';
import {
  getCachedProjects,
  removeProjectFromCache,
} from '@/lib/graph-cache';
import { useGlobalTasks, triggerTaskRefresh, GlobalTask } from '@/lib/hooks/useGlobalTasks';
import { apiFetch } from '@/lib/api-client';
import { getMcpEndpoint as resolveMcpEndpoint } from '@/lib/api-config';

// Lazy load ONLY heavy/rarely-used components
const SyncPanelWrapper = lazy(() => import('@/components/SyncPanel').then(m => ({ default: m.SyncPanel })));
const FloatingFeedbackWidgetWrapper = lazy(() => import('@/components/FloatingFeedbackWidget').then(m => ({ default: m.FloatingFeedbackWidget })));

// Aliases for lazy loaded components
const SyncPanel = SyncPanelWrapper;
const FloatingFeedbackWidget = FloatingFeedbackWidgetWrapper;

// Loading fallback for lazy loaded components
function LoadingFallback() {
  return <div style={{ minHeight: '100px' }} />;
}

// Centered floating panel for SyncPanel (no blur backdrop, click-outside to close)
function SyncPanelPopover({
  repo,
  colors,
  onClose,
  onSyncComplete,
  onCheckoutStart,
  onError,
}: {
  repo: RepoWithGraph;
  colors: any;
  onClose: () => void;
  onSyncComplete: () => Promise<void>;
  onCheckoutStart: (taskId: string) => Promise<void>;
  onError: (error: string) => void;
}) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleEscape);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [onClose]);

  return (
    <div style={{
      position: 'fixed',
      inset: 0,
      zIndex: 200,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      pointerEvents: 'none',
    }}>
      <div
        ref={panelRef}
        style={{
          pointerEvents: 'auto',
          width: '860px',
          maxWidth: 'calc(100vw - 48px)',
          maxHeight: 'calc(100vh - 60px)',
          display: 'flex',
          flexDirection: 'column',
          background: colors.card,
          border: `1px solid ${colors.borderLight}`,
          borderRadius: '14px',
          boxShadow: `0 16px 48px ${colors.shadowColor || 'rgba(0,0,0,0.2)'}`,
          animation: 'fadeInDown 150ms ease',
        }}
      >
        {/* Header (sticky) */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '14px 24px',
          borderBottom: `1px solid ${colors.borderLight}`,
          flexShrink: 0,
        }}>
          <span style={{ fontSize: '16px', fontWeight: 600, color: colors.text }}>
            Sync Settings - {repo.name}
          </span>
          <button
            onClick={onClose}
            style={{
              background: 'none',
              border: 'none',
              color: colors.textMuted,
              cursor: 'pointer',
              padding: '4px 8px',
              fontSize: '20px',
              lineHeight: 1,
              borderRadius: '6px',
            }}
            onMouseEnter={(e) => { e.currentTarget.style.background = colors.bgHover; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'none'; }}
          >
            &times;
          </button>
        </div>
        {/* Scrollable body */}
        <div style={{ overflowY: 'auto', flex: 1 }}>
          <SyncPanel
            projectName={repo.name}
            repoPath={repo.path}
            hasGraph={repo.hasGraph}
            onSyncComplete={onSyncComplete}
            onCheckoutStart={onCheckoutStart}
            onError={onError}
          />
        </div>
      </div>
    </div>
  );
}

interface Repo {
  name: string;
  researchCount: number;
  lastUpdated: string;
  hasDocs: boolean;
  path?: string;  // Local repository path
  hasGraph?: boolean;
}

interface RepoWithGraph extends Repo {
  graphNodeCount?: number;
  graphRelationshipCount?: number;
  hasGraph?: boolean;
  syncEnabled?: boolean;
}

type RepoGraphOperationStatus = 'queued' | 'generating' | 'cleaning';

const TASK_ERROR_TOASTS_STORAGE_KEY = 'atcode_task_error_toasts_v1';
const MAX_TRACKED_TASK_ERROR_TOASTS = 200;

function deriveGraphStatsFromProjects(
  projects: GraphProject[],
  connected: boolean,
  cacheEpoch?: string | null
): GraphStatsResponse {
  const nodeTypes: Record<string, number> = {};
  let totalProjects = 0;
  let totalNodes = 0;
  let totalRelationships = 0;

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

function loadShownTaskErrorToasts(): Set<string> {
  if (typeof window === 'undefined') {
    return new Set();
  }

  try {
    const raw = sessionStorage.getItem(TASK_ERROR_TOASTS_STORAGE_KEY);
    if (!raw) {
      return new Set();
    }

    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return new Set();
    }

    return new Set(parsed.filter((value): value is string => typeof value === 'string'));
  } catch {
    return new Set();
  }
}

function persistShownTaskErrorToasts(taskIds: Set<string>): void {
  if (typeof window === 'undefined') {
    return;
  }

  try {
    const values = Array.from(taskIds);
    const trimmedValues = values.slice(-MAX_TRACKED_TASK_ERROR_TOASTS);
    sessionStorage.setItem(TASK_ERROR_TOASTS_STORAGE_KEY, JSON.stringify(trimmedValues));
  } catch {
    // Best-effort cache only.
  }
}

export default function ReposPage() {
  const router = useRouter();
  const { theme, toggleTheme, setTheme } = useTheme();
  const { t } = useTranslation();
  const { showToast } = useToast();
  const colors = getThemeColors(theme);
  const [repos, setRepos] = useState<RepoWithGraph[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [showAddRepoModal, setShowAddRepoModal] = useState(false);
  const [addMode, setAddMode] = useState<'remote' | 'local'>('remote');
  const [repoUrl, setRepoUrl] = useState('');
  const [localPath, setLocalPath] = useState('');
  const [projectName, setProjectName] = useState('');
  const [repoBranch, setRepoBranch] = useState('');
  const [repoUsername, setRepoUsername] = useState('');
  const [repoPassword, setRepoPassword] = useState('');
  const [showCredentials, setShowCredentials] = useState(false);
  const [skipEmbeddings, setSkipEmbeddings] = useState(true);
  const [isAdding, setIsAdding] = useState(false);
  const [addError, setAddError] = useState('');

  // Subdirectory scanning state for local mode
  interface SubdirInfo {
    name: string;
    path: string;
    file_count: number;
    has_python: boolean;
    has_init: boolean;
  }
  const [subdirectories, setSubdirectories] = useState<SubdirInfo[]>([]);
  const [selectedSubdirs, setSelectedSubdirs] = useState<Set<string>>(new Set());
  const [isScanning, setIsScanning] = useState(false);
  const [isAddingMultiple, setIsAddingMultiple] = useState(false);
  const [subdirSearchQuery, setSubdirSearchQuery] = useState('');
  const [projectNamePrefix, setProjectNamePrefix] = useState('');  // Project name (used as prefix for batch)
  const [showSelectedOnly, setShowSelectedOnly] = useState(false);  // Toggle: show all vs selected only

  // Dropdown and delete state
  const [openDropdown, setOpenDropdown] = useState<string | null>(null);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState<string | null>(null);
  const [showRegenerateModal, setShowRegenerateModal] = useState<RepoWithGraph | null>(null);
  const [showSyncPanel, setShowSyncPanel] = useState<RepoWithGraph | null>(null);

  // Knowledge Graph management state
  // Initialize from cache synchronously to avoid flicker on navigation
  const [graphStats, setGraphStats] = useState<GraphStatsResponse | null>(() => {
    const cachedProjects = getCachedProjects();
    return cachedProjects
      ? deriveGraphStatsFromProjects(
          cachedProjects.projects,
          cachedProjects.connected !== false,
          cachedProjects.cache_epoch
        )
      : null;
  });
  const [graphProjects, setGraphProjects] = useState<GraphProject[]>(() => getCachedProjects()?.projects ?? []);
  const [showGraphPanel, setShowGraphPanel] = useState(false);
  const [graphLoading, setGraphLoading] = useState(false);
  const [showCleanGraphConfirm, setShowCleanGraphConfirm] = useState<string | null>(null);
  const [showCleanAllConfirm, setShowCleanAllConfirm] = useState(false);
  const [cleanAllLoading, setCleanAllLoading] = useState(false);
  const [showRefreshGraphConfirm, setShowRefreshGraphConfirm] = useState<string | null>(null);
  const [rebuildSkipEmbeddings, setRebuildSkipEmbeddings] = useState(true);
  // Per-repo graph operation status: Map<repoName, 'generating' | 'cleaning' | null>
  const [graphOperationStatus, setGraphOperationStatus] = useState<Map<string, RepoGraphOperationStatus>>(new Map());

  // Feedback panel state
  const [showFeedbackPanel, setShowFeedbackPanel] = useState(false);

  // Theme dropdown state
  const [showMcpDropdown, setShowMcpDropdown] = useState(false);
  const [mcpEndpoint, setMcpEndpoint] = useState('');
  const mcpDropdownRef = useRef<HTMLDivElement>(null);
  const [showThemeDropdown, setShowThemeDropdown] = useState(false);
  const themeDropdownRef = useRef<HTMLDivElement>(null);
  const shownTaskErrorToastsRef = useRef<Set<string> | null>(null);

  // Filters
  const [graphFilter, setGraphFilter] = useState<'all' | 'with' | 'without'>('all');
  const [sortKey, setSortKey] = useState<'updated' | 'name'>('name');

  // Reference to loadGraphData for task completion callback
  const loadGraphDataRef = useRef<(forceRefresh: boolean) => Promise<GraphProject[]>>(async () => []);
  const loadReposRef = useRef<(graphList?: GraphProject[], forceRefresh?: boolean) => Promise<void>>(async () => {});

  const shouldShowTaskErrorToast = useCallback((taskId: string): boolean => {
    if (!taskId) {
      return true;
    }

    if (!shownTaskErrorToastsRef.current) {
      shownTaskErrorToastsRef.current = loadShownTaskErrorToasts();
    }

    const shownTaskErrorToasts = shownTaskErrorToastsRef.current;
    if (shownTaskErrorToasts.has(taskId)) {
      return false;
    }

    shownTaskErrorToasts.add(taskId);
    if (shownTaskErrorToasts.size > MAX_TRACKED_TASK_ERROR_TOASTS) {
      const oldestTaskId = shownTaskErrorToasts.values().next().value;
      if (oldestTaskId) {
        shownTaskErrorToasts.delete(oldestTaskId);
      }
    }
    persistShownTaskErrorToasts(shownTaskErrorToasts);
    return true;
  }, []);

  // Get active tasks from global task system (source of truth)
  // This replaces the local useGenerationStore for job cards
  const { tasks } = useGlobalTasks({
    pollInterval: 5000,
    autoStart: true,
    stopWhenInactive: true, // Stop polling when no active tasks; resumes on task-created event
    onTaskComplete: async (task: GlobalTask) => {
      console.log('Task completed:', task.task_id, task.repo_name);
      // Update only the changed project's cache, then refresh UI from cache
      if (task.repo_name && ['graph_build', 'overview_gen'].includes(task.task_type)) {
        await updateProjectGraphCache(task.repo_name);
      }
      // Refresh UI from (now updated) cache
      const cachedProjects = getCachedProjects();
      if (cachedProjects) {
        setGraphProjects(cachedProjects.projects);
        setGraphStats(
          deriveGraphStatsFromProjects(
            cachedProjects.projects,
            cachedProjects.connected !== false,
            cachedProjects.cache_epoch
          )
        );
        await loadReposRef.current(cachedProjects.projects, true);
      } else {
        // Fallback: full refresh if no cache (first time or after clean all)
        const latestProjects = await loadGraphDataRef.current(true);
        await loadReposRef.current(latestProjects, true);
      }
    },
    onTaskError: (task: GlobalTask) => {
      console.error('Task failed:', task.task_id, task.error);
      if (!shouldShowTaskErrorToast(task.task_id)) {
        return;
      }

      const taskLabel = task.repo_name || task.task_type || 'Task';
      const errorMessage = task.error || task.status_message || 'Task failed';
      showToast('error', `${taskLabel}: ${errorMessage}`);
    },
  });

  // Filter for repo-related tasks (graph_build, overview_gen, doc_gen)
  // Use activeTasks to show pending/running, plus recently completed tasks for smooth transition
  const repoTasks = tasks.filter(task =>
    ['graph_build', 'overview_gen', 'doc_gen'].includes(task.task_type) &&
    (task.status === 'pending' || task.status === 'running')
  );

  const activeGraphTasks = useMemo(
    () => tasks.filter(
      task => task.task_type === 'graph_build' &&
        (task.status === 'pending' || task.status === 'running' || task.status === 'stalled')
    ),
    [tasks]
  );

  // Legacy: keep useGenerationStore for backward compatibility but prefer useGlobalTasks
  const { addJob, removeJob } = useGenerationStore();

  const didInitRef = useRef(false);
  useEffect(() => {
    // Guard against React Strict Mode double-mount
    if (didInitRef.current) return;
    didInitRef.current = true;
    // Load graph data first, then load repos
    // Pass the fetched projects directly to avoid race condition with state updates
    const loadInitialData = async () => {
      const projects = await loadGraphData();
      await loadRepos(projects);
    };
    loadInitialData();
  }, []);

  // Load graph data (projects and stats)
  // Uses cached data by default, pass forceRefresh=true to bypass cache
  // Returns the fetched projects for immediate use
  const loadGraphData = useCallback(async (forceRefresh: boolean = false): Promise<GraphProject[]> => {
    // If not forcing refresh, check if we can use cached data
    if (!forceRefresh) {
      const cachedProjects = getCachedProjects();
      if (cachedProjects) {
        setGraphProjects(cachedProjects.projects);
        setGraphStats(
          deriveGraphStatsFromProjects(
            cachedProjects.projects,
            cachedProjects.connected !== false,
            cachedProjects.cache_epoch
          )
        );
        void (async () => {
          try {
            const freshProjectsRes = await listGraphProjects(true).catch(
              () => ({ projects: [], total: 0, connected: false, cache_epoch: null })
            );
            setGraphProjects(freshProjectsRes.projects);
            setGraphStats(
              deriveGraphStatsFromProjects(
                freshProjectsRes.projects,
                freshProjectsRes.connected !== false,
                freshProjectsRes.cache_epoch
              )
            );
          } catch (error) {
            console.warn('Background graph cache revalidation failed:', error);
          }
        })();
        return cachedProjects.projects;
      }
    }

    setGraphLoading(true);
    try {
      const projectsRes = await listGraphProjects(forceRefresh).catch(
        () => ({ projects: [], total: 0, connected: false, cache_epoch: null })
      );
      setGraphProjects(projectsRes.projects);
      setGraphStats(
        deriveGraphStatsFromProjects(
          projectsRes.projects,
          projectsRes.connected !== false,
          projectsRes.cache_epoch
        )
      );
      return projectsRes.projects; // Return for immediate use
    } catch (error) {
      console.error('Failed to load graph data:', error);
      return [];
    } finally {
      setGraphLoading(false);
    }
  }, []);

  const handleManualGraphCacheSync = useCallback(async () => {
    setGraphLoading(true);
    try {
      const syncResult = await syncGraphCache();
      const latestProjects = await loadGraphData(true);
      await loadReposRef.current(latestProjects, true);
      showToast(
        'success',
        syncResult.message ||
          `Graph cache synced (${syncResult.total_projects} projects)`
      );
    } catch (error) {
      console.error('Failed to sync graph cache:', error);
      showToast(
        'error',
        error instanceof Error ? error.message : 'Failed to sync graph cache'
      );
    } finally {
      setGraphLoading(false);
    }
  }, [loadGraphData, showToast]);

  // Merge repo data with graph data
  const mergeReposWithGraph = useCallback((repoList: Repo[], graphList: GraphProject[]): RepoWithGraph[] => {
    const repoMap = new Map(repoList.map(r => [r.name, r]));
    const graphMap = new Map(graphList.map(g => [g.name, g]));

    // Start with repos from file system
    const result: RepoWithGraph[] = repoList.map(repo => {
      const graphInfo = graphMap.get(repo.name);
      return {
        ...repo,
        // Use path from Memgraph if available (for local projects)
        path: graphInfo?.path || repo.path,
        graphNodeCount: graphInfo?.node_count ?? 0,
        graphRelationshipCount: graphInfo?.relationship_count ?? 0,
        hasGraph: graphInfo?.has_graph ?? repo.hasGraph ?? false,
        syncEnabled: graphInfo?.sync_enabled ?? false,
      };
    });

    // Add projects that only exist in Memgraph (added via local path)
    for (const graphProject of graphList) {
      if (!repoMap.has(graphProject.name)) {
        result.push({
          name: graphProject.name,
          researchCount: 0,
          lastUpdated: new Date().toISOString(),
          hasDocs: false,
          path: graphProject.path,  // Include path from Memgraph
          graphNodeCount: graphProject.node_count,
          graphRelationshipCount: graphProject.relationship_count,
          hasGraph: graphProject.has_graph,
          syncEnabled: graphProject.sync_enabled ?? false,
        });
      }
    }

    return result;
  }, []);

  const loadRepos = useCallback(async (graphList?: GraphProject[], forceRefresh: boolean = false) => {
    try {
      // Short-lived session cache (30s) to avoid refetching on page navigation
      const REPOS_CACHE_KEY = 'atcode:repos-cache';
      const REPOS_CACHE_TTL = 30_000;
      let repoList: Repo[] | null = null;

      if (!forceRefresh) {
        try {
          const raw = sessionStorage.getItem(REPOS_CACHE_KEY);
          if (raw) {
            const cached = JSON.parse(raw);
            if (Date.now() - cached.ts < REPOS_CACHE_TTL) {
              repoList = cached.data;
            }
          }
        } catch { /* ignore */ }
      }

      if (!repoList) {
        const response = await apiFetch('/api/repos');
        const data = await response.json();
        // Map backend snake_case to frontend camelCase
        repoList = (data.repos || []).map((repo: any) => ({
          name: repo.name,
          researchCount: repo.research_count ?? 0,
          lastUpdated: repo.last_updated || new Date().toISOString(),
          hasDocs: repo.has_docs ?? false,
          path: repo.path,
          hasGraph: repo.has_graph ?? false,
        }));
        try {
          sessionStorage.setItem(REPOS_CACHE_KEY, JSON.stringify({ ts: Date.now(), data: repoList }));
        } catch { /* ignore */ }
      }

      // Use provided graph list or fall back to state
      const projectsToUse = graphList ?? graphProjects ?? [];
      // Merge with graph data if available
      const mergedRepos = mergeReposWithGraph(repoList ?? [], projectsToUse);
      setRepos(mergedRepos);
    } catch (error) {
      console.error('Error loading repositories:', error);
      showToast('error', t('repos.toast.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [graphProjects, mergeReposWithGraph, showToast, t]);

  // Update refs for useGlobalTasks callbacks
  useEffect(() => {
    loadGraphDataRef.current = loadGraphData;
    loadReposRef.current = loadRepos;
  }, [loadGraphData, loadRepos]);

  // Update repos when graph projects change
  // This handles cases where graph data loads after initial repo load
  // Updates existing repos' graph data AND adds new projects from Memgraph
  useEffect(() => {
    if (graphProjects?.length > 0) {
      setRepos(prev => {
        const result = [...prev];
        const repoMap = new Map(prev.map(r => [r.name, r]));

        // Create a map of graph projects for quick lookup
        const graphMap = new Map(graphProjects.map(g => [g.name, g]));

        // Update existing repos and add new ones
        for (const [repoName, repo] of repoMap) {
          const graphProject = graphMap.get(repoName);
          if (graphProject) {
            // Update existing repo with graph data
            const index = result.findIndex(r => r.name === repoName);
            if (index >= 0) {
              result[index] = {
                ...repo,
                graphNodeCount: graphProject.node_count,
                graphRelationshipCount: graphProject.relationship_count,
                hasGraph: graphProject.has_graph,
                syncEnabled: graphProject.sync_enabled ?? false,
              };
            }
          }
        }

        // Add projects that only exist in Memgraph
        for (const graphProject of graphProjects) {
          if (!repoMap.has(graphProject.name)) {
            result.push({
              name: graphProject.name,
              researchCount: 0,
              lastUpdated: new Date().toISOString(),
              hasDocs: false,
              graphNodeCount: graphProject.node_count,
              graphRelationshipCount: graphProject.relationship_count,
              hasGraph: graphProject.has_graph,
              syncEnabled: graphProject.sync_enabled ?? false,
            });
          }
        }

        return result;
      });
    }
  }, [graphProjects]);

  // Close theme dropdown when clicking outside
  useEffect(() => {
    if (typeof window === 'undefined') return;
    setMcpEndpoint(resolveMcpEndpoint());
  }, []);

  const mcpCommands = useMemo(() => {
    if (!mcpEndpoint) return [];
    return [
      {
        key: 'claude-default',
        label: 'Claude Code',
        description: 'Default scope',
        command: `claude mcp add --transport http atcode ${mcpEndpoint}`,
      },
      {
        key: 'claude-project',
        label: 'Claude Code Project',
        description: 'Project scope',
        command: `claude mcp add --transport http --scope project atcode ${mcpEndpoint}`,
      },
      {
        key: 'claude-user',
        label: 'Claude Code User',
        description: 'User scope',
        command: `claude mcp add --transport http --scope user atcode ${mcpEndpoint}`,
      },
      {
        key: 'codex',
        label: 'Codex',
        description: 'Codex CLI',
        command: `codex mcp add atcode --url ${mcpEndpoint}`,
      },
    ];
  }, [mcpEndpoint]);

  const handleCopyMcpCommand = useCallback(async (command: string, label: string) => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(command);
      } else if (!fallbackCopyText(command)) {
        throw new Error('Clipboard API unavailable');
      }
      showToast('success', `${label} command copied`);
    } catch (error) {
      if (fallbackCopyText(command)) {
        showToast('success', `${label} command copied`);
        return;
      }
      console.error('Failed to copy MCP command:', error);
      showToast('error', 'Failed to copy command. Browser clipboard permission was denied.');
    }
  }, [showToast]);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      // Check if click is inside MCP dropdown
      if (mcpDropdownRef.current && mcpDropdownRef.current.contains(event.target as Node)) {
        return;
      }

      // Check if click is inside theme dropdown
      if (themeDropdownRef.current && themeDropdownRef.current.contains(event.target as Node)) {
        return;
      }

      if (showMcpDropdown) {
        setShowMcpDropdown(false);
      }
      if (showThemeDropdown) {
        setShowThemeDropdown(false);
      }
    }

    document.addEventListener('click', handleClickOutside);
    return () => document.removeEventListener('click', handleClickOutside);
  }, [showMcpDropdown, showThemeDropdown]);

  async function handleAddRepo() {
    // Validate based on mode
    if (addMode === 'remote' && !repoUrl.trim()) {
      setAddError(t('repos.toast.enterRepoUrl'));
      showToast('error', t('repos.toast.enterRepoUrl'));
      return;
    }
    if (addMode === 'local' && !localPath.trim()) {
      setAddError('Please enter a local path');
      showToast('error', 'Please enter a local path');
      return;
    }

    setIsAdding(true);
    setAddError('');

    try {
      let requestBody: Record<string, any>;
      let endpoint: string;

      if (addMode === 'local') {
        // Local mode: use /api/repos/add-local
        endpoint = '/api/repos/add-local';
        requestBody = {
          local_path: localPath.trim(),
          project_name: projectName.trim() || undefined,
          skip_embeddings: skipEmbeddings,
        };
      } else {
        // Remote mode: use /api/repos/add
        endpoint = '/api/repos/add';
        requestBody = {
          repo_url: repoUrl.trim(),
          branch: repoBranch.trim() || undefined,
          username: repoUsername.trim() || undefined,
          password: repoPassword || undefined,
          skip_embeddings: skipEmbeddings,
        };
      }

      const response = await apiFetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || data.message || data.error || 'Failed to add repository');
      }

      // Add job to store for tracking in card grid
      addJob({
        id: data.task_id,
        type: 'repo',
        name: data.repo_name,
        status: 'pending',
        progress: 0,
        logs: [],
      });

      showToast('success', t('repos.toast.addStarted', { name: data.repo_name }));

      // Trigger task refresh to show the new task in TaskStatusPanel
      triggerTaskRefresh();

      // Close modal and reset form
      setShowAddRepoModal(false);
      setAddMode('remote');
      setRepoUrl('');
      setLocalPath('');
      setProjectName('');
      setRepoBranch('');
      setRepoUsername('');
      setRepoPassword('');
      setShowCredentials(false);
      setSkipEmbeddings(false);

      // Don't redirect - let user monitor via TaskStatusPanel
      // The floating button at bottom-right will show progress

    } catch (error: any) {
      setAddError(error.message || t('repos.toast.addFailed'));
      showToast('error', error.message || t('repos.toast.addFailed'));
    } finally {
      setIsAdding(false);
    }
  }

  // Scan subdirectories for local mode batch addition
  async function handleScanSubdirectories() {
    if (!localPath.trim()) {
      setAddError('Please enter a local path first');
      showToast('error', 'Please enter a local path first');
      return;
    }

    setIsScanning(true);
    setAddError('');
    setSubdirectories([]);
    setSubdirSearchQuery('');
    setSelectedSubdirs(new Set());
    setProjectNamePrefix('');

    try {
      const response = await apiFetch(`/api/repos/list-subdirectories?path=${encodeURIComponent(localPath.trim())}`);
      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || data.error || 'Failed to scan subdirectories');
      }

      setSubdirectories(data.subdirectories || []);
      if (data.subdirectories?.length === 0) {
        showToast('info', 'No subdirectories found in this path');
      } else {
        showToast('success', `Found ${data.subdirectories.length} subdirectories`);
      }
    } catch (error: any) {
      setAddError(error.message || 'Failed to scan subdirectories');
      showToast('error', error.message || 'Failed to scan subdirectories');
    } finally {
      setIsScanning(false);
    }
  }

  // Batch add selected subdirectories as separate projects
  async function handleAddMultipleLocalRepos() {
    if (selectedSubdirs.size === 0) {
      showToast('error', 'Please select at least one subdirectory');
      return;
    }

    // Get project name from input (handle local paths, not URLs)
    const finalProjectName = projectNamePrefix.trim() || localPath.split('/').filter(Boolean).pop() || 'project';

    setIsAddingMultiple(true);
    setAddError('');

    try {
      // Get selected subdirectory names (not full paths)
      const selectedSubdirNames = subdirectories
        .filter(subdir => selectedSubdirs.has(subdir.path))
        .map(subdir => subdir.name);

      const response = await apiFetch('/api/repos/add-multiple-local', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_name: finalProjectName,
          local_path: localPath,
          subdirs: selectedSubdirNames,
          skip_embeddings: skipEmbeddings,
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || data.error || 'Failed to add repository');
      }

      // Add job to store for tracking
      if (data.success && data.job_id) {
        addJob({
          id: data.job_id,
          type: 'repo',
          name: data.project_name,
          status: 'pending',
          progress: 0,
          logs: [],
        });
      }

      showToast('success', `Started building graph for ${finalProjectName} with ${selectedSubdirNames.length} subdirectories`);

      // Trigger task refresh
      triggerTaskRefresh();

      // Close modal and reset form
      setShowAddRepoModal(false);
      setAddMode('remote');
      setRepoUrl('');
      setLocalPath('');
      setProjectName('');
      setRepoBranch('');
      setRepoUsername('');
      setRepoPassword('');
      setShowCredentials(false);
      setSkipEmbeddings(false);
      setSubdirectories([]);
      setSubdirSearchQuery('');
      setSelectedSubdirs(new Set());
      setProjectNamePrefix('');
      setShowSelectedOnly(false);

    } catch (error: any) {
      setAddError(error.message || 'Failed to add repository');
      showToast('error', error.message || 'Failed to add repository');
    } finally {
      setIsAddingMultiple(false);
    }
  }

  // Toggle subdirectory selection
  function toggleSubdirSelection(path: string) {
    setSelectedSubdirs(prev => {
      const newSet = new Set(prev);
      if (newSet.has(path)) {
        newSet.delete(path);
      } else {
        newSet.add(path);
      }
      return newSet;
    });
  }

  // Select/deselect all subdirectories
  function toggleSelectAllSubdirs() {
    if (selectedSubdirs.size === subdirectories.length) {
      setSelectedSubdirs(new Set());
        setProjectNamePrefix('');
    } else {
      setSelectedSubdirs(new Set(subdirectories.map(s => s.path)));
    }
  }

  async function handleDeleteRepo(repoName: string) {
    try {
      const response = await apiFetch(`/api/repos/${repoName}`, {
        method: 'DELETE',
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || t('repos.toast.deleteFailed'));
      }

      showToast('success', t('repos.toast.deleteSuccess'));
      // Force refresh from server to ensure UI shows latest data
      await updateProjectUIFromCache(repoName);
      setShowDeleteConfirm(null);
    } catch (error: any) {
      showToast('error', error.message || t('repos.toast.deleteFailed'));
    }
  }

  async function handleRegenerateRepo() {
    if (!showRegenerateModal) return;

    try {
      const response = await apiFetch('/api/regenerate-repo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repoName: showRegenerateModal.name }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || t('repos.toast.regenerateFailed'));
      }

      addJob({
        id: data.jobId,
        type: 'repo',
        name: showRegenerateModal.name,
        status: 'pending',
        progress: 0,
        logs: [],
      });

      showToast('success', `Started regenerating ${showRegenerateModal.name} documentation, check progress in task panel`);
      setShowRegenerateModal(null);

      // Don't redirect - let user monitor via TaskStatusPanel
    } catch (error: any) {
      showToast('error', error.message || 'Regeneration failed');
    }
  }

  // Handle cleaning a project's knowledge graph
  // Force refresh from server to ensure UI shows correct data
  const updateProjectUIFromCache = useCallback(async (projectName: string) => {
    // Update only this project's stats in cache (lightweight single-project query)
    await updateProjectGraphCache(projectName);
    // Refresh UI from updated cache
    const cached = getCachedProjects();
    if (cached) {
      setGraphProjects(cached.projects);
      setGraphStats(
        deriveGraphStatsFromProjects(
          cached.projects,
          cached.connected !== false,
          cached.cache_epoch
        )
      );
      await loadRepos(cached.projects, true);
    } else {
      // Fallback: full refresh if no cache exists
      const latestProjects = await loadGraphData(true);
      await loadRepos(latestProjects, true);
    }
  }, [loadGraphData, loadRepos]);

  async function handleCleanGraph(projectName: string) {
    setGraphOperationStatus(prev => new Map(prev).set(projectName, 'cleaning'));
    try {
      const result = await cleanProjectGraph(projectName);
      if (result.success) {
        showToast('success', `Cleaned knowledge graph for ${projectName}, deleted ${result.deleted_nodes} nodes`);
        // Force refresh from server to ensure UI shows latest data
        await updateProjectUIFromCache(projectName);
      } else {
        showToast('error', result.message || 'Cleanup failed');
      }
    } catch (error: any) {
      showToast('error', error.message || 'Failed to clean knowledge graph');
    } finally {
      setGraphOperationStatus(prev => {
        const next = new Map(prev);
        next.delete(projectName);
        return next;
      });
      setShowCleanGraphConfirm(null);
    }
  }

  // Clean ALL knowledge graph data
  async function handleCleanAllGraphs() {
    setCleanAllLoading(true);
    try {
      const result = await cleanAllGraphs();
      if (result.success) {
        showToast('success', 'All knowledge graph data has been cleaned');
        // Refresh the graph data from server and update repos
        const latestProjects = await loadGraphData(true);
        await loadRepos(latestProjects, true);
      } else {
        showToast('error', result.message || 'Failed to clean all graphs');
      }
    } catch (error: any) {
      showToast('error', error.message || 'Failed to clean all knowledge graphs');
    } finally {
      setCleanAllLoading(false);
      setShowCleanAllConfirm(false);
    }
  }

  // Poll for job completion and update UI when done
  async function pollJobCompletion(jobId: string, projectName: string) {
    const maxAttempts = 120; // Max 10 minutes (120 * 5 seconds)
    let attempts = 0;

    const poll = async () => {
      attempts++;
      try {
        const status = await getGraphJobStatus(jobId);

        if (status.status === 'completed') {
          // Job completed successfully - force refresh from server
          await updateProjectUIFromCache(projectName);
          showToast('success', `${projectName} knowledge graph build complete`);
          setGraphOperationStatus(prev => {
            const next = new Map(prev);
            next.delete(projectName);
            return next;
          });
          // Remove job from store to avoid duplicate cards
          removeJob(jobId);
        } else if (status.status === 'failed') {
          showToast('error', `${projectName} knowledge graph build failed: ${status.error || status.message}`);
          setGraphOperationStatus(prev => {
            const next = new Map(prev);
            next.delete(projectName);
            return next;
          });
          // Remove failed job from store
          removeJob(jobId);
        } else if (attempts < maxAttempts) {
          // Still running, poll again after 8 seconds (increased to reduce load)
          setTimeout(poll, 8000);
        } else {
          // Timeout - stop polling but leave status indicator
          console.warn(`Job ${jobId} polling timeout after ${maxAttempts} attempts`);
          showToast('info', `${projectName} knowledge graph build is taking longer, please refresh manually later`);
          setGraphOperationStatus(prev => {
            const next = new Map(prev);
            next.delete(projectName);
            return next;
          });
          // Remove job from store on timeout
          removeJob(jobId);
        }
      } catch (error) {
        // Job not found or error - might be completed and cleaned up
        // Force refresh from server to get latest state
        console.warn(`Failed to get job status for ${jobId}:`, error);
        await updateProjectUIFromCache(projectName);
        setGraphOperationStatus(prev => {
          const next = new Map(prev);
          next.delete(projectName);
          return next;
        });
        // Remove job from store on error (assume completed)
        removeJob(jobId);
      }
    };

    // Start polling after initial delay
    setTimeout(poll, 3000);
  }

  // Handle refreshing/generating a project's knowledge graph
  async function handleRefreshGraph(projectName: string, fastMode: boolean = false) {
    setGraphOperationStatus(prev => new Map(prev).set(projectName, 'queued'));
    try {
      const result = await refreshProjectGraph(projectName, { fastMode });
      if (result.success) {
        const isNew = !repos.find(r => r.name === projectName)?.hasGraph;
        const modeMsg = fastMode ? ' (fast mode)' : '';
        const queuedMatch = result.message?.match(/position (\d+)/i);
        const isQueued = /queued/i.test(result.message || '');

        setGraphOperationStatus(prev => new Map(prev).set(projectName, isQueued ? 'queued' : 'generating'));

        if (isQueued) {
          const queueSuffix = queuedMatch ? ` (position ${queuedMatch[1]})` : '';
          showToast(
            'success',
            `${projectName} added to the graph build queue${queueSuffix}${modeMsg}`
          );
        } else {
          showToast(
            'success',
            `Started ${isNew ? 'generating' : 'refreshing'} knowledge graph for ${projectName}${modeMsg}`
          );
        }

        // Trigger task refresh to show the new task in TaskStatusPanel
        triggerTaskRefresh();

        // Start polling for job completion
        if (result.job_id) {
          pollJobCompletion(result.job_id, projectName);
        } else {
          // No job_id - fall back to delayed update
          setTimeout(async () => {
            await updateProjectUIFromCache(projectName);
            setGraphOperationStatus(prev => {
              const next = new Map(prev);
              next.delete(projectName);
              return next;
            });
          }, 5000);
        }
      } else {
        showToast('error', result.message || 'Operation failed');
        setGraphOperationStatus(prev => {
          const next = new Map(prev);
          next.delete(projectName);
          return next;
        });
      }
    } catch (error: any) {
      showToast('error', error.message || 'Failed to operate on knowledge graph');
      setGraphOperationStatus(prev => {
        const next = new Map(prev);
        next.delete(projectName);
        return next;
      });
    } finally {
      setShowRefreshGraphConfirm(null);
    }
  }

  // Helper to check if a repo has ongoing graph operation
  // Checks both local graphOperationStatus and global tasks
  const getRepoGraphStatus = useCallback((repoName: string): RepoGraphOperationStatus | null => {
    // Active tasks are the source of truth once they appear
    const activeTask = repoTasks.find(
      task => task.repo_name === repoName &&
        task.task_type === 'graph_build' &&
        (task.status === 'pending' || task.status === 'running' || task.status === 'stalled')
    );
    if (activeTask) {
      return activeTask.status === 'pending' ? 'queued' : 'generating';
    }

    const localStatus = graphOperationStatus.get(repoName);
    if (localStatus) return localStatus;

    return null;
  }, [graphOperationStatus, repoTasks]);

  const knowledgeGraphRepos = useMemo(() => {
    const getPriority = (repo: RepoWithGraph) => {
      const status = getRepoGraphStatus(repo.name);
      if (status === 'cleaning') return 0;
      if (status === 'generating') return 1;
      if (status === 'queued') return 2;
      if (!repo.hasGraph) return 3;
      return 4;
    };

    return [...repos].sort((a, b) => {
      const priorityDiff = getPriority(a) - getPriority(b);
      if (priorityDiff !== 0) return priorityDiff;
      if (!!a.hasGraph !== !!b.hasGraph) return a.hasGraph ? 1 : -1;
      return a.name.localeCompare(b.name);
    });
  }, [getRepoGraphStatus, repos]);

  const filteredRepos = repos
    .filter((repo) => repo.name.toLowerCase().includes(searchQuery.toLowerCase()))
    .filter((repo) => {
      if (graphFilter === 'with') return !!repo.hasGraph;
      if (graphFilter === 'without') return !repo.hasGraph;
      return true;
    })
    .sort((a, b) => {
      if (sortKey === 'name') return a.name.localeCompare(b.name);
      const aTime = a.lastUpdated ? new Date(a.lastUpdated).getTime() : 0;
      const bTime = b.lastUpdated ? new Date(b.lastUpdated).getTime() : 0;
      return bTime - aTime;
    });

  if (loading) {
    return (
      <div style={{
        background: colors.bg,
        minHeight: '100vh',
        padding: '0 48px 80px'
      }}>
        {/* Header skeleton */}
        <header style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '16px 0',
          marginBottom: '48px'
        }}>
          <div style={{
            width: '120px',
            height: '24px',
            background: colors.card,
            borderRadius: '8px',
            animation: 'shimmer 2s infinite'
          }} />
        </header>

        {/* Grid skeleton */}
        <main style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
          gap: '24px',
          maxWidth: '1400px',
          margin: '0 auto'
        }}>
          {[1, 2, 3, 4, 5, 6].map(i => (
            <RepoCardSkeleton key={i} />
          ))}
        </main>
      </div>
    );
  }

  return (
    <div style={{
      background: colors.bg,
      color: colors.text,
      minHeight: '100vh',
      fontFamily: 'var(--font-sans)',
    }}>
      {/* Redirect handler for /repos?repo=xxx&paper_id=yyy */}
      <Suspense>
        <RepoRedirectHandler />
      </Suspense>
      {/* Header */}
      <header style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        padding: '12px 32px',
        borderBottom: `1px solid ${colors.borderLight}`,
        background: colors.bgOverlay,
        position: 'sticky',
        top: 0,
        zIndex: 100,
        backdropFilter: 'blur(12px)',
        WebkitBackdropFilter: 'blur(12px)',
      }}>
        <h1 style={{
          fontSize: '18px',
          fontWeight: '600',
          color: colors.text,
          letterSpacing: '-0.02em',
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
        }}>
          <img
            src="/logo.png"
            alt="AtCode"
            style={{
              width: '28px',
              height: '28px',
              borderRadius: '6px',
              objectFit: 'contain',
            }}
          />
          AtCode
        </h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          {/* Knowledge Graph Stats Badge */}
          {graphStats && graphStats.connected && (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '6px 12px',
                background: colors.infoBg,
                borderRadius: '20px',
                fontSize: '12px',
                color: colors.info,
                border: `1px solid ${colors.infoBorder}`,
              }}
            >
              <span style={{ fontSize: '14px' }}>🔗</span>
              <span>{graphStats.total_nodes.toLocaleString()} nodes</span>
              <span style={{ opacity: 0.6 }}>|</span>
              <span>{graphStats.total_relationships.toLocaleString()} edges</span>
            </div>
          )}
          {/* Feedback Button */}
          <button
            onClick={() => setShowFeedbackPanel(true)}
            style={{
              padding: '8px 14px',
              background: 'transparent',
              border: `1px solid ${colors.borderLight}`,
              borderRadius: '10px',
              fontSize: '13px',
              cursor: 'pointer',
              transition: 'all 150ms ease-out',
              fontWeight: '500',
              color: colors.textSecondary,
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = colors.bgHover;
              e.currentTarget.style.borderColor = colors.border;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'transparent';
              e.currentTarget.style.borderColor = colors.borderLight;
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
            Feedback
          </button>
          {/* MCP Install Commands */}
          <div ref={mcpDropdownRef} style={{ position: 'relative', zIndex: 250 }}>
            <button
              onClick={(e) => {
                e.stopPropagation();
                setShowThemeDropdown(false);
                setShowMcpDropdown(!showMcpDropdown);
              }}
              style={{
                padding: '8px 14px',
                background: 'transparent',
                border: `1px solid ${colors.borderLight}`,
                borderRadius: '10px',
                fontSize: '13px',
                cursor: 'pointer',
                transition: 'all 150ms ease-out',
                fontWeight: '500',
                color: colors.textSecondary,
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = colors.bgHover;
                e.currentTarget.style.borderColor = colors.border;
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = 'transparent';
                e.currentTarget.style.borderColor = colors.borderLight;
              }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M8 3H5a2 2 0 0 0-2 2v3" />
                <path d="M16 3h3a2 2 0 0 1 2 2v3" />
                <path d="M8 21H5a2 2 0 0 1-2-2v-3" />
                <path d="M16 21h3a2 2 0 0 0 2-2v-3" />
                <path d="M12 8v8" />
                <path d="m9 11 3-3 3 3" />
              </svg>
              MCP
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ opacity: 0.5 }}>
                <path d="m6 9 6 6 6-6"/>
              </svg>
            </button>
            {showMcpDropdown && (
              <div
                style={{
                  position: 'absolute',
                  top: '100%',
                  right: 0,
                  marginTop: '6px',
                  width: '460px',
                  maxWidth: 'min(460px, calc(100vw - 32px))',
                  background: colors.card,
                  border: `1px solid ${colors.borderLight}`,
                  borderRadius: '12px',
                  boxShadow: `0 8px 24px ${colors.shadowColor}`,
                  zIndex: 300,
                  overflow: 'hidden',
                  animation: 'fadeInDown 150ms ease-out',
                }}
              >
                <div style={{ padding: '14px 16px 10px', borderBottom: `1px solid ${colors.borderLight}` }}>
                  <div style={{ fontSize: '14px', fontWeight: 600, color: colors.text }}>
                    MCP Install Commands
                  </div>
                  <div style={{ fontSize: '12px', color: colors.textSecondary, marginTop: '4px', lineHeight: 1.5 }}>
                    Copy these commands into Claude Code or Codex. Endpoint defaults to the backend host and API port for direct MCP access.
                  </div>
                  {mcpEndpoint && (
                    <div style={{
                      marginTop: '10px',
                      padding: '8px 10px',
                      borderRadius: '8px',
                      background: colors.bgHover,
                      border: `1px solid ${colors.borderLight}`,
                      fontSize: '11px',
                      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                      color: colors.textSecondary,
                      wordBreak: 'break-all',
                    }}>
                      {mcpEndpoint}
                    </div>
                  )}
                </div>
                <div style={{ padding: '10px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  {mcpCommands.map((item) => (
                    <div
                      key={item.key}
                      style={{
                        border: `1px solid ${colors.borderLight}`,
                        borderRadius: '10px',
                        padding: '12px',
                        background: colors.bgSecondary,
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', marginBottom: '8px' }}>
                        <div>
                          <div style={{ fontSize: '13px', fontWeight: 600, color: colors.text }}>
                            {item.label}
                          </div>
                          <div style={{ fontSize: '11px', color: colors.textMuted, marginTop: '2px' }}>
                            {item.description}
                          </div>
                        </div>
                        <button
                          onClick={() => handleCopyMcpCommand(item.command, item.label)}
                          style={{
                            padding: '6px 10px',
                            borderRadius: '8px',
                            border: `1px solid ${colors.border}`,
                            background: colors.card,
                            color: colors.textSecondary,
                            fontSize: '12px',
                            fontWeight: 500,
                            cursor: 'pointer',
                          }}
                        >
                          Copy
                        </button>
                      </div>
                      <div
                        style={{
                          padding: '10px 12px',
                          borderRadius: '8px',
                          background: colors.bgTertiary,
                          color: colors.text,
                          fontSize: '11px',
                          lineHeight: 1.6,
                          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                          wordBreak: 'break-all',
                        }}
                      >
                        {item.command}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
          {/* Knowledge Graph Management Button */}
          <button
            onClick={() => setShowGraphPanel(true)}
            style={{
              padding: '8px 14px',
              background: colors.accentBg,
              border: `1px solid ${colors.accentBorder}`,
              borderRadius: '10px',
              fontSize: '13px',
              cursor: 'pointer',
              transition: 'all 150ms ease-out',
              fontWeight: '500',
              color: colors.accent,
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = colors.accent;
              e.currentTarget.style.color = '#ffffff';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = colors.accentBg;
              e.currentTarget.style.color = colors.accent;
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="3"/>
              <circle cx="19" cy="5" r="2"/>
              <circle cx="5" cy="19" r="2"/>
              <path d="M14.5 9.5 17 7M9.5 14.5 7 17"/>
            </svg>
            Knowledge Graph
          </button>
          {/* Theme Dropdown */}
          <div ref={themeDropdownRef} style={{ position: 'relative', zIndex: 250 }}>
            <button
              onClick={(e) => {
                e.stopPropagation();
                setShowMcpDropdown(false);
                setShowThemeDropdown(!showThemeDropdown);
              }}
              style={{
                padding: '8px 14px',
                background: 'transparent',
                border: `1px solid ${colors.borderLight}`,
                borderRadius: '10px',
                fontSize: '13px',
                cursor: 'pointer',
                transition: 'all 150ms ease-out',
                fontWeight: '500',
                color: colors.textSecondary,
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = colors.bgHover;
                e.currentTarget.style.borderColor = colors.border;
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = 'transparent';
                e.currentTarget.style.borderColor = colors.borderLight;
              }}
            >
              {theme === 'dark' ? (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
                </svg>
              ) : theme === 'beige' ? (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/>
                  <circle cx="12" cy="12" r="3"/>
                </svg>
              ) : (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="5"/>
                  <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>
                </svg>
              )}
              <span>{theme === 'dark' ? 'Dark' : theme === 'beige' ? 'Comfort' : 'Light'}</span>
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ opacity: 0.5 }}>
                <path d="m6 9 6 6 6-6"/>
              </svg>
            </button>
            {showThemeDropdown && (
              <div
                style={{
                  position: 'absolute',
                  top: '100%',
                  right: 0,
                  marginTop: '6px',
                  background: colors.card,
                  border: `1px solid ${colors.borderLight}`,
                  borderRadius: '12px',
                  boxShadow: `0 8px 24px ${colors.shadowColor}`,
                  minWidth: '160px',
                  zIndex: 300,
                  overflow: 'hidden',
                  animation: 'fadeInDown 150ms ease-out',
                  padding: '4px',
                }}
              >
                {[
                  { value: 'dark' as const, label: 'Dark', icon: (
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
                    </svg>
                  ) },
                  { value: 'light' as const, label: 'Light', icon: (
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <circle cx="12" cy="12" r="5"/>
                      <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>
                    </svg>
                  ) },
                  { value: 'beige' as const, label: 'Eye Comfort', icon: (
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/>
                      <circle cx="12" cy="12" r="3"/>
                    </svg>
                  ) },
                ].map((option) => (
                  <button
                    key={option.value}
                    onClick={() => {
                      setTheme(option.value);
                      setShowThemeDropdown(false);
                    }}
                    style={{
                      width: '100%',
                      padding: '10px 12px',
                      background: theme === option.value ? colors.accentBg : 'transparent',
                      border: 'none',
                      borderRadius: '8px',
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '10px',
                      fontSize: '13px',
                      color: theme === option.value ? colors.accent : colors.textSecondary,
                      fontWeight: theme === option.value ? '500' : '400',
                      transition: 'all 150ms ease-out',
                      marginBottom: option.value !== 'beige' ? '2px' : '0',
                    }}
                    onMouseEnter={(e) => {
                      if (theme !== option.value) {
                        e.currentTarget.style.background = colors.bgHover;
                        e.currentTarget.style.color = colors.text;
                      }
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = theme === option.value ? colors.accentBg : 'transparent';
                      e.currentTarget.style.color = theme === option.value ? colors.accent : colors.textSecondary;
                    }}
                  >
                    {option.icon}
                    <span>{option.label}</span>
                    {theme === option.value && (
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ marginLeft: 'auto' }}>
                        <polyline points="20 6 9 17 4 12"/>
                      </svg>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Language Switcher */}
          <LanguageSwitcher />
        </div>
      </header>

      {/* Page Title */}
      <section style={{ textAlign: 'center', marginTop: '48px', marginBottom: '32px' }}>
        <h2 style={{
          fontSize: '28px',
          fontWeight: '600',
          color: colors.text,
          letterSpacing: '-0.02em',
          marginBottom: '8px',
        }}>
          {t('repos.subtitle')}
        </h2>
        <p style={{
          fontSize: '14px',
          color: colors.textMuted,
          fontWeight: '400',
        }}>
          {t('repos.addDescription')}
        </p>
      </section>

      {/* Search Box */}
      <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '48px' }}>
        <div style={{ position: 'relative', width: '100%', maxWidth: '560px', padding: '0 24px' }}>
          <div style={{
            position: 'absolute',
            left: '40px',
            top: '50%',
            transform: 'translateY(-50%)',
            color: colors.textMuted,
            pointerEvents: 'none',
          }}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="11" cy="11" r="8"/>
              <path d="m21 21-4.35-4.35"/>
            </svg>
          </div>
          <input
            type="text"
            placeholder={t('repos.searchPlaceholder')}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{
              width: '100%',
              padding: '14px 20px 14px 48px',
              background: colors.card,
              border: `2px solid ${colors.borderLight}`,
              borderRadius: '12px',
              color: colors.inputText,
              fontSize: '15px',
              outline: 'none',
              transition: 'all 200ms cubic-bezier(0.16, 1, 0.3, 1)',
            }}
            onFocus={(e) => {
              e.currentTarget.style.borderColor = colors.accent;
              e.currentTarget.style.boxShadow = `0 0 0 4px ${colors.accentBg}`;
            }}
            onBlur={(e) => {
              e.currentTarget.style.borderColor = colors.borderLight;
              e.currentTarget.style.boxShadow = 'none';
            }}
          />
        </div>
      </div>

      {/* Card Grid */}
      <main style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
        gap: '24px',
        padding: '0 48px 80px',
        maxWidth: '1400px',
        margin: '0 auto'
      }}>
        {/* Add Repo Card */}
        <Card
          type="add"
          label={t('repos.addRepo')}
          icon="＋"
          onClick={() => setShowAddRepoModal(true)}
          theme={theme}
        />

        {/* Generating Repo Cards - Using global task system as source of truth */}
        {repoTasks
          .filter(task => !filteredRepos.some(repo => repo.name === task.repo_name))
          .map((task) => (
          <Card
            key={task.task_id}
            type="job"
            name={task.repo_name}
            status={task.status === 'cancelled' ? 'failed' : task.status}
            progress={task.progress}
            currentStep={task.step || task.status_message}
            onClick={() => router.push(`/progress/${task.task_id}`)}
            theme={theme}
          />
        ))}

        {/* Repository Cards */}
        {filteredRepos.map((repo) => (
          <div key={repo.name} style={{ position: 'relative' }}>
            <Card
              type="repo"
              name={repo.name}
              path={repo.path}
              researchCount={repo.researchCount}
              lastUpdated={repo.lastUpdated}
              hasDocs={repo.hasDocs}
              hasGraph={repo.hasGraph}
              graphNodeCount={repo.graphNodeCount}
              graphRelationshipCount={repo.graphRelationshipCount}
              graphOperationStatus={getRepoGraphStatus(repo.name)}
              syncEnabled={repo.syncEnabled}
              onClick={() => router.push(`/repos/${repo.name}`)}
              onMenuClick={(e) => {
                e.stopPropagation();
                setOpenDropdown(openDropdown === repo.name ? null : repo.name);
              }}
              showMenu={openDropdown === repo.name}
              theme={theme}
            />

            <DropdownMenu
              isOpen={openDropdown === repo.name}
              onClose={() => setOpenDropdown(null)}
              onDelete={() => setShowDeleteConfirm(repo.name)}
              onRegenerate={() => setShowRegenerateModal(repo)}
              onRefreshGraph={() => setShowRefreshGraphConfirm(repo.name)}
              onSync={() => setShowSyncPanel(repo)}
              hasGraph={repo.hasGraph}
              graphOperationStatus={getRepoGraphStatus(repo.name)}
              theme={theme}
            />

            {/* Sync Panel Floating Popover */}
            {showSyncPanel?.name === repo.name && (
              <Suspense fallback={<LoadingFallback />}>
                <SyncPanelPopover
                  repo={showSyncPanel}
                  colors={colors}
                  onClose={() => setShowSyncPanel(null)}
                  onSyncComplete={async () => {
                    await updateProjectUIFromCache(repo.name);
                  }}
                  onCheckoutStart={async (taskId) => {
                    const maxAttempts = 60;
                    let attempts = 0;
                    const poll = async () => {
                      attempts++;
                      try {
                        const response = await apiFetch(`/api/tasks/${taskId}`);
                        if (response.ok) {
                          const task = await response.json();
                          if (task.status === 'completed' || task.status === 'failed') {
                            await updateProjectUIFromCache(repo.name);
                            return;
                          }
                        }
                        if (attempts < maxAttempts) {
                          setTimeout(poll, 5000);
                        }
                      } catch (e) {
                        console.error('Error polling checkout task:', e);
                      }
                    };
                    setTimeout(poll, 2000);
                  }}
                  onError={(error) => showToast('error', error)}
                />
              </Suspense>
            )}
          </div>
        ))}

        {filteredRepos.length === 0 && repos.length === 0 && !searchQuery && (
          <div style={{ gridColumn: '1 / -1' }}>
            <RepoEmptyState 
              onAddRepo={() => setShowAddRepoModal(true)}
              theme={theme}
            />
          </div>
        )}

        {filteredRepos.length === 0 && searchQuery && (
          <div style={{
            gridColumn: '1 / -1',
            textAlign: 'center',
            padding: '48px',
            color: colors.textMuted
          }}>
            <div style={{ fontSize: '48px', marginBottom: '16px' }}>🔍</div>
            <p style={{ fontSize: '16px' }}>No repositories found matching "{searchQuery}"</p>
          </div>
        )}
      </main>

      {/* Add Repo Modal */}
      <Modal
        isOpen={showAddRepoModal}
        onClose={() => {
          setShowAddRepoModal(false);
          setAddMode('remote');
          setRepoUrl('');
          setLocalPath('');
          setProjectName('');
          setRepoBranch('');
          setRepoUsername('');
          setRepoPassword('');
          setShowCredentials(false);
          setSkipEmbeddings(false);
          setAddError('');
          setSubdirectories([]);
          setSubdirSearchQuery('');
          setSelectedSubdirs(new Set());
          setProjectNamePrefix('');
        }}
        title="Add Repository"
        >
        <div>
          {/* Mode Toggle */}
          <div style={{
            display: 'flex',
            gap: '8px',
            marginBottom: '16px',
            padding: '4px',
            background: colors.bgTertiary,
            borderRadius: '8px',
          }}>
            <button
              type="button"
              onClick={() => setAddMode('remote')}
              disabled={isAdding}
              style={{
                flex: 1,
                padding: '8px 16px',
                border: 'none',
                borderRadius: '6px',
                fontSize: '14px',
                fontWeight: '500',
                cursor: isAdding ? 'not-allowed' : 'pointer',
                background: addMode === 'remote' ? colors.accent : 'transparent',
                color: addMode === 'remote' ? '#fff' : colors.textSecondary,
                transition: 'all 0.2s',
              }}
            >
              🌐 Remote URL
            </button>
            <button
              type="button"
              onClick={() => setAddMode('local')}
              disabled={isAdding}
              style={{
                flex: 1,
                padding: '8px 16px',
                border: 'none',
                borderRadius: '6px',
                fontSize: '14px',
                fontWeight: '500',
                cursor: isAdding ? 'not-allowed' : 'pointer',
                background: addMode === 'local' ? colors.accent : 'transparent',
                color: addMode === 'local' ? '#fff' : colors.textSecondary,
                transition: 'all 0.2s',
              }}
            >
              📁 Local Path
            </button>
          </div>

          {/* Remote Mode Fields */}
          {addMode === 'remote' && (
            <>
              <label style={{
                display: 'block',
                marginBottom: '8px',
                fontSize: '14px',
                fontWeight: '500',
                color: colors.text
              }}>
                Repository URL (GitHub/GitLab)
              </label>
              <input
                type="text"
                value={repoUrl}
                onChange={(e) => setRepoUrl(e.target.value)}
                placeholder="https://github.com/username/repo.git"
                disabled={isAdding}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  background: colors.inputBg,
                  border: `1px solid ${colors.inputBorder}`,
                  borderRadius: '8px',
                  color: colors.inputText,
                  fontSize: '14px',
                  outline: 'none',
                  marginBottom: '12px'
                }}
                onFocus={(e) => {
                  e.currentTarget.style.borderColor = colors.accent;
                }}
                onBlur={(e) => {
                  e.currentTarget.style.borderColor = colors.inputBorder;
                }}
              />

              <label style={{
                display: 'block',
                marginBottom: '8px',
                fontSize: '14px',
                fontWeight: '500',
                color: colors.text
              }}>
                Branch (optional)
              </label>
              <input
                type="text"
                value={repoBranch}
                onChange={(e) => setRepoBranch(e.target.value)}
                placeholder="main, master, develop, etc. (leave empty for default branch)"
                disabled={isAdding}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  background: colors.inputBg,
                  border: `1px solid ${colors.inputBorder}`,
                  borderRadius: '8px',
                  color: colors.inputText,
                  fontSize: '14px',
                  outline: 'none',
                  marginBottom: '16px'
                }}
                onFocus={(e) => {
                  e.currentTarget.style.borderColor = colors.accent;
                }}
                onBlur={(e) => {
                  e.currentTarget.style.borderColor = colors.inputBorder;
                }}
              />
            </>
          )}

          {/* Local Mode Fields */}
          {addMode === 'local' && (
            <>
              <label style={{
                display: 'block',
                marginBottom: '8px',
                fontSize: '14px',
                fontWeight: '500',
                color: colors.text
              }}>
                Local Path
              </label>
              <div style={{ display: 'flex', gap: '8px', marginBottom: '12px' }}>
                <input
                  type="text"
                  value={localPath}
                  onChange={(e) => {
                    setLocalPath(e.target.value);
                    // Clear subdirectories when path changes
                    setSubdirectories([]);
                    setSubdirSearchQuery('');
                    setSelectedSubdirs(new Set());
                    setProjectNamePrefix('');
                  }}
                  placeholder="/path/to/your/project"
                  disabled={isAdding || isScanning || isAddingMultiple}
                  style={{
                    flex: 1,
                    padding: '10px 12px',
                    background: colors.inputBg,
                    border: `1px solid ${colors.inputBorder}`,
                    borderRadius: '8px',
                    color: colors.inputText,
                    fontSize: '14px',
                    outline: 'none',
                  }}
                  onFocus={(e) => {
                    e.currentTarget.style.borderColor = colors.accent;
                  }}
                  onBlur={(e) => {
                    e.currentTarget.style.borderColor = colors.inputBorder;
                  }}
                />
                <button
                  type="button"
                  onClick={handleScanSubdirectories}
                  disabled={isAdding || isScanning || isAddingMultiple || !localPath.trim()}
                  style={{
                    padding: '10px 16px',
                    background: colors.accentBg,
                    border: `1px solid ${colors.accentBorder}`,
                    borderRadius: '8px',
                    fontSize: '13px',
                    fontWeight: '500',
                    cursor: (isAdding || isScanning || isAddingMultiple || !localPath.trim()) ? 'not-allowed' : 'pointer',
                    color: colors.accent,
                    opacity: (isAdding || isScanning || isAddingMultiple || !localPath.trim()) ? 0.5 : 1,
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {isScanning ? (
                    <>
                      <div style={{
                        width: '14px',
                        height: '14px',
                        border: `2px solid ${colors.accent}`,
                        borderTopColor: 'transparent',
                        borderRadius: '50%',
                        animation: 'spin 0.6s linear infinite'
                      }} />
                      Scanning...
                    </>
                  ) : (
                    <>
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
                      </svg>
                      Scan
                    </>
                  )}
                </button>
              </div>

              {/* Project Name (for batch addition) */}
              {subdirectories.length > 0 && (
                <>
                  <label style={{
                    display: 'block',
                    marginBottom: '8px',
                    fontSize: '14px',
                    fontWeight: '500',
                    color: colors.text
                  }}>
                    Project Name (optional)
                  </label>
                  <input
                    type="text"
                    value={projectNamePrefix}
                    onChange={(e) => setProjectNamePrefix(e.target.value)}
                    placeholder="Leave empty to use parent directory name"
                    disabled={isAdding || isAddingMultiple}
                    style={{
                      width: '100%',
                      padding: '10px 12px',
                      background: colors.inputBg,
                      border: `1px solid ${colors.inputBorder}`,
                      borderRadius: '8px',
                      color: colors.inputText,
                      fontSize: '14px',
                      outline: 'none',
                      marginBottom: '12px'
                    }}
                    onFocus={(e) => {
                      e.currentTarget.style.borderColor = colors.accent;
                    }}
                    onBlur={(e) => {
                      e.currentTarget.style.borderColor = colors.inputBorder;
                    }}
                  />
                </>
              )}

              {/* Subdirectory List */}
              {subdirectories.length > 0 && (
                <div style={{
                  marginBottom: '16px',
                  border: `1px solid ${colors.borderLight}`,
                  borderRadius: '8px',
                  overflow: 'hidden',
                }}>
                  {/* Header with tabs, search and select all */}
                  <div style={{
                    padding: '10px 12px',
                    background: colors.bgTertiary,
                    borderBottom: `1px solid ${colors.borderLight}`,
                  }}>
                    {/* Tabs: All / Selected */}
                    <div style={{
                      display: 'flex',
                      gap: '4px',
                      marginBottom: '10px',
                      borderBottom: `1px solid ${colors.borderLight}`,
                    }}>
                      <button
                        type="button"
                        onClick={() => setShowSelectedOnly(false)}
                        style={{
                          padding: '6px 12px',
                          background: !showSelectedOnly ? colors.accentBg : 'transparent',
                          border: 'none',
                          borderBottom: !showSelectedOnly ? `2px solid ${colors.accent}` : 'none',
                          borderRadius: '6px 6px 0 0',
                          fontSize: '13px',
                          fontWeight: '500',
                          cursor: 'pointer',
                          color: !showSelectedOnly ? colors.accent : colors.textMuted,
                        }}
                      >
                        All ({subdirectories.length})
                      </button>
                      <button
                        type="button"
                        onClick={() => setShowSelectedOnly(true)}
                        style={{
                          padding: '6px 12px',
                          background: showSelectedOnly ? colors.accentBg : 'transparent',
                          border: 'none',
                          borderBottom: showSelectedOnly ? `2px solid ${colors.accent}` : 'none',
                          borderRadius: '6px 6px 0 0',
                          fontSize: '13px',
                          fontWeight: '500',
                          cursor: 'pointer',
                          color: showSelectedOnly ? colors.accent : colors.textMuted,
                        }}
                      >
                        Selected ({selectedSubdirs.size})
                      </button>
                    </div>

                    {/* Search box */}
                    <div style={{
                      position: 'relative',
                      marginBottom: '8px',
                    }}>
                      <input
                        type="text"
                        value={subdirSearchQuery}
                        onChange={(e) => setSubdirSearchQuery(e.target.value)}
                        placeholder="Search subdirectories..."
                        style={{
                          width: '100%',
                          padding: '8px 12px 8px 32px',
                          background: colors.inputBg,
                          border: `1px solid ${colors.inputBorder}`,
                          borderRadius: '6px',
                          color: colors.inputText,
                          fontSize: '13px',
                          outline: 'none',
                        }}
                        onFocus={(e) => {
                          e.currentTarget.style.borderColor = colors.accent;
                        }}
                        onBlur={(e) => {
                          e.currentTarget.style.borderColor = colors.inputBorder;
                        }}
                      />
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{
                        position: 'absolute',
                        left: '10px',
                        top: '50%',
                        transform: 'translateY(-50%)',
                        color: colors.textMuted,
                        pointerEvents: 'none',
                      }}>
                        <circle cx="11" cy="11" r="8"/>
                        <path d="m21 21-4.35-4.35"/>
                      </svg>
                    </div>

                    {/* Select all and count row - only show in All tab */}
                    {!showSelectedOnly && (
                      <div style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                      }}>
                        <label style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '8px',
                          fontSize: '13px',
                          fontWeight: '500',
                          color: colors.text,
                          cursor: 'pointer',
                        }}>
                          <input
                            type="checkbox"
                            checked={selectedSubdirs.size === subdirectories.length && subdirectories.length > 0}
                            onChange={toggleSelectAllSubdirs}
                            style={{ width: '16px', height: '16px', cursor: 'pointer' }}
                          />
                          Select All
                        </label>
                        <span style={{
                          fontSize: '12px',
                          color: colors.textMuted,
                        }}>
                          {selectedSubdirs.size} selected
                        </span>
                      </div>
                    )}
                    {/* Clear All button - only show in Selected tab */}
                    {showSelectedOnly && selectedSubdirs.size > 0 && (
                      <div style={{
                        display: 'flex',
                        justifyContent: 'flex-end',
                      }}>
                        <button
                          type="button"
                          onClick={() => setSelectedSubdirs(new Set())}
                          style={{
                            padding: '4px 10px',
                            background: colors.errorBg || '#fee',
                            border: `1px solid ${colors.errorBorder || '#fcc'}`,
                            borderRadius: '4px',
                            fontSize: '12px',
                            cursor: 'pointer',
                            color: colors.error || '#c00',
                          }}
                        >
                          Clear All
                        </button>
                      </div>
                    )}
                    {/* Empty state for Selected tab */}
                    {showSelectedOnly && selectedSubdirs.size === 0 && (
                      <div style={{
                        padding: '20px',
                        textAlign: 'center',
                        color: colors.textMuted,
                        fontSize: '13px',
                      }}>
                        No subdirectories selected. Go to &quot;All&quot; tab to select.
                      </div>
                    )}
                  </div>

                  {/* Scrollable list */}
                  <div style={{
                    maxHeight: '200px',
                    overflowY: 'auto',
                  }}>
                    {(() => {
                      // Filter based on search and tab
                      let displayList = subdirectories;

                      // Apply search filter
                      if (subdirSearchQuery) {
                        displayList = displayList.filter(subdir =>
                          subdir.name.toLowerCase().includes(subdirSearchQuery.toLowerCase())
                        );
                      }

                      // Apply selected filter
                      if (showSelectedOnly) {
                        displayList = displayList.filter(subdir =>
                          selectedSubdirs.has(subdir.path)
                        );
                      }

                      if (displayList.length === 0) {
                        return (
                          <div style={{
                            padding: '20px',
                            textAlign: 'center',
                            color: colors.textMuted,
                            fontSize: '13px',
                          }}>
                            {showSelectedOnly
                              ? 'No subdirectories selected'
                              : `No subdirectories match "${subdirSearchQuery}"`
                            }
                          </div>
                        );
                      }

                      return displayList.map((subdir, index) => {
                        return (
                          <div
                            key={subdir.path}
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              padding: '10px 12px',
                              borderBottom: index < displayList.length - 1 ? `1px solid ${colors.borderLight}` : 'none',
                              background: selectedSubdirs.has(subdir.path) ? colors.accentBg : 'transparent',
                              cursor: 'pointer',
                              transition: 'background 0.15s ease',
                            }}
                            onClick={() => !showSelectedOnly && toggleSubdirSelection(subdir.path)}
                            onMouseEnter={(e) => {
                              if (!selectedSubdirs.has(subdir.path)) {
                                e.currentTarget.style.background = colors.bgHover;
                              }
                            }}
                            onMouseLeave={(e) => {
                              e.currentTarget.style.background = selectedSubdirs.has(subdir.path) ? colors.accentBg : 'transparent';
                            }}
                          >
                            {!showSelectedOnly && (
                              <input
                                type="checkbox"
                                checked={selectedSubdirs.has(subdir.path)}
                                onChange={() => toggleSubdirSelection(subdir.path)}
                                onClick={(e) => e.stopPropagation()}
                                style={{ width: '16px', height: '16px', marginRight: '10px', cursor: 'pointer' }}
                              />
                            )}
                            {showSelectedOnly && (
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  toggleSubdirSelection(subdir.path);
                                }}
                                style={{
                                  width: '20px',
                                  height: '20px',
                                  marginRight: '10px',
                                  padding: '0',
                                  background: colors.errorBg || '#fee',
                                  border: `1px solid ${colors.errorBorder || '#fcc'}`,
                                  borderRadius: '4px',
                                  fontSize: '12px',
                                  cursor: 'pointer',
                                  color: colors.error || '#c00',
                                  display: 'flex',
                                  alignItems: 'center',
                                  justifyContent: 'center',
                                }}
                              >
                                ×
                              </button>
                            )}
                            <div style={{ flex: 1, minWidth: 0 }}>
                              <div style={{
                                fontSize: '14px',
                                fontWeight: '500',
                                color: colors.text,
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                whiteSpace: 'nowrap',
                              }}>
                                {subdir.name}
                              </div>
                              <div style={{
                                fontSize: '12px',
                                color: colors.textMuted,
                                display: 'flex',
                                alignItems: 'center',
                                gap: '8px',
                                marginTop: '2px',
                              }}>
                                <span>{subdir.file_count} files</span>
                                {subdir.has_python && (
                                  <span style={{
                                    padding: '1px 6px',
                                    background: colors.successBg || colors.bgTertiary,
                                    color: colors.success,
                                    borderRadius: '4px',
                                    fontSize: '11px',
                                  }}>
                                    Python
                                  </span>
                                )}
                                {subdir.has_init && (
                                  <span style={{
                                    padding: '1px 6px',
                                    background: colors.infoBg || colors.bgTertiary,
                                    color: colors.info,
                                    borderRadius: '4px',
                                    fontSize: '11px',
                                  }}>
                                    Package
                                  </span>
                                )}
                              </div>
                            </div>
                          </div>
                        );
                      });
                    })()}
                  </div>
                </div>
              )}

              {/* Single project fields - only show when no subdirectories scanned */}
              {subdirectories.length === 0 && (
                <>
                  <label style={{
                    display: 'block',
                    marginBottom: '8px',
                    fontSize: '14px',
                    fontWeight: '500',
                    color: colors.text
                  }}>
                    Project Name (optional)
                  </label>
                  <input
                    type="text"
                    value={projectName}
                    onChange={(e) => setProjectName(e.target.value)}
                    placeholder="Leave empty to use directory name"
                    disabled={isAdding}
                    style={{
                      width: '100%',
                      padding: '10px 12px',
                      background: colors.inputBg,
                      border: `1px solid ${colors.inputBorder}`,
                      borderRadius: '8px',
                      color: colors.inputText,
                      fontSize: '14px',
                      outline: 'none',
                      marginBottom: '16px'
                    }}
                    onFocus={(e) => {
                      e.currentTarget.style.borderColor = colors.accent;
                    }}
                    onBlur={(e) => {
                      e.currentTarget.style.borderColor = colors.inputBorder;
                    }}
                  />
                </>
              )}

              <div style={{
                marginBottom: '16px',
                padding: '12px',
                background: colors.infoBg || colors.bgTertiary,
                borderRadius: '8px',
                fontSize: '13px',
                color: colors.info || colors.textSecondary,
                border: `1px solid ${colors.infoBorder || colors.border}`,
              }}>
                💡 {subdirectories.length > 0
                  ? 'Select subdirectories to include in this project. Only selected folders will be analyzed.'
                  : 'Use this mode to analyze local projects. Click "Scan" to detect subdirectories for filtering.'}
              </div>
            </>
          )}

          {/* Skip Embeddings Checkbox */}
          <div style={{ marginBottom: '16px' }}>
            <label style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              fontSize: '14px',
              color: colors.text,
              cursor: 'pointer',
            }}>
              <input
                type="checkbox"
                checked={skipEmbeddings}
                onChange={(e) => setSkipEmbeddings(e.target.checked)}
                disabled={isAdding}
                style={{
                  width: '16px',
                  height: '16px',
                  cursor: isAdding ? 'not-allowed' : 'pointer',
                }}
              />
              <span style={{ fontWeight: '500' }}>Skip semantic embeddings generation</span>
            </label>
            <p style={{
              fontSize: '12px',
              color: colors.textMuted,
              marginTop: '4px',
              marginLeft: '24px',
            }}>
              💡 Enable for large repositories to speed up initial processing. You can generate embeddings later.
            </p>
          </div>

          {/* Private Repository Credentials Toggle - Only show for remote mode */}
          {addMode === 'remote' && (
            <div style={{ marginBottom: '16px' }}>
              <button
                type="button"
                onClick={() => setShowCredentials(!showCredentials)}
                style={{
                  background: 'transparent',
                  border: 'none',
                  padding: '0',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  fontSize: '13px',
                  color: colors.accent,
                }}
              >
                <span style={{
                  transform: showCredentials ? 'rotate(90deg)' : 'rotate(0deg)',
                  transition: 'transform 0.2s',
                  display: 'inline-block',
                }}>
                  ▶
                </span>
                Private Repository (requires authentication)
              </button>

              {showCredentials && (
                <div style={{
                  marginTop: '12px',
                  padding: '16px',
                  background: colors.bgTertiary,
                  borderRadius: '8px',
                  border: `1px solid ${colors.border}`,
                }}>
                  <div style={{
                    marginBottom: '12px',
                    padding: '8px 12px',
                    background: colors.warningBg,
                    borderRadius: '6px',
                    fontSize: '12px',
                    color: colors.warning,
                    border: `1px solid ${colors.warningBorder}`,
                  }}>
                    For private repositories, please provide Git username and password/access token.
                  </div>

                  <label style={{
                    display: 'block',
                    marginBottom: '6px',
                    fontSize: '13px',
                    fontWeight: '500',
                    color: colors.textSecondary
                  }}>
                    Username
                  </label>
                  <input
                    type="text"
                    value={repoUsername}
                    onChange={(e) => setRepoUsername(e.target.value)}
                    placeholder="Git username"
                    disabled={isAdding}
                    style={{
                      width: '100%',
                      padding: '8px 10px',
                      background: colors.inputBg,
                      border: `1px solid ${colors.inputBorder}`,
                      borderRadius: '6px',
                      color: colors.inputText,
                      fontSize: '13px',
                      outline: 'none',
                      marginBottom: '10px'
                    }}
                    onFocus={(e) => {
                      e.currentTarget.style.borderColor = colors.accent;
                    }}
                    onBlur={(e) => {
                      e.currentTarget.style.borderColor = colors.inputBorder;
                    }}
                  />

                  <label style={{
                    display: 'block',
                    marginBottom: '6px',
                    fontSize: '13px',
                    fontWeight: '500',
                    color: colors.textSecondary
                  }}>
                    Password / Access Token
                  </label>
                  <input
                    type="password"
                    value={repoPassword}
                    onChange={(e) => setRepoPassword(e.target.value)}
                    placeholder="Password or access token"
                    disabled={isAdding}
                    style={{
                      width: '100%',
                      padding: '8px 10px',
                      background: colors.inputBg,
                      border: `1px solid ${colors.inputBorder}`,
                      borderRadius: '6px',
                      color: colors.inputText,
                      fontSize: '13px',
                      outline: 'none',
                    }}
                    onFocus={(e) => {
                      e.currentTarget.style.borderColor = colors.accent;
                    }}
                    onBlur={(e) => {
                      e.currentTarget.style.borderColor = colors.inputBorder;
                    }}
                  />

                  <div style={{
                    marginTop: '10px',
                    fontSize: '11px',
                    color: colors.textMuted,
                  }}>
                    💡 For GitHub/GitLab, we recommend using Personal Access Token
                  </div>
                </div>
              )}
            </div>
          )}

          {addError && (
            <div style={{
              padding: '12px',
              background: colors.errorBg,
              border: `1px solid ${colors.errorBorder}`,
              borderRadius: '8px',
              color: colors.error,
              fontSize: '14px',
              marginBottom: '16px'
            }}>
              {addError}
            </div>
          )}

          <div style={{
            fontSize: '13px',
            color: colors.textMuted,
            marginBottom: '20px'
          }}>
            {addMode === 'remote' ? (
              <>
                <p>The repository will be:</p>
                <ul style={{ marginLeft: '20px', marginTop: '8px' }}>
                  <li>Cloned locally</li>
                  <li>Knowledge graph built automatically</li>
                  <li>Available for documentation generation</li>
                </ul>
                <p style={{ marginTop: '8px' }}>⚠️ This process may take several minutes.</p>
              </>
            ) : subdirectories.length > 0 ? (
              <>
                <p>One project will be created:</p>
                <ul style={{ marginLeft: '20px', marginTop: '8px' }}>
                  <li>Analyzing {selectedSubdirs.size} selected subdirectories</li>
                  <li>Knowledge graph built for the project</li>
                  <li>Available for documentation generation</li>
                </ul>
                <p style={{ marginTop: '8px' }}>⚠️ This process may take several minutes.</p>
              </>
            ) : (
              <>
                <p>The local project will be:</p>
                <ul style={{ marginLeft: '20px', marginTop: '8px' }}>
                  <li>Analyzed directly (no cloning needed)</li>
                  <li>Knowledge graph built automatically</li>
                  <li>Available for documentation generation</li>
                </ul>
                <p style={{ marginTop: '8px' }}>⚠️ This process may take several minutes for large projects.</p>
              </>
            )}
          </div>

          <div style={{ display: 'flex', gap: '12px', justifyContent: 'flex-end' }}>
            <button
              onClick={() => {
                setShowAddRepoModal(false);
                setAddMode('remote');
                setRepoUrl('');
                setLocalPath('');
                setProjectName('');
                setRepoBranch('');
                setRepoUsername('');
                setRepoPassword('');
                setShowCredentials(false);
                setSkipEmbeddings(false);
                setAddError('');
                setSubdirectories([]);
                setSubdirSearchQuery('');
                setSelectedSubdirs(new Set());
                setProjectNamePrefix('');
                setShowSelectedOnly(false);
              }}
              disabled={isAdding || isAddingMultiple}
              style={{
                padding: '10px 20px',
                background: colors.buttonSecondaryBg,
                border: 'none',
                borderRadius: '8px',
                fontSize: '14px',
                fontWeight: '500',
                cursor: (isAdding || isAddingMultiple) ? 'not-allowed' : 'pointer',
                color: colors.text,
                opacity: (isAdding || isAddingMultiple) ? 0.5 : 1
              }}
            >
              Cancel
            </button>
            {/* Show "Build Project" button when subdirectories are selected */}
            {addMode === 'local' && subdirectories.length > 0 && selectedSubdirs.size > 0 ? (
              <button
                onClick={handleAddMultipleLocalRepos}
                disabled={isAddingMultiple}
                style={{
                  padding: '10px 20px',
                  background: isAddingMultiple ? colors.textMuted : colors.buttonPrimaryBg,
                  border: 'none',
                  borderRadius: '8px',
                  fontSize: '14px',
                  fontWeight: '500',
                  cursor: isAddingMultiple ? 'not-allowed' : 'pointer',
                  color: '#ffffff',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px'
                }}
              >
                {isAddingMultiple && (
                  <div style={{
                    width: '16px',
                    height: '16px',
                    border: '2px solid #ffffff',
                    borderTopColor: 'transparent',
                    borderRadius: '50%',
                    animation: 'spin 0.6s linear infinite'
                  }} />
                )}
                {isAddingMultiple ? 'Building...' : `Build Project (${selectedSubdirs.size} subdirs)`}
              </button>
            ) : (
              <button
                onClick={handleAddRepo}
                disabled={isAdding || (addMode === 'local' && subdirectories.length > 0)}
                style={{
                  padding: '10px 20px',
                  background: (isAdding || (addMode === 'local' && subdirectories.length > 0)) ? colors.textMuted : colors.buttonPrimaryBg,
                  border: 'none',
                  borderRadius: '8px',
                  fontSize: '14px',
                  fontWeight: '500',
                  cursor: (isAdding || (addMode === 'local' && subdirectories.length > 0)) ? 'not-allowed' : 'pointer',
                  color: '#ffffff',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px'
                }}
              >
                {isAdding && (
                  <div style={{
                    width: '16px',
                    height: '16px',
                    border: '2px solid #ffffff',
                    borderTopColor: 'transparent',
                    borderRadius: '50%',
                    animation: 'spin 0.6s linear infinite'
                  }} />
                )}
                {isAdding ? 'Adding...' : 'Add Repository'}
              </button>
            )}
          </div>
        </div>
      </Modal>

      {/* Delete Confirmation Modal */}
      {showDeleteConfirm && (
        <Modal
          isOpen={true}
          onClose={() => setShowDeleteConfirm(null)}
          title="Confirm Delete Repository"
        >
          <div>
            <p style={{
              marginBottom: '16px',
              color: colors.textMuted
            }}>
              Are you sure you want to delete repository <strong>{showDeleteConfirm}</strong>?
            </p>
            <div style={{
              marginBottom: '20px',
              padding: '12px',
              background: colors.errorBg,
              borderRadius: '8px',
              border: `1px solid ${colors.errorBorder}`,
            }}>
              <p style={{ color: colors.error, fontSize: '13px', marginBottom: '8px', fontWeight: '500' }}>
                ⚠️ This action will delete the following and cannot be undone:
              </p>
              <ul style={{
                color: colors.error,
                fontSize: '12px',
                marginLeft: '16px',
                listStyle: 'disc',
              }}>
                <li>All research documentation</li>
                <li>Local repository copy</li>
                <li>Chat history</li>
                <li>Knowledge graph data</li>
              </ul>
            </div>
            <div style={{ display: 'flex', gap: '12px', justifyContent: 'flex-end' }}>
              <button
                onClick={() => setShowDeleteConfirm(null)}
                style={{
                  padding: '10px 20px',
                  background: colors.buttonSecondaryBg,
                  border: 'none',
                  borderRadius: '8px',
                  fontSize: '14px',
                  fontWeight: '500',
                  cursor: 'pointer',
                  color: colors.text
                }}
              >
                Cancel
              </button>
              <button
                onClick={() => handleDeleteRepo(showDeleteConfirm)}
                style={{
                  padding: '10px 20px',
                  background: colors.error,
                  border: 'none',
                  borderRadius: '8px',
                  fontSize: '14px',
                  fontWeight: '500',
                  cursor: 'pointer',
                  color: '#ffffff'
                }}
              >
                Confirm Delete
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* Regenerate Confirmation Modal */}
      {showRegenerateModal && (
        <Modal
          isOpen={true}
          onClose={() => setShowRegenerateModal(null)}
          title="Regenerate Repository Documentation"
        >
          <div>
            <p style={{
              marginBottom: '20px',
              color: colors.textMuted
            }}>
              Are you sure you want to regenerate documentation for repository <strong>{showRegenerateModal.name}</strong>?
              <br />
              <span style={{ fontSize: '13px', marginTop: '8px', display: 'block' }}>
                This will rebuild the knowledge graph and update all research documentation.
              </span>
            </p>
            <div style={{ display: 'flex', gap: '12px', justifyContent: 'flex-end' }}>
              <button
                onClick={() => setShowRegenerateModal(null)}
                style={{
                  padding: '10px 20px',
                  background: colors.buttonSecondaryBg,
                  border: 'none',
                  borderRadius: '8px',
                  fontSize: '14px',
                  fontWeight: '500',
                  cursor: 'pointer',
                  color: colors.text
                }}
              >
                Cancel
              </button>
              <button
                onClick={handleRegenerateRepo}
                style={{
                  padding: '10px 20px',
                  background: colors.buttonPrimaryBg,
                  border: 'none',
                  borderRadius: '8px',
                  fontSize: '14px',
                  fontWeight: '500',
                  cursor: 'pointer',
                  color: '#ffffff'
                }}
              >
                Regenerate
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* Knowledge Graph Management Panel - Linear/Vercel Style */}
      <Modal
        isOpen={showGraphPanel}
        onClose={() => setShowGraphPanel(false)}
        title="Knowledge Graph"
        maxWidth="520px"
      >
        <div style={{ margin: '-8px 0' }}>
          {/* Connection Status - Minimal inline */}
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: '24px',
          }}>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
            }}>
              <span style={{
                width: '8px',
                height: '8px',
                borderRadius: '50%',
                background: graphStats?.connected ? colors.success : colors.error,
              }} />
              <span style={{
                fontSize: '13px',
                color: colors.textSecondary,
                fontWeight: '500',
              }}>
                {graphStats?.connected ? 'Connected' : 'Disconnected'}
              </span>
            </div>
            <button
              onClick={handleManualGraphCacheSync}
              disabled={graphLoading}
              style={{
                padding: '6px 12px',
                background: 'transparent',
                border: 'none',
                borderRadius: '6px',
                fontSize: '13px',
                cursor: graphLoading ? 'not-allowed' : 'pointer',
                color: colors.accent,
                fontWeight: '500',
                opacity: graphLoading ? 0.5 : 1,
                transition: 'all 150ms ease',
              }}
              onMouseEnter={(e) => {
                if (!graphLoading) e.currentTarget.style.background = colors.accentBg;
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = 'transparent';
              }}
            >
              {graphLoading ? 'Syncing...' : 'Sync'}
            </button>
          </div>

          {/* Stats Row - Clean horizontal layout */}
          {graphStats && graphStats.connected && (
            <div style={{
              display: 'flex',
              gap: '32px',
              paddingBottom: '20px',
              marginBottom: '20px',
              borderBottom: `1px solid ${colors.borderLight}`,
            }}>
              {[
                { label: 'Projects', value: graphStats.total_projects },
                { label: 'Nodes', value: graphStats.total_nodes },
                { label: 'Edges', value: graphStats.total_relationships },
              ].map((stat) => (
                <div key={stat.label}>
                  <div style={{
                    fontSize: '24px',
                    fontWeight: '600',
                    color: colors.text,
                    fontFamily: 'var(--font-mono)',
                    letterSpacing: '-0.02em',
                  }}>
                    {stat.value.toLocaleString()}
                  </div>
                  <div style={{
                    fontSize: '12px',
                    color: colors.textMuted,
                    fontWeight: '500',
                    marginTop: '2px',
                  }}>
                    {stat.label}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Node Types - Minimal tags */}
          {graphStats && graphStats.connected && Object.keys(graphStats.node_types).length > 0 && (
            <div style={{ marginBottom: '20px' }}>
              <div style={{
                fontSize: '11px',
                fontWeight: '600',
                color: colors.textMuted,
                textTransform: 'uppercase',
                letterSpacing: '0.5px',
                marginBottom: '10px',
              }}>
                Node Types
              </div>
              <div style={{
                display: 'flex',
                flexWrap: 'wrap',
                gap: '6px',
              }}>
                {Object.entries(graphStats.node_types)
                  .sort(([, a], [, b]) => b - a)
                  .slice(0, 6)
                  .map(([type, count]) => (
                    <span
                      key={type}
                      style={{
                        padding: '4px 8px',
                        background: 'transparent',
                        border: `1px solid ${colors.borderLight}`,
                        borderRadius: '4px',
                        fontSize: '12px',
                        color: colors.textSecondary,
                        fontFamily: 'var(--font-mono)',
                      }}
                    >
                      {type} <span style={{ color: colors.textMuted }}>{count.toLocaleString()}</span>
                    </span>
                  ))}
              </div>
            </div>
          )}

          {/* Project List - Clean table style */}
          <div>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginBottom: '12px',
            }}>
              <div style={{
                fontSize: '11px',
                fontWeight: '600',
                color: colors.textMuted,
                textTransform: 'uppercase',
                letterSpacing: '0.5px',
              }}>
                Repositories
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <label style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  fontSize: '11px',
                  color: colors.textSecondary,
                  cursor: 'pointer',
                  userSelect: 'none',
                }}>
                  <input
                    type="checkbox"
                    checked={rebuildSkipEmbeddings}
                    onChange={(e) => setRebuildSkipEmbeddings(e.target.checked)}
                    style={{ cursor: 'pointer' }}
                  />
                  Fast build
                </label>
                {graphProjects?.length > 0 && (
                  <button
                    onClick={() => {
                      setShowGraphPanel(false);
                      setShowCleanAllConfirm(true);
                    }}
                    style={{
                      padding: '4px 10px',
                      background: 'transparent',
                      border: `1px solid ${colors.error}`,
                      borderRadius: '6px',
                      fontSize: '11px',
                      fontWeight: '500',
                      cursor: 'pointer',
                      color: colors.error,
                      transition: 'all 150ms ease',
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.background = colors.errorBg;
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = 'transparent';
                    }}
                  >
                    Clean All
                  </button>
                )}
              </div>
            </div>

            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              marginBottom: '12px',
              fontSize: '12px',
              color: colors.textMuted,
            }}>
              <span>
                Running {activeGraphTasks.filter(task => task.status === 'running' || task.status === 'stalled').length}
              </span>
              <span style={{ opacity: 0.5 }}>·</span>
              <span>
                Queued {activeGraphTasks.filter(task => task.status === 'pending').length}
              </span>
              <span style={{ opacity: 0.5 }}>·</span>
              <span>{rebuildSkipEmbeddings ? 'Skip embeddings' : 'Full build'}</span>
            </div>

            {graphLoading ? (
              <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                padding: '40px',
                color: colors.textMuted,
              }}>
                <div style={{
                  width: '20px',
                  height: '20px',
                  border: `2px solid ${colors.borderLight}`,
                  borderTopColor: colors.accent,
                  borderRadius: '50%',
                  animation: 'spin 0.8s linear infinite',
                }} />
              </div>
            ) : knowledgeGraphRepos.length === 0 ? (
              <div style={{
                padding: '40px 20px',
                textAlign: 'center',
                color: colors.textMuted,
                fontSize: '13px',
              }}>
                No repositories yet
              </div>
            ) : (
              <div style={{
                maxHeight: '240px',
                overflowY: 'auto',
              }}>
                {knowledgeGraphRepos.map((project, index) => {
                  const repoStatus = getRepoGraphStatus(project.name);
                  const isBusy = !!repoStatus;
                  const hasGraph = !!project.hasGraph;
                  const statusText =
                    repoStatus === 'cleaning'
                      ? 'Cleaning graph...'
                      : repoStatus === 'generating'
                        ? 'Building knowledge graph...'
                        : repoStatus === 'queued'
                          ? 'Queued for graph build'
                          : hasGraph
                            ? `${(project.graphNodeCount || 0).toLocaleString()} nodes${(project.graphNodeCount || 0) > 0 ? ` · ${(project.graphRelationshipCount || 0).toLocaleString()} edges` : ''}`
                            : 'Pending graph';

                  return (
                  <div
                    key={project.name}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '12px 0',
                      borderBottom: index < knowledgeGraphRepos.length - 1 ? `1px solid ${colors.borderLight}` : 'none',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flex: 1, minWidth: 0 }}>
                      <span style={{
                        width: '6px',
                        height: '6px',
                        borderRadius: '50%',
                        background:
                          repoStatus === 'cleaning' ? colors.error :
                          repoStatus === 'generating' ? colors.accent :
                          repoStatus === 'queued' ? colors.info || colors.accent :
                          hasGraph ? colors.success : colors.borderLight,
                        flexShrink: 0,
                      }} />
                      <div style={{ minWidth: 0 }}>
                        <div style={{
                          fontSize: '14px',
                          fontWeight: '500',
                          color: colors.text,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}>
                          {project.name}
                        </div>
                        <div style={{
                          fontSize: '12px',
                          color: colors.textMuted,
                          fontFamily: 'var(--font-mono)',
                        }}>
                          {statusText}
                        </div>
                      </div>
                    </div>
                    <div style={{ display: 'flex', gap: '4px', flexShrink: 0 }}>
                      <button
                        onClick={() => handleRefreshGraph(project.name, rebuildSkipEmbeddings)}
                        disabled={isBusy}
                        style={{
                          padding: '6px 10px',
                          background: 'transparent',
                          border: 'none',
                          borderRadius: '6px',
                          fontSize: '12px',
                          fontWeight: '500',
                          cursor: isBusy ? 'not-allowed' : 'pointer',
                          color: colors.accent,
                          opacity: isBusy ? 0.4 : 1,
                          transition: 'all 150ms ease',
                        }}
                        onMouseEnter={(e) => {
                          if (!isBusy) {
                            e.currentTarget.style.background = colors.accentBg;
                          }
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.background = 'transparent';
                        }}
                      >
                        {repoStatus === 'queued'
                          ? 'Queued...'
                          : repoStatus === 'generating'
                            ? 'Building...'
                            : repoStatus === 'cleaning'
                              ? 'Cleaning...'
                              : (hasGraph ? 'Rebuild' : 'Build')}
                      </button>
                      {hasGraph && (
                        <button
                          onClick={() => {
                            setShowCleanGraphConfirm(project.name);
                          }}
                          disabled={isBusy}
                          style={{
                            padding: '6px 10px',
                            background: 'transparent',
                            border: 'none',
                            borderRadius: '6px',
                            fontSize: '12px',
                            fontWeight: '500',
                            cursor: isBusy ? 'not-allowed' : 'pointer',
                            color: colors.textMuted,
                            opacity: isBusy ? 0.4 : 1,
                            transition: 'all 150ms ease',
                          }}
                          onMouseEnter={(e) => {
                            if (!isBusy) {
                              e.currentTarget.style.color = colors.error;
                              e.currentTarget.style.background = colors.errorBg;
                            }
                          }}
                          onMouseLeave={(e) => {
                            e.currentTarget.style.color = colors.textMuted;
                            e.currentTarget.style.background = 'transparent';
                          }}
                        >
                          {repoStatus === 'cleaning' ? 'Cleaning...' : 'Clean'}
                        </button>
                      )}
                    </div>
                  </div>
                )})}
              </div>
            )}
          </div>
        </div>
      </Modal>

      {/* Clean Graph Confirmation Modal */}
      {showCleanGraphConfirm && (
        <Modal
          isOpen={true}
          onClose={() => setShowCleanGraphConfirm(null)}
          title="Confirm Clean Knowledge Graph"
          zIndex={150}
        >
          <div>
            <p style={{
              marginBottom: '20px',
              color: colors.textMuted
            }}>
              Are you sure you want to clean the knowledge graph for project <strong>{showCleanGraphConfirm}</strong>?
              <br />
              <span style={{ color: colors.error, fontSize: '13px' }}>
                ⚠️ This action will delete all graph nodes and edges for this project and cannot be undone.
              </span>
            </p>
            <div style={{ display: 'flex', gap: '12px', justifyContent: 'flex-end' }}>
              <button
                onClick={() => setShowCleanGraphConfirm(null)}
                disabled={!!getRepoGraphStatus(showCleanGraphConfirm)}
                style={{
                  padding: '10px 20px',
                  background: colors.buttonSecondaryBg,
                  border: 'none',
                  borderRadius: '8px',
                  fontSize: '14px',
                  fontWeight: '500',
                  cursor: getRepoGraphStatus(showCleanGraphConfirm) ? 'not-allowed' : 'pointer',
                  color: colors.text,
                  opacity: getRepoGraphStatus(showCleanGraphConfirm) ? 0.5 : 1,
                }}
              >
                Cancel
              </button>
              <button
                onClick={() => handleCleanGraph(showCleanGraphConfirm)}
                disabled={!!getRepoGraphStatus(showCleanGraphConfirm)}
                style={{
                  padding: '10px 20px',
                  background: colors.error,
                  border: 'none',
                  borderRadius: '8px',
                  fontSize: '14px',
                  fontWeight: '500',
                  cursor: getRepoGraphStatus(showCleanGraphConfirm) ? 'not-allowed' : 'pointer',
                  color: '#ffffff',
                  opacity: getRepoGraphStatus(showCleanGraphConfirm) ? 0.5 : 1,
                }}
              >
                {getRepoGraphStatus(showCleanGraphConfirm) === 'cleaning' ? 'Cleaning...' : 'Clean'}
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* Clean All Confirmation Modal */}
      {showCleanAllConfirm && (
        <Modal
          isOpen={true}
          onClose={() => !cleanAllLoading && setShowCleanAllConfirm(false)}
          title="Confirm Clean All Knowledge Graphs"
          zIndex={150}
        >
          <div>
            <p style={{
              marginBottom: '20px',
              color: colors.textMuted
            }}>
              Are you sure you want to clean <strong>ALL</strong> knowledge graph data?
              <br />
              <span style={{ color: colors.error, fontSize: '13px' }}>
                This action will delete ALL graph nodes and edges for ALL projects ({graphStats?.total_nodes?.toLocaleString() || 0} nodes, {graphStats?.total_relationships?.toLocaleString() || 0} edges) and cannot be undone.
              </span>
            </p>
            <div style={{ display: 'flex', gap: '12px', justifyContent: 'flex-end' }}>
              <button
                onClick={() => setShowCleanAllConfirm(false)}
                disabled={cleanAllLoading}
                style={{
                  padding: '10px 20px',
                  background: colors.buttonSecondaryBg,
                  border: 'none',
                  borderRadius: '8px',
                  fontSize: '14px',
                  fontWeight: '500',
                  cursor: cleanAllLoading ? 'not-allowed' : 'pointer',
                  color: colors.text,
                  opacity: cleanAllLoading ? 0.5 : 1,
                }}
              >
                Cancel
              </button>
              <button
                onClick={handleCleanAllGraphs}
                disabled={cleanAllLoading}
                style={{
                  padding: '10px 20px',
                  background: colors.error,
                  border: 'none',
                  borderRadius: '8px',
                  fontSize: '14px',
                  fontWeight: '500',
                  cursor: cleanAllLoading ? 'not-allowed' : 'pointer',
                  color: '#ffffff',
                  opacity: cleanAllLoading ? 0.5 : 1,
                }}
              >
                {cleanAllLoading ? 'Cleaning...' : 'Clean All'}
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* Refresh Graph Confirmation Modal */}
      {showRefreshGraphConfirm && (
        <Modal
          isOpen={true}
          onClose={() => {
            setShowRefreshGraphConfirm(null);
          }}
          title={repos.find(r => r.name === showRefreshGraphConfirm)?.hasGraph ? "Confirm Refresh Knowledge Graph" : "Confirm Build Knowledge Graph"}
          zIndex={150}
        >
          <div>
            <p style={{
              marginBottom: '16px',
              color: colors.textMuted
            }}>
              {repos.find(r => r.name === showRefreshGraphConfirm)?.hasGraph ? (
                <>
                  Are you sure you want to refresh the knowledge graph for project <strong>{showRefreshGraphConfirm}</strong>?
                  <br />
                  <span style={{ fontSize: '13px', marginTop: '8px', display: 'block' }}>
                    This will re-parse the code repository and rebuild the knowledge graph, including semantic embeddings.
                  </span>
                </>
              ) : (
                <>
                  Are you sure you want to build the knowledge graph for project <strong>{showRefreshGraphConfirm}</strong>?
                  <br />
                  <span style={{ fontSize: '13px', marginTop: '8px', display: 'block' }}>
                    This will parse the code repository and build the knowledge graph, including semantic embeddings.
                  </span>
                </>
              )}
            </p>

            {/* Skip Embeddings Checkbox */}
            <div style={{ marginBottom: '16px' }}>
              <label style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                fontSize: '14px',
                color: colors.text,
                cursor: 'pointer',
              }}>
                <input
                  type="checkbox"
                  checked={rebuildSkipEmbeddings}
                  onChange={(e) => setRebuildSkipEmbeddings(e.target.checked)}
                  disabled={!!getRepoGraphStatus(showRefreshGraphConfirm)}
                  style={{
                    width: '16px',
                    height: '16px',
                    cursor: getRepoGraphStatus(showRefreshGraphConfirm) ? 'not-allowed' : 'pointer',
                  }}
                />
                <span style={{ fontWeight: '500' }}>Skip semantic embeddings generation (faster)</span>
              </label>
              <p style={{
                fontSize: '12px',
                color: colors.textMuted,
                marginTop: '4px',
                marginLeft: '24px',
              }}>
                Enable for large repositories to speed up rebuild. You can generate embeddings later.
              </p>
            </div>

            {/* Info hint */}
            <div style={{
              marginBottom: '20px',
              padding: '12px',
              background: colors.bgTertiary,
              borderRadius: '8px',
              border: `1px solid ${colors.border}`,
            }}>
              <p style={{
                fontSize: '12px',
                color: colors.textSecondary,
              }}>
                The system will use GPU batch processing to accelerate embedding generation. Large repositories may take several minutes.
              </p>
            </div>

            <div style={{ display: 'flex', gap: '12px', justifyContent: 'flex-end' }}>
              <button
                onClick={() => {
                  setShowRefreshGraphConfirm(null);
                }}
                disabled={!!getRepoGraphStatus(showRefreshGraphConfirm)}
                style={{
                  padding: '10px 20px',
                  background: colors.buttonSecondaryBg,
                  border: 'none',
                  borderRadius: '8px',
                  fontSize: '14px',
                  fontWeight: '500',
                  cursor: getRepoGraphStatus(showRefreshGraphConfirm) ? 'not-allowed' : 'pointer',
                  color: colors.text,
                  opacity: getRepoGraphStatus(showRefreshGraphConfirm) ? 0.5 : 1,
                }}
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  handleRefreshGraph(showRefreshGraphConfirm, rebuildSkipEmbeddings);
                  setShowRefreshGraphConfirm(null);
                }}
                disabled={!!getRepoGraphStatus(showRefreshGraphConfirm)}
                style={{
                  padding: '10px 20px',
                  background: colors.success,
                  border: 'none',
                  borderRadius: '8px',
                  fontSize: '14px',
                  fontWeight: '500',
                  cursor: getRepoGraphStatus(showRefreshGraphConfirm) ? 'not-allowed' : 'pointer',
                  color: '#ffffff',
                  opacity: getRepoGraphStatus(showRefreshGraphConfirm) ? 0.5 : 1,
                }}
              >
                {getRepoGraphStatus(showRefreshGraphConfirm) === 'generating'
                  ? "Building..."
                  : (repos.find(r => r.name === showRefreshGraphConfirm)?.hasGraph ? 'Refresh' : 'Build')}
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* Sync Panel is now rendered inline as a floating popover per repo card */}

      {/* Floating Feedback Widget */}
      <Suspense fallback={<LoadingFallback />}>
        <FloatingFeedbackWidget
          isOpen={showFeedbackPanel}
          onToggle={() => setShowFeedbackPanel(!showFeedbackPanel)}
        />
      </Suspense>

      {/* Close theme dropdown when clicking outside - handled by useEffect */}

      <style jsx>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
        @keyframes shimmer {
          0% { background-position: -200% 0; }
          100% { background-position: 200% 0; }
        }
        @keyframes fadeInDown {
          from {
            opacity: 0;
            transform: translateY(-4px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }
        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
      `}</style>
    </div>
  );
}

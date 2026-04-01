'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect, useCallback, useRef } from 'react';
import type { IndexData, VersionInfo } from '@/components/OverviewDocV2';
import { listGraphProjects } from '@/lib/graph-api';

/** Fetch with timeout (default 15s). Throws on timeout. */
function fetchWithTimeout(url: string, opts?: RequestInit, timeoutMs = 15000): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  return fetch(url, { ...opts, signal: controller.signal }).finally(() => clearTimeout(timer));
}

interface GraphStats {
  name: string;
  node_count: number;
  relationship_count: number;
  has_graph: boolean;
  path: string | null;
  sync_enabled: boolean;
}

interface OverviewTreeItem {
  path?: string;
}

interface OverviewTrajectoryEvent {
  timestamp: string;
  status: string;
  progress: number;
  step: string;
  message: string;
  error?: string | null;
  details?: Record<string, unknown> | null;
}

interface GenProgress {
  generating: boolean;
  status: string | null;
  taskId: string | null;
  progress: number;
  message: string;
  trajectory: OverviewTrajectoryEvent[];
  lastUpdateAt: string | null;
}

export interface OverviewData {
  overviewIndex: IndexData | null;
  overviewLoading: boolean;
  currentDocPath: string;
  docContent: string | null;
  docContentLoading: boolean;
  versions: VersionInfo[];
  currentVersionId?: string;
  defaultVersionId?: string;
  graphStats: GraphStats | null;
  genProgress: GenProgress;
  handleNavigate: (path: string) => void;
  handleRefresh: () => void;
  loadDocContent: (path: string, versionId?: string) => Promise<void>;
  handleVersionChange: (versionId: string) => Promise<void>;
  loadOverview: (versionId?: string) => Promise<{ path: string; versionId: string } | null>;
  startGeneration: (opts?: { model?: string; doc_depth?: number; language?: string }) => void;
  resumeGeneration: (taskId?: string | null) => void;
}

export function useOverviewData(repoName: string | null): OverviewData {
  const [overviewIndex, setOverviewIndex] = useState<IndexData | null>(null);
  // Start as true to prevent GraphStatsView flash before first load completes
  const [overviewLoading, setOverviewLoading] = useState(!!repoName);
  const initialLoadDone = useRef(false);
  const [currentDocPath, setCurrentDocPath] = useState('overview.md');
  const [docContent, setDocContent] = useState<string | null>(null);
  const [docContentLoading, setDocContentLoading] = useState(false);
  const [docCache, setDocCache] = useState<Map<string, string>>(new Map());
  const [versions, setVersions] = useState<VersionInfo[]>([]);
  const [currentVersionId, setCurrentVersionId] = useState<string | undefined>(undefined);
  const [defaultVersionId, setDefaultVersionId] = useState<string | undefined>(undefined);

  const [graphStats, setGraphStats] = useState<GraphStats | null>(null);
  const [, setGraphStatsLoading] = useState(false);
  const [genProgress, setGenProgress] = useState<GenProgress>({
    generating: false, status: null, taskId: null, progress: 0, message: '', trajectory: [], lastUpdateAt: null,
  });
  const genTaskIdRef = useRef<string | null>(null);
  const genPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Load document markdown content with caching
  const loadDocContent = useCallback(async (path: string, versionId?: string) => {
    if (!repoName || !path) return;

    const effectiveVersionId = versionId || currentVersionId;
    const cacheKey = `${repoName}:${effectiveVersionId || 'current'}:${path}`;
    if (docCache.has(cacheKey)) {
      setDocContent(docCache.get(cacheKey)!);
      return;
    }

    setDocContentLoading(true);
    try {
      const url = effectiveVersionId
        ? `/api/overview/${repoName}/doc/${path}?version_id=${effectiveVersionId}`
        : `/api/overview/${repoName}/doc/${path}`;
      const response = await fetchWithTimeout(url);
      if (response.ok) {
        const content = await response.text();
        setDocContent(content);
        setDocCache(prev => new Map(prev).set(cacheKey, content));
      } else {
        setDocContent(null);
      }
    } catch (error) {
      console.error('Error loading doc content:', error);
      setDocContent(null);
    } finally {
      setDocContentLoading(false);
    }
  }, [repoName, docCache, currentVersionId]);

  // Load overview document (index.json) with version support
  const loadOverview = useCallback(async (versionId?: string): Promise<{ path: string; versionId: string } | null> => {
    if (!repoName) return null;

    setOverviewLoading(true);
    try {
      const statusResponse = await fetchWithTimeout(`/api/overview/${repoName}/status`);
      const statusData = await statusResponse.json();

      if (statusData.exists) {
        if (statusData.versions && statusData.versions.length > 0) {
          setVersions(statusData.versions);
        }
        if (statusData.version_id) {
          setDefaultVersionId(statusData.version_id);
        }

        const targetVersionId = versionId || statusData.version_id || (statusData.versions?.[0]?.version_id);
        setCurrentVersionId(targetVersionId);

        let indexUrl = `/api/overview/${repoName}`;
        if (targetVersionId) {
          indexUrl = `/api/overview/${repoName}?version_id=${targetVersionId}`;
        }

        const overviewResponse = await fetchWithTimeout(indexUrl);
        if (overviewResponse.ok) {
          const data = await overviewResponse.json();
          setOverviewIndex(data);

          const firstItem = data.tree?.find((item: OverviewTreeItem) => item.path);
          const firstPath = firstItem?.path || null;
          if (firstPath) {
            setCurrentDocPath(firstPath);
            return { path: firstPath, versionId: targetVersionId };
          }
        }
      }
      return null;
    } catch (error: unknown) {
      console.error('Error loading overview:', error);
      return null;
    } finally {
      setOverviewLoading(false);
      initialLoadDone.current = true;
    }
  }, [repoName]);

  // Handle version change
  const handleVersionChange = useCallback(async (versionId: string) => {
    setDocCache(new Map());
    setDocContent(null);
    setOverviewIndex(null);
    setCurrentDocPath('');
    setCurrentVersionId(versionId);
    await loadOverview(versionId);
  }, [loadOverview]);

  // Handle navigation to a document
  const handleNavigate = useCallback((path: string) => {
    setCurrentDocPath(path);
  }, []);

  // Handle refresh after generation completes
  const handleRefresh = useCallback(() => {
    setDocCache(new Map());
    setOverviewIndex(null);
    setDocContent(null);
    loadOverview();
  }, [loadOverview]);

  const startPollingTask = useCallback((taskId: string) => {
    genTaskIdRef.current = taskId;
    if (genPollRef.current) clearInterval(genPollRef.current);

    const poll = setInterval(async () => {
      try {
        const resp = await fetch(`/api/tasks/${taskId}`);
        const status = await resp.json();
        const progress = status.progress ?? 0;
        const message = status.status_message || status.message || status.step || 'Generating...';
        const trajectory = status.trajectory || [];
        const lastUpdateAt = trajectory.length > 0
          ? trajectory[trajectory.length - 1].timestamp
          : null;

        if (status.status === 'completed') {
          clearInterval(poll);
          genPollRef.current = null;
          setGenProgress({
            generating: false,
            status: 'completed',
            taskId,
            progress: 100,
            message: 'Done!',
            trajectory,
            lastUpdateAt,
          });
          setTimeout(() => handleRefresh(), 500);
        } else if (status.status === 'failed' || status.status === 'cancelled' || status.status === 'stalled') {
          clearInterval(poll);
          genPollRef.current = null;
          setGenProgress({
            generating: false,
            status: status.status,
            taskId,
            progress,
            message: status.error || message,
            trajectory,
            lastUpdateAt,
          });
        } else {
          setGenProgress({
            generating: true,
            status: status.status,
            taskId,
            progress,
            message,
            trajectory,
            lastUpdateAt,
          });
        }
      } catch {
        // ignore polling errors
      }
    }, 2000);

    genPollRef.current = poll;
  }, [handleRefresh]);

  // Load overview on mount
  useEffect(() => {
    graphStatsFetched.current = false;
    if (repoName) {
      loadOverview();
    } else {
      setOverviewLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoName]);

  // Load document content when path or version changes
  const hasOverviewIndex = !!overviewIndex;
  useEffect(() => {
    if (hasOverviewIndex && currentDocPath && currentVersionId) {
      loadDocContent(currentDocPath, currentVersionId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentDocPath, currentVersionId, hasOverviewIndex]);

  // Fetch graph stats when no docs exist (only after initial overview load completes)
  const graphStatsFetched = useRef(false);
  useEffect(() => {
    if (initialLoadDone.current && !overviewLoading && !overviewIndex && repoName && !graphStats && !graphStatsFetched.current) {
      graphStatsFetched.current = true;
      setGraphStatsLoading(true);
      listGraphProjects()
        .then((data) => {
          const project = data.projects?.find((p) =>
            p.name === repoName || p.name === `${repoName}_claude`
          );
          if (project) {
            setGraphStats({
              name: project.name,
              node_count: project.node_count,
              relationship_count: project.relationship_count,
              has_graph: project.has_graph,
              path: project.path ?? null,
              sync_enabled: project.sync_enabled ?? false,
            });
          }
        })
        .catch(err => console.warn('Failed to fetch graph stats:', err))
        .finally(() => setGraphStatsLoading(false));
    }
  }, [overviewLoading, overviewIndex, repoName, graphStats]);

  // Manual doc generation trigger
  const startGeneration = useCallback((opts?: { model?: string; doc_depth?: number; language?: string }) => {
    if (!repoName || genProgress.generating) return;
    setGenProgress({
      generating: true,
      status: 'pending',
      taskId: null,
      progress: 0,
      message: 'Starting documentation generation...',
      trajectory: [],
      lastUpdateAt: null,
    });

    const body: { language: string; doc_depth: number; model?: string } = {
      language: opts?.language || 'en',
      doc_depth: opts?.doc_depth ?? 2,
    };
    if (opts?.model) body.model = opts.model;

    fetch(`/api/overview/${repoName}/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
      .then(r => r.json())
      .then(data => {
        if (data.task_id) {
          startPollingTask(data.task_id);
        }
      })
      .catch(() => {
        setGenProgress({
          generating: false,
          status: 'failed',
          taskId: null,
          progress: 0,
          message: 'Failed to start generation',
          trajectory: [],
          lastUpdateAt: null,
        });
      });
  }, [repoName, genProgress.generating, startPollingTask]);

  const resumeGeneration = useCallback((taskId?: string | null) => {
    const effectiveTaskId = taskId || genProgress.taskId || genTaskIdRef.current;
    if (!repoName || !effectiveTaskId || genProgress.generating) return;

    setGenProgress(prev => ({
      ...prev,
      generating: true,
      status: 'running',
      taskId: effectiveTaskId,
      message: 'Resuming documentation generation...',
    }));

    fetch(`/api/overview/${repoName}/generate/${effectiveTaskId}/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    })
      .then(async (response) => {
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data.error || data.detail || 'Failed to resume generation');
        }
        startPollingTask(effectiveTaskId);
      })
      .catch((error: unknown) => {
        setGenProgress(prev => ({
          ...prev,
          generating: false,
          status: 'failed',
          message: error instanceof Error ? error.message : 'Failed to resume generation',
        }));
      });
  }, [repoName, genProgress.generating, genProgress.taskId, startPollingTask]);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (genPollRef.current) {
        clearInterval(genPollRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (!repoName || overviewIndex || genProgress.generating || genProgress.taskId) return;

    fetch(`/api/tasks/active?include_completed=false&task_type=overview_gen&repo_name=${repoName}`)
      .then(r => r.json())
      .then(data => {
        const tasks = Array.isArray(data.tasks) ? data.tasks : [];
        const existingTask = tasks.find((task: { status?: string }) =>
          task.status === 'running' || task.status === 'pending' || task.status === 'stalled'
        );
        if (!existingTask?.task_id) return;

        genTaskIdRef.current = existingTask.task_id;
        const trajectory = existingTask.trajectory || [];
        const lastUpdateAt = trajectory.length > 0
          ? trajectory[trajectory.length - 1].timestamp
          : null;
        const isGenerating = existingTask.status === 'running' || existingTask.status === 'pending';

        setGenProgress({
          generating: isGenerating,
          status: existingTask.status || null,
          taskId: existingTask.task_id,
          progress: existingTask.progress ?? 0,
          message: existingTask.error || existingTask.status_message || existingTask.step || '',
          trajectory,
          lastUpdateAt,
        });

        if (isGenerating) {
          startPollingTask(existingTask.task_id);
        }
      })
      .catch(() => {
        // ignore task restore errors
      });
  }, [repoName, overviewIndex, genProgress.generating, genProgress.taskId, startPollingTask]);

  return {
    overviewIndex,
    overviewLoading,
    currentDocPath,
    docContent,
    docContentLoading,
    versions,
    currentVersionId,
    defaultVersionId,
    graphStats,
    genProgress,
    handleNavigate,
    handleRefresh,
    loadDocContent,
    handleVersionChange,
    loadOverview,
    startGeneration,
    resumeGeneration,
  };
}

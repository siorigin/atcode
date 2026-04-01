'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * SyncPanel - Simplified sync control panel for a repository.
 *
 * Provides:
 * - Current branch display + Pull button (most frequent operation)
 * - Sync Now button with pending change count (detect local file changes)
 * - Branch/tag checkout (via dropdown)
 * - Advanced settings (collapsed): Auto-watch toggle, Embedding mode
 * - Sync result summary
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';
import { Modal } from '@/components/Modal';
import { apiFetch } from '@/lib/api-client';
import {
  SyncStatus,
  GitRef,
  UpdateResult,
  CheckoutTaskResponse,
  PendingFile,
  SyncHistoryItem,
  EmbeddingMode,
  getSyncStatus,
  startWatching,
  stopWatching,
  syncNow,
  isSyncTaskResponse,
  listBranches,
  listTags,
  fetchRemote,
  checkoutRef,
  pullRemote,
} from '@/lib/sync-api';
import { useSyncStatus } from '@/lib/hooks/useSyncStatus';

interface SyncPanelProps {
  projectName: string;
  repoPath?: string;
  subdirs?: string[];
  hasGraph?: boolean;
  onSyncComplete?: (result: UpdateResult) => void;
  onCheckoutStart?: (taskId: string) => void;
  onError?: (error: string) => void;
}

export function SyncPanel({
  projectName,
  repoPath,
  subdirs,
  hasGraph,
  onSyncComplete,
  onCheckoutStart,
  onError,
}: SyncPanelProps) {
  const { theme } = useTheme();
  const colors = getThemeColors(theme);

  // State
  const [status, setStatus] = useState<SyncStatus | null>(null);
  const [branches, setBranches] = useState<GitRef[]>([]);
  const [tags, setTags] = useState<GitRef[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showBranchDropdown, setShowBranchDropdown] = useState(false);
  const [includeRemoteBranches, setIncludeRemoteBranches] = useState(false);
  const [needsInit, setNeedsInit] = useState(false);
  const [initRepoPath, setInitRepoPath] = useState(repoPath || '');
  // Checkout confirmation state
  const [pendingCheckoutRef, setPendingCheckoutRef] = useState<string | null>(null);
  const [showCheckoutConfirm, setShowCheckoutConfirm] = useState(false);
  // Tag search and expand
  const [tagSearch, setTagSearch] = useState('');
  const [showAllTags, setShowAllTags] = useState(false);
  // Advanced settings
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [embeddingMode, setEmbeddingMode] = useState<EmbeddingMode>('skip');
  // Sync result toast
  const [syncResult, setSyncResult] = useState<{ added: number; modified: number; deleted: number; duration_ms: number } | null>(null);
  // Task progress (for pull/checkout)
  const [taskProgress, setTaskProgress] = useState<{ taskId: string; progress: number; step: string; message: string } | null>(null);

  // Real-time sync status via WebSocket
  const syncStatus = useSyncStatus(projectName);

  // Merge WebSocket real-time status with local status
  const effectiveStatus = syncStatus.status || status;
  const statusRequestInProgressRef = useRef(false);
  const gitRefsRequestInProgressRef = useRef(false);
  const isPanelOpenRef = useRef(true);
  const branchDropdownRef = useRef<HTMLDivElement>(null);
  const branchMenuRef = useRef<HTMLDivElement>(null);
  const branchButtonRef = useRef<HTMLButtonElement>(null);

  // Load sync status
  const loadStatus = useCallback(async () => {
    if (!isPanelOpenRef.current || statusRequestInProgressRef.current) return;
    statusRequestInProgressRef.current = true;
    try {
      const s = await getSyncStatus(projectName);
      if (isPanelOpenRef.current) {
        setStatus(s);
        setError(null);
        setNeedsInit(false);
      }
    } catch (e: any) {
      if (isPanelOpenRef.current) {
        if (e.message?.includes('not found') || e.message?.includes('404')) {
          setNeedsInit(true);
        }
        setStatus(null);
      }
    } finally {
      statusRequestInProgressRef.current = false;
    }
  }, [projectName]);

  // Load Git refs
  const loadGitRefs = useCallback(async () => {
    if (!isPanelOpenRef.current || gitRefsRequestInProgressRef.current) return;
    gitRefsRequestInProgressRef.current = true;
    try {
      const [branchList, tagList] = await Promise.all([
        listBranches(projectName, includeRemoteBranches).catch(() => []),
        listTags(projectName).catch(() => []),
      ]);
      if (isPanelOpenRef.current) {
        setBranches(branchList);
        setTags(tagList);
      }
    } catch (e) {
      // silently ignore
    } finally {
      gitRefsRequestInProgressRef.current = false;
    }
  }, [projectName, includeRemoteBranches]);

  // Initial load
  useEffect(() => {
    const load = async () => {
      setLoading(true);
      if (repoPath) {
        try {
          await startWatching(projectName, repoPath, false, subdirs, embeddingMode);
        } catch (e: any) {
          console.debug('Initialize sync manager failed:', e.message);
        }
      }
      await Promise.all([loadStatus(), loadGitRefs()]);
      setLoading(false);
    };
    load();
  }, [projectName, repoPath]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-initialize
  useEffect(() => {
    if (needsInit && repoPath && !actionLoading && !status) {
      const autoInit = async () => {
        setActionLoading('init');
        setError(null);
        try {
          await startWatching(projectName, repoPath, false);
          await Promise.all([loadStatus(), loadGitRefs()]);
        } catch (e: any) {
          setError(e.message);
        } finally {
          setActionLoading(null);
        }
      };
      autoInit();
    }
  }, [needsInit, repoPath, projectName, actionLoading, status, loadStatus, loadGitRefs]);

  // Re-fetch branches when includeRemoteBranches toggle changes
  const isFirstRender = useRef(true);
  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }
    const refetch = async () => {
      setActionLoading('branches');
      try {
        const branchList = await listBranches(projectName, includeRemoteBranches).catch(() => []);
        if (isPanelOpenRef.current) {
          setBranches(branchList);
        }
      } finally {
        setActionLoading(null);
      }
    };
    refetch();
  }, [includeRemoteBranches, projectName]);

  // Cleanup
  useEffect(() => {
    isPanelOpenRef.current = true;
    return () => { isPanelOpenRef.current = false; };
  }, []);

  // Close branch dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      const inButton = branchDropdownRef.current?.contains(target);
      const inMenu = branchMenuRef.current?.contains(target);
      if (branchDropdownRef.current && !inButton && !inMenu) {
        setShowBranchDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Poll task progress
  useEffect(() => {
    if (!taskProgress) return;
    const poll = async () => {
      try {
        const response = await apiFetch(`/api/tasks/${taskProgress.taskId}`);
        if (response.ok) {
          const task = await response.json();
          setTaskProgress(prev => prev ? {
            ...prev,
            progress: task.progress || 0,
            step: task.step || '',
            message: task.status_message || task.message || '',
          } : null);
          if (task.status === 'completed') {
            const result = task.result;
            if (result) {
              setSyncResult({ added: result.added || 0, modified: result.modified || 0, deleted: result.deleted || 0, duration_ms: result.duration_ms || 0 });
            }
            setTaskProgress(null);
            await Promise.all([loadStatus(), loadGitRefs()]);
            setActionLoading(null);
          } else if (task.status === 'failed') {
            setTaskProgress(null);
            setError(task.error || 'Operation failed');
            setActionLoading(null);
          } else {
            setTimeout(poll, 1000);
          }
        }
      } catch (e) {
        console.error('Error polling task:', e);
      }
    };
    setTimeout(poll, 500);
  }, [taskProgress?.taskId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-dismiss sync result toast
  useEffect(() => {
    if (!syncResult) return;
    const timer = setTimeout(() => setSyncResult(null), 8000);
    return () => clearTimeout(timer);
  }, [syncResult]);

  // === Handlers ===

  const handleInitialize = async () => {
    if (!initRepoPath.trim()) { setError('Please enter the repository path'); return; }
    setActionLoading('init');
    setError(null);
    try {
      await startWatching(projectName, initRepoPath.trim(), false, subdirs, embeddingMode);
      await Promise.all([loadStatus(), loadGitRefs()]);
    } catch (e: any) {
      setError(e.message);
      onError?.(e.message);
    } finally {
      setActionLoading(null);
    }
  };

  const handleSyncNow = async () => {
    setActionLoading('sync');
    setError(null);
    try {
      const response = await syncNow(projectName, repoPath, embeddingMode);
      if (isSyncTaskResponse(response)) {
        // Backend is building graph first — switch to task polling mode
        onCheckoutStart?.(response.task_id);
        setTaskProgress({
          taskId: response.task_id,
          progress: 0,
          step: 'Building knowledge graph...',
          message: response.message || 'Building graph before sync...',
        });
      } else {
        setSyncResult({ added: response.added, modified: response.modified, deleted: response.deleted, duration_ms: response.duration_ms });
        await loadStatus();
        await syncStatus.refresh();
        onSyncComplete?.(response);
        setActionLoading(null);
      }
    } catch (e: any) {
      setError(e.message);
      onError?.(e.message);
      setActionLoading(null);
    }
  };

  const handlePull = async () => {
    setActionLoading('pull');
    setError(null);
    try {
      const response = await pullRemote(projectName);
      if (response.task_id) {
        onCheckoutStart?.(response.task_id);
        setTaskProgress({
          taskId: response.task_id,
          progress: 0,
          step: 'Starting...',
          message: response.message || 'Pulling...',
        });
      } else {
        await Promise.all([loadStatus(), loadGitRefs()]);
        setActionLoading(null);
      }
    } catch (e: any) {
      setError(e.message);
      onError?.(e.message);
      setActionLoading(null);
    }
  };

  const handleCheckoutClick = (ref: string) => {
    setPendingCheckoutRef(ref);
    setShowCheckoutConfirm(true);
    setShowBranchDropdown(false);
  };

  const confirmCheckout = async () => {
    if (!pendingCheckoutRef) return;
    setShowCheckoutConfirm(false);
    setActionLoading('checkout');
    setError(null);
    try {
      const response = await checkoutRef(projectName, pendingCheckoutRef, true);
      if (response.task_id) {
        onCheckoutStart?.(response.task_id);
        setTaskProgress({
          taskId: response.task_id,
          progress: 0,
          step: 'Starting...',
          message: response.message || 'Checking out...',
        });
      } else {
        await Promise.all([loadStatus(), loadGitRefs()]);
        setActionLoading(null);
      }
    } catch (e: any) {
      setError(e.message);
      onError?.(e.message);
      setActionLoading(null);
    }
    setPendingCheckoutRef(null);
  };

  const handleStartWatching = async () => {
    setActionLoading('start');
    try {
      await startWatching(projectName, repoPath, true, subdirs, embeddingMode);
      await loadStatus();
    } catch (e: any) {
      setError(e.message);
      onError?.(e.message);
    } finally {
      setActionLoading(null);
    }
  };

  const handleStopWatching = async () => {
    setActionLoading('stop');
    try {
      await stopWatching(projectName);
      await loadStatus();
    } catch (e: any) {
      setError(e.message);
      onError?.(e.message);
    } finally {
      setActionLoading(null);
    }
  };

  // Current ref display
  const currentRef = branches.find(b => b.is_current) ||
                     tags.find(t => t.is_current) ||
                     (status?.current_ref ? { name: status.current_ref, short_sha: '' } : null);

  const isGitRepo = effectiveStatus?.is_git_repo ?? (branches.length > 0 || tags.length > 0);
  const pendingCount = effectiveStatus?.pending_changes ?? 0;
  const isWatching = effectiveStatus?.is_watching ?? false;
  const isProcessing = effectiveStatus?.is_processing ?? false;
  const isBusy = !!actionLoading || isProcessing || !!taskProgress;

  // === Render ===

  if (loading) {
    return (
      <div style={{
        padding: '16px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '8px',
        color: colors.textMuted,
      }}>
        <Spinner color={colors.accent} borderColor={colors.borderLight} size={14} />
        <span style={{ fontSize: '13px' }}>Loading...</span>
      </div>
    );
  }

  // Needs initialization
  if (needsInit && !status) {
    if (repoPath && actionLoading === 'init') {
      return (
        <div style={{ padding: '16px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', color: colors.textMuted }}>
          <Spinner color={colors.accent} borderColor={colors.borderLight} size={14} />
          <span style={{ fontSize: '13px' }}>Initializing...</span>
        </div>
      );
    }

    if (!repoPath) {
      return (
        <div style={{ padding: '16px' }}>
          <div style={{ fontSize: '13px', color: colors.textSecondary, marginBottom: '12px' }}>
            Enter repository path to enable sync:
          </div>
          <div style={{ display: 'flex', gap: '8px' }}>
            <input
              type="text"
              value={initRepoPath}
              onChange={(e) => setInitRepoPath(e.target.value)}
              placeholder="/path/to/repo"
              style={{
                flex: 1,
                padding: '8px 12px',
                background: colors.bgTertiary,
                border: `1px solid ${colors.borderLight}`,
                borderRadius: '6px',
                fontSize: '13px',
                color: colors.text,
                outline: 'none',
              }}
              onKeyDown={(e) => e.key === 'Enter' && handleInitialize()}
            />
            <ActionButton
              onClick={handleInitialize}
              disabled={!!actionLoading}
              loading={actionLoading === 'init'}
              colors={colors}
              title="Initialize"
            >
              Init
            </ActionButton>
          </div>
          {error && <ErrorMessage message={error} colors={colors} />}
        </div>
      );
    }
  }

  return (
    <div style={{ padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: '18px' }}>

      {/* Row 1: Branch + Pull + Sync Now */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>

        {/* Branch selector (if git repo) */}
        {isGitRepo && (
          <div ref={branchDropdownRef} style={{ position: 'relative' }}>
            <button
              ref={branchButtonRef}
              onClick={() => setShowBranchDropdown(!showBranchDropdown)}
              disabled={isBusy}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '8px 14px',
                background: colors.bgTertiary,
                border: `1px solid ${colors.borderLight}`,
                borderRadius: '8px',
                fontSize: '15px',
                fontWeight: 500,
                color: colors.text,
                cursor: isBusy ? 'not-allowed' : 'pointer',
                opacity: isBusy ? 0.6 : 1,
                transition: 'all 150ms ease',
                maxWidth: '260px',
              }}
              onMouseEnter={(e) => { if (!isBusy) e.currentTarget.style.background = colors.bgHover; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = colors.bgTertiary; }}
            >
              <BranchIcon color={colors.textMuted} />
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {currentRef?.name || 'No branch'}
              </span>
              {(currentRef as any)?.short_sha && (
                <span style={{ fontSize: '12px', color: colors.textMuted, fontFamily: 'var(--font-mono)' }}>
                  {(currentRef as any).short_sha}
                </span>
              )}
              <ChevronIcon />
            </button>

            {/* Branch dropdown */}
            {showBranchDropdown && (() => {
              const rect = branchButtonRef.current?.getBoundingClientRect();
              const top = rect ? rect.bottom + 6 : 0;
              const left = rect ? rect.left : 0;
              const maxH = rect ? Math.max(200, window.innerHeight - rect.bottom - 24) : 400;
              return (
              <div ref={branchMenuRef} style={{
                position: 'fixed',
                top: `${top}px`,
                left: `${left}px`,
                background: colors.card,
                border: `1px solid ${colors.borderLight}`,
                borderRadius: '10px',
                boxShadow: `0 8px 24px ${colors.shadowColor}`,
                minWidth: '320px',
                maxWidth: '480px',
                maxHeight: `${maxH}px`,
                overflowY: 'auto',
                zIndex: 9999,
              }}>
                {/* Remote toggle */}
                <div style={{
                  padding: '10px 16px',
                  borderBottom: `1px solid ${colors.borderLight}`,
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                }}>
                  <input
                    type="checkbox"
                    id={`remote-${projectName}`}
                    checked={includeRemoteBranches}
                    onChange={(e) => setIncludeRemoteBranches(e.target.checked)}
                    style={{ cursor: 'pointer', width: '16px', height: '16px' }}
                  />
                  <label htmlFor={`remote-${projectName}`} style={{ fontSize: '14px', color: colors.textSecondary, cursor: 'pointer' }}>
                    Show remote branches
                    {actionLoading === 'branches' && <span style={{ marginLeft: '6px', fontSize: '12px', color: colors.textMuted }}>Loading...</span>}
                  </label>
                </div>

                {/* Branches */}
                {branches.length > 0 && (
                  <>
                    <SectionHeader label={`Branches (${branches.length})`} colors={colors} />
                    {branches.map((branch) => (
                      <RefItem
                        key={branch.name}
                        ref_={branch}
                        isCurrent={branch.is_current}
                        onClick={() => !branch.is_current && handleCheckoutClick(branch.name)}
                        colors={colors}
                      />
                    ))}
                  </>
                )}

                {/* Tags */}
                {tags.length > 0 && (() => {
                  const filteredTags = tagSearch
                    ? tags.filter(t => t.name.toLowerCase().includes(tagSearch.toLowerCase()))
                    : tags;
                  const displayTags = (showAllTags || tagSearch) ? filteredTags : filteredTags.slice(0, 10);
                  const hiddenCount = filteredTags.length - displayTags.length;

                  return (
                    <>
                      <SectionHeader label={`Tags (${tags.length})`} colors={colors} border={branches.length > 0} />
                      {/* Tag search input */}
                      {tags.length > 10 && (
                        <div style={{ padding: '6px 12px' }}>
                          <input
                            type="text"
                            value={tagSearch}
                            onChange={(e) => setTagSearch(e.target.value)}
                            placeholder="Search tags..."
                            style={{
                              width: '100%',
                              padding: '7px 10px',
                              background: colors.bgTertiary,
                              border: `1px solid ${colors.borderLight}`,
                              borderRadius: '6px',
                              fontSize: '14px',
                              color: colors.text,
                              outline: 'none',
                              boxSizing: 'border-box' as const,
                            }}
                            onFocus={(e) => { e.currentTarget.style.borderColor = colors.accent; }}
                            onBlur={(e) => { e.currentTarget.style.borderColor = colors.borderLight; }}
                            onClick={(e) => e.stopPropagation()}
                          />
                        </div>
                      )}
                      <div style={{ maxHeight: '280px', overflowY: 'auto' }}>
                        {displayTags.map((tag) => (
                          <RefItem
                            key={tag.name}
                            ref_={tag}
                            isCurrent={false}
                            isTag
                            onClick={() => handleCheckoutClick(tag.name)}
                            colors={colors}
                          />
                        ))}
                        {hiddenCount > 0 && (
                          <button
                            onClick={(e) => { e.stopPropagation(); setShowAllTags(true); }}
                            style={{
                              width: '100%',
                              padding: '10px 16px',
                              fontSize: '14px',
                              color: colors.accent,
                              background: 'none',
                              border: 'none',
                              cursor: 'pointer',
                              textAlign: 'center' as const,
                            }}
                            onMouseEnter={(e) => { e.currentTarget.style.background = colors.bgHover; }}
                            onMouseLeave={(e) => { e.currentTarget.style.background = 'none'; }}
                          >
                            +{hiddenCount} more tags
                          </button>
                        )}
                        {tagSearch && filteredTags.length === 0 && (
                          <div style={{ padding: '10px 16px', fontSize: '14px', color: colors.textMuted, textAlign: 'center' }}>
                            No tags matching &ldquo;{tagSearch}&rdquo;
                          </div>
                        )}
                      </div>
                    </>
                  );
                })()}
              </div>
              );
            })()}
          </div>
        )}

        {/* Pull button (if git repo) */}
        {isGitRepo && (
          <ActionButton
            onClick={handlePull}
            disabled={isBusy}
            loading={actionLoading === 'pull'}
            colors={colors}
            title="Pull from remote (fetch + merge + update graph)"
            variant="secondary"
          >
            <DownloadIcon color={actionLoading === 'pull' ? colors.accent : colors.textSecondary} />
            Pull
          </ActionButton>
        )}

        {/* Spacer */}
        <div style={{ flex: 1 }} />

        {/* Sync Now button */}
        <ActionButton
          onClick={handleSyncNow}
          disabled={isBusy}
          loading={actionLoading === 'sync'}
          colors={colors}
          title="Detect local file changes and update graph"
          variant="primary"
        >
          <SyncIcon />
          Sync
          {pendingCount > 0 && (
            <span style={{
              background: colors.accent,
              color: '#fff',
              borderRadius: '10px',
              padding: '2px 8px',
              fontSize: '12px',
              fontWeight: 600,
              marginLeft: '4px',
            }}>
              {pendingCount}
            </span>
          )}
        </ActionButton>
      </div>

      {/* Graph build commit indicator */}
      {isGitRepo && effectiveStatus && (() => {
        const builtSha = effectiveStatus.built_commit_sha;
        const currentSha = (currentRef as any)?.commit_sha || '';
        const builtShort = builtSha ? builtSha.slice(0, 7) : null;

        if (!builtSha) {
          if (hasGraph === false) return null;
          return (
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              fontSize: '13px',
              color: colors.textMuted,
              padding: '6px 12px',
              background: colors.bgTertiary,
              borderRadius: '8px',
            }}>
              <GraphIcon color={colors.textMuted} />
              <span>Graph version: unknown</span>
            </div>
          );
        }

        const isUpToDate = currentSha
          ? builtSha.startsWith(currentSha.slice(0, 7)) || currentSha.startsWith(builtSha.slice(0, 7))
          : false;
        return (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            fontSize: '13px',
            color: isUpToDate ? colors.success : colors.warning,
            padding: '6px 12px',
            background: isUpToDate ? `${colors.success}10` : `${colors.warning}10`,
            borderRadius: '8px',
          }}>
            <GraphIcon color={isUpToDate ? colors.success : colors.warning} />
            <span style={{ fontFamily: 'var(--font-mono)' }}>
              {isUpToDate
                ? `Graph up to date (${builtShort})`
                : `Graph outdated — built at ${builtShort}`
              }
            </span>
          </div>
        );
      })()}

      {/* Task progress bar */}
      {taskProgress && (
        <div style={{
          background: colors.bgTertiary,
          borderRadius: '8px',
          padding: '12px 16px',
          fontSize: '14px',
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '6px', color: colors.textSecondary }}>
            <span>{taskProgress.step}</span>
            <span>{taskProgress.progress}%</span>
          </div>
          <div style={{
            height: '6px',
            background: colors.borderLight,
            borderRadius: '3px',
            overflow: 'hidden',
          }}>
            <div style={{
              height: '100%',
              width: `${taskProgress.progress}%`,
              background: colors.accent,
              borderRadius: '3px',
              transition: 'width 300ms ease',
            }} />
          </div>
          {taskProgress.message && (
            <div style={{ marginTop: '6px', color: colors.textMuted, fontSize: '13px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {taskProgress.message}
            </div>
          )}
        </div>
      )}

      {/* Sync result toast */}
      {syncResult && (
        <div style={{
          background: colors.successBg || `${colors.success}15`,
          border: `1px solid ${colors.successBorder || colors.success}`,
          borderRadius: '8px',
          padding: '10px 16px',
          fontSize: '14px',
          color: colors.success,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}>
          <span>
            {syncResult.added + syncResult.modified + syncResult.deleted === 0
              ? 'No changes detected'
              : `+${syncResult.added} added, ~${syncResult.modified} modified, -${syncResult.deleted} deleted`
            }
          </span>
          <span style={{ fontSize: '13px', opacity: 0.7 }}>
            {(syncResult.duration_ms / 1000).toFixed(1)}s
          </span>
        </div>
      )}

      {/* Error message */}
      {error && <ErrorMessage message={error} colors={colors} onDismiss={() => setError(null)} />}

      {/* Status indicator */}
      {effectiveStatus && (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          fontSize: '13px',
          color: colors.textMuted,
        }}>
          <div style={{
            width: '8px',
            height: '8px',
            borderRadius: '50%',
            background: isWatching ? colors.success : colors.textMuted,
            flexShrink: 0,
          }} />
          <span>{isWatching ? 'Auto-watching' : 'Idle'}</span>
          {syncStatus.currentFile && (
            <>
              <span style={{ opacity: 0.5 }}>|</span>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '360px' }}>
                Processing: {syncStatus.currentFile.split('/').pop()}
              </span>
            </>
          )}
        </div>
      )}

      {/* No graph indicator */}
      {hasGraph === false && !taskProgress && (
        <div style={{
          background: `${colors.warning}12`,
          border: `1px solid ${colors.warning}30`,
          borderRadius: '8px',
          padding: '10px 14px',
          fontSize: '14px',
          color: colors.warning,
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
        }}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="12" />
            <line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
          No knowledge graph — will auto-build on sync/pull
        </div>
      )}

      {/* Advanced settings toggle */}
      <button
        onClick={() => setShowAdvanced(!showAdvanced)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          padding: '4px 0',
          background: 'none',
          border: 'none',
          fontSize: '13px',
          color: colors.textMuted,
          cursor: 'pointer',
          alignSelf: 'flex-start',
        }}
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
          style={{ transform: showAdvanced ? 'rotate(90deg)' : 'rotate(0deg)', transition: 'transform 150ms ease' }}>
          <path d="m9 18 6-6-6-6" />
        </svg>
        Advanced
      </button>

      {/* Advanced settings */}
      {showAdvanced && (
        <div style={{
          background: colors.bgTertiary,
          borderRadius: '10px',
          padding: '16px',
          display: 'flex',
          flexDirection: 'column',
          gap: '14px',
          fontSize: '14px',
        }}>
          {/* Auto-watch toggle */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <div style={{ color: colors.text, fontWeight: 500, fontSize: '14px' }}>Auto-watch</div>
              <div style={{ color: colors.textMuted, fontSize: '13px', marginTop: '2px' }}>Monitor file changes in real-time</div>
            </div>
            <button
              onClick={isWatching ? handleStopWatching : handleStartWatching}
              disabled={isBusy}
              style={{
                padding: '6px 16px',
                background: isWatching ? colors.error + '20' : colors.accent + '20',
                color: isWatching ? colors.error : colors.accent,
                border: 'none',
                borderRadius: '6px',
                fontSize: '14px',
                fontWeight: 500,
                cursor: isBusy ? 'not-allowed' : 'pointer',
                opacity: isBusy ? 0.5 : 1,
              }}
            >
              {actionLoading === 'start' || actionLoading === 'stop' ? '...' : isWatching ? 'Stop' : 'Start'}
            </button>
          </div>

          {/* Embedding mode */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <div style={{ color: colors.text, fontWeight: 500, fontSize: '14px' }}>Embeddings</div>
              <div style={{ color: colors.textMuted, fontSize: '13px', marginTop: '2px' }}>For semantic search (usually not needed)</div>
            </div>
            <select
              value={embeddingMode}
              onChange={(e) => setEmbeddingMode(e.target.value as EmbeddingMode)}
              style={{
                padding: '6px 10px',
                background: colors.bgSecondary,
                border: `1px solid ${colors.borderLight}`,
                borderRadius: '6px',
                fontSize: '14px',
                color: colors.text,
                cursor: 'pointer',
              }}
            >
              <option value="skip">Skip (fastest)</option>
              <option value="async">Async (background)</option>
              <option value="sync">Sync (immediate)</option>
            </select>
          </div>

          {/* Sync history (last result) */}
          {syncStatus.history.length > 0 && (
            <div>
              <div style={{ color: colors.text, fontWeight: 500, fontSize: '14px', marginBottom: '8px' }}>Recent sync</div>
              {syncStatus.history.slice(0, 3).map((item, i) => (
                <div key={i} style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  padding: '6px 0',
                  borderBottom: i < 2 && i < syncStatus.history.length - 1 ? `1px solid ${colors.borderLight}` : 'none',
                  color: colors.textSecondary,
                  fontSize: '13px',
                }}>
                  <span>
                    +{item.added} ~{item.modified} -{item.deleted}
                    {!item.success && <span style={{ color: colors.error }}> (errors)</span>}
                  </span>
                  <span style={{ color: colors.textMuted }}>
                    {new Date(item.timestamp).toLocaleTimeString()}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Checkout confirmation modal */}
      {showCheckoutConfirm && pendingCheckoutRef && (
        <Modal
          isOpen={true}
          onClose={() => { setShowCheckoutConfirm(false); setPendingCheckoutRef(null); }}
          title="Confirm Checkout"
          maxWidth="420px"
        >
          <div>
            <p style={{ marginBottom: '16px', color: colors.textSecondary, fontSize: '14px' }}>
              Switch to <strong>{pendingCheckoutRef}</strong>?
            </p>
            <div style={{
              marginBottom: '16px',
              padding: '10px',
              background: colors.warningBg,
              borderRadius: '6px',
              border: `1px solid ${colors.warningBorder}`,
              fontSize: '12px',
              color: colors.warning,
            }}>
              This will discard local uncommitted changes and update the knowledge graph.
            </div>
            <div style={{ display: 'flex', gap: '12px', justifyContent: 'flex-end' }}>
              <button
                onClick={() => { setShowCheckoutConfirm(false); setPendingCheckoutRef(null); }}
                style={{
                  padding: '8px 16px',
                  background: colors.buttonSecondaryBg,
                  border: 'none',
                  borderRadius: '6px',
                  fontSize: '13px',
                  cursor: 'pointer',
                  color: colors.text,
                }}
              >
                Cancel
              </button>
              <button
                onClick={confirmCheckout}
                style={{
                  padding: '8px 16px',
                  background: colors.buttonPrimaryBg,
                  border: 'none',
                  borderRadius: '6px',
                  fontSize: '13px',
                  fontWeight: 500,
                  cursor: 'pointer',
                  color: '#ffffff',
                }}
              >
                Switch
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* CSS for spinner animation */}
      <style jsx global>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}

// === Sub-components ===

function Spinner({ color, borderColor, size = 12 }: { color: string; borderColor: string; size?: number }) {
  return (
    <div style={{
      width: `${size}px`,
      height: `${size}px`,
      border: `2px solid ${borderColor}`,
      borderTopColor: color,
      borderRadius: '50%',
      animation: 'spin 0.8s linear infinite',
    }} />
  );
}

function ActionButton({
  onClick,
  disabled,
  loading,
  colors,
  title,
  variant = 'secondary',
  children,
}: {
  onClick: () => void;
  disabled: boolean;
  loading: boolean;
  colors: any;
  title: string;
  variant?: 'primary' | 'secondary';
  children: React.ReactNode;
}) {
  const isPrimary = variant === 'primary';
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '7px',
        padding: '8px 14px',
        background: isPrimary ? colors.buttonPrimaryBg : colors.bgTertiary,
        border: isPrimary ? 'none' : `1px solid ${colors.borderLight}`,
        borderRadius: '8px',
        fontSize: '14px',
        fontWeight: 500,
        color: isPrimary ? '#fff' : colors.text,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.6 : 1,
        transition: 'all 150ms ease',
      }}
      onMouseEnter={(e) => { if (!disabled) e.currentTarget.style.opacity = '0.85'; }}
      onMouseLeave={(e) => { e.currentTarget.style.opacity = disabled ? '0.6' : '1'; }}
    >
      {loading ? <Spinner color={isPrimary ? '#fff' : colors.accent} borderColor={isPrimary ? 'rgba(255,255,255,0.3)' : colors.borderLight} size={14} /> : children}
    </button>
  );
}

function ErrorMessage({ message, colors, onDismiss }: { message: string; colors: any; onDismiss?: () => void }) {
  return (
    <div style={{
      background: colors.errorBg || `${colors.error}15`,
      border: `1px solid ${colors.error}40`,
      borderRadius: '8px',
      padding: '10px 16px',
      fontSize: '14px',
      color: colors.error,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
    }}>
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{message}</span>
      {onDismiss && (
        <button onClick={onDismiss} style={{ background: 'none', border: 'none', color: colors.error, cursor: 'pointer', padding: '2px 6px', fontSize: '16px' }}>
          &times;
        </button>
      )}
    </div>
  );
}

function SectionHeader({ label, colors, border }: { label: string; colors: any; border?: boolean }) {
  return (
    <div style={{
      padding: '10px 16px 6px',
      fontSize: '12px',
      fontWeight: 600,
      color: colors.textMuted,
      textTransform: 'uppercase' as const,
      letterSpacing: '0.5px',
      borderTop: border ? `1px solid ${colors.borderLight}` : 'none',
    }}>
      {label}
    </div>
  );
}

function RefItem({ ref_, isCurrent, isTag, onClick, colors }: {
  ref_: GitRef;
  isCurrent: boolean;
  isTag?: boolean;
  onClick: () => void;
  colors: any;
}) {
  return (
    <button
      onClick={onClick}
      disabled={isCurrent}
      style={{
        width: '100%',
        padding: '10px 16px',
        background: isCurrent ? colors.accentBg : 'transparent',
        border: 'none',
        textAlign: 'left' as const,
        cursor: isCurrent ? 'default' : 'pointer',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        fontSize: '14px',
        color: isCurrent ? colors.accent : colors.text,
        transition: 'background 100ms ease',
        opacity: isCurrent ? 0.7 : 1,
      }}
      onMouseEnter={(e) => { if (!isCurrent) e.currentTarget.style.background = colors.bgHover; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = isCurrent ? colors.accentBg : 'transparent'; }}
    >
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const }}>
        {isTag ? '🏷️ ' : ''}{ref_.name}{isCurrent ? ' ✓' : ''}
      </span>
      <span style={{ fontSize: '12px', color: colors.textMuted, fontFamily: 'var(--font-mono)', flexShrink: 0, marginLeft: '10px' }}>
        {ref_.short_sha}
      </span>
    </button>
  );
}

// === SVG Icons ===

function BranchIcon({ color }: { color: string }) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2">
      <path d="M6 3v12" />
      <circle cx="18" cy="6" r="3" />
      <circle cx="6" cy="18" r="3" />
      <path d="M18 9a9 9 0 0 1-9 9" />
    </svg>
  );
}

function DownloadIcon({ color }: { color: string }) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2">
      <path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8" />
      <polyline points="8 12 12 16 16 12" />
      <line x1="12" y1="2" x2="12" y2="16" />
    </svg>
  );
}

function SyncIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.2" />
    </svg>
  );
}

function ChevronIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

function GraphIcon({ color }: { color: string }) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2">
      <circle cx="6" cy="6" r="3" />
      <circle cx="18" cy="18" r="3" />
      <circle cx="18" cy="6" r="3" />
      <line x1="8.5" y1="7.5" x2="15.5" y2="16.5" />
      <line x1="8.5" y1="6" x2="15" y2="6" />
    </svg>
  );
}

export default SyncPanel;

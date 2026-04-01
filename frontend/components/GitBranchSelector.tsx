'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * GitBranchSelector - Compact Git branch/tag selector dropdown.
 *
 * A standalone component for switching Git branches/tags.
 * Can be used in toolbars, sidebars, or as part of other panels.
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';
import { Modal } from '@/components/Modal';
import { apiFetch } from '@/lib/api-client';
import {
  GitRef,
  CheckoutTaskResponse,
  listBranches,
  listTags,
  getCurrentRef,
  checkoutRef,
  fetchRemote,
} from '@/lib/sync-api';

interface GitBranchSelectorProps {
  projectName: string;
  onCheckout?: (ref: string, taskResponse: CheckoutTaskResponse) => void;
  onError?: (error: string) => void;
  compact?: boolean;
  showFetchButton?: boolean;
}

export function GitBranchSelector({
  projectName,
  onCheckout,
  onError,
  compact = false,
  showFetchButton = true,
}: GitBranchSelectorProps) {
  const { theme } = useTheme();
  const colors = getThemeColors(theme);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // State
  const [isOpen, setIsOpen] = useState(false);
  const [branches, setBranches] = useState<GitRef[]>([]);
  const [tags, setTags] = useState<GitRef[]>([]);
  const [currentRef, setCurrentRef] = useState<GitRef | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [includeRemote, setIncludeRemote] = useState(false);
  const [pendingCheckout, setPendingCheckout] = useState<string | null>(null);
  const [showCheckoutConfirm, setShowCheckoutConfirm] = useState(false);
  const [checkoutProgress, setCheckoutProgress] = useState<{ taskId: string; ref: string; progress: number; step: string; message: string } | null>(null);

  // Load data
  const loadData = useCallback(async () => {
    try {
      const [branchList, tagList, current] = await Promise.all([
        listBranches(projectName, includeRemote).catch(() => []),
        listTags(projectName).catch(() => []),
        getCurrentRef(projectName).catch(() => null),
      ]);
      setBranches(branchList);
      setTags(tagList);
      setCurrentRef(current);
    } catch (e) {
      // Silently handle errors
    } finally {
      setLoading(false);
    }
  }, [projectName, includeRemote]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Close dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Poll for checkout task completion
  useEffect(() => {
    if (!checkoutProgress) return;

    const poll = async () => {
      try {
        const response = await apiFetch(`/api/tasks/${checkoutProgress.taskId}`);
        if (response.ok) {
          const task = await response.json();
          // Update progress with latest data
          setCheckoutProgress(prev => prev ? {
            ...prev,
            progress: task.progress || 0,
            step: task.step || '',
            message: task.status_message || task.message || '',
          } : null);

          if (task.status === 'completed') {
            setCheckoutProgress(null);
            await loadData();
            onCheckout?.(checkoutProgress.ref, { task_id: checkoutProgress.taskId, status: 'completed', message: 'Checkout completed' });
          } else if (task.status === 'failed') {
            setCheckoutProgress(null);
            onError?.(task.error || 'Checkout failed');
          } else {
            // Still running, poll again
            setTimeout(poll, 1000);
          }
        }
      } catch (e) {
        console.error('Error polling checkout task:', e);
      }
    };

    setTimeout(poll, 500);
  }, [checkoutProgress, projectName, loadData, onCheckout, onError]);

  // Handle checkout with confirmation
  const handleCheckoutClick = (ref: string) => {
    setPendingCheckout(ref);
    setShowCheckoutConfirm(true);
    setIsOpen(false);
  };

  // Confirm checkout
  const confirmCheckout = async () => {
    if (!pendingCheckout) return;
    setShowCheckoutConfirm(false);

    setActionLoading('checkout');
    try {
      const response = await checkoutRef(projectName, pendingCheckout, true);

      if (response.task_id) {
        // Background task - show progress and poll
        setCheckoutProgress({
          taskId: response.task_id,
          ref: pendingCheckout,
          progress: 0,
          step: 'Starting...',
          message: response.message || 'Checking out...',
        });
      } else {
        // Immediate completion
        await loadData();
        onCheckout?.(pendingCheckout, response);
        setActionLoading(null);
      }
    } catch (e: any) {
      onError?.(e.message);
      setActionLoading(null);
    }
  };

  // Handle fetch
  const handleFetch = async () => {
    setActionLoading('fetch');
    try {
      await fetchRemote(projectName);
      await loadData();
    } catch (e: any) {
      onError?.(e.message);
    } finally {
      setActionLoading(null);
    }
  };

  if (loading) {
    return (
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: '6px',
        padding: compact ? '4px 8px' : '6px 12px',
        background: colors.bgTertiary,
        borderRadius: '6px',
        color: colors.textMuted,
        fontSize: compact ? '12px' : '13px',
      }}>
        <div style={{
          width: '12px',
          height: '12px',
          border: `2px solid ${colors.borderLight}`,
          borderTopColor: colors.accent,
          borderRadius: '50%',
          animation: 'spin 0.8s linear infinite',
        }} />
        <span>Loading...</span>
      </div>
    );
  }

  // No branches found (not a git repo or error)
  if (branches.length === 0 && tags.length === 0) {
    return null;
  }

  // Show checkout in progress
  const isLoading = actionLoading === 'checkout' || !!checkoutProgress;

  return (
    <div ref={dropdownRef} style={{ position: 'relative', display: 'inline-block' }}>
      {/* Trigger button */}
      <div style={{ display: 'flex', gap: '4px' }}>
        <button
          onClick={() => setIsOpen(!isOpen)}
          disabled={isLoading}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: compact ? '4px 8px' : '6px 12px',
            background: colors.bgTertiary,
            border: `1px solid ${colors.borderLight}`,
            borderRadius: showFetchButton ? '6px 0 0 6px' : '6px',
            fontSize: compact ? '12px' : '13px',
            fontWeight: 500,
            color: colors.text,
            cursor: isLoading ? 'not-allowed' : 'pointer',
            opacity: isLoading ? 0.6 : 1,
            transition: 'all 150ms ease',
          }}
          onMouseEnter={(e) => {
            if (!isLoading) {
              e.currentTarget.style.background = colors.bgHover;
            }
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = colors.bgTertiary;
          }}
        >
          {isLoading ? (
            <div style={{
              width: '12px',
              height: '12px',
              border: `2px solid ${colors.borderLight}`,
              borderTopColor: colors.accent,
              borderRadius: '50%',
              animation: 'spin 0.8s linear infinite',
            }} />
          ) : (
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={colors.textMuted} strokeWidth="2">
              <path d="M6 3v12" />
              <circle cx="18" cy="6" r="3" />
              <circle cx="6" cy="18" r="3" />
              <path d="M18 9a9 9 0 0 1-9 9" />
            </svg>
          )}
          <span style={{
            maxWidth: '200px',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}>
            {checkoutProgress ? (
              <span title={`${checkoutProgress.step}: ${checkoutProgress.message}`}>
                {checkoutProgress.step} ({checkoutProgress.progress}%)
              </span>
            ) : (
              (currentRef?.name || 'Select branch')
            )}
          </span>
          {currentRef?.short_sha && !compact && !checkoutProgress && (
            <span style={{
              fontSize: '10px',
              color: colors.textMuted,
              fontFamily: 'var(--font-mono)',
            }}>
              {currentRef.short_sha}
            </span>
          )}
          {!checkoutProgress && (
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="m6 9 6 6 6-6" />
            </svg>
          )}
        </button>

        {/* Fetch button */}
        {showFetchButton && (
          <button
            onClick={handleFetch}
            disabled={isLoading}
            title="Fetch from remote"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: compact ? '4px 6px' : '6px 10px',
              background: colors.bgTertiary,
              border: `1px solid ${colors.borderLight}`,
              borderLeft: 'none',
              borderRadius: '0 6px 6px 0',
              cursor: isLoading ? 'not-allowed' : 'pointer',
              opacity: actionLoading === 'fetch' ? 0.6 : 1,
              transition: 'all 150ms ease',
            }}
            onMouseEnter={(e) => {
              if (!isLoading) {
                e.currentTarget.style.background = colors.bgHover;
              }
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = colors.bgTertiary;
            }}
          >
            {actionLoading === 'fetch' ? (
              <div style={{
                width: '12px',
                height: '12px',
                border: `2px solid ${colors.borderLight}`,
                borderTopColor: colors.accent,
                borderRadius: '50%',
                animation: 'spin 0.8s linear infinite',
              }} />
            ) : (
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={colors.textMuted} strokeWidth="2">
                <path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8" />
                <polyline points="16 6 12 2 8 6" />
                <line x1="12" y1="2" x2="12" y2="15" />
              </svg>
            )}
          </button>
        )}
      </div>

      {/* Dropdown */}
      {isOpen && (
        <div style={{
          position: 'absolute',
          top: '100%',
          left: 0,
          marginTop: '4px',
          background: colors.card,
          border: `1px solid ${colors.borderLight}`,
          borderRadius: '8px',
          boxShadow: `0 8px 24px ${colors.shadowColor}`,
          minWidth: '220px',
          maxHeight: '320px',
          overflowY: 'auto',
          zIndex: 200,
        }}>
          {/* Include remote toggle */}
          <div style={{
            padding: '8px 12px',
            borderBottom: `1px solid ${colors.borderLight}`,
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
          }}>
            <input
              type="checkbox"
              id={`includeRemote-${projectName}`}
              checked={includeRemote}
              onChange={(e) => setIncludeRemote(e.target.checked)}
              style={{ cursor: 'pointer' }}
            />
            <label
              htmlFor={`includeRemote-${projectName}`}
              style={{
                fontSize: '12px',
                color: colors.textSecondary,
                cursor: 'pointer',
              }}
            >
              Show remote branches
            </label>
          </div>

          {/* Branches */}
          {branches.length > 0 && (
            <>
              <div style={{
                padding: '8px 12px 4px',
                fontSize: '10px',
                fontWeight: 600,
                color: colors.textMuted,
                textTransform: 'uppercase',
                letterSpacing: '0.5px',
              }}>
                Branches ({branches.length})
              </div>
              {branches.map((branch) => (
                <button
                  key={branch.name}
                  onClick={() => !branch.is_current && handleCheckoutClick(branch.name)}
                  disabled={branch.is_current}
                  style={{
                    width: '100%',
                    padding: '8px 12px',
                    background: branch.is_current ? colors.accentBg : 'transparent',
                    border: 'none',
                    textAlign: 'left',
                    cursor: branch.is_current ? 'default' : 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    fontSize: '13px',
                    color: branch.is_current ? colors.accent : colors.text,
                    transition: 'background 100ms ease',
                    opacity: branch.is_current ? 0.7 : 1,
                  }}
                  onMouseEnter={(e) => {
                    if (!branch.is_current) {
                      e.currentTarget.style.background = colors.bgHover;
                    }
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = branch.is_current ? colors.accentBg : 'transparent';
                  }}
                >
                  <span style={{
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}>
                    {branch.name}
                    {branch.is_current && ' ✓'}
                  </span>
                  <span style={{
                    fontSize: '10px',
                    color: colors.textMuted,
                    fontFamily: 'var(--font-mono)',
                    flexShrink: 0,
                    marginLeft: '8px',
                  }}>
                    {branch.short_sha}
                  </span>
                </button>
              ))}
            </>
          )}

          {/* Tags */}
          {tags.length > 0 && (
            <>
              <div style={{
                padding: '8px 12px 4px',
                fontSize: '10px',
                fontWeight: 600,
                color: colors.textMuted,
                textTransform: 'uppercase',
                letterSpacing: '0.5px',
                borderTop: branches.length > 0 ? `1px solid ${colors.borderLight}` : 'none',
              }}>
                Tags ({tags.length})
              </div>
              {tags.slice(0, 10).map((tag) => (
                <button
                  key={tag.name}
                  onClick={() => handleCheckoutClick(tag.name)}
                  style={{
                    width: '100%',
                    padding: '8px 12px',
                    background: 'transparent',
                    border: 'none',
                    textAlign: 'left',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    fontSize: '13px',
                    color: colors.text,
                    transition: 'background 100ms ease',
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = colors.bgHover;
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = 'transparent';
                  }}
                >
                  <span>🏷️ {tag.name}</span>
                  <span style={{
                    fontSize: '10px',
                    color: colors.textMuted,
                    fontFamily: 'var(--font-mono)',
                  }}>
                    {tag.short_sha}
                  </span>
                </button>
              ))}
              {tags.length > 10 && (
                <div style={{
                  padding: '8px 12px',
                  fontSize: '12px',
                  color: colors.textMuted,
                  textAlign: 'center',
                }}>
                  +{tags.length - 10} more tags
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* Checkout Confirmation Modal */}
      {showCheckoutConfirm && pendingCheckout && (
        <Modal
          isOpen={true}
          onClose={() => {
            setShowCheckoutConfirm(false);
            setPendingCheckout(null);
          }}
          title="Confirm Git Checkout"
          maxWidth="450px"
        >
          <div>
            <p style={{
              marginBottom: '16px',
              color: colors.textSecondary,
              fontSize: '14px',
            }}>
              Are you sure you want to switch to <strong>{pendingCheckout}</strong>?
            </p>
            <div style={{
              marginBottom: '20px',
              padding: '12px',
              background: colors.warningBg,
              borderRadius: '8px',
              border: `1px solid ${colors.warningBorder}`,
            }}>
              <p style={{
                color: colors.warning,
                fontSize: '13px',
                marginBottom: '8px',
                fontWeight: '500',
              }}>
                ⚠️ This will:
              </p>
              <ul style={{
                color: colors.warning,
                fontSize: '12px',
                marginLeft: '16px',
                listStyle: 'disc',
              }}>
                <li>Switch the Git branch/tag</li>
                <li>Discard any local uncommitted changes</li>
                <li>Update the code in the knowledge graph</li>
                <li>This may take a moment for large repositories</li>
              </ul>
            </div>
            <div style={{
              marginBottom: '20px',
              padding: '12px',
              background: colors.bgTertiary,
              borderRadius: '8px',
              fontSize: '12px',
              color: colors.textSecondary,
            }}>
              <p style={{ margin: 0 }}>
                <strong>Progress tracking:</strong> You'll see detailed progress including:
              </p>
              <ul style={{
                margin: '8px 0 0 16px',
                listStyle: 'circle',
              }}>
                <li>Git operations (checkout, reset)</li>
                <li>Graph updates (adding/removing files)</li>
                <li>Call relationship rebuilding</li>
              </ul>
            </div>
            <div style={{ display: 'flex', gap: '12px', justifyContent: 'flex-end' }}>
              <button
                onClick={() => {
                  setShowCheckoutConfirm(false);
                  setPendingCheckout(null);
                }}
                style={{
                  padding: '10px 20px',
                  background: colors.buttonSecondaryBg,
                  border: 'none',
                  borderRadius: '8px',
                  fontSize: '14px',
                  fontWeight: '500',
                  cursor: 'pointer',
                  color: colors.text,
                }}
              >
                Cancel
              </button>
              <button
                onClick={confirmCheckout}
                style={{
                  padding: '10px 20px',
                  background: colors.buttonPrimaryBg,
                  border: 'none',
                  borderRadius: '8px',
                  fontSize: '14px',
                  fontWeight: '500',
                  cursor: 'pointer',
                  color: '#ffffff',
                }}
              >
                Switch Branch
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* CSS for spin animation */}
      <style jsx global>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}

export default GitBranchSelector;

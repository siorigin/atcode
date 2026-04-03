'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * TaskStatusPanel - Floating button + modal for background task monitoring
 *
 * Displays a small floating button that shows active task count.
 * Clicking opens a modal with detailed task information.
 * Visible to all users in multi-user environments.
 */

import React, { useState, useCallback } from 'react';
import { useTheme } from '@/lib/theme-context';
import type { Theme } from '@/lib/theme-context';
import {
  useGlobalTasks,
  GlobalTask,
  getTaskTypeShortLabel,
} from '@/lib/hooks/useGlobalTasks';
import { TraceViewer } from '@/components/TraceViewer';
import { adaptTaskTrajectory, summarizeTrajectoryDetails } from '@/types/trace';

interface TaskStatusPanelProps {
  /**
   * Position of the floating button.
   * Default: 'bottom-right'
   */
  position?: 'bottom-right' | 'bottom-left' | 'top-right' | 'top-left';

  /**
   * Callback when a task completes.
   */
  onTaskComplete?: (task: GlobalTask) => void;
}

/**
 * Format relative time (e.g., "2 min ago")
 */
function formatRelativeTime(dateStr: string): string {
  try {
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffSec = Math.floor(diffMs / 1000);
    const diffMin = Math.floor(diffSec / 60);
    const diffHr = Math.floor(diffMin / 60);

    if (diffSec < 60) return 'just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHr < 24) return `${diffHr}h ago`;
    return date.toLocaleDateString();
  } catch {
    return '';
  }
}

function getLastTrajectoryTimestamp(task: GlobalTask): string | null {
  const trajectory = task.trajectory || [];
  return trajectory.length > 0 ? trajectory[trajectory.length - 1].timestamp : null;
}

/**
 * Get status color based on task status.
 */
function getStatusStyle(status: GlobalTask['status'], theme: Theme): React.CSSProperties {
  const colors: Record<string, { bg: string; text: string }> = {
    running: { bg: theme === 'dark' ? '#1e3a5f' : '#dbeafe', text: theme === 'dark' ? '#60a5fa' : '#1d4ed8' },
    pending: { bg: theme === 'dark' ? '#422006' : '#fef3c7', text: theme === 'dark' ? '#fbbf24' : '#b45309' },
    stalled: { bg: theme === 'dark' ? '#172554' : '#dbeafe', text: theme === 'dark' ? '#93c5fd' : '#1e40af' },
    completed: { bg: theme === 'dark' ? '#14532d' : '#dcfce7', text: theme === 'dark' ? '#4ade80' : '#15803d' },
    failed: { bg: theme === 'dark' ? '#450a0a' : '#fee2e2', text: theme === 'dark' ? '#f87171' : '#b91c1c' },
    cancelled: { bg: theme === 'dark' ? '#1f2937' : '#f3f4f6', text: theme === 'dark' ? '#9ca3af' : '#6b7280' },
  };
  const style = colors[status] || colors.pending;
  return {
    backgroundColor: style.bg,
    color: style.text,
    padding: '2px 8px',
    borderRadius: '12px',
    fontSize: '11px',
    fontWeight: 500,
  };
}

/**
 * Get progress bar color.
 */
function getProgressColor(status: GlobalTask['status']): string {
  switch (status) {
    case 'running':
      return '#3b82f6';
    case 'pending':
      return '#f59e0b';
    case 'stalled':
      return '#2563eb';
    case 'completed':
      return '#10b981';
    case 'failed':
      return '#ef4444';
    case 'cancelled':
      return '#6b7280';
    default:
      return '#3b82f6';
  }
}

/**
 * Single task item component.
 */
function TaskItem({
  task,
  onCancel,
  isCancelling,
  theme,
}: {
  task: GlobalTask;
  onCancel: (taskId: string) => void;
  isCancelling: boolean;
  theme: Theme;
}) {
  const canCancel = task.status === 'running' || task.status === 'pending' || task.status === 'stalled';
  const isActive = task.status === 'running' || task.status === 'pending' || task.status === 'stalled';
  const [showTrajectory, setShowTrajectory] = useState(isActive);
  const trajectory = task.trajectory || [];
  const lastTrajectoryTimestamp = getLastTrajectoryTimestamp(task);
  const staleLabel = lastTrajectoryTimestamp ? formatRelativeTime(lastTrajectoryTimestamp) : '';

  return (
    <div
      style={{
        padding: '12px 16px',
        borderRadius: '10px',
        border: `1px solid ${theme === 'dark' ? '#374151' : '#e5e7eb'}`,
        backgroundColor: isActive
          ? (theme === 'dark' ? '#1f2937' : '#ffffff')
          : (theme === 'dark' ? '#111827' : '#f9fafb'),
        opacity: isActive ? 1 : 0.7,
        marginBottom: '8px',
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{
            fontSize: '14px',
            fontWeight: 600,
            color: theme === 'dark' ? '#f3f4f6' : '#111827',
          }}>
            {getTaskTypeShortLabel(task.task_type)}
          </span>
          <span style={getStatusStyle(task.status, theme)}>
            {task.status}
          </span>
        </div>
        {canCancel && (
          <button
            onClick={() => onCancel(task.task_id)}
            disabled={isCancelling}
            style={{
              padding: '4px 10px',
              fontSize: '12px',
              fontWeight: 500,
              borderRadius: '6px',
              border: 'none',
              cursor: isCancelling ? 'not-allowed' : 'pointer',
              backgroundColor: isCancelling
                ? (theme === 'dark' ? '#374151' : '#e5e7eb')
                : (theme === 'dark' ? '#7f1d1d' : '#fee2e2'),
              color: isCancelling
                ? (theme === 'dark' ? '#6b7280' : '#9ca3af')
                : (theme === 'dark' ? '#fca5a5' : '#b91c1c'),
              transition: 'all 0.2s',
            }}
          >
            {isCancelling ? 'Cancelling...' : 'Cancel'}
          </button>
        )}
      </div>

      {/* Repository name */}
      {task.repo_name && (
        <div style={{
          fontSize: '13px',
          color: theme === 'dark' ? '#9ca3af' : '#6b7280',
          marginBottom: '8px',
        }}>
          {task.repo_name}
        </div>
      )}

      {/* Progress bar */}
      {isActive && (
        <div style={{ marginBottom: '8px' }}>
          <div style={{
            height: '6px',
            backgroundColor: theme === 'dark' ? '#374151' : '#e5e7eb',
            borderRadius: '3px',
            overflow: 'hidden',
          }}>
            <div
              style={{
                height: '100%',
                backgroundColor: getProgressColor(task.status),
                width: `${task.progress}%`,
                transition: 'width 0.3s ease',
                borderRadius: '3px',
              }}
            />
          </div>
          <div style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginTop: '4px',
          }}>
            <span style={{
              fontSize: '12px',
              color: theme === 'dark' ? '#9ca3af' : '#6b7280',
            }}>
              {task.progress}%
            </span>
            {task.started_at && (
              <span style={{
                fontSize: '11px',
                color: theme === 'dark' ? '#6b7280' : '#9ca3af',
              }}>
                {lastTrajectoryTimestamp
                  ? `updated ${staleLabel}`
                  : formatRelativeTime(task.started_at)}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Status message */}
      {task.status_message && (
        <div style={{
          fontSize: '12px',
          color: theme === 'dark' ? '#9ca3af' : '#6b7280',
          lineHeight: 1.4,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          display: '-webkit-box',
          WebkitLineClamp: 2,
          WebkitBoxOrient: 'vertical',
        }}>
          {task.status_message}
        </div>
      )}

      {isActive && lastTrajectoryTimestamp && staleLabel && (
        <div style={{
          marginTop: '6px',
          fontSize: '11px',
          color: theme === 'dark' ? '#fbbf24' : '#b45309',
        }}>
          Last update {staleLabel}
        </div>
      )}

      {/* Error message */}
      {task.error && (
        <div style={{
          fontSize: '12px',
          color: theme === 'dark' ? '#f87171' : '#b91c1c',
          marginTop: '6px',
          lineHeight: 1.4,
        }}>
          {task.error}
        </div>
      )}

      {trajectory.length > 0 && (
        <div style={{ marginTop: '10px' }}>
          <button
            onClick={() => setShowTrajectory(prev => !prev)}
            style={{
              background: 'transparent',
              border: 'none',
              padding: 0,
              cursor: 'pointer',
              fontSize: '12px',
              fontWeight: 600,
              color: theme === 'dark' ? '#93c5fd' : '#2563eb',
            }}
          >
            {showTrajectory ? 'Hide trajectory' : `Show trajectory (${trajectory.length})`}
          </button>

          {showTrajectory && (
            <div style={{
              marginTop: '10px',
              borderTop: `1px solid ${theme === 'dark' ? '#374151' : '#e5e7eb'}`,
              paddingTop: '10px',
            }}>
              <TraceViewer
                nodes={adaptTaskTrajectory(trajectory, task.task_id)}
                theme={theme}
                maxHeight="400px"
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Main TaskStatusPanel component.
 */
export function TaskStatusPanel({
  position = 'bottom-right',
  onTaskComplete,
}: TaskStatusPanelProps) {
  const { theme } = useTheme();
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [cancellingTasks, setCancellingTasks] = useState<Set<string>>(new Set());

  const {
    activeTasks,
    tasks,
    cancelTask,
    hasActiveTasks,
    activeTaskCount,
    error,
  } = useGlobalTasks({
    pollInterval: 3000,
    stopWhenInactive: true,
    onTaskComplete,
  });

  // Handle cancel
  const handleCancel = useCallback(async (taskId: string) => {
    setCancellingTasks(prev => new Set(prev).add(taskId));
    try {
      await cancelTask(taskId);
    } finally {
      setCancellingTasks(prev => {
        const next = new Set(prev);
        next.delete(taskId);
        return next;
      });
    }
  }, [cancelTask]);

  // Position styles for the floating button
  const positionStyles: Record<string, React.CSSProperties> = {
    'bottom-right': { bottom: '24px', right: '24px' },
    'bottom-left': { bottom: '24px', left: '24px' },
    'top-right': { top: '24px', right: '24px' },
    'top-left': { top: '24px', left: '24px' },
  };

  // Show recently completed/failed/cancelled tasks too
  const recentTasks = tasks.filter(
    t => t.status === 'completed' || t.status === 'failed' || t.status === 'cancelled'
  ).slice(0, 3);

  const displayTasks = [...activeTasks, ...recentTasks];

  // Don't render if no tasks
  if (displayTasks.length === 0) {
    return null;
  }

  return (
    <>
      {/* Floating Button */}
      <button
        onClick={() => setIsModalOpen(true)}
        style={{
          position: 'fixed',
          ...positionStyles[position],
          zIndex: 50,
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: '10px 16px',
          backgroundColor: theme === 'dark' ? '#1f2937' : '#ffffff',
          border: `1px solid ${theme === 'dark' ? '#374151' : '#e5e7eb'}`,
          borderRadius: '50px',
          boxShadow: '0 4px 20px rgba(0, 0, 0, 0.15)',
          cursor: 'pointer',
          transition: 'all 0.2s ease',
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.transform = 'scale(1.05)';
          e.currentTarget.style.boxShadow = '0 6px 24px rgba(0, 0, 0, 0.2)';
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.transform = 'scale(1)';
          e.currentTarget.style.boxShadow = '0 4px 20px rgba(0, 0, 0, 0.15)';
        }}
      >
        {/* Spinning icon for active tasks */}
        {hasActiveTasks ? (
          <div style={{ position: 'relative' }}>
            <svg
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              style={{
                animation: 'spin 1s linear infinite',
              }}
            >
              <circle
                cx="12"
                cy="12"
                r="10"
                stroke={theme === 'dark' ? '#374151' : '#e5e7eb'}
                strokeWidth="3"
              />
              <path
                d="M12 2a10 10 0 0 1 10 10"
                stroke="#3b82f6"
                strokeWidth="3"
                strokeLinecap="round"
              />
            </svg>
          </div>
        ) : (
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke={theme === 'dark' ? '#10b981' : '#059669'}
            strokeWidth="2"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"
            />
          </svg>
        )}

        <span style={{
          fontSize: '14px',
          fontWeight: 500,
          color: theme === 'dark' ? '#f3f4f6' : '#111827',
        }}>
          {hasActiveTasks ? `${activeTaskCount} Running` : 'Tasks'}
        </span>

        {/* Badge for task count */}
        {displayTasks.length > 0 && (
          <span style={{
            minWidth: '20px',
            height: '20px',
            borderRadius: '10px',
            backgroundColor: hasActiveTasks ? '#3b82f6' : (theme === 'dark' ? '#374151' : '#e5e7eb'),
            color: hasActiveTasks ? '#ffffff' : (theme === 'dark' ? '#9ca3af' : '#6b7280'),
            fontSize: '12px',
            fontWeight: 600,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '0 6px',
          }}>
            {displayTasks.length}
          </span>
        )}
      </button>

      {/* Modal */}
      {isModalOpen && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.6)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 100,
            backdropFilter: 'blur(2px)',
          }}
          onClick={() => setIsModalOpen(false)}
        >
          <div
            style={{
              backgroundColor: theme === 'dark' ? '#111827' : '#ffffff',
              borderRadius: '16px',
              padding: '0',
              maxWidth: '500px',
              width: '90%',
              maxHeight: '80vh',
              display: 'flex',
              flexDirection: 'column',
              boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.4)',
              border: `1px solid ${theme === 'dark' ? '#374151' : '#e5e7eb'}`,
              overflow: 'hidden',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              padding: '16px 20px',
              borderBottom: `1px solid ${theme === 'dark' ? '#374151' : '#e5e7eb'}`,
              backgroundColor: theme === 'dark' ? '#1f2937' : '#f9fafb',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <svg
                  width="22"
                  height="22"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke={theme === 'dark' ? '#9ca3af' : '#6b7280'}
                  strokeWidth="2"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"
                  />
                </svg>
                <h2 style={{
                  fontSize: '18px',
                  fontWeight: 600,
                  color: theme === 'dark' ? '#f3f4f6' : '#111827',
                  margin: 0,
                }}>
                  Background Tasks
                </h2>
                {hasActiveTasks && (
                  <span style={{
                    padding: '2px 10px',
                    fontSize: '12px',
                    fontWeight: 600,
                    backgroundColor: '#3b82f6',
                    color: '#ffffff',
                    borderRadius: '12px',
                  }}>
                    {activeTaskCount} active
                  </span>
                )}
              </div>
              <button
                onClick={() => setIsModalOpen(false)}
                style={{
                  background: 'transparent',
                  border: 'none',
                  width: '32px',
                  height: '32px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  cursor: 'pointer',
                  borderRadius: '8px',
                  color: theme === 'dark' ? '#9ca3af' : '#6b7280',
                  transition: 'all 0.2s',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.backgroundColor = theme === 'dark' ? '#374151' : '#e5e7eb';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.backgroundColor = 'transparent';
                }}
              >
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            {/* Error banner */}
            {error && (
              <div style={{
                padding: '10px 20px',
                backgroundColor: theme === 'dark' ? '#450a0a' : '#fee2e2',
                color: theme === 'dark' ? '#fca5a5' : '#b91c1c',
                fontSize: '13px',
              }}>
                {error}
              </div>
            )}

            {/* Task list */}
            <div style={{
              padding: '16px 20px',
              overflowY: 'auto',
              flex: 1,
            }}>
              {displayTasks.length === 0 ? (
                <div style={{
                  textAlign: 'center',
                  padding: '40px 20px',
                  color: theme === 'dark' ? '#6b7280' : '#9ca3af',
                }}>
                  No tasks to display
                </div>
              ) : (
                displayTasks.map(task => (
                  <TaskItem
                    key={task.task_id}
                    task={task}
                    onCancel={handleCancel}
                    isCancelling={cancellingTasks.has(task.task_id)}
                    theme={theme}
                  />
                ))
              )}
            </div>

            {/* Footer */}
            <div style={{
              padding: '12px 20px',
              borderTop: `1px solid ${theme === 'dark' ? '#374151' : '#e5e7eb'}`,
              backgroundColor: theme === 'dark' ? '#1f2937' : '#f9fafb',
              textAlign: 'center',
            }}>
              <p style={{
                fontSize: '12px',
                color: theme === 'dark' ? '#6b7280' : '#9ca3af',
                margin: 0,
              }}>
                Tasks are visible to all users
              </p>
            </div>
          </div>
        </div>
      )}

      {/* CSS for spin animation */}
      <style jsx global>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </>
  );
}

export default TaskStatusPanel;

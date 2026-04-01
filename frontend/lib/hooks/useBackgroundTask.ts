// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Simplified hook for managing background task monitoring.
 * Uses polling to track task progress with retry resilience.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

export interface TaskTrajectoryEntry {
  timestamp: string;
  status: 'pending' | 'running' | 'stalled' | 'completed' | 'failed' | 'cancelled' | string;
  progress: number;
  step: string;
  message: string;
  error?: string | null;
  details?: Record<string, unknown> | null;
}

export interface TaskState {
  task_id: string;
  status: 'pending' | 'running' | 'stalled' | 'completed' | 'failed' | 'cancelled';
  progress: number;
  step: string;
  status_message?: string;
  message?: string;
  error?: string;
  created_at?: string;
  started_at?: string;
  completed_at?: string;
  result?: unknown;
  trajectory?: TaskTrajectoryEntry[];
}

export interface UseBackgroundTaskOptions {
  /**
   * Polling interval in milliseconds.
   * Default: 5000ms (5 seconds) - increased to reduce server load
   */
  pollInterval?: number;

  /**
   * Number of consecutive poll failures before escalating to error.
   * Default: 3
   */
  maxRetries?: number;

  /**
   * Callback when task completes successfully.
   */
  onComplete?: (state: TaskState) => void;

  /**
   * Callback when task fails.
   */
  onError?: (error: string) => void;
}

export interface UseBackgroundTaskReturn {
  // State
  taskId: string | null;
  status: TaskState['status'] | null;
  progress: number;
  message: string;
  trajectory: TaskTrajectoryEntry[];
  lastUpdateAt: string | null;
  error: string | null;
  isMonitoring: boolean;
  isComplete: boolean;
  isError: boolean;

  // Actions
  startMonitoring: (taskId: string, apiBasePath: string) => void;
  stopMonitoring: () => void;
  reset: () => void;
}

/**
 * Hook for monitoring background task execution via polling.
 */
export function useBackgroundTask(
  options: UseBackgroundTaskOptions = {}
): UseBackgroundTaskReturn {
  const { pollInterval = 5000, maxRetries = 3, onComplete, onError } = options;

  // Core state
  const [taskId, setTaskId] = useState<string | null>(null);
  const [status, setStatus] = useState<TaskState['status'] | null>(null);
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState('');
  const [trajectory, setTrajectory] = useState<TaskTrajectoryEntry[]>([]);
  const [lastUpdateAt, setLastUpdateAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isMonitoring, setIsMonitoring] = useState(false);

  // Refs for cleanup
  const pollIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const apiBasePathRef = useRef<string>('');
  const consecutiveFailuresRef = useRef(0);

  // Callbacks stored in refs to avoid dependency issues
  const onCompleteRef = useRef(onComplete);
  const onErrorRef = useRef(onError);
  useEffect(() => {
    onCompleteRef.current = onComplete;
    onErrorRef.current = onError;
  }, [onComplete, onError]);

  /**
   * Stop polling and cleanup.
   */
  const stopMonitoring = useCallback(() => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
    setIsMonitoring(false);
  }, []);

  /**
   * Reset all state.
   */
  const reset = useCallback(() => {
    stopMonitoring();
    setTaskId(null);
    setStatus(null);
    setProgress(0);
    setMessage('');
    setTrajectory([]);
    setLastUpdateAt(null);
    setError(null);
    consecutiveFailuresRef.current = 0;
  }, [stopMonitoring]);

  /**
   * Poll for task status.
   */
  const pollTaskStatus = useCallback(async (tid: string, basePath: string) => {
    try {
      const response = await fetch(`${basePath}/${tid}`);

      if (!response.ok) {
        if (response.status === 404) {
          // Task not yet registered, keep polling
          console.log(`Task ${tid} not yet available, retrying...`);
          return;
        }
        if (response.status === 429) {
          // Rate limited, keep polling (will retry on next interval)
          console.log(`Rate limited, will retry...`);
          return;
        }
        // Transient error (503, 500, etc.) - retry before escalating
        consecutiveFailuresRef.current += 1;
        console.warn(
          `Task ${tid} poll failed (${response.status}), ` +
          `attempt ${consecutiveFailuresRef.current}/${maxRetries}`
        );
        if (consecutiveFailuresRef.current >= maxRetries) {
          throw new Error(`Failed to fetch task status: ${response.status}`);
        }
        return; // Keep polling, don't escalate yet
      }

      // Success - reset failure counter
      consecutiveFailuresRef.current = 0;

      const data: TaskState = await response.json();

      setStatus(data.status);
      setProgress(data.progress || 0);
      setMessage(data.status_message || data.message || data.step || '');
      setTrajectory(data.trajectory || []);
      setLastUpdateAt(
        data.trajectory && data.trajectory.length > 0
          ? data.trajectory[data.trajectory.length - 1].timestamp
          : null
      );

      // Handle terminal states
      if (data.status === 'completed') {
        console.log(`Task ${tid} completed`);
        stopMonitoring();
        onCompleteRef.current?.(data);
      } else if (data.status === 'failed' || data.status === 'stalled') {
        console.error(`Task ${tid} failed:`, data.error);
        stopMonitoring();
        setError(data.error || (data.status === 'stalled' ? 'Task stalled' : 'Task failed'));
        onErrorRef.current?.(data.error || (data.status === 'stalled' ? 'Task stalled' : 'Task failed'));
      } else if (data.status === 'cancelled') {
        console.log(`Task ${tid} cancelled`);
        stopMonitoring();
      }
    } catch (err) {
      consecutiveFailuresRef.current += 1;
      console.error(`Polling error (attempt ${consecutiveFailuresRef.current}/${maxRetries}):`, err);

      if (consecutiveFailuresRef.current >= maxRetries) {
        stopMonitoring();
        const errMsg = err instanceof Error ? err.message : 'Unknown error';
        setError(errMsg);
        onErrorRef.current?.(errMsg);
      }
      // Otherwise: silently continue polling on next interval
    }
  }, [stopMonitoring, maxRetries]);

  /**
   * Start monitoring a task.
   */
  const startMonitoring = useCallback(
    (tid: string, apiBasePath: string) => {
      // Stop any existing monitoring
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
      }

      console.log(`Starting monitoring for task: ${tid}`);

      setTaskId(tid);
      setStatus('pending');
      setProgress(0);
      setMessage('Initializing...');
      setTrajectory([]);
      setLastUpdateAt(null);
      setError(null);
      setIsMonitoring(true);
      apiBasePathRef.current = apiBasePath;
      consecutiveFailuresRef.current = 0;

      // Initial poll
      pollTaskStatus(tid, apiBasePath);

      // Set up polling interval
      pollIntervalRef.current = setInterval(() => {
        pollTaskStatus(tid, apiBasePath);
      }, pollInterval);
    },
    [pollInterval, pollTaskStatus]
  );

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
      }
    };
  }, []);

  return {
    // State
    taskId,
    status,
    progress,
    message,
    trajectory,
    lastUpdateAt,
    error,
    isMonitoring,
    isComplete: status === 'completed',
    isError: status === 'failed' || status === 'stalled',

    // Actions
    startMonitoring,
    stopMonitoring,
    reset,
  };
}

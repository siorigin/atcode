// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * React Hook for Real-time Task Updates via WebSocket
 *
 * Automatically connects to WebSocket on component mount and includes
 * polling fallback for backward compatibility.
 */

'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import { getWebSocketClient, type TaskUpdate } from '../websocket-client';

export interface UseTaskUpdatesOptions {
  taskId?: string;
  pollingIntervalMs?: number; // Polling fallback interval (default: 10000ms)
  onTaskUpdate?: (update: TaskUpdate) => void;
  onConnectionChange?: (connected: boolean) => void;
  usePollingFallback?: boolean; // Enable polling fallback (default: true)
}

/**
 * Hook for real-time task updates
 *
 * Provides:
 * - WebSocket connection for instant updates
 * - Automatic fallback to polling if WebSocket unavailable
 * - Cleanup on unmount
 *
 * @param taskId - Optional task ID to filter updates for
 * @param options - Configuration options
 */
export function useTaskUpdates(taskId?: string, options: UseTaskUpdatesOptions = {}): {
  isConnected: boolean;
  usingWebSocket: boolean;
  usingPolling: boolean;
} {
  const {
    pollingIntervalMs = 10000,
    onTaskUpdate,
    onConnectionChange,
    usePollingFallback = true,
  } = options;

  const [isConnected, setIsConnected] = useState(false);
  const [usingWebSocket, setUsingWebSocket] = useState(false);
  const [usingPolling, setUsingPolling] = useState(false);
  const wsClientRef = useRef(getWebSocketClient());
  const unsubscribeRef = useRef<(() => void)[]>([]);
  const pollingTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const connectionAttemptRef = useRef(false);

  // Connect WebSocket and setup listeners
  useEffect(() => {
    const wsClient = wsClientRef.current;

    // Try to establish WebSocket connection
    if (!connectionAttemptRef.current) {
      connectionAttemptRef.current = true;

      wsClient
        .connect()
        .then(() => {
          setUsingWebSocket(true);
          setIsConnected(true);
          setUsingPolling(false);

          // Subscribe to task updates
          const unsubscribe = wsClient.onTaskUpdate((update) => {
            // If taskId is specified, only process updates for that task
            if (taskId && update.task.task_id !== taskId) {
              return;
            }

            onTaskUpdate?.(update);
          });

          unsubscribeRef.current.push(unsubscribe);

          // Subscribe to connection state changes
          const unsubscribeConnection = wsClient.onConnectionStateChange((connected) => {
            setIsConnected(connected);
            onConnectionChange?.(connected);
          });

          unsubscribeRef.current.push(unsubscribeConnection);
        })
        .catch((error) => {
          console.warn('WebSocket connection failed, falling back to polling:', error);

          if (usePollingFallback) {
            setUsingPolling(true);
            setUsingWebSocket(false);
            setIsConnected(false);
            onConnectionChange?.(false);
          }
        });
    }

    return () => {
      // Cleanup subscriptions
      unsubscribeRef.current.forEach((unsub) => unsub());
      unsubscribeRef.current = [];
    };
  }, [taskId, onTaskUpdate, onConnectionChange, usePollingFallback]);

  // Setup polling fallback if needed
  useEffect(() => {
    if (!usePollingFallback || !usingPolling) {
      return;
    }

    // Only setup polling if we're actually using it
    if (pollingTimeoutRef.current) {
      clearTimeout(pollingTimeoutRef.current);
    }

    const setupPolling = () => {
      if (!taskId) {
        console.warn('useTaskUpdates: taskId is required for polling mode');
        return;
      }

      // Polling will be implemented by the component using this hook
      // This hook just sets the usingPolling flag
    };

    setupPolling();

    return () => {
      if (pollingTimeoutRef.current) {
        clearTimeout(pollingTimeoutRef.current);
      }
    };
  }, [usingPolling, usePollingFallback, taskId]);

  return {
    isConnected,
    usingWebSocket,
    usingPolling,
  };
}

/**
 * Hook for polling task status (used as fallback when WebSocket is unavailable)
 *
 * @param taskId - The task ID to poll for
 * @param pollingInterval - Polling interval in milliseconds
 * @param onUpdate - Callback when task status changes
 */
export function useTaskPolling(
  taskId: string | undefined,
  pollingInterval: number = 5000,
  onUpdate?: (status: Record<string, unknown>) => void
): {
  status: Record<string, unknown> | null;
  loading: boolean;
  error: string | null;
} {
  const [status, setStatus] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollingTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const lastStatusRef = useRef<Record<string, unknown> | null>(null);

  const fetchTaskStatus = useCallback(async () => {
    if (!taskId) return;

    try {
      setLoading(true);
      const response = await fetch(`/api/tasks/${taskId}`);

      if (!response.ok) {
        throw new Error(`Failed to fetch task status: ${response.statusText}`);
      }

      const data = await response.json();

      // Only trigger callback if status changed
      if (JSON.stringify(data) !== JSON.stringify(lastStatusRef.current)) {
        setStatus(data);
        lastStatusRef.current = data;
        onUpdate?.(data);
      }

      setError(null);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Unknown error';
      setError(errorMessage);
      console.error('Failed to poll task status:', errorMessage);
    } finally {
      setLoading(false);
    }
  }, [taskId, onUpdate]);

  useEffect(() => {
    if (!taskId) return;

    // Initial fetch
    fetchTaskStatus();

    // Setup polling
    const setupPolling = () => {
      pollingTimeoutRef.current = setTimeout(() => {
        fetchTaskStatus().finally(setupPolling);
      }, pollingInterval);
    };

    setupPolling();

    return () => {
      if (pollingTimeoutRef.current) {
        clearTimeout(pollingTimeoutRef.current);
      }
    };
  }, [taskId, pollingInterval, fetchTaskStatus]);

  return {
    status,
    loading,
    error,
  };
}

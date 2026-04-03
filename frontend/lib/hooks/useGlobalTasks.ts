// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Global Task Monitoring Hook
 *
 * Provides visibility into all active tasks across the application,
 * supporting multi-user environments where all users need to see
 * ongoing operations like knowledge graph builds or documentation generation.
 *
 * Features:
 * - Real-time WebSocket updates for instant task status changes
 * - Falls back to polling if WebSocket is unavailable
 * - Persists task IDs to localStorage for recovery after page refresh
 * - Provides cancel functionality for running tasks
 * - Auto-cleanup of completed tasks from local storage
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { apiFetch } from '@/lib/api-client';
import { getWebSocketClient, type TaskUpdate } from '@/lib/websocket-client';

export interface GlobalTask {
  task_id: string;
  status: 'pending' | 'running' | 'stalled' | 'completed' | 'failed' | 'cancelled';
  task_type: string;  // 'graph_build' | 'overview_gen' | 'doc_gen' | 'other'
  repo_name: string;
  user_id: string;
  progress: number;
  step: string;
  status_message: string;
  error?: string;
  created_at: string;
  started_at?: string;
  completed_at?: string;
  trajectory?: Array<{
    timestamp: string;
    status: string;
    progress: number;
    step: string;
    message: string;
    error?: string | null;
    details?: Record<string, unknown> | null;
  }>;
}

export interface UseGlobalTasksOptions {
  /**
   * Polling interval in milliseconds.
   * Default: 5000ms (5 seconds) - increased to reduce server load
   */
  pollInterval?: number;

  /**
   * Whether to auto-start polling on mount.
   * Default: true
   */
  autoStart?: boolean;

  /**
   * Whether to stop polling when there are no active tasks.
   * Default: true - helps reduce unnecessary requests
   */
  stopWhenInactive?: boolean;

  /**
   * Filter by task type.
   */
  taskType?: string;

  /**
   * Filter by repository name.
   */
  repoName?: string;

  /**
   * Callback when any task completes.
   */
  onTaskComplete?: (task: GlobalTask) => void;

  /**
   * Callback when any task fails.
   */
  onTaskError?: (task: GlobalTask) => void;
}

export interface UseGlobalTasksReturn {
  // State
  tasks: GlobalTask[];
  activeTasks: GlobalTask[];
  isPolling: boolean;
  lastUpdated: Date | null;
  error: string | null;

  // Actions
  startPolling: () => void;
  stopPolling: () => void;
  refresh: () => Promise<void>;
  cancelTask: (taskId: string) => Promise<boolean>;

  // Helpers
  getTasksByType: (type: string) => GlobalTask[];
  getTasksByRepo: (repoName: string) => GlobalTask[];
  hasActiveTasks: boolean;
  activeTaskCount: number;
}

const STORAGE_KEY = 'atcode_active_task_ids';
const TASK_CREATED_EVENT = 'atcode:task-created';

/**
 * Trigger a task refresh across all useGlobalTasks hooks.
 * Call this after creating a new task to immediately update the UI.
 */
export function triggerTaskRefresh(): void {
  window.dispatchEvent(new CustomEvent(TASK_CREATED_EVENT));
}

/**
 * Hook for monitoring all active tasks globally.
 */
export function useGlobalTasks(
  options: UseGlobalTasksOptions = {}
): UseGlobalTasksReturn {
  const {
    pollInterval = 3000, // Reduced from 5000 to 3000ms for faster status updates
    autoStart = true,
    stopWhenInactive = true,
    taskType,
    repoName,
    onTaskComplete,
    onTaskError,
  } = options;

  // State
  const [tasks, setTasks] = useState<GlobalTask[]>([]);
  const [isPolling, setIsPolling] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isWebSocketConnected, setIsWebSocketConnected] = useState(false);

  // Refs for cleanup and callbacks
  const pollIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const onTaskCompleteRef = useRef(onTaskComplete);
  const onTaskErrorRef = useRef(onTaskError);
  const previousTasksRef = useRef<Map<string, GlobalTask>>(new Map());
  const processedCompletedTasksRef = useRef<Set<string>>(new Set()); // Track completed tasks we've already processed
  const wsUnsubscribeRef = useRef<(() => void) | null>(null);
  const eventsEndpointAvailableRef = useRef(true);
  // Initialize with actual value from options, not default
  const stopWhenInactiveRef = useRef(options.stopWhenInactive ?? true);

  // Update callback refs
  useEffect(() => {
    onTaskCompleteRef.current = onTaskComplete;
    onTaskErrorRef.current = onTaskError;
    stopWhenInactiveRef.current = stopWhenInactive;
  }, [onTaskComplete, onTaskError, stopWhenInactive]);

  /**
   * Fetch active tasks from the API.
   */
  const fetchTasks = useCallback(async () => {
    try {
      // Build query params
      const params = new URLSearchParams();
      params.set('include_completed', 'true');
      params.set('recent_minutes', '5');
      if (taskType) params.set('task_type', taskType);
      if (repoName) params.set('repo_name', repoName);

      const response = await apiFetch(`/api/tasks/active?${params.toString()}`);

      if (!response.ok) {
        throw new Error(`Failed to fetch tasks: ${response.status}`);
      }

      const data = await response.json();
      const newTasks: GlobalTask[] = data.tasks || [];

      // Check for state changes and trigger callbacks
      const previousTasks = previousTasksRef.current;
      const processedCompleted = processedCompletedTasksRef.current;

      for (const task of newTasks) {
        const prevTask = previousTasks.get(task.task_id);

        if (prevTask) {
          // Task existed before - check for status changes
          if (prevTask.status !== task.status) {
            if (task.status === 'completed') {
              processedCompleted.add(task.task_id);
              onTaskCompleteRef.current?.(task);
            } else if (task.status === 'failed') {
              processedCompleted.add(task.task_id);
              onTaskErrorRef.current?.(task);
            }
          }
        } else {
          // Newly seen task - check if it's already completed/failed
          // This handles fast tasks that complete between polls
          if (task.status === 'completed' && !processedCompleted.has(task.task_id)) {
            processedCompleted.add(task.task_id);
            onTaskCompleteRef.current?.(task);
          } else if (task.status === 'failed' && !processedCompleted.has(task.task_id)) {
            processedCompleted.add(task.task_id);
            onTaskErrorRef.current?.(task);
          }
        }
      }

      // Clean up old processed task IDs (keep only those still in the task list)
      const currentTaskIds = new Set(newTasks.map(t => t.task_id));
      for (const taskId of processedCompleted) {
        if (!currentTaskIds.has(taskId)) {
          processedCompleted.delete(taskId);
        }
      }

      // Update previous tasks reference
      previousTasksRef.current = new Map(
        newTasks.map(t => [t.task_id, t])
      );

      setTasks(newTasks);
      setLastUpdated(new Date());
      setError(null);

      // Update localStorage with active task IDs
      const activeIds = newTasks
        .filter(t => t.status === 'running' || t.status === 'pending' || t.status === 'stalled')
        .map(t => t.task_id);

      if (activeIds.length > 0) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(activeIds));
      } else {
        localStorage.removeItem(STORAGE_KEY);
        // If stopWhenInactive is enabled and no active tasks, stop polling
        if (stopWhenInactiveRef.current && pollIntervalRef.current) {
          if (pollIntervalRef.current) {
            clearInterval(pollIntervalRef.current);
            pollIntervalRef.current = null;
          }
          setIsPolling(false);
        }
      }

    } catch (err) {
      const errMsg = err instanceof Error ? err.message : 'Unknown error';
      console.error('Failed to fetch global tasks:', errMsg);
      setError(errMsg);
    }
  }, [taskType, repoName]);

  /**
   * Start polling for tasks.
   */
  const startPolling = useCallback(() => {
    if (pollIntervalRef.current) {
      return; // Already polling
    }

    setIsPolling(true);

    // Initial fetch
    fetchTasks();

    // Set up polling interval
    pollIntervalRef.current = setInterval(fetchTasks, pollInterval);
  }, [fetchTasks, pollInterval]);

  /**
   * Stop polling.
   */
  const stopPolling = useCallback(() => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
    setIsPolling(false);
  }, []);

  /**
   * Manual refresh.
   */
  const refresh = useCallback(async () => {
    await fetchTasks();
  }, [fetchTasks]);

  /**
   * Cancel a running task.
   */
  const cancelTask = useCallback(async (taskId: string): Promise<boolean> => {
    try {
      const response = await apiFetch(`/api/tasks/${taskId}/cancel`, {
        method: 'POST',
      });

      if (!response.ok) {
        throw new Error(`Failed to cancel task: ${response.status}`);
      }

      const data = await response.json();

      // Refresh tasks to get updated status
      await fetchTasks();

      return data.success;
    } catch (err) {
      console.error('Failed to cancel task:', err);
      return false;
    }
  }, [fetchTasks]);

  /**
   * Handle WebSocket task update - immediately update task state when receiving WebSocket message.
   */
  const handleWebSocketUpdate = useCallback((update: TaskUpdate) => {
    const updatedTask = update.task as GlobalTask;

    console.log('WebSocket task update received:', updatedTask.task_id, updatedTask.status);

    // Determine callbacks BEFORE setTasks to avoid calling setState
    // in another component (ToastProvider) during this component's render
    const previousTasks = previousTasksRef.current;
    const processedCompleted = processedCompletedTasksRef.current;
    const prevTask = previousTasks.get(updatedTask.task_id);

    let shouldCallComplete = false;
    let shouldCallError = false;

    if (prevTask && prevTask.status !== updatedTask.status) {
      if (updatedTask.status === 'completed') {
        shouldCallComplete = true;
      } else if (updatedTask.status === 'failed') {
        shouldCallError = true;
      }
    } else if (!prevTask) {
      if (updatedTask.status === 'completed' && !processedCompleted.has(updatedTask.task_id)) {
        shouldCallComplete = true;
      } else if (updatedTask.status === 'failed' && !processedCompleted.has(updatedTask.task_id)) {
        shouldCallError = true;
      }
    }

    // Update task state (no external callbacks inside the updater)
    setTasks(prevTasks => {
      const taskIndex = prevTasks.findIndex(t => t.task_id === updatedTask.task_id);
      let newTasks: GlobalTask[];

      if (taskIndex >= 0) {
        newTasks = [...prevTasks];
        newTasks[taskIndex] = updatedTask;
      } else {
        if (taskType && updatedTask.task_type !== taskType) {
          return prevTasks;
        }
        if (repoName && updatedTask.repo_name !== repoName) {
          return prevTasks;
        }
        newTasks = [...prevTasks, updatedTask];
      }

      previousTasksRef.current = new Map(
        newTasks.map(t => [t.task_id, t])
      );

      const activeIds = newTasks
        .filter(t => t.status === 'running' || t.status === 'pending' || t.status === 'stalled')
        .map(t => t.task_id);

      if (activeIds.length > 0) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(activeIds));
      } else {
        localStorage.removeItem(STORAGE_KEY);
      }

      setLastUpdated(new Date());
      return newTasks;
    });

    // Fire callbacks OUTSIDE of setTasks updater to avoid
    // "Cannot update a component while rendering a different component"
    if (shouldCallComplete) {
      processedCompleted.add(updatedTask.task_id);
      onTaskCompleteRef.current?.(updatedTask);
    } else if (shouldCallError) {
      processedCompleted.add(updatedTask.task_id);
      onTaskErrorRef.current?.(updatedTask);
    }
  }, [taskType, repoName]);

  /**
   * Get tasks filtered by type.
   */
  const getTasksByType = useCallback((type: string): GlobalTask[] => {
    return tasks.filter(t => t.task_type === type);
  }, [tasks]);

  /**
   * Get tasks filtered by repository.
   */
  const getTasksByRepo = useCallback((repo: string): GlobalTask[] => {
    return tasks.filter(t => t.repo_name === repo);
  }, [tasks]);

  // Computed values
  const activeTasks = tasks.filter(
    t => t.status === 'running' || t.status === 'pending' || t.status === 'stalled'
  );
  const hasActiveTasks = activeTasks.length > 0;
  const activeTaskCount = activeTasks.length;

  // Auto-start polling if enabled
  useEffect(() => {
    if (autoStart) {
      startPolling();
    }

    return () => {
      stopPolling();
    };
  }, [autoStart, startPolling, stopPolling]);

  // Restore task monitoring from localStorage on mount
  useEffect(() => {
    const storedIds = localStorage.getItem(STORAGE_KEY);
    if (storedIds) {
      try {
        const taskIds = JSON.parse(storedIds);
        if (Array.isArray(taskIds) && taskIds.length > 0) {
          console.log('Restoring task monitoring for:', taskIds);
          // The polling will pick up these tasks automatically
        }
      } catch (e) {
        console.error('Failed to parse stored task IDs:', e);
        localStorage.removeItem(STORAGE_KEY);
      }
    }
  }, []);

  // Listen for task-created events to immediately refresh
  useEffect(() => {
    const handleTaskCreated = () => {
      console.log('Task created event received, refreshing and starting polling...');
      // Restart polling if it was stopped
      if (!pollIntervalRef.current) {
        startPolling();
      }
      // Fetch immediately, then once more after a short delay to catch newly created tasks
      fetchTasks();
      setTimeout(() => {
        fetchTasks();
      }, 1000);
    };

    window.addEventListener(TASK_CREATED_EVENT, handleTaskCreated);
    return () => {
      window.removeEventListener(TASK_CREATED_EVENT, handleTaskCreated);
    };
  }, [startPolling, fetchTasks]);

  // Setup WebSocket connection for real-time task updates
  useEffect(() => {
    const wsClient = getWebSocketClient();

    wsClient.connect()
      .then(() => {
        console.log('WebSocket connected for global task monitoring');
        setIsWebSocketConnected(true);

        // Subscribe to all task updates (no taskId filter)
        const unsubscribe = wsClient.onTaskUpdate(handleWebSocketUpdate);
        wsUnsubscribeRef.current = unsubscribe;
      })
      .catch((err) => {
        console.log('WebSocket connection failed, will use polling:', err);
        setIsWebSocketConnected(false);
      });

    // Monitor connection state — restart polling when WS disconnects
    const unsubConn = wsClient.onConnectionStateChange((connected) => {
      setIsWebSocketConnected(connected);
      if (!connected && !pollIntervalRef.current) {
        startPolling();
      }
    });

    return () => {
      unsubConn();
      if (wsUnsubscribeRef.current) {
        wsUnsubscribeRef.current();
        wsUnsubscribeRef.current = null;
      }
    };
  }, [handleWebSocketUpdate, startPolling]);

  // Event compensation mechanism
  // Periodically fetch missed events from Redis Stream for reliability
  // This ensures we don't miss any updates even if WebSocket disconnects
  useEffect(() => {
    // Skip if WebSocket is connected (we get updates in real-time)
    if (isWebSocketConnected || !eventsEndpointAvailableRef.current) {
      return;
    }

    const COMPENSATION_INTERVAL = 30000; // 30 seconds
    let lastEventId = '0';

    const fetchEvents = async () => {
      try {
        const params = new URLSearchParams({
          since: lastEventId,
          limit: '100'
        });

        const response = await apiFetch(`/api/tasks/events?${params.toString()}`);

        if (!response.ok) {
          if (response.status === 404) {
            eventsEndpointAvailableRef.current = false;
          }
          return; // Events endpoint not available (might be using filesystem store)
        }

        const data = await response.json();

        if (data.events && data.events.length > 0) {
          console.log(`Fetched ${data.events.length} task events for compensation`);

          // Apply each event to update task state
          setTasks(prevTasks => {
            let updatedTasks = [...prevTasks];
            const taskMap = new Map(updatedTasks.map(t => [t.task_id, t]));

            for (const event of data.events) {
              const task = event.task as GlobalTask;
              // Apply filters
              if (taskType && task.task_type !== taskType) continue;
              if (repoName && task.repo_name !== repoName) continue;

              taskMap.set(task.task_id, task);
            }

            updatedTasks = Array.from(taskMap.values());

            // Update previous tasks reference
            previousTasksRef.current = new Map(
              updatedTasks.map(t => [t.task_id, t])
            );

            // Update localStorage
            const activeIds = updatedTasks
              .filter(t => t.status === 'running' || t.status === 'pending' || t.status === 'stalled')
              .map(t => t.task_id);

            if (activeIds.length > 0) {
              localStorage.setItem(STORAGE_KEY, JSON.stringify(activeIds));
            } else {
              localStorage.removeItem(STORAGE_KEY);
            }

            return updatedTasks;
          });

          // Update last event ID for next request
          lastEventId = data.last_id;
          setLastUpdated(new Date());
        }
      } catch (err) {
        console.debug('Event compensation fetch failed (expected if using filesystem store):', err);
      }
    };

    // Initial fetch
    fetchEvents();

    // Set up interval
    const interval = setInterval(fetchEvents, COMPENSATION_INTERVAL);

    return () => clearInterval(interval);
  }, [isWebSocketConnected, taskType, repoName]);

  return {
    // State
    tasks,
    activeTasks,
    isPolling,
    lastUpdated,
    error,

    // Actions
    startPolling,
    stopPolling,
    refresh,
    cancelTask,

    // Helpers
    getTasksByType,
    getTasksByRepo,
    hasActiveTasks,
    activeTaskCount,
  };
}

/**
 * Get a human-readable label for a task type.
 */
export function getTaskTypeLabel(taskType: string): string {
  const labels: Record<string, string> = {
    graph_build: 'Knowledge Graph Build',
    overview_gen: 'Documentation Generation',
    doc_gen: 'Document Generation',
    paper_read: 'Paper Reading Pipeline',
    other: 'Background Task',
  };
  return labels[taskType] || taskType;
}

/**
 * Get a short label for a task type (for compact displays).
 */
export function getTaskTypeShortLabel(taskType: string): string {
  const labels: Record<string, string> = {
    graph_build: 'Graph',
    overview_gen: 'Docs',
    doc_gen: 'Doc',
    paper_read: 'Paper',
    other: 'Task',
  };
  return labels[taskType] || 'Task';
}

/**
 * Get an icon/emoji for a task type.
 */
export function getTaskTypeIcon(taskType: string): string {
  const icons: Record<string, string> = {
    graph_build: '🔗',
    overview_gen: '📚',
    doc_gen: '📄',
    paper_read: '📖',
    other: '⚙️',
  };
  return icons[taskType] || '⚙️';
}

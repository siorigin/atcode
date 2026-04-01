'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * useSyncStatus - Real-time sync status hook with WebSocket support.
 *
 * Features:
 * - Subscribes to WebSocket sync_* messages for real-time updates
 * - Falls back to polling (10s) when WebSocket disconnects
 * - Provides pending files, current processing file, and history
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  SyncStatus,
  PendingFile,
  SyncHistoryItem,
  getSyncStatus,
  getPendingFiles,
  getSyncHistory,
} from '@/lib/sync-api';

export interface UseSyncStatusReturn {
  status: SyncStatus | null;
  pendingFiles: PendingFile[];
  currentFile: string | null;
  history: SyncHistoryItem[];
  isConnected: boolean;
  refresh: () => Promise<void>;
}

export function useSyncStatus(projectName: string): UseSyncStatusReturn {
  const [status, setStatus] = useState<SyncStatus | null>(null);
  const [pendingFiles, setPendingFiles] = useState<PendingFile[]>([]);
  const [currentFile, setCurrentFile] = useState<string | null>(null);
  const [history, setHistory] = useState<SyncHistoryItem[]>([]);
  const [isConnected, setIsConnected] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const pollingRef = useRef<NodeJS.Timeout | null>(null);
  const reconnectRef = useRef<NodeJS.Timeout | null>(null);
  const mountedRef = useRef(true);
  const reconnectAttempts = useRef(0);
  const MAX_RECONNECT_ATTEMPTS = 5;

  // Fetch full status from REST API
  const fetchStatus = useCallback(async () => {
    if (!mountedRef.current) return;
    try {
      const s = await getSyncStatus(projectName);
      if (mountedRef.current) setStatus(s);
    } catch {
      // Ignore - sync manager may not be initialized
    }
  }, [projectName]);

  // Fetch pending files
  const fetchPending = useCallback(async () => {
    if (!mountedRef.current) return;
    try {
      const files = await getPendingFiles(projectName);
      if (mountedRef.current) setPendingFiles(files);
    } catch {
      // Ignore
    }
  }, [projectName]);

  // Fetch history
  const fetchHistory = useCallback(async () => {
    if (!mountedRef.current) return;
    try {
      const h = await getSyncHistory(projectName, 20);
      if (mountedRef.current) setHistory(h);
    } catch {
      // Ignore
    }
  }, [projectName]);

  // Full refresh
  const refresh = useCallback(async () => {
    await Promise.all([fetchStatus(), fetchPending(), fetchHistory()]);
  }, [fetchStatus, fetchPending, fetchHistory]);

  // Start polling fallback
  const startPolling = useCallback(() => {
    if (pollingRef.current) return;
    pollingRef.current = setInterval(() => {
      if (mountedRef.current) {
        fetchStatus();
      }
    }, 10000);
  }, [fetchStatus]);

  const stopPolling = useCallback(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }, []);

  // Build WebSocket URL — use same-origin rewrite proxy (/ws-proxy/...)
  const getWsUrl = useCallback(() => {
    if (typeof window === 'undefined') return null;

    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${wsProtocol}//${window.location.host}/ws-proxy/api/sync/${encodeURIComponent(projectName)}/ws`;
  }, [projectName]);

  // Connect WebSocket
  const connectWs = useCallback(() => {
    const url = getWsUrl();
    if (!url || !mountedRef.current) return;

    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!mountedRef.current) { ws.close(); return; }
        setIsConnected(true);
        reconnectAttempts.current = 0;
        stopPolling();
      };

      ws.onmessage = (event) => {
        if (!mountedRef.current) return;
        try {
          const msg = JSON.parse(event.data);

          if (msg.type === 'sync_status' && msg.data) {
            // Update status from WebSocket data
            const d = msg.data;
            setStatus({
              is_watching: d.is_watching,
              is_processing: d.is_processing,
              is_git_repo: d.is_git_repo,
              current_ref: d.current_ref,
              current_ref_type: d.current_ref_type,
              pending_changes: d.pending_changes,
              latest_result: d.latest_result,
              built_commit_sha: d.built_commit_sha ?? null,
            });
            setCurrentFile(d.current_file || null);
          } else if (msg.type === 'sync_progress' && msg.data) {
            setCurrentFile(msg.data.current_file || null);
          } else if (msg.type === 'sync_complete' && msg.data) {
            setCurrentFile(null);
            // Refresh history and pending after completion
            fetchHistory();
            fetchPending();
            fetchStatus();
          }
        } catch {
          // Ignore parse errors
        }
      };

      ws.onclose = () => {
        if (!mountedRef.current) return;
        setIsConnected(false);
        wsRef.current = null;

        // Start polling fallback
        startPolling();

        // Attempt reconnect
        if (reconnectAttempts.current < MAX_RECONNECT_ATTEMPTS) {
          const delay = Math.min(1000 * Math.pow(2, reconnectAttempts.current), 30000);
          reconnectAttempts.current++;
          reconnectRef.current = setTimeout(() => {
            if (mountedRef.current) connectWs();
          }, delay);
        }
      };

      ws.onerror = () => {
        // onclose will handle cleanup
      };
    } catch {
      startPolling();
    }
  }, [getWsUrl, stopPolling, startPolling, fetchHistory, fetchPending, fetchStatus]);

  // Initial load + WebSocket connect
  useEffect(() => {
    mountedRef.current = true;
    reconnectAttempts.current = 0;

    // Fetch initial data
    refresh();

    // Connect WebSocket
    connectWs();

    return () => {
      mountedRef.current = false;
      stopPolling();

      if (reconnectRef.current) {
        clearTimeout(reconnectRef.current);
        reconnectRef.current = null;
      }

      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [projectName]); // eslint-disable-line react-hooks/exhaustive-deps

  return {
    status,
    pendingFiles,
    currentFile,
    history,
    isConnected,
    refresh,
  };
}

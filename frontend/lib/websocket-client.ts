// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * WebSocket Client for Real-time Task Updates
 *
 * Provides WebSocket connection management and event handling for task status updates.
 * Includes automatic fallback to polling if WebSocket is unavailable.
 */

// Configuration
//
// Browser-side WebSocket connections go through the Next.js same-origin
// rewrite proxy (/ws-proxy/...) so that SSH port-forward and reverse-proxy
// deployments work without exposing the backend port to the browser.
const WEBSOCKET_URL = (() => {
  if (typeof window === 'undefined') return null;
  const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${wsProtocol}//${window.location.host}/ws-proxy/api/tasks/ws`;
})();

export interface TaskUpdate {
  type: 'task_update';
  task: {
    task_id: string;
    status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
    task_type: string;
    repo_name: string;
    user_id: string;
    progress: number;
    step: string;
    status_message: string;
    error?: string;
    created_at: string;
    started_at?: string;
    completed_at?: string;
    queue_position: number;
    remote_host: string;
    trajectory?: Array<{
      timestamp: string;
      status: string;
      progress: number;
      step: string;
      message: string;
      error?: string | null;
      details?: Record<string, unknown> | null;
    }>;
  };
}

export interface WebSocketMessage {
  type: string;
  message?: string;
  task?: Record<string, unknown>;
}

export type TaskUpdateCallback = (update: TaskUpdate) => void;
export type ConnectionStateCallback = (connected: boolean) => void;

/**
 * WebSocket client for real-time task updates
 */
export class TaskWebSocketClient {
  private websocket: WebSocket | null = null;
  private taskUpdateListeners: Set<TaskUpdateCallback> = new Set();
  private connectionStateListeners: Set<ConnectionStateCallback> = new Set();
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 20;
  private reconnectDelay = 1000;
  private reconnectTimeout: NodeJS.Timeout | null = null;
  private isManualClose = false;

  /**
   * Connect to the WebSocket server
   */
  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      if (!WEBSOCKET_URL) {
        console.warn('WebSocket URL not available');
        reject(new Error('WebSocket URL not available'));
        return;
      }

      try {
        this.websocket = new WebSocket(WEBSOCKET_URL);

        this.websocket.onopen = () => {
          console.log('WebSocket connected');
          this.reconnectAttempts = 0;
          this.reconnectDelay = 1000;

          // Send subscribe message - use setTimeout to ensure WebSocket is fully open
          // This fixes a race condition where onopen fires before readyState is OPEN
          setTimeout(() => {
            if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
              try {
                this.websocket.send(JSON.stringify({ action: 'subscribe' }));
              } catch (error) {
                console.error('Failed to send subscribe message:', error);
              }
            }
          }, 0);

          // Notify listeners
          this.connectionStateListeners.forEach((cb) => cb(true));
          resolve();
        };

        this.websocket.onmessage = (event) => {
          this.handleMessage(event.data);
        };

        this.websocket.onerror = (error) => {
          console.error('WebSocket error:', error);
          reject(error);
        };

        this.websocket.onclose = () => {
          console.log('WebSocket closed');
          this.websocket = null;

          // Notify listeners
          this.connectionStateListeners.forEach((cb) => cb(false));

          // Attempt reconnect if not manually closed
          if (!this.isManualClose) {
            this.attemptReconnect();
          }
        };
      } catch (error) {
        console.error('Failed to create WebSocket:', error);
        reject(error);
      }
    });
  }

  /**
   * Disconnect from the WebSocket server
   */
  disconnect(): void {
    this.isManualClose = true;

    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }

    if (this.websocket) {
      this.websocket.close();
      this.websocket = null;
    }
  }

  /**
   * Attempt to reconnect after a delay
   */
  private attemptReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.warn('Max reconnection attempts reached, giving up');
      return;
    }

    this.reconnectAttempts++;
    const delay = Math.min(this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1), 30000);

    console.log(
      `Attempting to reconnect... (attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts}, delay: ${delay}ms)`
    );

    this.reconnectTimeout = setTimeout(() => {
      this.isManualClose = false;
      this.connect().catch((error) => {
        console.error('Reconnection failed:', error);
      });
    }, delay);
  }

  /**
   * Handle incoming WebSocket message
   */
  private handleMessage(data: string): void {
    try {
      const message = JSON.parse(data) as WebSocketMessage;

      if (message.type === 'task_update') {
        // Notify all listeners of task update
        this.taskUpdateListeners.forEach((cb) => {
          cb(message as TaskUpdate);
        });
      } else if (message.type === 'connected') {
        console.log('WebSocket subscription confirmed:', message.message);
      }
    } catch (error) {
      console.error('Failed to parse WebSocket message:', error, data);
    }
  }

  /**
   * Subscribe to task updates
   */
  onTaskUpdate(callback: TaskUpdateCallback): () => void {
    this.taskUpdateListeners.add(callback);

    // Return unsubscribe function
    return () => {
      this.taskUpdateListeners.delete(callback);
    };
  }

  /**
   * Subscribe to connection state changes
   */
  onConnectionStateChange(callback: ConnectionStateCallback): () => void {
    this.connectionStateListeners.add(callback);

    // Return unsubscribe function
    return () => {
      this.connectionStateListeners.delete(callback);
    };
  }

  /**
   * Check if WebSocket is connected
   */
  isConnected(): boolean {
    return this.websocket?.readyState === WebSocket.OPEN;
  }

  /**
   * Get the current number of task update listeners
   */
  getTaskUpdateListenerCount(): number {
    return this.taskUpdateListeners.size;
  }
}

// Global WebSocket client instance
let globalWebSocketClient: TaskWebSocketClient | null = null;

/**
 * Get or create the global WebSocket client
 */
export function getWebSocketClient(): TaskWebSocketClient {
  if (!globalWebSocketClient) {
    globalWebSocketClient = new TaskWebSocketClient();
  }
  return globalWebSocketClient;
}

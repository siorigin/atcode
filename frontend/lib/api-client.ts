// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * AtCode API Client
 *
 * Browser-side requests go through the Next.js same-origin proxy to avoid
 * host/CORS issues in SSH port-forward and reverse-proxy deployments.
 * Server-side requests still connect to FastAPI directly.
 */

import { getFastAPIUrl } from './api-config';

// Configuration - use centralized config
const AUTH_TOKEN_KEY = 'atcode-auth-token';
const BROWSER_PROXY_BASE = '/api/proxy';

// Browser-side requests should use the frontend's same-origin proxy.
// Server-side code can still talk to FastAPI directly.
function getApiUrl(): string {
  if (typeof window !== 'undefined') {
    return BROWSER_PROXY_BASE;
  }
  return getFastAPIUrl();
}

// Types
export interface DocumentContext {
  filePath?: string;
  selectedText?: string;
}

export interface ChatRequest {
  repo: string;
  message: string;
  sessionId: string;
  mode?: 'quick' | 'default' | 'detailed';
  maxToolCalls?: number;
  preload?: boolean;
  documentContext?: DocumentContext;
}

export interface ChatEvent {
  type: 'tool_call' | 'tool_result' | 'response' | 'complete' | 'error' | 'thinking' | 'status';
  content: string;
  metadata?: Record<string, unknown>;
  timestamp?: string;
}

export interface SessionMetadata {
  id: string;
  userId: string;
  repoName: string;
  createdAt: string;
  updatedAt: string;
  turnsCount: number;
  firstQuery?: string;
  lastQuery?: string;
}

export interface SessionDetail extends SessionMetadata {
  turns: Array<{
    query: string;
    response: string;
    timestamp: string;
    references?: unknown[];
  }>;
  metadata?: Record<string, unknown>;
}

export interface SessionsResponse {
  sessions: SessionMetadata[];
  total: number;
  limit: number;
  offset: number;
}

export interface HealthResponse {
  status: 'healthy' | 'degraded' | 'unhealthy';
  version: string;
  uptimeSeconds: number;
  components: Record<string, string>;
  timestamp: string;
}

export interface PoolStats {
  size: number;
  maxSize: number;
  totalRequests: number;
  cacheHits: number;
  cacheMisses: number;
  hitRate: number;
  orchestrators: Array<{
    key: string;
    repoName: string;
    mode: string;
    createdAt: string;
    lastAccess: string;
    accessCount: number;
    idleSeconds: number;
  }>;
}

/**
 * Get auth token from cookie or localStorage
 */
function getAuthToken(): string | null {
  if (typeof window === 'undefined') {
    return null;
  }

  // Try cookie first
  const cookies = document.cookie.split(';');
  for (const cookie of cookies) {
    const [name, value] = cookie.trim().split('=');
    if (name === AUTH_TOKEN_KEY && value) {
      return decodeURIComponent(value);
    }
  }

  // Fallback to localStorage
  try {
    return localStorage.getItem(AUTH_TOKEN_KEY);
  } catch {
    return null;
  }
}

/**
 * Get the FastAPI base URL
 */
export function getFastApiUrl(): string {
  return getApiUrl();
}

/**
 * Make API request to FastAPI backend
 * This is the primary function for all API calls.
 */
export async function apiFetch(
  path: string,
  options: RequestInit = {}
): Promise<Response> {
  const token = getAuthToken();

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> || {}),
  };

  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const url = `${getApiUrl()}${path}`;

  return fetch(url, {
    ...options,
    headers,
    credentials: 'include',
  });
}

/**
 * Stream fetch with SSE parsing
 * Used for chat and other streaming endpoints
 */
export async function* streamFetch<T>(
  path: string,
  body: unknown
): AsyncGenerator<T> {
  const response = await apiFetch(path, {
    method: 'POST',
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ error: response.statusText }));
    throw new Error(error.error || error.detail || `Request failed: ${response.statusText}`);
  }

  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error('No response body');
  }

  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6);
          if (data === '[DONE]') continue;

          try {
            const event = JSON.parse(data) as T;
            yield event;
          } catch (e) {
            console.error('Failed to parse SSE event:', e);
          }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/**
 * AtCode Chat API Client
 */
export class ChatAPIClient {
  private timeout: number;

  constructor(config?: { timeout?: number }) {
    this.timeout = config?.timeout || 60000;
  }

  private getEndpoint(path: string): string {
    return `${getApiUrl()}${path}`;
  }

  private getHeaders(): Record<string, string> {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };

    const token = getAuthToken();
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    return headers;
  }

  private async fetchWithConfig(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<Response> {
    return fetch(endpoint, {
      ...options,
      headers: {
        ...this.getHeaders(),
        ...(options.headers as Record<string, string> || {}),
      },
      credentials: 'include',
    });
  }

  /**
   * Stream chat responses
   */
  async *streamChat(request: ChatRequest): AsyncGenerator<ChatEvent> {
    const endpoint = this.getEndpoint('/api/chat/stream');

    const response = await this.fetchWithConfig(endpoint, {
      method: 'POST',
      body: JSON.stringify({
        repo: request.repo,
        message: request.message,
        sessionId: request.sessionId,
        mode: request.mode || 'default',
        maxToolCalls: request.maxToolCalls,
        preload: request.preload,
        documentContext: request.documentContext,
      }),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ error: response.statusText }));
      throw new Error(error.error || error.detail || `Chat request failed: ${response.statusText}`);
    }

    const reader = response.body?.getReader();
    if (!reader) {
      throw new Error('No response body');
    }

    const decoder = new TextDecoder();
    let buffer = '';

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6);
            if (data === '[DONE]') continue;

            try {
              const event = JSON.parse(data) as ChatEvent;
              yield event;
            } catch (e) {
              console.error('Failed to parse event:', e);
            }
          }
        }
      }
    } finally {
      reader.releaseLock();
    }
  }

  /**
   * Send chat message and get complete response (non-streaming)
   */
  async chat(request: ChatRequest): Promise<ChatEvent> {
    const endpoint = this.getEndpoint('/api/chat');
    const response = await this.fetchWithConfig(endpoint, {
      method: 'POST',
      body: JSON.stringify(request),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ error: response.statusText }));
      throw new Error(error.error || `Chat request failed: ${response.statusText}`);
    }

    return response.json();
  }

  /**
   * List sessions for current user
   */
  async listSessions(repo?: string, limit = 100, offset = 0): Promise<SessionsResponse> {
    const params = new URLSearchParams();
    if (repo) params.set('repo', repo);
    params.set('limit', String(limit));
    params.set('offset', String(offset));

    const endpoint = this.getEndpoint(`/api/sessions?${params.toString()}`);
    const response = await this.fetchWithConfig(endpoint);

    if (!response.ok) {
      throw new Error('Failed to load sessions');
    }

    return response.json();
  }

  /**
   * Get session details
   */
  async getSession(repo: string, sessionId: string): Promise<SessionDetail> {
    const endpoint = this.getEndpoint(`/api/sessions/${repo}/${sessionId}`);
    const response = await this.fetchWithConfig(endpoint);

    if (!response.ok) {
      if (response.status === 404) {
        throw new Error('Session not found');
      }
      throw new Error('Failed to load session');
    }

    return response.json();
  }

  /**
   * Delete a session
   */
  async deleteSession(sessionId: string): Promise<boolean> {
    const endpoint = this.getEndpoint(`/api/sessions/${sessionId}`);
    const response = await this.fetchWithConfig(endpoint, {
      method: 'DELETE',
    });

    return response.ok;
  }

  /**
   * Create a new session
   */
  async createSession(repo: string, sessionId?: string, metadata?: Record<string, unknown>): Promise<SessionDetail> {
    const endpoint = this.getEndpoint('/api/sessions');
    const response = await this.fetchWithConfig(endpoint, {
      method: 'POST',
      body: JSON.stringify({
        repo,
        sessionId,
        metadata,
      }),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ error: response.statusText }));
      throw new Error(error.error || 'Failed to create session');
    }

    return response.json();
  }

  /**
   * Health check
   */
  async healthCheck(): Promise<HealthResponse> {
    const endpoint = this.getEndpoint('/api/health');
    const response = await this.fetchWithConfig(endpoint);

    if (!response.ok) {
      throw new Error('Health check failed');
    }

    return response.json();
  }

  /**
   * Get pool statistics
   */
  async getPoolStats(): Promise<PoolStats | null> {
    const endpoint = this.getEndpoint('/api/debug/pool-stats');

    try {
      const response = await this.fetchWithConfig(endpoint);

      if (!response.ok) {
        return null;
      }

      return response.json();
    } catch {
      return null;
    }
  }

  /**
   * Check if FastAPI service is available
   */
  async isAvailable(): Promise<boolean> {
    try {
      const response = await fetch(`${getApiUrl()}/api/health/live`, {
        method: 'GET',
        signal: AbortSignal.timeout(2000),
      });
      return response.ok;
    } catch {
      return false;
    }
  }

  // ============== Folder Management ==============

  /**
   * Get folder structure for a repository
   */
  async getFolderStructure(repo: string): Promise<any> {
    const endpoint = this.getEndpoint(`/api/repos/${repo}/folders`);
    const response = await this.fetchWithConfig(endpoint);

    if (!response.ok) {
      throw new Error('Failed to get folder structure');
    }

    const data = await response.json();

    // Transform snake_case to camelCase for frontend compatibility
    return {
      version: data.version,
      folders: (data.folders || []).map((f: any) => ({
        id: f.id,
        name: f.name,
        parentId: f.parent_id,
        createdAt: f.created_at,
        updatedAt: f.updated_at,
      })),
      documentFolders: data.document_folders || {},
    };
  }

  /**
   * Create a new folder
   */
  async createFolder(repo: string, name: string, parentId?: string | null): Promise<any> {
    const endpoint = this.getEndpoint(`/api/repos/${repo}/folders`);
    const response = await this.fetchWithConfig(endpoint, {
      method: 'POST',
      body: JSON.stringify({ name, parent_id: parentId }),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Failed to create folder' }));
      throw new Error(error.detail || 'Failed to create folder');
    }

    const data = await response.json();
    // Transform response to camelCase
    return {
      id: data.id,
      name: data.name,
      parentId: data.parent_id,
      createdAt: data.created_at,
      updatedAt: data.updated_at,
    };
  }

  /**
   * Update a folder (rename or move)
   */
  async updateFolder(
    repo: string,
    folderId: string,
    updates: { name?: string; parentId?: string | null }
  ): Promise<any> {
    const endpoint = this.getEndpoint(`/api/repos/${repo}/folders/${folderId}`);
    const response = await this.fetchWithConfig(endpoint, {
      method: 'PUT',
      body: JSON.stringify({
        name: updates.name,
        parent_id: updates.parentId,
      }),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Failed to update folder' }));
      throw new Error(error.detail || 'Failed to update folder');
    }

    return response.json();
  }

  /**
   * Delete a folder
   */
  async deleteFolder(repo: string, folderId: string): Promise<any> {
    const endpoint = this.getEndpoint(`/api/repos/${repo}/folders/${folderId}`);
    const response = await this.fetchWithConfig(endpoint, {
      method: 'DELETE',
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Failed to delete folder' }));
      throw new Error(error.detail || 'Failed to delete folder');
    }

    return response.json();
  }

  /**
   * Move a document to a folder
   */
  async moveDocument(repo: string, docName: string, folderId: string | null): Promise<any> {
    const endpoint = this.getEndpoint(`/api/repos/${repo}/documents/${docName}/folder`);
    const response = await this.fetchWithConfig(endpoint, {
      method: 'PUT',
      body: JSON.stringify({ folder_id: folderId }),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Failed to move document' }));
      throw new Error(error.detail || 'Failed to move document');
    }

    return response.json();
  }

  /**
   * Batch move documents to a folder
   */
  async batchMoveDocuments(
    repo: string,
    documentNames: string[],
    folderId: string | null
  ): Promise<any> {
    const endpoint = this.getEndpoint(`/api/repos/${repo}/documents/batch-move`);
    const response = await this.fetchWithConfig(endpoint, {
      method: 'POST',
      body: JSON.stringify({ document_names: documentNames, folder_id: folderId }),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Failed to batch move documents' }));
      throw new Error(error.detail || 'Failed to batch move documents');
    }

    return response.json();
  }

  /**
   * Get folder path (for breadcrumb navigation)
   */
  async getFolderPath(repo: string, folderId: string): Promise<any[]> {
    const endpoint = this.getEndpoint(`/api/repos/${repo}/folders/${folderId}/path`);
    const response = await this.fetchWithConfig(endpoint);

    if (!response.ok) {
      throw new Error('Failed to get folder path');
    }

    return response.json();
  }
}

// Default client instance
let defaultClient: ChatAPIClient | null = null;

/**
 * Get the default API client instance
 */
export function getApiClient(): ChatAPIClient {
  if (!defaultClient) {
    defaultClient = new ChatAPIClient();
  }
  return defaultClient;
}

/**
 * Create a new API client with custom configuration
 */
export function createApiClient(config?: { timeout?: number }): ChatAPIClient {
  return new ChatAPIClient(config);
}

export default ChatAPIClient;

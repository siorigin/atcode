// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * ChatStreamManager — module-level singleton that manages SSE streaming
 * outside the React lifecycle. Streams survive component unmount/remount
 * and page navigation within the SPA.
 */

import { useChatStore, type ToolTraceItem } from './store';
import { getFastApiUrl, type ChatEvent } from './api-client';

const AUTH_TOKEN_KEY = 'atcode-auth-token';

// ---------------------------------------------------------------------------
// Module-level state
// ---------------------------------------------------------------------------

const activeStreamControllers = new Map<string, AbortController>();
const activeStreamRequestIds = new Map<string, string>();

function createStreamRequestId(): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return `stream-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function getAuthToken(): string | null {
  if (typeof window === 'undefined') return null;

  const cookies = document.cookie.split(';');
  for (const cookie of cookies) {
    const [name, value] = cookie.trim().split('=');
    if (name === AUTH_TOKEN_KEY && value) {
      return decodeURIComponent(value);
    }
  }

  try {
    return localStorage.getItem(AUTH_TOKEN_KEY);
  } catch {
    return null;
  }
}

function getChatApiUrl(): string {
  return `${getFastApiUrl()}/api/chat/stream`;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function finalizeInterruptedMessage(
  sessionId: string,
  repoName: string,
  toolTrace: ToolTraceItem[],
  options?: {
    marker?: string;
    fallbackError?: string;
    preferContent?: string;
  },
): boolean {
  const store = useChatStore.getState();
  const rawContent = options?.preferContent ?? store.getStreamingContent(repoName, sessionId);
  if (rawContent && rawContent.trim()) {
    const suffix = options?.marker ?? '\n\n*[Generation interrupted]*';
    store.addMessage(sessionId, {
      role: 'assistant',
      content: `${rawContent}${suffix}`,
      metadata: {
        interrupted: true,
        toolTrace: [...toolTrace],
      },
    });
    return true;
  }

  if (options?.fallbackError) {
    store.addMessage(sessionId, {
      role: 'assistant',
      content: options.fallbackError,
      metadata: {
        interrupted: true,
        toolTrace: [...toolTrace],
      },
    });
    return true;
  }

  return false;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export interface StreamParams {
  sessionId: string;
  repoName: string;
  message: string;
  selectedModel?: string;
  documentContext?: {
    filePath?: string;
    selectedText?: string;
    pageType?: string;
    operatorName?: string;
    documentTitle?: string;
    sourcePaperId?: string;
    sourcePaperTitle?: string;
  };
  truncateTurns?: number;
  preload: boolean;
}

export function startStream(params: StreamParams): void {
  const { sessionId, repoName } = params;

  // Abort existing stream for this session
  const existingController = activeStreamControllers.get(sessionId);
  if (existingController && !existingController.signal.aborted) {
    existingController.abort();
  }

  const abortController = new AbortController();
  const requestRunId = createStreamRequestId();
  activeStreamControllers.set(sessionId, abortController);
  activeStreamRequestIds.set(sessionId, requestRunId);

  const store = useChatStore.getState();
  store.setSessionStreaming(sessionId, true);
  store.clearStreamingContent(repoName, sessionId);
  store.setStreamSessionState(sessionId, {
    currentToolCall: null,
    toolCallHistory: [],
    traceExpanded: true,
    autoCollapsed: false,
  });

  // Fire-and-forget — the async loop manages its own lifecycle
  runStreamLoop(params, abortController, requestRunId).catch(err => {
    console.error('Unexpected stream loop error:', err);
  });
}

export function stopStream(sessionId: string, repoName: string): void {
  const controller = activeStreamControllers.get(sessionId);
  if (controller && !controller.signal.aborted) {
    controller.abort();
  }

  const store = useChatStore.getState();
  const streamState = store.streamSessionStates[sessionId];
  const toolTrace = streamState?.toolCallHistory ?? [];

  activeStreamRequestIds.delete(sessionId);
  activeStreamControllers.delete(sessionId);

  finalizeInterruptedMessage(sessionId, repoName, toolTrace, {
    marker: '\n\n*[Generation interrupted]*',
  });

  store.clearStreamingContent(repoName, sessionId);
  store.setSessionStreaming(sessionId, false);
  store.clearStreamSessionState(sessionId);
}

export function isStreamActive(sessionId: string): boolean {
  const controller = activeStreamControllers.get(sessionId);
  return !!controller && !controller.signal.aborted;
}

// ---------------------------------------------------------------------------
// Internal streaming loop
// ---------------------------------------------------------------------------

async function runStreamLoop(
  params: StreamParams,
  abortController: AbortController,
  requestRunId: string,
): Promise<void> {
  const { sessionId, repoName, message, selectedModel, documentContext, truncateTurns, preload } = params;

  const isCurrentRequest = () =>
    activeStreamControllers.get(sessionId) === abortController &&
    activeStreamRequestIds.get(sessionId) === requestRunId;

  const isRetryable = (err: unknown): boolean => {
    if (err instanceof TypeError && /fetch|network/i.test(err.message)) return true;
    if (err instanceof Error && err.name === 'TimeoutError') return true;
    if (err instanceof Error && /network|ECONNREFUSED|ECONNRESET|timeout|502|503|504|incomplete chunked|peer closed/i.test(err.message)) return true;
    return false;
  };

  const MAX_RETRIES = 3;
  const RETRY_BASE_DELAY = 2000;
  const REQUEST_TIMEOUT = 5 * 60 * 1000;

  // Local tool trace accumulator
  let localToolTrace: ToolTraceItem[] = [];

  const updateStreamState = (updates: Partial<import('./store').StreamSessionState>) => {
    useChatStore.getState().setStreamSessionState(sessionId, updates);
  };

  try {
    for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
      if (attempt > 0) {
        const delay = RETRY_BASE_DELAY * Math.pow(2, attempt - 1);
        updateStreamState({ currentToolCall: `Network error — retrying (${attempt}/${MAX_RETRIES}) in ${delay / 1000}s...` });
        useChatStore.getState().clearStreamingContent(repoName, sessionId);
        await new Promise(resolve => setTimeout(resolve, delay));
        if (abortController.signal.aborted || !isCurrentRequest()) return;
        updateStreamState({ currentToolCall: `Retrying (${attempt}/${MAX_RETRIES})...` });
      }

      let assistantContent = '';
      let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;

      try {
        if (!isCurrentRequest()) return;

        const store = useChatStore.getState();
        const session = store.getSession(sessionId);
        const hasCompleteTurn = session?.messages.some(msg => msg.role === 'assistant') ?? false;
        const shouldPreload = preload && hasCompleteTurn;

        if (attempt === 0) {
          console.log(`💬 [StreamManager] Submitting for session ${sessionId}: preload=${shouldPreload}, model=${selectedModel}`);
        }

        const requestBody: any = {
          repo: repoName,
          message,
          session_id: sessionId,
          preload: shouldPreload,
        };

        if (selectedModel) {
          requestBody.model_override = { model: selectedModel };
        }

        if (truncateTurns !== undefined) {
          requestBody.truncate_turns = truncateTurns;
        }

        if (documentContext && Object.keys(documentContext).length > 0) {
          requestBody.document_context = {
            file_path: documentContext.filePath,
            selected_text: documentContext.selectedText,
            page_type: documentContext.pageType,
            operator_name: documentContext.operatorName,
            document_title: documentContext.documentTitle,
            source_paper_id: documentContext.sourcePaperId,
            source_paper_title: documentContext.sourcePaperTitle,
          };
        }

        const chatUrl = getChatApiUrl();
        const headers: Record<string, string> = {
          'Content-Type': 'application/json',
        };

        const token = getAuthToken();
        if (token) {
          headers['Authorization'] = `Bearer ${token}`;
        }

        const timeoutId = setTimeout(() => {
          if (!abortController.signal.aborted) {
            abortController.abort(new DOMException('Request timeout', 'TimeoutError'));
          }
        }, REQUEST_TIMEOUT);

        let response: Response;
        try {
          response = await fetch(chatUrl, {
            method: 'POST',
            headers,
            body: JSON.stringify(requestBody),
            signal: abortController.signal,
            credentials: 'include',
          });
        } finally {
          clearTimeout(timeoutId);
        }

        if (!isCurrentRequest()) return;

        if (!response.ok) {
          let errorMsg = `Chat request failed (${response.status})`;
          try {
            const errData = await response.json();
            errorMsg = errData.error || errData.detail || errorMsg;
          } catch {
            // Ignore JSON parse errors
          }
          const err = new Error(errorMsg);
          if (response.status >= 500 && attempt < MAX_RETRIES) {
            console.warn(`Server error ${response.status}, will retry (${attempt + 1}/${MAX_RETRIES})`);
            continue;
          }
          throw err;
        }

        if (attempt > 0) {
          updateStreamState({ currentToolCall: null });
        }

        reader = response.body?.getReader() || null;
        const decoder = new TextDecoder();

        if (!reader) {
          throw new Error('No response body');
        }

        let buffer = '';
        let streamCompleted = false;
        let terminalEventHandled = false;
        let shouldStopReading = false;

        while (true) {
          if (abortController.signal.aborted || !isCurrentRequest()) {
            try { await reader.cancel(); } catch { /* ignore */ }
            return;
          }

          const { done, value } = await reader.read();
          if (done) break;

          if (!isCurrentRequest()) {
            try { await reader.cancel(); } catch { /* ignore */ }
            return;
          }

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (!isCurrentRequest()) {
              shouldStopReading = true;
              break;
            }
            if (!line.startsWith('data: ')) continue;

            const data = line.slice(6);
            try {
              const event = JSON.parse(data) as ChatEvent;

              if (event.type === 'response') {
                const s = useChatStore.getState();
                if (event.metadata?.is_partial) {
                  s.appendStreamingContent(repoName, sessionId, event.content);
                  assistantContent += event.content;
                } else {
                  assistantContent = event.content;
                  s.setStreamingContent(repoName, sessionId, event.content);
                }

                // Auto-collapse tool trace when first content arrives
                const ss = s.streamSessionStates[sessionId];
                if (ss && !ss.autoCollapsed && localToolTrace.length > 0) {
                  s.setStreamSessionState(sessionId, { traceExpanded: false, autoCollapsed: true });
                }
              } else if (event.type === 'tool_call') {
                updateStreamState({ currentToolCall: event.content });
                const toolName = event.metadata?.tool || event.content;
                const keyArg = event.metadata?.key_arg || '';
                localToolTrace = [...localToolTrace, { tool: String(toolName), key_arg: String(keyArg) }];
                updateStreamState({ toolCallHistory: localToolTrace });
              } else if (event.type === 'tool_result') {
                const resultCount = event.metadata?.count;
                const preview = event.metadata?.preview as string[] | undefined;
                if (localToolTrace.length > 0) {
                  const updated = [...localToolTrace];
                  updated[updated.length - 1] = {
                    ...updated[updated.length - 1],
                    result: resultCount != null ? `${resultCount} results` : event.content,
                    preview,
                  };
                  localToolTrace = updated;
                  updateStreamState({ toolCallHistory: localToolTrace, currentToolCall: null });
                }
              } else if (event.type === 'status') {
                // Status updates handled by loading indicator
              } else if (event.type === 'complete') {
                streamCompleted = true;
                terminalEventHandled = true;

                const s = useChatStore.getState();
                s.clearStreamingContent(repoName, sessionId);
                s.setSessionStreaming(sessionId, false);

                const metadata: any = {
                  toolCalls: event.metadata?.tool_calls,
                  exploredNodes: (event.metadata?.explored_nodes as any[])?.length || 0,
                  toolTrace: event.metadata?.tool_trace || [...localToolTrace],
                };

                if (event.metadata?.references && (event.metadata.references as any[]).length > 0) {
                  metadata.references = event.metadata.references;
                }

                if (event.metadata?.accumulated_code_blocks && (event.metadata.accumulated_code_blocks as any[]).length > 0) {
                  metadata.code_blocks = event.metadata.accumulated_code_blocks;
                }

                s.addMessage(sessionId, {
                  role: 'assistant',
                  content: assistantContent,
                  metadata,
                });

                localToolTrace = [];
                s.clearStreamSessionState(sessionId);
              } else if (event.type === 'error') {
                terminalEventHandled = true;
                updateStreamState({ currentToolCall: null });

                const errText = (event.content || '').toLowerCase();
                const isBenign = errText.includes('stream cancelled') ||
                  errText.includes('incomplete chunked read') ||
                  errText.includes('peer closed connection');

                if (isBenign) {
                  console.warn('[StreamManager] Chat stream ended:', event.content);
                  finalizeInterruptedMessage(sessionId, repoName, localToolTrace, {
                    marker: '\n\n*[Stream interrupted before completion]*',
                    preferContent: assistantContent,
                    fallbackError: 'Error: Chat stream ended before completion.',
                  });
                } else {
                  console.error('[StreamManager] Chat error:', event.content);
                  const preserved = finalizeInterruptedMessage(sessionId, repoName, localToolTrace, {
                    marker: '\n\n*[Stream interrupted by an error]*',
                    preferContent: assistantContent,
                  });
                  if (!preserved) {
                    useChatStore.getState().addMessage(sessionId, {
                      role: 'assistant',
                      content: `Error: ${event.content}`,
                      metadata: {
                        toolTrace: [...localToolTrace],
                      },
                    });
                  }
                }

                shouldStopReading = true;
                break;
              }
            } catch (e) {
              console.error('[StreamManager] Failed to parse SSE event:', e);
            }
          }

          if (shouldStopReading) {
            try { await reader.cancel(); } catch { /* ignore */ }
            break;
          }
        }

        if (!streamCompleted && !terminalEventHandled && !abortController.signal.aborted && isCurrentRequest()) {
          console.warn('[StreamManager] Chat stream closed without a complete event');
          finalizeInterruptedMessage(sessionId, repoName, localToolTrace, {
            marker: '\n\n*[Stream ended before completion]*',
            preferContent: assistantContent,
            fallbackError: 'Error: Chat stream ended unexpectedly before completion.',
          });
        }

        break;
      } catch (error) {
        if (error instanceof Error && error.name === 'AbortError') {
          console.log('[StreamManager] Chat request aborted');
          return;
        }

        if (isRetryable(error) && attempt < MAX_RETRIES) {
          console.warn(`[StreamManager] Network error, will retry (${attempt + 1}/${MAX_RETRIES}):`, error);
          continue;
        }

        console.error('[StreamManager] Chat error:', error);
        updateStreamState({ currentToolCall: null });
        const errorMsg = error instanceof Error ? error.message : 'Unknown error';
        const retryHint = isRetryable(error) ? '\n\n_(All retry attempts failed. Please check your connection and try again.)_' : '';
        const preserved = finalizeInterruptedMessage(sessionId, repoName, localToolTrace, {
          marker: '\n\n*[Stream interrupted by an error]*',
          preferContent: assistantContent,
        });
        if (!preserved) {
          useChatStore.getState().addMessage(sessionId, {
            role: 'assistant',
            content: `Error: ${errorMsg}${retryHint}`,
            metadata: {
              toolTrace: [...localToolTrace],
            },
          });
        }
        break;
      } finally {
        try {
          reader?.releaseLock();
        } catch {
          // Ignore release failures when stream is already closed
        }
      }
    }
  } finally {
    if (activeStreamRequestIds.get(sessionId) === requestRunId) {
      const s = useChatStore.getState();
      s.setSessionStreaming(sessionId, false);
      s.clearStreamingContent(repoName, sessionId);
      s.clearStreamSessionState(sessionId);
      activeStreamRequestIds.delete(sessionId);
    }
    if (activeStreamControllers.get(sessionId) === abortController) {
      activeStreamControllers.delete(sessionId);
    }
  }
}

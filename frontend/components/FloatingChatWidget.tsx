'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useRef, useEffect, useMemo, useCallback, memo } from 'react';
import { createPortal } from 'react-dom';
import { useChatStore, CodeBlock } from '@/lib/store';
import { startStream, stopStream } from '@/lib/chat-stream-manager';
import { useTheme } from '@/lib/theme-context';
import type { Theme } from '@/lib/theme-context';
import { ModelCombobox } from './ModelCombobox';
import { useModels } from '@/lib/hooks/useModels';
import { getThemeColors } from '@/lib/theme-colors';
import { WikiDoc } from '@/components/WikiDoc';
import { useRepoViewer } from '@/lib/repo-viewer-context';
import { useDock } from '@/lib/dock-context';
import { ChatEmptyState } from '@/components/EmptyState';
import { exportChatToMarkdown, downloadMarkdown, downloadPDF, createExportableHTML, exportWithAllCode } from '@/lib/export-utils';
import { TraceViewer } from '@/components/TraceViewer';
import { adaptChatToolTrace, adaptStreamingToolTrace } from '@/types/trace';

/**
 * Fallback copy for non-HTTPS contexts where navigator.clipboard is unavailable.
 */
function fallbackCopyText(text: string) {
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand('copy');
  document.body.removeChild(textarea);
}

interface FloatingChatWidgetProps {
  repoName?: string;
  isOpen: boolean;
  onToggle: () => void;
  activeContext?: {
    documentTitle?: string;
    filePath?: string;
    pageType?: string;
    operatorName?: string;
    sourcePaperId?: string;
    sourcePaperTitle?: string;
    selectedText?: string;
  };
  initialSessionId?: string;
  /** When true, renders as an inline flex child without floating/drag/dock chrome */
  embedded?: boolean;
  /** External text to fill into the input box (e.g. from "Discuss" button) */
  pendingInput?: string;
  /** Called after pendingInput has been consumed */
  onPendingInputConsumed?: () => void;
  /** Optional context ID to scope sessions (e.g. "paper:arxiv_id" for per-paper sessions) */
  contextId?: string;
  /** Navigate to node in shared RepoViewer — when provided, overrides floating panel navigation */
  onNavigateToNode?: (qualifiedName: string) => void;
}

// Type for code block callbacks
type AddCodeBlockCallback = (block: CodeBlock) => void;

// Memoized message component to prevent unnecessary re-renders
const ChatMessage = memo(({
  message,
  bgDark,
  textDark,
  borderDark,
  accent,
  theme: messageTheme,
  onAddCodeBlock,
  onNavigateToNode,
  isStreaming,
  repoName,
  onRetry,
  editingMessageId,
  editingContent,
  onStartEdit,
  onEditChange,
  onEditSubmit,
  onEditCancel,
}: {
  message: any;
  bgDark: string;
  textDark: string;
  borderDark: string;
  accent: string;
  theme: Theme;
  onAddCodeBlock?: AddCodeBlockCallback;
  onNavigateToNode?: (qualifiedName: string) => void;
  isStreaming?: boolean;
  repoName?: string;
  onRetry?: () => void;
  editingMessageId?: string | null;
  editingContent?: string;
  onStartEdit?: (messageId: string, content: string) => void;
  onEditChange?: (content: string) => void;
  onEditSubmit?: (messageId: string, content: string) => void;
  onEditCancel?: () => void;
}) => {
  const isEditing = editingMessageId === message.id;
  const [isHovered, setIsHovered] = useState(false);
  const [showToolTrace, setShowToolTrace] = useState(false);
  const toolTrace: import('@/lib/store').ToolTraceItem[] = message.metadata?.toolTrace || [];
  const contextStats = message.metadata?.contextStats as { total_turns?: number; kept_turns?: number; trimmed?: boolean; approx_tokens?: number; summaries_count?: number } | null;
  // Process markdown to convert [[node]] to {{NODE_LINK:node}} markers for assistant messages
  const processedContent = useMemo(() => 
    message.role === 'assistant' 
      ? message.content
      : message.content,
    [message.content, message.role]
  );

  // Convert references to WikiDoc format - pass all fields needed for matching and code display
  const references = useMemo(() =>
    message.metadata?.references?.map((ref: any) => ({
      // Identity fields for matching
      identifier: ref.identifier || ref.qualified_name || '',
      qualified_name: ref.qualified_name || ref.identifier || '',
      name: ref.name || ref.qualified_name || ref.identifier || '',
      ref: ref.identifier || ref.qualified_name || '',
      // Location fields
      file: ref.file || ref.path || '',
      path: ref.path || ref.file || '',
      startLine: ref.start_line || ref.startLine || 0,
      endLine: ref.end_line || ref.endLine || 0,
      start_line: ref.start_line || ref.startLine || null,
      end_line: ref.end_line || ref.endLine || null,
      // Code fields for embedded code display
      code: ref.code || '',
      language: ref.language || 'python',
      // Type info
      nodeType: ref.nodeType || ref.type || '',
      type: ref.type || ref.nodeType || '',
      // Repo info for cross-repo references
      repo_name: ref.repo_name || '',
    })),
    [message.metadata?.references]
  );

  return (
    <div
      style={{
        display: 'flex',
        justifyContent: message.role === 'user' ? 'flex-end' : 'flex-start',
        minWidth: 0,
      }}
      onMouseEnter={() => message.role === 'user' && setIsHovered(true)}
      onMouseLeave={() => message.role === 'user' && setIsHovered(false)}
    >
      {/* Pencil edit icon — shown on hover for user messages */}
      {message.role === 'user' && isHovered && !isStreaming && !isEditing && onStartEdit && (
        <button
          onClick={() => onStartEdit(message.id, message.content)}
          style={{
            alignSelf: 'center',
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            padding: '4px',
            marginRight: '4px',
            opacity: 0.5,
            transition: 'opacity 0.15s',
            color: textDark,
            flexShrink: 0,
          }}
          onMouseEnter={(e) => { e.currentTarget.style.opacity = '1'; }}
          onMouseLeave={(e) => { e.currentTarget.style.opacity = '0.5'; }}
          title="Edit message"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
            <path d="m15 5 4 4" />
          </svg>
        </button>
      )}
      <div
        className={message.role === 'user' ? 'chat-message-user' : 'chat-message-assistant'}
        style={{
          maxWidth: '90%',
          minWidth: 0,
          padding: '10px 14px',
          borderRadius: '12px',
          fontSize: '13px',
          background: message.role === 'user' ? accent : bgDark,
          color: message.role === 'user' ? '#ffffff' : textDark,
          border: message.role === 'assistant' ? `1px solid ${borderDark}` : 'none',
          wordWrap: 'break-word',
          overflowWrap: 'break-word',
          userSelect: 'text',
          WebkitUserSelect: 'text',
          cursor: 'text',
        }}
      >
        {message.role === 'assistant' ? (
          <>
            {toolTrace.length > 0 && (
              <div style={{ marginBottom: '10px' }}>
                <button
                  type="button"
                  onClick={() => setShowToolTrace(prev => !prev)}
                  style={{
                    width: '100%',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: '10px',
                    padding: '8px 10px',
                    borderRadius: '10px',
                    border: `1px solid ${borderDark}`,
                    background: 'transparent',
                    color: textDark,
                    cursor: 'pointer',
                    fontSize: '12px',
                  }}
                >
                  <span style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
                    <span style={{ fontWeight: 600 }}>Trace</span>
                    <span style={{ color: textDark, opacity: 0.65 }}>
                      {toolTrace.length} step{toolTrace.length > 1 ? 's' : ''}
                    </span>
                    {contextStats && (
                      <span style={{ color: textDark, opacity: 0.5, fontSize: '11px', borderLeft: `1px solid ${borderDark}`, paddingLeft: '8px' }}>
                        {contextStats.trimmed
                          ? `${contextStats.kept_turns}/${contextStats.total_turns} turns`
                          : `${contextStats.total_turns} turn${(contextStats.total_turns || 0) > 1 ? 's' : ''}`}
                        {` · ~${((contextStats.approx_tokens || 0) / 1000).toFixed(1)}k tokens`}
                      </span>
                    )}
                  </span>
                  <span style={{ color: textDark, opacity: 0.65, fontSize: '11px' }}>
                    {showToolTrace ? 'Hide' : 'Show'}
                  </span>
                </button>

                {showToolTrace && (
                  <div style={{
                    marginTop: '8px',
                    padding: '8px 10px',
                    borderRadius: '10px',
                    border: `1px solid ${borderDark}`,
                    background: bgDark,
                  }}>
                    <TraceViewer
                      nodes={adaptChatToolTrace(toolTrace, message.id)}
                      theme={messageTheme}
                      compact
                    />
                  </div>
                )}
              </div>
            )}
            <WikiDoc
              markdown={processedContent}
              references={references}
              codeBlocks={message.metadata?.code_blocks}
              layoutMode="split"
              onAddCodeBlock={onAddCodeBlock}
              onNavigateToNode={onNavigateToNode}
              onNavigateToPaper={(paperId) => {
                window.open(`/repos/papers?paper=${encodeURIComponent(paperId)}&tab=daily`, '_blank');
              }}
              isStreaming={isStreaming}
              repoName={repoName}
            />
            {message.metadata && (
              <div style={{ marginTop: '8px', fontSize: '11px', opacity: 0.6 }}>
                {message.metadata.toolCalls && (
                  <span>🔍 {message.metadata.toolCalls} searches</span>
                )}
                {message.metadata.exploredNodes && (
                  <span style={{ marginLeft: '8px' }}>
                    📦 {message.metadata.exploredNodes} elements
                  </span>
                )}
              </div>
            )}
            {/* Retry button for error messages */}
            {onRetry && message.content.startsWith('Error:') && (
              <button
                onClick={onRetry}
                style={{
                  marginTop: 8,
                  padding: '5px 14px',
                  fontSize: 12,
                  fontWeight: 500,
                  fontFamily: "'Inter', sans-serif",
                  color: accent,
                  background: accent + '15',
                  border: `1px solid ${accent}40`,
                  borderRadius: 8,
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 5,
                  transition: 'all 0.15s',
                }}
                onMouseEnter={(e) => { e.currentTarget.style.background = accent + '25'; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = accent + '15'; }}
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="23 4 23 10 17 10" /><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
                </svg>
                Retry
              </button>
            )}
          </>
        ) : isEditing ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', minWidth: '200px' }}>
            <textarea
              value={editingContent}
              onChange={(e) => onEditChange?.(e.target.value)}
              autoFocus
              style={{
                width: '100%',
                minHeight: '60px',
                padding: '8px',
                borderRadius: '8px',
                border: '1px solid rgba(255,255,255,0.3)',
                background: 'rgba(0,0,0,0.2)',
                color: '#ffffff',
                fontSize: '13px',
                fontFamily: 'inherit',
                resize: 'vertical',
                outline: 'none',
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  if (editingContent?.trim()) onEditSubmit?.(message.id, editingContent.trim());
                }
                if (e.key === 'Escape') onEditCancel?.();
              }}
            />
            <div style={{ display: 'flex', gap: '6px', justifyContent: 'flex-end' }}>
              <button
                onClick={() => onEditCancel?.()}
                style={{
                  padding: '4px 12px',
                  fontSize: '12px',
                  borderRadius: '6px',
                  border: '1px solid rgba(255,255,255,0.3)',
                  background: 'transparent',
                  color: '#ffffff',
                  cursor: 'pointer',
                }}
              >
                Cancel
              </button>
              <button
                onClick={() => editingContent?.trim() && onEditSubmit?.(message.id, editingContent.trim())}
                style={{
                  padding: '4px 12px',
                  fontSize: '12px',
                  borderRadius: '6px',
                  border: 'none',
                  background: 'rgba(255,255,255,0.2)',
                  color: '#ffffff',
                  cursor: 'pointer',
                  fontWeight: 500,
                }}
              >
                Save & Send
              </button>
            </div>
          </div>
        ) : (
          <p style={{ margin: 0, whiteSpace: 'pre-wrap', userSelect: 'text', WebkitUserSelect: 'text' }}>{message.content}</p>
        )}
      </div>
    </div>
  );
});

ChatMessage.displayName = 'ChatMessage';

export function FloatingChatWidget({ repoName, isOpen, onToggle, activeContext, initialSessionId, embedded, pendingInput, onPendingInputConsumed, contextId, onNavigateToNode: onNavigateToNodeProp }: FloatingChatWidgetProps) {
  // Use '__global__' as repo key when no specific repo is selected
  const effectiveRepoKey = repoName ?? '__global__';
  const { theme } = useTheme();
  const { tiers, defaultModel } = useModels({ enabled: isOpen || !!embedded });
  const [input, setInput] = useState('');
  const [showAllMessages, setShowAllMessages] = useState(false);
  const [selectedModel, setSelectedModel] = useState<string>('');
  const [showSavedLogs, setShowSavedLogs] = useState(false);
  const [savedLogs, setSavedLogs] = useState<any[]>([]);
  const [loadingLogs, setLoadingLogs] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);
  const [exportDropdownOpen, setExportDropdownOpen] = useState(false);
  const exportDropdownRef = useRef<HTMLDivElement>(null);
  const [isExporting, setIsExporting] = useState(false);
  const [exportMode, setExportMode] = useState<'appendix' | 'inline'>('appendix');
  const [linkCopied, setLinkCopied] = useState(false);
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [editingContent, setEditingContent] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const { openRepoViewer } = useRepoViewer();
  const { dock: dockToSidebar, undock: undockFromSidebar, isDocked } = useDock();
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const prevSessionIdRef = useRef<string | null>(null); // Track session ID to avoid spurious aborts on re-render
  const isComposingRef = useRef(false); // IME composition state
  const userScrollingRef = useRef(false);
  const isStreamingRef = useRef(false);
  const lastScrollTimeRef = useRef(0);

  // Drag state - use ref to track position during drag to avoid frequent re-renders
  const [position, setPosition] = useState<{x: number | null, y: number | null}>(() => {
    if (typeof window === 'undefined') return { x: null, y: null };
    try {
      const saved = localStorage.getItem('floating-chat-position');
      if (saved) {
        return JSON.parse(saved);
      }
    } catch { /* ignore */ }
    return { x: null, y: null };
  });
  const [isDragging, setIsDragging] = useState(false);
  const dragStartRef = useRef({ x: 0, y: 0, posX: 0, posY: 0 });
  const containerRef = useRef<HTMLDivElement>(null);
  const positionRef = useRef(position); // Track position during drag

  // Sync position to ref
  useEffect(() => {
    positionRef.current = position;
  }, [position]);

  useEffect(() => {
    if (!selectedModel && defaultModel) {
      setSelectedModel(defaultModel);
    }
  }, [defaultModel, selectedModel]);

  // Custom size state (user manually adjusted size)
  const [customSize, setCustomSize] = useState<{width: number | null, height: number | null}>(() => {
    if (typeof window === 'undefined') return { width: null, height: null };
    try {
      const saved = localStorage.getItem('floating-chat-size');
      if (saved) {
        return JSON.parse(saved);
      }
    } catch { /* ignore */ }
    return { width: null, height: null };
  });
  const [isResizing, setIsResizing] = useState(false);
  const resizeStartRef = useRef({ x: 0, y: 0, width: 0, height: 0, left: 0, top: 0, winW: 0, winH: 0 });
  const sizeRef = useRef(customSize);

  // Sync customSize to ref
  useEffect(() => {
    sizeRef.current = customSize;
  }, [customSize]);

  // Dynamic width state (auto-adjust based on input, but use custom size if set)
  const [dynamicWidth, setDynamicWidth] = useState(500);

  const {
    activeSessionIds,
    sessions: allSessions,
    createSession,
    addMessage,
    isSessionStreaming,
    streamingSessionIds,
    getStreamingContent,
    clearMessages,
    setDocumentContext,
    clearDocumentContext,
    loadSession,
    setActiveSession,
    openTabs,
    activeTabId,
    openTab,
    closeTab,
    switchTab,
    truncateMessages,
  } = useChatStore();
  
  // FloatingChatWidget no longer needs global codeBlocks and setCodeBlocks
  // It uses its own computed currentCodeBlocks (see useMemo below)
  // const { codeBlocks, setCodeBlocks, setActiveBlock } = useWikiStore();

  // Subscribe to session changes properly - use selector to trigger re-renders
  // When contextId is provided, scope sessions to that context (e.g. per-paper)
  const session = useChatStore((state) => {
    const activeId = state.activeSessionIds[effectiveRepoKey];

    if (contextId) {
      // Context-scoped: if activeId points to a session in this context, use it
      if (activeId) {
        const activeSession = state.sessions.find(
          s => s.id === activeId && s.repoName === effectiveRepoKey && s.contextId === contextId
        );
        if (activeSession) return activeSession;
      }
      // Fallback: find most recent session for this context
      const contextSessions = state.sessions
        .filter(s => s.repoName === effectiveRepoKey && s.contextId === contextId)
        .sort((a, b) => b.updatedAt - a.updatedAt);
      return contextSessions[0];
    }

    if (!activeId) {
      // No active session for this repo, find most recent (without contextId)
      const repoSessions = state.sessions
        .filter(s => s.repoName === effectiveRepoKey && !s.contextId)
        .sort((a, b) => b.updatedAt - a.updatedAt);
      return repoSessions[0];
    }
    // When no contextId, only match sessions without contextId to avoid picking up
    // context-scoped sessions (e.g. per-paper sessions from PaperWorkspace)
    const activeSession = state.sessions.find(s => s.id === activeId && s.repoName === effectiveRepoKey && !s.contextId);
    if (activeSession) return activeSession;
    // Active session has a contextId we don't want — fall back to most recent unscoped session
    const repoSessions = state.sessions
      .filter(s => s.repoName === effectiveRepoKey && !s.contextId)
      .sort((a, b) => b.updatedAt - a.updatedAt);
    return repoSessions[0];
  });
  const activeSessionId = activeSessionIds[effectiveRepoKey];
  const messages = session?.messages || [];

  // Per-session streaming state (replaces global isStreaming for this widget)
  const isStreaming = session?.id ? isSessionStreaming(session.id) : false;

  // Get streaming content for current session
  const currentStreamingContent = session?.id ? getStreamingContent(effectiveRepoKey, session.id) : '';

  // Stream UI state from Zustand (managed by ChatStreamManager)
  const streamState = useChatStore(state => state.streamSessionStates[session?.id ?? '']);
  const currentToolCall = streamState?.currentToolCall ?? null;
  const toolCallHistory = streamState?.toolCallHistory ?? [];
  const streamToolTraceExpanded = streamState?.traceExpanded ?? true;

  // Sync activeContext to session
  // Sync activeContext to session — only re-run when activeContext identity or session changes
  const activeDocTitle = activeContext?.documentTitle;
  const activeFilePath = activeContext?.filePath;
  const activePageType = activeContext?.pageType;
  const activeOperatorName = activeContext?.operatorName;
  const activeSourcePaperId = activeContext?.sourcePaperId;
  const activeSourcePaperTitle = activeContext?.sourcePaperTitle;
  const activeSelectedText = activeContext?.selectedText;
  const sessionId = session?.id;

  useEffect(() => {
    if (!sessionId) return;

    if (activeDocTitle || activeFilePath || activePageType) {
      setDocumentContext(sessionId, {
        documentTitle: activeDocTitle,
        filePath: activeFilePath,
        pageType: activePageType,
        operatorName: activeOperatorName,
        sourcePaperId: activeSourcePaperId,
        sourcePaperTitle: activeSourcePaperTitle,
        selectedText: activeSelectedText,
      });
    } else {
      clearDocumentContext(sessionId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeDocTitle, activeFilePath, activePageType, activeOperatorName, activeSourcePaperId, activeSourcePaperTitle, activeSelectedText, sessionId]);

  // Minimal session initialization logic:
  // 1. If active session exists and repo matches → keep unchanged
  // 2. If repo doesn't match → find most recent session for repo or create new
  // 3. When contextId is provided, scope lookup to that context (per-paper sessions)
  useEffect(() => {
    // If session already found (by selector above), keep it
    if (session && session.repoName === effectiveRepoKey && (!contextId || session.contextId === contextId)) {
      prevSessionIdRef.current = session.id;
      return;
    }

    // Need to find or create a session
    console.log('🔄 Looking for session in repo:', effectiveRepoKey, contextId ? `context: ${contextId}` : '');

    // Track current session ID for change detection, but do NOT abort streams here.
    // Multiple float chat widgets may share the same repo's activeSessionId.
    // Aborting here would kill a sibling widget's in-flight stream whenever the
    // active session changes. Streams are only aborted explicitly when the user
    // submits a new message or clicks stop.
    const currentSessionId = session?.id ?? null;
    prevSessionIdRef.current = currentSessionId;

    // Find most recently used session for this repo (and context if provided)
    const { sessions } = useChatStore.getState();
    const matchingSessions = sessions
      .filter(s => s.repoName === effectiveRepoKey && (contextId ? s.contextId === contextId : !s.contextId))
      .sort((a, b) => b.updatedAt - a.updatedAt);

    if (matchingSessions.length > 0) {
      const latestSession = matchingSessions[0];
      console.log('🔄 Switching to session:', latestSession.id, contextId ? `(context: ${contextId})` : '');
      setActiveSession(effectiveRepoKey, latestSession.id);
    } else {
      // No matching sessions, create new one
      const newSessionId = typeof crypto !== 'undefined' && crypto.randomUUID
        ? crypto.randomUUID()
        : `session-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`;

      console.log('🆕 Creating new session for repo:', effectiveRepoKey, contextId ? `context: ${contextId}` : '', 'ID:', newSessionId);
      createSession(effectiveRepoKey, newSessionId, undefined, contextId);
    }
  }, [effectiveRepoKey, contextId, session, createSession, setActiveSession]);

  // Load session from initialSessionId (URL-based deep link)
  useEffect(() => {
    if (!initialSessionId) return;

    const loadInitialSession = async () => {
      // Check if session already exists locally
      const { sessions } = useChatStore.getState();
      const existing = sessions.find(s => s.id === initialSessionId);
      if (existing) {
        setActiveSession(effectiveRepoKey, initialSessionId);
        return;
      }

      // Try loading from server
      try {
        const response = await fetch(`/api/chat-logs/${effectiveRepoKey}/${initialSessionId}`);
        if (!response.ok) {
          // Session not found — create new one with this ID
          console.log('📭 Session not found on server, creating new:', initialSessionId);
          createSession(effectiveRepoKey, initialSessionId);
          return;
        }

        const logData = await response.json();
        const serverTurns = logData.turns || [];

        let msgCounter = 0;
        const generateMsgId = () => {
          msgCounter++;
          return `msg-${initialSessionId}-${msgCounter}-${Math.random().toString(36).substring(2, 8)}`;
        };

        const msgs: any[] = [];
        for (const turn of serverTurns) {
          msgs.push({
            id: generateMsgId(),
            role: 'user' as const,
            content: turn.query,
            timestamp: new Date(turn.timestamp).getTime(),
          });
          msgs.push({
            id: generateMsgId(),
            role: 'assistant' as const,
            content: turn.interrupted ? `${turn.response}\n\n*[Generation interrupted]*` : turn.response,
            timestamp: new Date(turn.timestamp).getTime(),
            metadata: {
              toolCalls: turn.tool_calls || 0,
              exploredNodes: turn.explored_nodes?.length || 0,
              references: turn.references || [],
              toolTrace: turn.tool_trace || [],
            },
          });
        }

        const newSession = {
          id: initialSessionId,
          repoName: effectiveRepoKey,
          contextId,
          messages: msgs,
          createdAt: new Date(logData.created_at).getTime(),
          updatedAt: new Date(logData.updated_at || logData.created_at).getTime(),
        };

        loadSession(newSession);
        setActiveSession(effectiveRepoKey, initialSessionId);
        console.log('✅ Loaded session from URL:', initialSessionId);
      } catch (error) {
        console.error('Failed to load session from URL:', error);
        createSession(effectiveRepoKey, initialSessionId);
      }
    };

    loadInitialSession();
  }, [initialSessionId, effectiveRepoKey, loadSession, setActiveSession, createSession]);

  // Ensure current session is opened as a tab
  useEffect(() => {
    if (session?.id && session.repoName === effectiveRepoKey) {
      const currentTabs = openTabs[effectiveRepoKey] || [];
      if (!currentTabs.includes(session.id)) {
        openTab(effectiveRepoKey, session.id);
      }
    }
  }, [session?.id, effectiveRepoKey, openTabs, openTab]);

  // Timestamp of the last programmatic scroll — used to ignore scroll events
  // that fire as a side-effect of scrollToBottom (reflow, multi-fire, etc.)
  const programmaticScrollTsRef = useRef(0);

  // Scroll to bottom - only if user is not manually scrolling
  const scrollToBottom = useCallback(() => {
    if (userScrollingRef.current) return;
    const container = messagesContainerRef.current;
    if (container) {
      programmaticScrollTsRef.current = Date.now();
      container.scrollTop = container.scrollHeight;
    }
  }, []);

  // Detect user scrolling via wheel, touch, AND scrollbar drag.
  // We use a 100ms cooldown after programmatic scrolls to avoid false positives
  // from reflow-triggered or multi-fire scroll events.
  useEffect(() => {
    const container = messagesContainerRef.current;
    if (!container) return;

    const isProgrammaticScroll = () => Date.now() - programmaticScrollTsRef.current < 100;

    const handleWheel = (e: WheelEvent) => {
      if (e.deltaY < 0) {
        // User scrolled up — pause auto-scroll
        userScrollingRef.current = true;
      } else if (e.deltaY > 0) {
        // User scrolled down — re-enable auto-scroll if near bottom
        const gap = container.scrollHeight - container.scrollTop - container.clientHeight;
        const threshold = isStreamingRef.current ? 20 : 80;
        if (gap < threshold) {
          userScrollingRef.current = false;
        }
      }
    };

    const handleScroll = () => {
      const gap = container.scrollHeight - container.scrollTop - container.clientHeight;
      // If the user is far from the bottom, always treat as user scrolling —
      // even during programmatic scroll cooldown. This handles scrollbar drag,
      // which only fires scroll events (no wheel events) and would otherwise
      // be swallowed by the cooldown.
      if (gap > 200) {
        userScrollingRef.current = true;
        return;
      }
      // Ignore scroll events caused by programmatic scrollToBottom or content reflow
      if (isProgrammaticScroll()) return;
      // During streaming, content growth changes scrollHeight which fires scroll
      // events even when the user hasn't touched anything. Only treat it as user
      // scroll if they've actually scrolled away from the bottom.
      if (isStreamingRef.current) {
        if (gap > 80) {
          userScrollingRef.current = true;
        }
        // During streaming, never re-enable auto-scroll from scroll events —
        // only wheel/touch down near bottom can re-enable it.
        return;
      }
      // Not streaming: standard logic
      if (gap > 80) {
        userScrollingRef.current = true;
      } else {
        userScrollingRef.current = false;
      }
    };

    const handleTouchStart = (e: TouchEvent) => {
      // Record touch start position for swipe detection
      (container as any).__touchStartY = e.touches[0].clientY;
    };

    const handleTouchMove = (e: TouchEvent) => {
      const startY = (container as any).__touchStartY;
      if (startY === undefined) return;
      const deltaY = startY - e.touches[0].clientY;
      if (deltaY < -10) {
        // Swiped down (scroll up) — pause auto-scroll
        userScrollingRef.current = true;
      } else if (deltaY > 10) {
        const gap = container.scrollHeight - container.scrollTop - container.clientHeight;
        const threshold = isStreamingRef.current ? 20 : 80;
        if (gap < threshold) {
          userScrollingRef.current = false;
        }
      }
    };

    container.addEventListener('wheel', handleWheel, { passive: true });
    container.addEventListener('scroll', handleScroll, { passive: true });
    container.addEventListener('touchstart', handleTouchStart, { passive: true });
    container.addEventListener('touchmove', handleTouchMove, { passive: true });
    return () => {
      container.removeEventListener('wheel', handleWheel);
      container.removeEventListener('scroll', handleScroll);
      container.removeEventListener('touchstart', handleTouchStart);
      container.removeEventListener('touchmove', handleTouchMove);
    };
  }, []);

  // Keep isStreamingRef in sync for use in scroll handlers
  useEffect(() => {
    isStreamingRef.current = isStreaming;
  }, [isStreaming]);

  // Reset auto-scroll lock when a NEW user message is sent (not when streaming ends).
  // This ensures the user stays where they scrolled during streaming, but the next
  // conversation turn starts with auto-scroll enabled.
  const prevMessagesLenRef = useRef(0);
  useEffect(() => {
    const len = messages.length;
    // A new user message was just added — re-enable auto-scroll
    if (len > prevMessagesLenRef.current && messages[len - 1]?.role === 'user') {
      userScrollingRef.current = false;
    }
    prevMessagesLenRef.current = len;
  }, [messages]);

  // Auto-scroll during streaming (throttled).
  // Pauses when the user has scrolled up, OR when the streaming message has grown
  // taller than the visible area (user is likely reading the upper portion).
  useEffect(() => {
    if (session?.id && currentStreamingContent) {
      const now = Date.now();
      if (now - lastScrollTimeRef.current >= 150) {
        lastScrollTimeRef.current = now;
        const container = messagesContainerRef.current;
        if (container && !userScrollingRef.current) {
          const gap = container.scrollHeight - container.scrollTop - container.clientHeight;
          // If the user is already far from the bottom (e.g. reading earlier content
          // in a long streaming message), treat as user-scrolling and stop auto-scroll.
          if (gap > container.clientHeight * 0.8) {
            userScrollingRef.current = true;
            return;
          }
        }
        scrollToBottom();
      }
    }
  }, [currentStreamingContent, session?.id, scrollToBottom]);

  // Focus input when opened
  useEffect(() => {
    if (isOpen && inputRef.current) {
      inputRef.current.focus();
    }
  }, [isOpen]);

  // Accept external pending input (e.g. from "Discuss" button in papers)
  useEffect(() => {
    if (pendingInput) {
      setInput(pendingInput);
      onPendingInputConsumed?.();
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [pendingInput]);

  // Auto-adjust textarea height
  const adjustTextareaHeight = useCallback(() => {
    const textarea = inputRef.current;
    if (textarea) {
      textarea.style.height = 'auto';
      textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`;
    }
  }, []);

  useEffect(() => {
    adjustTextareaHeight();
  }, [input, adjustTextareaHeight]);

  // Auto-adjust window width based on input content
  useEffect(() => {
    if (isExpanded) return;
    const baseWidth = 500;
    const maxWidth = 800;
    // Add 50px width for every 50 characters
    const extraWidth = Math.min(Math.floor(input.length / 50) * 50, maxWidth - baseWidth);
    const newWidth = baseWidth + extraWidth;
    setDynamicWidth(newWidth);
  }, [input, isExpanded]);

  // Adjust position when width changes to prevent overflow
  const customWidth = customSize.width;
  useEffect(() => {
    if (isExpanded || position.x === null) return;
    // Use actual width (custom or dynamic)
    const actualWidth = customWidth ?? dynamicWidth;
    const maxX = window.innerWidth - actualWidth;
    if (position.x > maxX) {
      const newX = Math.max(0, maxX);
      setPosition(prev => prev.x !== newX ? { x: newX, y: prev.y } : prev);
    }
  }, [dynamicWidth, customWidth, isExpanded, position.x]);

  const handleSubmit = async (e: React.FormEvent, options?: { overrideMessage?: string; truncateTurns?: number }) => {
    e.preventDefault();
    const messageToSend = options?.overrideMessage ?? input.trim();
    if (!messageToSend || isStreaming) return;

    let currentSession: typeof session | undefined = session ?? useChatStore.getState().getActiveSession(effectiveRepoKey);

    const userMessage = messageToSend;
    if (!options?.overrideMessage) setInput('');

    // If session is 'new', generate real session ID first
    if (currentSession?.id === 'new') {
      try {
        const response = await fetch('/api/new-session');
        if (!response.ok) {
          throw new Error('Failed to generate session ID');
        }

        const { sessionId: newSessionId } = await response.json();
        console.log('🆕 Creating new session on first message in widget:', newSessionId);

        // Clear the 'new' placeholder and create real session
        if (currentSession) {
          clearMessages(currentSession.id);
        }
        createSession(effectiveRepoKey, newSessionId, undefined, contextId);
        // Get the newly created session
        currentSession = useChatStore.getState().getActiveSession(effectiveRepoKey);
      } catch (error) {
        console.error('Failed to create session:', error);
        return;
      }
    }

    if (!currentSession) return;

    // Add user message
    addMessage(currentSession.id, {
      role: 'user',
      content: userMessage,
    });

    // Determine preload: true when existing assistant messages exist
    const hasCompleteTurn = currentSession.messages.some(msg => msg.role === 'assistant');

    // Delegate streaming to the background manager
    startStream({
      sessionId: currentSession.id,
      repoName: effectiveRepoKey,
      message: userMessage,
      selectedModel,
      documentContext: currentSession.documentContext ? {
        filePath: currentSession.documentContext.filePath,
        selectedText: currentSession.documentContext.selectedText,
        pageType: currentSession.documentContext.pageType,
        operatorName: currentSession.documentContext.operatorName,
        documentTitle: currentSession.documentContext.documentTitle,
        sourcePaperId: currentSession.documentContext.sourcePaperId,
        sourcePaperTitle: currentSession.documentContext.sourcePaperTitle,
      } : undefined,
      truncateTurns: options?.truncateTurns,
      preload: hasCompleteTurn,
    });
  };

  const handleEditAndRegenerate = (messageId: string, newContent: string) => {
    if (!session || isStreaming) return;
    const messageIndex = session.messages.findIndex(m => m.id === messageId);
    if (messageIndex === -1) return;

    setEditingMessageId(null);

    // Count complete user-assistant turns before this message
    const messagesBefore = session.messages.slice(0, messageIndex);
    const turnsToKeep = messagesBefore.filter(m => m.role === 'user').length;

    // Truncate frontend messages from the edit point
    truncateMessages(session.id, messageIndex);

    // Submit the edited message with truncation signal
    const fakeEvent = { preventDefault: () => {} } as React.FormEvent;
    handleSubmit(fakeEvent, { overrideMessage: newContent, truncateTurns: turnsToKeep });
  };

  // Cancel editing when streaming starts
  useEffect(() => {
    if (isStreaming) setEditingMessageId(null);
  }, [isStreaming]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !isComposingRef.current) {
      e.preventDefault();
      handleSubmit(e as any);
    }
  };

  const handleClearMessages = useCallback(async () => {
    console.log('🆕 Opening new chat tab');
    // Create a new session with a proper UUID (createSession also opens it as a tab)
    const newId = typeof crypto !== 'undefined' && crypto.randomUUID
      ? crypto.randomUUID()
      : `session-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`;
    createSession(effectiveRepoKey, newId, undefined, contextId);
  }, [createSession, effectiveRepoKey, contextId]);

  const handleOpenSavedLogs = useCallback(async () => {
    console.log('🔍 Opening saved logs for repo:', effectiveRepoKey);
    setShowSavedLogs(true);
    setLoadingLogs(true);
    try {
      const response = await fetch(`/api/chat-logs/${effectiveRepoKey}`, {
        cache: 'no-store',
      });
      console.log('📡 API response status:', response.status);
      if (response.ok) {
        const data = await response.json();
        console.log('📦 Received logs data:', data);
        setSavedLogs(data.logs || []);
      } else {
        console.error('❌ API response not OK:', response.status, response.statusText);
      }
    } catch (error) {
      console.error('Failed to load saved logs:', error);
    } finally {
      console.log('✅ Setting loadingLogs to false');
      setLoadingLogs(false);
    }
  }, [effectiveRepoKey]);

  const handleLoadSavedLog = useCallback(async (logId: string) => {
    try {
      console.log('📂 Loading saved log:', logId);

      // Optimization: check if already the current active session
      const currentSession = session || useChatStore.getState().getActiveSession(effectiveRepoKey);
      if (currentSession?.id === logId) {
        console.log('✅ Session already active, skipping reload:', logId);
        setIsExpanded(true);
        setShowSavedLogs(false);
        return;
      }

      // 🔧 优化：检查 store 中是否已有该 session（避免重复加载）
      const { sessions } = useChatStore.getState();
      const existingSession = sessions.find(s => s.id === logId);

      // Load the session data from the server to check for updates
      const response = await fetch(`/api/chat-logs/${effectiveRepoKey}/${logId}`, {
        cache: 'no-store',
      });
      if (!response.ok) {
        throw new Error('Failed to load chat log');
      }

      const logData = await response.json();
      const serverTurns = logData.turns || [];
      const serverTurnsCount = serverTurns.length;

      // 🔧 优化：如果本地 session 已存在且 turns 数量相同或更多，直接使用本地版本
      // （本地可能有尚未保存到服务器的新消息）
      if (existingSession) {
        const localTurnsCount = Math.floor(existingSession.messages.length / 2);
        if (localTurnsCount >= serverTurnsCount) {
          console.log(`✅ Using existing local session (${localTurnsCount} turns vs server ${serverTurnsCount} turns)`);
          setActiveSession(effectiveRepoKey, logId);
          setIsExpanded(true);
          setShowSavedLogs(false);
          return;
        }
        console.log(`🔄 Server has newer data (${serverTurnsCount} turns vs local ${localTurnsCount} turns), reloading...`);
      }

      console.log('✅ Loaded session from server:', logData.id, `(${serverTurnsCount} turns)`);

      // Convert the loaded chat log to session format
      // 🔧 优化：使用唯一 ID 生成器避免在快速循环中产生重复 ID
      let msgCounter = 0;
      const generateMsgId = () => {
        msgCounter++;
        return `msg-${logId}-${msgCounter}-${Math.random().toString(36).substring(2, 8)}`;
      };
      
      const messages: any[] = [];

      for (const turn of serverTurns) {
        // Add user message
        messages.push({
          id: generateMsgId(),
          role: 'user' as const,
          content: turn.query,
          timestamp: new Date(turn.timestamp).getTime(),
        });

        // Add assistant message with metadata
        const assistantMetadata: any = {
          toolCalls: turn.tool_calls || 0,
          exploredNodes: turn.explored_nodes?.length || 0,
          references: turn.references || [],
          toolTrace: turn.tool_trace || [],
        };
        
        messages.push({
          id: generateMsgId(),
          role: 'assistant' as const,
          content: turn.interrupted ? `${turn.response}\n\n*[Generation interrupted]*` : turn.response,
          timestamp: new Date(turn.timestamp).getTime(),
          metadata: assistantMetadata,
        });
      }

      // Create a new session with the loaded data.
      // Preserve the current widget's contextId so the session selector can find it.
      const newSession = {
        id: logId,
        repoName: effectiveRepoKey,
        contextId,  // Use this widget's contextId so session is visible in context-scoped views
        messages,
        createdAt: new Date(logData.created_at).getTime(),
        updatedAt: new Date(logData.updated_at || logData.created_at).getTime(),
      };

      // Load code blocks if available
      if (serverTurns.length > 0) {
        const hasReferences = serverTurns.some((turn: any) => 
          turn.references && turn.references.length > 0
        );
        
        if (hasReferences) {
          try {
            console.log('📦 Fetching code blocks for saved session...');
            const codeBlocksResponse = await fetch(`/api/chat-logs/${effectiveRepoKey}/${logId}/code-blocks`);

            if (codeBlocksResponse.ok) {
              const codeBlocksData = await codeBlocksResponse.json();

              if (codeBlocksData.code_blocks && codeBlocksData.code_blocks.length > 0) {
                console.log(`✅ Loaded ${codeBlocksData.code_blocks.length} code blocks`);

                // Distribute code blocks to all messages with references
                // This ensures each message has access to its code blocks
                messages.forEach((msg) => {
                  if (msg.role === 'assistant' && msg.metadata?.references?.length > 0) {
                    if (!msg.metadata.code_blocks) {
                      msg.metadata.code_blocks = codeBlocksData.code_blocks;
                    }
                  }
                });
              }
            }
          } catch (error) {
            console.error('❌ Failed to load code blocks:', error);
          }
        }
      }

      // 🔧 Load the session into the store
      // loadSession 会检查是否已存在同 ID 的 session，如果存在则替换
      loadSession(newSession);
      setActiveSession(effectiveRepoKey, logId);
      console.log('✅ Session loaded and activated:', logId);
      console.log('📝 Future messages will be appended to log file:', `${effectiveRepoKey}/${logId}.json`);

      // Expand the widget to show full view
      setIsExpanded(true);

      // Close the saved logs dialog
      setShowSavedLogs(false);
    } catch (error) {
      console.error('Failed to load saved log:', error);
    }
  }, [effectiveRepoKey, contextId, session, loadSession, setActiveSession, setIsExpanded, setShowSavedLogs]);

  const handleDeleteSavedLog = useCallback(async (logId: string, e: React.MouseEvent) => {
    e.stopPropagation(); // Prevent triggering the load action

    try {
      console.log('🗑️ Deleting saved log:', logId);

      const response = await fetch(`/api/chat-logs/${effectiveRepoKey}/${logId}`, {
        method: 'DELETE',
      });

      if (!response.ok) {
        throw new Error('Failed to delete chat log');
      }

      // Remove from local state
      setSavedLogs(prev => prev.filter(log => log.id !== logId));

      // If the deleted log is the current active session, clear it
      const currentSession = session || useChatStore.getState().getActiveSession(effectiveRepoKey);
      if (currentSession?.id === logId) {
        clearMessages(logId);
        createSession(effectiveRepoKey, 'new', undefined, contextId);
      }

      console.log('✅ Chat log deleted successfully');
    } catch (error) {
      console.error('Failed to delete saved log:', error);
    }
  }, [effectiveRepoKey, session, clearMessages, createSession]);

  // Drag start handler
  const handleDragStart = useCallback((e: React.MouseEvent) => {
    if (isExpanded) return;
    // Prevent button clicks from triggering drag
    if ((e.target as HTMLElement).closest('button')) return;

    e.preventDefault();
    setIsDragging(true);

    const container = containerRef.current;
    if (!container) return;

    // Get current actual position
    const rect = container.getBoundingClientRect();
    const currentX = rect.left;
    const currentY = rect.top;

    dragStartRef.current = {
      x: e.clientX,
      y: e.clientY,
      posX: currentX,
      posY: currentY,
    };

    // Immediately switch to left/top positioning mode
    container.style.right = 'auto';
    container.style.bottom = 'auto';
    container.style.left = `${currentX}px`;
    container.style.top = `${currentY}px`;
  }, [isExpanded]);

  // Drag move and end - directly manipulate DOM to avoid re-render
  useEffect(() => {
    if (!isDragging) return;

    const container = containerRef.current;
    if (!container) return;

    const handleMouseMove = (e: MouseEvent) => {
      const deltaX = e.clientX - dragStartRef.current.x;
      const deltaY = e.clientY - dragStartRef.current.y;

      let newX = dragStartRef.current.posX + deltaX;
      let newY = dragStartRef.current.posY + deltaY;

      // Boundary detection
      const width = container.offsetWidth;
      const height = container.offsetHeight;
      newX = Math.max(0, Math.min(window.innerWidth - width, newX));
      newY = Math.max(0, Math.min(window.innerHeight - height, newY));

      // Directly manipulate DOM, no re-render
      container.style.left = `${newX}px`;
      container.style.top = `${newY}px`;

      // Update ref for later saving
      positionRef.current = { x: newX, y: newY };
    };

    const handleMouseUp = () => {
      setIsDragging(false);
      // Update state and save on drag end
      const finalPos = positionRef.current;
      setPosition(finalPos);
      localStorage.setItem('floating-chat-position', JSON.stringify(finalPos));
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging]);

  // Resize start handler
  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    if (isExpanded) return;
    e.preventDefault();
    e.stopPropagation();
    setIsResizing(true);

    const container = containerRef.current;
    if (!container) return;

    const rect = container.getBoundingClientRect();
    resizeStartRef.current = {
      x: e.clientX,
      y: e.clientY,
      width: container.offsetWidth,
      height: container.offsetHeight,
      left: rect.left,
      top: rect.top,
      winW: window.innerWidth,
      winH: window.innerHeight,
    };

    // Switch to left/top positioning for resize
    container.style.right = 'auto';
    container.style.bottom = 'auto';
    container.style.left = `${rect.left}px`;
    container.style.top = `${rect.top}px`;
  }, [isExpanded]);

  // Resize move and end
  useEffect(() => {
    if (!isResizing) return;

    const container = containerRef.current;
    if (!container) return;

    const handleMouseMove = (e: MouseEvent) => {
      const deltaX = e.clientX - resizeStartRef.current.x;
      const deltaY = e.clientY - resizeStartRef.current.y;

      // Calculate new size with relaxed constraints
      // For top-left resize: dragging left/up increases size, dragging right/down decreases size
      const minWidth = 280;
      const maxWidth = resizeStartRef.current.winW - 24;
      const minHeight = 200;
      const maxHeight = resizeStartRef.current.winH - 24;

      // Width: drag left = increase, drag right = decrease
      const newWidth = Math.max(minWidth, Math.min(maxWidth, resizeStartRef.current.width - deltaX));
      // Height: drag up = increase, drag down = decrease
      const newHeight = Math.max(minHeight, Math.min(maxHeight, resizeStartRef.current.height - deltaY));

      // Calculate new position (moves when size changes)
      const widthDiff = resizeStartRef.current.width - newWidth;
      const heightDiff = resizeStartRef.current.height - newHeight;
      const newLeft = Math.max(0, resizeStartRef.current.left + widthDiff);
      const newTop = Math.max(0, resizeStartRef.current.top + heightDiff);

      // Directly manipulate DOM
      container.style.width = `${newWidth}px`;
      container.style.height = `${newHeight}px`;
      container.style.left = `${newLeft}px`;
      container.style.top = `${newTop}px`;

      // Update refs
      sizeRef.current = { width: newWidth, height: newHeight };
      positionRef.current = { x: newLeft, y: newTop };
    };

    const handleMouseUp = () => {
      setIsResizing(false);
      // Save final size and position
      const finalSize = sizeRef.current;
      const finalPos = positionRef.current;
      setCustomSize(finalSize);
      setPosition(finalPos);
      localStorage.setItem('floating-chat-size', JSON.stringify(finalSize));
      localStorage.setItem('floating-chat-position', JSON.stringify(finalPos));
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isResizing]);

  // Double-click resize handle to reset to default size
  const handleResizeReset = useCallback(() => {
    setCustomSize({ width: null, height: null });
    localStorage.removeItem('floating-chat-size');
  }, []);

  // Close export dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (exportDropdownRef.current && !exportDropdownRef.current.contains(event.target as Node)) {
        setExportDropdownOpen(false);
      }
    };
    if (exportDropdownOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [exportDropdownOpen]);

  // Export chat as markdown
  const handleExportMarkdown = useCallback(async () => {
    if (messages.length === 0) return;

    setExportDropdownOpen(false);
    setIsExporting(true);

    try {
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
      const filename = `${effectiveRepoKey}-chat-${timestamp}`;

      // Collect all assistant messages content
      const allContent = messages
        .filter(m => m.role === 'assistant')
        .map(m => m.content)
        .join('\n\n---\n\n');

      // Collect all references from all messages
      const allRefs = messages
        .flatMap(m => m.metadata?.references || [])
        .map((ref: any) => ({
          name: ref.qualified_name || ref.identifier || ref.name || '',
          file: ref.path || ref.file || '',
          startLine: ref.start_line || 0,
          endLine: ref.end_line || 0,
          ref: ref.identifier || ref.qualified_name || ref.name || '',
          nodeType: ref.nodeType,
          code: ref.code,
          language: ref.language,
          qualified_name: ref.qualified_name,
        }));

      // Use enhanced export with automatic code fetching
      const enriched = await exportWithAllCode({
        title: `Chat - ${effectiveRepoKey}`,
        markdown: allContent,
        repoName: effectiveRepoKey,
        references: allRefs,
        metadata: { timestamp: new Date().toLocaleString() },
        onProgress: (status) => console.log('Export progress:', status),
        inlineCode: exportMode === 'inline',
        collapsibleCode: true,
      });

      downloadMarkdown(enriched, filename);
    } finally {
      setIsExporting(false);
    }
  }, [messages, effectiveRepoKey, exportMode]);

  // Export chat as PDF
  const handleExportPDF = useCallback(async () => {
    if (messages.length === 0 || isExporting) return;

    setExportDropdownOpen(false);
    setIsExporting(true);

    try {
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
      const filename = `${effectiveRepoKey}-chat-${timestamp}`;

      // Collect all assistant messages content
      const allContent = messages
        .filter(m => m.role === 'assistant')
        .map(m => m.content)
        .join('\n\n---\n\n');

      // Collect all references from all messages
      const allRefs = messages
        .flatMap(m => m.metadata?.references || [])
        .map((ref: any) => ({
          name: ref.qualified_name || ref.identifier || ref.name || '',
          file: ref.path || ref.file || '',
          startLine: ref.start_line || 0,
          endLine: ref.end_line || 0,
          ref: ref.identifier || ref.qualified_name || ref.name || '',
          nodeType: ref.nodeType,
          code: ref.code,
          language: ref.language,
          qualified_name: ref.qualified_name,
        }));

      // Use enhanced export with automatic code fetching
      const enriched = await exportWithAllCode({
        title: `Chat - ${effectiveRepoKey}`,
        markdown: allContent,
        repoName: effectiveRepoKey,
        references: allRefs,
        metadata: { timestamp: new Date().toLocaleString() },
        onProgress: (status) => console.log('Export progress:', status),
        inlineCode: exportMode === 'inline',
        collapsibleCode: true,
      });

      // Create temporary HTML element for export
      const tempDiv = await createExportableHTML(enriched, theme);
      document.body.appendChild(tempDiv);

      await downloadPDF(tempDiv, filename);

      document.body.removeChild(tempDiv);
    } catch (error) {
      console.error('Failed to export PDF:', error);
    } finally {
      setIsExporting(false);
    }
  }, [messages, effectiveRepoKey, theme, isExporting, exportMode]);


  // 🔧 Removed this useEffect! It interferes with Doc page's codeBlocks
  // FloatingChatWidget shows its own RepoViewer in expanded mode
  // Instead of modifying global codeBlocks state
  // 
  // Update code blocks from the latest assistant message
  // useEffect(() => {
  //   if (!isExpanded) return;
  //   
  //   // Find the most recent assistant message with code blocks
  //   for (let i = messages.length - 1; i >= 0; i--) {
  //     const message = messages[i];
  //     if (message.role === 'assistant' && message.metadata?.code_blocks) {
  //       setCodeBlocks(message.metadata.code_blocks);
  //       return;
  //     }
  //   }
  //   
  //   // Clear code blocks if no messages have them
  //   setCodeBlocks([]);
  // }, [messages, isExpanded, setCodeBlocks]);

  // Store code blocks for potential export (no display)
  const handleAddCodeBlock = React.useCallback((_block: CodeBlock) => {
    // Code blocks are stored in message metadata; no separate tracking needed
  }, []);

  // Navigate to node — use shared RepoViewer if available, otherwise floating panel
  const handleNavigateToNode = React.useCallback((qualifiedName: string) => {
    if (onNavigateToNodeProp) {
      onNavigateToNodeProp(qualifiedName);
    } else {
      openRepoViewer(effectiveRepoKey, qualifiedName);
    }
  }, [onNavigateToNodeProp, openRepoViewer, effectiveRepoKey]);

  // Responsive size calculation
  const getResponsiveSize = useCallback(() => {
    if (typeof window === 'undefined') return { width: '500px', height: '600px' };

    const width = window.innerWidth;

    // Mobile: fullscreen
    if (width < 768) {
      return {
        position: 'fixed' as const,
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        width: '100vw',
        height: '100vh',
        borderRadius: 0
      };
    }

    // Tablet: 80% width
    if (width < 1024) {
      return {
        width: 'min(600px, 80vw)',
        height: 'min(700px, 80vh)'
      };
    }

    // Desktop: fixed size
    return {
      width: '500px',
      height: '600px'
    };
  }, []);

  const [responsiveSize, setResponsiveSize] = useState(getResponsiveSize());

  useEffect(() => {
    const handleResize = () => setResponsiveSize(getResponsiveSize());
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [getResponsiveSize]);

  const chatIsDocked = embedded || isDocked('chat');

  // Dock target element (for portal)
  const [dockTarget, setDockTarget] = useState<HTMLElement | null>(null);
  useEffect(() => {
    if (chatIsDocked) {
      // Poll briefly for dock container (it may render slightly after)
      const el = document.getElementById('dock-chat-container');
      if (el) { setDockTarget(el); return; }
      const timer = setInterval(() => {
        const found = document.getElementById('dock-chat-container');
        if (found) { setDockTarget(found); clearInterval(timer); }
      }, 100);
      return () => clearInterval(timer);
    } else {
      setDockTarget(null);
    }
  }, [chatIsDocked]);

  if (!isOpen) {
    return null;
  }

  // Use unified theme colors
  const colors = getThemeColors(theme);
  const bgDark = colors.card;
  const bgLight = colors.bg;
  const textDark = colors.text;
  const mutedDark = colors.textSecondary;
  const borderDark = colors.border;
  const hoverBg = colors.bgHover;
  const accentBg = colors.accentBg;
  const accent = colors.accent;

  // Calculate actual width and height to use
  const actualWidth = customSize.width ?? dynamicWidth;
  const actualHeight = customSize.height ?? (typeof responsiveSize.height === 'string' ? parseInt(responsiveSize.height) : 600);

  const containerStyle = isExpanded ? {
          top: '24px',
          left: '24px',
          right: '24px',
          bottom: '24px',
          width: 'auto',
          height: 'auto',
        } : {
          ...(position.x !== null && position.y !== null
            ? { left: position.x, top: position.y }
            : { bottom: '24px', right: '24px' }),
          width: `${actualWidth}px`,
          height: `${actualHeight}px`,
  };

  // Build the outer container style
  const outerStyle: React.CSSProperties = (chatIsDocked && dockTarget) || embedded ? {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
    background: bgDark,
    borderTop: embedded ? 'none' : `1px solid ${borderDark}`,
    minHeight: 0,
    height: '100%',
  } : {
    position: 'fixed',
    ...containerStyle,
    background: bgDark,
    border: `1px solid ${borderDark}`,
    borderRadius: isExpanded ? '16px' : (responsiveSize.borderRadius ?? '16px'),
    boxShadow: theme === 'dark'
      ? '0 20px 60px rgba(0, 0, 0, 0.5), 0 8px 32px rgba(0, 0, 0, 0.3)'
      : '0 20px 60px rgba(0, 0, 0, 0.2), 0 8px 32px rgba(0, 0, 0, 0.1)',
    display: 'flex',
    flexDirection: 'column',
    zIndex: 1000,
    overflow: 'hidden',
    transition: (isDragging || isResizing) ? 'none' : 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
  };

  const chatContent = (
    <div
      ref={containerRef}
      style={outerStyle}
    >
      {/* Header - Compact and Clean */}
      <div
        onMouseDown={chatIsDocked ? undefined : handleDragStart}
        style={{
          padding: '10px 14px',
          borderBottom: `1px solid ${borderDark}`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          background: bgLight,
          gap: '8px',
          cursor: chatIsDocked ? 'default' : (isExpanded ? 'default' : 'move'),
          userSelect: 'none',
        }}
      >
        {/* Left: Resize Handle + Title */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
          {/* Resize Handle - integrated into header (hidden when docked) */}
          {!isExpanded && !chatIsDocked && (
            <div
              onMouseDown={(e) => {
                e.stopPropagation(); // Prevent drag start
                handleResizeStart(e);
              }}
              onDoubleClick={handleResizeReset}
              title="Drag to resize, double-click to reset"
              style={{
                width: '20px',
                height: '20px',
                cursor: 'nwse-resize',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                borderRadius: '4px',
                flexShrink: 0,
                transition: 'opacity 0.15s, background 0.15s',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = hoverBg;
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = 'transparent';
              }}
            >
              <svg width="12" height="12" viewBox="0 0 12 12">
                <path d="M2 10V2h8" fill="none" stroke={textDark} strokeWidth="2" strokeLinecap="round"/>
                <path d="M2 6V2h4" fill="none" stroke={textDark} strokeWidth="2" strokeLinecap="round"/>
              </svg>
            </div>
          )}
          <span style={{ fontSize: '13px', fontWeight: '600', color: textDark, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {repoName ?? 'AtCode'}
          </span>
        </div>

        {/* Right: Actions */}
        <div style={{ display: 'flex', gap: '4px', alignItems: 'center', flexShrink: 0 }}>
          {/* History Button */}
          <button
            onClick={handleOpenSavedLogs}
            style={{
              padding: '6px',
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              color: mutedDark,
              borderRadius: '6px',
              transition: 'all 150ms ease-out',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
            title="History"
            onMouseEnter={(e) => { e.currentTarget.style.background = hoverBg; e.currentTarget.style.color = textDark; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = mutedDark; }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10"/>
              <polyline points="12,6 12,12 16,14"/>
            </svg>
          </button>
          {/* New Chat Button */}
          <button
            onClick={handleClearMessages}
            style={{
              padding: '6px',
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              color: mutedDark,
              borderRadius: '6px',
              transition: 'all 150ms ease-out',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
            title="New chat"
            onMouseEnter={(e) => { e.currentTarget.style.background = hoverBg; e.currentTarget.style.color = textDark; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = mutedDark; }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 5v14M5 12h14"/>
            </svg>
          </button>
          {/* Expand/Collapse Button */}
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            style={{
              padding: '6px',
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              color: mutedDark,
              borderRadius: '6px',
              transition: 'all 150ms ease-out',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
            title={isExpanded ? "Collapse" : "Expand"}
            onMouseEnter={(e) => { e.currentTarget.style.background = hoverBg; e.currentTarget.style.color = textDark; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = mutedDark; }}
          >
            {isExpanded ? (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="6,9 12,15 18,9"/>
              </svg>
            ) : (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="18,15 12,9 6,15"/>
              </svg>
            )}
          </button>
          {/* Copy Link Button */}
          <button
            onClick={() => {
              if (!session?.id) return;
              const url = `${window.location.origin}/chat/${effectiveRepoKey}/${session.id}`;
              // Use clipboard API if available (HTTPS), otherwise fall back to execCommand
              if (navigator.clipboard?.writeText) {
                navigator.clipboard.writeText(url).then(() => {
                  setLinkCopied(true);
                  setTimeout(() => setLinkCopied(false), 2000);
                }).catch(() => {
                  fallbackCopyText(url);
                  setLinkCopied(true);
                  setTimeout(() => setLinkCopied(false), 2000);
                });
              } else {
                fallbackCopyText(url);
                setLinkCopied(true);
                setTimeout(() => setLinkCopied(false), 2000);
              }
            }}
            disabled={!session?.id}
            style={{
              padding: '6px',
              background: 'transparent',
              border: 'none',
              cursor: !session?.id ? 'not-allowed' : 'pointer',
              color: linkCopied ? accent : mutedDark,
              borderRadius: '6px',
              transition: 'all 150ms ease-out',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              opacity: !session?.id ? 0.5 : 1,
            }}
            title={linkCopied ? "Copied!" : "Copy chat link"}
            onMouseEnter={(e) => {
              if (session?.id && !linkCopied) {
                e.currentTarget.style.background = hoverBg;
                e.currentTarget.style.color = textDark;
              }
            }}
            onMouseLeave={(e) => {
              if (!linkCopied) {
                e.currentTarget.style.background = 'transparent';
                e.currentTarget.style.color = mutedDark;
              }
            }}
          >
            {linkCopied ? (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="20,6 9,17 4,12"/>
              </svg>
            ) : (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
                <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
              </svg>
            )}
          </button>
          {/* Export Button */}
          <div ref={exportDropdownRef} style={{ position: 'relative' }}>
            <button
              onClick={() => setExportDropdownOpen(!exportDropdownOpen)}
              disabled={messages.length === 0 || isExporting}
              style={{
                padding: '6px',
                background: 'transparent',
                border: 'none',
                cursor: messages.length === 0 || isExporting ? 'not-allowed' : 'pointer',
                color: (messages.length === 0 || isExporting) ? mutedDark.replace('1)', '0.5)') : mutedDark,
                borderRadius: '6px',
                transition: 'all 150ms ease-out',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                opacity: messages.length === 0 ? 0.5 : 1,
              }}
              title="Export chat"
              onMouseEnter={(e) => {
                if (messages.length > 0 && !isExporting) {
                  e.currentTarget.style.background = hoverBg;
                  e.currentTarget.style.color = textDark;
                }
              }}
              onMouseLeave={(e) => {
                if (!exportDropdownOpen) {
                  e.currentTarget.style.background = 'transparent';
                  e.currentTarget.style.color = mutedDark;
                }
              }}
            >
              {isExporting ? (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="10" strokeDasharray="32" strokeDashoffset="32" style={{ animation: 'spin 1s linear infinite' }} />
                </svg>
              ) : (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                  <polyline points="7,10 12,15 17,10"/>
                  <line x1="12" y1="15" x2="12" y2="3"/>
                </svg>
              )}
            </button>
            {exportDropdownOpen && (
              <div
                style={{
                  position: 'absolute',
                  top: '100%',
                  right: 0,
                  marginTop: '6px',
                  background: bgDark,
                  border: `1px solid ${borderDark}`,
                  borderRadius: '12px',
                  boxShadow: `0 8px 24px ${colors.shadowColor}`,
                  zIndex: 1000,
                  minWidth: '180px',
                  overflow: 'hidden',
                  padding: '4px',
                }}
              >
                {/* Code Mode Selection */}
                <div style={{ padding: '4px 8px', fontSize: '11px', color: mutedDark, fontWeight: 500 }}>
                  Code Placement
                </div>
                <button
                  onClick={() => setExportMode('appendix')}
                  style={{
                    width: '100%',
                    padding: '8px 10px',
                    background: exportMode === 'appendix' ? accentBg : 'transparent',
                    border: 'none',
                    borderRadius: '6px',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    fontSize: '12px',
                    color: exportMode === 'appendix' ? accent : textDark,
                    transition: 'all 150ms ease-out',
                  }}
                  onMouseEnter={(e) => { if (exportMode !== 'appendix') e.currentTarget.style.background = hoverBg; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = exportMode === 'appendix' ? accentBg : 'transparent'; }}
                >
                  <span style={{ fontSize: '14px' }}>📋</span>
                  <span>At end (appendix)</span>
                  {exportMode === 'appendix' && (
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={accent} strokeWidth="2.5" style={{ marginLeft: 'auto' }}>
                      <polyline points="20,6 9,17 4,12"/>
                    </svg>
                  )}
                </button>
                <button
                  onClick={() => setExportMode('inline')}
                  style={{
                    width: '100%',
                    padding: '8px 10px',
                    background: exportMode === 'inline' ? accentBg : 'transparent',
                    border: 'none',
                    borderRadius: '6px',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    fontSize: '12px',
                    color: exportMode === 'inline' ? accent : textDark,
                    transition: 'all 150ms ease-out',
                  }}
                  onMouseEnter={(e) => { if (exportMode !== 'inline') e.currentTarget.style.background = hoverBg; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = exportMode === 'inline' ? accentBg : 'transparent'; }}
                >
                  <span style={{ fontSize: '14px' }}>📝</span>
                  <span>Inline in doc</span>
                  {exportMode === 'inline' && (
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={accent} strokeWidth="2.5" style={{ marginLeft: 'auto' }}>
                      <polyline points="20,6 9,17 4,12"/>
                    </svg>
                  )}
                </button>
                {/* Divider */}
                <div style={{ height: '1px', background: borderDark, margin: '4px 8px' }} />
                {/* Export Options */}
                <button
                  onClick={handleExportMarkdown}
                  style={{
                    width: '100%',
                    padding: '10px 12px',
                    background: 'transparent',
                    border: 'none',
                    borderRadius: '8px',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '10px',
                    fontSize: '13px',
                    color: textDark,
                    transition: 'all 150ms ease-out',
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = hoverBg; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                    <polyline points="14,2 14,8 20,8"/>
                    <line x1="16" y1="13" x2="8" y2="13"/>
                    <line x1="16" y1="17" x2="8" y2="17"/>
                    <polyline points="10,9 9,9 8,9"/>
                  </svg>
                  <span>Markdown (.md)</span>
                </button>
                {/* PDF export hidden for now - uncomment when ready
                <button
                  onClick={handleExportPDF}
                  disabled={isExporting}
                  style={{
                    width: '100%',
                    padding: '10px 12px',
                    background: 'transparent',
                    border: 'none',
                    borderRadius: '8px',
                    cursor: isExporting ? 'not-allowed' : 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '10px',
                    fontSize: '13px',
                    color: isExporting ? mutedDark : textDark,
                    opacity: isExporting ? 0.5 : 1,
                    transition: 'all 150ms ease-out',
                  }}
                  onMouseEnter={(e) => { if (!isExporting) e.currentTarget.style.background = hoverBg; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                    <polyline points="14,2 14,8 20,8"/>
                  </svg>
                  <span>PDF</span>
                </button>
                */}
              </div>
            )}
          </div>
          {/* Dock/Undock Button — hidden in embedded mode */}
          {!embedded && (
          <button
            onClick={() => chatIsDocked ? undockFromSidebar('chat') : dockToSidebar('chat')}
            style={{
              padding: '6px',
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              color: mutedDark,
              borderRadius: '6px',
              transition: 'all 150ms ease-out',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
            title={chatIsDocked ? "Undock (float)" : "Dock to sidebar"}
            onMouseEnter={(e) => { e.currentTarget.style.background = hoverBg; e.currentTarget.style.color = accent; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = mutedDark; }}
          >
            {chatIsDocked ? (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="15 3 21 3 21 9" />
                <polyline points="9 21 3 21 3 15" />
                <line x1="21" y1="3" x2="14" y2="10" />
                <line x1="3" y1="21" x2="10" y2="14" />
              </svg>
            ) : (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="3" width="18" height="18" rx="2" />
                <line x1="15" y1="3" x2="15" y2="21" />
              </svg>
            )}
          </button>
          )}
          {/* Close Button — hidden in embedded mode */}
          {!embedded && (
          <button
            onClick={onToggle}
            style={{
              padding: '6px',
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              color: mutedDark,
              borderRadius: '6px',
              transition: 'all 150ms ease-out',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
            title="Close"
            onMouseEnter={(e) => { e.currentTarget.style.background = hoverBg; e.currentTarget.style.color = textDark; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = mutedDark; }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6L6 18M6 6l12 12"/>
            </svg>
          </button>
          )}
        </div>
      </div>

      {/* Chat Tab Bar - shown when multiple tabs are open */}
      {(() => {
        const rawTabs = openTabs[effectiveRepoKey] || [];
        // Filter tabs to only show sessions matching the current contextId scope
        const repoTabs = rawTabs.filter(tabId => {
          const s = allSessions.find(sess => sess.id === tabId);
          if (!s) return false;
          return contextId ? s.contextId === contextId : !s.contextId;
        });
        if (repoTabs.length <= 1) return null;

        return (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            borderBottom: `1px solid ${borderDark}`,
            overflowX: 'auto',
            background: bgDark,
            minHeight: '30px',
            flexShrink: 0,
            scrollbarWidth: 'none',
          }}>
            {repoTabs.map((tabSessionId, idx) => {
              const isActive = tabSessionId === (activeTabId[effectiveRepoKey] || session?.id);
              const isTabStreaming = streamingSessionIds.includes(tabSessionId);
              const tabSession = allSessions.find(s => s.id === tabSessionId);
              const firstMsg = tabSession?.messages?.[0]?.content;
              const label = firstMsg ? firstMsg.slice(0, 16) + (firstMsg.length > 16 ? '...' : '') : `Chat ${idx + 1}`;

              return (
                <div
                  key={tabSessionId}
                  onClick={() => switchTab(effectiveRepoKey, tabSessionId)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '4px',
                    padding: '4px 10px',
                    cursor: 'pointer',
                    borderBottom: isActive ? `2px solid ${accent}` : '2px solid transparent',
                    color: isActive ? textDark : mutedDark,
                    fontSize: '11px',
                    whiteSpace: 'nowrap',
                    flexShrink: 0,
                    transition: 'color 0.15s',
                    userSelect: 'none',
                  }}
                  onMouseEnter={(e) => { if (!isActive) e.currentTarget.style.color = textDark; }}
                  onMouseLeave={(e) => { if (!isActive) e.currentTarget.style.color = mutedDark; }}
                >
                  {isTabStreaming && (
                    <span style={{
                      width: '6px',
                      height: '6px',
                      borderRadius: '50%',
                      background: accent,
                      animation: 'pulse 1.5s ease-in-out infinite',
                      flexShrink: 0,
                    }} />
                  )}
                  <span>{label}</span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      closeTab(effectiveRepoKey, tabSessionId);
                    }}
                    style={{
                      background: 'transparent',
                      border: 'none',
                      cursor: 'pointer',
                      color: mutedDark,
                      padding: '0 2px',
                      fontSize: '11px',
                      lineHeight: 1,
                      opacity: 0.6,
                      transition: 'opacity 0.15s',
                    }}
                    onMouseEnter={(e) => { e.currentTarget.style.opacity = '1'; }}
                    onMouseLeave={(e) => { e.currentTarget.style.opacity = '0.6'; }}
                    title="Close tab"
                  >
                    x
                  </button>
                </div>
              );
            })}
            <button
              onClick={handleClearMessages}
              style={{
                background: 'transparent',
                border: 'none',
                cursor: 'pointer',
                color: mutedDark,
                padding: '4px 8px',
                fontSize: '13px',
                flexShrink: 0,
                transition: 'color 0.15s',
              }}
              onMouseEnter={(e) => { e.currentTarget.style.color = textDark; }}
              onMouseLeave={(e) => { e.currentTarget.style.color = mutedDark; }}
              title="New chat tab"
            >
              +
            </button>
          </div>
        );
      })()}

      {/* Main Content Area - Dual column when expanded, single column otherwise */}
      <div
        style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          gap: '0',
          overflow: 'hidden',
          padding: isExpanded ? '16px' : '0',
          background: bgLight,
          minHeight: 0,
        }}
      >
        {/* Messages Column */}
        <div
          ref={messagesContainerRef}
          style={{
            display: 'flex',
            flexDirection: 'column',
            overflowY: 'auto',
            padding: isExpanded ? '0' : '16px',
            gap: '12px',
            minHeight: 0,
            userSelect: 'text',
            WebkitUserSelect: 'text',
            overflowAnchor: 'none',
          }}
        >
          {messages.length === 0 && !isStreaming && (
            <ChatEmptyState theme={theme} />
          )}

          {/* Limit rendered messages to last 50 for performance */}
          {messages.length > 50 && !showAllMessages && (
            <button
              onClick={() => setShowAllMessages(true)}
              style={{
                padding: '8px 16px',
                background: 'transparent',
                border: `1px solid ${borderDark}`,
                borderRadius: '8px',
                color: accent,
                cursor: 'pointer',
                fontSize: '12px',
                alignSelf: 'center',
              }}
            >
              Show {messages.length - 50} earlier messages
            </button>
          )}

          {(showAllMessages ? messages : messages.slice(-50)).map((message, idx, arr) => {
            // Provide retry handler on the last assistant error message
            const isLastMsg = idx === arr.length - 1;
            const isError = message.role === 'assistant' && message.content.startsWith('Error:');
            const retryHandler = (isLastMsg && isError && !isStreaming) ? () => {
              // Find the last user message to re-submit
              const lastUserMsg = [...messages].reverse().find(m => m.role === 'user');
              if (lastUserMsg && session) {
                // Remove the error message
                const store = useChatStore.getState();
                store.removeLastMessage(session.id);
                // Re-submit
                setInput(lastUserMsg.content);
                setTimeout(() => {
                  const fakeEvent = { preventDefault: () => {} } as React.FormEvent;
                  handleSubmit(fakeEvent);
                }, 50);
              }
            } : undefined;

            return (
              <ChatMessage
                key={message.id}
                message={message}
                bgDark={bgDark}
                textDark={textDark}
                borderDark={borderDark}
                accent={accent}
                theme={theme}
                onAddCodeBlock={handleAddCodeBlock}
                onNavigateToNode={handleNavigateToNode}
                isStreaming={isStreaming}
                repoName={repoName}
                onRetry={retryHandler}
                editingMessageId={editingMessageId}
                editingContent={editingContent}
                onStartEdit={(id, content) => { setEditingMessageId(id); setEditingContent(content); }}
                onEditChange={setEditingContent}
                onEditSubmit={handleEditAndRegenerate}
                onEditCancel={() => setEditingMessageId(null)}
              />
            );
          })}

          {/* Streaming message */}
          {isStreaming && session?.id && (() => {
            const hasContent = currentStreamingContent || currentToolCall || toolCallHistory.length > 0;
            return (
              <div style={{ display: 'flex', justifyContent: 'flex-start', minWidth: 0 }}>
                <div
                  style={{
                    maxWidth: '90%',
                    minWidth: 0,
                    padding: '10px 14px',
                    borderRadius: '12px',
                    fontSize: '13px',
                    background: bgDark,
                    color: textDark,
                    border: `1px solid ${borderDark}`,
                    wordWrap: 'break-word',
                    overflowWrap: 'break-word',
                    userSelect: 'text',
                    cursor: 'text',
                  }}
                >
                  {/* Thinking indicator — shown when streaming but no content/tool calls yet */}
                  {!hasContent && (
                    <div style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '8px',
                      color: mutedDark,
                      fontSize: '12px',
                      padding: '4px 0',
                    }}>
                      <svg
                        width="14"
                        height="14"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        style={{ animation: 'spin 1s linear infinite' }}
                      >
                        <circle cx="12" cy="12" r="10" strokeOpacity="0.25" />
                        <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
                      </svg>
                      <span>Thinking...</span>
                    </div>
                  )}
                  {toolCallHistory.length > 0 && (
                    <div style={{
                      marginBottom: currentStreamingContent ? '10px' : '6px',
                      border: `1px solid ${borderDark}`,
                      borderRadius: '10px',
                      overflow: 'hidden',
                    }}>
                      <button
                        type="button"
                        onClick={() => {
                          if (session?.id) {
                            useChatStore.getState().setStreamSessionState(session.id, { traceExpanded: !streamToolTraceExpanded });
                          }
                        }}
                        style={{
                          width: '100%',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          gap: '8px',
                          padding: '8px 10px',
                          border: 'none',
                          background: 'transparent',
                          color: textDark,
                          cursor: 'pointer',
                          fontSize: '12px',
                        }}
                      >
                        <span style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
                          <span style={{ fontWeight: 600 }}>Trace</span>
                          <span style={{ color: mutedDark }}>
                            {toolCallHistory.length} step{toolCallHistory.length > 1 ? 's' : ''}
                          </span>
                        </span>
                        <span style={{ color: mutedDark, fontSize: '11px' }}>
                          {streamToolTraceExpanded ? 'Hide' : 'Show'}
                        </span>
                      </button>

                      {streamToolTraceExpanded && (
                        <div style={{ padding: '0 10px 10px 10px' }}>
                          <TraceViewer
                            nodes={adaptStreamingToolTrace(toolCallHistory, currentToolCall, session?.id || '')}
                            theme={theme}
                            compact
                          />
                        </div>
                      )}
                    </div>
                  )}

                  {/* Streaming content */}
                  {currentStreamingContent && (
                    <WikiDoc
                      markdown={currentStreamingContent}
                      layoutMode="split"
                      isStreaming={true}
                      repoName={repoName}
                      onNavigateToNode={handleNavigateToNode}
                      onNavigateToPaper={(paperId) => {
                        window.open(`/repos/papers?paper=${encodeURIComponent(paperId)}&tab=daily`, '_blank');
                      }}
                    />
                  )}

                  {/* Typing indicator */}
                  <div style={{
                    marginTop: currentStreamingContent ? '8px' : '0',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '4px'
                  }}>
                    <div
                      style={{
                        width: '6px',
                        height: '6px',
                        background: colors.textMuted,
                        borderRadius: '50%',
                        animation: 'pulse 1.5s ease-in-out infinite',
                      }}
                    />
                    <div
                      style={{
                        width: '6px',
                        height: '6px',
                        background: colors.textMuted,
                        borderRadius: '50%',
                        animation: 'pulse 1.5s ease-in-out 0.2s infinite',
                      }}
                    />
                    <div
                      style={{
                        width: '6px',
                        height: '6px',
                        background: colors.textMuted,
                        borderRadius: '50%',
                        animation: 'pulse 1.5s ease-in-out 0.4s infinite',
                      }}
                    />
                  </div>
                </div>
              </div>
            );
          })()}

          <div ref={messagesEndRef} />
        </div>

      </div>

      {/* Input Area - Rounded Rectangle Design */}
      <div style={{ borderTop: `1px solid ${borderDark}` }}>
        <form onSubmit={handleSubmit}>
          <div style={{ background: bgDark }}>
            {/* Document Context Display — only show when there's meaningful context (operator doc, selected text, file path, or source paper) */}
            {session?.documentContext && (session.documentContext.operatorName || session.documentContext.selectedText || session.documentContext.filePath || session.documentContext.sourcePaperId) && (
              <div
                style={{
                  padding: '8px 12px',
                  background: theme === 'dark' ? '#0d1117' : '#f6f8fa',
                  borderBottom: `1px solid ${borderDark}`,
                  fontSize: '13px',
                  color: mutedDark,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                }}
              >
                <div style={{ flex: 1, overflow: 'hidden' }}>
                  <div style={{ fontWeight: '600', marginBottom: '2px' }}>
                    📄 Context: {session.documentContext.documentTitle || session.documentContext.filePath || 'Selected content'}
                  </div>
                  {session.documentContext.selectedText && (
                    <div
                      style={{
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        fontStyle: 'italic',
                      }}
                    >
                      "{session.documentContext.selectedText.substring(0, 100)}..."
                    </div>
                  )}
                  {session.documentContext.sourcePaperId && (
                    <div style={{ fontSize: '12px', opacity: 0.8, marginTop: '2px' }}>
                      Paper: {session.documentContext.sourcePaperTitle || session.documentContext.sourcePaperId}
                    </div>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => {
                    if (session?.id) {
                      clearDocumentContext(session.id);
                    }
                  }}
                  style={{
                    background: 'transparent',
                    border: 'none',
                    cursor: 'pointer',
                    padding: '2px 6px',
                    color: mutedDark,
                    fontSize: '14px',
                  }}
                  title="Clear context"
                >
                  ✕
                </button>
              </div>
            )}

            {/* Top Input Area */}
            <div style={{ padding: '12px' }}>
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                onCompositionStart={() => { isComposingRef.current = true; }}
                onCompositionEnd={() => { requestAnimationFrame(() => { isComposingRef.current = false; }); }}
                placeholder="Ask about this codebase... (Shift+Enter for newline)"
                disabled={isStreaming}
                style={{
                  width: '100%',
                  background: 'transparent',
                  border: 'none',
                  resize: 'none',
                  outline: 'none',
                  fontSize: '13px',
                  color: textDark,
                  minHeight: '40px',
                  maxHeight: '200px',
                  fontFamily: 'inherit',
                  overflow: 'auto',
                }}
              />
            </div>

            {/* Bottom Controls - Clean layout with Model + Mode + Send/Stop */}
            <div
              style={{
                padding: '8px 12px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: '8px',
                borderTop: `1px solid ${borderDark}`,
              }}
            >
              {/* Left: Model & Mode selectors */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                {/* Model Selector - Editable combobox */}
                <ModelCombobox
                  value={selectedModel}
                  onChange={setSelectedModel}
                  disabled={isStreaming}
                  theme={theme}
                  showTiers={true}
                  tiers={tiers}
                  style={{ minWidth: '140px' }}
                />
              </div>

              {/* Right: Send/Stop Button */}
              {isStreaming ? (
                <button
                  type="button"
                  onClick={() => {
                    const activeSessionId = session?.id;
                    if (activeSessionId) {
                      stopStream(activeSessionId, effectiveRepoKey);
                      console.log('🛑 Chat interrupted via stop button');
                    }
                  }}
                  style={{
                    width: '32px',
                    height: '32px',
                    borderRadius: '50%',
                    background: colors.error,
                    border: 'none',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    transition: 'all 150ms ease-out',
                    flexShrink: 0,
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = colors.errorBorder;
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = colors.error;
                  }}
                  title="Stop generating"
                >
                  <svg
                    width="14"
                    height="14"
                    fill="#ffffff"
                    viewBox="0 0 24 24"
                  >
                    <rect x="6" y="6" width="12" height="12" rx="2" />
                  </svg>
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!input.trim()}
                  style={{
                    width: '32px',
                    height: '32px',
                    borderRadius: '50%',
                    background: !input.trim() ? colors.textDimmed : accent,
                    border: 'none',
                    cursor: !input.trim() ? 'not-allowed' : 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    transition: 'all 150ms ease-out',
                    flexShrink: 0,
                  }}
                  onMouseEnter={(e) => {
                    if (input.trim()) {
                      e.currentTarget.style.background = colors.accentHover;
                    }
                  }}
                  onMouseLeave={(e) => {
                    if (input.trim()) {
                      e.currentTarget.style.background = accent;
                    }
                  }}
                  title="Send message"
                >
                  <svg
                    width="16"
                    height="16"
                    fill="none"
                    stroke="#ffffff"
                    viewBox="0 0 24 24"
                    strokeWidth={2.5}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M6 12h12m0 0l-5-5m5 5l-5 5"
                    />
                  </svg>
                </button>
              )}
            </div>
          </div>
        </form>
      </div>

      {/* Saved Logs Browser Dialog */}
      {showSavedLogs && (
        <div
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: 'rgba(0, 0, 0, 0.5)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 2000,
          }}
          onClick={() => setShowSavedLogs(false)}
        >
          <div
            style={{
              background: bgDark,
              border: `1px solid ${borderDark}`,
              borderRadius: '12px',
              padding: '24px',
              maxWidth: '600px',
              width: '90%',
              maxHeight: '70vh',
              overflow: 'hidden',
              display: 'flex',
              flexDirection: 'column',
              boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.3)',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ fontSize: '16px', fontWeight: '600', color: textDark, margin: '0 0 16px' }}>
              📂 Saved Chat Logs
            </h3>
            
            {loadingLogs ? (
              <div style={{ padding: '48px', textAlign: 'center', color: mutedDark }}>
                <div
                  style={{
                    width: '32px',
                    height: '32px',
                    border: '3px solid rgba(59, 130, 246, 0.3)',
                    borderTopColor: '#3b82f6',
                    borderRadius: '50%',
                    animation: 'spin 0.8s linear infinite',
                    margin: '0 auto 16px',
                  }}
                />
                Loading saved logs...
              </div>
            ) : savedLogs.length === 0 ? (
              <div style={{ padding: '48px', textAlign: 'center', color: mutedDark }}>
                <div style={{ fontSize: '48px', marginBottom: '16px' }}>📭</div>
                <p style={{ fontSize: '14px', margin: 0 }}>No saved chat logs found</p>
              </div>
            ) : (
              <div
                style={{
                  flex: 1,
                  overflowY: 'auto',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '12px',
                }}
              >
                {savedLogs.map((log) => (
                  <div
                    key={log.id}
                    onClick={() => handleLoadSavedLog(log.id)}
                    style={{
                      padding: '12px',
                      background: bgLight,
                      border: `1px solid ${borderDark}`,
                      borderRadius: '8px',
                      cursor: 'pointer',
                      transition: 'all 0.2s',
                      position: 'relative',
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.background = theme === 'dark' ? '#2d3748' : '#e2e8f0';
                      e.currentTarget.style.borderColor = '#3b82f6';
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = bgLight;
                      e.currentTarget.style.borderColor = borderDark;
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '8px' }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: '13px', fontWeight: '600', color: textDark, marginBottom: '6px' }}>
                          {log.query.length > 70 ? log.query.substring(0, 70) + '...' : log.query}
                        </div>
                        <div style={{ display: 'flex', gap: '8px', fontSize: '11px', color: mutedDark, flexWrap: 'wrap' }}>
                          {log.timestamp && (
                            <span>🕐 {new Date(log.timestamp).toLocaleString()}</span>
                          )}
                          {log.turns_count > 1 && (
                            <span>💬 {log.turns_count} turns</span>
                          )}
                          {log.code_blocks_count > 0 && (
                            <span>📦 {log.code_blocks_count} blocks</span>
                          )}
                          {log.references_count > 0 && (
                            <span>🔗 {log.references_count} refs</span>
                          )}
                          {log.tool_calls > 0 && (
                            <span>🔍 {log.tool_calls} searches</span>
                          )}
                        </div>
                      </div>
                      <button
                        onClick={(e) => handleDeleteSavedLog(log.id, e)}
                        style={{
                          background: 'transparent',
                          border: 'none',
                          cursor: 'pointer',
                          padding: '4px 6px',
                          color: mutedDark,
                          fontSize: '14px',
                          borderRadius: '4px',
                          transition: 'all 0.15s',
                          flexShrink: 0,
                        }}
                        onMouseEnter={(e) => {
                          e.currentTarget.style.background = theme === 'dark' ? '#4a1d1d' : '#fee2e2';
                          e.currentTarget.style.color = '#ef4444';
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.background = 'transparent';
                          e.currentTarget.style.color = mutedDark;
                        }}
                        title="Delete this chat log"
                      >
                        🗑️
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
            
            <div style={{ marginTop: '16px', display: 'flex', justifyContent: 'flex-end' }}>
              <button
                onClick={() => setShowSavedLogs(false)}
                style={{
                  padding: '8px 16px',
                  background: bgLight,
                  border: `1px solid ${borderDark}`,
                  borderRadius: '6px',
                  color: textDark,
                  fontSize: '14px',
                  fontWeight: '500',
                  cursor: 'pointer',
                  transition: 'background 0.2s',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = theme === 'dark' ? '#2d3748' : '#e2e8f0';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = bgLight;
                }}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      <style jsx>{`
        @keyframes pulse {
          0%, 100% { opacity: 0.3; }
          50% { opacity: 1; }
        }
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
      <style jsx global>{`
        .chat-message-user ::selection {
          background: #ffffff;
          color: #374151;
        }
        .chat-message-user ::-moz-selection {
          background: #ffffff;
          color: #374151;
        }
        .chat-message-assistant ::selection {
          background: rgba(59, 130, 246, 0.4);
          color: inherit;
        }
        .chat-message-assistant ::-moz-selection {
          background: rgba(59, 130, 246, 0.4);
          color: inherit;
        }
        /* Theme-aware select dropdown styles */
        .chat-select:hover {
          border-color: #3b82f6 !important;
        }
        .chat-select:focus {
          border-color: #3b82f6 !important;
          box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.2);
        }
        .chat-select:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }
        /* Dark theme dropdown options */
        [data-theme="dark"] .chat-select option,
        [data-theme="dark"] .chat-select optgroup {
          background: #21262d;
          color: #c9d1d9;
        }
        /* Light theme dropdown options */
        [data-theme="light"] .chat-select option,
        [data-theme="light"] .chat-select optgroup {
          background: #ffffff;
          color: #24292f;
        }
      `}</style>
    </div>
  );

  // Embedded mode: render inline, no portal
  if (embedded) {
    return chatContent;
  }
  // Portal into dock container when docked, otherwise render inline
  if (chatIsDocked && dockTarget) {
    return createPortal(chatContent, dockTarget);
  }
  return chatContent;
}

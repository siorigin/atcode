// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { create } from 'zustand';
import { getChatStorageKey, getUserId } from './user-identity';

export interface CodeBlock {
  id: string;
  file: string;
  startLine: number;
  endLine: number;
  code: string;
  language: string;
}

export interface ToolTraceItem {
  tool: string;
  key_arg: string;
  result?: string;
  preview?: string[];
}

interface WikiState {
  codeBlocks: CodeBlock[];
  activeBlockId: string | null;
  pendingHash: string | null;

  setCodeBlocks: (blocks: CodeBlock[]) => void;
  setActiveBlock: (blockId: string) => void;
  activateBlockByReference: (file: string, startLine: number, endLine: number) => void;
  setActiveBlockFromUrl: (blockId: string) => void;
  initializeFromHash: () => void;
  processPendingHash: () => void;
  addCodeBlock: (block: CodeBlock) => void;
}

export const useWikiStore = create<WikiState>((set, get) => ({
  codeBlocks: [],
  activeBlockId: null,
  pendingHash: null,

  setCodeBlocks: (blocks) => {
    // Handle undefined or null blocks
    const safeBlocks = blocks || [];
    
    // Deduplicate blocks by ID - keep the first occurrence
    const uniqueBlocks = safeBlocks.reduce((acc: CodeBlock[], block: CodeBlock) => {
      if (!acc.some(b => b.id === block.id)) {
        acc.push(block);
      } else {
        console.warn('⚠️ Duplicate block ID detected and removed:', block.id);
      }
      return acc;
    }, []);
    
    console.log('📦 Setting code blocks:', uniqueBlocks.length, '(removed', safeBlocks.length - uniqueBlocks.length, 'duplicates)');
    set({ codeBlocks: uniqueBlocks });
    get().processPendingHash();
  },

  addCodeBlock: (block) => {
    const { codeBlocks } = get();
    // Check if block already exists to avoid duplicates
    if (!codeBlocks.some(b => b.id === block.id)) {
      console.log('📦 Adding code block:', block.id);
      set({ codeBlocks: [...codeBlocks, block] });
    }
  },

  setActiveBlock: (blockId) => {
    console.log('✨ Activating block:', blockId);
    set({ activeBlockId: blockId });
  },

  setActiveBlockFromUrl: (blockId) => {
    console.log('🔗 Activating block from URL:', blockId);
    set({ activeBlockId: blockId });

    // Update URL hash to maintain shareable links
    if (typeof window !== 'undefined') {
      window.history.replaceState(null, '', `#${blockId}`);
    }
  },

  activateBlockByReference: (file, startLine, endLine) => {
    const { codeBlocks } = get();
    console.log('🔍 Looking for block:', { file, startLine, endLine });
    console.log('📦 Available code blocks:', codeBlocks.map(b => ({
      id: b.id,
      file: b.file,
      lines: `${b.startLine}-${b.endLine}`
    })));

    // Try exact match first
    let block = codeBlocks.find(
      b => b.file === file && b.startLine === startLine && b.endLine === endLine
    );

    // If not found, try flexible matching (in case file path format differs)
    if (!block) {
      console.log('⚠️ Exact match failed, trying flexible matching...');
      block = codeBlocks.find(
        b => (b.file.endsWith(file) || file.endsWith(b.file) || b.file.includes(file) || file.includes(b.file)) &&
             b.startLine === startLine &&
             b.endLine === endLine
      );
    }

    if (block) {
      console.log('✅ Found and activating block:', block.id);
      get().setActiveBlock(block.id);

      // No longer modifying URL hash to avoid cross-page interference
      // URL hash is now managed by each page individually
    } else {
      console.warn('⚠️ Block not found:', { file, startLine, endLine });
      console.log('Available blocks:', codeBlocks.map(b => ({
        id: b.id,
        file: b.file,
        lines: `${b.startLine}-${b.endLine}`
      })));
    }
  },

  initializeFromHash: () => {
    if (typeof window !== 'undefined' && window.location.hash) {
      const hash = window.location.hash.substring(1);
      console.log('🔗 Initializing from hash:', hash);
      set({ pendingHash: hash });
      get().processPendingHash();
    }
  },

  processPendingHash: () => {
    const { pendingHash, codeBlocks } = get();
    if (!pendingHash || codeBlocks.length === 0) return;

    const block = codeBlocks.find(b => b.id === pendingHash);
    if (block) {
      console.log('🔗 Processing pending hash:', pendingHash);
      get().setActiveBlockFromUrl(pendingHash);
      set({ pendingHash: null });
    } else if (codeBlocks.length > 0) {
      // Hash block not found, activate first block
      console.log('🔗 Hash block not found, activating first block');
      get().setActiveBlockFromUrl(codeBlocks[0].id);
      set({ pendingHash: null });
    }
  },
}));

export interface GenerationJob {
  id: string;
  type: 'repo' | 'research';
  name: string;
  repoName?: string; // For research jobs
  status: 'pending' | 'running' | 'completed' | 'failed';
  progress: number; // 0-100
  currentStep?: string;
  command?: string;
  logs?: string[];
  error?: string;
  startTime: number;
  endTime?: number;
}

interface GenerationState {
  jobs: GenerationJob[];
  addJob: (job: Omit<GenerationJob, 'startTime'>) => void;
  updateJob: (id: string, updates: Partial<GenerationJob>) => void;
  removeJob: (id: string) => void;
  getJob: (id: string) => GenerationJob | undefined;
}

export const useGenerationStore = create<GenerationState>((set, get) => ({
  jobs: [],

  addJob: (job) => {
    const newJob: GenerationJob = {
      ...job,
      startTime: Date.now(),
    };
    set((state) => ({ jobs: [...state.jobs, newJob] }));
  },

  updateJob: (id, updates) => {
    set((state) => ({
      jobs: state.jobs.map((job) =>
        job.id === id ? { ...job, ...updates } : job
      ),
    }));
  },

  removeJob: (id) => {
    set((state) => ({
      jobs: state.jobs.filter((job) => job.id !== id),
    }));
  },

  getJob: (id) => {
    return get().jobs.find((job) => job.id === id);
  },
}));

// Chat state management
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: number;
  metadata?: {
    toolCalls?: number;
    exploredNodes?: number;
    isStreaming?: boolean;
    interrupted?: boolean;
    toolTrace?: ToolTraceItem[];
    references?: Array<{
      identifier: string;
      qualified_name: string;
      name: string;
      type: string;
      path: string | null;
      start_line: number | null;
      end_line: number | null;
      ref: string;
    }>;
    code_blocks?: CodeBlock[];
  };
}

export interface DocumentContext {
  filePath?: string;
  startLine?: number;
  endLine?: number;
  selectedText?: string;
  documentTitle?: string;
  fullContent?: string;
  // Lightweight metadata (Phase 4) — replaces fullContent injection
  pageType?: string;
  operatorName?: string;
  // Paper linking — set when repo was built from a paper
  sourcePaperId?: string;
  sourcePaperTitle?: string;
}

export interface ChatSession {
  id: string;
  repoName: string;
  contextId?: string; // Optional: identifies the specific context (e.g. operator name)
  messages: ChatMessage[];
  createdAt: number;
  updatedAt: number;
  documentContext?: DocumentContext;
  paperIds?: string[];  // Paper IDs discussed in this session
  title?: string;       // Display title (auto-set from first message)
}

export interface StreamSessionState {
  currentToolCall: string | null;
  toolCallHistory: ToolTraceItem[];
  traceExpanded: boolean;
  autoCollapsed: boolean;
}

interface ChatState {
  sessions: ChatSession[];
  // Repository-scoped active sessions
  // Key: repoName, Value: sessionId
  activeSessionIds: Record<string, string>;
  isStreaming: boolean; // Backward compat: true if any session is streaming
  streamingSessionIds: string[]; // Per-session streaming tracking
  streamingContent: Record<string, string>; // Key: `${repoName}:${sessionId}`

  // Per-session stream UI state (transient, NOT persisted)
  streamSessionStates: Record<string, StreamSessionState>;
  setStreamSessionState: (sessionId: string, updates: Partial<StreamSessionState>) => void;
  clearStreamSessionState: (sessionId: string) => void;

  // Tab management
  openTabs: Record<string, string[]>; // repoName -> ordered list of open tab session IDs
  activeTabId: Record<string, string>; // repoName -> currently focused tab session ID

  // Session management
  createSession: (repoName: string, sessionId?: string, documentContext?: DocumentContext, contextId?: string) => string;
  getSession: (sessionId: string) => ChatSession | undefined;
  findSession: (repoName: string, contextId?: string) => ChatSession | undefined;
  getActiveSession: (repoName: string) => ChatSession | undefined;
  setActiveSession: (repoName: string, sessionId: string) => void;
  clearActiveSession: (repoName: string) => void;
  deleteSession: (sessionId: string) => void;
  loadSession: (session: ChatSession) => void; // Load a complete session from data
  setDocumentContext: (sessionId: string, context: DocumentContext) => void;
  clearDocumentContext: (sessionId: string) => void;

  // Message management
  addMessage: (sessionId: string, message: Omit<ChatMessage, 'id' | 'timestamp'>) => void;
  updateMessage: (sessionId: string, messageId: string, updates: Partial<ChatMessage>) => void;
  removeLastMessage: (sessionId: string) => void;
  truncateMessages: (sessionId: string, fromIndex: number) => void;
  clearMessages: (sessionId: string) => void;

  // Streaming state
  setStreaming: (isStreaming: boolean) => void; // Legacy: sets/clears global streaming
  setSessionStreaming: (sessionId: string, isStreaming: boolean) => void; // Per-session streaming
  isSessionStreaming: (sessionId: string) => boolean;
  setStreamingContent: (repoName: string, sessionId: string, content: string) => void;
  appendStreamingContent: (repoName: string, sessionId: string, content: string) => void;
  clearStreamingContent: (repoName: string, sessionId: string) => void;
  getStreamingContent: (repoName: string, sessionId: string) => string;

  // Tab actions
  openTab: (repoName: string, sessionId: string) => void;
  closeTab: (repoName: string, sessionId: string) => void;
  switchTab: (repoName: string, sessionId: string) => void;
  getOpenTabs: (repoName: string) => string[];

  // Paper session helpers
  addPaperToSession: (sessionId: string, paperId: string) => void;
  setSessionTitle: (sessionId: string, title: string) => void;
}

// Helper function to create streaming content key
const getStreamingKey = (repoName: string, sessionId: string): string => {
  return `${repoName}:${sessionId}`;
};

// Helper to load from localStorage with user isolation
const loadChatState = (): { sessions: ChatSession[]; activeSessionIds: Record<string, string>; openTabs: Record<string, string[]>; activeTabId: Record<string, string> } => {
  if (typeof window === 'undefined') {
    return { sessions: [], activeSessionIds: {}, openTabs: {}, activeTabId: {} };
  }

  try {
    const storageKey = getChatStorageKey();
    const stored = localStorage.getItem(storageKey);

    if (stored) {
      const parsed = JSON.parse(stored);
      console.log(`📂 Loaded chat state from ${storageKey}:`, {
        sessionCount: parsed.sessions?.length || 0,
        activeSessionIds: parsed.activeSessionIds || {}
      });

      return {
        sessions: parsed.sessions || [],
        activeSessionIds: parsed.activeSessionIds || {},
        openTabs: parsed.openTabs || {},
        activeTabId: parsed.activeTabId || {},
      };
    }
  } catch (e) {
    console.error('Failed to load chat state from localStorage:', e);
  }

  return { sessions: [], activeSessionIds: {}, openTabs: {}, activeTabId: {} };
};

// Maximum number of sessions to store
const MAX_STORED_SESSIONS = 20;

// Clean session data to reduce storage size (remove code_blocks as they can be re-fetched from server)
const cleanSessionForStorage = (session: ChatSession): ChatSession => {
  return {
    ...session,
    messages: session.messages.map(msg => ({
      ...msg,
      metadata: msg.metadata ? {
        ...msg.metadata,
        code_blocks: undefined,  // Don't store code_blocks, too large
      } : undefined
    }))
  };
};

// Clean and limit session count
const prepareStateForStorage = (state: { sessions: ChatSession[]; activeSessionIds: Record<string, string>; openTabs?: Record<string, string[]>; activeTabId?: Record<string, string> }) => {
  // Sort by update time, keep most recent sessions
  const sortedSessions = [...state.sessions]
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .slice(0, MAX_STORED_SESSIONS);

  // Clean large data from each session
  const cleanedSessions = sortedSessions.map(cleanSessionForStorage);

  // Filter openTabs to only include sessions that still exist
  const sessionIds = new Set(cleanedSessions.map(s => s.id));
  const cleanedOpenTabs: Record<string, string[]> = {};
  if (state.openTabs) {
    for (const [repo, tabs] of Object.entries(state.openTabs)) {
      const validTabs = tabs.filter(id => sessionIds.has(id));
      if (validTabs.length > 0) {
        cleanedOpenTabs[repo] = validTabs;
      }
    }
  }

  return {
    sessions: cleanedSessions,
    activeSessionIds: state.activeSessionIds,
    openTabs: cleanedOpenTabs,
    activeTabId: state.activeTabId || {},
    savedAt: Date.now(),
    userId: getUserId(), // Include for debugging
  };
};

// Helper to save to localStorage with debouncing
let saveTimeout: NodeJS.Timeout | null = null;
const debouncedSaveChatState = (state: { sessions: ChatSession[]; activeSessionIds: Record<string, string>; openTabs?: Record<string, string[]>; activeTabId?: Record<string, string> }) => {
  if (typeof window === 'undefined') return;

  // Clear existing timeout
  if (saveTimeout) {
    clearTimeout(saveTimeout);
  }

  // Debounce saves to reduce localStorage writes
  saveTimeout = setTimeout(() => {
    try {
      const storageKey = getChatStorageKey();
      const cleanedState = prepareStateForStorage(state);
      localStorage.setItem(storageKey, JSON.stringify(cleanedState));
      console.log(`💾 Saved chat state to ${storageKey}`);
    } catch (e) {
      console.error('Failed to save chat state to localStorage:', e);
      // When quota exceeded, try to clean old data
      if (e instanceof DOMException && e.name === 'QuotaExceededError') {
        console.warn('⚠️ localStorage quota exceeded, clearing old sessions...');
        try {
          const storageKey = getChatStorageKey();
          // Keep only the latest 5 sessions
          const minimalState = {
            sessions: state.sessions.slice(-5).map(cleanSessionForStorage),
            activeSessionIds: state.activeSessionIds,
            savedAt: Date.now(),
            userId: getUserId(),
          };
          localStorage.setItem(storageKey, JSON.stringify(minimalState));
        } catch {
          // If still failing, clear all
          const storageKey = getChatStorageKey();
          localStorage.removeItem(storageKey);
        }
      }
    }
  }, 500); // Save after 500ms of inactivity
};

// Immediate save for critical operations
const saveChatState = (state: { sessions: ChatSession[]; activeSessionIds: Record<string, string>; openTabs?: Record<string, string[]>; activeTabId?: Record<string, string> }) => {
  if (typeof window === 'undefined') return;
  try {
    const storageKey = getChatStorageKey();
    const cleanedState = prepareStateForStorage(state);
    localStorage.setItem(storageKey, JSON.stringify(cleanedState));
    console.log(`💾 Saved chat state to ${storageKey}`);
  } catch (e) {
    console.error('Failed to save chat state to localStorage:', e);
    // When quota exceeded, try to clean old data
    if (e instanceof DOMException && e.name === 'QuotaExceededError') {
      console.warn('⚠️ localStorage quota exceeded, clearing old sessions...');
      try {
        const storageKey = getChatStorageKey();
        // Keep only the latest 5 sessions
        const minimalState = {
          sessions: state.sessions.slice(-5).map(cleanSessionForStorage),
          activeSessionIds: state.activeSessionIds,
          savedAt: Date.now(),
          userId: getUserId(),
        };
        localStorage.setItem(storageKey, JSON.stringify(minimalState));
      } catch {
        // If still failing, clear all
        const storageKey = getChatStorageKey();
        localStorage.removeItem(storageKey);
      }
    }
  }
};

/**
 * Migrates data from old shared storage to user-scoped storage
 * This runs once when the app loads
 */
const migrateOldStorageData = () => {
  if (typeof window === 'undefined') return;

  const OLD_KEY = 'atcode-chat-store';
  const migrationFlagKey = 'atcode-migration-completed';

  // Check if migration already completed
  if (localStorage.getItem(migrationFlagKey)) {
    return;
  }

  try {
    const oldData = localStorage.getItem(OLD_KEY);

    if (oldData) {
      const parsed = JSON.parse(oldData);
      const newStorageKey = getChatStorageKey();

      // Check if new storage already has data
      const existingData = localStorage.getItem(newStorageKey);

      if (!existingData && parsed.sessions?.length > 0) {
        console.log('🔄 Migrating old chat data to user-scoped storage...');

        // Migrate sessions, converting activeSessionId to activeSessionIds
        const activeSessionIds: Record<string, string> = {};

        if (parsed.activeSessionId) {
          // Find which repo this session belongs to
          const activeSession = parsed.sessions.find(
            (s: ChatSession) => s.id === parsed.activeSessionId
          );
          if (activeSession) {
            activeSessionIds[activeSession.repoName] = parsed.activeSessionId;
          }
        }

        const migratedData = {
          sessions: parsed.sessions,
          activeSessionIds,
          migratedAt: Date.now(),
          migratedFrom: OLD_KEY,
          userId: getUserId(),
        };

        localStorage.setItem(newStorageKey, JSON.stringify(migratedData));
        console.log('✅ Migration completed successfully');
      }

      // Mark migration as completed (but don't delete old data yet)
      localStorage.setItem(migrationFlagKey, Date.now().toString());
    }
  } catch (e) {
    console.error('Migration failed:', e);
  }
};

// Call migration at module load time
if (typeof window !== 'undefined') {
  migrateOldStorageData();
}

const initialState = loadChatState();

export const useChatStore = create<ChatState>((set, get) => ({
  sessions: initialState.sessions,
  activeSessionIds: initialState.activeSessionIds,
  isStreaming: false,
  streamingSessionIds: [],
  streamingContent: {},
  openTabs: initialState.openTabs,
  activeTabId: initialState.activeTabId,

  streamSessionStates: {},
  setStreamSessionState: (sessionId, updates) => {
    set(state => {
      const current = state.streamSessionStates[sessionId] ?? {
        currentToolCall: null, toolCallHistory: [], traceExpanded: true, autoCollapsed: false,
      };
      return { streamSessionStates: { ...state.streamSessionStates, [sessionId]: { ...current, ...updates } } };
    });
  },
  clearStreamSessionState: (sessionId) => {
    set(state => {
      const { [sessionId]: _, ...rest } = state.streamSessionStates;
      return { streamSessionStates: rest };
    });
  },

  createSession: (repoName, sessionId, documentContext, contextId) => {
    const newSessionId = sessionId || (typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`);
    const newSession: ChatSession = {
      id: newSessionId,
      repoName,
      contextId,
      messages: [],
      createdAt: Date.now(),
      updatedAt: Date.now(),
      documentContext,
    };
    set((state) => {
      const newSessions = [newSession, ...state.sessions];
      // Enforce session limit
      const limitedSessions = newSessions.slice(0, MAX_STORED_SESSIONS);

      const newActiveSessionIds = {
        ...state.activeSessionIds,
        [repoName]: newSessionId,
      };

      // Also open as a tab
      const currentTabs = state.openTabs[repoName] || [];
      const newOpenTabs = { ...state.openTabs, [repoName]: [...currentTabs, newSessionId] };
      const newActiveTabId = { ...state.activeTabId, [repoName]: newSessionId };

      saveChatState({
        sessions: limitedSessions,
        activeSessionIds: newActiveSessionIds,
        openTabs: newOpenTabs,
        activeTabId: newActiveTabId,
      });

      return {
        sessions: limitedSessions,
        activeSessionIds: newActiveSessionIds,
        openTabs: newOpenTabs,
        activeTabId: newActiveTabId,
      };
    });
    console.log(`➕ Created new session for ${repoName}:`, newSessionId);
    return newSessionId;
  },

  findSession: (repoName, contextId) => {
    const { sessions } = get();
    // Find most recent session matching repo and context
    // If contextId is provided, match it exactly.
    // If contextId is undefined, match sessions with undefined contextId (root repo sessions)
    return sessions
      .filter(s => s.repoName === repoName && s.contextId === contextId)
      .sort((a, b) => b.updatedAt - a.updatedAt)[0];
  },

  setDocumentContext: (sessionId, context) => {
    set((state) => {
      const newSessions = state.sessions.map((session) =>
        session.id === sessionId
          ? {
              ...session,
              documentContext: {
                ...(session.documentContext || {}),
                ...context
              },
              updatedAt: Date.now()
            }
          : session
      );
      saveChatState({ sessions: newSessions, activeSessionIds: state.activeSessionIds });
      return { sessions: newSessions };
    });
  },

  clearDocumentContext: (sessionId) => {
    set((state) => {
      const newSessions = state.sessions.map((session) =>
        session.id === sessionId
          ? {
              ...session,
              documentContext: undefined,
              updatedAt: Date.now()
            }
          : session
      );
      saveChatState({ sessions: newSessions, activeSessionIds: state.activeSessionIds });
      return { sessions: newSessions };
    });
  },

  getSession: (sessionId) => {
    return get().sessions.find((s) => s.id === sessionId);
  },

  // Updated getActiveSession - now requires repoName
  getActiveSession: (repoName: string) => {
    const { sessions, activeSessionIds } = get();
    const sessionId = activeSessionIds[repoName];

    if (!sessionId) {
      // No active session for this repo, find most recent
      const repoSessions = sessions
        .filter(s => s.repoName === repoName)
        .sort((a, b) => b.updatedAt - a.updatedAt);

      return repoSessions[0];
    }

    return sessions.find(s => s.id === sessionId && s.repoName === repoName);
  },

  // Updated setActiveSession - now requires repoName
  setActiveSession: (repoName: string, sessionId: string) => {
    set((state) => {
      const newActiveSessionIds = {
        ...state.activeSessionIds,
        [repoName]: sessionId,
      };

      // Save immediately for critical operations
      saveChatState({
        sessions: state.sessions,
        activeSessionIds: newActiveSessionIds,
      });

      return { activeSessionIds: newActiveSessionIds };
    });

    console.log(`📌 Set active session for ${repoName}:`, sessionId);
  },

  // New method to clear active session for a repo
  clearActiveSession: (repoName: string) => {
    set((state) => {
      const newActiveSessionIds = { ...state.activeSessionIds };
      delete newActiveSessionIds[repoName];

      return { activeSessionIds: newActiveSessionIds };
    });
  },

  loadSession: (session) => {
    set((state) => {
      // Check if session with same ID already exists
      const existingIndex = state.sessions.findIndex(s => s.id === session.id);

      let newSessions;
      if (existingIndex >= 0) {
        // Replace existing session (preserving history)
        console.log('🔄 Replacing existing session:', session.id);
        newSessions = [...state.sessions];
        newSessions[existingIndex] = session;
      } else {
        // Add new session
        console.log('➕ Adding new session:', session.id);
        newSessions = [...state.sessions, session];
      }

      const newActiveSessionIds = {
        ...state.activeSessionIds,
        [session.repoName]: session.id,
      };

      saveChatState({
        sessions: newSessions,
        activeSessionIds: newActiveSessionIds,
      });

      return {
        sessions: newSessions,
        activeSessionIds: newActiveSessionIds,
      };
    });
  },

  deleteSession: (sessionId) => {
    set((state) => {
      const sessionToDelete = state.sessions.find(s => s.id === sessionId);
      const newSessions = state.sessions.filter((s) => s.id !== sessionId);
      const newActiveSessionIds = { ...state.activeSessionIds };
      const newOpenTabs = { ...state.openTabs };
      const newActiveTabId = { ...state.activeTabId };

      // If deleting the active session for a repo, update it
      if (sessionToDelete) {
        const repoName = sessionToDelete.repoName;

        // Clean up tab state
        if (newOpenTabs[repoName]) {
          newOpenTabs[repoName] = newOpenTabs[repoName].filter(id => id !== sessionId);
          if (newOpenTabs[repoName].length === 0) {
            delete newOpenTabs[repoName];
            delete newActiveTabId[repoName];
          } else if (newActiveTabId[repoName] === sessionId) {
            newActiveTabId[repoName] = newOpenTabs[repoName][newOpenTabs[repoName].length - 1];
          }
        }

        if (newActiveSessionIds[repoName] === sessionId) {
          // Find next most recent session for this repo
          const nextSession = newSessions
            .filter(s => s.repoName === repoName)
            .sort((a, b) => b.updatedAt - a.updatedAt)[0];

          if (nextSession) {
            newActiveSessionIds[repoName] = nextSession.id;
          } else {
            delete newActiveSessionIds[repoName];
          }
        }
      }

      saveChatState({
        sessions: newSessions,
        activeSessionIds: newActiveSessionIds,
        openTabs: newOpenTabs,
        activeTabId: newActiveTabId,
      });

      return {
        sessions: newSessions,
        activeSessionIds: newActiveSessionIds,
        openTabs: newOpenTabs,
        activeTabId: newActiveTabId,
      };
    });
  },

  addMessage: (sessionId, message) => {
    const messageId = `msg-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    const newMessage: ChatMessage = {
      ...message,
      id: messageId,
      timestamp: Date.now(),
    };

    set((state) => {
      const newSessions = state.sessions.map((session) =>
        session.id === sessionId
          ? {
              ...session,
              messages: [...session.messages, newMessage],
              updatedAt: Date.now(),
            }
          : session
      );
      debouncedSaveChatState({ sessions: newSessions, activeSessionIds: state.activeSessionIds });
      return { sessions: newSessions };
    });
  },

  updateMessage: (sessionId, messageId, updates) => {
    set((state) => {
      const newSessions = state.sessions.map((session) =>
        session.id === sessionId
          ? {
              ...session,
              messages: session.messages.map((msg) =>
                msg.id === messageId ? { ...msg, ...updates } : msg
              ),
              updatedAt: Date.now(),
            }
          : session
      );
      debouncedSaveChatState({ sessions: newSessions, activeSessionIds: state.activeSessionIds });
      return { sessions: newSessions };
    });
  },

  removeLastMessage: (sessionId) => {
    set((state) => {
      const newSessions = state.sessions.map((session) =>
        session.id === sessionId
          ? {
              ...session,
              messages: session.messages.slice(0, -1),
              updatedAt: Date.now(),
            }
          : session
      );
      saveChatState({ sessions: newSessions, activeSessionIds: state.activeSessionIds });
      return { sessions: newSessions };
    });
  },

  truncateMessages: (sessionId, fromIndex) => {
    set((state) => {
      const newSessions = state.sessions.map((session) =>
        session.id === sessionId
          ? {
              ...session,
              messages: session.messages.slice(0, fromIndex),
              updatedAt: Date.now(),
            }
          : session
      );
      saveChatState({ sessions: newSessions, activeSessionIds: state.activeSessionIds, openTabs: state.openTabs, activeTabId: state.activeTabId });
      return { sessions: newSessions };
    });
  },

  clearMessages: (sessionId) => {
    set((state) => {
      const newSessions = state.sessions.map((session) =>
        session.id === sessionId
          ? {
              ...session,
              messages: [],
              updatedAt: Date.now(),
            }
          : session
      );
      saveChatState({ sessions: newSessions, activeSessionIds: state.activeSessionIds });
      return { sessions: newSessions };
    });
  },

  setStreaming: (isStreaming) => {
    // Legacy: if clearing, also clear all session streaming
    if (!isStreaming) {
      set({ isStreaming: false, streamingSessionIds: [] });
    } else {
      set({ isStreaming });
    }
  },

  setSessionStreaming: (sessionId, streaming) => {
    set((state) => {
      let newIds: string[];
      if (streaming) {
        newIds = state.streamingSessionIds.includes(sessionId)
          ? state.streamingSessionIds
          : [...state.streamingSessionIds, sessionId];
      } else {
        newIds = state.streamingSessionIds.filter(id => id !== sessionId);
      }
      return {
        streamingSessionIds: newIds,
        isStreaming: newIds.length > 0, // Keep backward compat
      };
    });
  },

  isSessionStreaming: (sessionId) => {
    return get().streamingSessionIds.includes(sessionId);
  },

  // Updated streaming methods with composite keys
  setStreamingContent: (repoName: string, sessionId: string, content: string) => {
    const key = getStreamingKey(repoName, sessionId);
    set((state) => ({
      streamingContent: {
        ...state.streamingContent,
        [key]: content,
      },
    }));
  },

  appendStreamingContent: (repoName: string, sessionId: string, content: string) => {
    const key = getStreamingKey(repoName, sessionId);
    set((state) => ({
      streamingContent: {
        ...state.streamingContent,
        [key]: (state.streamingContent[key] || '') + content,
      },
    }));
  },

  clearStreamingContent: (repoName: string, sessionId: string) => {
    const key = getStreamingKey(repoName, sessionId);
    set((state) => {
      const newStreamingContent = { ...state.streamingContent };
      delete newStreamingContent[key];
      return { streamingContent: newStreamingContent };
    });
  },

  getStreamingContent: (repoName: string, sessionId: string): string => {
    const key = getStreamingKey(repoName, sessionId);
    return get().streamingContent[key] || '';
  },

  // Tab management
  openTab: (repoName, sessionId) => {
    set((state) => {
      const currentTabs = state.openTabs[repoName] || [];
      if (currentTabs.includes(sessionId)) {
        // Already open, just switch to it
        const newState = {
          activeTabId: { ...state.activeTabId, [repoName]: sessionId },
          activeSessionIds: { ...state.activeSessionIds, [repoName]: sessionId },
        };
        saveChatState({ sessions: state.sessions, activeSessionIds: newState.activeSessionIds, openTabs: state.openTabs, activeTabId: newState.activeTabId });
        return newState;
      }
      const newTabs = [...currentTabs, sessionId];
      const newOpenTabs = { ...state.openTabs, [repoName]: newTabs };
      const newActiveTabId = { ...state.activeTabId, [repoName]: sessionId };
      const newActiveSessionIds = { ...state.activeSessionIds, [repoName]: sessionId };

      saveChatState({ sessions: state.sessions, activeSessionIds: newActiveSessionIds, openTabs: newOpenTabs, activeTabId: newActiveTabId });
      return {
        openTabs: newOpenTabs,
        activeTabId: newActiveTabId,
        activeSessionIds: newActiveSessionIds,
      };
    });
  },

  closeTab: (repoName, sessionId) => {
    set((state) => {
      const currentTabs = state.openTabs[repoName] || [];
      const newTabs = currentTabs.filter(id => id !== sessionId);
      const newOpenTabs = { ...state.openTabs, [repoName]: newTabs };
      const updates: Partial<ChatState> = { openTabs: newOpenTabs };

      // If closing the active tab, switch to the last remaining tab
      if (state.activeTabId[repoName] === sessionId && newTabs.length > 0) {
        const newActiveTab = newTabs[newTabs.length - 1];
        updates.activeTabId = { ...state.activeTabId, [repoName]: newActiveTab };
        updates.activeSessionIds = { ...state.activeSessionIds, [repoName]: newActiveTab };
      } else if (newTabs.length === 0) {
        // All tabs closed - remove tab state but keep active session
        const newActiveTabId = { ...state.activeTabId };
        delete newActiveTabId[repoName];
        updates.activeTabId = newActiveTabId;
      }

      saveChatState({
        sessions: state.sessions,
        activeSessionIds: (updates.activeSessionIds || state.activeSessionIds) as Record<string, string>,
        openTabs: newOpenTabs,
        activeTabId: (updates.activeTabId || state.activeTabId) as Record<string, string>,
      });
      return updates;
    });
  },

  switchTab: (repoName, sessionId) => {
    set((state) => {
      const newActiveTabId = { ...state.activeTabId, [repoName]: sessionId };
      const newActiveSessionIds = { ...state.activeSessionIds, [repoName]: sessionId };

      saveChatState({ sessions: state.sessions, activeSessionIds: newActiveSessionIds, openTabs: state.openTabs, activeTabId: newActiveTabId });
      return {
        activeTabId: newActiveTabId,
        activeSessionIds: newActiveSessionIds,
      };
    });
  },

  getOpenTabs: (repoName) => {
    return get().openTabs[repoName] || [];
  },

  addPaperToSession: (sessionId, paperId) => {
    set((state) => {
      const newSessions = state.sessions.map((session) =>
        session.id === sessionId
          ? {
              ...session,
              paperIds: session.paperIds?.includes(paperId)
                ? session.paperIds
                : [...(session.paperIds || []), paperId],
              updatedAt: Date.now(),
            }
          : session
      );
      debouncedSaveChatState({ sessions: newSessions, activeSessionIds: state.activeSessionIds, openTabs: state.openTabs, activeTabId: state.activeTabId });
      return { sessions: newSessions };
    });
  },

  setSessionTitle: (sessionId, title) => {
    set((state) => {
      const newSessions = state.sessions.map((session) =>
        session.id === sessionId
          ? { ...session, title, updatedAt: Date.now() }
          : session
      );
      debouncedSaveChatState({ sessions: newSessions, activeSessionIds: state.activeSessionIds, openTabs: state.openTabs, activeTabId: state.activeTabId });
      return { sessions: newSessions };
    });
  },
}));

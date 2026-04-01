// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Database Module
 *
 * SQLite database schema and repository for chat logs.
 * Provides ACID-compliant storage with proper concurrent access handling.
 */

import Database from 'better-sqlite3';
import path from 'path';

// Database path - can be configured via environment variable
const DB_PATH = process.env.DATABASE_PATH || path.join(process.cwd(), '..', 'data', 'atcode.db');

// Singleton database instance
let db: Database.Database | null = null;

/**
 * Gets or creates the database connection
 */
export function getDatabase(): Database.Database {
  if (!db) {
    // Ensure directory exists
    const dbDir = path.dirname(DB_PATH);
    const fs = require('fs');
    if (!fs.existsSync(dbDir)) {
      fs.mkdirSync(dbDir, { recursive: true });
    }

    db = new Database(DB_PATH);

    // Enable WAL mode for better concurrent performance
    db.pragma('journal_mode = WAL');

    // Enable foreign keys
    db.pragma('foreign_keys = ON');

    // Initialize schema
    initializeSchema(db);

    console.log('Database initialized at:', DB_PATH);
  }
  return db;
}

/**
 * Closes the database connection
 */
export function closeDatabase(): void {
  if (db) {
    db.close();
    db = null;
    console.log('Database connection closed');
  }
}

/**
 * Initialize database schema
 */
function initializeSchema(db: Database.Database): void {
  db.exec(`
    -- Users table (for tracking anonymous and authenticated users)
    CREATE TABLE IF NOT EXISTS users (
      id TEXT PRIMARY KEY,
      anonymous_id TEXT,
      email TEXT,
      display_name TEXT,
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    );

    -- Create index for anonymous_id lookups
    CREATE INDEX IF NOT EXISTS idx_users_anonymous_id
    ON users(anonymous_id);

    -- Chat sessions table
    CREATE TABLE IF NOT EXISTS chat_sessions (
      id TEXT PRIMARY KEY,
      user_id TEXT NOT NULL,
      repo_name TEXT NOT NULL,
      context_id TEXT,
      document_context_json TEXT,
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL,
      FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    -- Create indexes for faster queries
    CREATE INDEX IF NOT EXISTS idx_sessions_user_repo
    ON chat_sessions(user_id, repo_name);

    CREATE INDEX IF NOT EXISTS idx_sessions_updated
    ON chat_sessions(updated_at DESC);

    -- Chat messages table
    CREATE TABLE IF NOT EXISTS chat_messages (
      id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL,
      role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
      content TEXT NOT NULL,
      metadata_json TEXT,
      created_at INTEGER NOT NULL,
      FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
    );

    -- Create index for faster message retrieval
    CREATE INDEX IF NOT EXISTS idx_messages_session
    ON chat_messages(session_id, created_at);

    -- Active sessions table (tracks which session is active per user per repo)
    CREATE TABLE IF NOT EXISTS active_sessions (
      user_id TEXT NOT NULL,
      repo_name TEXT NOT NULL,
      session_id TEXT NOT NULL,
      PRIMARY KEY (user_id, repo_name),
      FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
      FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
    );
  `);
}

// Type definitions
export interface DbUser {
  id: string;
  anonymous_id: string | null;
  email: string | null;
  display_name: string | null;
  created_at: number;
  updated_at: number;
}

export interface DbChatSession {
  id: string;
  user_id: string;
  repo_name: string;
  context_id: string | null;
  document_context_json: string | null;
  created_at: number;
  updated_at: number;
}

export interface DbChatMessage {
  id: string;
  session_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  metadata_json: string | null;
  created_at: number;
}

// Application-level types (matching store.ts)
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: number;
  metadata?: {
    toolCalls?: number;
    exploredNodes?: number;
    isStreaming?: boolean;
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
    code_blocks?: Array<{
      id: string;
      file: string;
      startLine: number;
      endLine: number;
      code: string;
      language: string;
    }>;
  };
}

export interface DocumentContext {
  filePath?: string;
  startLine?: number;
  endLine?: number;
  selectedText?: string;
  documentTitle?: string;
  fullContent?: string;
}

export interface ChatSession {
  id: string;
  repoName: string;
  contextId?: string;
  messages: ChatMessage[];
  createdAt: number;
  updatedAt: number;
  documentContext?: DocumentContext;
}

/**
 * Chat Repository - Database operations for chat data
 */
export const ChatRepository = {
  /**
   * Create or update user
   */
  getOrCreateUser(userId: string, anonymousId?: string): DbUser {
    const db = getDatabase();
    const now = Date.now();

    // Try to find existing user
    const existing = db.prepare(`
      SELECT * FROM users WHERE id = ?
    `).get(userId) as DbUser | undefined;

    if (existing) {
      // Update last access time
      db.prepare(`
        UPDATE users SET updated_at = ? WHERE id = ?
      `).run(now, userId);
      return { ...existing, updated_at: now };
    }

    // Create new user
    db.prepare(`
      INSERT INTO users (id, anonymous_id, created_at, updated_at)
      VALUES (?, ?, ?, ?)
    `).run(userId, anonymousId || userId, now, now);

    return {
      id: userId,
      anonymous_id: anonymousId || userId,
      email: null,
      display_name: null,
      created_at: now,
      updated_at: now,
    };
  },

  /**
   * Create a new chat session
   */
  createSession(
    sessionId: string,
    userId: string,
    repoName: string,
    contextId?: string,
    documentContext?: DocumentContext
  ): ChatSession {
    const db = getDatabase();
    const now = Date.now();

    // Ensure user exists
    this.getOrCreateUser(userId);

    const documentContextJson = documentContext ? JSON.stringify(documentContext) : null;

    db.prepare(`
      INSERT INTO chat_sessions (id, user_id, repo_name, context_id, document_context_json, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?)
    `).run(sessionId, userId, repoName, contextId || null, documentContextJson, now, now);

    // Set as active session for this repo
    db.prepare(`
      INSERT INTO active_sessions (user_id, repo_name, session_id)
      VALUES (?, ?, ?)
      ON CONFLICT(user_id, repo_name) DO UPDATE SET session_id = excluded.session_id
    `).run(userId, repoName, sessionId);

    return {
      id: sessionId,
      repoName,
      contextId,
      messages: [],
      createdAt: now,
      updatedAt: now,
      documentContext,
    };
  },

  /**
   * Get session by ID
   */
  getSession(sessionId: string): ChatSession | null {
    const db = getDatabase();

    const session = db.prepare(`
      SELECT * FROM chat_sessions WHERE id = ?
    `).get(sessionId) as DbChatSession | undefined;

    if (!session) return null;

    const messages = this.getMessages(sessionId);

    return {
      id: session.id,
      repoName: session.repo_name,
      contextId: session.context_id || undefined,
      messages,
      createdAt: session.created_at,
      updatedAt: session.updated_at,
      documentContext: session.document_context_json
        ? JSON.parse(session.document_context_json)
        : undefined,
    };
  },

  /**
   * Get sessions for a user and repo
   */
  getSessions(userId: string, repoName?: string): ChatSession[] {
    const db = getDatabase();

    let query = `
      SELECT * FROM chat_sessions
      WHERE user_id = ?
    `;
    const params: (string)[] = [userId];

    if (repoName) {
      query += ` AND repo_name = ?`;
      params.push(repoName);
    }

    query += ` ORDER BY updated_at DESC`;

    const sessions = db.prepare(query).all(...params) as DbChatSession[];

    return sessions.map(session => ({
      id: session.id,
      repoName: session.repo_name,
      contextId: session.context_id || undefined,
      messages: this.getMessages(session.id),
      createdAt: session.created_at,
      updatedAt: session.updated_at,
      documentContext: session.document_context_json
        ? JSON.parse(session.document_context_json)
        : undefined,
    }));
  },

  /**
   * Get active session for user and repo
   */
  getActiveSession(userId: string, repoName: string): ChatSession | null {
    const db = getDatabase();

    const active = db.prepare(`
      SELECT session_id FROM active_sessions
      WHERE user_id = ? AND repo_name = ?
    `).get(userId, repoName) as { session_id: string } | undefined;

    if (active) {
      return this.getSession(active.session_id);
    }

    // Fall back to most recent session
    const sessions = this.getSessions(userId, repoName);
    return sessions[0] || null;
  },

  /**
   * Set active session for user and repo
   */
  setActiveSession(userId: string, repoName: string, sessionId: string): void {
    const db = getDatabase();

    db.prepare(`
      INSERT INTO active_sessions (user_id, repo_name, session_id)
      VALUES (?, ?, ?)
      ON CONFLICT(user_id, repo_name) DO UPDATE SET session_id = excluded.session_id
    `).run(userId, repoName, sessionId);
  },

  /**
   * Delete a session and all its messages
   */
  deleteSession(sessionId: string): void {
    const db = getDatabase();

    // Messages are deleted automatically due to ON DELETE CASCADE
    db.prepare(`
      DELETE FROM chat_sessions WHERE id = ?
    `).run(sessionId);
  },

  /**
   * Add a message to a session
   */
  addMessage(sessionId: string, message: Omit<ChatMessage, 'id' | 'timestamp'>): ChatMessage {
    const db = getDatabase();
    const now = Date.now();
    const messageId = `msg-${now}-${Math.random().toString(36).substring(2, 8)}`;

    const metadataJson = message.metadata ? JSON.stringify(message.metadata) : null;

    db.transaction(() => {
      // Insert message
      db.prepare(`
        INSERT INTO chat_messages (id, session_id, role, content, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
      `).run(messageId, sessionId, message.role, message.content, metadataJson, now);

      // Update session timestamp
      db.prepare(`
        UPDATE chat_sessions SET updated_at = ? WHERE id = ?
      `).run(now, sessionId);
    })();

    return {
      id: messageId,
      role: message.role,
      content: message.content,
      timestamp: now,
      metadata: message.metadata,
    };
  },

  /**
   * Update a message
   */
  updateMessage(messageId: string, updates: Partial<ChatMessage>): void {
    const db = getDatabase();

    const setClauses: string[] = [];
    const values: (string | number | null)[] = [];

    if (updates.content !== undefined) {
      setClauses.push('content = ?');
      values.push(updates.content);
    }

    if (updates.metadata !== undefined) {
      setClauses.push('metadata_json = ?');
      values.push(JSON.stringify(updates.metadata));
    }

    if (setClauses.length === 0) return;

    values.push(messageId);

    db.prepare(`
      UPDATE chat_messages
      SET ${setClauses.join(', ')}
      WHERE id = ?
    `).run(...values);
  },

  /**
   * Get messages for a session
   */
  getMessages(sessionId: string): ChatMessage[] {
    const db = getDatabase();

    const messages = db.prepare(`
      SELECT * FROM chat_messages
      WHERE session_id = ?
      ORDER BY created_at ASC
    `).all(sessionId) as DbChatMessage[];

    return messages.map(msg => ({
      id: msg.id,
      role: msg.role,
      content: msg.content,
      timestamp: msg.created_at,
      metadata: msg.metadata_json ? JSON.parse(msg.metadata_json) : undefined,
    }));
  },

  /**
   * Clear all messages in a session
   */
  clearMessages(sessionId: string): void {
    const db = getDatabase();
    const now = Date.now();

    db.transaction(() => {
      db.prepare(`
        DELETE FROM chat_messages WHERE session_id = ?
      `).run(sessionId);

      db.prepare(`
        UPDATE chat_sessions SET updated_at = ? WHERE id = ?
      `).run(now, sessionId);
    })();
  },

  /**
   * Update document context for a session
   */
  setDocumentContext(sessionId: string, context: DocumentContext): void {
    const db = getDatabase();
    const now = Date.now();

    db.prepare(`
      UPDATE chat_sessions
      SET document_context_json = ?, updated_at = ?
      WHERE id = ?
    `).run(JSON.stringify(context), now, sessionId);
  },

  /**
   * Clear document context for a session
   */
  clearDocumentContext(sessionId: string): void {
    const db = getDatabase();
    const now = Date.now();

    db.prepare(`
      UPDATE chat_sessions
      SET document_context_json = NULL, updated_at = ?
      WHERE id = ?
    `).run(now, sessionId);
  },

  /**
   * Get all active sessions for a user (across all repos)
   */
  getActiveSessionIds(userId: string): Record<string, string> {
    const db = getDatabase();

    const actives = db.prepare(`
      SELECT repo_name, session_id FROM active_sessions
      WHERE user_id = ?
    `).all(userId) as Array<{ repo_name: string; session_id: string }>;

    const result: Record<string, string> = {};
    for (const active of actives) {
      result[active.repo_name] = active.session_id;
    }
    return result;
  },

  /**
   * Check if database is enabled
   */
  isEnabled(): boolean {
    return process.env.USE_DATABASE === 'true';
  },
};

// Export database stats function for monitoring
export function getDatabaseStats(): {
  userCount: number;
  sessionCount: number;
  messageCount: number;
  dbSizeBytes: number;
} {
  const db = getDatabase();
  const fs = require('fs');

  const userCount = (db.prepare('SELECT COUNT(*) as count FROM users').get() as { count: number }).count;
  const sessionCount = (db.prepare('SELECT COUNT(*) as count FROM chat_sessions').get() as { count: number }).count;
  const messageCount = (db.prepare('SELECT COUNT(*) as count FROM chat_messages').get() as { count: number }).count;

  let dbSizeBytes = 0;
  try {
    const stats = fs.statSync(DB_PATH);
    dbSizeBytes = stats.size;
  } catch {
    // File might not exist yet
  }

  return { userCount, sessionCount, messageCount, dbSizeBytes };
}

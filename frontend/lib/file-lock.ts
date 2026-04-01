// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * File Locking Utilities
 *
 * Provides file locking for concurrent write protection.
 * Uses proper-lockfile to prevent data corruption when multiple
 * processes try to write to the same file simultaneously.
 */

import lockfile from 'proper-lockfile';
import fs from 'fs';
import path from 'path';

// Lock configuration options
const LOCK_OPTIONS: lockfile.LockOptions = {
  stale: 10000,      // Consider lock stale after 10 seconds
  retries: {
    retries: 5,
    factor: 2,
    minTimeout: 100,
    maxTimeout: 1000,
  },
};

/**
 * Ensures the directory for a file exists
 */
function ensureDirectory(filePath: string): void {
  const dir = path.dirname(filePath);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

/**
 * Ensures a file exists (creates empty file if needed for locking)
 */
function ensureFile(filePath: string): void {
  ensureDirectory(filePath);
  if (!fs.existsSync(filePath)) {
    fs.writeFileSync(filePath, '{}');
  }
}

/**
 * Safely read and write to a JSON file with locking
 *
 * @param filePath - Path to the JSON file
 * @param operation - Function that receives current data and returns new data
 * @returns The new data after the operation
 */
export async function withFileLock<T>(
  filePath: string,
  operation: (currentData: T | null) => T | null
): Promise<T | null> {
  ensureFile(filePath);

  let release: (() => Promise<void>) | null = null;

  try {
    // Acquire lock
    release = await lockfile.lock(filePath, LOCK_OPTIONS);

    // Read current data
    let currentData: T | null = null;
    try {
      const content = fs.readFileSync(filePath, 'utf-8');
      currentData = JSON.parse(content);
    } catch {
      // File is empty or invalid JSON
      currentData = null;
    }

    // Perform operation
    const newData = operation(currentData);

    // Write new data
    if (newData !== null) {
      fs.writeFileSync(filePath, JSON.stringify(newData, null, 2));
    }

    return newData;
  } catch (error) {
    if (error instanceof Error && error.message.includes('ELOCKED')) {
      console.warn(`File ${filePath} is locked, retrying...`);
      // Retry logic is handled by proper-lockfile options
    }
    throw error;
  } finally {
    // Always release lock
    if (release) {
      await release();
    }
  }
}

/**
 * Append a message to a chat log file safely
 *
 * @param filePath - Path to the chat log file
 * @param message - Message to append
 */
export async function appendToChatLog(
  filePath: string,
  message: {
    role: string;
    content: string;
    references?: unknown[];
    metadata?: unknown;
  }
): Promise<void> {
  interface ChatLog {
    messages: Array<{
      role: string;
      content: string;
      references?: unknown[];
      metadata?: unknown;
      timestamp: number;
    }>;
    updatedAt: number;
    createdAt?: number;
  }

  await withFileLock<ChatLog>(filePath, (data) => {
    const chatLog: ChatLog = data || { messages: [], createdAt: Date.now(), updatedAt: Date.now() };

    if (!chatLog.messages) {
      chatLog.messages = [];
    }

    chatLog.messages.push({
      ...message,
      timestamp: Date.now(),
    });

    chatLog.updatedAt = Date.now();

    return chatLog;
  });
}

/**
 * Read chat log safely with locking
 *
 * @param filePath - Path to the chat log file
 * @returns The chat log data
 */
export async function readChatLog(filePath: string): Promise<{
  messages: Array<{
    role: string;
    content: string;
    references?: unknown[];
    metadata?: unknown;
    timestamp: number;
  }>;
  updatedAt: number;
  createdAt?: number;
} | null> {
  if (!fs.existsSync(filePath)) {
    return null;
  }

  // Use lock for reading to ensure consistency
  return await withFileLock(filePath, (data) => data);
}

/**
 * Update specific message in chat log
 *
 * @param filePath - Path to the chat log file
 * @param messageIndex - Index of the message to update
 * @param updates - Updates to apply to the message
 */
export async function updateChatLogMessage(
  filePath: string,
  messageIndex: number,
  updates: Partial<{
    content: string;
    references: unknown[];
    metadata: unknown;
  }>
): Promise<void> {
  interface ChatLog {
    messages: Array<{
      role: string;
      content: string;
      references?: unknown[];
      metadata?: unknown;
      timestamp: number;
    }>;
    updatedAt: number;
  }

  await withFileLock<ChatLog>(filePath, (data) => {
    if (!data || !data.messages || messageIndex >= data.messages.length) {
      return data;
    }

    data.messages[messageIndex] = {
      ...data.messages[messageIndex],
      ...updates,
    };

    data.updatedAt = Date.now();

    return data;
  });
}

/**
 * Clear all messages in a chat log
 *
 * @param filePath - Path to the chat log file
 */
export async function clearChatLog(filePath: string): Promise<void> {
  interface ChatLog {
    messages: Array<unknown>;
    updatedAt: number;
    createdAt?: number;
  }

  await withFileLock<ChatLog>(filePath, (data) => {
    return {
      messages: [],
      createdAt: data?.createdAt || Date.now(),
      updatedAt: Date.now(),
    };
  });
}

/**
 * Delete a chat log file
 *
 * @param filePath - Path to the chat log file
 */
export async function deleteChatLog(filePath: string): Promise<void> {
  if (!fs.existsSync(filePath)) {
    return;
  }

  let release: (() => Promise<void>) | null = null;

  try {
    release = await lockfile.lock(filePath, LOCK_OPTIONS);
    fs.unlinkSync(filePath);
  } finally {
    if (release) {
      await release();
    }
  }
}

/**
 * Check if a file is locked
 *
 * @param filePath - Path to check
 * @returns true if file is locked
 */
export async function isFileLocked(filePath: string): Promise<boolean> {
  if (!fs.existsSync(filePath)) {
    return false;
  }

  return await lockfile.check(filePath, LOCK_OPTIONS);
}

/**
 * Wrapper for atomic file operations
 * Use this when you need to perform multiple read-modify-write operations
 */
export class AtomicFileOperation<T> {
  private filePath: string;
  private release: (() => Promise<void>) | null = null;
  private data: T | null = null;

  constructor(filePath: string) {
    this.filePath = filePath;
  }

  /**
   * Acquire lock and read current data
   */
  async begin(): Promise<T | null> {
    ensureFile(this.filePath);

    this.release = await lockfile.lock(this.filePath, LOCK_OPTIONS);

    try {
      const content = fs.readFileSync(this.filePath, 'utf-8');
      this.data = JSON.parse(content);
    } catch {
      this.data = null;
    }

    return this.data;
  }

  /**
   * Get current data (must call begin first)
   */
  getData(): T | null {
    return this.data;
  }

  /**
   * Update data (will be committed on end)
   */
  setData(data: T): void {
    this.data = data;
  }

  /**
   * Commit changes and release lock
   */
  async commit(): Promise<void> {
    if (!this.release) {
      throw new Error('Cannot commit: no active transaction');
    }

    try {
      if (this.data !== null) {
        fs.writeFileSync(this.filePath, JSON.stringify(this.data, null, 2));
      }
    } finally {
      await this.release();
      this.release = null;
    }
  }

  /**
   * Rollback changes and release lock
   */
  async rollback(): Promise<void> {
    if (this.release) {
      await this.release();
      this.release = null;
    }
  }
}

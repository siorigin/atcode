// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { NextRequest } from 'next/server';
import { readdir, readFile, stat } from 'fs/promises';
import path from 'path';
import { getWikiChatDir } from '@/lib/paths';
import { open } from 'fs/promises';

// Force dynamic rendering - this route uses dynamic file paths
export const dynamic = 'force-dynamic';

type ChatTurnMetadata = {
  query?: string;
  response?: string;
  references?: unknown[];
  code_blocks?: unknown[];
  tool_calls?: number;
  timestamp?: string;
};

type ChatLogMetadata = {
  id?: string;
  query?: string;
  response?: string;
  references?: unknown[];
  code_blocks?: unknown[];
  tool_calls?: number;
  timestamp?: string;
  created_at?: string;
  updated_at?: string;
  turns?: ChatTurnMetadata[];
};

type SavedChatLog = {
  id: string;
  query: string;
  timestamp: string;
  tool_calls: number;
  references_count: number;
  code_blocks_count: number;
  turns_count: number;
};

function isErrnoException(error: unknown): error is NodeJS.ErrnoException {
  return typeof error === 'object' && error !== null && 'code' in error;
}

/**
 * Read only the beginning of a JSON file to extract metadata.
 * This avoids loading large chat history files into memory.
 * We read up to 8KB which should be enough for id, query, timestamp, and turns array info.
 */
async function readChatMetadata(filePath: string): Promise<ChatLogMetadata> {
  const fd = await open(filePath, 'r');
  try {
    const buffer = Buffer.alloc(8192); // Read only first 8KB
    const { bytesRead } = await fd.read(buffer, 0, 8192, 0);
    const partialContent = buffer.toString('utf-8', 0, bytesRead);

    // Try to parse the partial JSON
    // Note: This may fail if the JSON structure is complex, but works for our format
    try {
      return JSON.parse(partialContent);
    } catch {
      // Fallback: read full file if partial parsing fails
      const content = await readFile(filePath, 'utf-8');
      return JSON.parse(content);
    }
  } finally {
    await fd.close();
  }
}

/**
 * API endpoint to list saved chat logs for a repository
 * GET /api/chat-logs/[repo]
 */
export async function GET(
  request: NextRequest,
  context: { params: Promise<{ repo: string }> }
) {
  try {
    const params = await context.params;
    const repo = params.repo;

    if (!repo) {
      return new Response(
        JSON.stringify({ error: 'Repository name is required' }),
        { status: 400, headers: { 'Content-Type': 'application/json' } }
      );
    }

    // Path to the chat logs directory (using centralized config)
    const chatLogsDir = getWikiChatDir(repo);

    try {
      // Read all JSON files in the directory
      const files = await readdir(chatLogsDir);
      const jsonFiles = files.filter(f => f.endsWith('.json'));

      // Read each file's metadata (optimized to only read first 8KB)
      const chatLogs = await Promise.all(
        jsonFiles.map(async (filename) => {
          try {
            const filePath = path.join(chatLogsDir, filename);

            // Get file stats for mtime (fallback timestamp)
            const fileStats = await stat(filePath);

            // Read only metadata, not full content
            const data = await readChatMetadata(filePath);

            // Handle both old single-turn and new multi-turn format
            const turns = data.turns && data.turns.length > 0 ? data.turns : [{
              query: data.query,
              response: data.response,
              references: data.references,
              code_blocks: data.code_blocks,
              tool_calls: data.tool_calls,
              timestamp: data.timestamp
            }];

            // Get first and last turn info
            const firstTurn = turns[0];
            const lastTurn = turns[turns.length - 1];

            // Calculate total counts
            const totalReferences = turns.reduce((sum, turn) => sum + (turn.references?.length || 0), 0);
            const totalCodeBlocks = turns.reduce((sum, turn) => sum + (turn.code_blocks?.length || 0), 0);
            const totalToolCalls = turns.reduce((sum, turn) => sum + (turn.tool_calls || 0), 0);

            // Extract session_id from filename or data
            // Filename format: {user_id}__{session_id}.json
            let sessionId = data.id;
            if (!sessionId) {
              const nameWithoutExt = filename.replace('.json', '');
              // Parse filename to extract session_id part (after __)
              if (nameWithoutExt.includes('__')) {
                const parts = nameWithoutExt.split('__', 1);
                sessionId = nameWithoutExt.substring(parts[0].length + 2); // Skip user_id and __
              } else {
                sessionId = nameWithoutExt;
              }
            }

            return {
              id: sessionId,
              query: firstTurn.query || 'Untitled',
              timestamp: data.updated_at || lastTurn.timestamp || data.created_at || fileStats.mtime.toISOString(),
              tool_calls: totalToolCalls,
              references_count: totalReferences,
              code_blocks_count: totalCodeBlocks,
              turns_count: turns.length,
            };
          } catch (err) {
            console.error(`Failed to parse ${filename}:`, err);
            return null;
          }
        })
      );

      // Filter out failed parses and sort by timestamp (newest first)
      const validLogs = chatLogs
        .filter((log): log is SavedChatLog => log !== null)
        .sort((a, b) => {
          if (!a.timestamp) return 1;
          if (!b.timestamp) return -1;
          return new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime();
        });

      return new Response(JSON.stringify({ logs: validLogs }), {
        status: 200,
        headers: {
          'Content-Type': 'application/json',
          'Cache-Control': 'no-store',
        },
      });
    } catch (err: unknown) {
      if (isErrnoException(err) && err.code === 'ENOENT') {
        // Directory doesn't exist - return empty list
        return new Response(JSON.stringify({ logs: [] }), {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
            'Cache-Control': 'no-store',
          },
        });
      }
      throw err;
    }
  } catch (error) {
    console.error('Chat logs list error:', error);
    return new Response(
      JSON.stringify({ 
        error: 'Failed to list chat logs',
        details: error instanceof Error ? error.message : 'Unknown error'
      }),
      { status: 500, headers: { 'Content-Type': 'application/json' } }
    );
  }
}

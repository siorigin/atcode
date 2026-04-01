// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { NextRequest } from 'next/server';
import { readFile, unlink } from 'fs/promises';
import path from 'path';
import { resolveChatLogPath } from '../resolve';

// Force dynamic rendering - this route uses dynamic file paths
export const dynamic = 'force-dynamic';

function isErrnoException(error: unknown): error is NodeJS.ErrnoException {
  return typeof error === 'object' && error !== null && 'code' in error;
}

/**
 * API endpoint to load a specific chat log
 * GET /api/chat-logs/[repo]/[logId]
 */
export async function GET(
  request: NextRequest,
  context: { params: Promise<{ repo: string; logId: string }> }
) {
  try {
    const params = await context.params;
    const { repo, logId } = params;

    if (!repo || !logId) {
      return new Response(
        JSON.stringify({ error: 'Repository name and log ID are required' }),
        { status: 400, headers: { 'Content-Type': 'application/json' } }
      );
    }

    const logFilePath = resolveChatLogPath(request, repo, logId);

    if (!logFilePath) {
      return new Response(
        JSON.stringify({ error: 'Chat log not found' }),
        { status: 404, headers: { 'Content-Type': 'application/json' } }
      );
    }

    try {
      // Read and parse the file
      const content = await readFile(logFilePath, 'utf-8');
      const data = JSON.parse(content);

      return new Response(JSON.stringify(data), {
        status: 200,
        headers: {
          'Content-Type': 'application/json',
          'Cache-Control': 'no-store',
        },
      });
    } catch (err: unknown) {
      if (isErrnoException(err) && err.code === 'ENOENT') {
        return new Response(
          JSON.stringify({ error: 'Chat log not found' }),
          { status: 404, headers: { 'Content-Type': 'application/json' } }
        );
      }
      throw err;
    }
  } catch (error) {
    console.error('Chat log load error:', error);
    return new Response(
      JSON.stringify({
        error: 'Failed to load chat log',
        details: error instanceof Error ? error.message : 'Unknown error'
      }),
      { status: 500, headers: { 'Content-Type': 'application/json' } }
    );
  }
}

/**
 * API endpoint to delete a specific chat log
 * DELETE /api/chat-logs/[repo]/[logId]
 */
export async function DELETE(
  request: NextRequest,
  context: { params: Promise<{ repo: string; logId: string }> }
) {
  try {
    const params = await context.params;
    const { repo, logId } = params;

    if (!repo || !logId) {
      return new Response(
        JSON.stringify({ error: 'Repository name and log ID are required' }),
        { status: 400, headers: { 'Content-Type': 'application/json' } }
      );
    }

    const logFilePath = resolveChatLogPath(request, repo, logId);

    if (!logFilePath) {
      return new Response(
        JSON.stringify({ error: 'Chat log not found' }),
        { status: 404, headers: { 'Content-Type': 'application/json' } }
      );
    }

    // Delete the file
    await unlink(logFilePath);

    console.log(`Deleted chat log: ${repo}/${path.basename(logFilePath)}`);

    return new Response(
      JSON.stringify({ success: true, message: 'Chat log deleted successfully' }),
      { status: 200, headers: { 'Content-Type': 'application/json' } }
    );
  } catch (error) {
    console.error('Chat log delete error:', error);
    return new Response(
      JSON.stringify({
        error: 'Failed to delete chat log',
        details: error instanceof Error ? error.message : 'Unknown error'
      }),
      { status: 500, headers: { 'Content-Type': 'application/json' } }
    );
  }
}

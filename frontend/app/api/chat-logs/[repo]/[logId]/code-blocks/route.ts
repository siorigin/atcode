// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { NextRequest } from 'next/server';
import { readFile } from 'fs/promises';
import path from 'path';
import { getWikiReposDir } from '@/lib/paths';
import { resolveChatLogPath } from '../../resolve';
import crypto from 'crypto';

// Force dynamic rendering - this route uses dynamic file paths
export const dynamic = 'force-dynamic';

/**
 * API endpoint to generate code blocks from a saved chat log's references
 * GET /api/chat-logs/[repo]/[logId]/code-blocks
 *
 * This endpoint reads a chat log and generates code block metadata from its references,
 * allowing the frontend to display code without sending a new message.
 */
export async function GET(
  request: NextRequest,
  context: { params: Promise<{ repo: string; logId: string }> }
) {
  try {
    const params = await context.params;
    const { repo, logId } = params;

    console.log(`[Code Blocks API] Request received - Repo: ${repo}, LogId: ${logId}`);

    if (!repo || !logId) {
      console.error('[Code Blocks API] Missing required parameters');
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
      // Read and parse the chat log
      const content = await readFile(logFilePath, 'utf-8');
      const data = JSON.parse(content);

      // Extract all references from all turns
      const turns = data.turns || [];
      const allReferences = turns.flatMap((turn: any) => turn.references || []);

      console.log(`[Code Blocks API] Found ${turns.length} turns with ${allReferences.length} total references`);

      // Generate code blocks from references
      const codeBlocks = [];
      const seenBlocks = new Set<string>();

      // Group references by file path to minimize file reads
      const refsByFile = new Map<string, { repo: string, refs: any[] }>();

      for (const ref of allReferences) {
        if (ref.path && ref.start_line && ref.end_line) {
          const blockKey = `${ref.path}:${ref.start_line}:${ref.end_line}`;

          // Skip duplicates
          if (seenBlocks.has(blockKey)) {
            continue;
          }
          seenBlocks.add(blockKey);

          let actualRepo = repo;
          if (ref.qualified_name) {
            const parts = ref.qualified_name.split('.');
            if (parts.length > 0) {
              actualRepo = parts[0];
            }
          }

          const fileKey = `${actualRepo}:${ref.path}`;
          if (!refsByFile.has(fileKey)) {
            refsByFile.set(fileKey, { repo: actualRepo, refs: [] });
          }
          refsByFile.get(fileKey)!.refs.push(ref);
        }
      }

      // Process files
      for (const [fileKey, { repo: actualRepo, refs }] of refsByFile.entries()) {
        const filePath = refs[0].path;
        const repoFilePath = path.join(getWikiReposDir(actualRepo), filePath);

        try {
          const fileContent = await readFile(repoFilePath, 'utf-8');
          const lines = fileContent.split('\n');

          for (const ref of refs) {
             const blockKey = `${ref.path}:${ref.start_line}:${ref.end_line}`;
             // Generate stable ID (same logic as backend)
             const blockId = crypto.createHash('md5').update(blockKey).digest('hex').slice(0, 12);

             const codeLines = lines.slice(ref.start_line - 1, ref.end_line);
             const code = codeLines.join('\n');

             // Detect language from file extension
             const ext = ref.path.split('.').pop() || 'text';
             const languageMap: Record<string, string> = {
              'py': 'python',
              'js': 'javascript',
              'ts': 'typescript',
              'jsx': 'javascript',
              'tsx': 'typescript',
              'java': 'java',
              'cpp': 'cpp',
              'c': 'c',
              'h': 'c',
              'hpp': 'cpp',
              'cu': 'cuda',
              'go': 'go',
              'rs': 'rust',
              'rb': 'ruby',
              'php': 'php',
              'swift': 'swift',
              'kt': 'kotlin',
              'scala': 'scala',
              'yaml': 'yaml',
              'yml': 'yaml',
              'json': 'json',
              'md': 'markdown',
              'sh': 'bash',
              'bash': 'bash',
             };
             const language = languageMap[ext] || ext;

             codeBlocks.push({
               id: `block-${blockId}`,
               file: ref.path,
               startLine: ref.start_line,
               endLine: ref.end_line,
               code: code,
               language: language,
               qualified_name: ref.qualified_name || ref.identifier || '',
             });
          }
        } catch (fileError) {
          console.error(`Failed to read code file ${filePath} from repo ${actualRepo}:`, fileError);
          // Add block without code for all refs in this file
           for (const ref of refs) {
             const blockKey = `${ref.path}:${ref.start_line}:${ref.end_line}`;
             const blockId = crypto.createHash('md5').update(blockKey).digest('hex').slice(0, 12);

              codeBlocks.push({
                id: `block-${blockId}`,
                file: ref.path,
                startLine: ref.start_line,
                endLine: ref.end_line,
                code: `// Could not load code from ${actualRepo}/${ref.path}`,
                language: 'text',
                qualified_name: ref.qualified_name || ref.identifier || '',
              });
           }
        }
      }

      console.log(`[Code Blocks API] Successfully generated ${codeBlocks.length} code blocks`);

      return new Response(
        JSON.stringify({ code_blocks: codeBlocks }),
        {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
            'Cache-Control': 'no-cache, no-store, must-revalidate',
          },
        }
      );
    } catch (err: any) {
      if (err.code === 'ENOENT') {
        return new Response(
          JSON.stringify({ error: 'Chat log not found' }),
          { status: 404, headers: { 'Content-Type': 'application/json' } }
        );
      }
      throw err;
    }
  } catch (error) {
    console.error('Code blocks generation error:', error);
    return new Response(
      JSON.stringify({
        error: 'Failed to generate code blocks',
        details: error instanceof Error ? error.message : 'Unknown error'
      }),
      { status: 500, headers: { 'Content-Type': 'application/json' } }
    );
  }
}


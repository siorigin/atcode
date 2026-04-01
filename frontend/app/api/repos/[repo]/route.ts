// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { NextResponse } from 'next/server';
import { promises as fs } from 'fs';
import path from 'path';
import { getFastAPIUrl } from '@/lib/api-config';
import { getWikiDocDir, getWikiReposDir, getWikiChatDir } from '@/lib/paths';
import { open } from 'fs/promises';

const FASTAPI_URL = getFastAPIUrl();

/**
 * Read only the beginning of a JSON file to extract minimal metadata.
 * This avoids loading large operator files into memory.
 */
async function readOperatorMetadata(filePath: string): Promise<{
  id: string;
  referencesCount: number;
  codeBlocksCount: number;
  query: string;
} | null> {
  const fd = await open(filePath, 'r');
  try {
    const buffer = Buffer.alloc(4096); // Read only first 4KB
    const { bytesRead } = await fd.read(buffer, 0, 4096, 0);
    const partialContent = buffer.toString('utf-8', 0, bytesRead);

    // Try to parse the partial JSON to get metadata
    try {
      const data = JSON.parse(partialContent);
      return {
        id: data.id || path.basename(filePath, '.json'),
        referencesCount: data.references?.length || 0,
        codeBlocksCount: data.codeBlocks?.length || 0,
        query: data.query || '',
      };
    } catch {
      // If partial parsing fails, return null - will use defaults
      return null;
    }
  } catch {
    return null;
  } finally {
    await fd.close();
  }
}

/**
 * API endpoint to list all operators for a specific repository
 * Returns: { repo: string, operators: [{name: string, lastUpdated: string, metadata: any}] }
 */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ repo: string }> }
) {
  try {
    const { repo: repoName } = await params;

    // Path to this repo's operators (using centralized config)
    const repoPath = getWikiDocDir(repoName);

    // Check if repo exists
    try {
      await fs.access(repoPath);
    } catch {
      return NextResponse.json(
        { error: 'Repository not found', operators: [] },
        { status: 404 }
      );
    }

    // Read all JSON files (operators) in this repo
    // Filter out files starting with '_' (like _meta.json) which are used by overview system
    const files = await fs.readdir(repoPath);
    const jsonFiles = files.filter((file) =>
      file.endsWith('.json') && !file.startsWith('_')
    );

    // Collect operator metadata - read stats in parallel, then read minimal metadata
    const operators = await Promise.all(
      jsonFiles.map(async (file) => {
        const operatorName = file.replace('.json', '');
        const filePath = path.join(repoPath, file);

        try {
          const stats = await fs.stat(filePath);

          // Try to read minimal metadata (only first 4KB)
          const metadata = await readOperatorMetadata(filePath);

          return {
            name: operatorName,
            lastUpdated: stats.mtime.toISOString(),
            metadata: metadata || {
              id: operatorName,
              referencesCount: 0,
              codeBlocksCount: 0,
              query: '',
            },
          };
        } catch (error) {
          console.error(`Error reading operator ${operatorName}:`, error);
          return {
            name: operatorName,
            lastUpdated: new Date().toISOString(),
            metadata: {
              id: operatorName,
              referencesCount: 0,
              codeBlocksCount: 0,
              query: '',
            },
          };
        }
      })
    );

    // Sort alphabetically by name
    operators.sort((a, b) => a.name.localeCompare(b.name));

    return NextResponse.json(
      {
        repo: repoName,
        operators,
      },
      {
        headers: {
          'Cache-Control': 'private, max-age=30, stale-while-revalidate=60', // Cache for 30s
        },
      }
    );
  } catch (error) {
    console.error('Error listing operators:', error);
    return NextResponse.json(
      { error: 'Failed to list operators', operators: [] },
      { status: 500 }
    );
  }
}

/**
 * Helper to recursively delete a directory
 */
async function deleteDirectoryRecursive(dirPath: string): Promise<void> {
  try {
    const entries = await fs.readdir(dirPath, { withFileTypes: true });
    await Promise.all(
      entries.map(async (entry) => {
        const fullPath = path.join(dirPath, entry.name);
        if (entry.isDirectory()) {
          await deleteDirectoryRecursive(fullPath);
        } else {
          await fs.unlink(fullPath);
        }
      })
    );
    await fs.rmdir(dirPath);
  } catch (error) {
    // Ignore if directory doesn't exist
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') {
      throw error;
    }
  }
}

/**
 * API endpoint to delete a repository and all associated data
 * Deletes: wiki_doc, wiki_repos (local clone), chat logs, and knowledge graph
 */
export async function DELETE(
  request: Request,
  { params }: { params: Promise<{ repo: string }> }
) {
  try {
    const { repo: repoName } = await params;

    if (!repoName) {
      return NextResponse.json(
        { error: 'Repository name is required' },
        { status: 400 }
      );
    }

    const results: {
      docsDeleted: boolean;
      repoDeleted: boolean;
      chatLogsDeleted: boolean;
      graphDeleted: boolean;
      errors: string[];
    } = {
      docsDeleted: false,
      repoDeleted: false,
      chatLogsDeleted: false,
      graphDeleted: false,
      errors: [],
    };

    // 1. Delete wiki_doc (operator documentation)
    const wikiDocPath = getWikiDocDir(repoName);
    try {
      await deleteDirectoryRecursive(wikiDocPath);
      results.docsDeleted = true;
    } catch (error: any) {
      if (error.code !== 'ENOENT') {
        results.errors.push(`Failed to delete docs: ${error.message}`);
      }
    }

    // 2. Delete wiki_repos (local clone)
    const wikiRepoPath = getWikiReposDir(repoName);
    try {
      await deleteDirectoryRecursive(wikiRepoPath);
      results.repoDeleted = true;
    } catch (error: any) {
      if (error.code !== 'ENOENT') {
        results.errors.push(`Failed to delete local repo: ${error.message}`);
      }
    }

    // 3. Also try to delete from local_repos (another common location)
    const localRepoPath = path.join(process.cwd(), '..', 'local_repos', repoName);
    try {
      await deleteDirectoryRecursive(localRepoPath);
    } catch (error: any) {
      // Ignore if doesn't exist
    }

    // 4. Delete chat logs (using centralized config)
    const chatLogsPath = getWikiChatDir(repoName);
    try {
      await deleteDirectoryRecursive(chatLogsPath);
      results.chatLogsDeleted = true;
    } catch (error: any) {
      if (error.code !== 'ENOENT') {
        results.errors.push(`Failed to delete chat logs: ${error.message}`);
      }
    }

    // 5. Delete knowledge graph via FastAPI backend
    try {
      const graphResponse = await fetch(`${FASTAPI_URL}/api/graph/projects/${encodeURIComponent(repoName)}`, {
        method: 'DELETE',
        headers: {
          'Content-Type': 'application/json',
        },
      });

      if (graphResponse.ok) {
        const graphResult = await graphResponse.json();
        results.graphDeleted = graphResult.success || false;
      } else if (graphResponse.status !== 404) {
        // 404 means project doesn't exist in graph, which is okay
        const errorData = await graphResponse.json().catch(() => ({}));
        results.errors.push(`Failed to delete graph: ${errorData.detail || 'Unknown error'}`);
      }
    } catch (error: any) {
      results.errors.push(`Failed to connect to graph service: ${error.message}`);
    }

    // Determine overall success
    const hasAnyDeletion = results.docsDeleted || results.repoDeleted || results.chatLogsDeleted || results.graphDeleted;

    if (!hasAnyDeletion && results.errors.length > 0) {
      return NextResponse.json(
        {
          error: 'Failed to delete repository',
          details: results.errors,
        },
        { status: 500 }
      );
    }

    return NextResponse.json({
      success: true,
      message: `Repository '${repoName}' deleted successfully`,
      details: results,
    });
  } catch (error: any) {
    console.error('Error deleting repository:', error);
    return NextResponse.json(
      {
        error: 'Failed to delete repository',
        details: error.message,
      },
      { status: 500 }
    );
  }
}


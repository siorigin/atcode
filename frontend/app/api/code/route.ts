// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { NextRequest, NextResponse } from 'next/server';
import * as fs from 'fs';
import * as path from 'path';
import { getWikiReposDir, normalizeDataPath } from '@/lib/paths';

// Force dynamic rendering - this route uses dynamic file paths
export const dynamic = 'force-dynamic';

/**
 * GET /api/code?path=<file_path>
 * Fetch code from a file path (supports cross-repo references)
 */
export async function GET(request: NextRequest) {
  try {
    const searchParams = request.nextUrl.searchParams;
    const filePath = searchParams.get('path');

    if (!filePath) {
      return NextResponse.json(
        { error: 'Missing required parameter: path' },
        { status: 400 }
      );
    }

    // 🔧 支持跨仓库路径格式：data/wiki_repos/repo_name/file/path 或 wiki_repos/repo_name/file/path (legacy)
    // 或者直接的文件路径（假设在当前 repo 下）
    let fullFilePath: string;
    const wikiReposDir = getWikiReposDir();

    // Normalize path to use data/ prefix
    const normalizedPath = normalizeDataPath(filePath);

    if (normalizedPath.startsWith('data/wiki_repos/')) {
      // 完整路径格式：data/wiki_repos/repo_name/file/path
      const projectRoot = path.join(process.cwd(), '..');
      fullFilePath = path.join(projectRoot, normalizedPath);
    } else if (filePath.startsWith('wiki_repos/')) {
      // Legacy format: wiki_repos/repo_name/file/path -> data/wiki_repos/...
      fullFilePath = path.join(wikiReposDir, filePath.substring('wiki_repos/'.length));
    } else {
      // 相对路径格式（需要从 referer 或其他方式获取 repo）
      // 这种情况下，我们无法确定 repo，返回错误
      return NextResponse.json(
        { error: 'Path must include repository: use format data/wiki_repos/repo_name/file/path' },
        { status: 400 }
      );
    }

    // Security check: ensure the resolved path is within wiki_repos directory
    const resolvedPath = path.resolve(fullFilePath);
    const resolvedWikiReposPath = path.resolve(wikiReposDir);
    if (!resolvedPath.startsWith(resolvedWikiReposPath)) {
      return NextResponse.json(
        { error: 'Invalid file path' },
        { status: 403 }
      );
    }

    // Check if file exists
    if (!fs.existsSync(resolvedPath)) {
      return NextResponse.json(
        { error: `File not found: ${filePath}` },
        { status: 404 }
      );
    }

    // Read the entire file
    const fileContent = fs.readFileSync(resolvedPath, 'utf-8');

    // Return as JSON with code field
    return NextResponse.json(
      { code: fileContent },
      { status: 200 }
    );
  } catch (error) {
    console.error('Error fetching code (GET):', error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Failed to fetch code' },
      { status: 500 }
    );
  }
}

/**
 * POST /api/code
 * Fetch code snippet from a specific line range
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { repo, path: filePath, startLine, endLine } = body;

    if (!repo || !filePath || !startLine || !endLine) {
      return NextResponse.json(
        { error: 'Missing required parameters: repo, path, startLine, endLine' },
        { status: 400 }
      );
    }

    // Construct the full file path (using centralized config)
    const repoPath = getWikiReposDir(repo);
    const fullFilePath = path.join(repoPath, filePath);

    // Security check: ensure the resolved path is within the repo directory
    const resolvedPath = path.resolve(fullFilePath);
    const resolvedRepoPath = path.resolve(repoPath);
    if (!resolvedPath.startsWith(resolvedRepoPath)) {
      return NextResponse.json(
        { error: 'Invalid file path' },
        { status: 403 }
      );
    }

    // Check if file exists
    if (!fs.existsSync(resolvedPath)) {
      return NextResponse.json(
        { error: `File not found: ${filePath} in repo ${repo}` },
        { status: 404 }
      );
    }

    // Read the file
    const fileContent = fs.readFileSync(resolvedPath, 'utf-8');
    const lines = fileContent.split('\n');

    // Extract the requested lines (1-indexed)
    const start = Math.max(0, startLine - 1);
    const end = Math.min(lines.length, endLine);
    const codeSnippet = lines.slice(start, end).join('\n');

    // Return as plain text
    return new NextResponse(codeSnippet, {
      status: 200,
      headers: {
        'Content-Type': 'text/plain; charset=utf-8',
      },
    });
  } catch (error) {
    console.error('Error fetching code (POST):', error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Failed to fetch code' },
      { status: 500 }
    );
  }
}

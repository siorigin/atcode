// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Centralized path configuration for AtCode frontend.
 *
 * All persistent data (wiki_doc, wiki_repos, wiki_chat) is stored under ./data
 * relative to the project root for portability.
 */

import path from 'path';

/**
 * Get the project root directory (atcode/).
 * In dev mode, process.cwd() is typically the frontend directory.
 * In standalone prod mode, process.cwd() is usually
 * frontend/.next/standalone/frontend, so we need to walk back to the real
 * project root instead of resolving relative to the bundled output directory.
 */
export function getProjectRoot(): string {
  const configuredPwd = process.env.PWD?.trim();
  if (configuredPwd && path.basename(configuredPwd) === 'frontend') {
    return path.resolve(configuredPwd, '..');
  }

  const cwd = process.cwd();
  const standaloneSuffix = path.join('.next', 'standalone', 'frontend');
  if (cwd.endsWith(standaloneSuffix)) {
    return path.resolve(cwd, '..', '..', '..', '..');
  }

  if (path.basename(cwd) === 'frontend') {
    return path.resolve(cwd, '..');
  }

  return path.resolve(cwd, '..');
}

function getConfiguredDataDir(): string | null {
  const configured = process.env.ATCODE_DATA_DIR?.trim();
  if (!configured) {
    return null;
  }

  // Preserve existing manual-dev behavior for relative defaults like ../data.
  // Only honor explicit absolute overrides so frontend/backend can share one
  // runtime data directory across different checkouts on the same machine.
  if (!path.isAbsolute(configured)) {
    return null;
  }

  return configured;
}

/**
 * Get the data directory (atcode/data/).
 */
export function getDataDir(): string {
  return getConfiguredDataDir() || path.join(getProjectRoot(), 'data');
}

/**
 * Get the wiki_doc directory for storing generated documentation.
 * @param repoName - Optional repository name to get repo-specific directory
 */
export function getWikiDocDir(repoName?: string): string {
  const wikiDoc = path.join(getDataDir(), 'wiki_doc');
  if (repoName) {
    return path.join(wikiDoc, repoName);
  }
  return wikiDoc;
}

/**
 * Get the wiki_repos directory for storing cloned repositories.
 * @param repoName - Optional repository name to get repo-specific directory
 */
export function getWikiReposDir(repoName?: string): string {
  const wikiRepos = path.join(getDataDir(), 'wiki_repos');
  if (repoName) {
    return path.join(wikiRepos, repoName);
  }
  return wikiRepos;
}

/**
 * Get the wiki_chat directory for storing chat history.
 * @param repoName - Optional repository name to get repo-specific directory
 */
export function getWikiChatDir(repoName?: string): string {
  const wikiChat = path.join(getDataDir(), 'wiki_chat');
  if (repoName) {
    return path.join(wikiChat, repoName);
  }
  return wikiChat;
}

/**
 * Convert a file path to use the data directory structure.
 * Handles legacy paths like 'wiki_repos/repo/file' -> 'data/wiki_repos/repo/file'
 */
export function normalizeDataPath(filePath: string): string {
  // If already using data/ prefix, return as-is
  if (filePath.startsWith('data/')) {
    return filePath;
  }

  // Convert legacy wiki_* paths to data/wiki_* paths
  if (filePath.startsWith('wiki_repos/') ||
      filePath.startsWith('wiki_doc/') ||
      filePath.startsWith('wiki_chat/')) {
    return path.join('data', filePath);
  }

  return filePath;
}

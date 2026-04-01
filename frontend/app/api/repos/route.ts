// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { NextResponse } from 'next/server';
import { promises as fs } from 'fs';
import path from 'path';
import { getWikiDocDir, getWikiReposDir } from '@/lib/paths';

// Force dynamic rendering - this route uses dynamic file paths
export const dynamic = 'force-dynamic';

/**
 * Process a wiki_doc repo directory in parallel for better performance
 */
async function processWikiDocRepo(repoDir: any, wikiDocPath: string): Promise<{
  name: string;
  operatorCount: number;
  lastUpdated: string;
  hasDocs: boolean;
  researchCount: number;
}> {
  const repoName = repoDir.name;
  const repoPath = path.join(wikiDocPath, repoName);

  try {
    const files = await fs.readdir(repoPath);
    const jsonFiles = files.filter((file) => file.endsWith('.json') && !file.startsWith('_'));

    if (jsonFiles.length === 0) {
      return {
        name: repoName,
        operatorCount: 0,
        researchCount: 0,
        lastUpdated: new Date().toISOString(),
        hasDocs: true,
      };
    }

    // Parallel stat all JSON files to find the most recent one
    const statsPromises = jsonFiles.map(async (file) => {
      const filePath = path.join(repoPath, file);
      return fs.stat(filePath);
    });

    const statsResults = await Promise.all(statsPromises);
    const lastUpdated = statsResults.reduce((latest, stat) => {
      return stat.mtime > new Date(latest) ? stat.mtime.toISOString() : latest;
    }, new Date(0).toISOString());

    return {
      name: repoName,
      operatorCount: jsonFiles.length,
      researchCount: jsonFiles.length,
      lastUpdated,
      hasDocs: true,
    };
  } catch (error) {
    console.error(`Error reading wiki_doc repo ${repoName}:`, error);
    return {
      name: repoName,
      operatorCount: 0,
      researchCount: 0,
      lastUpdated: new Date().toISOString(),
      hasDocs: true,
    };
  }
}

/**
 * API endpoint to list all repositories in wiki_doc
 * Returns: { repos: [{name: string, operatorCount: number, researchCount: number, lastUpdated: string, hasDocs: boolean}] }
 */
export async function GET() {
  try {
    // Path to wiki_doc directory (using centralized config)
    const wikiDocPath = getWikiDocDir();
    // Path to local repositories (using centralized config)
    const localRepoPath = getWikiReposDir();

    const repos: Array<{
      name: string;
      operatorCount: number;
      lastUpdated: string;
      hasDocs: boolean;
      researchCount: number;
    }> = [];

    // Check wiki_doc directory (repos with generated docs) - process in parallel
    try {
      await fs.access(wikiDocPath);
      const wikiDocEntries = await fs.readdir(wikiDocPath, { withFileTypes: true });
      const wikiDocDirs = wikiDocEntries.filter((entry) => entry.isDirectory());

      // Process all repos in parallel
      const wikiDocRepos = await Promise.all(
        wikiDocDirs.map((repoDir) => processWikiDocRepo(repoDir, wikiDocPath))
      );
      repos.push(...wikiDocRepos);
    } catch {
      // wiki_doc doesn't exist yet, that's okay
      console.log('wiki_doc directory does not exist yet');
    }

    // Check local_repo directory (cloned repos without docs yet) - process in parallel
    try {
      await fs.access(localRepoPath);
      const localRepoEntries = await fs.readdir(localRepoPath, { withFileTypes: true });
      const localRepoDirs = localRepoEntries.filter((entry) => entry.isDirectory());

      const existingRepoNames = new Set(repos.map(r => r.name));

      // Process all local repos in parallel
      const localRepoPromises = localRepoDirs
        .filter((repoDir) => !existingRepoNames.has(repoDir.name))
        .map(async (repoDir) => {
          const repoName = repoDir.name;
          const repoPath = path.join(localRepoPath, repoName);

          try {
            const stats = await fs.stat(repoPath);
            return {
              name: repoName,
              operatorCount: 0,
              researchCount: 0,
              lastUpdated: stats.mtime.toISOString(),
              hasDocs: false,
            };
          } catch (error) {
            console.error(`Error reading local repo ${repoName}:`, error);
            return {
              name: repoName,
              operatorCount: 0,
              researchCount: 0,
              lastUpdated: new Date().toISOString(),
              hasDocs: false,
            };
          }
        });

      const localRepos = await Promise.all(localRepoPromises);
      repos.push(...localRepos);
    } catch {
      // local_repo doesn't exist yet, that's okay
      console.log('local_repo directory does not exist yet');
    }

    // Sort by last updated (most recent first)
    repos.sort((a, b) => new Date(b.lastUpdated).getTime() - new Date(a.lastUpdated).getTime());

    return NextResponse.json(
      { repos },
      {
        headers: {
          'Cache-Control': 'private, max-age=10, stale-while-revalidate=30', // Cache for 10s
        },
      }
    );
  } catch (error) {
    console.error('Error listing repositories:', error);
    return NextResponse.json(
      { error: 'Failed to list repositories', repos: [] },
      { status: 500 }
    );
  }
}


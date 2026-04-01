// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Add Repository API Route
 * Proxies requests to FastAPI backend for repository addition
 * Supports both remote (git clone) and local path modes
 * Uses unified task system for multi-user visibility and persistence
 */

import { NextResponse } from 'next/server';

import { getFastAPIUrl } from '@/lib/api-config';

const FASTAPI_URL = getFastAPIUrl();

export async function POST(request: Request) {
  try {
    const { repoUrl, localPath, projectName, branch, username, password, skip_embeddings } = await request.json();

    // Determine which mode to use: local path or remote URL
    const isLocalMode = !!localPath;

    if (!isLocalMode && !repoUrl) {
      return NextResponse.json({ error: 'Repository URL or local path is required' }, { status: 400 });
    }

    let response: Response;

    if (isLocalMode) {
      // Local mode: call /api/repos/add-local
      response = await fetch(`${FASTAPI_URL}/api/repos/add-local`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          local_path: localPath,
          project_name: projectName || null,
          skip_embeddings: skip_embeddings || false,
        }),
      });
    } else {
      // Remote mode: call /api/repos/add (original behavior)
      response = await fetch(`${FASTAPI_URL}/api/repos/add`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          repo_url: repoUrl,
          branch: branch || null,
          username: username || null,
          password: password || null,
          skip_embeddings: skip_embeddings || false,
        }),
      });
    }

    const data = await response.json();

    if (!response.ok) {
      return NextResponse.json(
        { error: data.detail || 'Failed to add repository' },
        { status: response.status }
      );
    }

    // Return response with task_id renamed to jobId for frontend compatibility
    return NextResponse.json({
      success: data.success,
      jobId: data.task_id,  // Map task_id to jobId for frontend
      repoName: data.repo_name,
      message: data.message,
      isLocal: isLocalMode,  // Indicate whether this was a local add
    });

  } catch (error: any) {
    console.error('Error starting repository addition:', error);
    return NextResponse.json(
      {
        error: 'Failed to connect to backend service',
        details: error.message
      },
      { status: 503 }
    );
  }
}

// GET endpoint to check job status - proxies to unified task API
export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const jobId = searchParams.get('jobId');

  if (!jobId) {
    return NextResponse.json({ error: 'Job ID is required' }, { status: 400 });
  }

  try {
    // Call unified task API
    const response = await fetch(`${FASTAPI_URL}/api/tasks/${encodeURIComponent(jobId)}`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    if (!response.ok) {
      if (response.status === 404) {
        return NextResponse.json({ error: 'Job not found' }, { status: 404 });
      }
      throw new Error(`Backend returned ${response.status}`);
    }

    const task = await response.json();

    // Transform task format to match expected job format for frontend
    return NextResponse.json({
      id: task.task_id,
      type: 'repo',
      name: task.repo_name || 'unknown',
      status: task.status,
      progress: task.progress,
      currentStep: task.status_message || task.step,
      error: task.error,
      startTime: task.created_at ? new Date(task.created_at).getTime() : Date.now(),
      endTime: task.completed_at ? new Date(task.completed_at).getTime() : undefined,
      logs: task.status_message ? [task.status_message] : [],
    });

  } catch (error: any) {
    console.error('Error fetching job status:', error);
    return NextResponse.json(
      { error: 'Failed to fetch job status', details: error.message },
      { status: 503 }
    );
  }
}


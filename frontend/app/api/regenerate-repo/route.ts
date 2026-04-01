// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Regenerate Repository Documentation API Route
 * Proxies requests to FastAPI backend for async batch regeneration
 * Uses unified task system for multi-user visibility and persistence
 */

import { NextResponse, NextRequest } from 'next/server';

import { getFastAPIUrl } from '@/lib/api-config';

const FASTAPI_URL = getFastAPIUrl();

export async function POST(request: NextRequest) {
  try {
    const { repoName, operators } = await request.json();

    if (!repoName) {
      return NextResponse.json({ error: 'Repository name is required' }, { status: 400 });
    }

    // Call FastAPI backend async regeneration endpoint
    const response = await fetch(`${FASTAPI_URL}/api/docs/regenerate/async`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        repo: repoName,
        operators: operators || null,  // null means regenerate all
      }),
    });

    const data = await response.json();

    if (!response.ok) {
      return NextResponse.json(
        { error: data.detail || data.message || 'Failed to start regeneration' },
        { status: response.status }
      );
    }

    // Return response with task_id renamed to jobId for frontend compatibility
    return NextResponse.json({
      success: data.success,
      jobId: data.task_id,  // Map task_id to jobId for frontend
      repoName: data.repo_name,
      operatorCount: data.operator_count,
      message: data.message,
    });

  } catch (error: any) {
    console.error('Error starting repository regeneration:', error);
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
      type: 'regen',
      name: task.repo_name || 'unknown',
      status: task.status,
      progress: task.progress,
      currentStep: task.status_message || task.step,
      error: task.error,
      startTime: task.created_at ? new Date(task.created_at).getTime() : Date.now(),
      endTime: task.completed_at ? new Date(task.completed_at).getTime() : undefined,
      logs: task.status_message ? [task.status_message] : [],
      // Include result data if available
      completedOperators: task.result?.success_count,
      totalOperators: task.result?.total,
    });

  } catch (error: any) {
    console.error('Error fetching job status:', error);
    return NextResponse.json(
      { error: 'Failed to fetch job status', details: error.message },
      { status: 503 }
    );
  }
}

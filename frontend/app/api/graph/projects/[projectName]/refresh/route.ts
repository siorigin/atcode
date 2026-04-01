// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Graph Project Refresh API Route
 * Proxy POST requests to FastAPI backend
 */

import { NextRequest, NextResponse } from 'next/server';
import { getFastAPIUrl } from '@/lib/api-config';

const FASTAPI_URL = getFastAPIUrl();

interface RouteParams {
  params: Promise<{
    projectName: string;
  }>;
}

export async function POST(request: NextRequest, { params }: RouteParams) {
  const { projectName } = await params;
  const { searchParams } = new URL(request.url);
  const repoPath = searchParams.get('repo_path');
  const fastMode = searchParams.get('fast_mode');

  try {
    const url = new URL(`${FASTAPI_URL}/api/graph/projects/${encodeURIComponent(projectName)}/refresh`);
    if (repoPath) {
      url.searchParams.set('repo_path', repoPath);
    }
    if (fastMode) {
      url.searchParams.set('fast_mode', fastMode);
    }

    const response = await fetch(url.toString(), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error('Failed to refresh project graph:', error);
    return NextResponse.json(
      { error: 'Failed to connect to graph service', success: false },
      { status: 503 }
    );
  }
}

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Graph Project Stats API Route
 * Proxy GET requests to FastAPI backend for single project statistics
 */

import { NextRequest, NextResponse } from 'next/server';
import { getFastAPIUrl } from '@/lib/api-config';

const FASTAPI_URL = getFastAPIUrl();

interface RouteParams {
  params: Promise<{
    projectName: string;
  }>;
}

export async function GET(request: NextRequest, { params }: RouteParams) {
  const { projectName } = await params;

  try {
    const response = await fetch(
      `${FASTAPI_URL}/api/graph/projects/${encodeURIComponent(projectName)}/stats`,
      {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
        },
      }
    );

    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error('Failed to get project stats:', error);
    return NextResponse.json(
      {
        error: 'Failed to connect to graph service',
        name: projectName,
        node_count: 0,
        relationship_count: 0,
        has_graph: false
      },
      { status: 503 }
    );
  }
}

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Graph Job Status API Route
 * Proxy GET requests to FastAPI backend
 */

import { NextRequest, NextResponse } from 'next/server';
import { getFastAPIUrl } from '@/lib/api-config';

const FASTAPI_URL = getFastAPIUrl();

interface RouteParams {
  params: Promise<{
    jobId: string;
  }>;
}

export async function GET(request: NextRequest, { params }: RouteParams) {
  const { jobId } = await params;

  try {
    const response = await fetch(
      `${FASTAPI_URL}/api/graph/jobs/${encodeURIComponent(jobId)}`,
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
    console.error('Failed to get job status:', error);
    return NextResponse.json(
      { error: 'Failed to connect to graph service' },
      { status: 503 }
    );
  }
}

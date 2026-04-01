// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Task Status API Route
 * Proxy GET requests to FastAPI backend for specific task status
 *
 * Note: This route must NOT be cached to ensure real-time task status.
 */

import { NextRequest, NextResponse } from 'next/server';
import { getFastAPIUrl } from '@/lib/api-config';

const FASTAPI_URL = getFastAPIUrl();

interface RouteParams {
  params: Promise<{
    taskId: string;
  }>;
}

export async function GET(request: NextRequest, { params }: RouteParams) {
  const { taskId } = await params;

  try {
    const response = await fetch(
      `${FASTAPI_URL}/api/tasks/${encodeURIComponent(taskId)}`,
      {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
        },
        // Disable caching for real-time status
        cache: 'no-store',
      }
    );

    const data = await response.json();
    return NextResponse.json(data, {
      status: response.status,
      headers: {
        'Cache-Control': 'no-store, no-cache, must-revalidate',
        'Pragma': 'no-cache',
      },
    });
  } catch (error) {
    console.error('Failed to get task status:', error);
    return NextResponse.json(
      { error: 'Failed to connect to task service' },
      { status: 503 }
    );
  }
}

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Task Cancel API Route
 * Proxy POST requests to FastAPI backend to cancel a task
 */

import { NextRequest, NextResponse } from 'next/server';
import { getFastAPIUrl } from '@/lib/api-config';

const FASTAPI_URL = getFastAPIUrl();

interface RouteParams {
  params: Promise<{
    taskId: string;
  }>;
}

export async function POST(request: NextRequest, { params }: RouteParams) {
  const { taskId } = await params;

  try {
    const response = await fetch(
      `${FASTAPI_URL}/api/tasks/${encodeURIComponent(taskId)}/cancel`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
      }
    );

    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error('Failed to cancel task:', error);
    return NextResponse.json(
      { error: 'Failed to connect to task service', success: false },
      { status: 503 }
    );
  }
}

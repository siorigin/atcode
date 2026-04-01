// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Active Tasks API Route
 * Proxy GET requests to FastAPI backend for listing active tasks
 *
 * Uses short cache (5 seconds) to reduce server load while keeping
 * task status reasonably fresh.
 */

import { NextRequest, NextResponse } from 'next/server';
import { getFastAPIUrl } from '@/lib/api-config';

const FASTAPI_URL = getFastAPIUrl();

// Force dynamic rendering - this route uses searchParams
export const dynamic = 'force-dynamic';

export async function GET(request: NextRequest) {
  try {
    // Forward query parameters
    const searchParams = request.nextUrl.searchParams;
    const queryString = searchParams.toString();
    const url = `${FASTAPI_URL}/api/tasks/active${queryString ? `?${queryString}` : ''}`;

    const response = await fetch(url, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
      // Use short cache to reduce server load
      next: { revalidate: 5 },
    });

    const data = await response.json();

    // Return with short cache headers
    return NextResponse.json(data, {
      status: response.status,
      headers: {
        'Cache-Control': 'max-age=5, must-revalidate',
      },
    });
  } catch (error) {
    console.error('Failed to get active tasks:', error);
    return NextResponse.json(
      { error: 'Failed to connect to task service', tasks: [], total: 0 },
      { status: 503 }
    );
  }
}

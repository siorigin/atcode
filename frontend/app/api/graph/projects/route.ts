// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Graph Projects API Route
 * Proxy requests to FastAPI backend with caching
 */

import { NextRequest, NextResponse } from 'next/server';

import { getFastAPIUrl } from '@/lib/api-config';

const FASTAPI_URL = getFastAPIUrl();

// No caching - always fetch fresh from backend
export const revalidate = 0;
export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    const response = await fetch(`${FASTAPI_URL}/api/graph/projects`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
      cache: 'no-store',
    });

    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error('Failed to fetch graph projects:', error);
    return NextResponse.json(
      {
        error: 'Failed to connect to graph service',
        projects: [],
        total: 0,
        connected: false,
        cache_epoch: null,
      },
      { status: 503 }
    );
  }
}

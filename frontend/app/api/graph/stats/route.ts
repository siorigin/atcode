// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Graph Stats API Route
 * Proxy requests to FastAPI backend with caching
 */

import { NextResponse } from 'next/server';
import { getFastAPIUrl } from '@/lib/api-config';

const FASTAPI_URL = getFastAPIUrl();

// No caching - always fetch fresh from backend
export const revalidate = 0;
export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    const response = await fetch(`${FASTAPI_URL}/api/graph/stats`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
      cache: 'no-store',
    });

    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error('Failed to fetch graph stats:', error);
    return NextResponse.json(
      {
        total_projects: 0,
        total_nodes: 0,
        total_relationships: 0,
        node_types: {},
        connected: false,
      },
      { status: 503 }
    );
  }
}

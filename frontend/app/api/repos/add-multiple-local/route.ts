// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Add Multiple Local Repositories API Route
 * Proxy POST request to FastAPI backend for batch adding local directories
 */

import { NextRequest, NextResponse } from 'next/server';
import { getFastAPIUrl } from '@/lib/api-config';

const FASTAPI_URL = getFastAPIUrl();

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();

    const response = await fetch(
      `${FASTAPI_URL}/api/repos/add-multiple-local`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(body),
      }
    );

    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error('Failed to add multiple repositories:', error);
    return NextResponse.json(
      { error: 'Failed to connect to backend service', results: [], successful: 0, failed: 0 },
      { status: 503 }
    );
  }
}

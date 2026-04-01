// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * List Subdirectories API Route
 * Proxy GET request to FastAPI backend for scanning subdirectories
 */

import { NextRequest, NextResponse } from 'next/server';
import { getFastAPIUrl } from '@/lib/api-config';

const FASTAPI_URL = getFastAPIUrl();

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const path = searchParams.get('path');

    if (!path) {
      return NextResponse.json(
        { error: 'Path parameter is required' },
        { status: 400 }
      );
    }

    const response = await fetch(
      `${FASTAPI_URL}/api/repos/list-subdirectories?path=${encodeURIComponent(path)}`,
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
    console.error('Failed to list subdirectories:', error);
    return NextResponse.json(
      { error: 'Failed to connect to backend service', subdirectories: [], total: 0 },
      { status: 503 }
    );
  }
}

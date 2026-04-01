// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Graph Database API Route
 * Proxy requests to FastAPI backend for database-level operations
 */

import { NextRequest, NextResponse } from 'next/server';
import { getFastAPIUrl } from '@/lib/api-config';

const FASTAPI_URL = getFastAPIUrl();

/**
 * DELETE /api/graph/database?confirm=true
 * Clean the entire graph database
 */
export async function DELETE(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams;
  const confirm = searchParams.get('confirm');

  if (confirm !== 'true') {
    return NextResponse.json(
      { detail: 'Must set confirm=true to delete entire database' },
      { status: 400 }
    );
  }

  try {
    const response = await fetch(`${FASTAPI_URL}/api/graph/database?confirm=true`, {
      method: 'DELETE',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error: any) {
    console.error('Failed to clean graph database:', error);
    return NextResponse.json(
      { detail: error.message || 'Failed to clean graph database' },
      { status: 503 }
    );
  }
}

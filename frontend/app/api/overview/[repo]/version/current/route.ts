// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Set Current Version API Route
 * Update the current (default) version for a repository's documentation
 */

import { NextRequest, NextResponse } from 'next/server';
import { getFastAPIUrl } from '@/lib/api-config';

const FASTAPI_URL = getFastAPIUrl();

export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ repo: string }> }
) {
  const { repo } = await params;

  try {
    const body = await request.json();

    const response = await fetch(`${FASTAPI_URL}/api/overview/${repo}/version/current`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    });

    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error('Failed to set current version:', error);
    return NextResponse.json(
      { success: false, error: 'Failed to connect to overview service' },
      { status: 503 }
    );
  }
}

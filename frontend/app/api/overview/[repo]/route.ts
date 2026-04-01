// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Overview API Route - Get overview document for a repository
 * Proxy requests to FastAPI backend
 */

import { NextRequest, NextResponse } from 'next/server';
import { getFastAPIUrl } from '@/lib/api-config';

const FASTAPI_URL = getFastAPIUrl();

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ repo: string }> }
) {
  const { repo } = await params;

  // Forward query parameters (e.g., version_id)
  const searchParams = request.nextUrl.searchParams;
  const queryString = searchParams.toString();
  const url = queryString
    ? `${FASTAPI_URL}/api/overview/${repo}?${queryString}`
    : `${FASTAPI_URL}/api/overview/${repo}`;

  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    const response = await fetch(url, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
      signal: controller.signal,
    });
    clearTimeout(timer);

    const data = await response.json();
    return NextResponse.json(data, {
      status: response.status,
      headers: {
        'Cache-Control': 'private, max-age=60, stale-while-revalidate=120',
      },
    });
  } catch (error) {
    console.error('Failed to fetch overview:', error);
    return NextResponse.json(
      { error: 'Failed to connect to overview service' },
      { status: 503 }
    );
  }
}

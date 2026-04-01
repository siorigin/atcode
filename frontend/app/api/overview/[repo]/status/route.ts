// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Overview Status API Route
 * Check if overview document exists for a repository
 */

import { NextRequest, NextResponse } from 'next/server';
import { getFastAPIUrl } from '@/lib/api-config';

const FASTAPI_URL = getFastAPIUrl();

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ repo: string }> }
) {
  const { repo } = await params;

  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    const response = await fetch(`${FASTAPI_URL}/api/overview/${repo}/status`, {
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
        'Cache-Control': 'private, max-age=30, stale-while-revalidate=60',
      },
    });
  } catch (error) {
    console.error('Failed to fetch overview status:', error);
    return NextResponse.json(
      { exists: false, repo, error: 'Failed to connect to overview service' },
      { status: 503 }
    );
  }
}

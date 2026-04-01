// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Task Status API Route
 * GET /api/overview/[repo]/generate/[taskId]
 * Proxies task status polling requests to FastAPI backend
 */

import { NextRequest, NextResponse } from 'next/server';
import { getFastAPIUrl } from '@/lib/api-config';

const FASTAPI_URL = getFastAPIUrl();

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ repo: string; taskId: string }> }
) {
  const { repo, taskId } = await params;

  try {
    // Forward authentication headers from the original request
    const headers = new Headers({
      'Content-Type': 'application/json',
    });

    // Forward user-agent for anonymous ID generation
    const userAgent = request.headers.get('user-agent');
    if (userAgent) {
      headers.set('user-agent', userAgent);
    }

    // Forward X-Forwarded-For for IP tracking
    const forwarded = request.headers.get('x-forwarded-for');
    if (forwarded) {
      headers.set('x-forwarded-for', forwarded);
    }

    // Forward any authorization headers
    const authorization = request.headers.get('authorization');
    if (authorization) {
      headers.set('authorization', authorization);
    }

    const response = await fetch(
      `${FASTAPI_URL}/api/overview/${repo}/generate/${taskId}`,
      {
        method: 'GET',
        headers,
      }
    );

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ detail: 'Unknown error' }));
      return NextResponse.json(
        { error: errorData.detail || 'Failed to get task status' },
        { status: response.status }
      );
    }

    const data = await response.json();
    return NextResponse.json(data);
  } catch (error) {
    console.error('Failed to fetch task status:', error);
    return NextResponse.json(
      { error: 'Failed to connect to task service' },
      { status: 503 }
    );
  }
}

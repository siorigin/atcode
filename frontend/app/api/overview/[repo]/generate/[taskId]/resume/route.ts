// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { NextRequest, NextResponse } from 'next/server';
import { getFastAPIUrl } from '@/lib/api-config';

const FASTAPI_URL = getFastAPIUrl();

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ repo: string; taskId: string }> }
) {
  const { repo, taskId } = await params;

  try {
    const headers = new Headers({
      'Content-Type': 'application/json',
    });

    const userAgent = request.headers.get('user-agent');
    if (userAgent) headers.set('user-agent', userAgent);

    const forwarded = request.headers.get('x-forwarded-for');
    if (forwarded) headers.set('x-forwarded-for', forwarded);

    const authorization = request.headers.get('authorization');
    if (authorization) headers.set('authorization', authorization);

    const response = await fetch(
      `${FASTAPI_URL}/api/overview/${repo}/generate/${taskId}/resume`,
      {
        method: 'POST',
        headers,
      }
    );

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      return NextResponse.json(
        { error: data.detail || data.message || 'Failed to resume generation' },
        { status: response.status }
      );
    }

    return NextResponse.json(data);
  } catch (error) {
    console.error('Failed to resume overview generation:', error);
    return NextResponse.json(
      { error: 'Failed to connect to overview service' },
      { status: 503 }
    );
  }
}

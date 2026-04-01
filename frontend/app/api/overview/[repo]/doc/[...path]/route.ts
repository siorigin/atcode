// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Overview Doc API Route
 * Get specific markdown document from documentation
 */

import { NextRequest, NextResponse } from 'next/server';
import { getFastAPIUrl } from '@/lib/api-config';

const FASTAPI_URL = getFastAPIUrl();

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ repo: string; path: string[] }> }
) {
  const { repo, path } = await params;
  // Next.js decodes [...path] segments; re-encode for the backend URL
  const docPath = path.map(segment => encodeURIComponent(segment)).join('/');

  // Forward query parameters (e.g., version_id)
  const searchParams = request.nextUrl.searchParams;
  const queryString = searchParams.toString();
  const url = queryString
    ? `${FASTAPI_URL}/api/overview/${repo}/doc/${docPath}?${queryString}`
    : `${FASTAPI_URL}/api/overview/${repo}/doc/${docPath}`;

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

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ detail: 'Document not found' }));
      return NextResponse.json(
        { error: errorData.detail || 'Failed to fetch document' },
        { status: response.status }
      );
    }

    const content = await response.text();
    return new Response(content, {
      headers: {
        'Content-Type': 'text/markdown; charset=utf-8',
      },
    });
  } catch (error) {
    console.error('Failed to fetch overview doc:', error);
    return NextResponse.json(
      { error: 'Failed to connect to overview service' },
      { status: 503 }
    );
  }
}

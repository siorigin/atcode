// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * PDF proxy route — streams PDF from FastAPI backend to ensure same-origin
 * iframe embedding with Content-Disposition: inline.
 */

import { NextRequest, NextResponse } from 'next/server';

import { getFastAPIUrl } from '@/lib/api-config';

const FASTAPI_URL = getFastAPIUrl();

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ paperId: string }> }
) {
  const { paperId } = await params;

  try {
    const forwardedHeaders = new Headers();
    const range = request.headers.get('range');
    if (range) {
      forwardedHeaders.set('Range', range);
    }

    const upstream = await fetch(
      `${FASTAPI_URL}/api/papers/${encodeURIComponent(paperId)}/pdf`,
      {
        method: 'GET',
        headers: forwardedHeaders,
        cache: 'no-store',
      }
    );

    if (!upstream.ok) {
      return NextResponse.json(
        { error: upstream.status === 404 ? 'PDF not found' : 'Failed to fetch PDF' },
        { status: upstream.status }
      );
    }

    if (!upstream.body) {
      return NextResponse.json(
        { error: 'PDF response body unavailable' },
        { status: 502 }
      );
    }

    const responseHeaders = new Headers();
    responseHeaders.set(
      'Content-Type',
      upstream.headers.get('content-type') || 'application/pdf'
    );
    responseHeaders.set(
      'Content-Disposition',
      upstream.headers.get('content-disposition') || 'inline'
    );

    for (const headerName of ['content-length', 'content-range', 'accept-ranges', 'etag', 'last-modified']) {
      const value = upstream.headers.get(headerName);
      if (value) {
        responseHeaders.set(headerName, value);
      }
    }

    return new NextResponse(upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch (error) {
    console.error('PDF proxy error:', error);
    return NextResponse.json(
      { error: 'Failed to proxy PDF' },
      { status: 502 }
    );
  }
}

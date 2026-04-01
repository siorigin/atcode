// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Thumbnail proxy route — proxies HuggingFace thumbnail images to avoid CORS.
 * GET /api/papers/thumbnail?url=https://...
 */

import { NextRequest, NextResponse } from 'next/server';
import { ProxyAgent, fetch as undiciFetch } from 'undici';

const ALLOWED_HOSTS = ['huggingface.co', 'cdn-thumbnails.huggingface.co', 'cdn-uploads.huggingface.co'];

/** Return the first defined proxy URL from standard env vars, or undefined. */
function getProxyUrl(): string | undefined {
  return (
    process.env.HTTPS_PROXY ||
    process.env.https_proxy ||
    process.env.HTTP_PROXY ||
    process.env.http_proxy ||
    process.env.ALL_PROXY ||
    process.env.all_proxy ||
    undefined
  );
}

export async function GET(request: NextRequest) {
  const url = request.nextUrl.searchParams.get('url');

  if (!url) {
    return NextResponse.json({ error: 'Missing url parameter' }, { status: 400 });
  }

  try {
    const parsed = new URL(url);
    if (!ALLOWED_HOSTS.some((h) => parsed.hostname === h || parsed.hostname.endsWith('.' + h))) {
      return NextResponse.json({ error: 'URL not allowed' }, { status: 403 });
    }

    const fetchOptions: Parameters<typeof undiciFetch>[1] = {
      headers: { 'User-Agent': 'AtCode/1.0' },
    };

    const proxyUrl = getProxyUrl();
    if (proxyUrl) {
      fetchOptions.dispatcher = new ProxyAgent(proxyUrl);
    }

    const upstream = await undiciFetch(url, fetchOptions);

    if (!upstream.ok) {
      return NextResponse.json({ error: 'Failed to fetch thumbnail' }, { status: upstream.status });
    }

    const buffer = await upstream.arrayBuffer();
    const contentType = upstream.headers.get('content-type') || 'image/jpeg';

    return new NextResponse(buffer, {
      status: 200,
      headers: {
        'Content-Type': contentType,
        'Cache-Control': 'public, max-age=86400, immutable',
        'Content-Length': String(buffer.byteLength),
      },
    });
  } catch (error) {
    console.error('Thumbnail proxy error:', error);
    return NextResponse.json({ error: 'Failed to proxy thumbnail' }, { status: 502 });
  }
}

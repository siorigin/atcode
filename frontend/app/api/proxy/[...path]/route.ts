// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Generic backend proxy for browser-side HTTP/SSE requests.
 *
 * This keeps browser traffic same-origin with the frontend so SSH port-forward,
 * LAN access, and reverse proxies do not depend on the browser reaching the
 * FastAPI host directly.
 */

import { NextRequest, NextResponse } from 'next/server';
import { getFastAPIUrl } from '@/lib/api-config';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

const PROXY_PREFIX = '/api/proxy/';
const PROXY_TIMEOUT_MS = 120_000; // 2 minutes — generous for long-running API calls
const HOP_BY_HOP_HEADERS = new Set([
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
  'host',
]);

function buildBackendUrl(request: NextRequest): string {
  const backendBase = getFastAPIUrl().replace(/\/$/, '');
  const incomingPath = request.nextUrl.pathname.startsWith(PROXY_PREFIX)
    ? request.nextUrl.pathname.slice(PROXY_PREFIX.length)
    : '';
  const query = request.nextUrl.search;
  return `${backendBase}/${incomingPath}${query}`;
}

function copyHeaders(source: Headers): Headers {
  const headers = new Headers();
  source.forEach((value, key) => {
    if (!HOP_BY_HOP_HEADERS.has(key.toLowerCase())) {
      headers.append(key, value);
    }
  });
  return headers;
}

async function proxyRequest(request: NextRequest): Promise<Response> {
  const backendUrl = buildBackendUrl(request);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), PROXY_TIMEOUT_MS);

  const init: RequestInit & { duplex?: 'half' } = {
    method: request.method,
    headers: copyHeaders(request.headers),
    cache: 'no-store',
    redirect: 'manual',
    signal: controller.signal,
  };

  if (!['GET', 'HEAD'].includes(request.method)) {
    init.body = await request.arrayBuffer();
    init.duplex = 'half';
  }

  try {
    const response = await fetch(backendUrl, init);
    const responseHeaders = copyHeaders(response.headers);

    // Prevent buffering for streaming responses (SSE / chunked)
    const contentType = response.headers.get('content-type') || '';
    if (
      contentType.includes('text/event-stream') ||
      contentType.includes('application/x-ndjson')
    ) {
      responseHeaders.set('X-Accel-Buffering', 'no');
      responseHeaders.set('Cache-Control', 'no-cache, no-transform');
    }

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  } catch (error) {
    const isAbort =
      error instanceof DOMException && error.name === 'AbortError';
    console.error(
      `[Proxy] ${request.method} ${backendUrl} ${isAbort ? 'timed out' : 'failed'}:`,
      error,
    );
    return NextResponse.json(
      {
        detail: isAbort
          ? 'Backend request timed out'
          : 'Failed to connect to backend service',
      },
      { status: isAbort ? 504 : 503 },
    );
  } finally {
    clearTimeout(timeout);
  }
}

export async function GET(request: NextRequest) {
  return proxyRequest(request);
}

export async function POST(request: NextRequest) {
  return proxyRequest(request);
}

export async function PUT(request: NextRequest) {
  return proxyRequest(request);
}

export async function PATCH(request: NextRequest) {
  return proxyRequest(request);
}

export async function DELETE(request: NextRequest) {
  return proxyRequest(request);
}

export async function OPTIONS(request: NextRequest) {
  return proxyRequest(request);
}

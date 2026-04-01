// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Overview Generate API Route (SSE Streaming)
 * Stream overview document generation progress from FastAPI backend
 */

import { NextRequest } from 'next/server';
import { getFastAPIUrl } from '@/lib/api-config';

const FASTAPI_URL = getFastAPIUrl();

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ repo: string }> }
) {
  const { repo } = await params;

  try {
    const body = await request.json();

    // Forward authentication headers from the original request
    const headers = new Headers({
      'Content-Type': 'application/json',
      'Accept': 'text/event-stream',
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

    // Make request to FastAPI backend
    const response = await fetch(`${FASTAPI_URL}/api/overview/${repo}/generate`, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    });

    // Check if response is OK
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ detail: 'Unknown error' }));
      return new Response(JSON.stringify({
        type: 'error',
        content: errorData.detail || 'Failed to generate overview',
      }), {
        status: response.status,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    // Stream the SSE response back to the client
    const readable = response.body;
    if (!readable) {
      return new Response(JSON.stringify({
        type: 'error',
        content: 'No response body from server',
      }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    // Return streaming response
    return new Response(readable, {
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'X-Accel-Buffering': 'no',
      },
    });
  } catch (error) {
    console.error('Failed to generate overview:', error);
    return new Response(JSON.stringify({
      type: 'error',
      content: 'Failed to connect to overview service',
    }), {
      status: 503,
      headers: { 'Content-Type': 'application/json' },
    });
  }
}

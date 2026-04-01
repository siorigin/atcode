// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Task SSE Stream API Route
 * GET /api/overview/[repo]/generate/[taskId]/stream
 * Proxies SSE streaming requests to FastAPI backend for real-time task updates
 */

import { NextRequest } from 'next/server';
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
      'Accept': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
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
      `${FASTAPI_URL}/api/overview/${repo}/generate/${taskId}/stream`,
      {
        method: 'GET',
        headers,
      }
    );

    if (!response.ok) {
      console.error(`SSE fetch failed: ${response.status} ${response.statusText}`);
      return new Response(
        `data: ${JSON.stringify({ error: 'Failed to connect to task service' })}\n\n`,
        {
          status: response.status,
          headers: { 'Content-Type': 'text/event-stream' },
        }
      );
    }

    // Stream the SSE response back to the client
    const readable = response.body;
    if (!readable) {
      return new Response(
        `data: ${JSON.stringify({ error: 'No response body from server' })}\n\n`,
        {
          status: 500,
          headers: { 'Content-Type': 'text/event-stream' },
        }
      );
    }

    // Return streaming response with proper headers
    return new Response(readable, {
      status: 200,
      headers: {
        'Content-Type': 'text/event-stream; charset=utf-8',
        'Cache-Control': 'no-cache, no-transform',
        'Connection': 'keep-alive',
        'X-Accel-Buffering': 'no',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
      },
    });
  } catch (error) {
    console.error('Failed to stream task updates:', error);
    return new Response(
      `data: ${JSON.stringify({ error: 'Failed to connect to task service' })}\n\n`,
      {
        status: 503,
        headers: { 'Content-Type': 'text/event-stream' },
      }
    );
  }
}

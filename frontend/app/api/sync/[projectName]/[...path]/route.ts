// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Sync API Proxy Route
 *
 * Proxies sync-related requests to the FastAPI backend.
 * Handles all sync operations for a project.
 */

import { NextRequest, NextResponse } from 'next/server';
import { getFastAPIUrl } from '@/lib/api-config';

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ projectName: string; path: string[] }> }
) {
  const { projectName, path } = await params;
  const pathStr = path.join('/');
  const searchParams = request.nextUrl.searchParams.toString();
  const queryString = searchParams ? `?${searchParams}` : '';

  // Map frontend path to backend path
  const backendUrl = `${getFastAPIUrl()}/repos/${encodeURIComponent(projectName)}/sync/${pathStr}${queryString}`;

  try {
    const response = await fetch(backendUrl, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    const data = await response.json();

    if (!response.ok) {
      return NextResponse.json(data, { status: response.status });
    }

    return NextResponse.json(data);
  } catch (error: any) {
    console.error(`[Sync API] GET ${backendUrl} failed:`, error);
    return NextResponse.json(
      { detail: error.message || 'Failed to connect to backend' },
      { status: 503 }
    );
  }
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ projectName: string; path: string[] }> }
) {
  const { projectName, path } = await params;
  const pathStr = path.join('/');
  const searchParams = request.nextUrl.searchParams.toString();
  const queryString = searchParams ? `?${searchParams}` : '';

  // Map frontend path to backend path
  const backendUrl = `${getFastAPIUrl()}/repos/${encodeURIComponent(projectName)}/sync/${pathStr}${queryString}`;

  try {
    // Get request body if present
    let body: string | undefined;
    try {
      const json = await request.json();
      body = JSON.stringify(json);
    } catch {
      // No body or invalid JSON
    }

    const response = await fetch(backendUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body,
    });

    const data = await response.json();

    if (!response.ok) {
      return NextResponse.json(data, { status: response.status });
    }

    return NextResponse.json(data);
  } catch (error: any) {
    console.error(`[Sync API] POST ${backendUrl} failed:`, error);
    return NextResponse.json(
      { detail: error.message || 'Failed to connect to backend' },
      { status: 503 }
    );
  }
}

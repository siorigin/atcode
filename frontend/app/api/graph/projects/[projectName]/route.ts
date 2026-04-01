// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Graph Project Operations API Route
 * Proxy DELETE requests to FastAPI backend
 */

import { NextRequest, NextResponse } from 'next/server';
import { getFastAPIUrl } from '@/lib/api-config';

const FASTAPI_URL = getFastAPIUrl();

interface RouteParams {
  params: Promise<{
    projectName: string;
  }>;
}

export async function DELETE(request: NextRequest, { params }: RouteParams) {
  const { projectName } = await params;

  try {
    const response = await fetch(
      `${FASTAPI_URL}/api/graph/projects/${encodeURIComponent(projectName)}`,
      {
        method: 'DELETE',
        headers: {
          'Content-Type': 'application/json',
        },
      }
    );

    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error('Failed to clean project graph:', error);
    return NextResponse.json(
      { error: 'Failed to connect to graph service', success: false },
      { status: 503 }
    );
  }
}

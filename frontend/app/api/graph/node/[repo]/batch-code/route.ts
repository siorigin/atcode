// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { NextRequest, NextResponse } from 'next/server';
import { getFastAPIUrl } from '@/lib/api-config';

const BACKEND_URL = getFastAPIUrl();

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ repo: string }> }
) {
  try {
    const { repo } = await params;
    const body = await request.json();

    if (!body.qualified_names || !Array.isArray(body.qualified_names)) {
      return NextResponse.json(
        { error: 'qualified_names array is required' },
        { status: 400 }
      );
    }

    const backendUrl = `${BACKEND_URL}/api/graph/node/${encodeURIComponent(repo)}/batch-code`;

    const response = await fetch(backendUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const errorText = await response.text();
      console.error(`Backend batch-code error: ${response.status} - ${errorText}`);
      return NextResponse.json(
        { error: errorText || 'Backend request failed' },
        { status: response.status }
      );
    }

    const data = await response.json();
    return NextResponse.json(data);
  } catch (error) {
    console.error('Error proxying batch-code request:', error);
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    );
  }
}

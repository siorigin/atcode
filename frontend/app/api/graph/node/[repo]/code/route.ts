// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { NextRequest, NextResponse } from 'next/server';
import { getFastAPIUrl } from '@/lib/api-config';

const BACKEND_URL = getFastAPIUrl();

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ repo: string }> }
) {
  try {
    const { repo } = await params;
    const searchParams = request.nextUrl.searchParams;
    const qualifiedName = searchParams.get('qualified_name');

    if (!qualifiedName) {
      return NextResponse.json(
        { error: 'qualified_name parameter is required' },
        { status: 400 }
      );
    }

    const backendUrl = `${BACKEND_URL}/api/graph/node/${encodeURIComponent(repo)}/code?qualified_name=${encodeURIComponent(qualifiedName)}`;

    const response = await fetch(backendUrl, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    if (!response.ok) {
      const errorText = await response.text();
      console.error(`Backend error: ${response.status} - ${errorText}`);
      return NextResponse.json(
        { error: errorText || 'Backend request failed' },
        { status: response.status }
      );
    }

    const data = await response.json();
    return NextResponse.json(data);
  } catch (error) {
    console.error('Error proxying code request:', error);
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    );
  }
}

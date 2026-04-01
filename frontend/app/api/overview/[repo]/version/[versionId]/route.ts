// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Version Management API Route
 * Delete a specific documentation version
 */

import { NextRequest, NextResponse } from 'next/server';
import { getFastAPIUrl } from '@/lib/api-config';

const FASTAPI_URL = getFastAPIUrl();

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ repo: string; versionId: string }> }
) {
  const { repo, versionId } = await params;

  try {
    const response = await fetch(`${FASTAPI_URL}/api/overview/${repo}/version/${versionId}`, {
      method: 'DELETE',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error('Failed to delete version:', error);
    return NextResponse.json(
      { success: false, error: 'Failed to connect to overview service' },
      { status: 503 }
    );
  }
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ repo: string; versionId: string }> }
) {
  const { repo, versionId } = await params;

  try {
    const response = await fetch(`${FASTAPI_URL}/api/overview/${repo}/version/${versionId}`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error('Failed to get version:', error);
    return NextResponse.json(
      { error: 'Failed to connect to overview service' },
      { status: 503 }
    );
  }
}

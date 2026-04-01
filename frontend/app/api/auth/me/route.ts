// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Auth API Endpoint
 *
 * GET /api/auth/me - Get current user information
 * POST /api/auth/me - Create/refresh user session
 * DELETE /api/auth/me - Clear user session
 */

import { NextRequest, NextResponse } from 'next/server';
import { getOrCreateUser, clearUserSession } from '@/lib/auth';

/**
 * GET /api/auth/me
 * Returns the current user's information
 */
export async function GET(request: NextRequest) {
  try {
    const user = await getOrCreateUser(request);

    return NextResponse.json({
      success: true,
      user: {
        userId: user.userId,
        isAnonymous: user.isAnonymous,
        createdAt: user.createdAt,
      },
    });
  } catch (error) {
    console.error('Auth error:', error);
    return NextResponse.json(
      { success: false, error: 'Authentication failed' },
      { status: 500 }
    );
  }
}

/**
 * POST /api/auth/me
 * Creates or refreshes the user session
 * Can accept a frontend userId to correlate with backend
 */
export async function POST(request: NextRequest) {
  try {
    // Get request body if any
    let frontendUserId: string | undefined;
    try {
      const body = await request.json();
      frontendUserId = body.userId;
    } catch {
      // No body provided, that's fine
    }

    // If frontend provides userId, we pass it via header for auth to use
    // Note: We can't modify NextRequest directly, so we pass frontendUserId separately
    const user = await getOrCreateUser(request, frontendUserId);

    return NextResponse.json({
      success: true,
      user: {
        userId: user.userId,
        isAnonymous: user.isAnonymous,
        createdAt: user.createdAt,
      },
      message: 'Session created/refreshed',
    });
  } catch (error) {
    console.error('Auth error:', error);
    return NextResponse.json(
      { success: false, error: 'Failed to create session' },
      { status: 500 }
    );
  }
}

/**
 * DELETE /api/auth/me
 * Clears the user session (logout)
 */
export async function DELETE() {
  try {
    await clearUserSession();

    return NextResponse.json({
      success: true,
      message: 'Session cleared',
    });
  } catch (error) {
    console.error('Auth error:', error);
    return NextResponse.json(
      { success: false, error: 'Failed to clear session' },
      { status: 500 }
    );
  }
}

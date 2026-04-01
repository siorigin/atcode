// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { NextRequest } from 'next/server';

/**
 * API endpoint to generate a new session ID
 * GET /api/new-session
 * Returns: { sessionId: string }
 */
export async function GET(request: NextRequest) {
  try {
    // Generate a unique session ID with timestamp and random component
    const timestamp = Date.now();
    const randomPart = Math.random().toString(36).substring(2, 11);
    const sessionId = `session-${timestamp}-${randomPart}`;

    return new Response(
      JSON.stringify({ sessionId }),
      { 
        status: 200, 
        headers: { 'Content-Type': 'application/json' } 
      }
    );
  } catch (error) {
    console.error('Failed to generate session ID:', error);
    return new Response(
      JSON.stringify({ 
        error: 'Failed to generate session ID',
        details: error instanceof Error ? error.message : 'Unknown error'
      }),
      { status: 500, headers: { 'Content-Type': 'application/json' } }
    );
  }
}

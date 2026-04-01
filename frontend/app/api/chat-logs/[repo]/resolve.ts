// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { NextRequest } from 'next/server';
import path from 'path';
import { existsSync } from 'fs';
import { getWikiChatDir } from '@/lib/paths';
import crypto from 'crypto';

/**
 * Generate anonymous user ID based on request (matching backend logic)
 */
function generateAnonymousId(request: NextRequest): string {
  const forwarded = request.headers.get('x-forwarded-for');
  const realIp = request.headers.get('x-real-ip');
  const ip = forwarded ? forwarded.split(',')[0].trim() : (realIp || 'unknown');
  const userAgent = request.headers.get('user-agent') || 'unknown';
  const content = `${ip}:${userAgent}`;
  const hash = crypto.createHash('sha256').update(content).digest('hex').substring(0, 12);
  return `anon-${hash}`;
}

/**
 * Resolve the file path for a chat log, trying multiple filename formats.
 *
 * The backend saves as `{session_id}.json` but the frontend originally
 * expected `{user_id}__{session_id}.json`. This function tries both formats
 * and returns the first match, or null if neither exists.
 */
export function resolveChatLogPath(
  request: NextRequest,
  repo: string,
  logId: string
): string | null {
  const chatDir = getWikiChatDir(repo);

  // If logId already contains __, treat as full filename
  if (logId.includes('__')) {
    const p = path.join(chatDir, `${logId}.json`);
    return existsSync(p) ? p : null;
  }

  // Try candidates in order:
  // 1. {user_id}__{session_id}.json (legacy frontend format)
  // 2. {session_id}.json (backend format)
  const userId = generateAnonymousId(request);
  const candidates = [
    path.join(chatDir, `${userId}__${logId}.json`),
    path.join(chatDir, `${logId}.json`),
  ];

  for (const candidate of candidates) {
    if (existsSync(candidate)) {
      return candidate;
    }
  }

  return null;
}

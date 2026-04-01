// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Authentication Utilities
 *
 * Provides JWT-based authentication for the AtCode application.
 * Supports both anonymous users and future authenticated users.
 */

import { cookies } from 'next/headers';
import crypto from 'crypto';

// JWT implementation without external dependency
// Using HMAC-SHA256 for signing

const JWT_SECRET = process.env.JWT_SECRET || 'atcode-secret-key-change-in-production';
const TOKEN_NAME = 'atcode-auth-token';
const TOKEN_EXPIRY_SECONDS = 30 * 24 * 60 * 60; // 30 days

export interface UserPayload {
  userId: string;
  isAnonymous: boolean;
  createdAt: number;
  exp?: number; // Expiration timestamp
}

/**
 * Base64URL encode (JWT-safe)
 */
function base64UrlEncode(data: string): string {
  return Buffer.from(data)
    .toString('base64')
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=/g, '');
}

/**
 * Base64URL decode
 */
function base64UrlDecode(data: string): string {
  // Restore padding
  let padded = data.replace(/-/g, '+').replace(/_/g, '/');
  while (padded.length % 4) {
    padded += '=';
  }
  return Buffer.from(padded, 'base64').toString('utf-8');
}

/**
 * Create HMAC signature
 */
function createSignature(data: string): string {
  return crypto
    .createHmac('sha256', JWT_SECRET)
    .update(data)
    .digest('base64')
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=/g, '');
}

/**
 * Generates a JWT token for a user
 */
export function generateToken(payload: Omit<UserPayload, 'exp'>): string {
  const header = {
    alg: 'HS256',
    typ: 'JWT'
  };

  const fullPayload: UserPayload = {
    ...payload,
    exp: Math.floor(Date.now() / 1000) + TOKEN_EXPIRY_SECONDS
  };

  const headerEncoded = base64UrlEncode(JSON.stringify(header));
  const payloadEncoded = base64UrlEncode(JSON.stringify(fullPayload));
  const signature = createSignature(`${headerEncoded}.${payloadEncoded}`);

  return `${headerEncoded}.${payloadEncoded}.${signature}`;
}

/**
 * Verifies and decodes a JWT token
 */
export function verifyToken(token: string): UserPayload | null {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) {
      return null;
    }

    const [headerEncoded, payloadEncoded, signature] = parts;

    // Verify signature
    const expectedSignature = createSignature(`${headerEncoded}.${payloadEncoded}`);
    if (signature !== expectedSignature) {
      console.warn('JWT signature verification failed');
      return null;
    }

    // Decode payload
    const payload: UserPayload = JSON.parse(base64UrlDecode(payloadEncoded));

    // Check expiration
    if (payload.exp && payload.exp < Math.floor(Date.now() / 1000)) {
      console.warn('JWT token expired');
      return null;
    }

    return payload;
  } catch (error) {
    console.error('JWT verification error:', error);
    return null;
  }
}

/**
 * Generates anonymous user ID from request metadata
 * IMPORTANT: This must match the backend Python implementation exactly!
 * Backend: backend/api/middleware/auth.py - generate_anonymous_id()
 * Format: SHA256(ip:user_agent)[:12] → "anon-xxxxxxxxxxxx"
 */
function generateAnonymousUserId(request: Request): string {
  const forwarded = request.headers.get('x-forwarded-for');
  const realIp = request.headers.get('x-real-ip');
  const ip = forwarded ? forwarded.split(',')[0] : (realIp || 'unknown');
  const userAgent = request.headers.get('user-agent') || 'unknown';

  // Create a stable hash for anonymous ID
  // NOTE: Must use 12 characters to match backend Python implementation
  const hash = crypto
    .createHash('sha256')
    .update(`${ip}:${userAgent}`)
    .digest('hex')
    .substring(0, 12);  // Changed from 16 to 12 to match backend

  return `anon-${hash}`;
}

/**
 * Gets or creates user from request
 * This provides a unified way to identify users across API routes
 * @param request - The incoming request
 * @param frontendUserId - Optional userId provided by frontend (for POST /api/auth/me)
 */
export async function getOrCreateUser(request: Request, frontendUserId?: string): Promise<UserPayload> {
  // Try to get existing token from cookie
  const cookieStore = await cookies();
  const existingToken = cookieStore.get(TOKEN_NAME)?.value;

  if (existingToken) {
    const payload = verifyToken(existingToken);
    if (payload) {
      return payload;
    }
  }

  // Try to get user ID from: 1) parameter, 2) header, 3) generate
  const headerUserId = request.headers.get('x-user-id');
  const userId = frontendUserId || headerUserId || generateAnonymousUserId(request);

  const payload: UserPayload = {
    userId,
    isAnonymous: true,
    createdAt: Date.now(),
  };

  // Set cookie for future requests
  const token = generateToken(payload);
  cookieStore.set(TOKEN_NAME, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    maxAge: TOKEN_EXPIRY_SECONDS,
    path: '/',
  });

  console.log('Created new user:', userId);

  return payload;
}

/**
 * Gets user from request without creating new user
 * Returns null if no valid user session exists
 */
export async function getUser(request: Request): Promise<UserPayload | null> {
  const cookieStore = await cookies();
  const existingToken = cookieStore.get(TOKEN_NAME)?.value;

  if (existingToken) {
    return verifyToken(existingToken);
  }

  return null;
}

/**
 * Clears the user session
 */
export async function clearUserSession(): Promise<void> {
  const cookieStore = await cookies();
  cookieStore.delete(TOKEN_NAME);
}

/**
 * API route middleware to require authentication
 */
export function withAuth(
  handler: (request: Request, user: UserPayload) => Promise<Response>
) {
  return async (request: Request): Promise<Response> => {
    const user = await getOrCreateUser(request);
    return handler(request, user);
  };
}

/**
 * Extracts user ID from request for backward compatibility
 * Priority: JWT token > X-User-ID header > anonymous ID
 */
export async function getUserIdFromRequest(request: Request): Promise<string> {
  // Try JWT token first
  const cookieStore = await cookies();
  const existingToken = cookieStore.get(TOKEN_NAME)?.value;

  if (existingToken) {
    const payload = verifyToken(existingToken);
    if (payload) {
      return payload.userId;
    }
  }

  // Try X-User-ID header (from frontend)
  const headerUserId = request.headers.get('x-user-id');
  if (headerUserId) {
    return headerUserId;
  }

  // Generate anonymous ID
  return generateAnonymousUserId(request);
}

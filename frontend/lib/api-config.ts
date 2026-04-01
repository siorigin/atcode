// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Unified API Configuration
 *
 * Centralized configuration for API endpoints.
 * Port is driven by API_PORT in .env (injected via next.config.ts at build time).
 */

/**
 * Get the FastAPI backend URL
 * - Client-side (browser): Uses NEXT_PUBLIC_API_URL (e.g., "/backapi" via Nginx)
 * - Server-side (Next.js API routes): Uses SERVER_API_URL or falls back to localhost:API_PORT
 */
export function getFastAPIUrl(): string {
  // Server-side: use absolute URL to connect directly to FastAPI
  if (typeof window === 'undefined') {
    // Priority: SERVER_API_URL > http://localhost:API_PORT
    const serverUrl = process.env.SERVER_API_URL;
    if (serverUrl) {
      return serverUrl;
    }
    // API_PORT is injected by next.config.ts from .env
    const port = process.env.API_PORT;
    return `http://localhost:${port}`;
  }

  // Client-side: use NEXT_PUBLIC_API_URL (can be relative path like /backapi)
  // If not set, dynamically use the browser's current hostname so that
  // both http://localhost:3008 and http://10.96.11.7:3008 work automatically.
  if (process.env.NEXT_PUBLIC_API_URL) {
    return process.env.NEXT_PUBLIC_API_URL;
  }
  // NEXT_PUBLIC_API_PORT is injected by next.config.ts from .env's API_PORT
  const port = process.env.NEXT_PUBLIC_API_PORT;
  if (!port) {
    console.warn('getFastAPIUrl: NEXT_PUBLIC_API_PORT not set, API calls may fail');
    return `http://${window.location.hostname}:8008`;
  }
  return `http://${window.location.hostname}:${port}`;
}

/**
 * Get the browser-facing MCP endpoint for CLI install commands.
 *
 * By default this uses direct backend host:port instead of reverse-proxy paths,
 * because Claude/Codex CLI often connects outside the browser context.
 * Set NEXT_PUBLIC_MCP_URL to override when needed.
 */
export function getMcpEndpoint(): string {
  const explicitMcpUrl = process.env.NEXT_PUBLIC_MCP_URL?.trim();
  if (explicitMcpUrl) {
    if (typeof window === 'undefined') {
      return explicitMcpUrl.replace(/\/$/, '');
    }
    try {
      return new URL(explicitMcpUrl, window.location.origin).toString().replace(/\/$/, '');
    } catch {
      return explicitMcpUrl.replace(/\/$/, '');
    }
  }

  if (typeof window === 'undefined') {
    const port = process.env.API_PORT || process.env.NEXT_PUBLIC_API_PORT || '8008';
    return `http://localhost:${port}/mcp`;
  }

  const port = process.env.NEXT_PUBLIC_API_PORT || process.env.API_PORT || '8008';
  return `${window.location.protocol}//${window.location.hostname}:${port}/mcp`;
}

/**
 * Get the API host (for backward compatibility)
 */
export function getAPIHost(): string {
  const url = getFastAPIUrl();
  try {
    return new URL(url).hostname;
  } catch {
    return 'localhost';
  }
}

/**
 * Get the API port (for backward compatibility)
 */
export function getAPIPort(): number {
  const url = getFastAPIUrl();
  try {
    return parseInt(new URL(url).port, 10);
  } catch {
    return parseInt(process.env.NEXT_PUBLIC_API_PORT || process.env.API_PORT || '0', 10);
  }
}

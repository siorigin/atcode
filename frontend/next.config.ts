// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import type { NextConfig } from "next";
import { readFileSync, existsSync } from "fs";
import { resolve } from "path";

// Load .env from project root directory (works reliably with Turbopack)
function loadEnvFile(): Record<string, string> {
  const envPath = resolve(__dirname, "../.env");
  if (!existsSync(envPath)) return {};

  const content = readFileSync(envPath, "utf-8");
  const env: Record<string, string> = {};

  for (const line of content.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const match = trimmed.match(/^([^=]+)=(.*)$/);
    if (match) {
      let value = match[2].trim();
      // Remove inline comments (but not inside quotes)
      if (!value.startsWith('"') && !value.startsWith("'")) {
        value = value.split(/\s+#/)[0].trim();
      }
      // Remove surrounding quotes
      value = value.replace(/^["']|["']$/g, "");
      env[match[1].trim()] = value;
    }
  }
  return env;
}

const envFile = loadEnvFile();

// Make LLM config available to server-side API routes (NOT exposed to client)
if (envFile.LLM_BASE_URL) process.env.LLM_BASE_URL = envFile.LLM_BASE_URL;
if (envFile.LLM_API_KEY) process.env.LLM_API_KEY = envFile.LLM_API_KEY;

// Get API configuration from environment (env file > process.env > defaults)
// Priority: NEXT_PUBLIC_API_URL (explicit) > auto-derive from API_PORT
// NOTE: 8008 is only the last-resort default if .env is missing entirely
const API_PORT = envFile.API_PORT || process.env.API_PORT || '8008';
// Persist into process.env so server-side runtime code (standalone mode) can read it
process.env.API_PORT = API_PORT;
// Only use NEXT_PUBLIC_API_URL if explicitly set — otherwise let client-side
// code dynamically use window.location.hostname (see api-config.ts)
const API_URL = envFile.NEXT_PUBLIC_API_URL || process.env.NEXT_PUBLIC_API_URL || '';
const MCP_URL = envFile.NEXT_PUBLIC_MCP_URL || process.env.NEXT_PUBLIC_MCP_URL || '';
const PORT = parseInt(envFile.PORT || process.env.PORT || '3006', 10);

const nextConfig: NextConfig = {
  output: 'standalone',  // 启用 standalone 模式以支持 cluster

  // 修复 standalone 输出路径嵌套问题
  outputFileTracingRoot: resolve(__dirname, '../'),

  // 排除大型数据目录，避免 standalone 复制几万个文件
  // API routes 在运行时直接读原始路径，不需要复制进 standalone
  outputFileTracingExcludes: {
    '/*': [
      '../data/wiki_repos/**',
      '../data/wiki_chat/**',
      '../data/wiki_doc/**',
      '../data/wiki_embedding/**',
      '../data/wiki_papers/**',
      '../data/wiki_overview/**',
      '../data/redis/**',
      '../data/logs/**',
    ],
  },


  env: {
    // Make API URL and PORT available to both server and client
    NEXT_PUBLIC_API_URL: API_URL,
    NEXT_PUBLIC_MCP_URL: MCP_URL,
    NEXT_PUBLIC_API_PORT: API_PORT,
    // Also inject API_PORT for server-side SSR routes (api-config.ts reads this)
    API_PORT: API_PORT,
  },

  // Performance optimizations
  modularizeImports: {
    'mermaid': {
      transform: 'mermaid/dist/mermaid.esm.mjs',
    },
    '@dagrejs/dagre': {
      transform: '@dagrejs/dagre/dist/dagre.mjs',
    },
  },

  // Production optimizations
  productionBrowserSourceMaps: false,

  // Experimental features for better performance
  experimental: {
    optimizePackageImports: [
      'mermaid',
      'katex',
      '@xyflow/react',
      'react-syntax-highlighter',
    ],
  },

  // Turbopack configuration for Next.js 16
  turbopack: {},

  // WebSocket proxy for dev mode only.
  // In dev mode (`npm run dev`), the Next.js dev server handles WebSocket
  // upgrade requests through rewrites. The backend is always on localhost in
  // this mode so the hardcoded destination is fine.
  //
  // In production standalone mode, start.js wraps server.js and intercepts
  // upgrade events directly, using SERVER_API_URL at runtime — rewrites are
  // skipped so there is no conflict.
  async rewrites() {
    if (process.env.NODE_ENV === 'production') {
      return [];
    }
    return {
      beforeFiles: [
        {
          source: '/ws-proxy/:path*',
          destination: `http://localhost:${API_PORT}/:path*`,
        },
      ],
      afterFiles: [],
      fallback: [],
    };
  },
};

export default nextConfig;

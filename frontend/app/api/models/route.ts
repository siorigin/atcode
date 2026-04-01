// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { NextResponse } from 'next/server';
import { getFastAPIUrl } from '@/lib/api-config';

export const dynamic = 'force-dynamic';

const CACHE_HEADERS = {
  'Cache-Control': 'max-age=300, stale-while-revalidate=600',
};
const MODEL_FETCH_TIMEOUT_MS = 2500;

interface ModelInfo {
  id: string;
  owned_by?: string | null;
}

async function fetchWithTimeout(url: string, init?: RequestInit, timeoutMs = MODEL_FETCH_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

function fallbackPayload(configuredModel: string) {
  return {
    data: configuredModel ? [{ id: configuredModel }] : [] as ModelInfo[],
    current_model: configuredModel,
  };
}

async function fetchBackendModels(configuredModel: string) {
  const backendUrl = getFastAPIUrl();
  const res = await fetchWithTimeout(`${backendUrl}/api/config/models`, {
    headers: { 'Content-Type': 'application/json' },
  });

  if (!res.ok) {
    throw new Error(`Backend model listing failed with status ${res.status}`);
  }

  const data = await res.json();
  return {
    data: Array.isArray(data.models) ? data.models : [],
    current_model: data.current_model || configuredModel,
  };
}

export async function GET() {
  const configuredModel = process.env.LLM_MODEL || '';

  try {
    const payload = await fetchBackendModels(configuredModel);
    return NextResponse.json(payload, { headers: CACHE_HEADERS });
  } catch (backendError) {
    console.error('Failed to fetch models from backend config endpoint:', backendError);
    return NextResponse.json(
      fallbackPayload(configuredModel),
      { headers: CACHE_HEADERS }
    );
  }
}

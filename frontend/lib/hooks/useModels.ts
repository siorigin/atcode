// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect } from 'react';
import { MODEL_TIERS, KNOWN_MODEL_MAP, VENDOR_PATTERNS, type ModelPreset } from '../model-config';

export type TierMap = Record<string, readonly ModelPreset[]>;
interface UseModelsOptions {
  enabled?: boolean;
}

interface ModelsState {
  tiers: TierMap;
  defaultModel: string;
}

interface ModelsApiResponse {
  data?: Array<{ id: string }>;
  current_model?: string;
}

/**
 * Exclude patterns for non-text models that shouldn't appear in chat.
 * Only filters out models that are clearly not text-chat models (images, audio, embeddings, etc.)
 * All other models from the API are shown — users should be able to pick any text model.
 */
const EXCLUDE_PATTERNS: RegExp[] = [
  // --- Non-text modalities ---
  /image|img|t2[iv]|s2v|i2i|seedream|seededit|seedance|hailuo|kling|video/i,
  /audio|whisper|tts|transcribe/i,
  /\bvl\b|vision|ocr|captioner/i,
  /embedding|rerank|moderation/i,
  /clone|upload|retrieve|gizmo/i,
];

function detectVendor(modelId: string): string | null {
  for (const [pattern, vendor] of VENDOR_PATTERNS) {
    if (pattern.test(modelId)) return vendor;
  }
  return null;
}

function shouldIncludeUnknownModel(id: string): boolean {
  return !EXCLUDE_PATTERNS.some(p => p.test(id));
}

// Module-level cache so all components share one fetch
let cachedState: ModelsState | null = null;
let fetchPromise: Promise<ModelsState> | null = null;
let lastFetchTime = 0;
const CACHE_TTL_MS = 60 * 60 * 1000; // 1 hour

async function fetchModels(): Promise<ModelsState> {
  const res = await fetch('/api/models');
  if (!res.ok) throw new Error('Failed to fetch models');
  const json = await res.json() as ModelsApiResponse;
  const models: { id: string }[] = json.data || [];
  const configuredModel = typeof json.current_model === 'string' ? json.current_model : '';

  const tiers: Record<string, ModelPreset[]> = {};
  let firstAvailableModel = '';

  const addModel = (vendor: string, preset: ModelPreset) => {
    if (!tiers[vendor]) tiers[vendor] = [];
    tiers[vendor].push(preset);
    if (!firstAvailableModel) {
      firstAvailableModel = preset.model;
    }
  };

  for (const m of models) {
    // Known curated model — always include with nice label
    const known = KNOWN_MODEL_MAP[m.id];
    if (known) {
      addModel(known.vendor, { label: known.label, model: m.id });
      continue;
    }

    // ssvip variant — include if base model is curated
    if (/-ssvip$/i.test(m.id)) {
      const baseId = m.id.replace(/-ssvip$/i, '');
      const baseKnown = KNOWN_MODEL_MAP[baseId];
      if (baseKnown) {
        addModel(baseKnown.vendor, { label: `${baseKnown.label} (ssvip)`, model: m.id });
      }
      continue;
    }

    // Unknown model — filter out non-text models, classify by vendor or put in "Other"
    if (!shouldIncludeUnknownModel(m.id)) continue;

    const vendor = detectVendor(m.id) || 'Other';
    addModel(vendor, { label: m.id, model: m.id });
  }

  return {
    tiers: Object.keys(tiers).length > 0 ? tiers : { ...MODEL_TIERS },
    defaultModel: configuredModel || firstAvailableModel,
  };
}

function isCacheStale(): boolean {
  return !cachedState || Date.now() - lastFetchTime > CACHE_TTL_MS;
}

function doFetch(): Promise<ModelsState> {
  if (!fetchPromise) {
    fetchPromise = fetchModels().then(result => {
      cachedState = result;
      lastFetchTime = Date.now();
      fetchPromise = null;
      return result;
    }).catch(err => {
      fetchPromise = null;
      throw err;
    });
  }
  return fetchPromise;
}

export function useModels(options: UseModelsOptions = {}) {
  const { enabled = true } = options;
  const [tiers, setTiers] = useState<TierMap>(() => cachedState?.tiers ?? { ...MODEL_TIERS });
  const [defaultModel, setDefaultModel] = useState<string>(() => cachedState?.defaultModel ?? '');
  const [isLoading, setIsLoading] = useState(enabled && !cachedState);

  useEffect(() => {
    if (!enabled) return;

    // Initial load or stale cache
    if (isCacheStale()) {
      doFetch()
        .then(result => {
          setTiers(result.tiers);
          setDefaultModel(result.defaultModel);
        })
        .catch(() => {})
        .finally(() => setIsLoading(false));
    } else {
      queueMicrotask(() => {
        setTiers(cachedState!.tiers);
        setDefaultModel(cachedState!.defaultModel);
        setIsLoading(false);
      });
    }

    // Periodic refresh every hour
    const interval = setInterval(() => {
      cachedState = null; // invalidate
      doFetch()
        .then(result => {
          setTiers(result.tiers);
          setDefaultModel(result.defaultModel);
        })
        .catch(() => {});
    }, CACHE_TTL_MS);

    return () => clearInterval(interval);
  }, [enabled]);

  return { tiers, defaultModel, isLoading: enabled ? isLoading : false };
}

export function buildModelSelectorTiers(tiers: TierMap, defaultModel: string): TierMap {
  return {
    Default: [
      {
        label: defaultModel ? `Default (${defaultModel})` : 'Default',
        model: '',
      },
    ],
    ...tiers,
  };
}

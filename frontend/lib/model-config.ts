// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Unified Model Configuration
 *
 * This file serves as the single source of truth for all AI model options
 * across the frontend. Update models here to propagate changes everywhere.
 */

export interface ModelPreset {
  label: string;
  model: string;
}

/**
 * Curated latest AI models organized by vendor.
 * These always show (with nice labels) if available on DMXAPI.
 * New DMXAPI models not listed here can still auto-appear if they pass filtering.
 */
export const MODEL_TIERS = {
  'OpenAI': [
    { label: 'GPT-5.4', model: 'gpt-5.4' },
    { label: 'GPT-5.3 Codex', model: 'gpt-5.3-codex' },
    { label: 'GPT-5.2', model: 'gpt-5.2' },
    { label: 'GPT-5.2 Pro', model: 'gpt-5.2-pro' },
    { label: 'GPT-5.1', model: 'gpt-5.1' },
    { label: 'GPT-5', model: 'gpt-5' },
    { label: 'GPT-5 Pro', model: 'gpt-5-pro' },
    { label: 'GPT-5 Mini', model: 'gpt-5-mini' },
    { label: 'GPT-5 Nano', model: 'gpt-5-nano' },
    { label: 'o4 Mini', model: 'o4-mini' },
    { label: 'o3', model: 'o3' },
    { label: 'o3 Pro', model: 'o3-pro' },
  ],
  'Claude': [
    { label: 'Opus 4.6', model: 'claude-opus-4-6-ssvip' },
    { label: 'Sonnet 4.6', model: 'claude-sonnet-4-6-ssvip' },
    { label: 'Opus 4.5', model: 'claude-opus-4-5-20251101' },
    { label: 'Sonnet 4.5', model: 'claude-sonnet-4-5-20250929' },
    { label: 'Haiku 4.5', model: 'claude-haiku-4-5-ssvip' },
  ],
  'Google': [
    { label: 'Gemini 3.1 Pro', model: 'gemini-3.1-pro-preview' },
    { label: 'Gemini 3.1 Flash', model: 'gemini-3.1-flash-image-preview' },
    { label: 'Gemini 3.1 Flash Lite', model: 'gemini-3.1-flash-lite-preview' },
    { label: 'Gemini 3 Pro', model: 'gemini-3-pro-preview' },
    { label: 'Gemini 3 Flash', model: 'gemini-3-flash-preview' },
    { label: 'Gemini 2.5 Pro', model: 'gemini-2.5-pro' },
    { label: 'Gemini 2.5 Flash', model: 'gemini-2.5-flash' },
  ],
  'DeepSeek': [
    { label: 'DeepSeek V3.2', model: 'DeepSeek-V3.2' },
    { label: 'DeepSeek R1', model: 'DeepSeek-R1' },
  ],
  'MiniMax': [
    { label: 'M2.5', model: 'MiniMax-M2.5' },
    { label: 'M2.1', model: 'MiniMax-M2.1' },
    { label: 'M1', model: 'MiniMax-M1' },
  ],
  '智谱AI': [
    { label: 'GLM-5', model: 'glm-5' },
    { label: 'GLM-4.7', model: 'glm-4.7' },
    { label: 'GLM-4.7 Flash', model: 'GLM-4.7-Flash' },
    { label: 'GLM-4.6', model: 'glm-4.6' },
    { label: 'GLM-4.5 Flash', model: 'GLM-4.5-Flash' },
    { label: 'GLM-Z1 Flash', model: 'GLM-Z1-Flash' },
  ],
  'Kimi': [
    { label: 'K2.5', model: 'kimi-k2.5' },
    { label: 'K2', model: 'Kimi-K2' },
  ],
  '豆包': [
    { label: 'Seed 2.0 Pro', model: 'doubao-seed-2-0-pro-260215' },
    { label: 'Seed 1.8', model: 'doubao-seed-1-8-251228' },
    { label: 'Seed 1.6', model: 'doubao-seed-1-6-251015' },
  ],
  '阿里云': [
    { label: 'Qwen 3.5 Plus', model: 'qwen3.5-plus' },
    { label: 'Qwen 3 Max', model: 'qwen3-max' },
    { label: 'Qwen Max', model: 'qwen-max-latest' },
  ],
} as const;

/**
 * Vendor detection patterns for classifying dynamically fetched models.
 * Order matters — first match wins.
 */
export const VENDOR_PATTERNS: [RegExp, string][] = [
  [/^gpt-|^o[1-9]/i, 'OpenAI'],
  [/^claude-/i, 'Claude'],
  [/^gemini-/i, 'Google'],
  [/^deepseek/i, 'DeepSeek'],
  [/^minimax/i, 'MiniMax'],
  [/^glm-|^GLM-|^codegeex|^chatglm/i, '智谱AI'],
  [/^kimi|^moonshot/i, 'Kimi'],
  [/^doubao|^skylark/i, '豆包'],
  [/^qwen/i, '阿里云'],
];

/**
 * Lookup map from model ID to label + vendor, derived from MODEL_TIERS.
 * Used by useModels hook to classify dynamically fetched models.
 */
export const KNOWN_MODEL_MAP: Record<string, { label: string; vendor: string }> = {};
for (const [vendor, models] of Object.entries(MODEL_TIERS)) {
  for (const m of models) {
    KNOWN_MODEL_MAP[m.model] = { label: m.label, vendor };
  }
}

/**
 * All available models as a flat array (for chat)
 */
export const ALL_CHAT_MODELS: readonly ModelPreset[] =
  Object.values(MODEL_TIERS).flat();

/**
 * Available models for documentation/research/overview generation
 */
export const DOC_GENERATION_MODELS: readonly ModelPreset[] = [
  { label: 'Default', model: '' },
  ...ALL_CHAT_MODELS,
];

/**
 * Default model for chat
 */
export const DEFAULT_CHAT_MODEL = '';

/**
 * Default model for documentation generation
 */
export const DEFAULT_DOC_MODEL = '';

/**
 * Helper to get model label by model ID
 */
export function getModelLabel(modelId: string): string {
  const known = KNOWN_MODEL_MAP[modelId];
  if (known) return known.label;
  const model = ALL_CHAT_MODELS.find(m => m.model === modelId);
  return model?.label || modelId;
}

/**
 * Helper to get models by vendor
 */
export function getModelsByTier(tier: keyof typeof MODEL_TIERS): readonly ModelPreset[] {
  return MODEL_TIERS[tier];
}

/**
 * Check if a model ID is a preset model
 */
export function isPresetModel(modelId: string): boolean {
  return ALL_CHAT_MODELS.some(m => m.model === modelId);
}

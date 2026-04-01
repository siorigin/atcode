// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Custom hook for managing user's custom model history
 *
 * Persists user-entered custom model names to localStorage
 * for quick access in future sessions.
 */

import { useState, useEffect, useCallback } from 'react';
import { ALL_CHAT_MODELS } from '../model-config';

const STORAGE_KEY = 'atcode-custom-models';
const MAX_HISTORY = 10;

export function useModelHistory() {
  const [customModels, setCustomModels] = useState<string[]>([]);

  // Load from localStorage on mount
  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        const parsed = JSON.parse(stored);
        if (Array.isArray(parsed)) {
          setCustomModels(parsed);
        }
      }
    } catch {
      // Ignore localStorage errors
    }
  }, []);

  // Add a custom model to history
  const addCustomModel = useCallback((model: string) => {
    if (!model.trim()) return;

    // Don't save if it's a preset model
    if (ALL_CHAT_MODELS.some(p => p.model === model)) return;

    setCustomModels(prev => {
      // Remove duplicates and add to front
      const filtered = prev.filter(m => m !== model);
      const updated = [model, ...filtered].slice(0, MAX_HISTORY);

      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
      } catch {
        // Ignore localStorage errors
      }

      return updated;
    });
  }, []);

  // Remove a custom model from history
  const removeCustomModel = useCallback((model: string) => {
    setCustomModels(prev => {
      const updated = prev.filter(m => m !== model);
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
      } catch {
        // Ignore localStorage errors
      }
      return updated;
    });
  }, []);

  // Clear all custom models
  const clearCustomModels = useCallback(() => {
    setCustomModels([]);
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // Ignore localStorage errors
    }
  }, []);

  return {
    customModels,
    addCustomModel,
    removeCustomModel,
    clearCustomModels,
  };
}

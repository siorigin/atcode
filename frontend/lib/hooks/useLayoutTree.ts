'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useEffect, useRef } from 'react';
import type { LayoutNode, PanelId, DropPosition } from '@/lib/layout-tree';
import {
  addPanel,
  removePanel,
  movePanel as movePanelFn,
  updateSizes as updateSizesFn,
  containsPanel,
  getActivePanels,
  validateLayout,
  buildDefaultLayout,
} from '@/lib/layout-tree';

export type { LayoutNode, PanelId, DropPosition };

export interface LayoutTreeResult {
  layout: LayoutNode | null;
  activePanels: PanelId[];
  togglePanel: (id: PanelId) => void;
  ensurePanelActive: (id: PanelId) => void;
  isPanelActive: (id: PanelId) => boolean;
  movePanel: (panelId: PanelId, targetId: PanelId, pos: DropPosition) => void;
  updateSizes: (path: number[], sizes: number[]) => void;
}

function loadLayout(storageKey: string): LayoutNode | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = localStorage.getItem(storageKey);
    if (raw) return JSON.parse(raw);
  } catch {}
  return null;
}

function saveLayout(storageKey: string, layout: LayoutNode | null) {
  if (typeof window === 'undefined') return;
  try {
    if (layout) {
      localStorage.setItem(storageKey, JSON.stringify(layout));
    } else {
      localStorage.removeItem(storageKey);
    }
  } catch {}
}

export function useLayoutTree(
  storageKey: string,
  availablePanels: PanelId[],
  defaultPanels: PanelId[]
): LayoutTreeResult {
  const [layout, setLayout] = useState<LayoutNode | null>(() => {
    try {
      const saved = loadLayout(storageKey);
      if (saved && saved.type) {
        const validated = validateLayout(saved, availablePanels);
        if (validated && getActivePanels(validated).length > 0) return validated;
      }
    } catch {}
    return buildDefaultLayout(defaultPanels);
  });

  const storageKeyRef = useRef(storageKey);
  storageKeyRef.current = storageKey;

  // Persist on change
  useEffect(() => {
    saveLayout(storageKeyRef.current, layout);
  }, [layout]);

  // Re-initialize when storageKey changes
  const prevKeyRef = useRef(storageKey);
  useEffect(() => {
    if (storageKey !== prevKeyRef.current) {
      prevKeyRef.current = storageKey;
      const saved = loadLayout(storageKey);
      if (saved) {
        const validated = validateLayout(saved, availablePanels);
        if (validated && getActivePanels(validated).length > 0) {
          setLayout(validated);
          return;
        }
      }
      setLayout(buildDefaultLayout(defaultPanels));
    }
  }, [storageKey, availablePanels, defaultPanels]);

  // Auto-add newly available panels to the layout
  const prevAvailableRef = useRef<PanelId[]>(availablePanels);
  useEffect(() => {
    const prev = new Set(prevAvailableRef.current);
    const newPanels = availablePanels.filter(p => !prev.has(p));
    prevAvailableRef.current = availablePanels;
    if (newPanels.length > 0) {
      setLayout(current => {
        let updated = current;
        for (const p of newPanels) {
          if (!containsPanel(updated, p)) {
            updated = addPanel(updated, p);
          }
        }
        return updated;
      });
    }
  }, [availablePanels]);

  const togglePanel = useCallback((id: PanelId) => {
    setLayout(prev => {
      if (containsPanel(prev, id)) {
        // Don't remove the last panel
        if (prev && getActivePanels(prev).length <= 1) return prev;
        const result = prev ? removePanel(prev, id) : null;
        return result;
      } else {
        return addPanel(prev, id);
      }
    });
  }, []);

  // Like togglePanel but only adds — never removes. Safe to call repeatedly.
  const ensurePanelActive = useCallback((id: PanelId) => {
    setLayout(prev => {
      if (containsPanel(prev, id)) return prev; // already active, no-op
      return addPanel(prev, id);
    });
  }, []);

  const isPanelActive = useCallback((id: PanelId) => {
    return containsPanel(layout, id);
  }, [layout]);

  const movePanel = useCallback((panelId: PanelId, targetId: PanelId, pos: DropPosition) => {
    setLayout(prev => {
      if (!prev) return prev;
      return movePanelFn(prev, panelId, targetId, pos);
    });
  }, []);

  const updateSizes = useCallback((path: number[], sizes: number[]) => {
    setLayout(prev => {
      if (!prev) return prev;
      return updateSizesFn(prev, path, sizes);
    });
  }, []);

  const activePanels = layout ? getActivePanels(layout) : [];

  return {
    layout,
    activePanels,
    togglePanel,
    ensurePanelActive,
    isPanelActive,
    movePanel,
    updateSizes,
  };
}

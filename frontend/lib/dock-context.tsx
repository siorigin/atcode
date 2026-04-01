'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { createContext, useContext, useState, useCallback } from 'react';

export type DockPanel = 'chat' | 'repoViewer';

interface DockContextType {
  dockedPanels: Set<DockPanel>;
  isDocked: (panel: DockPanel) => boolean;
  dock: (panel: DockPanel) => void;
  undock: (panel: DockPanel) => void;
  toggleDock: (panel: DockPanel) => void;
}

const DockContext = createContext<DockContextType | null>(null);

export function DockProvider({ children }: { children: React.ReactNode }) {
  const [dockedPanels, setDockedPanels] = useState<Set<DockPanel>>(new Set());

  const isDocked = useCallback((panel: DockPanel) => dockedPanels.has(panel), [dockedPanels]);

  const dock = useCallback((panel: DockPanel) => {
    setDockedPanels(prev => {
      const next = new Set(prev);
      next.add(panel);
      return next;
    });
  }, []);

  const undock = useCallback((panel: DockPanel) => {
    setDockedPanels(prev => {
      const next = new Set(prev);
      next.delete(panel);
      return next;
    });
  }, []);

  const toggleDock = useCallback((panel: DockPanel) => {
    setDockedPanels(prev => {
      const next = new Set(prev);
      if (next.has(panel)) next.delete(panel);
      else next.add(panel);
      return next;
    });
  }, []);

  return (
    <DockContext.Provider value={{ dockedPanels, isDocked, dock, undock, toggleDock }}>
      {children}
    </DockContext.Provider>
  );
}

export function useDock() {
  const ctx = useContext(DockContext);
  if (!ctx) throw new Error('useDock must be used within DockProvider');
  return ctx;
}

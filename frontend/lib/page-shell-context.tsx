'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { createContext, useContext, useState, useMemo } from 'react';
import { usePathname } from 'next/navigation';

export type PageShellTab = 'overview' | 'research' | 'operator';

interface PageShellContextValue {
  repoName: string;
  activeTab: PageShellTab;
  sidebarContent: React.ReactNode | null;
  setSidebarContent: (content: React.ReactNode | null) => void;
  sidebarCollapsed: boolean;
  setSidebarCollapsed: (collapsed: boolean) => void;
}

const PageShellContext = createContext<PageShellContextValue | null>(null);

export function PageShellProvider({ repoName, children }: { repoName: string; children: React.ReactNode }) {
  const pathname = usePathname();
  const [sidebarContent, setSidebarContent] = useState<React.ReactNode | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  const activeTab = useMemo<PageShellTab>(() => {
    const repoBase = `/repos/${encodeURIComponent(repoName)}`;
    if (pathname === repoBase || pathname === repoBase + '/') return 'overview';
    if (pathname.startsWith(repoBase + '/research')) return 'research';
    return 'operator';
  }, [pathname, repoName]);

  const value = useMemo<PageShellContextValue>(() => ({
    repoName,
    activeTab,
    sidebarContent,
    setSidebarContent,
    sidebarCollapsed,
    setSidebarCollapsed,
  }), [repoName, activeTab, sidebarContent, sidebarCollapsed]);

  return (
    <PageShellContext.Provider value={value}>
      {children}
    </PageShellContext.Provider>
  );
}

export function usePageShell(): PageShellContextValue {
  const ctx = useContext(PageShellContext);
  if (!ctx) throw new Error('usePageShell must be used within PageShellProvider');
  return ctx;
}

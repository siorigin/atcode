'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect, useMemo } from 'react';
import { usePathname, useParams } from 'next/navigation';
import { FloatingChatWidget } from './FloatingChatWidget';
import { useTheme } from '@/lib/theme-context';
import { useThemeColors } from '@/lib/theme-colors';
import { useDock } from '@/lib/dock-context';

/**
 * Derive repo name from current route.
 * /repos/[repo]/* → repo name
 * Anything else → undefined (global mode)
 */
function deriveRepoName(pathname: string, params: Record<string, string | string[] | undefined>): string | undefined {
  if (pathname.startsWith('/repos/') && params?.repo) {
    return typeof params.repo === 'string' ? params.repo : params.repo[0];
  }
  return undefined;
}

/**
 * Derive lightweight page context metadata (no fullContent).
 */
function derivePageContext(
  pathname: string,
  params: Record<string, string | string[] | undefined>
): { pageType: string; operatorName?: string; documentTitle?: string; filePath?: string } | undefined {
  const repo = typeof params?.repo === 'string' ? params.repo : params?.repo?.[0];
  const operator = typeof params?.operator === 'string' ? params.operator : params?.operator?.[0];

  if (pathname === '/') {
    return { pageType: 'home' };
  }

  if (pathname.startsWith('/repos/') && repo) {
    if (operator) {
      const decodedOperator = decodeURIComponent(operator);
      return {
        pageType: 'operator',
        operatorName: decodedOperator,
        documentTitle: `${repo} / ${decodedOperator}`,
        filePath: `/repos/${repo}/${operator}`,
      };
    }
    return { pageType: 'repo', documentTitle: repo };
  }

  return { pageType: 'other' };
}

/**
 * Global chat provider — renders FloatingChatWidget on every page except /chat/*.
 * Mounted once in the root layout.
 */
export function GlobalChatProvider() {
  const [isOpen, setIsOpen] = useState(false);
  const [sectionContext, setSectionContext] = useState<{
    sectionName: string;
    sectionPath: string;
    versionId?: string;
  } | null>(null);
  const pathname = usePathname();
  const params = useParams() as Record<string, string | string[] | undefined>;
  const { theme } = useTheme();
  const colors = useThemeColors(theme);

  const { isDocked, dock, undock } = useDock();
  const chatDocked = isDocked('chat');

  // Auto-open chat when docked
  useEffect(() => {
    if (chatDocked && !isOpen) setIsOpen(true);
  }, [chatDocked, isOpen]);

  // Open chat docked by default
  const handleOpenChat = () => {
    dock('chat');
    setIsOpen(true);
  };

  // Close chat — undock so overview returns to fullscreen.
  // Chat session and streaming remain in store and continue in background.
  const handleCloseChat = () => {
    setIsOpen(false);
    if (chatDocked) undock('chat');
  };

  const rawRepoName = useMemo(() => deriveRepoName(pathname, params), [pathname, params]);
  // On the papers page, use __papers__ so the backend activates paper tools
  const isPapersPage = pathname.startsWith('/repos/papers');
  const repoName = isPapersPage ? '__papers__' : rawRepoName;
  const pageContext = useMemo(() => derivePageContext(pathname, params), [pathname, params]);

  // Listen for overview section context from repo page
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      setSectionContext(detail ?? null);
    };
    window.addEventListener('atcode:section-context', handler);
    return () => window.removeEventListener('atcode:section-context', handler);
  }, []);

  // Listen for filtered papers context from PapersContent
  const [papersContext, setPapersContext] = useState<{ label: string; summary: string } | null>(null);
  useEffect(() => {
    const handler = (e: Event) => {
      setPapersContext((e as CustomEvent).detail ?? null);
    };
    window.addEventListener('atcode:papers-context', handler);
    return () => window.removeEventListener('atcode:papers-context', handler);
  }, []);

  // Clear section context when navigating away from repo page
  useEffect(() => {
    if (!pathname.startsWith('/repos/')) {
      setSectionContext(null);
    }
  }, [pathname]);

  // Build activeContext — merge section context from overview into operator-like context
  const activeContext = useMemo(() => {
    // Papers page — provide papers-specific context including filtered list
    if (isPapersPage) {
      return {
        pageType: 'papers',
        documentTitle: papersContext?.label || 'Daily Papers',
        selectedText: papersContext?.summary,
      };
    }
    // If we already have an operator page (legacy doc route), use it directly
    if (pageContext?.operatorName) {
      return {
        documentTitle: pageContext.documentTitle,
        filePath: pageContext.filePath,
        pageType: pageContext.pageType,
        operatorName: pageContext.operatorName,
      };
    }
    // If on a repo page viewing an overview section, inject section as operator context
    if (pageContext?.pageType === 'repo' && sectionContext) {
      const repo = repoName || pageContext.documentTitle;
      return {
        pageType: 'operator',
        operatorName: sectionContext.sectionName,
        documentTitle: `${repo} / ${sectionContext.sectionName}`,
        filePath: sectionContext.sectionPath,
      };
    }
    // Default: pass basic page context
    if (pageContext) {
      return { pageType: pageContext.pageType, documentTitle: pageContext.documentTitle };
    }
    return undefined;
  }, [isPapersPage, pageContext, sectionContext, repoName, papersContext]);

  // Track whether a child workspace (e.g. PaperWorkspace) manages its own chat
  const [managedChat, setManagedChat] = useState(false);
  useEffect(() => {
    const handler = (e: Event) => {
      const active = (e as CustomEvent).detail;
      setManagedChat(active);
      // When a workspace takes over chat, close & undock the global chat
      // so DockableLayout doesn't keep an empty sidebar open
      if (active) {
        setIsOpen(false);
        if (isDocked('chat')) undock('chat');
      }
    };
    window.addEventListener('atcode:managed-chat', handler);
    return () => window.removeEventListener('atcode:managed-chat', handler);
  }, [isDocked, undock]);
  // Reset when navigating away from papers
  useEffect(() => {
    if (!isPapersPage) setManagedChat(false);
  }, [isPapersPage]);

  // Don't render on pages that manage their own chat panel
  const isRepoDetail = /^\/repos\/[^/]+/.test(pathname) && !pathname.startsWith('/repos/papers');
  if (pathname.startsWith('/chat/') || isRepoDetail || managedChat) return null;

  return (
    <>
      <FloatingChatWidget
        repoName={repoName}
        isOpen={isOpen}
        onToggle={handleCloseChat}
        activeContext={activeContext}
      />
      {/* Chat FAB — hidden when docked */}
      {!isOpen && !chatDocked && (
        <button
          onClick={handleOpenChat}
          style={{
            position: 'fixed',
            bottom: '24px',
            right: '24px',
            width: '56px',
            height: '56px',
            borderRadius: '50%',
            background: colors.buttonPrimaryBg,
            border: 'none',
            boxShadow: `0 8px 24px ${colors.shadowColor}`,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            transition: 'all 0.3s ease',
            zIndex: 999,
            color: '#ffffff',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.transform = 'scale(1.1)';
            e.currentTarget.style.background = colors.buttonPrimaryHover;
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.transform = 'scale(1)';
            e.currentTarget.style.background = colors.buttonPrimaryBg;
          }}
          title="Open Chat"
        >
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          </svg>
        </button>
      )}
    </>
  );
}

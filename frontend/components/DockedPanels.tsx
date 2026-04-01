'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useCallback, useRef, useEffect } from 'react';
import { useDock } from '@/lib/dock-context';
import { useRepoViewer } from '@/lib/repo-viewer-context';
import { RepoViewerLayout, type RepoViewerHandle } from './repo-viewer';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';

/**
 * Renders docked panels (RepoViewer) in the sidebar.
 * Chat is portaled into #dock-chat-container by FloatingChatWidget when docked.
 * When both are docked, a draggable divider separates them.
 */
export function DockedPanels() {
  const { isDocked, undock, dockedPanels } = useDock();
  const { state: repoState, closePanel } = useRepoViewer();
  const { theme } = useTheme();
  const colors = getThemeColors(theme);
  const repoViewerRef = useRef<RepoViewerHandle>(null);

  // Resizable split: repoViewerPercent is the % of sidebar height for RepoViewer
  const [repoViewerPercent, setRepoViewerPercent] = useState(50);
  const splitDragRef = useRef<{ startY: number; startPct: number; parentHeight: number } | null>(null);
  const splitHandleRef = useRef<HTMLDivElement>(null);
  const repoViewerPanelRef = useRef<HTMLDivElement>(null);

  // Sync committed percent to DOM
  useEffect(() => {
    if (repoViewerPanelRef.current) {
      repoViewerPanelRef.current.style.height = `${repoViewerPercent}%`;
    }
  }, [repoViewerPercent]);

  const onSplitStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const sidebar = (e.currentTarget as HTMLElement).closest('[data-dock-sidebar]');
    const parentHeight = sidebar?.clientHeight || window.innerHeight;
    const currentPct = repoViewerPanelRef.current
      ? (repoViewerPanelRef.current.offsetHeight / parentHeight) * 100
      : repoViewerPercent;
    splitDragRef.current = { startY: e.clientY, startPct: currentPct, parentHeight };
    if (splitHandleRef.current) splitHandleRef.current.style.background = colors.accent;
    document.body.style.cursor = 'row-resize';
    document.body.style.userSelect = 'none';

    const onMove = (ev: MouseEvent) => {
      if (!splitDragRef.current || !repoViewerPanelRef.current) return;
      const deltaY = ev.clientY - splitDragRef.current.startY;
      const deltaPct = (deltaY / splitDragRef.current.parentHeight) * 100;
      const newPct = Math.max(15, Math.min(85, splitDragRef.current.startPct + deltaPct));
      // Direct DOM — no React re-render
      repoViewerPanelRef.current.style.height = `${newPct}%`;
    };

    const onUp = () => {
      // Read final height and commit
      if (repoViewerPanelRef.current && splitDragRef.current) {
        const finalPct = (repoViewerPanelRef.current.offsetHeight / splitDragRef.current.parentHeight) * 100;
        setRepoViewerPercent(Math.max(15, Math.min(85, finalPct)));
      }
      splitDragRef.current = null;
      if (splitHandleRef.current) splitHandleRef.current.style.background = 'transparent';
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [repoViewerPercent, colors.accent]);

  // Navigate to target when docked RepoViewer opens
  React.useEffect(() => {
    if (isDocked('repoViewer') && repoState.isOpen && repoState.targetQualifiedName && repoViewerRef.current) {
      const timer = setTimeout(() => {
        repoViewerRef.current?.navigateTo(repoState.targetQualifiedName!);
      }, 50);
      return () => clearTimeout(timer);
    }
  }, [isDocked, repoState.isOpen, repoState.targetQualifiedName]);

  if (dockedPanels.size === 0) return null;

  const repoViewerDocked = isDocked('repoViewer') && repoState.isOpen && repoState.repoName;
  const chatDocked = isDocked('chat');
  const bothDocked = repoViewerDocked && chatDocked;

  const panelLabelStyle: React.CSSProperties = {
    fontSize: '10px',
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
    color: colors.textMuted,
    padding: '6px 12px 4px',
    background: colors.bgHover,
    borderBottom: `1px solid ${colors.border}`,
    flexShrink: 0,
  };

  const headerBtnStyle: React.CSSProperties = {
    background: 'transparent',
    border: 'none',
    color: colors.textMuted,
    cursor: 'pointer',
    fontSize: '13px',
    padding: '2px 6px',
    borderRadius: '4px',
    display: 'flex',
    alignItems: 'center',
    gap: '4px',
    transition: 'all 0.15s',
  };

  // When both docked, RepoViewer gets repoViewerPercent% and chat gets rest
  // When only RepoViewer is docked, it fills available space (flex: 1)
  const repoViewerStyle: React.CSSProperties = bothDocked
    ? { height: `${repoViewerPercent}%`, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0, flexShrink: 0 }
    : { flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 };

  return (
    <>
      {/* Docked RepoViewer */}
      {repoViewerDocked && (
        <div ref={repoViewerPanelRef} style={repoViewerStyle}>
          {/* Panel label + controls */}
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            ...panelLabelStyle,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
              </svg>
              <span>{repoState.repoName}</span>
            </div>
            <div style={{ display: 'flex', gap: '2px' }}>
              <button
                onClick={() => undock('repoViewer')}
                style={headerBtnStyle}
                title="Undock (float)"
                onMouseEnter={(e) => { e.currentTarget.style.background = colors.accentBg; e.currentTarget.style.color = colors.accent; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = colors.textMuted; }}
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="15 3 21 3 21 9" />
                  <polyline points="9 21 3 21 3 15" />
                  <line x1="21" y1="3" x2="14" y2="10" />
                  <line x1="3" y1="21" x2="10" y2="14" />
                </svg>
              </button>
              <button
                onClick={() => { undock('repoViewer'); closePanel(); }}
                style={headerBtnStyle}
                title="Close"
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = 'rgba(239, 68, 68, 0.15)';
                  e.currentTarget.style.color = '#ef4444';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = 'transparent';
                  e.currentTarget.style.color = colors.textMuted;
                }}
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M18 6L6 18M6 6l12 12"/>
                </svg>
              </button>
            </div>
          </div>
          <div style={{ flex: 1, overflow: 'hidden' }}>
            <RepoViewerLayout ref={repoViewerRef} repoName={repoState.repoName} />
          </div>
        </div>
      )}

      {/* Resizable divider between RepoViewer and Chat */}
      {bothDocked && (
        <div
          ref={splitHandleRef}
          onMouseDown={onSplitStart}
          style={{
            height: '5px',
            cursor: 'row-resize',
            background: 'transparent',
            flexShrink: 0,
            position: 'relative',
            zIndex: 10,
            transition: 'background 0.15s',
          }}
          onMouseEnter={(e) => { if (!splitDragRef.current) e.currentTarget.style.background = colors.accent; }}
          onMouseLeave={(e) => { if (!splitDragRef.current) e.currentTarget.style.background = 'transparent'; }}
        />
      )}

      {/* Chat portal target — FloatingChatWidget renders here when isDocked('chat') */}
    </>
  );
}

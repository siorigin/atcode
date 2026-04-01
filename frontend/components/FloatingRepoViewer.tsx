'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { useRef, useState, useEffect, useCallback } from 'react';
import { RepoViewerLayout, type RepoViewerHandle } from './repo-viewer';
import { useRepoViewer } from '@/lib/repo-viewer-context';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';
import { useDock } from '@/lib/dock-context';

export function FloatingRepoViewer() {
  const { state, closePanel } = useRepoViewer();
  const { theme } = useTheme();
  const colors = getThemeColors(theme);
  const { isDocked, dock } = useDock();
  const repoViewerRef = useRef<RepoViewerHandle>(null);

  const [size, setSize] = useState({ w: 600, h: 650 });
  const [initialized, setInitialized] = useState(false);

  // Use refs for position during drag to avoid React re-renders
  const panelRef = useRef<HTMLDivElement>(null);
  const posRef = useRef({ x: 0, y: 0 });
  const dragRef = useRef<{ startX: number; startY: number; origX: number; origY: number } | null>(null);
  const resizeRef = useRef<{ startX: number; startY: number; origW: number; origH: number } | null>(null);

  // Auto-dock when opening — only run when isOpen transitions to true
  const prevOpenRef = useRef(false);
  useEffect(() => {
    if (state.isOpen && !prevOpenRef.current) {
      // Just became open — auto-dock
      if (!isDocked('repoViewer')) {
        dock('repoViewer');
      }
    }
    prevOpenRef.current = state.isOpen;
  }, [state.isOpen]); // eslint-disable-line react-hooks/exhaustive-deps

  // Center on first open (only used when undocked/floating)
  useEffect(() => {
    if (state.isOpen && !initialized) {
      const x = Math.max(40, Math.round((window.innerWidth - size.w) / 2));
      const y = Math.max(40, Math.round((window.innerHeight - size.h) / 2));
      posRef.current = { x, y };
      if (panelRef.current) {
        panelRef.current.style.transform = `translate(${x}px, ${y}px)`;
      }
      setInitialized(true);
    }
    if (!state.isOpen) {
      setInitialized(false);
    }
  }, [state.isOpen, initialized, size.w, size.h]);

  // Navigate to target when context changes
  useEffect(() => {
    if (state.isOpen && state.targetQualifiedName && repoViewerRef.current) {
      const timer = setTimeout(() => {
        repoViewerRef.current?.navigateTo(state.targetQualifiedName!);
      }, 50);
      return () => clearTimeout(timer);
    }
  }, [state.isOpen, state.targetQualifiedName]);

  // Drag handlers — direct DOM manipulation, no React state during drag
  const onDragStart = useCallback((e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest('button, input, .tree-item, .code-panel')) return;
    e.preventDefault();
    const pos = posRef.current;
    dragRef.current = { startX: e.clientX, startY: e.clientY, origX: pos.x, origY: pos.y };

    const onMove = (ev: MouseEvent) => {
      if (!dragRef.current || !panelRef.current) return;
      const x = Math.max(0, dragRef.current.origX + ev.clientX - dragRef.current.startX);
      const y = Math.max(0, dragRef.current.origY + ev.clientY - dragRef.current.startY);
      posRef.current = { x, y };
      panelRef.current.style.transform = `translate(${x}px, ${y}px)`;
    };
    const onUp = () => {
      dragRef.current = null;
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, []);

  // Resize handlers — direct DOM manipulation during drag, commit on mouseup
  const onResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    resizeRef.current = { startX: e.clientX, startY: e.clientY, origW: size.w, origH: size.h };
    document.body.style.cursor = 'nwse-resize';
    document.body.style.userSelect = 'none';
    const onMove = (ev: MouseEvent) => {
      if (!resizeRef.current || !panelRef.current) return;
      const w = Math.max(400, resizeRef.current.origW + ev.clientX - resizeRef.current.startX);
      const h = Math.max(350, resizeRef.current.origH + ev.clientY - resizeRef.current.startY);
      panelRef.current.style.width = `${w}px`;
      panelRef.current.style.height = `${h}px`;
    };
    const onUp = () => {
      // Commit final size to React state
      if (panelRef.current) {
        setSize({ w: panelRef.current.offsetWidth, h: panelRef.current.offsetHeight });
      }
      resizeRef.current = null;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [size]);

  // Escape to close
  useEffect(() => {
    if (!state.isOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closePanel();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [state.isOpen, closePanel]);

  // When docked, DockedPanels renders the RepoViewer instead
  if (isDocked('repoViewer')) return null;
  if (!state.isOpen || !initialized || !state.repoName) return null;

  const glassBg = theme === 'dark'
    ? 'rgba(16, 20, 28, 0.92)'
    : theme === 'beige'
    ? 'rgba(248, 244, 236, 0.95)'
    : 'rgba(255, 255, 255, 0.95)';

  return (
    <div
      ref={panelRef}
      style={{
        position: 'fixed',
        left: 0,
        top: 0,
        transform: `translate(${posRef.current.x}px, ${posRef.current.y}px)`,
        width: size.w,
        height: size.h,
        zIndex: 1000,
        display: 'flex',
        flexDirection: 'column',
        background: glassBg,
        backdropFilter: 'blur(16px)',
        border: `1px solid ${colors.border}`,
        borderRadius: '12px',
        boxShadow: theme === 'dark'
          ? '0 8px 32px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.05)'
          : '0 8px 32px rgba(0,0,0,0.15), 0 0 0 1px rgba(0,0,0,0.05)',
        overflow: 'hidden',
        willChange: 'transform',
      }}
    >
      {/* Title bar - draggable */}
      <div
        onMouseDown={onDragStart}
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '8px 12px',
          cursor: 'grab',
          borderBottom: `1px solid ${colors.border}`,
          flexShrink: 0,
          userSelect: 'none',
          background: theme === 'dark' ? 'rgba(255,255,255,0.03)' : 'rgba(0,0,0,0.02)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontSize: '14px' }}>{'\u{1F4C2}'}</span>
          <span style={{
            fontSize: '13px',
            fontWeight: 600,
            color: colors.text,
            fontFamily: 'var(--font-jetbrains-mono), monospace',
          }}>
            {state.repoName}
          </span>
        </div>
        <div style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
          {/* Dock button */}
          <button
            onClick={() => dock('repoViewer')}
            style={{
              background: 'transparent',
              border: 'none',
              color: colors.textMuted,
              cursor: 'pointer',
              padding: '2px 6px',
              borderRadius: '4px',
              transition: 'all 0.15s',
              display: 'flex',
              alignItems: 'center',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = colors.accentBg;
              e.currentTarget.style.color = colors.accent;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'transparent';
              e.currentTarget.style.color = colors.textMuted;
            }}
            title="Dock to sidebar"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="3" width="18" height="18" rx="2" />
              <line x1="15" y1="3" x2="15" y2="21" />
            </svg>
          </button>
          {/* Close button */}
          <button
            onClick={closePanel}
            style={{
              background: 'transparent',
              border: 'none',
              color: colors.textMuted,
              cursor: 'pointer',
              fontSize: '18px',
              lineHeight: 1,
              padding: '2px 6px',
              borderRadius: '4px',
              transition: 'all 0.15s',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = 'rgba(239, 68, 68, 0.15)';
              e.currentTarget.style.color = '#ef4444';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'transparent';
              e.currentTarget.style.color = colors.textMuted;
            }}
            title="Close (Esc)"
          >
            {'\u00D7'}
          </button>
        </div>
      </div>

      {/* RepoViewer content */}
      <div style={{ flex: 1, overflow: 'hidden' }}>
        <RepoViewerLayout ref={repoViewerRef} repoName={state.repoName} />
      </div>

      {/* Resize handle */}
      <div
        onMouseDown={onResizeStart}
        style={{
          position: 'absolute',
          right: 0,
          bottom: 0,
          width: '16px',
          height: '16px',
          cursor: 'nwse-resize',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <svg width="10" height="10" viewBox="0 0 10 10" style={{ opacity: 0.3 }}>
          <line x1="9" y1="1" x2="1" y2="9" stroke={colors.textMuted} strokeWidth="1.5" />
          <line x1="9" y1="5" x2="5" y2="9" stroke={colors.textMuted} strokeWidth="1.5" />
        </svg>
      </div>
    </div>
  );
}

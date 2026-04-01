'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { useRef, useState, useCallback, useEffect } from 'react';
import { useDock } from '@/lib/dock-context';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';

interface DockableLayoutProps {
  children: React.ReactNode;
  dockedContent?: React.ReactNode;
}

/**
 * Wraps main content with a stable flex container.
 * The sidebar is always in the DOM but hidden when no panels are docked,
 * so toggling dock state never remounts children.
 *
 * Sidebar resize uses direct DOM manipulation during drag for smooth 60fps performance,
 * committing to React state only on mouseup.
 */
export function DockableLayout({ children, dockedContent }: DockableLayoutProps) {
  const { dockedPanels } = useDock();
  const { theme } = useTheme();
  const colors = getThemeColors(theme);
  const hasDockedPanels = dockedPanels.size > 0;

  // Resizable sidebar width — React state is only the "committed" value (updated on mouseup)
  const [sidebarWidth, setSidebarWidth] = useState(460);
  const dragRef = useRef<{ startX: number; startW: number } | null>(null);
  const handleRef = useRef<HTMLDivElement>(null);
  const sidebarRef = useRef<HTMLDivElement>(null);
  const mainRef = useRef<HTMLDivElement>(null);

  // Sync committed width to sidebar DOM on state change (e.g. initial render, after drag)
  useEffect(() => {
    if (sidebarRef.current && hasDockedPanels) {
      sidebarRef.current.style.width = `${sidebarWidth}px`;
    }
  }, [sidebarWidth, hasDockedPanels]);

  const onResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const currentWidth = sidebarRef.current?.offsetWidth || sidebarWidth;
    dragRef.current = { startX: e.clientX, startW: currentWidth };

    // Visual feedback on handle
    if (handleRef.current) handleRef.current.style.background = colors.accent;

    // Disable transitions during drag for instant feedback
    if (sidebarRef.current) sidebarRef.current.style.transition = 'none';

    // Disable pointer events on iframes/embeds to prevent them stealing mousemove
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const onMove = (ev: MouseEvent) => {
      if (!dragRef.current || !sidebarRef.current) return;
      const delta = dragRef.current.startX - ev.clientX;
      const newW = Math.max(320, Math.min(window.innerWidth * 0.6, dragRef.current.startW + delta));
      // Direct DOM — no React re-render
      sidebarRef.current.style.width = `${newW}px`;
    };

    const onUp = () => {
      // Read final width from DOM and commit to React state
      const finalWidth = sidebarRef.current?.offsetWidth || sidebarWidth;
      dragRef.current = null;
      setSidebarWidth(finalWidth);

      // Restore transitions and cursor
      if (sidebarRef.current) sidebarRef.current.style.transition = 'width 0.2s ease, min-width 0.2s ease';
      if (handleRef.current) handleRef.current.style.background = 'transparent';
      document.body.style.cursor = '';
      document.body.style.userSelect = '';

      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [sidebarWidth, colors.accent]);

  return (
    <div style={{
      display: 'flex',
      width: '100%',
      height: '100%',
      minHeight: '100vh',
    }}>
      {/* Main content area — always the same DOM node */}
      <div ref={mainRef} style={{
        flex: 1,
        minWidth: 0,
        overflow: 'auto',
      }}>
        {children}
      </div>

      {/* Resize handle — only visible when docked */}
      <div
        ref={handleRef}
        onMouseDown={hasDockedPanels ? onResizeStart : undefined}
        style={{
          width: hasDockedPanels ? '6px' : '0px',
          cursor: hasDockedPanels ? 'col-resize' : 'default',
          background: 'transparent',
          flexShrink: 0,
          position: 'relative',
          zIndex: 10,
          transition: 'width 0.2s ease, background 0.15s ease',
          overflow: 'hidden',
        }}
        onMouseEnter={(e) => { if (hasDockedPanels && !dragRef.current) e.currentTarget.style.background = colors.accent + '66'; }}
        onMouseLeave={(e) => { if (!dragRef.current) e.currentTarget.style.background = 'transparent'; }}
      >
        {hasDockedPanels && (
          <div style={{
            position: 'absolute',
            top: '50%',
            left: '50%',
            transform: 'translate(-50%, -50%)',
            width: '2px',
            height: '32px',
            borderRadius: '1px',
            background: colors.textDimmed,
            opacity: 0.4,
            pointerEvents: 'none',
          }} />
        )}
      </div>

      {/* Docked sidebar — always in DOM, collapses to 0 width when empty */}
      <div ref={sidebarRef} data-dock-sidebar style={{
        width: hasDockedPanels ? `${sidebarWidth}px` : '0px',
        minWidth: hasDockedPanels ? '320px' : '0px',
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        position: 'sticky',
        top: 0,
        overflow: 'hidden',
        background: colors.bg,
        flexShrink: 0,
        borderLeft: hasDockedPanels ? `1px solid ${colors.border}` : 'none',
        transition: 'width 0.2s ease, min-width 0.2s ease',
      }}>
        {dockedContent}
        {/* Portal target for docked chat */}
        <div id="dock-chat-container" style={{
          flex: dockedPanels.has('chat') ? 1 : 0,
          minHeight: 0,
          display: dockedPanels.has('chat') ? 'flex' : 'none',
          flexDirection: 'column',
        }} />
      </div>
    </div>
  );
}

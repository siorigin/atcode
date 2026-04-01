'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';
import { useDocking } from './DockingContext';
import type { PanelId } from '@/lib/layout-tree';

interface PanelHeaderProps {
  title: string;
  icon?: React.ReactNode;
  panelId: PanelId;
  onClose: () => void;
}

// 6-dot grip icon
function GripIcon() {
  return (
    <svg width="8" height="14" viewBox="0 0 8 14" fill="currentColor" opacity={0.4}>
      <circle cx="2" cy="2" r="1.2" />
      <circle cx="6" cy="2" r="1.2" />
      <circle cx="2" cy="7" r="1.2" />
      <circle cx="6" cy="7" r="1.2" />
      <circle cx="2" cy="12" r="1.2" />
      <circle cx="6" cy="12" r="1.2" />
    </svg>
  );
}

export function PanelHeader({ title, icon, panelId, onClose }: PanelHeaderProps) {
  const { theme } = useTheme();
  const colors = getThemeColors(theme);
  const { startDrag } = useDocking();

  return (
    <div
      style={{
        height: 34,
        minHeight: 34,
        display: 'flex',
        alignItems: 'center',
        gap: 7,
        padding: '0 10px 0 6px',
        borderBottom: `1px solid ${colors.border}`,
        background: colors.card,
        fontSize: 12,
        fontWeight: 600,
        color: colors.textMuted,
        textTransform: 'uppercase',
        letterSpacing: '0.5px',
        fontFamily: "'Inter', -apple-system, sans-serif",
        flexShrink: 0,
        userSelect: 'none',
      }}
    >
      {/* Drag handle */}
      <span
        onMouseDown={(e) => startDrag(panelId, e)}
        style={{
          display: 'flex',
          alignItems: 'center',
          cursor: 'grab',
          padding: '2px 3px',
          borderRadius: 4,
        }}
        title="Drag to rearrange"
      >
        <GripIcon />
      </span>
      {icon && <span style={{ display: 'flex', alignItems: 'center', fontSize: 14 }}>{icon}</span>}
      <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {title}
      </span>
      <button
        onClick={onClose}
        style={{
          background: 'none',
          border: 'none',
          color: colors.textDimmed,
          cursor: 'pointer',
          padding: '2px 4px',
          fontSize: 16,
          lineHeight: 1,
          display: 'flex',
          alignItems: 'center',
          borderRadius: 4,
          transition: 'all 0.12s',
        }}
        onMouseEnter={(e) => { e.currentTarget.style.color = colors.text; e.currentTarget.style.background = colors.bgHover; }}
        onMouseLeave={(e) => { e.currentTarget.style.color = colors.textDimmed; e.currentTarget.style.background = 'none'; }}
        title="Close panel"
      >
        &times;
      </button>
    </div>
  );
}

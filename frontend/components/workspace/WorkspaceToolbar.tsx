'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';
import type { PanelId } from '@/lib/layout-tree';

interface PanelToggle {
  id: PanelId;
  label: string;
  icon: React.ReactNode;
}

interface WorkspaceToolbarProps {
  panels: PanelToggle[];
  activePanels: PanelId[];
  onToggle: (id: PanelId) => void;
}

export function WorkspaceToolbar({ panels, activePanels, onToggle }: WorkspaceToolbarProps) {
  const { theme } = useTheme();
  const colors = getThemeColors(theme);

  return (
    <div
      style={{
        height: 40,
        minHeight: 40,
        display: 'flex',
        alignItems: 'center',
        gap: 4,
        padding: '0 10px',
        borderBottom: `1px solid ${colors.border}`,
        background: colors.card,
        flexShrink: 0,
        overflow: 'hidden',
      }}
    >
      {panels.map((panel) => {
        const isActive = activePanels.includes(panel.id);
        return (
          <button
            key={panel.id}
            onClick={() => onToggle(panel.id)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              padding: '5px 12px',
              fontSize: 13,
              fontWeight: isActive ? 600 : 450,
              color: isActive ? colors.accent : colors.textMuted,
              background: isActive ? colors.accent + '15' : 'transparent',
              border: `1px solid ${isActive ? colors.accent + '40' : 'transparent'}`,
              borderRadius: 7,
              cursor: 'pointer',
              fontFamily: "'Inter', -apple-system, sans-serif",
              transition: 'all 0.15s ease',
              whiteSpace: 'nowrap',
            }}
            onMouseEnter={(e) => {
              if (!isActive) {
                e.currentTarget.style.background = colors.bgHover;
                e.currentTarget.style.color = colors.text;
              }
            }}
            onMouseLeave={(e) => {
              if (!isActive) {
                e.currentTarget.style.background = 'transparent';
                e.currentTarget.style.color = colors.textMuted;
              }
            }}
          >
            <span style={{ display: 'flex', alignItems: 'center', fontSize: 15 }}>{panel.icon}</span>
            {panel.label}
          </button>
        );
      })}
    </div>
  );
}

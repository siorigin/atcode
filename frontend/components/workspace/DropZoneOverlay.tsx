'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useCallback } from 'react';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';
import { useDocking, getDropPosition } from './DockingContext';
import type { PanelId, DropPosition } from '@/lib/layout-tree';

interface DropZoneOverlayProps {
  panelId: PanelId;
  containerRef: React.RefObject<HTMLElement | null>;
}

const positionStyles: Record<DropPosition, React.CSSProperties> = {
  top: { top: 0, left: 0, right: 0, height: '50%' },
  bottom: { bottom: 0, left: 0, right: 0, height: '50%' },
  left: { top: 0, bottom: 0, left: 0, width: '50%' },
  right: { top: 0, bottom: 0, right: 0, width: '50%' },
};

export function DropZoneOverlay({ panelId, containerRef }: DropZoneOverlayProps) {
  const { dragState } = useDocking();
  const { theme } = useTheme();
  const colors = getThemeColors(theme);
  const [hovered, setHovered] = useState<DropPosition | null>(null);

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    const el = containerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    setHovered(getDropPosition(rect, e.clientX, e.clientY));
  }, [containerRef]);

  if (!dragState || dragState.panelId === panelId) return null;

  // Compute current hovered position from dragState
  const el = containerRef.current;
  let currentPos: DropPosition | null = null;
  if (el) {
    const rect = el.getBoundingClientRect();
    const mx = dragState.mouseX;
    const my = dragState.mouseY;
    if (mx >= rect.left && mx <= rect.right && my >= rect.top && my <= rect.bottom) {
      currentPos = getDropPosition(rect, mx, my);
    }
  }

  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        zIndex: 50,
        pointerEvents: 'none',
      }}
    >
      {currentPos && (
        <div
          style={{
            position: 'absolute',
            ...positionStyles[currentPos],
            background: colors.accent + '30',
            border: `2px solid ${colors.accent}60`,
            borderRadius: 4,
            transition: 'all 0.1s ease',
          }}
        />
      )}
    </div>
  );
}

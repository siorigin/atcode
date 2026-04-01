'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { useRef, useCallback } from 'react';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';

interface ResizeDividerProps {
  direction: 'horizontal' | 'vertical';
  path: number[];
  childIndex: number;
  sizes: number[];
  onResizeEnd: (path: number[], newSizes: number[]) => void;
  containerRef: React.RefObject<HTMLElement | null>;
}

export function ResizeDivider({
  direction,
  path,
  childIndex,
  sizes,
  onResizeEnd,
  containerRef,
}: ResizeDividerProps) {
  const { theme } = useTheme();
  const colors = getThemeColors(theme);
  const handleRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ startPos: number; startSizes: number[]; containerSize: number } | null>(null);

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const container = containerRef.current;
    if (!container) return;

    // direction='horizontal' means rows (vertical splitter), resize heights
    // direction='vertical' means cols (horizontal splitter), resize widths
    const containerSize = direction === 'horizontal'
      ? container.clientHeight
      : container.clientWidth;

    dragRef.current = {
      startPos: direction === 'horizontal' ? e.clientY : e.clientX,
      startSizes: [...sizes],
      containerSize,
    };

    if (handleRef.current) handleRef.current.style.background = colors.accent;
    document.body.style.cursor = direction === 'horizontal' ? 'row-resize' : 'col-resize';
    document.body.style.userSelect = 'none';

    // Create a full-viewport overlay to prevent iframes from stealing mouse events
    const overlay = document.createElement('div');
    overlay.style.cssText = `position:fixed;inset:0;z-index:99999;cursor:${direction === 'horizontal' ? 'row-resize' : 'col-resize'}`;
    document.body.appendChild(overlay);

    const onMove = (ev: MouseEvent) => {
      if (!dragRef.current) return;
      const pos = direction === 'horizontal' ? ev.clientY : ev.clientX;
      const delta = pos - dragRef.current.startPos;
      const deltaPct = (delta / dragRef.current.containerSize) * 100;

      const newSizes = [...dragRef.current.startSizes];
      const minPct = 10;

      // Adjust the two adjacent sizes
      let newBefore = newSizes[childIndex] + deltaPct;
      let newAfter = newSizes[childIndex + 1] - deltaPct;

      if (newBefore < minPct) { newAfter -= (minPct - newBefore); newBefore = minPct; }
      if (newAfter < minPct) { newBefore -= (minPct - newAfter); newAfter = minPct; }

      newSizes[childIndex] = newBefore;
      newSizes[childIndex + 1] = newAfter;

      // Direct DOM manipulation for responsiveness
      const parent = handleRef.current?.parentElement;
      if (parent) {
        const children = Array.from(parent.children);
        const handleIdx = children.indexOf(handleRef.current!);
        const before = children[handleIdx - 1] as HTMLElement | undefined;
        const after = children[handleIdx + 1] as HTMLElement | undefined;
        if (before && after) {
          if (direction === 'horizontal') {
            before.style.height = `${newBefore}%`;
            after.style.height = `${newAfter}%`;
          } else {
            before.style.width = `${newBefore}%`;
            after.style.width = `${newAfter}%`;
          }
        }
      }
    };

    const onUp = () => {
      if (!dragRef.current) return;

      // Read final sizes from DOM
      const parent = handleRef.current?.parentElement;
      const newSizes = [...dragRef.current.startSizes];
      if (parent && containerRef.current) {
        const children = Array.from(parent.children);
        const handleIdx = children.indexOf(handleRef.current!);
        const before = children[handleIdx - 1] as HTMLElement | undefined;
        const after = children[handleIdx + 1] as HTMLElement | undefined;
        const totalSize = direction === 'horizontal'
          ? containerRef.current.clientHeight
          : containerRef.current.clientWidth;

        if (before && totalSize > 0) {
          const beforeSize = direction === 'horizontal' ? before.offsetHeight : before.offsetWidth;
          newSizes[childIndex] = (beforeSize / totalSize) * 100;
        }
        if (after && totalSize > 0) {
          const afterSize = direction === 'horizontal' ? after.offsetHeight : after.offsetWidth;
          newSizes[childIndex + 1] = (afterSize / totalSize) * 100;
        }
      }

      dragRef.current = null;
      if (handleRef.current) handleRef.current.style.background = 'transparent';
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      overlay.remove();

      onResizeEnd(path, newSizes);

      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [direction, path, childIndex, sizes, onResizeEnd, containerRef, colors.accent]);

  const isHorizontal = direction === 'horizontal';

  return (
    <div
      ref={handleRef}
      onMouseDown={onMouseDown}
      style={{
        width: isHorizontal ? '100%' : 6,
        height: isHorizontal ? 6 : '100%',
        cursor: isHorizontal ? 'row-resize' : 'col-resize',
        background: 'transparent',
        flexShrink: 0,
        position: 'relative',
        zIndex: 10,
        transition: 'background 0.15s ease',
      }}
      onMouseEnter={(e) => {
        if (!dragRef.current) e.currentTarget.style.background = colors.accent + '44';
      }}
      onMouseLeave={(e) => {
        if (!dragRef.current) e.currentTarget.style.background = 'transparent';
      }}
    >
      <div
        style={{
          position: 'absolute',
          ...(isHorizontal
            ? { top: -3, bottom: -3, left: 0, right: 0 }
            : { left: -3, right: -3, top: 0, bottom: 0 }),
        }}
      />
    </div>
  );
}

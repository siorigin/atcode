'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { createContext, useContext, useState, useCallback, useRef, useEffect } from 'react';
import type { PanelId, DropPosition } from '@/lib/layout-tree';

interface DragState {
  panelId: PanelId;
  mouseX: number;
  mouseY: number;
}

interface DockingContextValue {
  dragState: DragState | null;
  startDrag: (panelId: PanelId, e: React.MouseEvent) => void;
  dropTargets: React.MutableRefObject<Map<PanelId, HTMLElement>>;
  registerTarget: (id: PanelId, el: HTMLElement) => void;
  unregisterTarget: (id: PanelId) => void;
}

const DockingContext = createContext<DockingContextValue | null>(null);

export function useDocking() {
  const ctx = useContext(DockingContext);
  if (!ctx) throw new Error('useDocking must be used within DockingProvider');
  return ctx;
}

export function getDropPosition(rect: DOMRect, mx: number, my: number): DropPosition {
  const rx = (mx - rect.left) / rect.width;   // 0..1
  const ry = (my - rect.top) / rect.height;   // 0..1
  // Diagonals y=x and y=1-x divide the rect into 4 triangular zones
  if (ry < rx && ry < 1 - rx) return 'top';
  if (ry > rx && ry > 1 - rx) return 'bottom';
  if (ry > rx && ry < 1 - rx) return 'left';
  return 'right';
}

interface DockingProviderProps {
  onDrop: (panelId: PanelId, targetId: PanelId, pos: DropPosition) => void;
  children: React.ReactNode;
}

export function DockingProvider({ onDrop, children }: DockingProviderProps) {
  const [dragState, setDragState] = useState<DragState | null>(null);
  const dropTargets = useRef<Map<PanelId, HTMLElement>>(new Map());
  const dragRef = useRef<DragState | null>(null);
  const rafRef = useRef<number>(0);

  const startDrag = useCallback((panelId: PanelId, e: React.MouseEvent) => {
    e.preventDefault();
    const state: DragState = { panelId, mouseX: e.clientX, mouseY: e.clientY };
    dragRef.current = state;
    setDragState(state);

    document.body.style.cursor = 'grabbing';
    document.body.style.userSelect = 'none';

    // Full-viewport overlay prevents iframes from stealing mouse events during drag
    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;inset:0;z-index:99999;cursor:grabbing';
    document.body.appendChild(overlay);

    const onMove = (ev: MouseEvent) => {
      if (!dragRef.current) return;
      cancelAnimationFrame(rafRef.current);
      rafRef.current = requestAnimationFrame(() => {
        const newState = { ...dragRef.current!, mouseX: ev.clientX, mouseY: ev.clientY };
        dragRef.current = newState;
        setDragState(newState);
      });
    };

    const onUp = (ev: MouseEvent) => {
      cancelAnimationFrame(rafRef.current);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      overlay.remove();

      if (dragRef.current) {
        // Hit test against drop targets
        const mx = ev.clientX;
        const my = ev.clientY;
        const sourceId = dragRef.current.panelId;

        for (const [targetId, el] of dropTargets.current.entries()) {
          if (targetId === sourceId) continue;
          const rect = el.getBoundingClientRect();
          if (mx >= rect.left && mx <= rect.right && my >= rect.top && my <= rect.bottom) {
            const pos = getDropPosition(rect, mx, my);
            onDrop(sourceId, targetId, pos);
            break;
          }
        }
      }

      dragRef.current = null;
      setDragState(null);

      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };

    const onKeyDown = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape') {
        cancelAnimationFrame(rafRef.current);
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        overlay.remove();
        dragRef.current = null;
        setDragState(null);
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
        window.removeEventListener('keydown', onKeyDown);
      }
    };

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    window.addEventListener('keydown', onKeyDown);
  }, [onDrop]);

  const registerTarget = useCallback((id: PanelId, el: HTMLElement) => {
    dropTargets.current.set(id, el);
  }, []);

  const unregisterTarget = useCallback((id: PanelId) => {
    dropTargets.current.delete(id);
  }, []);

  return (
    <DockingContext.Provider value={{ dragState, startDrag, dropTargets, registerTarget, unregisterTarget }}>
      {children}
    </DockingContext.Provider>
  );
}

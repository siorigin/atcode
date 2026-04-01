'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useRef, useState, useCallback } from 'react';
import { getThemeColors } from '@/lib/theme-colors';

interface LassoRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

interface LassoSelectorProps {
  containerRef: React.RefObject<HTMLElement | null>;
  cardRefs: Map<string, HTMLElement>;
  onSelectionChange: (selectedIds: Set<string>) => void;
  enabled: boolean;
  theme?: 'dark' | 'light' | 'beige';
}

export function LassoSelector({
  containerRef,
  cardRefs,
  onSelectionChange,
  enabled,
  theme = 'dark',
}: LassoSelectorProps) {
  const colors = getThemeColors(theme);
  const [isSelecting, setIsSelecting] = useState(false);
  const [startPoint, setStartPoint] = useState<{ x: number; y: number } | null>(null);
  const [endPoint, setEndPoint] = useState<{ x: number; y: number } | null>(null);
  const [lassoRect, setLassoRect] = useState<LassoRect | null>(null);
  const rafRef = useRef<number | undefined>(undefined);

  // Calculate the bounding rectangle from start and end points
  const calculateLassoRect = useCallback((start: { x: number; y: number }, end: { x: number; y: number }): LassoRect => {
    return {
      left: Math.min(start.x, end.x),
      top: Math.min(start.y, end.y),
      width: Math.abs(end.x - start.x),
      height: Math.abs(end.y - start.y),
    };
  }, []);

  // Check if two rectangles intersect
  const rectsIntersect = useCallback((rect1: LassoRect, rect2: DOMRect): boolean => {
    return !(
      rect1.left + rect1.width < rect2.left ||
      rect2.left + rect2.width < rect1.left ||
      rect1.top + rect1.height < rect2.top ||
      rect2.top + rect2.height < rect1.top
    );
  }, []);

  // Find cards that intersect with the lasso rectangle
  const findIntersectingCards = useCallback((lasso: LassoRect): Set<string> => {
    const intersecting = new Set<string>();

    cardRefs.forEach((element, id) => {
      const rect = element.getBoundingClientRect();
      if (rectsIntersect(lasso, rect)) {
        intersecting.add(id);
      }
    });

    return intersecting;
  }, [cardRefs, rectsIntersect]);

  // Handle mouse down on container
  const handleMouseDown = useCallback((e: MouseEvent) => {
    if (!enabled) return;

    // Only start selection on left click and if clicking on the container background
    if (e.button !== 0) return;

    const target = e.target as HTMLElement;

    // Don't start lasso if clicking on a card or interactive element
    if (target.closest('[data-card]') || target.closest('button') || target.closest('input')) {
      return;
    }

    // Check if clicking on the container background
    const container = containerRef.current;
    if (!container) return;

    e.preventDefault();

    setStartPoint({ x: e.clientX, y: e.clientY });
    setEndPoint({ x: e.clientX, y: e.clientY });
    setIsSelecting(true);
  }, [enabled, containerRef]);

  // Handle mouse move during selection
  const handleMouseMove = useCallback((e: MouseEvent) => {
    if (!isSelecting || !startPoint) return;

    // Cancel any pending RAF
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
    }

    // Use requestAnimationFrame for smooth updates
    rafRef.current = requestAnimationFrame(() => {
      const newEndPoint = { x: e.clientX, y: e.clientY };
      setEndPoint(newEndPoint);

      const newLasso = calculateLassoRect(startPoint, newEndPoint);
      setLassoRect(newLasso);

      // Find and notify about intersecting cards
      const intersecting = findIntersectingCards(newLasso);
      onSelectionChange(intersecting);
    });
  }, [isSelecting, startPoint, calculateLassoRect, findIntersectingCards, onSelectionChange]);

  // Handle mouse up to end selection
  const handleMouseUp = useCallback(() => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
    }

    setIsSelecting(false);
    setStartPoint(null);
    setEndPoint(null);
    setLassoRect(null);
  }, []);

  // Set up event listeners
  useEffect(() => {
    if (!enabled) return;

    const container = containerRef.current;
    if (!container) return;

    container.addEventListener('mousedown', handleMouseDown);
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);

    return () => {
      container.removeEventListener('mousedown', handleMouseDown);
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
      }
    };
  }, [enabled, containerRef, handleMouseDown, handleMouseMove, handleMouseUp]);

  if (!isSelecting || !lassoRect) return null;

  // Only render if selection is significant (more than 5px in any direction)
  if (lassoRect.width < 5 && lassoRect.height < 5) return null;

  return (
    <div
      style={{
        position: 'fixed',
        left: lassoRect.left,
        top: lassoRect.top,
        width: lassoRect.width,
        height: lassoRect.height,
        border: `2px solid ${colors.accent}`,
        background: `${colors.accent}15`,
        borderRadius: '4px',
        pointerEvents: 'none',
        zIndex: 999,
        boxShadow: `0 0 0 1px ${colors.accent}30`,
      }}
      aria-hidden="true"
    />
  );
}

// Hook for managing lasso selection state
export function useLassoSelection() {
  const [selectedCards, setSelectedCards] = useState<Set<string>>(new Set());
  const [isLassoActive, setIsLassoActive] = useState(false);
  const cardRefsMap = useRef<Map<string, HTMLElement>>(new Map());

  const registerCard = useCallback((id: string, element: HTMLElement | null) => {
    if (element) {
      cardRefsMap.current.set(id, element);
    } else {
      cardRefsMap.current.delete(id);
    }
  }, []);

  const handleLassoSelection = useCallback((ids: Set<string>) => {
    setSelectedCards(ids);
    setIsLassoActive(ids.size > 0);
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedCards(new Set());
    setIsLassoActive(false);
  }, []);

  const toggleSelection = useCallback((id: string, ctrlKey: boolean) => {
    setSelectedCards((prev) => {
      const newSet = new Set(prev);
      if (ctrlKey) {
        // Toggle with Ctrl
        if (newSet.has(id)) {
          newSet.delete(id);
        } else {
          newSet.add(id);
        }
      } else {
        // Single select without Ctrl
        if (newSet.has(id) && newSet.size === 1) {
          // Deselect if already the only selection
          newSet.clear();
        } else {
          newSet.clear();
          newSet.add(id);
        }
      }
      return newSet;
    });
  }, []);

  const selectRange = useCallback((fromId: string, toId: string, allIds: string[]) => {
    const fromIndex = allIds.indexOf(fromId);
    const toIndex = allIds.indexOf(toId);

    if (fromIndex === -1 || toIndex === -1) return;

    const start = Math.min(fromIndex, toIndex);
    const end = Math.max(fromIndex, toIndex);

    setSelectedCards(new Set(allIds.slice(start, end + 1)));
  }, []);

  const selectAll = useCallback((allIds: string[]) => {
    setSelectedCards(new Set(allIds));
  }, []);

  return {
    selectedCards,
    isLassoActive,
    cardRefs: cardRefsMap.current,
    registerCard,
    handleLassoSelection,
    clearSelection,
    toggleSelection,
    selectRange,
    selectAll,
    setSelectedCards,
  };
}

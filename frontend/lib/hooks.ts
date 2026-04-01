// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState, useCallback } from 'react';
export {
  useBackgroundTask,
  type TaskState,
  type UseBackgroundTaskOptions,
  type UseBackgroundTaskReturn,
} from './hooks/useBackgroundTask';

export type ThemeType = 'dark' | 'light' | 'beige';

/**
 * Hook to detect and track theme changes
 * Returns isLightTheme for backward compatibility (true for light or beige themes)
 */
export function useThemeDetection() {
  const [isLightTheme, setIsLightTheme] = useState(() =>
    typeof document !== 'undefined'
      ? document.body.classList.contains('light') || document.body.classList.contains('beige')
      : false
  );

  useEffect(() => {
    const checkTheme = () => {
      if (typeof document !== 'undefined') {
        const lightTheme = document.body.classList.contains('light') || document.body.classList.contains('beige');
        setIsLightTheme(lightTheme);
      }
    };

    checkTheme();

    const observer = new MutationObserver(checkTheme);
    if (typeof document !== 'undefined' && document.body) {
      observer.observe(document.body, {
        attributes: true,
        attributeFilter: ['class']
      });
    }

    return () => observer.disconnect();
  }, []);

  return isLightTheme;
}

/**
 * Hook to detect the actual theme type (dark, light, or beige)
 */
export function useThemeType(): ThemeType {
  const [themeType, setThemeType] = useState<ThemeType>(() => {
    if (typeof document === 'undefined') return 'dark';
    if (document.body.classList.contains('beige')) return 'beige';
    if (document.body.classList.contains('light')) return 'light';
    return 'dark';
  });

  useEffect(() => {
    const checkTheme = () => {
      if (typeof document !== 'undefined') {
        if (document.body.classList.contains('beige')) {
          setThemeType('beige');
        } else if (document.body.classList.contains('light')) {
          setThemeType('light');
        } else {
          setThemeType('dark');
        }
      }
    };

    checkTheme();

    const observer = new MutationObserver(checkTheme);
    if (typeof document !== 'undefined' && document.body) {
      observer.observe(document.body, {
        attributes: true,
        attributeFilter: ['class']
      });
    }

    return () => observer.disconnect();
  }, []);

  return themeType;
}

/**
 * Hook to handle column resizing with drag
 */
export function useColumnResize(initialWidth: number = 45, enabled: boolean = true) {
  const [width, setWidth] = useState(initialWidth);
  const [isDragging, setIsDragging] = useState(false);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (!enabled) return;
    setIsDragging(true);
    e.preventDefault();
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return;

    let rafId: number | null = null;

    const handleMouseMove = (e: MouseEvent) => {
      if (!isDragging) return;

      if (rafId) cancelAnimationFrame(rafId);

      rafId = requestAnimationFrame(() => {
        const container = document.querySelector('main');
        if (!container) return;

        const containerRect = container.getBoundingClientRect();
        const newWidth = ((e.clientX - containerRect.left) / containerRect.width) * 100;

        // Constrain between 25% and 75%
        const constrainedWidth = Math.max(25, Math.min(75, newWidth));
        setWidth(constrainedWidth);
      });
    };

    const handleMouseUp = () => {
      setIsDragging(false);
      if (rafId) {
        cancelAnimationFrame(rafId);
        rafId = null;
      }
    };

    if (isDragging) {
      document.addEventListener('mousemove', handleMouseMove, { passive: true });
      document.addEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    } else {
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    }

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      if (rafId) {
        cancelAnimationFrame(rafId);
      }
    };
  }, [isDragging, enabled]);

  return { width, isDragging, handleMouseDown };
}

'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * React Hook for Responsive Design
 *
 * Automatically updates when window size changes.
 */

import { useState, useEffect } from 'react';
import { responsive, type ResponsiveState } from './responsive';

export function useResponsive(): ResponsiveState {
  const [state, setState] = useState<ResponsiveState>(() => {
    // Initial state (works on server too)
    if (typeof window === 'undefined') {
      return {
        isMobile: false,
        isTablet: false,
        isDesktop: true,
        width: 1024,
        height: 768,
        pagePadding: responsive.pagePadding.desktop as string,
        gridGap: responsive.cardGridGap.desktop as string,
        gridColumns: `repeat(auto-fill, minmax(${responsive.gridMinWidth}px, 1fr))`,
        maxWidth: '1400px',
      };
    }

    const width = window.innerWidth;
    const height = window.innerHeight;
    const isMobile = width <= responsive.mobileMax;
    const isTablet = width > responsive.mobileMax && width <= responsive.tabletMax;
    const isDesktop = width >= responsive.desktopMin;

    // Get responsive values
    const pagePadding = width <= responsive.mobileMax
      ? responsive.pagePadding.mobile as string
      : width <= responsive.tabletMax
        ? responsive.pagePadding.tablet as string
        : responsive.pagePadding.desktop as string;

    const gridGap = width <= responsive.mobileMax
      ? responsive.cardGridGap.mobile as string
      : width <= responsive.tabletMax
        ? responsive.cardGridGap.tablet as string
        : responsive.cardGridGap.desktop as string;

    const gridColumns = width <= 640
      ? 'repeat(auto-fill, minmax(240px, 1fr))'
      : width <= responsive.mobileMax
        ? 'repeat(auto-fill, minmax(260px, 1fr))'
        : `repeat(auto-fill, minmax(${responsive.gridMinWidth}px, 1fr))`;

    const maxWidth = width <= responsive.mobileMax ? '100%' : '1400px';

    return {
      isMobile,
      isTablet,
      isDesktop,
      width,
      height,
      pagePadding,
      gridGap,
      gridColumns,
      maxWidth,
    };
  });

  useEffect(() => {
    if (typeof window === 'undefined') return;

    let rafId: number | null = null;

    const handleResize = () => {
      // Use RAF to avoid excessive updates
      if (rafId !== null) {
        cancelAnimationFrame(rafId);
      }

      rafId = requestAnimationFrame(() => {
        const width = window.innerWidth;
        const height = window.innerHeight;
        const isMobile = width <= responsive.mobileMax;
        const isTablet = width > responsive.mobileMax && width <= responsive.tabletMax;
        const isDesktop = width >= responsive.desktopMin;

        // Get responsive values
        const pagePadding = width <= responsive.mobileMax
          ? responsive.pagePadding.mobile as string
          : width <= responsive.tabletMax
            ? responsive.pagePadding.tablet as string
            : responsive.pagePadding.desktop as string;

        const gridGap = width <= responsive.mobileMax
          ? responsive.cardGridGap.mobile as string
          : width <= responsive.tabletMax
            ? responsive.cardGridGap.tablet as string
            : responsive.cardGridGap.desktop as string;

        const gridColumns = width <= 640
          ? 'repeat(auto-fill, minmax(240px, 1fr))'
          : width <= responsive.mobileMax
            ? 'repeat(auto-fill, minmax(260px, 1fr))'
            : `repeat(auto-fill, minmax(${responsive.gridMinWidth}px, 1fr))`;

        const maxWidth = width <= responsive.mobileMax ? '100%' : '1400px';

        setState({
          isMobile,
          isTablet,
          isDesktop,
          width,
          height,
          pagePadding,
          gridGap,
          gridColumns,
          maxWidth,
        });
      });
    };

    // Add event listener with passive option for better performance
    window.addEventListener('resize', handleResize, { passive: true });

    // Initial call
    handleResize();

    return () => {
      window.removeEventListener('resize', handleResize);
      if (rafId !== null) {
        cancelAnimationFrame(rafId);
      }
    };
  }, []);

  return state;
}

/**
 * Simpler hook that just returns screen size info
 */
export function useScreenSize() {
  const [screenSize, setScreenSize] = useState(() => {
    if (typeof window === 'undefined') {
      return { width: 1024, height: 768 };
    }
    return {
      width: window.innerWidth,
      height: window.innerHeight,
    };
  });

  useEffect(() => {
    if (typeof window === 'undefined') return;

    let rafId: number | null = null;

    const handleResize = () => {
      if (rafId !== null) {
        cancelAnimationFrame(rafId);
      }

      rafId = requestAnimationFrame(() => {
        setScreenSize({
          width: window.innerWidth,
          height: window.innerHeight,
        });
      });
    };

    window.addEventListener('resize', handleResize, { passive: true });
    return () => {
      window.removeEventListener('resize', handleResize);
      if (rafId !== null) {
        cancelAnimationFrame(rafId);
      }
    };
  }, []);

  return screenSize;
}

/**
 * Hook to detect mobile viewport
 */
export function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(() => {
    if (typeof window === 'undefined') return false;
    return window.innerWidth <= responsive.mobileMax;
  });

  useEffect(() => {
    if (typeof window === 'undefined') return;

    const handleResize = () => {
      setIsMobile(window.innerWidth <= responsive.mobileMax);
    };

    window.addEventListener('resize', handleResize, { passive: true });
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  return isMobile;
}

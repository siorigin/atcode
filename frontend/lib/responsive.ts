// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Central responsive design tokens used across the UI.
 *
 * Keep this file dependency-free so it can be imported anywhere.
 */
export const responsive = {
  // Breakpoints (px)
  mobileMax: 768,
  tabletMax: 1024,
  desktopMin: 1025,

  // Layout tokens
  pagePadding: {
    mobile: '16px',
    tablet: '24px',
    desktop: '32px',
  },
  cardGridGap: {
    mobile: '12px',
    tablet: '16px',
    desktop: '20px',
  },

  // Cards grid sizing
  gridMinWidth: 300,
} as const;

export type ResponsiveState = {
  isMobile: boolean;
  isTablet: boolean;
  isDesktop: boolean;
  width: number;
  height: number;
  pagePadding: string;
  gridGap: string;
  gridColumns: string;
  maxWidth: string;
};


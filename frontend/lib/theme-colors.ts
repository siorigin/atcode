// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Unified Theme Colors Utility
 *
 * This module provides consistent theme colors across all components.
 * All colors should be sourced from here instead of being hardcoded in components.
 *
 * Design System Notes:
 * - Accent colors meet WCAG AA contrast requirements (4.5:1 minimum)
 * - Status colors are consistent across all themes
 * - Uses CSS custom property references where appropriate
 */

import { Theme } from './theme-context';

export interface ThemeColors {
  // Background colors
  bg: string;
  bgSecondary: string;
  bgTertiary: string;
  bgHover: string;
  bgOverlay: string;

  // Card backgrounds
  card: string;
  cardHover: string;

  // Text colors
  text: string;
  textSecondary: string;
  textMuted: string;
  textDimmed: string;

  // Border colors
  border: string;
  borderLight: string;
  borderHover: string;

  // Accent colors
  accent: string;
  accentHover: string;
  accentBg: string;
  accentBorder: string;

  // Status colors (consistent across themes)
  success: string;
  successBg: string;
  successBorder: string;
  warning: string;
  warningBg: string;
  warningBorder: string;
  error: string;
  errorBg: string;
  errorBorder: string;
  info: string;
  infoBg: string;
  infoBorder: string;

  // Code block colors
  codeBg: string;

  // Gradient backgrounds
  gradientPrimary: string;
  gradientHover: string;

  // Shadow colors
  shadowColor: string;

  // Input colors
  inputBg: string;
  inputBorder: string;
  inputText: string;
  inputPlaceholder: string;

  // Button colors
  buttonPrimaryBg: string;
  buttonPrimaryHover: string;
  buttonSecondaryBg: string;
  buttonSecondaryHover: string;

  // Scrollbar colors
  scrollbarTrack: string;
  scrollbarThumb: string;
  scrollbarThumbHover: string;
}

const darkColors: ThemeColors = {
  // Background colors - refined dark palette
  bg: '#0c1118',
  bgSecondary: '#101722',
  bgTertiary: '#151c22',
  bgHover: '#1c2733',
  bgOverlay: 'rgba(10, 14, 20, 0.85)',

  // Card backgrounds - slightly elevated
  card: '#111827',
  cardHover: '#162033',

  // Text colors - improved hierarchy
  text: '#e6edf3',
  textSecondary: '#adbac7',
  textMuted: '#768390',
  textDimmed: '#545d68',

  // Border colors
  border: '#243041',
  borderLight: '#1b2736',
  borderHover: 'rgba(88, 166, 255, 0.45)',

  // Accent colors - IMPROVED CONTRAST (was #4493f8, now #58a6ff)
  accent: '#58a6ff',
  accentHover: '#79b8ff',
  accentBg: 'rgba(88, 166, 255, 0.15)',
  accentBorder: 'rgba(88, 166, 255, 0.3)',

  // Status colors - consistent and accessible
  success: '#3fb950',
  successBg: 'rgba(63, 185, 80, 0.15)',
  successBorder: 'rgba(63, 185, 80, 0.3)',
  warning: '#d29922',
  warningBg: 'rgba(210, 153, 34, 0.15)',
  warningBorder: 'rgba(210, 153, 34, 0.3)',
  error: '#f85149',
  errorBg: 'rgba(248, 81, 73, 0.15)',
  errorBorder: 'rgba(248, 81, 73, 0.3)',
  info: '#58a6ff',
  infoBg: 'rgba(88, 166, 255, 0.15)',
  infoBorder: 'rgba(88, 166, 255, 0.3)',

  // Code block colors
  codeBg: '#0d1117',

  // Gradient backgrounds - subtle and modern
  gradientPrimary: 'linear-gradient(135deg, #111827 0%, #151c22 100%)',
  gradientHover: 'linear-gradient(135deg, #162033 0%, #111827 100%)',

  // Shadow colors
  shadowColor: 'rgba(0, 0, 0, 0.28)',

  // Input colors
  inputBg: '#161b22',
  inputBorder: '#30363d',
  inputText: '#e6edf3',
  inputPlaceholder: '#545d68',

  // Button colors - refined blue
  buttonPrimaryBg: '#238636',
  buttonPrimaryHover: '#2ea043',
  buttonSecondaryBg: '#21262d',
  buttonSecondaryHover: '#30363d',

  // Scrollbar colors
  scrollbarTrack: '#161b22',
  scrollbarThumb: 'rgba(255, 255, 255, 0.15)',
  scrollbarThumbHover: '#58a6ff',
};

const lightColors: ThemeColors = {
  // Background colors - clean and crisp
  bg: '#f6f8fa',
  bgSecondary: '#ffffff',
  bgTertiary: '#f0f3f6',
  bgHover: '#eaeef2',
  bgOverlay: 'rgba(255, 255, 255, 0.85)',

  // Card backgrounds
  card: '#ffffff',
  cardHover: '#f6f8fa',

  // Text colors - strong hierarchy
  text: '#1f2328',
  textSecondary: '#24292f',
  textMuted: '#57606a',
  textDimmed: '#8c959f',

  // Border colors
  border: '#d0d7de',
  borderLight: '#e8ecf0',
  borderHover: 'rgba(9, 105, 218, 0.5)',

  // Accent colors
  accent: '#0969da',
  accentHover: '#0550ae',
  accentBg: 'rgba(9, 105, 218, 0.08)',
  accentBorder: 'rgba(9, 105, 218, 0.2)',

  // Status colors - consistent and accessible
  success: '#1a7f37',
  successBg: 'rgba(26, 127, 55, 0.08)',
  successBorder: 'rgba(26, 127, 55, 0.2)',
  warning: '#9a6700',
  warningBg: 'rgba(154, 103, 0, 0.08)',
  warningBorder: 'rgba(154, 103, 0, 0.2)',
  error: '#cf222e',
  errorBg: 'rgba(207, 34, 46, 0.08)',
  errorBorder: 'rgba(207, 34, 46, 0.2)',
  info: '#0969da',
  infoBg: 'rgba(9, 105, 218, 0.08)',
  infoBorder: 'rgba(9, 105, 218, 0.2)',

  // Code block colors
  codeBg: '#f6f8fa',

  // Gradient backgrounds
  gradientPrimary: 'linear-gradient(135deg, #ffffff 0%, #f6f8fa 100%)',
  gradientHover: 'linear-gradient(135deg, #f6f8fa 0%, #eaeef2 100%)',

  // Shadow colors
  shadowColor: 'rgba(31, 35, 40, 0.12)',

  // Input colors
  inputBg: '#ffffff',
  inputBorder: '#d0d7de',
  inputText: '#1f2328',
  inputPlaceholder: '#8c959f',

  // Button colors - GitHub-style green primary
  buttonPrimaryBg: '#1f883d',
  buttonPrimaryHover: '#1a7f37',
  buttonSecondaryBg: '#f6f8fa',
  buttonSecondaryHover: '#eaeef2',

  // Scrollbar colors
  scrollbarTrack: '#f6f8fa',
  scrollbarThumb: 'rgba(31, 35, 40, 0.15)',
  scrollbarThumbHover: '#0969da',
};

const beigeColors: ThemeColors = {
  // Background colors - warm and easy on eyes
  bg: '#f5f0e6',
  bgSecondary: '#faf6ee',
  bgTertiary: '#ede8dc',
  bgHover: '#e5dfd3',
  bgOverlay: 'rgba(245, 240, 230, 0.85)',

  // Card backgrounds
  card: '#faf6ee',
  cardHover: '#f0ebe0',

  // Text colors - warm browns with good contrast
  text: '#3d3428',
  textSecondary: '#4a4035',
  textMuted: '#6b5f4e',
  textDimmed: '#8a7d6a',

  // Border colors
  border: '#d4cbb8',
  borderLight: '#e5dfd3',
  borderHover: 'rgba(139, 105, 20, 0.5)',

  // Accent colors - warm gold
  accent: '#8b6914',
  accentHover: '#a67c00',
  accentBg: 'rgba(139, 105, 20, 0.1)',
  accentBorder: 'rgba(139, 105, 20, 0.2)',

  // Status colors - earthy tones
  success: '#2d5016',
  successBg: 'rgba(45, 80, 22, 0.1)',
  successBorder: 'rgba(45, 80, 22, 0.2)',
  warning: '#8b4513',
  warningBg: 'rgba(139, 69, 19, 0.1)',
  warningBorder: 'rgba(139, 69, 19, 0.2)',
  error: '#9c4221',
  errorBg: 'rgba(156, 66, 33, 0.1)',
  errorBorder: 'rgba(156, 66, 33, 0.2)',
  info: '#1e6b52',
  infoBg: 'rgba(30, 107, 82, 0.1)',
  infoBorder: 'rgba(30, 107, 82, 0.2)',

  // Code block colors
  codeBg: '#ede8dc',

  // Gradient backgrounds
  gradientPrimary: 'linear-gradient(135deg, #faf6ee 0%, #f0ebe0 100%)',
  gradientHover: 'linear-gradient(135deg, #f0ebe0 0%, #e5dfd3 100%)',

  // Shadow colors
  shadowColor: 'rgba(61, 52, 40, 0.1)',

  // Input colors
  inputBg: '#faf6ee',
  inputBorder: '#d4cbb8',
  inputText: '#3d3428',
  inputPlaceholder: '#8a7d6a',

  // Button colors - warm gold primary
  buttonPrimaryBg: '#8b6914',
  buttonPrimaryHover: '#a67c00',
  buttonSecondaryBg: '#ede8dc',
  buttonSecondaryHover: '#e5dfd3',

  // Scrollbar colors
  scrollbarTrack: '#f5f0e6',
  scrollbarThumb: 'rgba(61, 52, 40, 0.2)',
  scrollbarThumbHover: '#8b6914',
};

/**
 * Get theme colors for the specified theme
 */
export function getThemeColors(theme: Theme): ThemeColors {
  switch (theme) {
    case 'light':
      return lightColors;
    case 'beige':
      return beigeColors;
    case 'dark':
    default:
      return darkColors;
  }
}

/**
 * React hook to get theme colors
 * Use this in components that already have access to the theme from useTheme()
 */
export function useThemeColors(theme: Theme): ThemeColors {
  return getThemeColors(theme);
}

// Export individual theme color objects for direct access if needed
export { darkColors, lightColors, beigeColors };

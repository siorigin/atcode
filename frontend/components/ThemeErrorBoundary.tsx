'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { ReactNode } from 'react';
import { useTheme } from '@/lib/theme-context';
import { ErrorBoundary } from '@/components/ErrorBoundary';

/**
 * Client-side wrapper that catches render errors triggered by theme switches.
 * Uses `theme` as the resetKey so the error boundary auto-clears when the
 * user switches themes again.
 */
export function ThemeErrorBoundary({ children }: { children: ReactNode }) {
  const { theme } = useTheme();
  return (
    <ErrorBoundary resetKey={theme}>
      {children}
    </ErrorBoundary>
  );
}

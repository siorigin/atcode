'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import { useParams } from 'next/navigation';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';
import { AppHeader } from '@/components/AppHeader';

export default function RepoLayout({ children }: { children: React.ReactNode }) {
  const params = useParams();
  const repoName = params.repo as string;
  const { theme } = useTheme();
  const colors = getThemeColors(theme);

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100vh',
      width: '100%',
      background: colors.bg,
      overflow: 'hidden',
    }}>
      <AppHeader
        backHref="/repos"
        backLabel="Repos"
        title={decodeURIComponent(repoName)}
      />
      <div style={{
        flex: 1,
        overflow: 'hidden',
        minHeight: 0,
      }}>
        {children}
      </div>
    </div>
  );
}

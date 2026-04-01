'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import { usePathname } from 'next/navigation';
import Link from 'next/link';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';

const NAV_ITEMS = [
  { key: 'repos', label: 'Repos', href: '/repos', icon: RepoIcon },
  { key: 'papers', label: 'Papers', href: '/repos/papers', icon: PapersIcon },
] as const;

function RepoIcon({ size = 18, color }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color || 'currentColor'} strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M15 22v-4a4.8 4.8 0 0 0-1-3.5c3 0 6-2 6-5.5.08-1.25-.27-2.48-1-3.5.28-1.15.28-2.35 0-3.5 0 0-1 0-3 1.5-2.64-.5-5.36-.5-8 0C6 2 5 2 5 2c-.3 1.15-.3 2.35 0 3.5A5.403 5.403 0 0 0 4 9c0 3.5 3 5.5 6 5.5-.39.49-.68 1.05-.85 1.65S8.93 17.38 9 18v4" />
      <path d="M9 18c-4.51 2-5-2-7-2" />
    </svg>
  );
}

function PapersIcon({ size = 18, color }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color || 'currentColor'} strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="16" y1="13" x2="8" y2="13" />
      <line x1="16" y1="17" x2="8" y2="17" />
      <polyline points="10 9 9 9 8 9" />
    </svg>
  );
}

export default function ReposLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { theme } = useTheme();
  const colors = getThemeColors(theme);

  // Determine active tab: /repos/papers → papers, everything else at /repos level → repos
  // But /repos/[repo]/... should NOT show the sidebar (it has its own layout)
  const isRepoDetail = /^\/repos\/[^/]+/.test(pathname) && !pathname.startsWith('/repos/papers');

  if (isRepoDetail) {
    // Repo detail pages have their own layout with PageShellSidebar
    return <>{children}</>;
  }

  const activeKey = pathname.startsWith('/repos/papers') ? 'papers' : 'repos';

  return (
    <div style={{
      display: 'flex',
      height: '100vh',
      width: '100%',
      background: colors.bg,
      overflow: 'hidden',
    }}>
      {/* Side navigation */}
      <nav style={{
        width: 52,
        minWidth: 52,
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        paddingTop: 12,
        gap: 4,
        borderRight: `1px solid ${colors.border}`,
        background: colors.bgSecondary,
        flexShrink: 0,
      }}>
        {NAV_ITEMS.map(({ key, label, href, icon: Icon }) => {
          const isActive = activeKey === key;
          return (
            <Link
              key={key}
              href={href}
              title={label}
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                width: 40,
                height: 40,
                borderRadius: 8,
                textDecoration: 'none',
                color: isActive ? colors.accent : colors.textMuted,
                background: isActive ? colors.accentBg : 'transparent',
                transition: 'all 0.15s ease',
                position: 'relative',
              }}
            >
              <Icon size={18} color={isActive ? colors.accent : undefined} />
              <span style={{
                fontSize: 9,
                marginTop: 2,
                fontWeight: isActive ? 600 : 450,
                fontFamily: "'Inter', sans-serif",
                letterSpacing: '0.02em',
              }}>
                {label}
              </span>
            </Link>
          );
        })}
      </nav>

      {/* Main content */}
      <main style={{
        flex: 1,
        overflow: 'auto',
        minWidth: 0,
        minHeight: 0,
      }}>
        {children}
      </main>
    </div>
  );
}

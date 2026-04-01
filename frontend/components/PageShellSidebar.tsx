'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import Link from 'next/link';
import { usePageShell } from '@/lib/page-shell-context';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';

const SIDEBAR_WIDTH = 260;
const SIDEBAR_COLLAPSED_WIDTH = 48;

const TABS = [
  { key: 'overview' as const, label: 'Overview', icon: OverviewIcon },
  { key: 'research' as const, label: 'Research', icon: ResearchIcon },
] as const;

function OverviewIcon({ size = 18, color }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color || 'currentColor'} strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
      <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
    </svg>
  );
}

function ResearchIcon({ size = 18, color }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color || 'currentColor'} strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" opacity="0" />
      <circle cx="11" cy="11" r="8" />
      <path d="m21 21-4.3-4.3" />
      <path d="M11 8v6" />
      <path d="M8 11h6" />
    </svg>
  );
}

function CollapseIcon({ collapsed }: { collapsed: boolean }) {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      {collapsed ? (
        <path d="M9 18l6-6-6-6" />
      ) : (
        <path d="M15 18l-6-6 6-6" />
      )}
    </svg>
  );
}

export function PageShellSidebar() {
  const { repoName, activeTab, sidebarContent, sidebarCollapsed, setSidebarCollapsed } = usePageShell();
  const { theme } = useTheme();
  const colors = getThemeColors(theme);

  const width = sidebarCollapsed ? SIDEBAR_COLLAPSED_WIDTH : SIDEBAR_WIDTH;

  return (
    <aside
      style={{
        width,
        minWidth: width,
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        borderRight: `1px solid ${colors.border}`,
        background: colors.bgSecondary,
        transition: 'width 0.2s cubic-bezier(0.4, 0, 0.2, 1), min-width 0.2s cubic-bezier(0.4, 0, 0.2, 1)',
        overflow: 'hidden',
        flexShrink: 0,
      }}
    >
      {/* Header with repo name & collapse toggle */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: sidebarCollapsed ? 'center' : 'space-between',
        padding: sidebarCollapsed ? '10px 0' : '10px 14px',
        borderBottom: `1px solid ${colors.border}`,
        flexShrink: 0,
        minHeight: '42px',
      }}>
        {!sidebarCollapsed && (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            overflow: 'hidden',
            minWidth: 0,
          }}>
            {/* Git repo icon */}
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={colors.textMuted} strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
              <circle cx="12" cy="12" r="4" />
              <line x1="1.05" y1="12" x2="7" y2="12" />
              <line x1="17.01" y1="12" x2="22.96" y2="12" />
            </svg>
            <span style={{
              fontSize: '13px',
              fontWeight: 600,
              color: colors.text,
              fontFamily: "'Inter', sans-serif",
              letterSpacing: '-0.01em',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}>
              {decodeURIComponent(repoName)}
            </span>
          </div>
        )}
        <button
          onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
          title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: '26px',
            height: '26px',
            background: 'transparent',
            border: 'none',
            borderRadius: '6px',
            cursor: 'pointer',
            color: colors.textMuted,
            flexShrink: 0,
            transition: 'all 0.15s ease',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = colors.bgHover;
            e.currentTarget.style.color = colors.text;
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = 'transparent';
            e.currentTarget.style.color = colors.textMuted;
          }}
        >
          <CollapseIcon collapsed={sidebarCollapsed} />
        </button>
      </div>

      {/* Tab navigation */}
      <nav style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '1px',
        padding: sidebarCollapsed ? '8px 8px' : '8px 10px',
        flexShrink: 0,
      }}>
        {TABS.map(({ key, label, icon: Icon }) => {
          const isActive = activeTab === key;
          const href = key === 'overview'
            ? `/repos/${encodeURIComponent(repoName)}`
            : `/repos/${encodeURIComponent(repoName)}/${key}`;
          return (
            <Link
              key={key}
              href={href}
              title={sidebarCollapsed ? label : undefined}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '10px',
                padding: sidebarCollapsed ? '8px 0' : '7px 10px',
                justifyContent: sidebarCollapsed ? 'center' : 'flex-start',
                borderRadius: '8px',
                textDecoration: 'none',
                fontSize: '13px',
                fontWeight: isActive ? 600 : 450,
                fontFamily: "'Inter', sans-serif",
                letterSpacing: '-0.005em',
                color: isActive ? colors.accent : colors.textMuted,
                background: isActive ? colors.accentBg : 'transparent',
                borderLeft: isActive ? `2px solid ${colors.accent}` : '2px solid transparent',
                transition: 'all 0.15s ease',
                position: 'relative',
              }}
              onMouseEnter={(e) => {
                if (!isActive) {
                  e.currentTarget.style.background = colors.bgHover;
                  e.currentTarget.style.color = colors.textSecondary;
                }
              }}
              onMouseLeave={(e) => {
                if (!isActive) {
                  e.currentTarget.style.background = 'transparent';
                  e.currentTarget.style.color = colors.textMuted;
                }
              }}
            >
              <Icon size={17} color={isActive ? colors.accent : undefined} />
              {!sidebarCollapsed && <span>{label}</span>}
            </Link>
          );
        })}
      </nav>

      {/* Pluggable content area */}
      {!sidebarCollapsed && sidebarContent && (
        <div style={{
          flex: 1,
          overflow: 'auto',
          minHeight: 0,
          borderTop: `1px solid ${colors.border}`,
          marginTop: '2px',
        }}>
          {sidebarContent}
        </div>
      )}
    </aside>
  );
}

'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import { useTheme, type Theme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';

interface AppHeaderProps {
  /** Left side: back navigation target URL */
  backHref?: string;
  backLabel?: string;
  /** Title text */
  title?: string;
  /** Subtitle / breadcrumb badge */
  subtitle?: string;
  /** Extra actions to render on the right (before theme toggle) */
  actions?: React.ReactNode;
}

const THEME_MENU_OPTIONS: { value: Theme; label: string; icon: React.ReactNode }[] = [
  { value: 'dark', label: 'Dark', icon: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
    </svg>
  ) },
  { value: 'light', label: 'Light', icon: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="5"/>
      <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>
    </svg>
  ) },
  { value: 'beige', label: 'Eye Comfort', icon: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/>
      <circle cx="12" cy="12" r="3"/>
    </svg>
  ) },
];

function ThemeButtonIcon({ theme }: { theme: Theme }) {
  if (theme === 'dark') return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
    </svg>
  );
  if (theme === 'beige') return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/>
      <circle cx="12" cy="12" r="3"/>
    </svg>
  );
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="5"/>
      <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>
    </svg>
  );
}

export function AppHeader({ backHref, backLabel = 'Back', title = 'AtCode', subtitle, actions }: AppHeaderProps) {
  const { theme, setTheme } = useTheme();
  const colors = getThemeColors(theme);
  const [showThemeMenu, setShowThemeMenu] = React.useState(false);
  const menuRef = React.useRef<HTMLDivElement>(null);

  // Close on outside click
  React.useEffect(() => {
    if (!showThemeMenu) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setShowThemeMenu(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [showThemeMenu]);

  const themeLabel = theme === 'dark' ? 'Dark' : theme === 'beige' ? 'Comfort' : 'Light';

  return (
    <header style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '8px 20px',
      borderBottom: `1px solid ${colors.border}`,
      position: 'sticky',
      top: 0,
      background: colors.bgOverlay,
      backdropFilter: 'blur(12px)',
      zIndex: 100,
      flexShrink: 0,
      minHeight: '42px',
    }}>
      {/* Left: Back + Title */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 0 }}>
        {backHref && (
          <a
            href={backHref}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              padding: '5px 8px',
              borderRadius: '6px',
              color: colors.textMuted,
              textDecoration: 'none',
              fontSize: '12px',
              fontWeight: 500,
              fontFamily: "'Inter', sans-serif",
              transition: 'all 0.15s ease',
            }}
            onMouseEnter={(e) => { e.currentTarget.style.background = colors.bgHover; e.currentTarget.style.color = colors.text; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = colors.textMuted; }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M19 12H5M12 19l-7-7 7-7"/>
            </svg>
            <span>{backLabel}</span>
          </a>
        )}
        {/* Repo icon */}
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={colors.accent} strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, opacity: 0.8 }}>
          <path d="M15 22v-4a4.8 4.8 0 0 0-1-3.5c3 0 6-2 6-5.5.08-1.25-.27-2.48-1-3.5.28-1.15.28-2.35 0-3.5 0 0-1 0-3 1.5-2.64-.5-5.36-.5-8 0C6 2 5 2 5 2c-.3 1.15-.3 2.35 0 3.5A5.403 5.403 0 0 0 4 9c0 3.5 3 5.5 6 5.5-.39.49-.68 1.05-.85 1.65S8.93 17.38 9 18v4" />
          <path d="M9 18c-4.51 2-5-2-7-2" />
        </svg>
        <span style={{
          fontSize: '15px',
          fontWeight: 600,
          color: colors.text,
          fontFamily: "'Inter', sans-serif",
          letterSpacing: '-0.02em',
        }}>
          {title}
        </span>
        {subtitle && (
          <span style={{
            fontSize: '11px',
            color: colors.textMuted,
            background: colors.card,
            padding: '2px 8px',
            borderRadius: '10px',
            border: `1px solid ${colors.borderLight}`,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            maxWidth: '300px',
            fontFamily: "'Inter', sans-serif",
            fontWeight: 500,
          }}>
            {subtitle}
          </span>
        )}
      </div>

      {/* Right: Actions + Theme */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexShrink: 0 }}>
        {actions}
        {/* Theme selector — matches repos page style */}
        <div ref={menuRef} style={{ position: 'relative' }}>
          <button
            onClick={() => setShowThemeMenu(!showThemeMenu)}
            style={{
              padding: '8px 14px',
              background: 'transparent',
              border: `1px solid ${colors.borderLight}`,
              borderRadius: '10px',
              fontSize: '13px',
              cursor: 'pointer',
              transition: 'all 150ms ease-out',
              fontWeight: '500',
              color: colors.textSecondary,
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
            }}
            onMouseEnter={(e) => { e.currentTarget.style.background = colors.bgHover; e.currentTarget.style.borderColor = colors.border; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.borderColor = colors.borderLight; }}
          >
            <ThemeButtonIcon theme={theme} />
            <span>{themeLabel}</span>
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ opacity: 0.5 }}>
              <path d="m6 9 6 6 6-6"/>
            </svg>
          </button>
          {showThemeMenu && (
            <div style={{
              position: 'absolute',
              top: '100%',
              right: 0,
              marginTop: '6px',
              background: colors.card,
              border: `1px solid ${colors.borderLight}`,
              borderRadius: '12px',
              boxShadow: `0 8px 24px ${colors.shadowColor}`,
              minWidth: '160px',
              zIndex: 200,
              overflow: 'hidden',
              animation: 'fadeInDown 150ms ease-out',
              padding: '4px',
            }}>
              {THEME_MENU_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  onClick={() => { setTheme(option.value); setShowThemeMenu(false); }}
                  style={{
                    width: '100%',
                    padding: '10px 12px',
                    background: theme === option.value ? colors.accentBg : 'transparent',
                    border: 'none',
                    borderRadius: '8px',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '10px',
                    fontSize: '13px',
                    color: theme === option.value ? colors.accent : colors.textSecondary,
                    fontWeight: theme === option.value ? '500' : '400',
                    transition: 'all 150ms ease-out',
                    marginBottom: option.value !== 'beige' ? '2px' : '0',
                  }}
                  onMouseEnter={(e) => {
                    if (theme !== option.value) {
                      e.currentTarget.style.background = colors.bgHover;
                      e.currentTarget.style.color = colors.text;
                    }
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = theme === option.value ? colors.accentBg : 'transparent';
                    e.currentTarget.style.color = theme === option.value ? colors.accent : colors.textSecondary;
                  }}
                >
                  {option.icon}
                  <span>{option.label}</span>
                  {theme === option.value && (
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ marginLeft: 'auto' }}>
                      <polyline points="20 6 9 17 4 12"/>
                    </svg>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </header>
  );
}

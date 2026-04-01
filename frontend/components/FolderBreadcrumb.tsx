'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { getThemeColors } from '@/lib/theme-colors';

interface BreadcrumbItem {
  id: string | null;
  name: string;
}

interface FolderBreadcrumbProps {
  path: BreadcrumbItem[];
  onNavigate: (folderId: string | null) => void;
  theme?: 'dark' | 'light' | 'beige';
}

export function FolderBreadcrumb({ path, onNavigate, theme = 'dark' }: FolderBreadcrumbProps) {
  const colors = getThemeColors(theme);

  if (path.length === 0) {
    return null;
  }

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: '8px',
      padding: '12px 0',
      fontSize: '14px',
      color: colors.textMuted,
    }}>
      {/* Home icon */}
      <button
        onClick={() => onNavigate(null)}
        style={{
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          padding: '4px 8px',
          borderRadius: '6px',
          color: colors.textMuted,
          display: 'flex',
          alignItems: 'center',
          transition: 'all 0.2s',
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
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
          <polyline points="9 22 9 12 15 12 15 22"/>
        </svg>
      </button>

      {/* Breadcrumb items */}
      {path.map((item, index) => (
        <div key={item.id || 'root'} style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          {/* Separator */}
          <span style={{ color: colors.textDimmed, opacity: 0.5 }}>/</span>

          {/* Folder name */}
          <button
            onClick={() => onNavigate(item.id)}
            style={{
              background: 'transparent',
              border: 'none',
              cursor: index === path.length - 1 ? 'default' : 'pointer',
              padding: '4px 8px',
              borderRadius: '6px',
              color: index === path.length - 1 ? colors.text : colors.textMuted,
              fontWeight: index === path.length - 1 ? '500' : '400',
              transition: 'all 0.2s',
            }}
            onMouseEnter={(e) => {
              if (index !== path.length - 1) {
                e.currentTarget.style.background = colors.bgHover;
                e.currentTarget.style.color = colors.text;
              }
            }}
            onMouseLeave={(e) => {
              if (index !== path.length - 1) {
                e.currentTarget.style.background = 'transparent';
                e.currentTarget.style.color = colors.textMuted;
              }
            }}
            disabled={index === path.length - 1}
          >
            {item.name}
          </button>
        </div>
      ))}
    </div>
  );
}

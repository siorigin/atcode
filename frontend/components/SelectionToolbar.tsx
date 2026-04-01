'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { getThemeColors } from '@/lib/theme-colors';

interface SelectionToolbarProps {
  selectedCount: number;
  onMoveToFolder: () => void;
  onCreateFolderFromSelection: () => void;
  onDelete: () => void;
  onClearSelection: () => void;
  theme?: 'dark' | 'light' | 'beige';
}

export function SelectionToolbar({
  selectedCount,
  onMoveToFolder,
  onCreateFolderFromSelection,
  onDelete,
  onClearSelection,
  theme = 'dark',
}: SelectionToolbarProps) {
  const colors = getThemeColors(theme);

  if (selectedCount === 0) return null;

  return (
    <div
      style={{
        position: 'fixed',
        bottom: '24px',
        left: '50%',
        transform: 'translateX(-50%)',
        background: colors.card,
        border: `1px solid ${colors.borderLight}`,
        borderRadius: '16px',
        boxShadow: `0 8px 32px ${colors.shadowColor}`,
        padding: '12px 20px',
        display: 'flex',
        alignItems: 'center',
        gap: '16px',
        zIndex: 1000,
        backdropFilter: 'blur(12px)',
        animation: 'slideUp 0.3s ease-out',
      }}
    >
      {/* Selection count */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          paddingRight: '16px',
          borderRight: `1px solid ${colors.border}`,
        }}
      >
        <span
          style={{
            background: colors.accent,
            color: '#ffffff',
            fontSize: '12px',
            fontWeight: '600',
            padding: '2px 8px',
            borderRadius: '10px',
            minWidth: '24px',
            textAlign: 'center',
          }}
        >
          {selectedCount}
        </span>
        <span
          style={{
            color: colors.textMuted,
            fontSize: '13px',
            fontWeight: '500',
          }}
        >
          {selectedCount === 1 ? 'item selected' : 'items selected'}
        </span>
      </div>

      {/* Action buttons */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        {/* Move to folder */}
        <button
          onClick={onMoveToFolder}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '8px 14px',
            background: colors.accentBg,
            border: `1px solid ${colors.accentBorder}`,
            borderRadius: '8px',
            color: colors.accent,
            fontSize: '13px',
            fontWeight: '500',
            cursor: 'pointer',
            transition: 'all 0.2s',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = colors.accent;
            e.currentTarget.style.color = '#ffffff';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = colors.accentBg;
            e.currentTarget.style.color = colors.accent;
          }}
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
          </svg>
          <span>Move to folder</span>
        </button>

        {/* Create folder from selection */}
        <button
          onClick={onCreateFolderFromSelection}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '8px 14px',
            background: 'transparent',
            border: `1px solid ${colors.border}`,
            borderRadius: '8px',
            color: colors.text,
            fontSize: '13px',
            fontWeight: '500',
            cursor: 'pointer',
            transition: 'all 0.2s',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = colors.bgHover;
            e.currentTarget.style.borderColor = colors.borderHover;
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = 'transparent';
            e.currentTarget.style.borderColor = colors.border;
          }}
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
            <line x1="12" y1="11" x2="12" y2="17" />
            <line x1="9" y1="14" x2="15" y2="14" />
          </svg>
          <span>New folder</span>
        </button>

        {/* Delete */}
        <button
          onClick={onDelete}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '8px 14px',
            background: 'transparent',
            border: `1px solid ${colors.error}40`,
            borderRadius: '8px',
            color: colors.error,
            fontSize: '13px',
            fontWeight: '500',
            cursor: 'pointer',
            transition: 'all 0.2s',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = colors.error;
            e.currentTarget.style.color = '#ffffff';
            e.currentTarget.style.borderColor = colors.error;
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = 'transparent';
            e.currentTarget.style.color = colors.error;
            e.currentTarget.style.borderColor = `${colors.error}40`;
          }}
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <polyline points="3 6 5 6 21 6" />
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
          </svg>
          <span>Delete</span>
        </button>
      </div>

      {/* Clear selection */}
      <button
        onClick={onClearSelection}
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: '32px',
          height: '32px',
          background: 'transparent',
          border: 'none',
          borderRadius: '8px',
          color: colors.textMuted,
          cursor: 'pointer',
          transition: 'all 0.2s',
          marginLeft: '8px',
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = colors.bgHover;
          e.currentTarget.style.color = colors.text;
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = 'transparent';
          e.currentTarget.style.color = colors.textMuted;
        }}
        title="Clear selection"
      >
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <line x1="18" y1="6" x2="6" y2="18" />
          <line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>

      <style jsx>{`
        @keyframes slideUp {
          from {
            opacity: 0;
            transform: translateX(-50%) translateY(20px);
          }
          to {
            opacity: 1;
            transform: translateX(-50%) translateY(0);
          }
        }
      `}</style>
    </div>
  );
}

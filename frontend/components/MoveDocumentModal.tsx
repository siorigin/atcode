'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { useState } from 'react';
import { getThemeColors } from '@/lib/theme-colors';
import { Folder } from '@/types/folders';

interface MoveDocumentModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: (folderId: string | null) => void;
  folders: Folder[];
  currentFolderId?: string | null;
  documentName: string;
  theme?: 'dark' | 'light' | 'beige';
}

export function MoveDocumentModal({
  isOpen,
  onClose,
  onConfirm,
  folders,
  currentFolderId,
  documentName,
  theme = 'dark',
}: MoveDocumentModalProps) {
  const [selectedFolderId, setSelectedFolderId] = useState<string | null>(currentFolderId || null);
  const colors = getThemeColors(theme);

  if (!isOpen) return null;

  const handleSubmit = () => {
    onConfirm(selectedFolderId);
    onClose();
  };

  // Build folder tree structure
  const buildFolderTree = (parentId: string | null = null): Folder[] => {
    return folders
      .filter(f => f.parentId === parentId)
      .sort((a, b) => a.name.localeCompare(b.name));
  };

  const renderFolderItem = (folder: Folder, level: number = 0) => {
    const isSelected = selectedFolderId === folder.id;
    const children = buildFolderTree(folder.id);

    return (
      <div key={folder.id}>
        <button
          type="button"
          onClick={() => setSelectedFolderId(folder.id)}
          style={{
            width: '100%',
            padding: '10px 12px',
            paddingLeft: `${12 + level * 20}px`,
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            background: isSelected ? colors.accentBg : 'transparent',
            border: 'none',
            borderRadius: '6px',
            color: isSelected ? colors.accent : colors.text,
            cursor: 'pointer',
            fontSize: '14px',
            textAlign: 'left',
            transition: 'all 0.2s',
          }}
          onMouseEnter={(e) => {
            if (!isSelected) {
              e.currentTarget.style.background = colors.bgHover;
            }
          }}
          onMouseLeave={(e) => {
            if (!isSelected) {
              e.currentTarget.style.background = 'transparent';
            }
          }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
          </svg>
          <span>{folder.name}</span>
        </button>
        {children.map(child => renderFolderItem(child, level + 1))}
      </div>
    );
  };

  const rootFolders = buildFolderTree(null);

  return (
    <div
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        background: 'rgba(0, 0, 0, 0.5)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: colors.card,
          borderRadius: '16px',
          padding: '24px',
          width: '90%',
          maxWidth: '520px',
          maxHeight: '80vh',
          display: 'flex',
          flexDirection: 'column',
          boxShadow: `0 20px 60px ${colors.shadowColor}`,
          border: `1px solid ${colors.borderLight}`,
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 style={{
          fontSize: '20px',
          fontWeight: '600',
          color: colors.text,
          marginBottom: '8px',
        }}>
          移动文档
        </h2>

        <p style={{
          fontSize: '13px',
          color: colors.textMuted,
          marginBottom: '16px',
        }}>
          将 "{documentName}" 移动到：
        </p>

        <div style={{
          flex: 1,
          overflowY: 'auto',
          marginBottom: '20px',
          border: `1px solid ${colors.border}`,
          borderRadius: '8px',
          padding: '8px',
          background: colors.bg,
        }}>
          {/* Root option */}
          <button
            type="button"
            onClick={() => setSelectedFolderId(null)}
            style={{
              width: '100%',
              padding: '10px 12px',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              background: selectedFolderId === null ? colors.accentBg : 'transparent',
              border: 'none',
              borderRadius: '6px',
              color: selectedFolderId === null ? colors.accent : colors.text,
              cursor: 'pointer',
              fontSize: '14px',
              textAlign: 'left',
              transition: 'all 0.2s',
              marginBottom: '4px',
            }}
            onMouseEnter={(e) => {
              if (selectedFolderId !== null) {
                e.currentTarget.style.background = colors.bgHover;
              }
            }}
            onMouseLeave={(e) => {
              if (selectedFolderId !== null) {
                e.currentTarget.style.background = 'transparent';
              }
            }}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
              <polyline points="9 22 9 12 15 12 15 22"/>
            </svg>
            <span>根目录</span>
          </button>

          {/* Folder tree */}
          {rootFolders.map(folder => renderFolderItem(folder, 0))}

          {folders.length === 0 && (
            <p style={{
              padding: '20px',
              textAlign: 'center',
              color: colors.textMuted,
              fontSize: '13px',
            }}>
              暂无文件夹
            </p>
          )}
        </div>

        <div style={{
          display: 'flex',
          gap: '12px',
          justifyContent: 'flex-end',
        }}>
          <button
            type="button"
            onClick={onClose}
            style={{
              padding: '10px 20px',
              fontSize: '14px',
              fontWeight: '500',
              border: `1px solid ${colors.border}`,
              borderRadius: '8px',
              background: 'transparent',
              color: colors.textMuted,
              cursor: 'pointer',
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
            取消
          </button>

          <button
            type="button"
            onClick={handleSubmit}
            style={{
              padding: '10px 20px',
              fontSize: '14px',
              fontWeight: '500',
              border: 'none',
              borderRadius: '8px',
              background: colors.accent,
              color: '#ffffff',
              cursor: 'pointer',
              transition: 'all 0.2s',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.opacity = '0.9';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.opacity = '1';
            }}
          >
            移动
          </button>
        </div>
      </div>
    </div>
  );
}

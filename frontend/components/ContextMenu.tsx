'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useRef, useState } from 'react';
import { getThemeColors } from '@/lib/theme-colors';
import { Folder } from '@/types/folders';

export interface ContextMenuItem {
  id: string;
  label: string;
  icon?: React.ReactNode;
  disabled?: boolean;
  danger?: boolean;
  submenu?: ContextMenuItem[];
  onClick?: () => void;
}

interface ContextMenuProps {
  isOpen: boolean;
  position: { x: number; y: number };
  items: ContextMenuItem[];
  folders?: Folder[];
  onClose: () => void;
  onMoveToFolder?: (folderId: string | null) => void;
  theme?: 'dark' | 'light' | 'beige';
}

export function ContextMenu({
  isOpen,
  position,
  items,
  folders = [],
  onClose,
  onMoveToFolder,
  theme = 'dark',
}: ContextMenuProps) {
  const colors = getThemeColors(theme);
  const menuRef = useRef<HTMLDivElement>(null);
  const [activeSubmenu, setActiveSubmenu] = useState<string | null>(null);
  const [adjustedPosition, setAdjustedPosition] = useState(position);
  const [focusedIndex, setFocusedIndex] = useState(-1);

  // Adjust position to ensure menu stays within viewport
  useEffect(() => {
    if (!isOpen || !menuRef.current) return;

    const menu = menuRef.current;
    const rect = menu.getBoundingClientRect();
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;

    let x = position.x;
    let y = position.y;

    // Adjust horizontal position
    if (x + rect.width > viewportWidth - 16) {
      x = viewportWidth - rect.width - 16;
    }
    if (x < 16) {
      x = 16;
    }

    // Adjust vertical position
    if (y + rect.height > viewportHeight - 16) {
      y = viewportHeight - rect.height - 16;
    }
    if (y < 16) {
      y = 16;
    }

    setAdjustedPosition({ x, y });
  }, [isOpen, position]);

  // Close on click outside
  useEffect(() => {
    if (!isOpen) return;

    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    };

    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleEscape);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [isOpen, onClose]);

  // Keyboard navigation
  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault();
          setFocusedIndex((prev) => {
            const next = prev + 1;
            return next >= items.length ? 0 : next;
          });
          break;
        case 'ArrowUp':
          e.preventDefault();
          setFocusedIndex((prev) => {
            const next = prev - 1;
            return next < 0 ? items.length - 1 : next;
          });
          break;
        case 'Enter':
          e.preventDefault();
          if (focusedIndex >= 0 && focusedIndex < items.length) {
            const item = items[focusedIndex];
            if (!item.disabled && item.onClick) {
              item.onClick();
              onClose();
            }
          }
          break;
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, items, focusedIndex, onClose]);

  if (!isOpen) return null;

  // Build folder tree for submenu
  const buildFolderTree = (parentId: string | null = null): Folder[] => {
    return folders
      .filter((f) => f.parentId === parentId)
      .sort((a, b) => a.name.localeCompare(b.name));
  };

  const renderFolderSubmenu = (parentId: string | null, level: number = 0): React.ReactNode => {
    const folderItems = buildFolderTree(parentId);

    return (
      <>
        {/* Root option at top level */}
        {level === 0 && (
          <button
            onClick={() => {
              onMoveToFolder?.(null);
              onClose();
            }}
            style={{
              width: '100%',
              padding: '8px 12px',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              background: 'transparent',
              border: 'none',
              borderRadius: '6px',
              color: colors.text,
              cursor: 'pointer',
              fontSize: '13px',
              textAlign: 'left',
              transition: 'all 0.15s',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = colors.bgHover;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'transparent';
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
              <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
              <polyline points="9 22 9 12 15 12 15 22" />
            </svg>
            <span>Root Directory</span>
          </button>
        )}

        {/* Folder items */}
        {folderItems.map((folder) => {
          const children = buildFolderTree(folder.id);
          const hasChildren = children.length > 0;

          return (
            <div key={folder.id}>
              <button
                onClick={() => {
                  onMoveToFolder?.(folder.id);
                  onClose();
                }}
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  paddingLeft: `${12 + level * 16}px`,
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  background: 'transparent',
                  border: 'none',
                  borderRadius: '6px',
                  color: colors.text,
                  cursor: 'pointer',
                  fontSize: '13px',
                  textAlign: 'left',
                  transition: 'all 0.15s',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = colors.bgHover;
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = 'transparent';
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
                <span>{folder.name}</span>
              </button>
              {hasChildren && renderFolderSubmenu(folder.id, level + 1)}
            </div>
          );
        })}
      </>
    );
  };

  return (
    <div
      ref={menuRef}
      style={{
        position: 'fixed',
        left: adjustedPosition.x,
        top: adjustedPosition.y,
        background: colors.card,
        border: `1px solid ${colors.borderLight}`,
        borderRadius: '12px',
        boxShadow: `0 12px 40px ${colors.shadowColor}`,
        minWidth: '180px',
        maxWidth: '280px',
        zIndex: 2000,
        padding: '6px',
        animation: 'contextMenuIn 0.15s ease-out',
      }}
      role="menu"
      aria-label="Context menu"
    >
      {items.map((item, index) => {
        const isFocused = focusedIndex === index;
        const hasSubmenu = item.id === 'move-to-folder' && folders.length > 0;

        if (hasSubmenu) {
          return (
            <div
              key={item.id}
              onMouseEnter={() => setActiveSubmenu(item.id)}
              onMouseLeave={() => setActiveSubmenu(null)}
              style={{ position: 'relative' }}
            >
              <button
                disabled={item.disabled}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '10px',
                  background: isFocused || activeSubmenu === item.id ? colors.bgHover : 'transparent',
                  border: 'none',
                  borderRadius: '8px',
                  color: item.disabled ? colors.textDimmed : item.danger ? colors.error : colors.text,
                  cursor: item.disabled ? 'not-allowed' : 'pointer',
                  fontSize: '13px',
                  fontWeight: '500',
                  textAlign: 'left',
                  transition: 'all 0.15s',
                  opacity: item.disabled ? 0.5 : 1,
                }}
                onMouseEnter={(e) => {
                  if (!item.disabled) {
                    e.currentTarget.style.background = colors.bgHover;
                  }
                }}
                onMouseLeave={(e) => {
                  if (!isFocused && activeSubmenu !== item.id) {
                    e.currentTarget.style.background = 'transparent';
                  }
                }}
              >
                {item.icon && <span style={{ opacity: 0.7 }}>{item.icon}</span>}
                <span style={{ flex: 1 }}>{item.label}</span>
                <svg
                  width="12"
                  height="12"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  style={{ opacity: 0.5 }}
                >
                  <polyline points="9 18 15 12 9 6" />
                </svg>
              </button>

              {/* Submenu */}
              {activeSubmenu === item.id && (
                <div
                  style={{
                    position: 'absolute',
                    left: '100%',
                    top: 0,
                    marginLeft: '4px',
                    background: colors.card,
                    border: `1px solid ${colors.borderLight}`,
                    borderRadius: '12px',
                    boxShadow: `0 12px 40px ${colors.shadowColor}`,
                    minWidth: '200px',
                    maxHeight: '300px',
                    overflowY: 'auto',
                    padding: '6px',
                    zIndex: 2001,
                  }}
                >
                  {renderFolderSubmenu(null)}
                  {folders.length === 0 && (
                    <div
                      style={{
                        padding: '12px',
                        textAlign: 'center',
                        color: colors.textMuted,
                        fontSize: '12px',
                      }}
                    >
                      No folders created
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        }

        return (
          <button
            key={item.id}
            onClick={() => {
              if (!item.disabled && item.onClick) {
                item.onClick();
                onClose();
              }
            }}
            disabled={item.disabled}
            role="menuitem"
            style={{
              width: '100%',
              padding: '10px 12px',
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
              background: isFocused ? colors.bgHover : 'transparent',
              border: 'none',
              borderRadius: '8px',
              color: item.disabled ? colors.textDimmed : item.danger ? colors.error : colors.text,
              cursor: item.disabled ? 'not-allowed' : 'pointer',
              fontSize: '13px',
              fontWeight: '500',
              textAlign: 'left',
              transition: 'all 0.15s',
              opacity: item.disabled ? 0.5 : 1,
            }}
            onMouseEnter={(e) => {
              setFocusedIndex(index);
              if (!item.disabled) {
                e.currentTarget.style.background = colors.bgHover;
              }
            }}
            onMouseLeave={(e) => {
              if (!isFocused) {
                e.currentTarget.style.background = 'transparent';
              }
            }}
          >
            {item.icon && <span style={{ opacity: 0.7 }}>{item.icon}</span>}
            <span>{item.label}</span>
          </button>
        );
      })}

      <style jsx>{`
        @keyframes contextMenuIn {
          from {
            opacity: 0;
            transform: scale(0.95);
          }
          to {
            opacity: 1;
            transform: scale(1);
          }
        }
      `}</style>
    </div>
  );
}

// Predefined icons for common actions
export const ContextMenuIcons = {
  folder: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
    </svg>
  ),
  folderPlus: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
      <line x1="12" y1="11" x2="12" y2="17"/>
      <line x1="9" y1="14" x2="15" y2="14"/>
    </svg>
  ),
  move: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 12h14"/>
      <path d="M12 5l7 7-7 7"/>
    </svg>
  ),
  trash: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6"/>
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
    </svg>
  ),
  edit: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
      <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
    </svg>
  ),
  deselect: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 6L6 18"/>
      <path d="M6 6l12 12"/>
    </svg>
  ),
};

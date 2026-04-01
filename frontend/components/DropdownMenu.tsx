'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useRef } from 'react';
import { getThemeColors } from '@/lib/theme-colors';
import { useTranslation } from '@/lib/i18n';

type GraphOperationStatus = 'queued' | 'generating' | 'cleaning' | null;

interface DropdownMenuProps {
  isOpen: boolean;
  onClose: () => void;
  onDelete: () => void;
  onRegenerate?: () => void;
  onRefreshGraph?: () => void;
  onSync?: () => void;
  onMoveToFolder?: () => void;
  hasGraph?: boolean;
  graphOperationStatus?: GraphOperationStatus;
  theme?: 'dark' | 'light' | 'beige';
  position?: { top: number; right: number };
  variant?: 'repo' | 'operator' | 'folder'; // Context: repo page, operator page, or folder
}

export function DropdownMenu({
  isOpen,
  onClose,
  onDelete,
  onRegenerate,
  onRefreshGraph,
  onSync,
  onMoveToFolder,
  hasGraph = false,
  graphOperationStatus = null,
  theme = 'dark',
  position = { top: 40, right: 0 },
  variant = 'repo'
}: DropdownMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const isGraphBusy = !!graphOperationStatus;
  const colors = getThemeColors(theme);
  const { t } = useTranslation();

  useEffect(() => {
    if (!isOpen) return;

    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        onClose();
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  // Get graph button label based on status
  const getGraphButtonLabel = () => {
    if (graphOperationStatus === 'queued') {
      return t('dropdown.queuedGraph');
    }
    if (graphOperationStatus === 'generating') {
      return t('dropdown.generatingGraph');
    }
    if (graphOperationStatus === 'cleaning') {
      return t('dropdown.cleaningGraph');
    }
    return hasGraph ? t('dropdown.refreshGraph') : t('dropdown.buildGraph');
  };

  const getGraphButtonDescription = () => {
    if (graphOperationStatus === 'queued') {
      return t('dropdown.waitingInQueue');
    }
    if (graphOperationStatus === 'generating') {
      return t('dropdown.parsingCode');
    }
    if (graphOperationStatus === 'cleaning') {
      return t('dropdown.cleaningData');
    }
    return hasGraph ? t('dropdown.refreshGraphDesc') : t('dropdown.buildGraphDesc');
  };

  const menuItems = [
    {
      label: t('dropdown.regenerateDocs'),
      description: t('dropdown.regenerateDocsDesc'),
      onClick: (e: React.MouseEvent) => {
        e.stopPropagation();
        onRegenerate?.();
        onClose();
      },
      color: colors.accent,
      show: !!onRegenerate && variant !== 'folder',
      disabled: false,
    },
    {
      label: t('dropdown.moveToFolder'),
      description: t('dropdown.moveToFolderDesc'),
      onClick: (e: React.MouseEvent) => {
        e.stopPropagation();
        onMoveToFolder?.();
        onClose();
      },
      color: colors.accent,
      show: !!onMoveToFolder && variant === 'operator',
      disabled: false,
    },
    {
      label: t('dropdown.syncSettings'),
      description: t('dropdown.syncSettingsDesc'),
      onClick: (e: React.MouseEvent) => {
        e.stopPropagation();
        onSync?.();
        onClose();
      },
      color: colors.info || colors.accent,
      show: !!onSync && variant === 'repo',
      disabled: false,
    },
    {
      label: getGraphButtonLabel(),
      description: getGraphButtonDescription(),
      onClick: (e: React.MouseEvent) => {
        e.stopPropagation();
        if (!isGraphBusy) {
          onRefreshGraph?.();
          onClose();
        }
      },
      color: isGraphBusy ? colors.textDimmed : colors.success,
      show: !!onRefreshGraph,
      disabled: isGraphBusy,
    },
    {
      label: variant === 'folder' ? t('dropdown.deleteFolder') :
             variant === 'operator' ? t('dropdown.deleteDoc') : t('dropdown.deleteRepo'),
      description: variant === 'folder' ? t('dropdown.deleteFolderDesc') :
                   variant === 'operator' ? t('dropdown.deleteDocDesc') : t('dropdown.deleteRepoDesc'),
      onClick: (e: React.MouseEvent) => {
        e.stopPropagation();
        onDelete();
        onClose();
      },
      color: colors.error,
      show: true,
      disabled: false,
    }
  ].filter(item => item.show);

  return (
    <div
      ref={menuRef}
      style={{
        position: 'absolute',
        top: position.top,
        right: position.right,
        background: colors.card,
        border: `1px solid ${colors.border}`,
        borderRadius: '8px',
        boxShadow: `0 8px 24px ${colors.shadowColor}`,
        zIndex: 1000,
        minWidth: '200px',
        overflow: 'hidden',
        animation: 'slideDown 0.2s ease-out'
      }}
      onClick={(e) => e.stopPropagation()}
    >
      {menuItems.map((item, index) => (
        <button
          key={index}
          onClick={item.onClick}
          disabled={item.disabled}
          style={{
            width: '100%',
            padding: '10px 16px',
            background: 'transparent',
            border: 'none',
            borderBottom: index < menuItems.length - 1 ? `1px solid ${colors.borderLight}` : 'none',
            textAlign: 'left',
            cursor: item.disabled ? 'not-allowed' : 'pointer',
            transition: 'all 0.2s',
            display: 'flex',
            flexDirection: 'column',
            gap: '2px',
            opacity: item.disabled ? 0.6 : 1,
          }}
          onMouseEnter={(e) => {
            if (!item.disabled) {
              e.currentTarget.style.background = colors.bgHover;
            }
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = 'transparent';
          }}
        >
          <span style={{
            fontSize: '14px',
            fontWeight: '500',
            color: item.color,
          }}>
            {item.label}
          </span>
          <span style={{
            fontSize: '11px',
            color: colors.textMuted,
          }}>
            {item.description}
          </span>
        </button>
      ))}

      <style jsx>{`
        @keyframes slideDown {
          from {
            opacity: 0;
            transform: translateY(-10px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }
      `}</style>
    </div>
  );
}

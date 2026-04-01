'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { useState } from 'react';
import { getThemeColors } from '@/lib/theme-colors';
import { useTranslation } from '@/lib/i18n';

interface CreateFolderModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: (name: string) => void;
  theme?: 'dark' | 'light' | 'beige';
  parentFolderName?: string;
}

export function CreateFolderModal({
  isOpen,
  onClose,
  onConfirm,
  theme = 'dark',
  parentFolderName,
}: CreateFolderModalProps) {
  const [folderName, setFolderName] = useState('');
  const [error, setError] = useState('');
  const colors = getThemeColors(theme);
  const { t } = useTranslation();

  if (!isOpen) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    if (!folderName.trim()) {
      setError(t('folder.errorEmpty'));
      return;
    }

    if (folderName.length > 100) {
      setError(t('folder.errorTooLong'));
      return;
    }

    onConfirm(folderName.trim());
    setFolderName('');
    setError('');
  };

  const handleClose = () => {
    setFolderName('');
    setError('');
    onClose();
  };

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
      onClick={handleClose}
    >
      <div
        style={{
          background: colors.card,
          borderRadius: '16px',
          padding: '24px',
          width: '90%',
          maxWidth: '480px',
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
          {t('folder.createTitle')}
        </h2>

        {parentFolderName && (
          <p style={{
            fontSize: '13px',
            color: colors.textMuted,
            marginBottom: '16px',
          }}>
            {t('folder.createIn', { name: parentFolderName })}
          </p>
        )}

        <form onSubmit={handleSubmit}>
          <input
            type="text"
            value={folderName}
            onChange={(e) => {
              setFolderName(e.target.value);
              setError('');
            }}
            placeholder={t('folder.namePlaceholder')}
            autoFocus
            style={{
              width: '100%',
              padding: '12px',
              fontSize: '14px',
              border: `1px solid ${error ? colors.error : colors.border}`,
              borderRadius: '8px',
              background: colors.bg,
              color: colors.text,
              outline: 'none',
              marginBottom: '8px',
            }}
          />

          {error && (
            <p style={{
              fontSize: '12px',
              color: colors.error,
              marginBottom: '16px',
            }}>
              {error}
            </p>
          )}

          <div style={{
            display: 'flex',
            gap: '12px',
            justifyContent: 'flex-end',
            marginTop: '20px',
          }}>
            <button
              type="button"
              onClick={handleClose}
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
              {t('common.cancel')}
            </button>

            <button
              type="submit"
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
              {t('folder.create')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

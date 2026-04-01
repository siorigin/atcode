'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { ReactNode } from 'react';
import { getThemeColors } from '@/lib/theme-colors';
import { useTranslation } from '@/lib/i18n';

interface EmptyStateProps {
  icon?: string;
  title?: string;
  titleSize?: 'normal' | 'large';
  description?: string;
  action?: {
    label: string;
    onClick: () => void;
  };
  theme?: 'dark' | 'light' | 'beige';
}

export function EmptyState({
  icon = '📭',
  title,
  titleSize = 'normal',
  description,
  action,
  theme = 'dark'
}: EmptyStateProps) {
  const colors = getThemeColors(theme);
  const { t } = useTranslation();

  const displayTitle = title || t('empty.noData');
  const displayDescription = description || t('empty.startProject');

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      padding: '40px 24px',
      textAlign: 'center'
    }}>
      <h3 style={{
        fontSize: titleSize === 'large' ? '20px' : '15px',
        fontWeight: titleSize === 'large' ? '600' : '500',
        marginBottom: '8px',
        color: titleSize === 'large' ? colors.text : colors.textMuted,
      }}>
        {displayTitle}
      </h3>
      <p style={{
        fontSize: '14px',
        color: colors.textMuted,
        marginBottom: action ? '24px' : '0',
        maxWidth: '400px',
        lineHeight: '1.6'
      }}>
        {displayDescription}
      </p>
      {action && (
        <button
          onClick={action.onClick}
          className="btn"
          style={{
            padding: '12px 24px',
            fontSize: '14px',
            fontWeight: '600',
            borderRadius: '8px',
            border: 'none',
            cursor: 'pointer',
            background: colors.buttonPrimaryBg,
            color: '#ffffff',
            boxShadow: `0 4px 15px ${colors.shadowColor}`,
            transition: 'all 0.3s ease'
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = colors.buttonPrimaryHover;
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = colors.buttonPrimaryBg;
          }}
        >
          {action.label}
        </button>
      )}
    </div>
  );
}

export function ChatEmptyState({ theme = 'dark' }: { theme?: 'dark' | 'light' | 'beige' }) {
  const { t } = useTranslation();
  return (
    <EmptyState
      title={t('empty.startChat')}
      titleSize="large"
      description={t('empty.chatDesc')}
      theme={theme}
    />
  );
}

export function CodeEmptyState({ theme = 'dark' }: { theme?: 'dark' | 'light' | 'beige' }) {
  const { t } = useTranslation();
  return (
    <EmptyState
      icon="📝"
      title={t('empty.codeReferences')}
      description={t('empty.codeReferencesDesc')}
      theme={theme}
    />
  );
}

export function RepoEmptyState({
  onAddRepo,
  theme = 'dark'
}: {
  onAddRepo: () => void;
  theme?: 'dark' | 'light' | 'beige';
}) {
  const { t } = useTranslation();
  return (
    <EmptyState
      icon="📦"
      title={t('empty.noRepos')}
      description={t('empty.noReposDesc')}
      action={{
        label: t('empty.addRepo'),
        onClick: onAddRepo
      }}
      theme={theme}
    />
  );
}

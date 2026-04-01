'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import { useTheme } from '../../lib/theme-context';
import { getThemeColors } from '../../lib/theme-colors';
import type { PaperMetadata } from '../../lib/papers-api';

interface PaperCardProps {
  paper: PaperMetadata;
  onRead: () => void;
}

const SOURCE_COLORS: Record<string, string> = {
  arxiv: '#b31b1b',
  semantic_scholar: '#1857b6',
  papers_with_code: '#21a366',
};

export default function PaperCard({ paper, onRead }: PaperCardProps) {
  const { theme } = useTheme();
  const colors = getThemeColors(theme);

  const sourceColor = SOURCE_COLORS[paper.source] || colors.accent;

  return (
    <div
      style={{
        background: colors.card,
        border: `1px solid ${colors.border}`,
        borderRadius: 8,
        padding: 16,
        transition: 'border-color 0.2s',
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
        <div style={{ flex: 1, marginRight: 12 }}>
          <h3 style={{ fontSize: 15, fontWeight: 600, lineHeight: 1.4, marginBottom: 4 }}>
            {paper.title}
          </h3>
          {paper.authors.length > 0 && (
            <div style={{ fontSize: 12, color: colors.textMuted }}>
              {paper.authors.slice(0, 5).join(', ')}
              {paper.authors.length > 5 && ` +${paper.authors.length - 5} more`}
            </div>
          )}
        </div>

        {/* Source badge */}
        <span
          style={{
            fontSize: 11,
            fontWeight: 600,
            padding: '2px 8px',
            borderRadius: 12,
            background: sourceColor + '20',
            color: sourceColor,
            whiteSpace: 'nowrap',
            flexShrink: 0,
          }}
        >
          {paper.source === 'semantic_scholar' ? 'S2' : paper.source === 'papers_with_code' ? 'PWC' : 'arXiv'}
        </span>
      </div>

      {/* Abstract */}
      {paper.abstract && (
        <p style={{
          fontSize: 13,
          color: colors.textSecondary,
          lineHeight: 1.5,
          marginBottom: 12,
          display: '-webkit-box',
          WebkitLineClamp: 3,
          WebkitBoxOrient: 'vertical' as any,
          overflow: 'hidden',
        }}>
          {paper.abstract}
        </p>
      )}

      {/* Meta row */}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
      }}>
        <div style={{ display: 'flex', gap: 12, fontSize: 12, color: colors.textMuted }}>
          {paper.published_date && <span>{paper.published_date}</span>}
          {paper.citations > 0 && <span>{paper.citations} citations</span>}
          {paper.github_urls.length > 0 && (
            <span style={{ color: colors.success }}>
              {paper.github_urls.length} repo{paper.github_urls.length > 1 ? 's' : ''}
            </span>
          )}
          {paper.paper_id && (
            <span style={{ fontFamily: 'monospace', opacity: 0.7 }}>{paper.paper_id}</span>
          )}
        </div>

        <button
          onClick={onRead}
          style={{
            padding: '6px 16px',
            background: colors.accent,
            color: '#fff',
            border: 'none',
            borderRadius: 6,
            fontSize: 13,
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          Read Paper
        </button>
      </div>
    </div>
  );
}

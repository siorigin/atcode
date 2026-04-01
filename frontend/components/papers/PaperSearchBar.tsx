'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useCallback } from 'react';
import { useTheme } from '../../lib/theme-context';
import { getThemeColors } from '../../lib/theme-colors';

interface PaperSearchBarProps {
  onSearch: (query: string, sources: string[]) => void;
  isLoading?: boolean;
}

const SOURCES = [
  { id: 'arxiv', label: 'arXiv' },
  { id: 'semantic_scholar', label: 'Semantic Scholar' },
  { id: 'papers_with_code', label: 'Papers With Code' },
];

export default function PaperSearchBar({ onSearch, isLoading }: PaperSearchBarProps) {
  const { theme } = useTheme();
  const colors = getThemeColors(theme);

  const [query, setQuery] = useState('');
  const [selectedSources, setSelectedSources] = useState<string[]>(['arxiv', 'semantic_scholar']);

  const handleSubmit = useCallback((e: React.FormEvent) => {
    e.preventDefault();
    if (query.trim()) {
      onSearch(query.trim(), selectedSources);
    }
  }, [query, selectedSources, onSearch]);

  const toggleSource = useCallback((sourceId: string) => {
    setSelectedSources(prev => {
      if (prev.includes(sourceId)) {
        return prev.length > 1 ? prev.filter(s => s !== sourceId) : prev;
      }
      return [...prev, sourceId];
    });
  }, []);

  return (
    <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', gap: 8 }}>
        <input
          type="text"
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Search papers... (e.g., 'attention mechanism' or arXiv ID '2504.20073')"
          style={{
            flex: 1,
            padding: '10px 16px',
            background: colors.inputBg,
            border: `1px solid ${colors.inputBorder}`,
            borderRadius: 8,
            color: colors.inputText,
            fontSize: 14,
            outline: 'none',
          }}
        />
        <button
          type="submit"
          disabled={isLoading || !query.trim()}
          style={{
            padding: '10px 24px',
            background: isLoading ? colors.textMuted : colors.accent,
            color: '#fff',
            border: 'none',
            borderRadius: 8,
            fontSize: 14,
            fontWeight: 600,
            cursor: isLoading ? 'not-allowed' : 'pointer',
            opacity: !query.trim() ? 0.5 : 1,
            minWidth: 100,
          }}
        >
          {isLoading ? 'Searching...' : 'Search'}
        </button>
      </div>

      {/* Source toggles */}
      <div style={{ display: 'flex', gap: 8 }}>
        {SOURCES.map(source => {
          const isSelected = selectedSources.includes(source.id);
          return (
            <button
              key={source.id}
              type="button"
              onClick={() => toggleSource(source.id)}
              style={{
                padding: '4px 12px',
                borderRadius: 16,
                fontSize: 12,
                fontWeight: 500,
                border: `1px solid ${isSelected ? colors.accentBorder : colors.borderLight}`,
                background: isSelected ? colors.accentBg : 'transparent',
                color: isSelected ? colors.accent : colors.textMuted,
                cursor: 'pointer',
              }}
            >
              {source.label}
            </button>
          );
        })}
      </div>
    </form>
  );
}

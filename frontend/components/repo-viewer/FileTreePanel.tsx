'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useCallback, useMemo } from 'react';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';
import type { FolderChildItem, Breadcrumb } from './repo-viewer-types';
import type { GitRef } from './repo-viewer-hooks';

// --- SVG Icons ---

function FolderIcon({ color = 'currentColor', size = 16 }: { color?: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" fill={color} opacity={0.15} stroke={color} />
    </svg>
  );
}

function FileIcon({ ext, size = 16 }: { ext?: string; size?: number }) {
  const extColors: Record<string, string> = {
    py: '#3572A5', pyx: '#3572A5',
    js: '#f1e05a', jsx: '#f1e05a', ts: '#3178c6', tsx: '#3178c6',
    rs: '#dea584', go: '#00ADD8', java: '#b07219',
    c: '#555555', cpp: '#f34b7d', h: '#555555', hpp: '#f34b7d', cu: '#76B900', cuh: '#76B900', cc: '#f34b7d',
    rb: '#701516', sh: '#89e051',
    json: '#c9a227', yaml: '#cb171e', yml: '#cb171e', toml: '#9c4221',
    md: '#083fa1', html: '#e34c26', css: '#563d7c',
  };
  const color = extColors[ext || ''] || '#8b949e';
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
      <polyline points="14 2 14 8 20 8" />
    </svg>
  );
}

function ChevronRight({ size = 12, color = 'currentColor' }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="9 18 15 12 9 6" />
    </svg>
  );
}

function SearchIcon({ size = 14, color = 'currentColor' }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  );
}

function GitBranchIcon({ size = 14, color = 'currentColor' }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="6" y1="3" x2="6" y2="15" /><circle cx="18" cy="6" r="3" /><circle cx="6" cy="18" r="3" /><path d="M18 9a9 9 0 01-9 9" />
    </svg>
  );
}

// --- Props ---

export interface FileTreePanelProps {
  repoName: string;
  children: FolderChildItem[];
  loading: boolean;
  error: string | null;
  breadcrumbs: Breadcrumb[];
  activeQN: string | null;
  branches: GitRef[];
  currentBranch: string;
  graphSearchResults: Array<{ qualified_name: string; name: string; node_type: string; file_path?: string }>;
  graphSearchLoading: boolean;
  onItemClick: (item: FolderChildItem) => void;
  onBreadcrumbClick: (index: number) => void;
  onGraphResultClick: (qn: string) => void;
  onSearchChange: (query: string) => void;
  onBranchChange: (branch: string) => void;
  onShowMore: () => void;
  fileListLimit: number;
}

export function FileTreePanel({
  children: childItems, loading, error, breadcrumbs, activeQN,
  branches, currentBranch,
  graphSearchResults, graphSearchLoading,
  onItemClick, onBreadcrumbClick, onGraphResultClick,
  onSearchChange, onBranchChange, onShowMore, fileListLimit,
}: FileTreePanelProps) {
  const { theme } = useTheme();
  const colors = getThemeColors(theme);
  const [searchQuery, setSearchQuery] = useState('');
  const [branchOpen, setBranchOpen] = useState(false);

  const handleSearchChange = useCallback((value: string) => {
    setSearchQuery(value);
    onSearchChange(value);
  }, [onSearchChange]);

  const filteredChildren = useMemo(() => {
    if (!searchQuery.trim()) return childItems;
    const q = searchQuery.toLowerCase();
    return childItems.filter(item => item.name.toLowerCase().includes(q));
  }, [childItems, searchQuery]);

  const visibleChildren = useMemo(() => {
    return filteredChildren.slice(0, fileListLimit);
  }, [filteredChildren, fileListLimit]);

  const hasMoreChildren = filteredChildren.length > fileListLimit;

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', height: '100%',
      background: colors.bgSecondary, overflow: 'hidden',
    }}>
      {/* Branch selector */}
      <div style={{ padding: '8px 10px', borderBottom: `1px solid ${colors.border}`, flexShrink: 0, position: 'relative' }}>
        <button
          onClick={() => setBranchOpen(!branchOpen)}
          style={{
            display: 'flex', alignItems: 'center', gap: '6px',
            width: '100%', padding: '5px 10px',
            background: colors.bg, border: `1px solid ${colors.border}`,
            borderRadius: '6px', color: colors.text, fontSize: '12px',
            fontFamily: "'Inter', -apple-system, sans-serif",
            cursor: 'pointer', fontWeight: 500,
          }}
        >
          <GitBranchIcon size={13} color={colors.accent} />
          <span style={{ flex: 1, textAlign: 'left', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {currentBranch || 'main'}
          </span>
          <ChevronRight size={10} color={colors.textMuted} />
        </button>
        {branchOpen && branches.length > 0 && (
          <div style={{
            position: 'absolute', zIndex: 100, left: '10px', right: '10px', marginTop: '4px',
            background: colors.bg, border: `1px solid ${colors.border}`,
            borderRadius: '8px', boxShadow: '0 8px 24px rgba(0,0,0,0.3)',
            maxHeight: '200px', overflow: 'auto',
          }}>
            {branches.map(b => (
              <button
                key={b.name}
                onClick={() => { onBranchChange(b.name); setBranchOpen(false); }}
                style={{
                  display: 'block', width: '100%', padding: '6px 12px',
                  background: b.name === currentBranch ? colors.accentBg : 'transparent',
                  border: 'none', color: b.name === currentBranch ? colors.accent : colors.text,
                  fontSize: '12px', textAlign: 'left', cursor: 'pointer',
                  fontFamily: "'Inter', -apple-system, sans-serif",
                }}
                onMouseEnter={(e) => { if (b.name !== currentBranch) e.currentTarget.style.background = colors.bgHover; }}
                onMouseLeave={(e) => { if (b.name !== currentBranch) e.currentTarget.style.background = 'transparent'; }}
              >
                {b.name}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Breadcrumb navigation */}
      {breadcrumbs.length > 1 && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: '2px',
          padding: '6px 10px', borderBottom: `1px solid ${colors.border}`,
          overflow: 'auto', whiteSpace: 'nowrap', fontSize: '12px',
          fontFamily: "'Inter', -apple-system, sans-serif", flexShrink: 0,
        }}>
          {breadcrumbs.map((crumb, idx) => (
            <React.Fragment key={crumb.qualified_name}>
              {idx > 0 && (
                <span style={{ color: colors.textDimmed, margin: '0 1px', display: 'flex', alignItems: 'center' }}>
                  <ChevronRight size={10} color={colors.textDimmed} />
                </span>
              )}
              <button
                onClick={() => onBreadcrumbClick(idx)}
                style={{
                  background: idx === breadcrumbs.length - 1 ? colors.accentBg : 'transparent',
                  color: idx === breadcrumbs.length - 1 ? colors.accent : colors.textMuted,
                  border: 'none', borderRadius: '4px', padding: '2px 6px',
                  cursor: 'pointer', fontSize: '12px', fontFamily: 'inherit',
                  fontWeight: idx === breadcrumbs.length - 1 ? 600 : 450,
                }}
                onMouseEnter={(e) => { if (idx !== breadcrumbs.length - 1) { e.currentTarget.style.color = colors.text; e.currentTarget.style.background = colors.bgHover; } }}
                onMouseLeave={(e) => { if (idx !== breadcrumbs.length - 1) { e.currentTarget.style.color = colors.textMuted; e.currentTarget.style.background = 'transparent'; } }}
              >
                {crumb.name}
              </button>
            </React.Fragment>
          ))}
        </div>
      )}

      {/* Search bar */}
      <div style={{ padding: '8px 10px', flexShrink: 0, borderBottom: `1px solid ${colors.border}` }}>
        <div style={{ position: 'relative' }}>
          <div style={{ position: 'absolute', left: 9, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }}>
            <SearchIcon size={13} color={colors.textDimmed} />
          </div>
          <input
            type="text"
            placeholder="Go to file..."
            value={searchQuery}
            onChange={(e) => handleSearchChange(e.target.value)}
            style={{
              width: '100%', padding: '6px 10px 6px 30px',
              fontSize: '12px', fontFamily: "'Inter', -apple-system, sans-serif",
              background: colors.bg, color: colors.text,
              border: `1px solid ${colors.border}`, borderRadius: '6px', outline: 'none',
              boxSizing: 'border-box',
            }}
            onFocus={(e) => { e.currentTarget.style.borderColor = colors.accent; e.currentTarget.style.boxShadow = `0 0 0 2px ${colors.accentBg}`; }}
            onBlur={(e) => { e.currentTarget.style.borderColor = colors.border; e.currentTarget.style.boxShadow = 'none'; }}
          />
        </div>
      </div>

      {/* File list */}
      <div style={{ flex: 1, overflow: 'auto' }}>
        {loading ? (
          <div style={{ padding: '24px', textAlign: 'center', color: colors.textMuted }}>
            <div style={{
              width: '20px', height: '20px',
              border: `2px solid ${colors.border}`, borderTopColor: colors.accent,
              borderRadius: '50%', animation: 'spin 0.8s linear infinite',
              margin: '0 auto 8px',
            }} />
            <span style={{ fontSize: '12px' }}>Loading...</span>
          </div>
        ) : error ? (
          <div style={{ padding: '16px', textAlign: 'center', color: '#ef4444', fontSize: '12px' }}>{error}</div>
        ) : (
          <>
            {/* Graph search results */}
            {searchQuery.trim() && graphSearchResults.length > 0 && (
              <>
                <div style={{
                  padding: '4px 10px', fontSize: '10px', fontWeight: 600,
                  color: colors.textMuted, background: colors.bgHover,
                  borderBottom: `1px solid ${colors.border}`,
                  letterSpacing: '0.3px', textTransform: 'uppercase',
                }}>
                  Graph Results
                </div>
                {graphSearchResults.slice(0, 20).map(r => (
                  <button
                    key={r.qualified_name}
                    onClick={() => onGraphResultClick(r.qualified_name)}
                    style={{
                      display: 'flex', alignItems: 'center', gap: '8px',
                      width: '100%', padding: '5px 10px',
                      background: 'transparent', border: 'none',
                      color: colors.text, fontSize: '12px', textAlign: 'left',
                      cursor: 'pointer', fontFamily: "'JetBrains Mono', monospace",
                    }}
                    onMouseEnter={(e) => e.currentTarget.style.background = colors.bgHover}
                    onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
                  >
                    <span style={{
                      fontSize: '9px', padding: '1px 4px', borderRadius: '3px',
                      background: colors.accentBg, color: colors.accent, fontWeight: 600,
                      fontFamily: "'Inter', sans-serif",
                    }}>
                      {r.node_type}
                    </span>
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {r.name}
                    </span>
                  </button>
                ))}
                {graphSearchLoading && (
                  <div style={{ padding: '8px 10px', color: colors.textMuted, fontSize: '11px', textAlign: 'center' }}>
                    Searching graph...
                  </div>
                )}
              </>
            )}

            {/* Local file/folder list */}
            {filteredChildren.length > 0 && searchQuery.trim() && (
              <div style={{
                padding: '4px 10px', fontSize: '10px', fontWeight: 600,
                color: colors.textMuted, background: colors.bgHover,
                borderBottom: `1px solid ${colors.border}`,
                letterSpacing: '0.3px', textTransform: 'uppercase',
              }}>
                Files
              </div>
            )}
            {visibleChildren.map(item => {
              const isFolder = item.is_package || item.node_type === 'Module' || item.child_count > 0;
              const ext = item.name.includes('.') ? item.name.split('.').pop() : undefined;
              const isActive = activeQN === item.qualified_name;
              return (
                <button
                  key={item.qualified_name}
                  onClick={() => onItemClick(item)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: '8px',
                    width: '100%', padding: '4px 10px',
                    background: isActive ? colors.accentBg : 'transparent',
                    border: 'none', borderLeft: isActive ? `2px solid ${colors.accent}` : '2px solid transparent',
                    color: isActive ? colors.accent : colors.text,
                    fontSize: '13px', textAlign: 'left', cursor: 'pointer',
                    fontFamily: "'Inter', -apple-system, sans-serif",
                  }}
                  onMouseEnter={(e) => { if (!isActive) e.currentTarget.style.background = colors.bgHover; }}
                  onMouseLeave={(e) => { if (!isActive) e.currentTarget.style.background = 'transparent'; }}
                >
                  {isFolder
                    ? <FolderIcon color="#e8a854" size={15} />
                    : <FileIcon ext={ext} size={15} />
                  }
                  <span style={{
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    fontWeight: isFolder ? 500 : 400,
                  }}>
                    {item.name}
                  </span>
                  {isFolder && item.child_count > 0 && (
                    <span style={{ marginLeft: 'auto', fontSize: '10px', color: colors.textDimmed }}>
                      {item.child_count}
                    </span>
                  )}
                </button>
              );
            })}
            {hasMoreChildren && (
              <button
                onClick={onShowMore}
                style={{
                  display: 'block', width: '100%', padding: '8px',
                  background: 'transparent', border: 'none', borderTop: `1px solid ${colors.border}`,
                  color: colors.accent, fontSize: '12px', cursor: 'pointer',
                  fontFamily: "'Inter', -apple-system, sans-serif",
                }}
                onMouseEnter={(e) => e.currentTarget.style.background = colors.bgHover}
                onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
              >
                Show more ({filteredChildren.length - fileListLimit} remaining)
              </button>
            )}
            {filteredChildren.length === 0 && !graphSearchResults.length && !graphSearchLoading && (
              <div style={{ padding: '16px', textAlign: 'center', color: colors.textMuted, fontSize: '12px' }}>
                {searchQuery ? 'No results' : 'Empty folder'}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

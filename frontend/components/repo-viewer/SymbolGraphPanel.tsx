'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useCallback, useMemo } from 'react';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';
import type { SymbolItem, SymbolCallsData, CallItem } from './repo-viewer-types';
import { SYMBOL_TYPE_COLORS } from './repo-viewer-types';

// --- Icons ---

function SymbolIcon({ type, size = 14 }: { type: string; size?: number }) {
  const color = SYMBOL_TYPE_COLORS[type] || '#8b949e';
  const letter = type === 'Class' ? 'C' : type === 'Method' ? 'M' : type === 'Variable' ? 'V' : 'F';
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      width: size, height: size, borderRadius: '3px',
      background: `${color}22`, color, fontSize: '9px', fontWeight: 700,
      fontFamily: "'Inter', -apple-system, sans-serif", flexShrink: 0,
    }}>
      {letter}
    </span>
  );
}

function CallArrowIcon({ direction, size = 10, color = '#8b949e' }: { direction: 'in' | 'out'; size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      {direction === 'in'
        ? <><polyline points="17 11 12 6 7 11" /><line x1="12" y1="6" x2="12" y2="18" /></>
        : <><polyline points="7 13 12 18 17 13" /><line x1="12" y1="18" x2="12" y2="6" /></>
      }
    </svg>
  );
}

// --- Props ---

export interface SymbolGraphPanelProps {
  symbols: SymbolItem[];
  loading: boolean;
  currentLine?: number;
  onSymbolClick: (symbol: SymbolItem) => void;
  onCallItemClick: (qn: string) => void;
  fetchCalls: (qn: string) => Promise<SymbolCallsData>;
}

export function SymbolGraphPanel({
  symbols, loading, currentLine,
  onSymbolClick, onCallItemClick, fetchCalls,
}: SymbolGraphPanelProps) {
  const { theme } = useTheme();
  const colors = getThemeColors(theme);
  const [filter, setFilter] = useState('');
  const [expandedCalls, setExpandedCalls] = useState<Map<string, SymbolCallsData>>(new Map());
  const [loadingCalls, setLoadingCalls] = useState<Set<string>>(new Set());
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());

  // Group symbols by type
  const grouped = useMemo(() => {
    const filtered = filter.trim()
      ? symbols.filter(s => s.name.toLowerCase().includes(filter.toLowerCase()))
      : symbols;
    const groups: Record<string, SymbolItem[]> = {};
    for (const s of filtered) {
      const key = s.type || 'Function';
      (groups[key] ||= []).push(s);
    }
    return groups;
  }, [symbols, filter]);

  // Current symbol based on line
  const currentSymbol = useMemo(() => {
    if (currentLine == null) return null;
    let best: SymbolItem | null = null;
    for (const s of symbols) {
      if (s.start_line && s.end_line && currentLine >= s.start_line && currentLine <= s.end_line) {
        if (!best || (s.start_line > best.start_line!)) best = s;
      }
    }
    return best;
  }, [symbols, currentLine]);

  const toggleCalls = useCallback(async (qn: string) => {
    if (expandedCalls.has(qn)) {
      setExpandedCalls(prev => { const next = new Map(prev); next.delete(qn); return next; });
      return;
    }
    setLoadingCalls(prev => new Set(prev).add(qn));
    try {
      const data = await fetchCalls(qn);
      setExpandedCalls(prev => new Map(prev).set(qn, data));
    } finally {
      setLoadingCalls(prev => { const next = new Set(prev); next.delete(qn); return next; });
    }
  }, [expandedCalls, fetchCalls]);

  const toggleGroup = useCallback((type: string) => {
    setCollapsedGroups(prev => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type); else next.add(type);
      return next;
    });
  }, []);

  if (loading) {
    return (
      <div style={{
        display: 'flex', flexDirection: 'column', height: '100%',
        background: colors.bgSecondary, alignItems: 'center', justifyContent: 'center',
      }}>
        <div style={{
          width: '20px', height: '20px',
          border: `2px solid ${colors.border}`, borderTopColor: colors.accent,
          borderRadius: '50%', animation: 'spin 0.8s linear infinite',
        }} />
        <span style={{ marginTop: '8px', fontSize: '12px', color: colors.textMuted }}>Loading symbols...</span>
      </div>
    );
  }

  if (symbols.length === 0) {
    return (
      <div style={{
        display: 'flex', flexDirection: 'column', height: '100%',
        background: colors.bgSecondary, alignItems: 'center', justifyContent: 'center',
        color: colors.textMuted, fontSize: '12px', padding: '20px',
        fontFamily: "'Inter', -apple-system, sans-serif",
      }}>
        No symbols found
      </div>
    );
  }

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', height: '100%',
      background: colors.bgSecondary, overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{
        padding: '8px 10px', borderBottom: `1px solid ${colors.border}`,
        flexShrink: 0, fontSize: '11px', fontWeight: 600,
        color: colors.textMuted, fontFamily: "'Inter', -apple-system, sans-serif",
        letterSpacing: '0.3px', textTransform: 'uppercase',
      }}>
        Symbols ({symbols.length})
      </div>

      {/* Filter */}
      <div style={{ padding: '6px 10px', borderBottom: `1px solid ${colors.border}`, flexShrink: 0 }}>
        <input
          type="text"
          placeholder="Filter symbols..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{
            width: '100%', padding: '5px 8px', fontSize: '11px',
            fontFamily: "'Inter', -apple-system, sans-serif",
            background: colors.bg, color: colors.text,
            border: `1px solid ${colors.border}`, borderRadius: '5px', outline: 'none',
            boxSizing: 'border-box',
          }}
          onFocus={(e) => { e.currentTarget.style.borderColor = colors.accent; }}
          onBlur={(e) => { e.currentTarget.style.borderColor = colors.border; }}
        />
      </div>

      {/* Symbol list */}
      <div style={{ flex: 1, overflow: 'auto' }}>
        {Object.entries(grouped).map(([type, items]) => {
          const isCollapsed = collapsedGroups.has(type);
          return (
            <div key={type}>
              {/* Group header */}
              <button
                onClick={() => toggleGroup(type)}
                style={{
                  display: 'flex', alignItems: 'center', gap: '6px',
                  width: '100%', padding: '5px 10px',
                  background: colors.bgHover, border: 'none', borderBottom: `1px solid ${colors.border}`,
                  color: colors.textMuted, fontSize: '10px', fontWeight: 600,
                  cursor: 'pointer', fontFamily: "'Inter', -apple-system, sans-serif",
                  letterSpacing: '0.2px', textTransform: 'uppercase',
                }}
              >
                <span style={{
                  display: 'inline-flex', transition: 'transform 0.15s',
                  transform: isCollapsed ? 'rotate(0deg)' : 'rotate(90deg)',
                }}>
                  <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="9 18 15 12 9 6" />
                  </svg>
                </span>
                <SymbolIcon type={type} size={12} />
                <span>{type}s ({items.length})</span>
              </button>

              {/* Symbol items */}
              {!isCollapsed && items.map(symbol => {
                const isCurrent = currentSymbol?.qualified_name === symbol.qualified_name;
                const callsData = expandedCalls.get(symbol.qualified_name);
                const isCallsLoading = loadingCalls.has(symbol.qualified_name);

                return (
                  <div key={symbol.qualified_name}>
                    <div style={{
                      display: 'flex', alignItems: 'center', gap: '6px',
                      padding: '3px 10px 3px 20px',
                      background: isCurrent ? colors.accentBg : 'transparent',
                      borderLeft: isCurrent ? `2px solid ${colors.accent}` : '2px solid transparent',
                    }}>
                      {/* Symbol name - click to jump */}
                      <button
                        onClick={() => onSymbolClick(symbol)}
                        style={{
                          flex: 1, display: 'flex', alignItems: 'center', gap: '6px',
                          background: 'transparent', border: 'none',
                          color: isCurrent ? colors.accent : colors.text,
                          fontSize: '12px', textAlign: 'left', cursor: 'pointer', padding: 0,
                          fontFamily: "'JetBrains Mono', monospace",
                          overflow: 'hidden',
                        }}
                      >
                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {symbol.name}
                        </span>
                        {symbol.start_line && (
                          <span style={{ marginLeft: 'auto', fontSize: '10px', color: colors.textDimmed, flexShrink: 0 }}>
                            :{symbol.start_line}
                          </span>
                        )}
                      </button>

                      {/* Expand calls button */}
                      <button
                        onClick={() => toggleCalls(symbol.qualified_name)}
                        title="Show callers & callees"
                        style={{
                          padding: '2px 4px', background: callsData ? colors.accentBg : 'transparent',
                          border: `1px solid ${callsData ? colors.accent : colors.border}`,
                          borderRadius: '4px', cursor: 'pointer',
                          color: callsData ? colors.accent : colors.textMuted,
                          fontSize: '9px', fontWeight: 600, flexShrink: 0,
                          fontFamily: "'Inter', -apple-system, sans-serif",
                          opacity: isCallsLoading ? 0.5 : 1,
                        }}
                      >
                        {isCallsLoading ? '...' : '\u21C4'}
                      </button>
                    </div>

                    {/* Expanded calls */}
                    {callsData && (
                      <div style={{ paddingLeft: '32px', borderLeft: `1px dashed ${colors.border}`, marginLeft: '20px' }}>
                        {/* Callers (incoming) */}
                        {callsData.callers.length > 0 && (
                          <div style={{ padding: '2px 0' }}>
                            <div style={{
                              display: 'flex', alignItems: 'center', gap: '4px',
                              fontSize: '10px', color: '#f0883e', fontWeight: 600, padding: '2px 0',
                              fontFamily: "'Inter', -apple-system, sans-serif",
                            }}>
                              <CallArrowIcon direction="in" size={10} color="#f0883e" />
                              called by ({callsData.callers.length})
                            </div>
                            {callsData.callers.slice(0, 8).map(caller => (
                              <CallItemRow
                                key={caller.qualified_name}
                                item={caller}
                                colors={colors}
                                onClick={() => onCallItemClick(caller.qualified_name)}
                              />
                            ))}
                            {callsData.callers.length > 8 && (
                              <div style={{ fontSize: '10px', color: colors.textDimmed, padding: '2px 4px' }}>
                                +{callsData.callers.length - 8} more
                              </div>
                            )}
                          </div>
                        )}
                        {/* Callees (outgoing) */}
                        {callsData.callees.length > 0 && (
                          <div style={{ padding: '2px 0' }}>
                            <div style={{
                              display: 'flex', alignItems: 'center', gap: '4px',
                              fontSize: '10px', color: '#a371f7', fontWeight: 600, padding: '2px 0',
                              fontFamily: "'Inter', -apple-system, sans-serif",
                            }}>
                              <CallArrowIcon direction="out" size={10} color="#a371f7" />
                              calls ({callsData.callees.length})
                            </div>
                            {callsData.callees.slice(0, 8).map(callee => (
                              <CallItemRow
                                key={callee.qualified_name}
                                item={callee}
                                colors={colors}
                                onClick={() => onCallItemClick(callee.qualified_name)}
                              />
                            ))}
                            {callsData.callees.length > 8 && (
                              <div style={{ fontSize: '10px', color: colors.textDimmed, padding: '2px 4px' }}>
                                +{callsData.callees.length - 8} more
                              </div>
                            )}
                          </div>
                        )}
                        {callsData.callers.length === 0 && callsData.callees.length === 0 && (
                          <div style={{ fontSize: '10px', color: colors.textDimmed, padding: '4px 0', fontStyle: 'italic' }}>
                            No call relationships found
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// --- Call Item Row ---

function CallItemRow({ item, colors, onClick }: {
  item: CallItem;
  colors: ReturnType<typeof getThemeColors>;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'center', gap: '6px',
        width: '100%', padding: '2px 4px',
        background: 'transparent', border: 'none',
        color: colors.text, fontSize: '11px', textAlign: 'left',
        cursor: 'pointer', fontFamily: "'JetBrains Mono', monospace",
      }}
      onMouseEnter={(e) => e.currentTarget.style.background = colors.bgHover}
      onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
    >
      <span style={{ color: colors.textDimmed, fontSize: '8px' }}>{'\u2022'}</span>
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {item.name}
      </span>
      {item.file_path && (
        <span style={{ marginLeft: 'auto', fontSize: '9px', color: colors.textDimmed, flexShrink: 0 }}>
          {item.file_path.split('/').pop()}
        </span>
      )}
    </button>
  );
}

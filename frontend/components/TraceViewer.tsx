'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useMemo } from 'react';
import type { TraceNode, TraceNodeStatus } from '@/types/trace';
import type { Theme } from '@/lib/theme-context';
import { getThemeColors, type ThemeColors } from '@/lib/theme-colors';

interface TraceViewerProps {
  nodes: TraceNode[];
  theme: Theme;
  compact?: boolean;
  maxHeight?: string;
  defaultExpanded?: boolean;
}

// ---------------------------------------------------------------------------
// Status helpers
// ---------------------------------------------------------------------------

function getStatusColor(status: TraceNodeStatus, c: ThemeColors) {
  switch (status) {
    case 'running': return { fg: c.info, bg: c.infoBg };
    case 'success': return { fg: c.success, bg: c.successBg };
    case 'error':   return { fg: c.error, bg: c.errorBg };
    case 'pending': return { fg: c.textDimmed, bg: 'transparent' };
  }
}

function StatusIcon({ status, colors }: { status: TraceNodeStatus; colors: ThemeColors }) {
  const sc = getStatusColor(status, colors);
  const size = 12;
  const common = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: sc.fg, strokeWidth: 2.5 } as const;

  if (status === 'running') {
    return (
      <svg {...common} style={{ animation: 'trace-spin 1s linear infinite', flexShrink: 0 }}>
        <circle cx="12" cy="12" r="10" strokeOpacity="0.2" />
        <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
      </svg>
    );
  }
  if (status === 'success') {
    return (
      <svg {...common} style={{ flexShrink: 0 }}>
        <polyline points="20 6 9 17 4 12" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  if (status === 'error') {
    return (
      <svg {...common} style={{ flexShrink: 0 }}>
        <line x1="18" y1="6" x2="6" y2="18" strokeLinecap="round" />
        <line x1="6" y1="6" x2="18" y2="18" strokeLinecap="round" />
      </svg>
    );
  }
  return (
    <svg {...common} style={{ flexShrink: 0 }}>
      <circle cx="12" cy="12" r="3" fill={sc.fg} stroke="none" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Agent badge — visual distinction for agent vs phase vs tool
// ---------------------------------------------------------------------------

function TypeBadge({ type, colors }: { type: TraceNode['type']; colors: ThemeColors }) {
  if (type === 'tool') return null;
  const isAgent = type === 'agent';
  return (
    <span style={{
      fontSize: '10px',
      fontWeight: 600,
      letterSpacing: '0.03em',
      padding: '1px 5px',
      borderRadius: '3px',
      background: isAgent ? colors.accentBg : colors.warningBg,
      color: isAgent ? colors.accent : colors.warning,
      flexShrink: 0,
    }}>
      {isAgent ? 'AGENT' : 'PHASE'}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Single trace node row
// ---------------------------------------------------------------------------

function TraceNodeRow({
  node,
  colors,
  expanded,
  onToggle,
  level,
}: {
  node: TraceNode;
  colors: ThemeColors;
  expanded: Set<string>;
  onToggle: (id: string) => void;
  level: number;
}) {
  const hasChildren = (node.children?.length ?? 0) > 0;
  const hasPreview = (node.preview?.length ?? 0) > 0;
  const isExpanded = expanded.has(node.id);
  const canExpand = hasChildren || hasPreview;
  const sc = getStatusColor(node.status, colors);
  const isAgent = node.type === 'agent';

  return (
    <div>
      {/* Row */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          padding: isAgent ? '5px 8px' : '3px 8px',
          paddingLeft: `${8 + level * 18}px`,
          cursor: canExpand ? 'pointer' : 'default',
          fontSize: '12px',
          borderRadius: '4px',
          background: isAgent && isExpanded ? colors.bgSecondary : 'transparent',
          borderLeft: level > 0 ? `2px solid ${colors.border}` : 'none',
          marginLeft: level > 0 ? `${(level - 1) * 18 + 8}px` : 0,
          transition: 'background 0.15s',
        }}
        onClick={() => canExpand && onToggle(node.id)}
        onMouseEnter={(e) => { if (canExpand) e.currentTarget.style.background = colors.bgHover; }}
        onMouseLeave={(e) => { e.currentTarget.style.background = isAgent && isExpanded ? colors.bgSecondary : 'transparent'; }}
      >
        {/* Chevron */}
        {canExpand ? (
          <span style={{
            fontSize: '9px',
            width: '10px',
            flexShrink: 0,
            color: colors.textMuted,
            transition: 'transform 0.15s',
            transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)',
            display: 'inline-block',
          }}>
            ▶
          </span>
        ) : (
          <span style={{ width: '10px', flexShrink: 0 }} />
        )}

        <StatusIcon status={node.status} colors={colors} />
        <TypeBadge type={node.type} colors={colors} />

        {/* Name */}
        <span style={{
          fontWeight: isAgent ? 600 : 400,
          color: isAgent ? colors.text : colors.textSecondary,
          fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          minWidth: 0,
        }}>
          {node.name}
        </span>

        {/* Input summary */}
        {node.input && (
          <span style={{
            color: colors.textDimmed,
            fontSize: '11px',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            maxWidth: '180px',
          }}>
            {node.input}
          </span>
        )}

        {/* Spacer */}
        <span style={{ flex: 1 }} />

        {/* Output */}
        {node.output && (
          <span style={{
            color: sc.fg,
            fontSize: '11px',
            flexShrink: 0,
            maxWidth: '150px',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}>
            {node.output}
          </span>
        )}

        {/* Children count badge */}
        {hasChildren && !isExpanded && (
          <span style={{
            fontSize: '10px',
            color: colors.textDimmed,
            background: colors.bgSecondary,
            padding: '0 5px',
            borderRadius: '8px',
            flexShrink: 0,
          }}>
            {node.children!.length}
          </span>
        )}

        {/* Duration */}
        {node.duration != null && node.duration > 0 && (
          <span style={{ fontSize: '10px', color: colors.textDimmed, fontFamily: 'monospace', flexShrink: 0 }}>
            {node.duration < 1000 ? `${node.duration}ms` : `${(node.duration / 1000).toFixed(1)}s`}
          </span>
        )}

        {/* Timestamp */}
        {node.timestamp && (
          <span style={{ fontSize: '10px', color: colors.textDimmed, fontFamily: 'monospace', flexShrink: 0 }}>
            {formatTime(node.timestamp)}
          </span>
        )}
      </div>

      {/* Error */}
      {node.error && (
        <div style={{
          paddingLeft: `${26 + level * 18}px`,
          fontSize: '11px',
          color: colors.error,
          padding: '2px 8px 2px',
          marginLeft: level > 0 ? `${(level - 1) * 18 + 8}px` : 0,
        }}>
          {node.error}
        </div>
      )}

      {/* Expanded: preview */}
      {isExpanded && hasPreview && (
        <div style={{
          marginLeft: `${26 + level * 18}px`,
          padding: '4px 0',
          fontSize: '11px',
          color: colors.textMuted,
          lineHeight: '1.5',
          borderLeft: `2px solid ${colors.border}`,
          paddingLeft: '10px',
        }}>
          {node.preview!.map((item, i) => (
            <div key={i} style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{item}</div>
          ))}
        </div>
      )}

      {/* Expanded: children */}
      {isExpanded && hasChildren && (
        <div>
          {node.children!.map((child) => (
            <TraceNodeRow
              key={child.id}
              node={child}
              colors={colors}
              expanded={expanded}
              onToggle={onToggle}
              level={level + 1}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTime(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return '';
  }
}

function collectAll(nodes: TraceNode[]): TraceNode[] {
  const all: TraceNode[] = [];
  const walk = (n: TraceNode) => { all.push(n); n.children?.forEach(walk); };
  nodes.forEach(walk);
  return all;
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function TraceViewer({ nodes, theme, compact, maxHeight, defaultExpanded }: TraceViewerProps) {
  const colors = useMemo(() => getThemeColors(theme), [theme]);

  // Default: expand agent nodes (top-level), collapse their children
  const [expanded, setExpanded] = useState<Set<string>>(() => {
    if (defaultExpanded) {
      return new Set(collectAll(nodes).filter(n => (n.children?.length ?? 0) > 0).map(n => n.id));
    }
    // Auto-expand agent nodes only
    return new Set(nodes.filter(n => n.type === 'agent').map(n => n.id));
  });

  const summary = useMemo(() => {
    const all = collectAll(nodes);
    const agents = nodes.filter(n => n.type === 'agent').length;
    const tools = all.filter(n => n.type === 'tool').length;
    return { total: all.length, agents, tools };
  }, [nodes]);

  const handleToggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    const expandable = collectAll(nodes).filter(n => (n.children?.length ?? 0) > 0 || (n.preview?.length ?? 0) > 0);
    if (expanded.size > 0) {
      setExpanded(new Set());
    } else {
      setExpanded(new Set(expandable.map(n => n.id)));
    }
  };

  if (nodes.length === 0) return null;

  return (
    <div style={{ fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif' }}>
      {/* Header */}
      {!compact && (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '5px 8px',
          borderRadius: '6px',
          background: colors.bgSecondary,
          marginBottom: '4px',
          fontSize: '11px',
          color: colors.textMuted,
        }}>
          <span>
            <span style={{ fontWeight: 600, color: colors.textSecondary }}>{summary.total}</span> steps
            {summary.agents > 0 && <> · <span style={{ fontWeight: 600, color: colors.accent }}>{summary.agents}</span> agents</>}
            {summary.tools > 0 && <> · {summary.tools} tools</>}
          </span>
          <button
            onClick={toggleAll}
            style={{
              background: 'none', border: 'none', cursor: 'pointer',
              color: colors.accent, fontSize: '11px', padding: '0 4px',
            }}
          >
            {expanded.size > 0 ? 'Collapse all' : 'Expand all'}
          </button>
        </div>
      )}

      {/* Node list */}
      <div style={maxHeight ? { maxHeight, overflowY: 'auto' } : undefined}>
        {nodes.map((node) => (
          <TraceNodeRow
            key={node.id}
            node={node}
            colors={colors}
            expanded={expanded}
            onToggle={handleToggle}
            level={0}
          />
        ))}
      </div>

      {/* Spin animation */}
      <style>{`
        @keyframes trace-spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}

'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useCallback, useRef, useMemo, useEffect } from 'react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { customSyntaxTheme, customSyntaxThemeLight, customSyntaxThemeBeige } from '@/lib/syntax-theme';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';
import type { FilePreview, ViewMode, BlameLineInfo } from './repo-viewer-types';
import { EXPAND_LINES, HIGHLIGHT_FADE_MS } from './repo-viewer-types';
import { computeFoldRegions } from './repo-viewer-hooks';

type Theme = 'dark' | 'light' | 'beige';

const getThemeStyle = (theme: Theme) => {
  switch (theme) {
    case 'light': return customSyntaxThemeLight;
    case 'beige': return customSyntaxThemeBeige;
    default: return customSyntaxTheme;
  }
};

// Virtual scroll: only render lines within this range of the viewport
const VIRTUAL_OVERSCAN = 40; // extra lines above/below viewport to render
const LINE_HEIGHT = 19.5;    // approximate line height in px

/** Scroll to a line by pixel calculation — works even if the line isn't rendered (virtual scroll). */
function scrollToLine(container: HTMLElement, targetLine: number, startLine: number, mode: 'center' | 'start' = 'center') {
  const lineIdx = targetLine - startLine;
  const targetY = lineIdx * LINE_HEIGHT;
  const viewportH = container.clientHeight;
  const scrollTo = mode === 'center'
    ? Math.max(0, targetY - viewportH / 2)
    : Math.max(0, targetY);
  container.scrollTo({ top: scrollTo, behavior: 'smooth' });
}

// --- Icons ---

function ChevronRight({ size = 11, color = 'currentColor' }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="9 18 15 12 9 6" />
    </svg>
  );
}

// --- Props ---

export interface CodeViewPanelProps {
  filePreview: FilePreview | null;
  fileLoading: boolean;
  expandLoading: 'up' | 'down' | null;
  breadcrumbs: Array<{ qualified_name: string; name: string }>;
  viewMode: ViewMode;
  blameData: BlameLineInfo[] | null;
  blameLoading: boolean;
  onExpandCode: (direction: 'up' | 'down') => void;
  onBreadcrumbClick: (index: number) => void;
  onViewModeChange: (mode: ViewMode) => void;
  onCloseFile: () => void;
  highlightLine?: number | null;
  // Symbol click — triggers sidebar in layout
  onSymbolClick?: (symbolName: string, x: number, y: number) => void;
  onCurrentLineChange?: (line: number) => void;
  // Navigation history
  canGoBack?: boolean;
  canGoForward?: boolean;
  onGoBack?: () => void;
  onGoForward?: () => void;
}

export function CodeViewPanel({
  filePreview, fileLoading, expandLoading,
  breadcrumbs, viewMode, blameData, blameLoading,
  onExpandCode, onBreadcrumbClick, onViewModeChange, onCloseFile,
  highlightLine,
  onSymbolClick,
  onCurrentLineChange,
  canGoBack, canGoForward, onGoBack, onGoForward,
}: CodeViewPanelProps) {
  const { theme } = useTheme();
  const colors = getThemeColors(theme);
  const codeContainerRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  // Selected line (cursor)
  const [selectedLine, setSelectedLine] = useState<number | null>(null);

  // Fold state
  const [foldedLines, setFoldedLines] = useState<Set<number>>(new Set());
  const [gutterHover, setGutterHover] = useState(false);
  const [wordWrap, setWordWrap] = useState(false);

  // Search state
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [searchMatchIndex, setSearchMatchIndex] = useState(0);

  // Sticky scroll
  const [stickyLines, setStickyLines] = useState<number[]>([]);
  const prevStickyRef = useRef<string>(''); // stringified prev value for comparison

  // Highlight fade
  const [highlightActive, setHighlightActive] = useState(true);

  // Virtual scroll state
  const [visibleRange, setVisibleRange] = useState({ start: 0, end: 200 });

  // Handle line click (cursor)
  const handleLineClick = useCallback((lineNum: number, _e: React.MouseEvent) => {
    setSelectedLine(lineNum);
  }, []);

  // Handle identifier click in code (for symbol navigation popup)
  const handleIdentifierClick = useCallback((e: React.MouseEvent) => {
    if (!onSymbolClick) return;
    const target = e.target as HTMLElement;
    // Only trigger on spans within code content that look like identifiers
    if (target.tagName !== 'SPAN') return;
    const text = target.textContent?.trim();
    if (!text || text.length < 2 || text.length > 60) return;
    // Check if it looks like an identifier (starts with letter/underscore, contains word chars)
    if (!/^[a-zA-Z_]\w*$/.test(text)) return;
    // Skip language keywords
    const keywords = new Set(['def', 'class', 'function', 'const', 'let', 'var', 'if', 'else', 'for', 'while', 'return', 'import', 'from', 'export', 'async', 'await', 'try', 'catch', 'finally', 'with', 'as', 'in', 'is', 'not', 'and', 'or', 'True', 'False', 'None', 'null', 'undefined', 'true', 'false', 'new', 'this', 'self', 'super', 'pub', 'fn', 'impl', 'struct', 'enum', 'trait', 'use', 'mod', 'crate', 'type', 'interface', 'extends', 'implements', 'static', 'private', 'public', 'protected', 'void', 'int', 'str', 'float', 'bool', 'string', 'number', 'boolean']);
    if (keywords.has(text)) return;
    // Stop propagation so handleLineClick doesn't close the popup immediately
    e.stopPropagation();
    const rect = target.getBoundingClientRect();
    const containerRect = codeContainerRef.current?.getBoundingClientRect();
    if (!containerRect) return;
    // Account for scroll position inside the container
    const scrollTop = codeContainerRef.current?.scrollTop || 0;
    const scrollLeft = codeContainerRef.current?.scrollLeft || 0;
    onSymbolClick(text, rect.left - containerRect.left + scrollLeft, rect.bottom - containerRect.top + scrollTop);
  }, [onSymbolClick]);

  // Reset fold state on file change
  useEffect(() => {
    setFoldedLines(new Set());
    setStickyLines([]);
    setSearchTerm('');
    setSearchOpen(false);
    setHighlightActive(true);
    setVisibleRange({ start: 0, end: 200 });
    setSelectedLine(null);
    if (filePreview?.highlightStart) {
      const timer = setTimeout(() => setHighlightActive(false), HIGHLIGHT_FADE_MS);
      return () => clearTimeout(timer);
    }
  }, [filePreview?.qualifiedName]); // eslint-disable-line react-hooks/exhaustive-deps

  // Ctrl+F and Alt+Z shortcut
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Ctrl+F: search
      if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
        const container = codeContainerRef.current;
        if (container && (container.contains(document.activeElement as Node) || container === document.activeElement)) {
          e.preventDefault();
          setSearchOpen(true);
          setTimeout(() => searchInputRef.current?.focus(), 50);
        }
      }
      // Alt+Z: toggle word wrap
      if (e.altKey && e.key === 'z') {
        e.preventDefault();
        setWordWrap(prev => !prev);
      }
      // Alt+Left: go back
      if (e.altKey && e.key === 'ArrowLeft') {
        e.preventDefault();
        onGoBack?.();
      }
      // Alt+Right: go forward
      if (e.altKey && e.key === 'ArrowRight') {
        e.preventDefault();
        onGoForward?.();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  // Total line count
  const totalLineCount = useMemo(() => {
    if (!filePreview) return 0;
    return filePreview.visibleCode.split('\n').length;
  }, [filePreview?.visibleCode]); // eslint-disable-line react-hooks/exhaustive-deps

  // Fold regions
  const foldRegions = useMemo(() => {
    if (!filePreview) return new Map<number, number>();
    return computeFoldRegions(filePreview.visibleCode, filePreview.visibleStart);
  }, [filePreview?.visibleCode, filePreview?.visibleStart]); // eslint-disable-line react-hooks/exhaustive-deps

  const hiddenLines = useMemo(() => {
    const hidden = new Set<number>();
    for (const lineNum of foldedLines) {
      const end = foldRegions.get(lineNum);
      if (end) {
        for (let l = lineNum + 1; l <= end; l++) hidden.add(l);
      }
    }
    return hidden;
  }, [foldedLines, foldRegions]);

  const toggleFold = useCallback((lineNum: number) => {
    setFoldedLines(prev => {
      const next = new Set(prev);
      if (next.has(lineNum)) next.delete(lineNum); else next.add(lineNum);
      return next;
    });
  }, []);

  // Search matches
  const searchMatches = useMemo(() => {
    if (!searchTerm || !filePreview) return [];
    const lines = filePreview.visibleCode.split('\n');
    const lower = searchTerm.toLowerCase();
    const matches: number[] = [];
    lines.forEach((line, i) => {
      if (line.toLowerCase().includes(lower)) matches.push(filePreview.visibleStart + i);
    });
    return matches;
  }, [searchTerm, filePreview]);

  const navigateSearch = useCallback((dir: 'next' | 'prev') => {
    if (searchMatches.length === 0) return;
    const newIdx = dir === 'next'
      ? (searchMatchIndex + 1) % searchMatches.length
      : (searchMatchIndex - 1 + searchMatches.length) % searchMatches.length;
    setSearchMatchIndex(newIdx);
    const line = searchMatches[newIdx];
    if (codeContainerRef.current && filePreview) {
      scrollToLine(codeContainerRef.current, line, filePreview.visibleStart);
    }
  }, [searchMatches, searchMatchIndex]);

  // Virtual scroll + sticky scroll on scroll
  // Pre-compute indents and definition lines for the current file (stable across scrolls)
  const fileAnalysis = useMemo(() => {
    if (!filePreview) return null;
    const lines = filePreview.visibleCode.split('\n');
    const indents: number[] = lines.map(line => {
      if (line.trim().length === 0) return -1;
      const match = line.match(/^(\s*)/);
      return match ? match[1].replace(/\t/g, '    ').length : 0;
    });
    // Pre-find definition lines
    const defLines: number[] = [];
    for (let i = 0; i < lines.length; i++) {
      const trimmed = lines[i].trim();
      if (trimmed.match(/^(def |class |function |async function |export |const |let |impl |fn |pub fn |struct |enum )/)) {
        defLines.push(i);
      }
    }
    return { lines, indents, defLines };
  }, [filePreview?.visibleCode]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const container = codeContainerRef.current;
    if (!container || !filePreview || !fileAnalysis) return;
    let rafId: number | null = null;
    const { lines, indents, defLines } = fileAnalysis;

    const handleScroll = () => {
      if (rafId) cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(() => {
        const scrollTop = container.scrollTop;
        const viewportHeight = container.clientHeight;
        const topLine = Math.floor(scrollTop / LINE_HEIGHT);
        const bottomLine = Math.ceil((scrollTop + viewportHeight) / LINE_HEIGHT);

        // Update virtual scroll range
        setVisibleRange({
          start: Math.max(0, topLine - VIRTUAL_OVERSCAN),
          end: Math.min(lines.length, bottomLine + VIRTUAL_OVERSCAN),
        });

        // Report current scroll line for navigation history
        const currentLine = filePreview.visibleStart + topLine;
        onCurrentLineChange?.(currentLine);

        // Sticky scroll: build scope stack using pre-computed data
        const stack: number[] = [];
        for (const di of defLines) {
          if (di > topLine) break;
          const indent = indents[di];
          if (indent === -1) continue;
          while (stack.length > 0) {
            const prevIdx = stack[stack.length - 1];
            if (indents[prevIdx] >= indent) stack.pop();
            else break;
          }
          stack.push(di);
        }
        // Only show the innermost scope (1 line) — avoids height oscillation at boundaries
        const innermost = stack.length > 0 ? [filePreview.visibleStart + stack[stack.length - 1]] : [];

        // Only update state if actually changed (prevents re-render flicker)
        const key = innermost.join(',');
        if (key !== prevStickyRef.current) {
          prevStickyRef.current = key;
          setStickyLines(innermost);
        }
      });
    };
    container.addEventListener('scroll', handleScroll, { passive: true });
    // Initial range
    handleScroll();
    return () => {
      container.removeEventListener('scroll', handleScroll);
      if (rafId) cancelAnimationFrame(rafId);
    };
  }, [filePreview, fileAnalysis, onCurrentLineChange]);

  // Scroll to highlighted line on load
  useEffect(() => {
    if (!filePreview?.highlightStart || !codeContainerRef.current) return;
    const timer = setTimeout(() => {
      if (codeContainerRef.current && filePreview) {
        scrollToLine(codeContainerRef.current, filePreview.highlightStart!, filePreview.visibleStart);
      }
    }, 100);
    return () => clearTimeout(timer);
  }, [filePreview?.highlightStart, filePreview?.qualifiedName]); // eslint-disable-line react-hooks/exhaustive-deps

  // Scroll to external highlight line
  useEffect(() => {
    if (highlightLine == null || !codeContainerRef.current || !filePreview) return;
    scrollToLine(codeContainerRef.current, highlightLine, filePreview.visibleStart);
  }, [highlightLine]); // eslint-disable-line react-hooks/exhaustive-deps

  const canExpandUp = filePreview && filePreview.visibleStart > 1;
  const canExpandDown = filePreview && (
    filePreview.totalLines ? filePreview.visibleEnd < filePreview.totalLines : true
  );

  // Blame data map by line
  const blameMap = useMemo(() => {
    if (!blameData) return new Map<number, BlameLineInfo>();
    const map = new Map<number, BlameLineInfo>();
    for (const b of blameData) map.set(b.line, b);
    return map;
  }, [blameData]);

  // --- Render ---

  if (!filePreview && !fileLoading) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100%', color: colors.textMuted, fontSize: '14px',
        fontFamily: "'Inter', -apple-system, sans-serif",
      }}>
        Select a file to view
      </div>
    );
  }

  // View mode labels with tooltips
  const viewModeLabels: Record<ViewMode, { label: string; title: string }> = {
    code: { label: 'Code', title: 'View source code' },
    blame: { label: 'Blame', title: 'Show who last modified each line (git blame)' },
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      {/* Header: breadcrumbs + actions */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: '8px',
        padding: '6px 14px', borderBottom: `1px solid ${colors.border}`,
        background: colors.bgSecondary, flexShrink: 0,
        fontFamily: "'Inter', -apple-system, sans-serif", fontSize: '13px',
      }}>
        {/* Back / Forward navigation */}
        {(canGoBack || canGoForward) && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '2px', flexShrink: 0, marginRight: '4px' }}>
            <button
              onClick={onGoBack}
              disabled={!canGoBack}
              title="Go back (Alt+Left)"
              style={{
                padding: '3px 5px', background: 'transparent', border: 'none',
                color: canGoBack ? colors.textMuted : colors.border,
                cursor: canGoBack ? 'pointer' : 'default', borderRadius: '4px',
                display: 'flex', alignItems: 'center',
              }}
              onMouseEnter={(e) => { if (canGoBack) { e.currentTarget.style.color = colors.accent; e.currentTarget.style.background = colors.bgHover; } }}
              onMouseLeave={(e) => { e.currentTarget.style.color = canGoBack ? colors.textMuted : colors.border; e.currentTarget.style.background = 'transparent'; }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="15 18 9 12 15 6" />
              </svg>
            </button>
            <button
              onClick={onGoForward}
              disabled={!canGoForward}
              title="Go forward (Alt+Right)"
              style={{
                padding: '3px 5px', background: 'transparent', border: 'none',
                color: canGoForward ? colors.textMuted : colors.border,
                cursor: canGoForward ? 'pointer' : 'default', borderRadius: '4px',
                display: 'flex', alignItems: 'center',
              }}
              onMouseEnter={(e) => { if (canGoForward) { e.currentTarget.style.color = colors.accent; e.currentTarget.style.background = colors.bgHover; } }}
              onMouseLeave={(e) => { e.currentTarget.style.color = canGoForward ? colors.textMuted : colors.border; e.currentTarget.style.background = 'transparent'; }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="9 18 15 12 9 6" />
              </svg>
            </button>
          </div>
        )}
        {/* Breadcrumbs */}
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: '2px', overflow: 'auto', whiteSpace: 'nowrap' }}>
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
          {filePreview && (
            <span style={{ marginLeft: '8px', color: colors.textDimmed, fontSize: '11px' }}>
              {filePreview.totalLines ? `${filePreview.totalLines} lines` : ''}
            </span>
          )}
        </div>

        {/* Action buttons */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '4px', flexShrink: 0 }}>
          {/* Code / Blame toggle */}
          <div style={{
            display: 'flex', borderRadius: '6px', overflow: 'hidden',
            border: `1px solid ${colors.border}`,
          }}>
            {(['code', 'blame'] as ViewMode[]).map(mode => (
              <button
                key={mode}
                onClick={() => onViewModeChange(mode)}
                title={viewModeLabels[mode].title}
                style={{
                  padding: '3px 10px', border: 'none',
                  background: viewMode === mode ? colors.accentBg : 'transparent',
                  color: viewMode === mode ? colors.accent : colors.textMuted,
                  fontSize: '11px', fontWeight: 500, cursor: 'pointer',
                  fontFamily: "'Inter', -apple-system, sans-serif",
                }}
              >
                {viewModeLabels[mode].label}
              </button>
            ))}
          </div>
          {/* Word wrap */}
          <button
            onClick={() => setWordWrap(!wordWrap)}
            title={`Word wrap: ${wordWrap ? 'ON' : 'OFF'} (Alt+Z)`}
            style={{
              padding: '3px 8px', border: `1px solid ${colors.border}`, borderRadius: '6px',
              background: wordWrap ? colors.accentBg : 'transparent',
              color: wordWrap ? colors.accent : colors.textMuted,
              fontSize: '11px', cursor: 'pointer',
              fontFamily: "'Inter', -apple-system, sans-serif",
            }}
          >
            Wrap
          </button>
          {/* Close */}
          <button
            onClick={onCloseFile}
            style={{
              padding: '2px 6px', background: 'transparent', border: 'none',
              color: colors.textMuted, fontSize: '16px', cursor: 'pointer',
              borderRadius: '4px',
            }}
            onMouseEnter={(e) => { e.currentTarget.style.color = colors.text; e.currentTarget.style.background = colors.bgHover; }}
            onMouseLeave={(e) => { e.currentTarget.style.color = colors.textMuted; e.currentTarget.style.background = 'transparent'; }}
            title="Close file"
          >
            {'\u00D7'}
          </button>
        </div>
      </div>

      {/* Ctrl+F Search bar */}
      {searchOpen && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: '6px',
          padding: '6px 14px', background: colors.bgSecondary,
          borderBottom: `1px solid ${colors.border}`, flexShrink: 0,
        }}>
          <input
            ref={searchInputRef}
            value={searchTerm}
            onChange={(e) => { setSearchTerm(e.target.value); setSearchMatchIndex(0); }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); navigateSearch(e.shiftKey ? 'prev' : 'next'); }
              if (e.key === 'Escape') { setSearchOpen(false); setSearchTerm(''); }
            }}
            placeholder="Find..."
            style={{
              flex: 1, minWidth: 0, padding: '4px 8px', fontSize: '12px',
              fontFamily: "'Inter', -apple-system, sans-serif",
              background: colors.bg, color: colors.text,
              border: `1px solid ${colors.border}`, borderRadius: '4px', outline: 'none',
            }}
          />
          <span style={{ fontSize: '11px', color: colors.textMuted, whiteSpace: 'nowrap', minWidth: '40px', textAlign: 'center' }}>
            {searchTerm ? `${searchMatches.length > 0 ? searchMatchIndex + 1 : 0}/${searchMatches.length}` : ''}
          </span>
          <button onClick={() => navigateSearch('prev')} disabled={searchMatches.length === 0}
            style={{ background: 'transparent', border: 'none', color: colors.textMuted, cursor: searchMatches.length > 0 ? 'pointer' : 'default', fontSize: '12px', padding: '2px 4px', opacity: searchMatches.length > 0 ? 1 : 0.4 }}>
            {'\u25B2'}
          </button>
          <button onClick={() => navigateSearch('next')} disabled={searchMatches.length === 0}
            style={{ background: 'transparent', border: 'none', color: colors.textMuted, cursor: searchMatches.length > 0 ? 'pointer' : 'default', fontSize: '12px', padding: '2px 4px', opacity: searchMatches.length > 0 ? 1 : 0.4 }}>
            {'\u25BC'}
          </button>
          <button onClick={() => { setSearchOpen(false); setSearchTerm(''); }}
            style={{ background: 'transparent', border: 'none', color: colors.textMuted, cursor: 'pointer', fontSize: '14px', padding: '2px 4px' }}>
            {'\u00D7'}
          </button>
        </div>
      )}

      {/* Code area */}
      {fileLoading ? (
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: colors.textMuted }}>
          <div style={{
            width: '24px', height: '24px',
            border: `2px solid ${colors.border}`, borderTopColor: colors.accent,
            borderRadius: '50%', animation: 'spin 0.8s linear infinite',
          }} />
        </div>
      ) : filePreview ? (
        <div ref={codeContainerRef} style={{ flex: 1, overflow: 'auto', position: 'relative' }} tabIndex={0}>
          {/* Sticky scroll header — always rendered with fixed max height to prevent layout shift */}
          <div style={{
            position: 'sticky', top: 0, zIndex: 5,
            background: stickyLines.length > 0 ? colors.bgSecondary : 'transparent',
            borderBottom: stickyLines.length > 0 ? `1px solid ${colors.border}` : 'none',
            fontSize: 'var(--right-font-size, 13px)',
            fontFamily: 'var(--font-jetbrains-mono), Menlo, Monaco, Consolas',
            lineHeight: '1.5',
            opacity: stickyLines.length > 0 ? 0.95 : 0,
            pointerEvents: stickyLines.length > 0 ? 'auto' : 'none',
            transition: 'opacity 0.1s ease-out',
            overflow: 'hidden',
          }}>
            {stickyLines.map(lineNum => {
                const lineIdx = lineNum - filePreview.visibleStart;
                const lines = filePreview.visibleCode.split('\n');
                const lineText = lineIdx >= 0 && lineIdx < lines.length ? lines[lineIdx] : '';
                return (
                  <div key={lineNum} onClick={() => {
                    if (codeContainerRef.current) scrollToLine(codeContainerRef.current, lineNum, filePreview.visibleStart, 'start');
                  }} style={{ display: 'flex', alignItems: 'flex-start', cursor: 'pointer' }}>
                    <span style={{ width: '16px', minWidth: '16px', flexShrink: 0 }} />
                    {viewMode === 'blame' && <span style={{ width: '200px', minWidth: '200px', flexShrink: 0 }} />}
                    <span style={{ minWidth: '40px', paddingRight: '12px', color: colors.textMuted, textAlign: 'right', userSelect: 'none', display: 'inline-block', flexShrink: 0 }}>
                      {lineNum}
                    </span>
                    <span style={{ whiteSpace: 'pre', color: colors.text, overflow: 'hidden', textOverflow: 'ellipsis' }}>{lineText}</span>
                  </div>
                );
              })}
          </div>

          {/* Load more above */}
          {canExpandUp && (
            <LoadMoreButton direction="up" onClick={() => onExpandCode('up')} loading={expandLoading === 'up'} colors={colors} />
          )}

          {/* Syntax highlighted code with virtual scrolling */}
          <div onMouseEnter={() => setGutterHover(true)} onMouseLeave={() => setGutterHover(false)}>
            <SyntaxHighlighter
              language={filePreview.language}
              style={getThemeStyle(theme as Theme)}
              showLineNumbers={false}
              wrapLines={true}
              renderer={({ rows, stylesheet, useInlineStyles }: any) => {
                const startNum = filePreview.visibleStart;
                const searchLower = searchTerm.toLowerCase();
                const currentMatchLine = searchMatches.length > 0 ? searchMatches[searchMatchIndex] : -1;

                const highlightText = (text: string, lineNum: number): React.ReactNode => {
                  if (!searchTerm || !text) return text;
                  const lower = text.toLowerCase();
                  const parts: React.ReactNode[] = [];
                  let lastIdx = 0;
                  let idx = lower.indexOf(searchLower);
                  let matchKey = 0;
                  while (idx !== -1) {
                    if (idx > lastIdx) parts.push(text.slice(lastIdx, idx));
                    parts.push(
                      <mark key={`m${matchKey++}`} style={{
                        background: lineNum === currentMatchLine ? 'rgba(255, 165, 0, 0.45)' : 'rgba(255, 255, 0, 0.25)',
                        color: 'inherit', borderRadius: '2px', padding: 0,
                      }}>{text.slice(idx, idx + searchTerm.length)}</mark>
                    );
                    lastIdx = idx + searchTerm.length;
                    idx = lower.indexOf(searchLower, lastIdx);
                  }
                  if (lastIdx < text.length) parts.push(text.slice(lastIdx));
                  return parts.length > 0 ? parts : text;
                };

                // Virtual scroll: compute non-hidden line indices
                const visibleLineIndices: number[] = [];
                for (let i = 0; i < rows.length; i++) {
                  const lineNum = startNum + i;
                  if (!hiddenLines.has(lineNum)) visibleLineIndices.push(i);
                }

                // Only render lines within the virtual viewport
                const renderStart = visibleRange.start;
                const renderEnd = Math.min(visibleLineIndices.length, visibleRange.end);
                const topPadding = renderStart * LINE_HEIGHT;
                const bottomPadding = Math.max(0, (visibleLineIndices.length - renderEnd)) * LINE_HEIGHT;

                return (
                  <>
                    {topPadding > 0 && <div style={{ height: topPadding }} />}
                    {visibleLineIndices.slice(renderStart, renderEnd).map(i => {
                      const row = rows[i];
                      const lineNum = startNum + i;
                      const isFoldStart = foldRegions.has(lineNum);
                      const isFolded = foldedLines.has(lineNum);
                      const isHighlighted = highlightActive && filePreview.highlightStart && filePreview.highlightEnd &&
                        lineNum >= filePreview.highlightStart && lineNum <= filePreview.highlightEnd;
                      const isExternalHighlight = highlightLine === lineNum;

                      const renderChildren = (node: any, key: string): React.ReactNode => {
                        if (node.type === 'text') return highlightText(node.value, lineNum);
                        const style = useInlineStyles
                          ? Object.assign({}, ...((node.properties?.className || []).map((c: string) => stylesheet[c] || {})))
                          : undefined;
                        return (
                          <span key={key} style={style} className={node.properties?.className?.join(' ')}>
                            {(node.children || []).map((child: any, j: number) => renderChildren(child, `${key}-${j}`))}
                          </span>
                        );
                      };

                      const blameInfo = viewMode === 'blame' ? blameMap.get(lineNum) : null;
                      const isSelected = selectedLine === lineNum;

                      return (
                        <div key={lineNum} data-line={lineNum}
                          onClick={(e) => handleLineClick(lineNum, e)}
                          style={{
                          display: 'flex', alignItems: 'flex-start',
                          cursor: 'text',
                          background: isSelected ? 'rgba(255, 255, 0, 0.08)'
                            : isExternalHighlight ? 'rgba(255, 200, 0, 0.15)'
                            : isHighlighted ? 'rgba(59, 130, 246, 0.15)' : 'transparent',
                          borderLeft: isSelected ? `2px solid ${colors.accent}` : '2px solid transparent',
                          transition: 'background 0.15s ease-out',
                          lineHeight: '1.5', minHeight: '1.5em',
                        }}>
                          {/* Fold gutter */}
                          <span
                            onClick={isFoldStart ? (e) => { e.stopPropagation(); toggleFold(lineNum); } : undefined}
                            style={{
                              width: '16px', minWidth: '16px',
                              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                              cursor: isFoldStart ? 'pointer' : 'default', userSelect: 'none',
                              fontSize: '10px', color: colors.textMuted,
                              opacity: isFoldStart ? (gutterHover || isFolded ? 1 : 0) : 0,
                              transition: 'opacity 0.15s', flexShrink: 0, paddingTop: '1px',
                            }}
                          >
                            {isFoldStart ? (isFolded ? '\u25B6' : '\u25BC') : ''}
                          </span>
                          {/* Blame column */}
                          {viewMode === 'blame' && (
                            <span style={{
                              width: '200px', minWidth: '200px', flexShrink: 0,
                              display: 'flex', alignItems: 'center', gap: '6px',
                              padding: '0 8px', fontSize: '11px',
                              color: colors.textDimmed, fontFamily: "'Inter', -apple-system, sans-serif",
                              borderRight: `1px solid ${colors.border}`,
                              background: blameInfo ? 'rgba(100,100,100,0.05)' : 'transparent',
                              overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis',
                            }}>
                              {blameInfo ? (
                                <>
                                  <span style={{ color: colors.accent, fontFamily: "'JetBrains Mono', monospace", fontSize: '10px' }}>{blameInfo.short_sha}</span>
                                  <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }} title={blameInfo.message}>{blameInfo.author}</span>
                                  <span style={{ flexShrink: 0, fontSize: '10px' }}>{blameInfo.date}</span>
                                </>
                              ) : blameLoading ? (
                                <span style={{ fontStyle: 'italic' }}>Loading...</span>
                              ) : null}
                            </span>
                          )}
                          {/* Line number */}
                          <span style={{
                            minWidth: '40px', paddingRight: '12px',
                            color: colors.textMuted, textAlign: 'right', userSelect: 'none',
                            display: 'inline-block', flexShrink: 0,
                            fontSize: 'var(--right-font-size, 13px)',
                            fontFamily: 'var(--font-jetbrains-mono), Menlo, Monaco, Consolas',
                          }}>
                            {lineNum}
                          </span>
                          {/* Code content */}
                          <span onClick={handleIdentifierClick} style={{ flex: 1, whiteSpace: wordWrap ? 'pre-wrap' : 'pre', wordBreak: wordWrap ? 'break-all' : undefined }}>
                            {(row.children || []).map((child: any, j: number) => renderChildren(child, `${i}-${j}`))}
                            {isFolded && (
                              <span style={{
                                color: colors.textMuted, background: colors.bgHover,
                                borderRadius: '3px', padding: '0 4px', marginLeft: '4px', fontSize: '0.85em',
                              }}>...</span>
                            )}
                          </span>
                        </div>
                      );
                    })}
                    {bottomPadding > 0 && <div style={{ height: bottomPadding }} />}
                  </>
                );
              }}
              customStyle={{
                margin: 0, padding: '8px 0',
                fontSize: 'var(--right-font-size, 13px)', lineHeight: '1.5',
                fontFamily: 'var(--font-jetbrains-mono), Menlo, Monaco, Consolas',
                background: 'transparent', borderRadius: '0',
              }}
              codeTagProps={{
                style: {
                  fontFamily: 'var(--font-jetbrains-mono), Menlo, Monaco, Consolas',
                  fontSize: 'var(--right-font-size, 13px)', lineHeight: 'inherit',
                },
              }}
            >
              {filePreview.visibleCode}
            </SyntaxHighlighter>
          </div>

          {/* Load more below */}
          {canExpandDown && (
            <LoadMoreButton direction="down" onClick={() => onExpandCode('down')} loading={expandLoading === 'down'} colors={colors} />
          )}
        </div>
      ) : null}
    </div>
  );
}

// --- LoadMoreButton ---

function LoadMoreButton({ direction, onClick, loading: isLoading, colors }: {
  direction: 'up' | 'down'; onClick: () => void; loading: boolean;
  colors: ReturnType<typeof getThemeColors>;
}) {
  return (
    <button
      onClick={onClick} disabled={isLoading}
      style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px',
        width: '100%', padding: '8px 14px',
        background: colors.bgHover, border: 'none',
        borderTop: direction === 'down' ? `1px solid ${colors.border}` : 'none',
        borderBottom: direction === 'up' ? `1px solid ${colors.border}` : 'none',
        color: colors.accent, fontSize: '12px',
        fontFamily: "'Inter', -apple-system, sans-serif", fontWeight: 500,
        cursor: isLoading ? 'wait' : 'pointer', opacity: isLoading ? 0.6 : 1,
        flexShrink: 0,
      }}
    >
      {isLoading ? 'Loading...' : `Load ${EXPAND_LINES} more lines ${direction === 'up' ? 'above' : 'below'}`}
    </button>
  );
}

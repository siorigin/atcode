'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useCallback, useRef, useMemo, useImperativeHandle, forwardRef, useEffect } from 'react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { customSyntaxTheme, customSyntaxThemeLight, customSyntaxThemeBeige } from '@/lib/syntax-theme';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';
import { apiFetch } from '@/lib/api-client';

// --- Types ---

interface FolderChildItem {
  qualified_name: string;
  name: string;
  node_type: string;
  is_package: boolean;
  child_count: number;
}

interface Breadcrumb {
  qualified_name: string;
  name: string;
}

interface FilePreview {
  qualifiedName: string;
  filePath: string;
  language: string;
  // Visible code window
  visibleCode: string;
  visibleStart: number;  // first line number shown
  visibleEnd: number;    // last line number shown
  // Highlight range (code entity)
  highlightStart?: number;
  highlightEnd?: number;
  // Full file (loaded on demand)
  fullLines?: string[];
  totalLines?: number;
}

type Theme = 'dark' | 'light' | 'beige';

export interface RepoViewerHandle {
  navigateTo: (qualifiedName: string) => Promise<void>;
}

interface RepoViewerProps {
  repoName: string;
}

// --- Constants ---

const EXPAND_LINES = 200;          // Lines to load per "load more" click
const FILE_LIST_PAGE_SIZE = 80;   // Items per page in file list
const MAX_INITIAL_LINES = 1500;   // Max lines to render initially — show full file if ≤ this
const SHOW_FULL_THRESHOLD = 1000; // Files ≤ this many lines are always shown in full
const HIGHLIGHT_FADE_MS = 3000;   // Highlight auto-fade delay

// --- Helpers ---

const getThemeStyle = (theme: Theme) => {
  switch (theme) {
    case 'light': return customSyntaxThemeLight;
    case 'beige': return customSyntaxThemeBeige;
    default: return customSyntaxTheme;
  }
};

function detectLanguage(filePath: string): string {
  const ext = filePath.split('.').pop()?.toLowerCase();
  const map: Record<string, string> = {
    py: 'python', pyx: 'python', js: 'javascript', jsx: 'javascript',
    ts: 'typescript', tsx: 'typescript', rs: 'rust', cpp: 'cpp', c: 'c',
    h: 'cpp', hpp: 'cpp', java: 'java',
    go: 'go', rb: 'ruby', sh: 'bash', yaml: 'yaml', yml: 'yaml',
    json: 'json', html: 'html', css: 'css', md: 'markdown', cu: 'cpp',
    cuh: 'cpp', cc: 'cpp',
  };
  return map[ext || ''] || 'text';
}

// --- SVG Icons ---

function FolderIcon({ color = 'currentColor', size = 16 }: { color?: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" fill={color} opacity={0.15} stroke={color} />
    </svg>
  );
}

function FileIcon({ ext, size = 16 }: { ext?: string; size?: number }) {
  // Color by file extension
  const extColors: Record<string, string> = {
    py: '#3572A5', pyx: '#3572A5',
    js: '#f1e05a', jsx: '#f1e05a', ts: '#3178c6', tsx: '#3178c6',
    rs: '#dea584', go: '#00ADD8', java: '#b07219',
    c: '#555555', cpp: '#f34b7d', h: '#555555', hpp: '#f34b7d', cu: '#76B900', cuh: '#76B900', cc: '#f34b7d',
    rb: '#701516', sh: '#89e051', bash: '#89e051',
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

function filePathToQN(filePath: string): string {
  return filePath
    .replace(/\.(py|pyx|js|jsx|ts|tsx|java|go|rs|cpp|c|h|hpp|cc|cu|cuh|rb|sh)$/, '')
    .replace(/\//g, '.');
}

// --- Code Folding ---

/** Compute foldable regions from indentation. Returns Map<startLine, endLine> (1-based). */
function computeFoldRegions(code: string, startingLine: number): Map<number, number> {
  const lines = code.split('\n');
  const regions = new Map<number, number>();

  // Compute indentation level for each line (-1 for blank lines)
  const indents: number[] = lines.map(line => {
    if (line.trim().length === 0) return -1;
    const match = line.match(/^(\s*)/);
    return match ? match[1].replace(/\t/g, '    ').length : 0;
  });

  // For each line, find the foldable region it starts
  // A line starts a fold if the next non-blank line has greater indentation
  for (let i = 0; i < lines.length; i++) {
    if (indents[i] === -1) continue; // skip blank lines

    // Find next non-blank line
    let nextNonBlank = -1;
    for (let j = i + 1; j < lines.length; j++) {
      if (indents[j] !== -1) { nextNonBlank = j; break; }
    }
    if (nextNonBlank === -1) continue;
    if (indents[nextNonBlank] <= indents[i]) continue;

    // This line starts a fold. Find where it ends.
    const baseIndent = indents[i];
    let endIdx = nextNonBlank;
    for (let j = nextNonBlank + 1; j < lines.length; j++) {
      if (indents[j] === -1) continue; // skip blank lines
      if (indents[j] <= baseIndent) break;
      endIdx = j;
    }

    // Include trailing blank lines in the fold
    while (endIdx + 1 < lines.length && indents[endIdx + 1] === -1) {
      endIdx++;
    }

    // Minimum 2 lines to fold
    if (endIdx - i >= 2) {
      regions.set(startingLine + i, startingLine + endIdx);
    }
  }

  return regions;
}

// --- Main Component ---

export const RepoViewer = forwardRef<RepoViewerHandle, RepoViewerProps>(({ repoName }, ref) => {
  const { theme } = useTheme();
  const colors = getThemeColors(theme);

  const [breadcrumbs, setBreadcrumbs] = useState<Breadcrumb[]>([]);
  const [children, setChildren] = useState<FolderChildItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [filePreview, setFilePreview] = useState<FilePreview | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const [expandLoading, setExpandLoading] = useState<'up' | 'down' | null>(null);
  const [activeQN, setActiveQN] = useState<string | null>(null);
  const [fileListLimit, setFileListLimit] = useState(FILE_LIST_PAGE_SIZE);
  const codeContainerRef = useRef<HTMLDivElement>(null);
  const [foldedLines, setFoldedLines] = useState<Set<number>>(new Set());
  const [gutterHover, setGutterHover] = useState(false);
  const [wordWrap, setWordWrap] = useState(false);
  // Ctrl+F search
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [searchMatchIndex, setSearchMatchIndex] = useState(0);
  const searchInputRef = useRef<HTMLInputElement>(null);
  // Sticky scroll
  const [stickyLines, setStickyLines] = useState<number[]>([]);

  // Graph search
  const [graphSearchResults, setGraphSearchResults] = useState<Array<{ qualified_name: string; name: string; node_type: string; file_path?: string }>>([]);
  const [graphSearchLoading, setGraphSearchLoading] = useState(false);
  const graphSearchCache = useRef<Map<string, Array<{ qualified_name: string; name: string; node_type: string; file_path?: string }>>>(new Map());
  const graphDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Calls panel
  const [callsPanelOpen, setCallsPanelOpen] = useState(false);
  const [callsData, setCallsData] = useState<{ callers: Array<{ qualified_name: string; name: string; node_type: string; file_path?: string }>; callees: Array<{ qualified_name: string; name: string; node_type: string; file_path?: string }> } | null>(null);
  const [callsLoading, setCallsLoading] = useState(false);
  const [callsQN, setCallsQN] = useState<string | null>(null);
  const callsCache = useRef<Map<string, { callers: any[]; callees: any[] }>>(new Map());

  // Caches
  const childrenCache = useRef<Map<string, FolderChildItem[]>>(new Map());
  const codeCache = useRef<Map<string, { code: string; language: string; filePath: string; startLine?: number; endLine?: number }>>(new Map());

  // Reset file list limit when children change
  useEffect(() => { setFileListLimit(FILE_LIST_PAGE_SIZE); }, [children]);

  // Auto-fade highlight after HIGHLIGHT_FADE_MS
  useEffect(() => {
    if (!filePreview?.highlightStart) return;
    const timer = setTimeout(() => {
      setFilePreview(prev => prev ? { ...prev, highlightStart: undefined, highlightEnd: undefined } : null);
    }, HIGHLIGHT_FADE_MS);
    return () => clearTimeout(timer);
  }, [filePreview?.highlightStart, filePreview?.highlightEnd]);

  // Fold regions computed from visible code
  const foldRegions = useMemo(() => {
    if (!filePreview?.visibleCode) return new Map<number, number>();
    return computeFoldRegions(filePreview.visibleCode, filePreview.visibleStart);
  }, [filePreview?.visibleCode, filePreview?.visibleStart]);

  // Reset fold state when file changes
  useEffect(() => { setFoldedLines(new Set()); }, [filePreview?.qualifiedName]);

  // Set of lines that are hidden due to folding
  const hiddenLines = useMemo(() => {
    const hidden = new Set<number>();
    for (const startLine of foldedLines) {
      const endLine = foldRegions.get(startLine);
      if (endLine) {
        for (let i = startLine + 1; i <= endLine; i++) hidden.add(i);
      }
    }
    return hidden;
  }, [foldedLines, foldRegions]);

  const toggleFold = useCallback((lineNumber: number) => {
    setFoldedLines(prev => {
      const next = new Set(prev);
      if (next.has(lineNumber)) next.delete(lineNumber);
      else next.add(lineNumber);
      return next;
    });
  }, []);

  // Alt+Z toggles word wrap, Ctrl+F opens search
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.altKey && e.key === 'z') {
        e.preventDefault();
        setWordWrap(prev => !prev);
      }
      if ((e.ctrlKey || e.metaKey) && e.key === 'f' && filePreview) {
        e.preventDefault();
        setSearchOpen(true);
        setTimeout(() => searchInputRef.current?.focus(), 0);
      }
      if (e.key === 'Escape' && searchOpen) {
        setSearchOpen(false);
        setSearchTerm('');
        setSearchMatchIndex(0);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [filePreview, searchOpen]);

  // Compute search matches (line numbers containing the term)
  const searchMatches = useMemo(() => {
    if (!searchTerm || !filePreview?.visibleCode) return [];
    const term = searchTerm.toLowerCase();
    const lines = filePreview.visibleCode.split('\n');
    const matches: number[] = [];
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].toLowerCase().includes(term)) {
        matches.push(filePreview.visibleStart + i);
      }
    }
    return matches;
  }, [searchTerm, filePreview?.visibleCode, filePreview?.visibleStart]);

  // Reset match index when matches change
  useEffect(() => { setSearchMatchIndex(0); }, [searchMatches.length]);

  // Scroll to current match
  useEffect(() => {
    if (searchMatches.length === 0 || !codeContainerRef.current) return;
    const lineNum = searchMatches[searchMatchIndex];
    const el = codeContainerRef.current.querySelector(`[data-line="${lineNum}"]`);
    el?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }, [searchMatchIndex, searchMatches]);

  // Close search when file changes
  useEffect(() => { setSearchOpen(false); setSearchTerm(''); }, [filePreview?.qualifiedName]);

  const navigateSearch = useCallback((direction: 'next' | 'prev') => {
    if (searchMatches.length === 0) return;
    setSearchMatchIndex(prev => {
      if (direction === 'next') return (prev + 1) % searchMatches.length;
      return (prev - 1 + searchMatches.length) % searchMatches.length;
    });
  }, [searchMatches.length]);

  // Sticky scroll: update on scroll
  useEffect(() => {
    const container = codeContainerRef.current;
    if (!container || !filePreview) return;
    const onScroll = () => {
      const containerRect = container.getBoundingClientRect();
      const scrollTop = containerRect.top;
      const active: number[] = [];
      // Find fold regions whose start line is above viewport top but end is below
      for (const [startLine, endLine] of foldRegions.entries()) {
        const startEl = container.querySelector(`[data-line="${startLine}"]`) as HTMLElement | null;
        const endEl = container.querySelector(`[data-line="${endLine}"]`) as HTMLElement | null;
        if (!startEl || !endEl) continue;
        const startRect = startEl.getBoundingClientRect();
        const endRect = endEl.getBoundingClientRect();
        if (startRect.top < scrollTop && endRect.bottom > scrollTop) {
          active.push(startLine);
        }
      }
      // Keep only innermost 3 scopes
      active.sort((a, b) => a - b);
      const result = active.slice(-3);
      setStickyLines(prev => {
        if (prev.length === result.length && prev.every((v, i) => v === result[i])) return prev;
        return result;
      });
    };
    container.addEventListener('scroll', onScroll, { passive: true });
    return () => container.removeEventListener('scroll', onScroll);
  }, [filePreview, foldRegions]);

  // --- API calls ---

  const fetchChildren = useCallback(async (qualifiedName: string): Promise<FolderChildItem[]> => {
    if (childrenCache.current.has(qualifiedName)) {
      return childrenCache.current.get(qualifiedName)!;
    }
    const response = await apiFetch(
      `/api/graph/node/${encodeURIComponent(repoName)}/children?qualified_name=${encodeURIComponent(qualifiedName)}`
    );
    if (!response.ok) throw new Error(`Failed to fetch children: ${response.status}`);
    const data = await response.json();
    const items = data.children || [];
    childrenCache.current.set(qualifiedName, items);
    return items;
  }, [repoName]);

  const fetchCode = useCallback(async (qualifiedName: string) => {
    if (codeCache.current.has(qualifiedName)) {
      return codeCache.current.get(qualifiedName)!;
    }
    const response = await apiFetch(
      `/api/graph/node/${encodeURIComponent(repoName)}/code?qualified_name=${encodeURIComponent(qualifiedName)}`
    );
    if (!response.ok) throw new Error(`Failed to fetch code: ${response.status}`);
    const data = await response.json();
    const result = {
      code: data.source_code || data.code || '',
      language: detectLanguage(data.file_path || data.file || qualifiedName),
      filePath: data.file_path || data.file || qualifiedName,
      startLine: data.start_line as number | undefined,
      endLine: data.end_line as number | undefined,
    };
    codeCache.current.set(qualifiedName, result);
    return result;
  }, [repoName]);

  // --- Derive current scope QN from sticky scroll context ---

  const currentScopeQN = useMemo(() => {
    if (!filePreview || stickyLines.length === 0) return null;
    const lines = filePreview.visibleCode.split('\n');
    const names: string[] = [];
    for (const lineNum of stickyLines) {
      const lineIdx = lineNum - filePreview.visibleStart;
      if (lineIdx < 0 || lineIdx >= lines.length) continue;
      const lineText = lines[lineIdx];
      // Python: class X or def X (handles async def too)
      let m = lineText.match(/^\s*(?:async\s+)?(?:class|def)\s+(\w+)/);
      if (!m) {
        // JS/TS: function X, class X
        m = lineText.match(/^\s*(?:(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class)\s+(\w+))/);
      }
      if (!m) {
        // JS/TS method shorthand: name( — but NOT return type lines (-> ...) or assignments
        if (!/->/.test(lineText) && !/=/.test(lineText)) {
          m = lineText.match(/^\s*(?:async\s+)?(\w+)\s*\(/);
        }
      }
      if (m) names.push(m[1]);
    }
    if (names.length === 0) return null;

    // Use filePreview.qualifiedName as base (it's the graph QN, always correct).
    // If the QN already ends with scope names from sticky scroll, strip them
    // to avoid duplication (e.g., navigating to a class then seeing class+method in sticky).
    let baseQN = filePreview.qualifiedName;
    const qnParts = baseQN.split('.');
    // Strip trailing parts that match the beginning of sticky scroll names
    let stripCount = 0;
    for (let i = 0; i < names.length && i < qnParts.length; i++) {
      if (qnParts[qnParts.length - 1 - i] === names[names.length - 1 - stripCount]) {
        // Don't strip — check from the start instead
        break;
      }
    }
    // Simpler: check if the last QN segment matches the first sticky name
    // If so, the QN is already at entity level — strip back to parent
    for (let i = 0; i < names.length; i++) {
      if (qnParts[qnParts.length - 1] === names[i]) {
        baseQN = qnParts.slice(0, qnParts.length - 1 - i).join('.');
        break;
      }
    }

    return baseQN + '.' + names.join('.');
  }, [filePreview, stickyLines]);

  // --- Graph search ---

  const fetchGraphSearch = useCallback(async (query: string) => {
    if (!query.trim()) {
      setGraphSearchResults([]);
      return;
    }
    const cached = graphSearchCache.current.get(query);
    if (cached) {
      setGraphSearchResults(cached);
      return;
    }
    setGraphSearchLoading(true);
    try {
      const response = await apiFetch(
        `/api/graph/node/${encodeURIComponent(repoName)}/find`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query, node_type: 'All' }) }
      );
      if (!response.ok) throw new Error(`Search failed: ${response.status}`);
      const data = await response.json();
      const results = data.nodes || data.results || [];
      graphSearchCache.current.set(query, results);
      setGraphSearchResults(results);
    } catch (err) {
      console.error('Graph search failed:', err);
      setGraphSearchResults([]);
    } finally {
      setGraphSearchLoading(false);
    }
  }, [repoName]);

  // Debounced graph search on query change (always runs alongside local filter)
  useEffect(() => {
    if (graphDebounceRef.current) clearTimeout(graphDebounceRef.current);
    if (!searchQuery.trim()) {
      setGraphSearchResults([]);
      return;
    }
    // Use cache immediately if available
    if (graphSearchCache.current.has(searchQuery)) {
      setGraphSearchResults(graphSearchCache.current.get(searchQuery)!);
      return;
    }
    graphDebounceRef.current = setTimeout(() => {
      fetchGraphSearch(searchQuery);
    }, 300);
    return () => { if (graphDebounceRef.current) clearTimeout(graphDebounceRef.current); };
  }, [searchQuery, fetchGraphSearch]);

  // --- Calls panel fetch ---

  const fetchCalls = useCallback(async (qn: string) => {
    if (callsCache.current.has(qn)) {
      const cached = callsCache.current.get(qn)!;
      setCallsData(cached);
      setCallsQN(qn);
      return;
    }
    setCallsLoading(true);
    try {
      const [outRes, inRes] = await Promise.all([
        apiFetch(`/api/graph/node/${encodeURIComponent(repoName)}/calls?qualified_name=${encodeURIComponent(qn)}&direction=outgoing`),
        apiFetch(`/api/graph/node/${encodeURIComponent(repoName)}/calls?qualified_name=${encodeURIComponent(qn)}&direction=incoming`),
      ]);
      const outData = outRes.ok ? await outRes.json() : { results: [] };
      const inData = inRes.ok ? await inRes.json() : { results: [] };
      const result = {
        callees: outData.results || [],
        callers: inData.results || [],
      };
      callsCache.current.set(qn, result);
      setCallsData(result);
      setCallsQN(qn);
    } catch (err) {
      console.error('Failed to fetch calls:', err);
      setCallsData(null);
    } finally {
      setCallsLoading(false);
    }
  }, [repoName]);

  // Auto-fetch calls when scope changes and panel is open
  useEffect(() => {
    if (!callsPanelOpen || !currentScopeQN || currentScopeQN === callsQN) return;
    fetchCalls(currentScopeQN);
  }, [callsPanelOpen, currentScopeQN, callsQN, fetchCalls]);

  // --- Scroll to a line ---

  const scrollToLine = useCallback((lineNumber: number, offsetFromVisibleStart: number) => {
    requestAnimationFrame(() => {
      const container = codeContainerRef.current;
      if (!container) return;
      const lineHeight = 21;
      // offsetFromVisibleStart = lineNumber - visibleStart
      const targetScroll = Math.max(0, (offsetFromVisibleStart - 3) * lineHeight);
      container.scrollTo({ top: targetScroll, behavior: 'smooth' });
    });
  }, []);

  // --- Load full file for expand ---

  const loadFullFile = useCallback(async (preview: FilePreview): Promise<string[] | null> => {
    if (preview.fullLines) return preview.fullLines;

    const fileQN = filePathToQN(preview.filePath);
    try {
      const result = await fetchCode(fileQN);
      const lines = result.code.split('\n');
      return lines;
    } catch {
      return null;
    }
  }, [fetchCode]);

  // --- Expand code up/down ---

  const expandCode = useCallback(async (direction: 'up' | 'down') => {
    if (!filePreview) return;
    setExpandLoading(direction);

    try {
      const fullLines = await loadFullFile(filePreview);
      if (!fullLines) {
        setExpandLoading(null);
        return;
      }

      const totalLines = fullLines.length;
      let newStart: number;
      let newEnd: number;

      if (direction === 'up') {
        newStart = Math.max(1, filePreview.visibleStart - EXPAND_LINES);
        newEnd = filePreview.visibleEnd;
      } else {
        newStart = filePreview.visibleStart;
        newEnd = Math.min(totalLines, filePreview.visibleEnd + EXPAND_LINES);
      }

      const visibleCode = fullLines.slice(newStart - 1, newEnd).join('\n');

      setFilePreview({
        ...filePreview,
        visibleCode,
        visibleStart: newStart,
        visibleEnd: newEnd,
        fullLines,
        totalLines,
      });

      // Maintain scroll position when expanding up
      if (direction === 'up' && codeContainerRef.current) {
        const container = codeContainerRef.current;
        const oldScrollTop = container.scrollTop;
        const addedLines = filePreview.visibleStart - newStart;
        requestAnimationFrame(() => {
          container.scrollTop = oldScrollTop + addedLines * 21;
        });
      }
    } finally {
      setExpandLoading(null);
    }
  }, [filePreview, loadFullFile]);

  // --- Navigation ---

  const navigateToFolder = useCallback(async (qualifiedName: string, name: string, newBreadcrumbs?: Breadcrumb[]) => {
    setLoading(true);
    setError(null);
    setFilePreview(null);
    setActiveQN(null);
    setSearchQuery('');
    try {
      const items = await fetchChildren(qualifiedName);
      setChildren(items);
      if (newBreadcrumbs) {
        setBreadcrumbs(newBreadcrumbs);
      } else {
        setBreadcrumbs(prev => [...prev, { qualified_name: qualifiedName, name }]);
      }
    } catch (err) {
      console.error('Failed to navigate:', err);
      setError(err instanceof Error ? err.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, [fetchChildren]);

  // Manual file click — show full file, no highlighting
  const handleFileClick = useCallback(async (qn: string) => {
    setActiveQN(qn);
    setFileLoading(true);
    try {
      const { code, language, filePath } = await fetchCode(qn);
      const lines = code.split('\n');
      const showFull = lines.length <= SHOW_FULL_THRESHOLD;
      const visEnd = showFull ? lines.length : Math.min(lines.length, MAX_INITIAL_LINES);
      const visibleCode = visEnd < lines.length
        ? lines.slice(0, visEnd).join('\n')
        : code;
      setFilePreview({
        qualifiedName: qn,
        filePath,
        language,
        visibleCode,
        visibleStart: 1,
        visibleEnd: visEnd,
        fullLines: lines,
        totalLines: lines.length,
      });
    } catch (err) {
      console.error('Failed to load file:', err);
      setFilePreview({
        qualifiedName: qn,
        filePath: qn,
        language: 'text',
        visibleCode: `// Failed to load: ${err instanceof Error ? err.message : 'Unknown error'}`,
        visibleStart: 1,
        visibleEnd: 1,
      });
    } finally {
      setFileLoading(false);
    }
  }, [fetchCode]);

  const handleItemClick = useCallback((item: FolderChildItem) => {
    const isFolder = item.node_type === 'Folder' || item.node_type === 'Package' || item.is_package;
    if (isFolder) {
      navigateToFolder(item.qualified_name, item.name);
    } else {
      handleFileClick(item.qualified_name);
    }
  }, [navigateToFolder, handleFileClick]);

  const handleBreadcrumbClick = useCallback((index: number) => {
    if (index < 0) return;
    const crumb = breadcrumbs[index];
    const newBreadcrumbs = breadcrumbs.slice(0, index + 1);
    navigateToFolder(crumb.qualified_name, crumb.name, newBreadcrumbs);
  }, [breadcrumbs, navigateToFolder]);

  // --- Load root on mount ---

  useEffect(() => {
    async function loadRoot() {
      setLoading(true);
      setError(null);
      try {
        const items = await fetchChildren(repoName);
        setChildren(items);
        setBreadcrumbs([{ qualified_name: repoName, name: repoName }]);
      } catch (err) {
        console.error('Failed to load root:', err);
        setError(err instanceof Error ? err.message : 'Failed to load repository');
      } finally {
        setLoading(false);
      }
    }
    if (repoName) loadRoot();
  }, [repoName, fetchChildren]);

  // --- navigateTo: lazy load — show entity snippet first, expand on demand ---

  const navigateTo = useCallback(async (qualifiedName: string) => {
    const parts = qualifiedName.split('.');
    if (parts.length === 0) return;

    setActiveQN(qualifiedName);
    setFileLoading(true);
    setSearchQuery('');

    // Start code fetch immediately
    const codePromise = fetchCode(qualifiedName).catch(() => null);

    // Expand breadcrumbs in background
    const breadcrumbPromise = (async () => {
      const newBreadcrumbs: Breadcrumb[] = [{ qualified_name: repoName, name: repoName }];
      const segmentPromises = [];
      for (let i = 1; i < parts.length; i++) {
        const segmentQN = parts.slice(0, i + 1).join('.');
        segmentPromises.push(
          fetchChildren(segmentQN)
            .then(items => ({ segmentQN, name: parts[i], items, ok: true as const }))
            .catch(() => ({ segmentQN, name: parts[i], items: [] as FolderChildItem[], ok: false as const }))
        );
      }
      const results = await Promise.all(segmentPromises);

      let lastValidChildren: FolderChildItem[] = [];
      for (const result of results) {
        if (!result.ok) break;
        newBreadcrumbs.push({ qualified_name: result.segmentQN, name: result.name });
        lastValidChildren = result.items;
      }

      if (lastValidChildren.length > 0) {
        setBreadcrumbs(newBreadcrumbs);
        setChildren(lastValidChildren);
      }
    })();

    // Wait for code
    const codeResult = await codePromise;
    if (codeResult) {
      const { code, language, filePath, startLine, endLine } = codeResult;
      const codeLines = code.split('\n');
      const totalLines = codeLines.length;
      const isFullFile = startLine === 1 && endLine && endLine >= totalLines - 1;

      if (!isFullFile && startLine && endLine) {
        // Code entity — show just the snippet with context, highlight it
        // Add a small context window around the entity
        const contextBefore = Math.min(5, startLine - 1);
        const contextAfter = 5;
        const visStart = startLine - contextBefore;
        const visEnd = endLine + contextAfter;

        // The API returns only entity lines, so for context we need the full file
        // For now, show entity lines with "load more" buttons
        setFilePreview({
          qualifiedName,
          filePath,
          language,
          visibleCode: code,
          visibleStart: startLine,
          visibleEnd: endLine,
          highlightStart: startLine,
          highlightEnd: endLine,
          // totalLines unknown until full file is loaded — we know it's at least endLine
        });
      } else {
        // Full file — show in full if small enough, otherwise cap initial render
        const showFull = totalLines <= SHOW_FULL_THRESHOLD;
        const visEnd = showFull ? totalLines : Math.min(totalLines, MAX_INITIAL_LINES);
        const visibleCode = visEnd < totalLines
          ? codeLines.slice(0, visEnd).join('\n')
          : code;
        setFilePreview({
          qualifiedName,
          filePath,
          language,
          visibleCode,
          visibleStart: 1,
          visibleEnd: visEnd,
          fullLines: codeLines,
          totalLines,
        });
      }
    } else {
      setFilePreview(null);
    }

    setFileLoading(false);
    await breadcrumbPromise;
  }, [repoName, fetchChildren, fetchCode]);

  useImperativeHandle(ref, () => ({ navigateTo }), [navigateTo]);

  // --- Search filtering + pagination ---

  const filteredChildren = useMemo(() => {
    if (!searchQuery.trim()) return children;
    const q = searchQuery.toLowerCase();
    return children.filter(item => item.name.toLowerCase().includes(q));
  }, [children, searchQuery]);

  const visibleChildren = useMemo(() => {
    return filteredChildren.slice(0, fileListLimit);
  }, [filteredChildren, fileListLimit]);

  const hasMoreChildren = filteredChildren.length > fileListLimit;

  // --- "Load more" button component ---

  const LoadMoreButton = useCallback(({ direction, onClick, loading: isLoading }: {
    direction: 'up' | 'down';
    onClick: () => void;
    loading: boolean;
  }) => (
    <button
      onClick={onClick}
      disabled={isLoading}
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '6px',
        width: '100%',
        padding: '8px 14px',
        background: colors.bgHover,
        border: 'none',
        borderTop: direction === 'down' ? `1px solid ${colors.border}` : 'none',
        borderBottom: direction === 'up' ? `1px solid ${colors.border}` : 'none',
        color: colors.accent,
        fontSize: '13px',
        fontFamily: "'Inter', -apple-system, sans-serif",
        fontWeight: 500,
        cursor: isLoading ? 'wait' : 'pointer',
        transition: 'background 0.15s',
        flexShrink: 0,
        opacity: isLoading ? 0.6 : 1,
      }}
      onMouseEnter={(e) => { if (!isLoading) e.currentTarget.style.background = colors.accentBg; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = colors.bgHover; }}
    >
      {isLoading ? (
        <span>Loading...</span>
      ) : (
        <>
          <span style={{ fontSize: '11px' }}>{direction === 'up' ? '\u25B2' : '\u25BC'}</span>
          <span>Load {EXPAND_LINES} more lines {direction === 'up' ? 'above' : 'below'}</span>
        </>
      )}
    </button>
  ), [colors]);

  // --- Render ---

  const canExpandUp = filePreview && filePreview.visibleStart > 1;
  const canExpandDown = filePreview && (
    filePreview.totalLines ? filePreview.visibleEnd < filePreview.totalLines : true
  );

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      overflow: 'hidden',
    }}>
      {/* Breadcrumb navigation bar */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: '2px',
        padding: '8px 14px',
        borderBottom: `1px solid ${colors.border}`,
        background: colors.bgSecondary,
        flexShrink: 0,
        overflow: 'auto',
        whiteSpace: 'nowrap',
        fontSize: '13px',
        fontFamily: "'Inter', -apple-system, sans-serif",
      }}>
        {breadcrumbs.map((crumb, idx) => (
          <React.Fragment key={crumb.qualified_name}>
            {idx > 0 && (
              <span style={{ color: colors.textDimmed, margin: '0 1px', flexShrink: 0, display: 'flex', alignItems: 'center' }}>
                <ChevronRight size={11} color={colors.textDimmed} />
              </span>
            )}
            <button
              onClick={() => handleBreadcrumbClick(idx)}
              style={{
                background: idx === breadcrumbs.length - 1 ? colors.accentBg : 'transparent',
                color: idx === breadcrumbs.length - 1 ? colors.accent : colors.textMuted,
                border: 'none',
                borderRadius: '6px',
                padding: '3px 8px',
                cursor: 'pointer',
                fontSize: '13px',
                fontFamily: 'inherit',
                fontWeight: idx === breadcrumbs.length - 1 ? 600 : 450,
                transition: 'all 0.15s',
                flexShrink: 0,
              }}
              onMouseEnter={(e) => {
                if (idx !== breadcrumbs.length - 1) {
                  e.currentTarget.style.color = colors.text;
                  e.currentTarget.style.background = colors.bgHover;
                }
              }}
              onMouseLeave={(e) => {
                if (idx !== breadcrumbs.length - 1) {
                  e.currentTarget.style.color = colors.textMuted;
                  e.currentTarget.style.background = 'transparent';
                }
              }}
            >
              {crumb.name}
            </button>
          </React.Fragment>
        ))}
      </div>

      {/* File list with search — hidden when viewing file */}
      {!filePreview && (
        <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          {/* Search bar */}
          {(children.length > 0) && !loading && (
            <div style={{ padding: '8px 10px', flexShrink: 0, borderBottom: `1px solid ${colors.border}` }}>
              <div style={{ position: 'relative' }}>
                <svg
                  width="14" height="14" viewBox="0 0 24 24" fill="none"
                  stroke={colors.textDimmed} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                  style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }}
                >
                  <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
                </svg>
                <input
                  type="text"
                  placeholder="Search files & graph..."
                  value={searchQuery}
                  onChange={(e) => { setSearchQuery(e.target.value); setFileListLimit(FILE_LIST_PAGE_SIZE); }}
                  style={{
                    width: '100%',
                    padding: '7px 10px 7px 32px',
                    fontSize: '13px',
                    fontFamily: "'Inter', -apple-system, sans-serif",
                    background: colors.bg,
                    color: colors.text,
                    border: `1px solid ${colors.border}`,
                    borderRadius: '8px',
                    outline: 'none',
                    transition: 'border-color 0.15s, box-shadow 0.15s',
                    boxSizing: 'border-box',
                  }}
                  onFocus={(e) => { e.currentTarget.style.borderColor = colors.accent; e.currentTarget.style.boxShadow = `0 0 0 3px ${colors.accentBg}`; }}
                  onBlur={(e) => { e.currentTarget.style.borderColor = colors.border; e.currentTarget.style.boxShadow = 'none'; }}
                />
              </div>
            </div>
          )}

          {/* Scrollable file list / graph search results */}
          <div style={{ flex: 1, overflow: 'auto' }}>
            {loading ? (
              <div style={{ padding: '32px', textAlign: 'center', color: colors.textMuted }}>
                <div style={{
                  width: '24px',
                  height: '24px',
                  border: `2px solid ${colors.border}`,
                  borderTopColor: colors.accent,
                  borderRadius: '50%',
                  animation: 'spin 0.8s linear infinite',
                  margin: '0 auto 10px',
                }} />
                <span style={{ fontSize: '13px' }}>Loading...</span>
              </div>
            ) : error ? (
              <div style={{ padding: '24px', textAlign: 'center', color: '#ef4444', fontSize: '14px' }}>
                {error}
              </div>
            ) : filteredChildren.length === 0 && graphSearchResults.length === 0 && !graphSearchLoading ? (
              <div style={{ padding: '24px', textAlign: 'center', color: colors.textMuted, fontSize: '14px' }}>
                {searchQuery ? 'No results' : 'Empty folder'}
              </div>
            ) : (
              <>
                {/* Local file matches */}
                {filteredChildren.length > 0 && (
                  <>
                    {searchQuery.trim() && (
                      <div style={{
                        padding: '4px 14px', fontSize: '11px', fontWeight: 600,
                        color: colors.textMuted, background: colors.bgHover,
                        borderBottom: `1px solid ${colors.border}`,
                        letterSpacing: '0.3px', textTransform: 'uppercase',
                      }}>
                        Files ({filteredChildren.length})
                      </div>
                    )}
                    {visibleChildren.map((item, idx) => {
                      const isFolder = item.node_type === 'Folder' || item.node_type === 'Package' || item.is_package;
                      const isActive = activeQN === item.qualified_name;
                      const fileExt = item.name.split('.').pop()?.toLowerCase();
                      const prevItem = idx > 0 ? visibleChildren[idx - 1] : null;
                      const prevIsFolder = prevItem ? (prevItem.node_type === 'Folder' || prevItem.node_type === 'Package' || prevItem.is_package) : false;
                      const showDivider = !isFolder && prevIsFolder;

                      return (
                        <React.Fragment key={item.qualified_name}>
                          {showDivider && (
                            <div style={{ height: 1, background: colors.border, margin: '4px 12px', opacity: 0.6 }} />
                          )}
                          <div
                            onClick={() => handleItemClick(item)}
                            style={{
                              display: 'flex', alignItems: 'center', gap: '10px',
                              padding: '7px 14px', cursor: 'pointer', fontSize: '14px',
                              fontFamily: "'Inter', -apple-system, sans-serif",
                              background: isActive ? colors.accentBg : 'transparent',
                              color: isActive ? colors.accent : colors.text,
                              borderLeft: isActive ? `3px solid ${colors.accent}` : '3px solid transparent',
                              transition: 'background 0.12s, border-color 0.12s', userSelect: 'none',
                            }}
                            onMouseEnter={(e) => { if (!isActive) e.currentTarget.style.background = colors.bgHover; }}
                            onMouseLeave={(e) => { if (!isActive) e.currentTarget.style.background = 'transparent'; }}
                            title={item.qualified_name}
                          >
                            <span style={{ flexShrink: 0, display: 'flex', alignItems: 'center' }}>
                              {isFolder ? <FolderIcon color={colors.accent} size={18} /> : <FileIcon ext={fileExt} size={18} />}
                            </span>
                            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: isFolder ? 500 : 400 }}>
                              {item.name}
                            </span>
                            {isFolder && item.child_count > 0 && (
                              <span style={{
                                marginLeft: 'auto', fontSize: '11px', color: colors.textMuted,
                                background: colors.bgHover, padding: '1px 7px', borderRadius: '10px',
                                flexShrink: 0, fontWeight: 500, fontFamily: "'Inter', sans-serif",
                              }}>
                                {item.child_count}
                              </span>
                            )}
                            {isFolder && (
                              <span style={{
                                marginLeft: item.child_count > 0 ? '0' : 'auto', flexShrink: 0,
                                display: 'flex', alignItems: 'center', color: colors.textDimmed,
                              }}>
                                <ChevronRight size={13} color={colors.textDimmed} />
                              </span>
                            )}
                          </div>
                        </React.Fragment>
                      );
                    })}
                    {hasMoreChildren && (
                      <button
                        onClick={() => setFileListLimit(prev => prev + FILE_LIST_PAGE_SIZE)}
                        style={{
                          display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px',
                          width: '100%', padding: '10px 14px', background: 'transparent', border: 'none',
                          borderTop: `1px solid ${colors.border}`, color: colors.accent, fontSize: '13px',
                          fontFamily: "'Inter', -apple-system, sans-serif", fontWeight: 500,
                          cursor: 'pointer', transition: 'background 0.15s',
                        }}
                        onMouseEnter={(e) => { e.currentTarget.style.background = colors.bgHover; }}
                        onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
                      >
                        Show more ({filteredChildren.length - fileListLimit} remaining)
                      </button>
                    )}
                  </>
                )}

                {/* Graph search results — shown below local results when query is active */}
                {searchQuery.trim() && (
                  <>
                    <div style={{
                      padding: '4px 14px', fontSize: '11px', fontWeight: 600,
                      color: colors.textMuted, background: colors.bgHover,
                      borderTop: filteredChildren.length > 0 ? `1px solid ${colors.border}` : 'none',
                      borderBottom: `1px solid ${colors.border}`,
                      letterSpacing: '0.3px', textTransform: 'uppercase',
                      display: 'flex', alignItems: 'center', gap: '6px',
                    }}>
                      <span>Graph</span>
                      {graphSearchLoading && (
                        <span style={{
                          width: '10px', height: '10px',
                          border: `1.5px solid ${colors.border}`, borderTopColor: colors.accent,
                          borderRadius: '50%', animation: 'spin 0.6s linear infinite',
                          display: 'inline-block',
                        }} />
                      )}
                      {!graphSearchLoading && <span>({graphSearchResults.length})</span>}
                    </div>
                    {graphSearchLoading ? (
                      <div style={{ padding: '12px', textAlign: 'center', color: colors.textMuted, fontSize: '12px' }}>
                        Searching graph...
                      </div>
                    ) : graphSearchResults.length === 0 ? (
                      <div style={{ padding: '12px', textAlign: 'center', color: colors.textMuted, fontSize: '12px' }}>
                        No graph results
                      </div>
                    ) : (
                      graphSearchResults.map((item) => {
                        const typeColors: Record<string, string> = {
                          Class: '#e5a00d', Function: '#6f42c1', Method: '#6f42c1',
                          Module: '#3178c6', File: '#8b949e', Package: colors.accent,
                        };
                        const typeColor = typeColors[item.node_type] || colors.textMuted;
                        return (
                          <div
                            key={item.qualified_name}
                            onClick={() => navigateTo(item.qualified_name)}
                            style={{
                              display: 'flex', alignItems: 'center', gap: '8px',
                              padding: '7px 14px', cursor: 'pointer', fontSize: '13px',
                              fontFamily: "'Inter', -apple-system, sans-serif",
                              transition: 'background 0.12s', userSelect: 'none',
                            }}
                            onMouseEnter={(e) => { e.currentTarget.style.background = colors.bgHover; }}
                            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
                            title={item.qualified_name}
                          >
                            <span style={{
                              fontSize: '10px', fontWeight: 600, color: typeColor,
                              background: `${typeColor}18`, padding: '1px 5px',
                              borderRadius: '4px', flexShrink: 0, textTransform: 'uppercase',
                              letterSpacing: '0.3px',
                            }}>
                              {item.node_type?.slice(0, 3) || '?'}
                            </span>
                            <span style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {item.name}
                            </span>
                            {item.file_path && (
                              <span style={{
                                marginLeft: 'auto', fontSize: '11px', color: colors.textDimmed,
                                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                                maxWidth: '45%', flexShrink: 0, direction: 'rtl', textAlign: 'left',
                              }}>
                                {item.file_path}
                              </span>
                            )}
                          </div>
                        );
                      })
                    )}
                  </>
                )}
              </>
            )}
          </div>
        </div>
      )}

      {/* File code preview with lazy loading */}
      {filePreview && (
        <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          {/* File path header */}
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 8,
            padding: '8px 14px',
            fontSize: '13px',
            fontFamily: 'var(--font-jetbrains-mono), monospace',
            color: colors.textMuted,
            background: colors.bgSecondary,
            borderBottom: `1px solid ${colors.border}`,
            flexShrink: 0,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, overflow: 'hidden', minWidth: 0 }}>
              <FileIcon ext={filePreview.filePath.split('.').pop()?.toLowerCase()} size={16} />
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: 500 }}>
                {filePreview.filePath}
              </span>
              {filePreview.highlightStart && filePreview.highlightEnd && (
                <span style={{
                  color: colors.accent,
                  fontSize: 11,
                  fontWeight: 600,
                  background: colors.accentBg,
                  padding: '1px 6px',
                  borderRadius: 6,
                  flexShrink: 0,
                }}>
                  L{filePreview.highlightStart}-{filePreview.highlightEnd}
                </span>
              )}
              {filePreview.totalLines && (
                <span style={{
                  fontSize: 11,
                  opacity: 0.6,
                  flexShrink: 0,
                }}>
                  {filePreview.visibleStart}-{filePreview.visibleEnd} / {filePreview.totalLines}
                </span>
              )}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '4px', flexShrink: 0 }}>
              <button
                onClick={() => { setCallsPanelOpen(prev => !prev); }}
                style={{
                  background: callsPanelOpen ? colors.accentBg : 'transparent',
                  border: 'none',
                  color: callsPanelOpen ? colors.accent : colors.textMuted,
                  cursor: 'pointer',
                  fontSize: '14px',
                  lineHeight: 1,
                  padding: '3px 7px',
                  borderRadius: '6px',
                  flexShrink: 0,
                  transition: 'all 0.12s',
                  fontFamily: 'inherit',
                }}
                onMouseEnter={(e) => { if (!callsPanelOpen) { e.currentTarget.style.color = colors.text; e.currentTarget.style.background = colors.bgHover; } }}
                onMouseLeave={(e) => { if (!callsPanelOpen) { e.currentTarget.style.color = colors.textMuted; e.currentTarget.style.background = 'transparent'; } }}
                title="Toggle calls/callers panel"
              >
                ⇄
              </button>
              <button
                onClick={() => { setFilePreview(null); setActiveQN(null); setCallsPanelOpen(false); setCallsData(null); setCallsQN(null); }}
              style={{
                background: 'transparent',
                border: 'none',
                color: colors.textMuted,
                cursor: 'pointer',
                fontSize: '18px',
                lineHeight: 1,
                padding: '2px 6px',
                borderRadius: '6px',
                flexShrink: 0,
                transition: 'all 0.12s',
              }}
              onMouseEnter={(e) => { e.currentTarget.style.color = colors.text; e.currentTarget.style.background = colors.bgHover; }}
              onMouseLeave={(e) => { e.currentTarget.style.color = colors.textMuted; e.currentTarget.style.background = 'transparent'; }}
              title="Back to file list"
            >
              {'\u00D7'}
            </button>
            </div>
          </div>

          {/* Ctrl+F Search bar */}
          {searchOpen && (
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '6px 14px',
              background: colors.bgSecondary,
              borderBottom: `1px solid ${colors.border}`,
              flexShrink: 0,
            }}>
              <input
                ref={searchInputRef}
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    navigateSearch(e.shiftKey ? 'prev' : 'next');
                  }
                  if (e.key === 'Escape') {
                    setSearchOpen(false);
                    setSearchTerm('');
                    setSearchMatchIndex(0);
                  }
                }}
                placeholder="Find..."
                style={{
                  flex: 1,
                  minWidth: 0,
                  padding: '4px 8px',
                  fontSize: '13px',
                  fontFamily: "'Inter', -apple-system, sans-serif",
                  background: colors.bg,
                  color: colors.text,
                  border: `1px solid ${colors.border}`,
                  borderRadius: '4px',
                  outline: 'none',
                }}
              />
              <span style={{
                fontSize: '12px',
                color: colors.textMuted,
                whiteSpace: 'nowrap',
                minWidth: '48px',
                textAlign: 'center',
              }}>
                {searchTerm ? `${searchMatches.length > 0 ? searchMatchIndex + 1 : 0}/${searchMatches.length}` : ''}
              </span>
              <button
                onClick={() => navigateSearch('prev')}
                disabled={searchMatches.length === 0}
                style={{
                  background: 'transparent', border: 'none', color: colors.textMuted,
                  cursor: searchMatches.length > 0 ? 'pointer' : 'default',
                  fontSize: '14px', padding: '2px 4px', borderRadius: '3px',
                  opacity: searchMatches.length > 0 ? 1 : 0.4,
                }}
                title="Previous match (Shift+Enter)"
              >▲</button>
              <button
                onClick={() => navigateSearch('next')}
                disabled={searchMatches.length === 0}
                style={{
                  background: 'transparent', border: 'none', color: colors.textMuted,
                  cursor: searchMatches.length > 0 ? 'pointer' : 'default',
                  fontSize: '14px', padding: '2px 4px', borderRadius: '3px',
                  opacity: searchMatches.length > 0 ? 1 : 0.4,
                }}
                title="Next match (Enter)"
              >▼</button>
              <button
                onClick={() => { setSearchOpen(false); setSearchTerm(''); setSearchMatchIndex(0); }}
                style={{
                  background: 'transparent', border: 'none', color: colors.textMuted,
                  cursor: 'pointer', fontSize: '16px', padding: '2px 4px', borderRadius: '3px',
                }}
                title="Close (Escape)"
              >×</button>
            </div>
          )}

          {fileLoading ? (
            <div style={{ padding: '32px', textAlign: 'center', color: colors.textMuted }}>
              <div style={{
                width: '24px',
                height: '24px',
                border: `2px solid ${colors.border}`,
                borderTopColor: colors.accent,
                borderRadius: '50%',
                animation: 'spin 0.6s linear infinite',
                margin: '0 auto 10px',
              }} />
              <span style={{ fontSize: '13px' }}>Loading code...</span>
            </div>
          ) : (
            <div ref={codeContainerRef} style={{ overflow: 'auto', flex: 1, position: 'relative' }}>
              {/* Sticky scroll header */}
              {stickyLines.length > 0 && filePreview && (
                <div style={{
                  position: 'sticky',
                  top: 0,
                  zIndex: 5,
                  background: colors.bgSecondary,
                  borderBottom: `1px solid ${colors.border}`,
                  fontSize: 'var(--right-font-size, 13px)',
                  fontFamily: 'var(--font-jetbrains-mono), Menlo, Monaco, Consolas',
                  lineHeight: '1.5',
                  opacity: 0.95,
                }}>
                  {stickyLines.map(lineNum => {
                    const lineIdx = lineNum - filePreview.visibleStart;
                    const lines = filePreview.visibleCode.split('\n');
                    const lineText = lineIdx >= 0 && lineIdx < lines.length ? lines[lineIdx] : '';
                    return (
                      <div
                        key={lineNum}
                        onClick={() => {
                          const el = codeContainerRef.current?.querySelector(`[data-line="${lineNum}"]`);
                          el?.scrollIntoView({ block: 'start', behavior: 'smooth' });
                        }}
                        style={{
                          display: 'flex',
                          alignItems: 'flex-start',
                          cursor: 'pointer',
                          padding: '0',
                        }}
                      >
                        <span style={{
                          width: '16px', minWidth: '16px', flexShrink: 0,
                        }} />
                        <span style={{
                          minWidth: '40px', paddingRight: '12px',
                          color: colors.textMuted, textAlign: 'right',
                          userSelect: 'none', display: 'inline-block', flexShrink: 0,
                        }}>
                          {lineNum}
                        </span>
                        <span style={{ whiteSpace: 'pre', color: colors.text, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {lineText}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
              {/* Load more above */}
              {canExpandUp && (
                <LoadMoreButton
                  direction="up"
                  onClick={() => expandCode('up')}
                  loading={expandLoading === 'up'}
                />
              )}

              <div
                onMouseEnter={() => setGutterHover(true)}
                onMouseLeave={() => setGutterHover(false)}
              >
              <SyntaxHighlighter
                language={filePreview.language}
                style={getThemeStyle(theme)}
                showLineNumbers={false}
                wrapLines={true}
                renderer={({ rows, stylesheet, useInlineStyles }: any) => {
                  const startNum = filePreview.visibleStart;
                  const searchLower = searchTerm.toLowerCase();
                  const currentMatchLine = searchMatches.length > 0 ? searchMatches[searchMatchIndex] : -1;

                  // Highlight search matches within text
                  const highlightText = (text: string, lineNum: number): React.ReactNode => {
                    if (!searchTerm || !text) return text;
                    const lower = text.toLowerCase();
                    const parts: React.ReactNode[] = [];
                    let lastIdx = 0;
                    let idx = lower.indexOf(searchLower);
                    let matchKey = 0;
                    while (idx !== -1) {
                      if (idx > lastIdx) parts.push(text.slice(lastIdx, idx));
                      const isCurrentMatch = lineNum === currentMatchLine;
                      parts.push(
                        <mark key={`m${matchKey++}`} style={{
                          background: isCurrentMatch ? 'rgba(255, 165, 0, 0.45)' : 'rgba(255, 255, 0, 0.25)',
                          color: 'inherit',
                          borderRadius: '2px',
                          padding: 0,
                        }}>
                          {text.slice(idx, idx + searchTerm.length)}
                        </mark>
                      );
                      lastIdx = idx + searchTerm.length;
                      idx = lower.indexOf(searchLower, lastIdx);
                    }
                    if (lastIdx < text.length) parts.push(text.slice(lastIdx));
                    return parts.length > 0 ? parts : text;
                  };

                  return rows.map((row: any, i: number) => {
                    const lineNum = startNum + i;
                    const isFoldStart = foldRegions.has(lineNum);
                    const isFolded = foldedLines.has(lineNum);
                    const isHidden = hiddenLines.has(lineNum);
                    const isHighlighted = filePreview.highlightStart && filePreview.highlightEnd &&
                      lineNum >= filePreview.highlightStart && lineNum <= filePreview.highlightEnd;

                    if (isHidden) return null;

                    // Render token children with search highlighting
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

                    return (
                      <div
                        key={lineNum}
                        data-line={lineNum}
                        style={{
                          display: 'flex',
                          alignItems: 'flex-start',
                          background: isHighlighted ? 'rgba(59, 130, 246, 0.15)' : 'transparent',
                          transition: 'background 0.6s ease-out',
                          lineHeight: '1.5',
                          minHeight: '1.5em',
                        }}
                      >
                        {/* Fold gutter */}
                        <span
                          onClick={isFoldStart ? (e) => { e.stopPropagation(); toggleFold(lineNum); } : undefined}
                          style={{
                            width: '16px',
                            minWidth: '16px',
                            display: 'inline-flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            cursor: isFoldStart ? 'pointer' : 'default',
                            userSelect: 'none',
                            fontSize: '10px',
                            color: colors.textMuted,
                            opacity: isFoldStart ? (gutterHover || isFolded ? 1 : 0) : 0,
                            transition: 'opacity 0.15s',
                            flexShrink: 0,
                            paddingTop: '1px',
                          }}
                        >
                          {isFoldStart ? (isFolded ? '▶' : '▼') : ''}
                        </span>
                        {/* Line number */}
                        <span style={{
                          minWidth: '40px',
                          paddingRight: '12px',
                          color: colors.textMuted,
                          textAlign: 'right',
                          userSelect: 'none',
                          display: 'inline-block',
                          flexShrink: 0,
                          fontSize: 'var(--right-font-size, 13px)',
                          fontFamily: 'var(--font-jetbrains-mono), Menlo, Monaco, Consolas',
                        }}>
                          {lineNum}
                        </span>
                        {/* Code content */}
                        <span style={{ flex: 1, whiteSpace: wordWrap ? 'pre-wrap' : 'pre', wordBreak: wordWrap ? 'break-all' : undefined }}>
                          {(row.children || []).map((child: any, j: number) => renderChildren(child, `${i}-${j}`))}
                          {isFolded && (
                            <span style={{
                              color: colors.textMuted,
                              background: colors.bgHover,
                              borderRadius: '3px',
                              padding: '0 4px',
                              marginLeft: '4px',
                              fontSize: '0.85em',
                            }}>
                              ...
                            </span>
                          )}
                        </span>
                      </div>
                    );
                  });
                }}
                customStyle={{
                  margin: 0,
                  padding: '8px 0',
                  fontSize: 'var(--right-font-size, 13px)',
                  lineHeight: '1.5',
                  fontFamily: 'var(--font-jetbrains-mono), Menlo, Monaco, Consolas',
                  background: 'transparent',
                  borderRadius: '0',
                }}
                codeTagProps={{
                  style: {
                    fontFamily: 'var(--font-jetbrains-mono), Menlo, Monaco, Consolas',
                    fontSize: 'var(--right-font-size, 13px)',
                    lineHeight: 'inherit',
                  },
                }}
              >
                {filePreview.visibleCode}
              </SyntaxHighlighter>
              </div>

              {/* Load more below */}
              {canExpandDown && (
                <LoadMoreButton
                  direction="down"
                  onClick={() => expandCode('down')}
                  loading={expandLoading === 'down'}
                />
              )}
            </div>
          )}

          {/* Calls/Callers panel */}
          {callsPanelOpen && filePreview && (
            <div style={{
              flexShrink: 0,
              maxHeight: '200px',
              overflow: 'auto',
              borderTop: `1px solid ${colors.border}`,
              background: colors.bgSecondary,
              fontSize: '13px',
              fontFamily: "'Inter', -apple-system, sans-serif",
            }}>
              {/* Panel header */}
              <div style={{
                padding: '5px 14px',
                fontSize: '11px',
                fontWeight: 600,
                color: colors.textMuted,
                background: colors.bg,
                borderBottom: `1px solid ${colors.border}`,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}>
                {currentScopeQN ? (
                  <span title={currentScopeQN}>⇄ {currentScopeQN.split('.').slice(-2).join('.')}</span>
                ) : (
                  <span style={{ fontStyle: 'italic' }}>Scroll to a function/class to see calls</span>
                )}
              </div>

              {callsLoading ? (
                <div style={{ padding: '12px', textAlign: 'center', color: colors.textMuted }}>
                  <span style={{ fontSize: '12px' }}>Loading calls...</span>
                </div>
              ) : !callsData || !currentScopeQN ? (
                <div style={{ padding: '12px', textAlign: 'center', color: colors.textMuted, fontSize: '12px' }}>
                  {!currentScopeQN ? 'Scroll into a function or class scope' : 'No call data'}
                </div>
              ) : (
                <div style={{ display: 'flex', gap: 0 }}>
                  {/* Callees */}
                  <div style={{ flex: 1, borderRight: `1px solid ${colors.border}`, minWidth: 0 }}>
                    <div style={{
                      padding: '4px 10px', fontSize: '11px', fontWeight: 600,
                      color: colors.accent, background: colors.bgHover,
                      borderBottom: `1px solid ${colors.border}`,
                    }}>
                      Calls → ({callsData.callees.length})
                    </div>
                    {callsData.callees.length === 0 ? (
                      <div style={{ padding: '8px 10px', color: colors.textDimmed, fontSize: '12px' }}>None</div>
                    ) : callsData.callees.map((item: any) => (
                      <div
                        key={item.qualified_name}
                        onClick={() => navigateTo(item.qualified_name)}
                        style={{
                          padding: '4px 10px', cursor: 'pointer',
                          display: 'flex', alignItems: 'center', gap: '6px',
                          transition: 'background 0.1s',
                        }}
                        onMouseEnter={(e) => { e.currentTarget.style.background = colors.bgHover; }}
                        onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
                        title={item.qualified_name}
                      >
                        <span style={{
                          fontSize: '9px', fontWeight: 600, color: '#6f42c1',
                          background: '#6f42c118', padding: '0px 4px',
                          borderRadius: '3px', flexShrink: 0, textTransform: 'uppercase',
                        }}>
                          {(item.node_type || 'fn').slice(0, 3)}
                        </span>
                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: '12px' }}>
                          {item.name}
                        </span>
                        {item.file_path && (
                          <span style={{ marginLeft: 'auto', fontSize: '10px', color: colors.textDimmed, flexShrink: 0 }}>
                            {item.file_path.split('/').pop()}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                  {/* Callers */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{
                      padding: '4px 10px', fontSize: '11px', fontWeight: 600,
                      color: '#e5a00d', background: colors.bgHover,
                      borderBottom: `1px solid ${colors.border}`,
                    }}>
                      ← Called by ({callsData.callers.length})
                    </div>
                    {callsData.callers.length === 0 ? (
                      <div style={{ padding: '8px 10px', color: colors.textDimmed, fontSize: '12px' }}>None</div>
                    ) : callsData.callers.map((item: any) => (
                      <div
                        key={item.qualified_name}
                        onClick={() => navigateTo(item.qualified_name)}
                        style={{
                          padding: '4px 10px', cursor: 'pointer',
                          display: 'flex', alignItems: 'center', gap: '6px',
                          transition: 'background 0.1s',
                        }}
                        onMouseEnter={(e) => { e.currentTarget.style.background = colors.bgHover; }}
                        onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
                        title={item.qualified_name}
                      >
                        <span style={{
                          fontSize: '9px', fontWeight: 600, color: '#e5a00d',
                          background: '#e5a00d18', padding: '0px 4px',
                          borderRadius: '3px', flexShrink: 0, textTransform: 'uppercase',
                        }}>
                          {(item.node_type || 'fn').slice(0, 3)}
                        </span>
                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: '12px' }}>
                          {item.name}
                        </span>
                        {item.file_path && (
                          <span style={{ marginLeft: 'auto', fontSize: '10px', color: colors.textDimmed, flexShrink: 0 }}>
                            {item.file_path.split('/').pop()}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

        </div>
      )}
    </div>
  );
});

RepoViewer.displayName = 'RepoViewer';

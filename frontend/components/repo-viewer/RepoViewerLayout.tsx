'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, {
  useState, useCallback, useRef, useMemo,
  useImperativeHandle, forwardRef, useEffect,
} from 'react';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';
import type {
  FolderChildItem, Breadcrumb, ViewMode, LayoutMode, RepoViewerHandle,
} from './repo-viewer-types';
import {
  SIDEBAR_LEFT_DEFAULT, SIDEBAR_LEFT_COMPACT,
  BREAKPOINT_FULL, BREAKPOINT_COMPACT,
} from './repo-viewer-types';
import {
  useFileTree, useCodeContent,
  useBlame, useBranches, useGraphSearch, useSymbolNavigation, filePathToQN,
} from './repo-viewer-hooks';
import type { SymbolNavData, SymbolNavResult } from './repo-viewer-hooks';
import { detectLanguage } from './repo-viewer-hooks';
import { FileTreePanel } from './FileTreePanel';
import { CodeViewPanel } from './CodeViewPanel';
import { apiFetch } from '@/lib/api-client';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { customSyntaxTheme, customSyntaxThemeLight, customSyntaxThemeBeige } from '@/lib/syntax-theme';

// --- SVG Icons ---

function FileTreeIcon({ size = 16, color = 'currentColor' }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
    </svg>
  );
}

// --- Navigation history entry ---

interface NavEntry {
  qualifiedName: string;
  filePath?: string;
  scrollLine?: number;
}

// --- Props ---

interface RepoViewerLayoutProps {
  repoName: string;
}

export const RepoViewerLayout = forwardRef<RepoViewerHandle, RepoViewerLayoutProps>(
  function RepoViewerLayout({ repoName }, ref) {
    const { theme } = useTheme();
    const colors = getThemeColors(theme);
    const containerRef = useRef<HTMLDivElement>(null);

    // --- Layout state ---
    const [layoutMode, setLayoutMode] = useState<LayoutMode>('full');
    const [leftWidth, setLeftWidth] = useState(SIDEBAR_LEFT_DEFAULT);
    const [leftCollapsed, setLeftCollapsed] = useState(false);
    const [narrowTab, setNarrowTab] = useState<'files' | 'code'>('code');

    // --- Core state ---
    const [breadcrumbs, setBreadcrumbs] = useState<Breadcrumb[]>([{ qualified_name: repoName, name: repoName }]);
    const [activeQN, setActiveQN] = useState<string | null>(null);
    const [viewMode, setViewMode] = useState<ViewMode>('code');
    const [highlightLine, setHighlightLine] = useState<number | null>(null);
    const [currentScrollLine, setCurrentScrollLine] = useState<number>(1);

    // --- Navigation history ---
    const [navHistory, setNavHistory] = useState<NavEntry[]>([]);
    const [navIndex, setNavIndex] = useState(-1);
    const isNavigatingRef = useRef(false);

    const canGoBack = navIndex > 0;
    const canGoForward = navIndex < navHistory.length - 1;

    // Save current scroll line into current history entry before navigating away
    const updateCurrentEntryScrollLine = useCallback(() => {
      if (navIndex >= 0 && navHistory[navIndex]) {
        setNavHistory(prev => {
          const updated = [...prev];
          updated[navIndex] = { ...updated[navIndex], scrollLine: currentScrollLine };
          return updated;
        });
      }
    }, [navIndex, navHistory, currentScrollLine]);

    const pushNavEntry = useCallback((entry: NavEntry) => {
      if (isNavigatingRef.current) return;
      // Save scroll position of current entry before pushing new one
      updateCurrentEntryScrollLine();
      setNavHistory(prev => {
        const trimmed = prev.slice(0, navIndex + 1);
        return [...trimmed, entry];
      });
      setNavIndex(prev => prev + 1);
    }, [navIndex, updateCurrentEntryScrollLine]);

    // --- Hooks ---
    const fileTree = useFileTree(repoName);
    const codeContent = useCodeContent(repoName);
    const blame = useBlame(repoName);
    const branchHook = useBranches(repoName);
    const graphSearch = useGraphSearch(repoName);
    const symbolNav = useSymbolNavigation(repoName);

    // Load initial data
    useEffect(() => {
      fileTree.loadFolder(repoName);
      branchHook.loadBranches();
    }, [repoName]); // eslint-disable-line react-hooks/exhaustive-deps

    // Responsive layout
    useEffect(() => {
      const container = containerRef.current;
      if (!container) return;
      const observer = new ResizeObserver(entries => {
        const width = entries[0]?.contentRect.width || 0;
        if (width >= BREAKPOINT_FULL) setLayoutMode('full');
        else if (width >= BREAKPOINT_COMPACT) setLayoutMode('compact');
        else setLayoutMode('narrow');
      });
      observer.observe(container);
      return () => observer.disconnect();
    }, []);

    // --- Navigation ---

    /** Build breadcrumb trail from a qualified_name by splitting on '.' */
    const buildBreadcrumbsFromQN = useCallback((qn: string): Breadcrumb[] => {
      const parts = qn.split('.');
      const crumbs: Breadcrumb[] = [];
      for (let i = 0; i < parts.length; i++) {
        crumbs.push({
          qualified_name: parts.slice(0, i + 1).join('.'),
          name: parts[i],
        });
      }
      return crumbs;
    }, []);

    /** Navigate the file tree to a folder (from anywhere) */
    const navigateToFolder = useCallback((folderQN: string) => {
      const crumbs = buildBreadcrumbsFromQN(folderQN);
      setBreadcrumbs(crumbs);
      fileTree.loadFolder(folderQN);
      setActiveQN(null);
      codeContent.setFilePreview(null);
      symbolNav.close();
      // Ensure file tree is visible
      if (layoutMode === 'narrow') setNarrowTab('files');
      if (leftCollapsed) setLeftCollapsed(false);
    }, [buildBreadcrumbsFromQN, fileTree, codeContent, symbolNav, layoutMode, leftCollapsed]);

    const navigateToQN = useCallback(async (qn: string, addToHistory = true) => {
      setActiveQN(qn);
      await codeContent.navigateToCode(qn);
      if (addToHistory) {
        pushNavEntry({ qualifiedName: qn, filePath: codeContent.filePreview?.filePath });
      }
      // Sync file tree: show the parent folder of the file being viewed
      const parts = qn.split('.');
      if (parts.length > 1) {
        const parentQN = parts.slice(0, -1).join('.');
        const parentCrumbs = buildBreadcrumbsFromQN(parentQN);
        setBreadcrumbs(parentCrumbs);
        fileTree.loadFolder(parentQN);
      }
      if (layoutMode === 'narrow') setNarrowTab('code');
    }, [codeContent, pushNavEntry, buildBreadcrumbsFromQN, fileTree, layoutMode]);

    const handleGoBack = useCallback(async () => {
      if (!canGoBack) return;
      // Save current scroll position first
      updateCurrentEntryScrollLine();
      isNavigatingRef.current = true;
      const entry = navHistory[navIndex - 1];
      setNavIndex(prev => prev - 1);
      setActiveQN(entry.qualifiedName);
      await codeContent.navigateToCode(entry.qualifiedName);
      // Restore scroll position after a tick
      if (entry.scrollLine) {
        setHighlightLine(entry.scrollLine);
        setTimeout(() => setHighlightLine(null), 1500);
      }
      isNavigatingRef.current = false;
    }, [canGoBack, navHistory, navIndex, codeContent, updateCurrentEntryScrollLine]);

    const handleGoForward = useCallback(async () => {
      if (!canGoForward) return;
      updateCurrentEntryScrollLine();
      isNavigatingRef.current = true;
      const entry = navHistory[navIndex + 1];
      setNavIndex(prev => prev + 1);
      setActiveQN(entry.qualifiedName);
      await codeContent.navigateToCode(entry.qualifiedName);
      if (entry.scrollLine) {
        setHighlightLine(entry.scrollLine);
        setTimeout(() => setHighlightLine(null), 1500);
      }
      isNavigatingRef.current = false;
    }, [canGoForward, navHistory, navIndex, codeContent, updateCurrentEntryScrollLine]);

    const handleItemClick = useCallback(async (item: FolderChildItem) => {
      // Determine if this is a folder/package (should expand, not load code)
      const hasExtension = item.name.includes('.') && !item.name.startsWith('.');
      const isFolder = item.is_package || item.child_count > 0 || (item.node_type === 'Module' && !hasExtension);
      if (isFolder) {
        navigateToFolder(item.qualified_name);
      } else {
        await navigateToQN(item.qualified_name);
        setViewMode('code');
        blame.clearBlame();
      }
    }, [navigateToFolder, navigateToQN, blame]);

    const handleBreadcrumbClick = useCallback((index: number) => {
      const crumbs = breadcrumbs.slice(0, index + 1);
      const targetQN = crumbs[crumbs.length - 1].qualified_name;
      if (index < breadcrumbs.length - 1) {
        // Navigating up to a folder
        navigateToFolder(targetQN);
      } else {
        // Clicking on the current (last) breadcrumb — just reload
        setBreadcrumbs(crumbs);
        fileTree.loadFolder(targetQN);
      }
    }, [breadcrumbs, fileTree, navigateToFolder]);

    const handleGraphResultClick = useCallback(async (qn: string) => {
      await navigateToQN(qn);
    }, [navigateToQN]);

    const handleViewModeChange = useCallback((mode: ViewMode) => {
      setViewMode(mode);
      if (mode === 'blame' && codeContent.filePreview) {
        blame.loadBlame(codeContent.filePreview.filePath);
      }
    }, [blame, codeContent.filePreview]);

    const handleCloseFile = useCallback(() => {
      setActiveQN(null);
      codeContent.setFilePreview(null);
      blame.clearBlame();
      setViewMode('code');
      symbolNav.close();
    }, [codeContent, blame, symbolNav]);

    const handleBranchChange = useCallback(async (_branch: string) => {
      // TODO: implement branch checkout via API
    }, []);

    const handleCurrentLineChange = useCallback((line: number) => {
      setCurrentScrollLine(line);
    }, []);

    // Symbol navigation — opens as sidebar, not popup
    const handleCodeSymbolClick = useCallback((symbolName: string, _x: number, _y: number) => {
      symbolNav.lookup(
        symbolName, 0, 0,
        codeContent.filePreview?.fullLines?.join('\n') || codeContent.filePreview?.visibleCode || '',
        codeContent.filePreview?.filePath || '',
      );
    }, [symbolNav, codeContent.filePreview]);

    const handleSymbolNavItemClick = useCallback(async (qn: string) => {
      symbolNav.close();
      await navigateToQN(qn);
    }, [symbolNav, navigateToQN]);

    const handleSymbolNavScrollToLine = useCallback((line: number) => {
      setHighlightLine(line);
      setTimeout(() => setHighlightLine(null), 2000);
    }, []);

    // --- Imperative handle ---
    useImperativeHandle(ref, () => ({
      navigateTo: async (qualifiedName: string) => {
        await navigateToQN(qualifiedName);
      },
    }), [navigateToQN]);

    // --- Resize handlers ---
    const resizeRef = useRef<{ startX: number; startW: number } | null>(null);

    const handleResizeStart = useCallback((e: React.MouseEvent) => {
      e.preventDefault();
      resizeRef.current = { startX: e.clientX, startW: leftWidth };
      const handleMove = (ev: MouseEvent) => {
        if (!resizeRef.current) return;
        const delta = ev.clientX - resizeRef.current.startX;
        setLeftWidth(Math.max(160, Math.min(400, resizeRef.current.startW + delta)));
      };
      const handleUp = () => {
        resizeRef.current = null;
        window.removeEventListener('mousemove', handleMove);
        window.removeEventListener('mouseup', handleUp);
      };
      window.addEventListener('mousemove', handleMove);
      window.addEventListener('mouseup', handleUp);
    }, [leftWidth]);

    // --- Symbol nav sidebar (right side) ---
    const symbolNavOpen = symbolNav.data !== null;
    const [symbolNavWidth, setSymbolNavWidth] = useState(340);

    const renderSymbolNavSidebar = () => {
      if (!symbolNav.data) return null;
      return (
        <SymbolNavSidebar
          data={symbolNav.data}
          loading={symbolNav.loading}
          colors={colors}
          width={symbolNavWidth}
          onWidthChange={setSymbolNavWidth}
          currentScrollLine={currentScrollLine}
          onClose={symbolNav.close}
          onItemClick={handleSymbolNavItemClick}
          onScrollToLine={handleSymbolNavScrollToLine}
          repoName={repoName}
        />
      );
    };

    // --- Code view props (shared) ---
    const codeViewProps = {
      filePreview: codeContent.filePreview,
      fileLoading: codeContent.fileLoading,
      expandLoading: codeContent.expandLoading,
      breadcrumbs,
      viewMode,
      blameData: blame.blameData,
      blameLoading: blame.loading,
      onExpandCode: codeContent.expandCode,
      onBreadcrumbClick: handleBreadcrumbClick,
      onViewModeChange: handleViewModeChange,
      onCloseFile: handleCloseFile,
      highlightLine,
      onSymbolClick: handleCodeSymbolClick,
      onCurrentLineChange: handleCurrentLineChange,
      canGoBack,
      canGoForward,
      onGoBack: handleGoBack,
      onGoForward: handleGoForward,
    };

    // --- Narrow mode: tab-based ---
    if (layoutMode === 'narrow') {
      return (
        <div ref={containerRef} style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
          <div style={{
            display: 'flex', borderBottom: `1px solid ${colors.border}`,
            background: colors.bgSecondary, flexShrink: 0,
          }}>
            {([
              { key: 'files' as const, label: 'Files' },
              { key: 'code' as const, label: 'Code' },
            ]).map(tab => (
              <button
                key={tab.key}
                onClick={() => setNarrowTab(tab.key)}
                style={{
                  flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '4px',
                  padding: '6px 8px', border: 'none',
                  borderBottom: narrowTab === tab.key ? `2px solid ${colors.accent}` : '2px solid transparent',
                  background: 'transparent',
                  color: narrowTab === tab.key ? colors.accent : colors.textMuted,
                  fontSize: '11px', fontWeight: 500, cursor: 'pointer',
                  fontFamily: "'Inter', -apple-system, sans-serif",
                }}
              >
                {tab.label}
              </button>
            ))}
          </div>
          <div style={{ flex: 1, overflow: 'hidden', display: 'flex' }}>
            <div style={{ flex: 1, overflow: 'hidden' }}>
              {narrowTab === 'files' && (
                <FileTreePanel
                  repoName={repoName}
                  children={fileTree.children}
                  loading={fileTree.loading}
                  error={fileTree.error}
                  breadcrumbs={breadcrumbs}
                  activeQN={activeQN}
                  branches={branchHook.branches}
                  currentBranch={branchHook.currentBranch}
                  graphSearchResults={graphSearch.results}
                  graphSearchLoading={graphSearch.loading}
                  onItemClick={handleItemClick}
                  onBreadcrumbClick={handleBreadcrumbClick}
                  onGraphResultClick={handleGraphResultClick}
                  onSearchChange={graphSearch.search}
                  onBranchChange={handleBranchChange}
                  onShowMore={fileTree.showMore}
                  fileListLimit={fileTree.fileListLimit}
                />
              )}
              {narrowTab === 'code' && <CodeViewPanel {...codeViewProps} />}
            </div>
            {symbolNavOpen && renderSymbolNavSidebar()}
          </div>
        </div>
      );
    }

    // --- Full / compact mode: file tree + code + optional symbol sidebar ---
    const effectiveLeftWidth = leftCollapsed ? 0 : (layoutMode === 'compact' ? SIDEBAR_LEFT_COMPACT : leftWidth);

    return (
      <div ref={containerRef} style={{ display: 'flex', height: '100%', overflow: 'hidden', position: 'relative' }}>
        {/* Left panel: File tree */}
        {!leftCollapsed && (
          <>
            <div style={{ width: effectiveLeftWidth, minWidth: effectiveLeftWidth, height: '100%', overflow: 'hidden', flexShrink: 0, display: 'flex', flexDirection: 'column' }}>
              <div style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '4px 8px', borderBottom: `1px solid ${colors.border}`,
                background: colors.bgSecondary, flexShrink: 0,
              }}>
                <span style={{ fontSize: '11px', fontWeight: 600, color: colors.textMuted, fontFamily: "'Inter', sans-serif", letterSpacing: '0.3px', textTransform: 'uppercase' }}>
                  Files
                </span>
                <button
                  onClick={() => setLeftCollapsed(true)}
                  title="Collapse file tree"
                  style={{
                    padding: '2px 4px', background: 'transparent', border: 'none',
                    color: colors.textMuted, cursor: 'pointer', borderRadius: '4px',
                    display: 'flex', alignItems: 'center',
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.color = colors.accent; e.currentTarget.style.background = colors.bgHover; }}
                  onMouseLeave={(e) => { e.currentTarget.style.color = colors.textMuted; e.currentTarget.style.background = 'transparent'; }}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="3" y="3" width="18" height="18" rx="2" /><line x1="9" y1="3" x2="9" y2="21" />
                    <polyline points="15 8 12 12 15 16" />
                  </svg>
                </button>
              </div>
              <div style={{ flex: 1, overflow: 'hidden' }}>
                <FileTreePanel
                  repoName={repoName}
                  children={fileTree.children}
                  loading={fileTree.loading}
                  error={fileTree.error}
                  breadcrumbs={breadcrumbs}
                  activeQN={activeQN}
                  branches={branchHook.branches}
                  currentBranch={branchHook.currentBranch}
                  graphSearchResults={graphSearch.results}
                  graphSearchLoading={graphSearch.loading}
                  onItemClick={handleItemClick}
                  onBreadcrumbClick={handleBreadcrumbClick}
                  onGraphResultClick={handleGraphResultClick}
                  onSearchChange={graphSearch.search}
                  onBranchChange={handleBranchChange}
                  onShowMore={fileTree.showMore}
                  fileListLimit={fileTree.fileListLimit}
                />
              </div>
            </div>
            <div
              onMouseDown={handleResizeStart}
              style={{ width: '4px', cursor: 'col-resize', flexShrink: 0, background: colors.border, transition: 'background 0.15s' }}
              onMouseEnter={(e) => e.currentTarget.style.background = colors.accent}
              onMouseLeave={(e) => e.currentTarget.style.background = colors.border}
            />
          </>
        )}
        {leftCollapsed && (
          <div style={{
            width: '32px', height: '100%', flexShrink: 0,
            display: 'flex', flexDirection: 'column', alignItems: 'center',
            paddingTop: '8px', background: colors.bgSecondary,
            borderRight: `1px solid ${colors.border}`,
          }}>
            <button
              onClick={() => setLeftCollapsed(false)}
              title="Show file tree"
              style={{ padding: '4px', background: 'transparent', border: 'none', color: colors.textMuted, cursor: 'pointer', borderRadius: '4px' }}
              onMouseEnter={(e) => { e.currentTarget.style.color = colors.accent; e.currentTarget.style.background = colors.bgHover; }}
              onMouseLeave={(e) => { e.currentTarget.style.color = colors.textMuted; e.currentTarget.style.background = 'transparent'; }}
            >
              <FileTreeIcon size={16} color="currentColor" />
            </button>
          </div>
        )}

        {/* Center panel: Code view */}
        <div style={{ flex: 1, height: '100%', overflow: 'hidden', minWidth: 0 }}>
          <CodeViewPanel {...codeViewProps} />
        </div>

        {/* Right sidebar: Symbol navigation (on-demand) */}
        {symbolNavOpen && renderSymbolNavSidebar()}
      </div>
    );
  }
);

// --- Symbol Navigation Sidebar ---

function SymbolNavSidebar({ data, loading, colors, width, onWidthChange, currentScrollLine, onClose, onItemClick, onScrollToLine, repoName }: {
  data: SymbolNavData;
  loading: boolean;
  colors: ReturnType<typeof getThemeColors>;
  width: number;
  onWidthChange: (w: number) => void;
  currentScrollLine?: number;
  onClose: () => void;
  onItemClick: (qn: string) => void;
  onScrollToLine: (line: number) => void;
  repoName: string;
}) {
  const hasContent = data.definitions.length > 0 || data.references.length > 0 || (data.inThisFile?.length || 0) > 0;
  const dragRef = useRef<{ startX: number; startW: number } | null>(null);

  const activeInFileIndex = useMemo(() => {
    if (!currentScrollLine || !data.inThisFile?.length) return -1;
    let closest = 0;
    let closestDist = Infinity;
    for (let i = 0; i < data.inThisFile.length; i++) {
      const dist = Math.abs(data.inThisFile[i].line - currentScrollLine);
      if (dist < closestDist) { closestDist = dist; closest = i; }
    }
    return closestDist <= 20 ? closest : -1;
  }, [currentScrollLine, data.inThisFile]);

  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragRef.current = { startX: e.clientX, startW: width };
    const onMove = (ev: MouseEvent) => {
      if (!dragRef.current) return;
      onWidthChange(Math.max(240, Math.min(600, dragRef.current.startW + (dragRef.current.startX - ev.clientX))));
    };
    const onUp = () => { dragRef.current = null; window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [width, onWidthChange]);

  return (
    <>
      <div
        onMouseDown={handleResizeStart}
        style={{ width: '4px', cursor: 'col-resize', flexShrink: 0, background: colors.border, transition: 'background 0.15s' }}
        onMouseEnter={(e) => e.currentTarget.style.background = colors.accent}
        onMouseLeave={(e) => e.currentTarget.style.background = colors.border}
      />
      <div style={{
        width, minWidth: width, height: '100%', flexShrink: 0,
        display: 'flex', flexDirection: 'column',
        background: colors.bgSecondary, overflow: 'hidden',
      }}>
        {/* Header */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '10px 14px', borderBottom: `1px solid ${colors.border}`, flexShrink: 0,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', overflow: 'hidden' }}>
            <span style={{ fontWeight: 600, color: colors.text, fontSize: '14px', fontFamily: "'JetBrains Mono', monospace", overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {data.symbolName}
            </span>
            {loading && <div style={{ width: '14px', height: '14px', flexShrink: 0, border: `2px solid ${colors.border}`, borderTopColor: colors.accent, borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />}
          </div>
          <button onClick={onClose} style={{ background: 'transparent', border: 'none', color: colors.textMuted, cursor: 'pointer', fontSize: '18px', padding: '2px 6px', flexShrink: 0, borderRadius: '4px' }}
            onMouseEnter={(e) => { e.currentTarget.style.color = colors.text; e.currentTarget.style.background = colors.bgHover; }}
            onMouseLeave={(e) => { e.currentTarget.style.color = colors.textMuted; e.currentTarget.style.background = 'transparent'; }}
          >{'\u00D7'}</button>
        </div>

        {/* Content */}
        <div style={{ flex: 1, overflow: 'auto', fontSize: '13px' }}>
          {!hasContent && !loading && (
            <div style={{ padding: '24px 16px', textAlign: 'center', color: colors.textDimmed, fontStyle: 'italic', fontFamily: "'Inter', -apple-system, sans-serif" }}>
              No results found
            </div>
          )}

          {/* Definitions */}
          {data.definitions.length > 0 && (
            <SidebarSection title={`${data.definitions.length} Definition${data.definitions.length > 1 ? 's' : ''}`} colors={colors} first>
              {data.definitions.map(d => (
                <SidebarNavRow key={d.qualified_name} item={d} colors={colors} onClick={() => onItemClick(d.qualified_name)} />
              ))}
            </SidebarSection>
          )}

          {/* In this file */}
          {(data.inThisFile?.length || 0) > 0 && (
            <SidebarSection title={`In this file (${data.inThisFile!.length})`} colors={colors}>
              {data.inThisFile!.map((entry, idx) => {
                const isActive = idx === activeInFileIndex;
                return (
                  <button
                    key={`local-${idx}`}
                    onClick={() => onScrollToLine(entry.line)}
                    style={{
                      display: 'flex', alignItems: 'baseline', gap: '10px',
                      width: '100%', padding: '6px 14px',
                      background: isActive ? colors.accentBg : 'transparent',
                      border: 'none',
                      borderLeft: isActive ? `3px solid ${colors.accent}` : '3px solid transparent',
                      textAlign: 'left', cursor: 'pointer',
                      fontFamily: "'JetBrains Mono', monospace", fontSize: '12px',
                    }}
                    onMouseEnter={(e) => { if (!isActive) e.currentTarget.style.background = colors.bgHover; }}
                    onMouseLeave={(e) => { e.currentTarget.style.background = isActive ? colors.accentBg : 'transparent'; }}
                  >
                    <span style={{ color: isActive ? colors.accent : colors.textDimmed, fontSize: '11px', minWidth: '36px', textAlign: 'right', flexShrink: 0, fontWeight: isActive ? 600 : 400 }}>
                      {entry.line}
                    </span>
                    <span style={{ color: isActive ? colors.text : colors.textMuted, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {entry.text.trim()}
                    </span>
                  </button>
                );
              })}
            </SidebarSection>
          )}

          {/* References */}
          {data.references.length > 0 && (
            <SidebarSection title={`${data.references.length} Reference${data.references.length > 1 ? 's' : ''}`} colors={colors}>
              {data.references.map(r => (
                <ReferenceRow key={r.qualified_name} item={r} colors={colors} repoName={repoName} onClick={() => onItemClick(r.qualified_name)} />
              ))}
            </SidebarSection>
          )}
        </div>
      </div>
    </>
  );
}

// --- Section header with visual separator ---

function SidebarSection({ title, colors, children, first }: {
  title: string; colors: ReturnType<typeof getThemeColors>; children: React.ReactNode; first?: boolean;
}) {
  return (
    <div style={{ marginTop: first ? 0 : '4px' }}>
      <div style={{
        padding: '8px 14px', fontSize: '11px', fontWeight: 700,
        color: colors.textMuted, background: colors.bgHover,
        borderTop: first ? 'none' : `2px solid ${colors.border}`,
        borderBottom: `1px solid ${colors.border}`,
        letterSpacing: '0.4px', textTransform: 'uppercase',
        fontFamily: "'Inter', -apple-system, sans-serif",
      }}>
        {title}
      </div>
      {children}
    </div>
  );
}

// --- Reference row with expandable code preview ---

function ReferenceRow({ item, colors, repoName, onClick }: {
  item: SymbolNavResult; colors: ReturnType<typeof getThemeColors>; repoName: string; onClick: () => void;
}) {
  const { theme } = useTheme();
  const [expanded, setExpanded] = useState(false);
  const [codeText, setCodeText] = useState<string | null>(null);
  const [codeLang, setCodeLang] = useState('text');
  const [codeStartLine, setCodeStartLine] = useState(1);
  const [loadingCode, setLoadingCode] = useState(false);
  const shortPath = item.file_path.split('/').slice(-2).join('/');

  const syntaxTheme = theme === 'light' ? customSyntaxThemeLight : theme === 'beige' ? customSyntaxThemeBeige : customSyntaxTheme;

  const handleExpand = useCallback(async () => {
    if (expanded) { setExpanded(false); return; }
    setExpanded(true);
    if (codeText !== null) return;
    setLoadingCode(true);
    try {
      const res = await apiFetch(`/api/graph/node/${encodeURIComponent(repoName)}/code?qualified_name=${encodeURIComponent(item.qualified_name)}`);
      if (res.ok) {
        const d = await res.json();
        const code = d.source_code || d.code || '';
        const lines = code.split('\n');
        // Limit to 50 lines for preview
        setCodeText(lines.slice(0, 50).join('\n'));
        setCodeLang(detectLanguage(d.file_path || item.file_path || ''));
        setCodeStartLine(d.start_line || item.start_line || 1);
      }
    } catch { /* ignore */ }
    finally { setLoadingCode(false); }
  }, [expanded, codeText, repoName, item.qualified_name, item.file_path, item.start_line]);

  return (
    <div style={{ borderBottom: `1px solid ${colors.border}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '8px 14px' }}>
        <button onClick={handleExpand} style={{
          padding: '2px 4px', background: 'transparent', border: 'none', color: colors.textMuted,
          cursor: 'pointer', flexShrink: 0, display: 'flex', alignItems: 'center',
          transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)', transition: 'transform 0.15s',
        }}>
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="9 18 15 12 9 6" />
          </svg>
        </button>
        <button onClick={onClick} style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '3px', background: 'transparent', border: 'none', textAlign: 'left', cursor: 'pointer', padding: 0 }}
          onMouseEnter={(e) => e.currentTarget.style.opacity = '0.8'} onMouseLeave={(e) => e.currentTarget.style.opacity = '1'}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span style={{ fontSize: '10px', padding: '1px 5px', borderRadius: '3px', background: colors.accentBg, color: colors.accent, fontWeight: 600, fontFamily: "'Inter', sans-serif" }}>
              {item.node_type}
            </span>
            <span style={{ color: colors.text, fontSize: '13px', fontWeight: 500, fontFamily: "'JetBrains Mono', monospace", overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {item.name}
            </span>
            {item.start_line && <span style={{ marginLeft: 'auto', fontSize: '11px', color: colors.textDimmed }}>:{item.start_line}</span>}
          </div>
          <div style={{ fontSize: '11px', color: colors.textDimmed, fontFamily: "'JetBrains Mono', monospace", overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {shortPath}
          </div>
        </button>
      </div>
      {expanded && (
        <div style={{
          margin: '0 14px 10px 28px', background: colors.bg, borderRadius: '6px',
          border: `1px solid ${colors.border}`, maxHeight: '220px',
          overflowY: 'scroll', overflowX: 'auto',
        }}>
          {loadingCode ? (
            <div style={{ padding: '12px 14px', color: colors.textDimmed, fontStyle: 'italic', fontFamily: "'Inter', sans-serif", fontSize: '12px' }}>Loading...</div>
          ) : codeText !== null ? (
            <SyntaxHighlighter
              language={codeLang}
              style={syntaxTheme}
              showLineNumbers={true}
              startingLineNumber={codeStartLine}
              wrapLines={true}
              lineNumberStyle={{ minWidth: '36px', paddingRight: '12px', color: colors.textDimmed, fontSize: '11px', userSelect: 'none' }}
              customStyle={{
                margin: 0, padding: '6px 0', fontSize: '12px', lineHeight: '1.6',
                fontFamily: "'JetBrains Mono', monospace",
                background: 'transparent', borderRadius: 0, overflow: 'visible',
              }}
              codeTagProps={{ style: { fontFamily: "'JetBrains Mono', monospace", fontSize: '12px' } }}
            >
              {codeText}
            </SyntaxHighlighter>
          ) : (
            <div style={{ padding: '12px 14px', color: colors.textDimmed, fontStyle: 'italic', fontFamily: "'Inter', sans-serif", fontSize: '12px' }}>Could not load code</div>
          )}
        </div>
      )}
    </div>
  );
}

// --- Definition/simple nav row ---

function SidebarNavRow({ item, colors, onClick }: {
  item: SymbolNavResult; colors: ReturnType<typeof getThemeColors>; onClick: () => void;
}) {
  const shortPath = item.file_path.split('/').slice(-2).join('/');
  return (
    <button onClick={onClick} style={{
      display: 'flex', flexDirection: 'column', gap: '3px', width: '100%', padding: '8px 14px',
      background: 'transparent', border: 'none', borderBottom: `1px solid ${colors.border}`,
      textAlign: 'left', cursor: 'pointer',
    }}
      onMouseEnter={(e) => e.currentTarget.style.background = colors.bgHover}
      onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
        <span style={{ fontSize: '10px', padding: '1px 5px', borderRadius: '3px', background: colors.accentBg, color: colors.accent, fontWeight: 600, fontFamily: "'Inter', sans-serif" }}>
          {item.node_type}
        </span>
        <span style={{ color: colors.text, fontSize: '13px', fontWeight: 500, fontFamily: "'JetBrains Mono', monospace", overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {item.name}
        </span>
        {item.start_line && <span style={{ marginLeft: 'auto', fontSize: '11px', color: colors.textDimmed }}>:{item.start_line}</span>}
      </div>
      <div style={{ fontSize: '11px', color: colors.textDimmed, fontFamily: "'JetBrains Mono', monospace", overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {shortPath}
      </div>
    </button>
  );
}

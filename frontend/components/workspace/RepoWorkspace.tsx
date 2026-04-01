'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { useMemo, useState, useEffect, useRef, useCallback } from 'react';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';
import { useOverviewData } from '@/lib/hooks/useOverviewData';
import { useLayoutTree } from '@/lib/hooks/useLayoutTree';
import type { PanelId } from '@/lib/layout-tree';
import { PanelWorkspace, type PanelSlot } from './PanelWorkspace';
import { OverviewDocV2, LeftNavigation } from '@/components/OverviewDocV2';
import { RepoViewerLayout, type RepoViewerHandle } from '@/components/repo-viewer';
import { FloatingChatWidget } from '@/components/FloatingChatWidget';
import { ResearchPanel } from './ResearchPanel';
import { ModelCombobox } from '@/components/ModelCombobox';
import { buildModelSelectorTiers, useModels } from '@/lib/hooks/useModels';
import { getPaperByRepo } from '@/lib/papers-api';

// SVG icons for toolbar
const OverviewIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M4 6h16M4 12h16M4 18h10" />
  </svg>
);

const CodeIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" />
  </svg>
);

const ChatIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
  </svg>
);

const ResearchIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" /><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
  </svg>
);

interface RepoWorkspaceProps {
  repoName: string;
}

const AVAILABLE_PANELS: PanelId[] = ['overview', 'code', 'chat', 'research'];
const DEFAULT_ACTIVE: PanelId[] = ['overview', 'chat'];

// --- Stable Panel Content Components ---
// These read volatile data from refs, so they re-render on their own schedule
// without causing the panels array to be recreated.

function OverviewPanelContent({
  repoName,
  overviewDataRef,
  themeRef,
  colorsRef,
  navCollapsedRef,
  showGraphOverviewRef,
  handleNavigateToNodeRef,
  setNavCollapsed,
}: {
  repoName: string;
  overviewDataRef: React.RefObject<ReturnType<typeof useOverviewData>>;
  themeRef: React.RefObject<'dark' | 'light' | 'beige'>;
  colorsRef: React.RefObject<ReturnType<typeof getThemeColors>>;
  navCollapsedRef: React.RefObject<boolean>;
  showGraphOverviewRef: React.RefObject<any>;
  handleNavigateToNodeRef: React.RefObject<(qn: string) => void>;
  setNavCollapsed: React.Dispatch<React.SetStateAction<boolean>>;
}) {
  const overviewData = overviewDataRef.current!;
  const theme = themeRef.current!;
  const colors = colorsRef.current!;
  const navCollapsed = navCollapsedRef.current!;
  const showGraphOverview = showGraphOverviewRef.current;

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      {overviewData.overviewIndex?.tree && overviewData.overviewIndex.tree.length > 0 && (
        <div style={{
          position: 'relative',
          width: navCollapsed ? 28 : 200,
          minWidth: navCollapsed ? 28 : 160,
          flexShrink: 0,
          transition: 'width 0.15s ease, min-width 0.15s ease',
          overflow: 'hidden',
        }}>
          {!navCollapsed && (
            <div style={{
              width: '100%',
              height: '100%',
              borderRight: `1px solid ${colors.border}`,
              overflow: 'auto',
            }}>
              <LeftNavigation
                theme={theme}
                tree={overviewData.overviewIndex.tree}
                currentPath={overviewData.currentDocPath}
                onNavigate={overviewData.handleNavigate}
                isCollapsed={false}
                onToggle={() => {}}
              />
            </div>
          )}
          <button
            onClick={() => setNavCollapsed(c => !c)}
            style={{
              position: 'absolute', top: 8, right: navCollapsed ? 4 : 0,
              width: 20, height: 20, padding: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
              background: colors.card, border: `1px solid ${colors.border}`, borderRadius: 4,
              cursor: 'pointer', color: colors.textMuted, fontSize: 10, lineHeight: 1,
              zIndex: 2,
              transition: 'right 0.15s ease',
            }}
            title={navCollapsed ? 'Show sidebar' : 'Hide sidebar'}
          >
            {navCollapsed ? '\u25B6' : '\u25C0'}
          </button>
        </div>
      )}
      <div style={{ flex: 1, overflow: 'hidden', minWidth: 0 }}>
        {showGraphOverview ? (
          <GraphStatsView
            repoName={repoName}
            graphStats={overviewData.graphStats!}
            genProgress={overviewData.genProgress}
            onGenerate={overviewData.startGeneration}
            onResume={overviewData.resumeGeneration}
            theme={theme}
          />
        ) : (
          <OverviewDocV2
            repoName={repoName}
            index={overviewData.overviewIndex}
            currentPath={overviewData.currentDocPath}
            content={overviewData.docContent}
            loading={overviewData.overviewLoading || overviewData.docContentLoading}
            versions={overviewData.versions}
            currentVersionId={overviewData.currentVersionId}
            defaultVersionId={overviewData.defaultVersionId}
            onNavigate={overviewData.handleNavigate}
            onRefresh={overviewData.handleRefresh}
            onSectionLoad={overviewData.loadDocContent}
            onVersionChange={overviewData.handleVersionChange}
            onVersionsUpdate={() => overviewData.loadOverview()}
            onNavigateToNode={(qn: string) => handleNavigateToNodeRef.current!(qn)}
          />
        )}
      </div>
    </div>
  );
}

function ChatPanelContent({
  repoName,
  activeContextRef,
  handleNavigateToNodeRef,
}: {
  repoName: string;
  activeContextRef: React.RefObject<any>;
  handleNavigateToNodeRef: React.RefObject<(qn: string) => void>;
}) {
  return (
    <FloatingChatWidget
      repoName={repoName}
      isOpen={true}
      onToggle={() => {}}
      embedded
      activeContext={activeContextRef.current}
      onNavigateToNode={(qn: string) => handleNavigateToNodeRef.current!(qn)}
    />
  );
}

export function RepoWorkspace({ repoName }: RepoWorkspaceProps) {
  const { theme } = useTheme();
  const colors = getThemeColors(theme);
  const overviewData = useOverviewData(repoName);
  const repoViewerRef = useRef<RepoViewerHandle>(null);
  const [activeResearchName, setActiveResearchName] = useState<string | null>(null);
  const [sourcePaper, setSourcePaper] = useState<{ paper_id: string; title: string } | null>(null);

  // Query whether this repo was built from a paper
  useEffect(() => {
    let cancelled = false;
    getPaperByRepo(repoName).then(result => {
      if (!cancelled) setSourcePaper(result);
    });
    return () => { cancelled = true; };
  }, [repoName]);

  const [navCollapsed, setNavCollapsed] = useState(() => {
    if (typeof window === 'undefined') return false;
    try { return localStorage.getItem('workspace:nav-collapsed') === 'true'; } catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem('workspace:nav-collapsed', String(navCollapsed)); } catch {}
  }, [navCollapsed]);

  const workspace = useLayoutTree(
    `workspace:repo:${repoName}`,
    AVAILABLE_PANELS,
    DEFAULT_ACTIVE
  );

  // Navigate to node in embedded RepoViewer (and auto-open Code panel)
  // Uses a pending ref so navigation works even if the code panel needs to mount first.
  const pendingNavigateRef = useRef<string | null>(null);

  const handleNavigateToNode = useCallback((qualifiedName: string) => {
    // ensurePanelActive only adds, never removes — safe against stale closures
    workspace.ensurePanelActive('code');

    if (repoViewerRef.current) {
      repoViewerRef.current.navigateTo(qualifiedName);
      pendingNavigateRef.current = null;
    } else {
      // Code panel not mounted yet — stash the target for when it mounts
      pendingNavigateRef.current = qualifiedName;
    }
  }, [workspace]);

  // Flush pending navigation after code panel mounts
  useEffect(() => {
    if (pendingNavigateRef.current && repoViewerRef.current) {
      const qn = pendingNavigateRef.current;
      pendingNavigateRef.current = null;
      repoViewerRef.current.navigateTo(qn);
    }
  });

  // Derive active context for chat — includes overview + research when both open
  const activeContext = useMemo(() => {
    const parts: string[] = [];
    let operatorName: string | undefined;
    let filePath: string | undefined;

    // Overview context
    if (overviewData.overviewIndex?.tree && overviewData.currentDocPath) {
      const section = overviewData.overviewIndex.tree.find(
        (item: any) => item.path === overviewData.currentDocPath
      );
      const sectionName = section?.name || overviewData.currentDocPath.replace('sections/', '').replace('.md', '');
      operatorName = sectionName;
      filePath = overviewData.currentDocPath;
      parts.push(sectionName);
    }

    // Research context
    if (activeResearchName) {
      parts.push(`Research: ${activeResearchName}`);
    }

    if (parts.length > 0) {
      return {
        pageType: 'operator' as const,
        operatorName: operatorName || activeResearchName || undefined,
        documentTitle: `${repoName} / ${parts.join(' + ')}`,
        filePath,
        sourcePaperId: sourcePaper?.paper_id,
        sourcePaperTitle: sourcePaper?.title,
      };
    }
    return {
      pageType: 'repo' as const,
      documentTitle: repoName,
      sourcePaperId: sourcePaper?.paper_id,
      sourcePaperTitle: sourcePaper?.title,
    };
  }, [overviewData.overviewIndex, overviewData.currentDocPath, repoName, activeResearchName, sourcePaper]);

  // Show graph stats + generate button when no docs yet
  const showGraphOverview = !overviewData.overviewIndex && !overviewData.overviewLoading && overviewData.graphStats;

  // --- Stable panel render functions using refs ---
  // Store volatile data in refs so render functions don't need to capture them as closures.
  // This keeps the panels array stable across re-renders (only changes when repoName changes),
  // preventing panel unmount/remount during resize, drag, or theme changes.
  const overviewDataRef = useRef(overviewData);
  overviewDataRef.current = overviewData;
  const activeContextRef = useRef(activeContext);
  activeContextRef.current = activeContext;
  const themeRef = useRef(theme);
  themeRef.current = theme;
  const colorsRef = useRef(colors);
  colorsRef.current = colors;
  const navCollapsedRef = useRef(navCollapsed);
  navCollapsedRef.current = navCollapsed;
  const showGraphOverviewRef = useRef(showGraphOverview);
  showGraphOverviewRef.current = showGraphOverview;
  const handleNavigateToNodeRef = useRef(handleNavigateToNode);
  handleNavigateToNodeRef.current = handleNavigateToNode;

  const panels: PanelSlot[] = useMemo(() => [
    {
      id: 'overview' as PanelId,
      title: 'Overview',
      icon: <OverviewIcon />,
      render: () => (
        <OverviewPanelContent
          repoName={repoName}
          overviewDataRef={overviewDataRef}
          themeRef={themeRef}
          colorsRef={colorsRef}
          navCollapsedRef={navCollapsedRef}
          showGraphOverviewRef={showGraphOverviewRef}
          handleNavigateToNodeRef={handleNavigateToNodeRef}
          setNavCollapsed={setNavCollapsed}
        />
      ),
    },
    {
      id: 'code' as PanelId,
      title: 'Code',
      icon: <CodeIcon />,
      render: () => <RepoViewerLayout ref={repoViewerRef} repoName={repoName} />,
    },
    {
      id: 'chat' as PanelId,
      title: 'Chat',
      icon: <ChatIcon />,
      render: () => (
        <ChatPanelContent
          repoName={repoName}
          activeContextRef={activeContextRef}
          handleNavigateToNodeRef={handleNavigateToNodeRef}
        />
      ),
    },
    {
      id: 'research' as PanelId,
      title: 'Research',
      icon: <ResearchIcon />,
      render: () => (
        <ResearchPanel
          repoName={repoName}
          onNavigateToNode={(qn: string) => handleNavigateToNodeRef.current(qn)}
          onResearchSelect={setActiveResearchName}
        />
      ),
    },
  ], [repoName]); // Only repoName — everything else accessed via refs

  return (
    <PanelWorkspace
      panels={panels}
      layout={workspace.layout}
      activePanels={workspace.activePanels}
      onTogglePanel={workspace.togglePanel}
      onMovePanel={workspace.movePanel}
      onUpdateSizes={workspace.updateSizes}
    />
  );
}

// Design tokens matching OverviewDocV2
const GS_COLORS = {
  dark: {
    bg: { primary: '#0d1117', secondary: '#161b22' },
    border: { primary: '#30363d', secondary: '#21262d' },
    text: { primary: '#e6edf3', secondary: '#8b949e', muted: '#6e7681' },
    accent: { blue: '#58a6ff', blueBg: 'rgba(56, 139, 253, 0.15)', green: '#3fb950', greenBg: 'rgba(46, 160, 67, 0.15)', purple: '#a371f7', purpleBg: 'rgba(163, 113, 247, 0.15)' },
  },
  light: {
    bg: { primary: '#ffffff', secondary: '#f6f8fa' },
    border: { primary: '#d0d7de', secondary: '#e8ebef' },
    text: { primary: '#1f2328', secondary: '#656d76', muted: '#8c959f' },
    accent: { blue: '#0969da', blueBg: 'rgba(9, 105, 218, 0.08)', green: '#1a7f37', greenBg: 'rgba(26, 127, 55, 0.08)', purple: '#8250df', purpleBg: 'rgba(130, 80, 223, 0.08)' },
  },
  beige: {
    bg: { primary: '#faf8f5', secondary: '#f5f0e8' },
    border: { primary: '#d4c8b8', secondary: '#e5ddd0' },
    text: { primary: '#3d3632', secondary: '#6b5f54', muted: '#8a7b6c' },
    accent: { blue: '#8b5a2b', blueBg: 'rgba(139, 90, 43, 0.12)', green: '#5d7a3a', greenBg: 'rgba(93, 122, 58, 0.12)', purple: '#7a5a8a', purpleBg: 'rgba(122, 90, 138, 0.12)' },
  },
};

const GS_FONTS = {
  ui: '-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif',
  mono: '"JetBrains Mono", "Fira Code", "SF Mono", Monaco, Consolas, monospace',
};

interface ProgressTrajectoryEvent {
  timestamp: string;
  status: string;
  progress: number;
  step: string;
  message: string;
  error?: string | null;
  details?: Record<string, unknown> | null;
}

function formatRelativeTime(dateStr: string | null | undefined): string {
  if (!dateStr) return '';
  try {
    const date = new Date(dateStr);
    const diffMs = Date.now() - date.getTime();
    const diffSec = Math.floor(diffMs / 1000);
    const diffMin = Math.floor(diffSec / 60);
    const diffHr = Math.floor(diffMin / 60);

    if (diffSec < 60) return 'just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHr < 24) return `${diffHr}h ago`;
    return date.toLocaleDateString();
  } catch {
    return '';
  }
}

function formatClockTime(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return '';
  }
}

function summarizeTrajectoryDetails(details?: Record<string, unknown> | null): string {
  if (!details) return '';

  const parts: string[] = [];
  if (typeof details.tool_call_count === 'number' && details.tool_call_count > 0) {
    parts.push(`${details.tool_call_count} tool calls`);
  }
  if (typeof details.explored_node_count === 'number' && details.explored_node_count > 0) {
    parts.push(`${details.explored_node_count} nodes`);
  }
  if (typeof details.outline_count === 'number' && details.outline_count > 0) {
    parts.push(`${details.outline_count} sections planned`);
  }
  if (typeof details.completed_section_count === 'number' && details.completed_section_count > 0) {
    parts.push(`${details.completed_section_count} sections aggregated`);
  }

  return parts.join(' • ');
}

function extractToolCalls(details?: Record<string, unknown> | null): Array<{ display: string; resultPreview?: string }> {
  const recentToolCalls = details?.recent_tool_calls;
  if (!Array.isArray(recentToolCalls)) return [];

  return recentToolCalls.flatMap((call) => {
    if (!call || typeof call !== 'object') return [];

    const display = typeof call.display === 'string' ? call.display : '';
    if (!display) return [];

    return [{
      display,
      resultPreview: typeof call.result_preview === 'string' ? call.result_preview : undefined,
    }];
  });
}

// Graph stats view with manual generate button — matches OverviewDocV2 design
function GraphStatsView({
  repoName,
  graphStats,
  genProgress,
  onGenerate,
  onResume,
  theme,
}: {
  repoName: string;
  graphStats: { name: string; node_count: number; relationship_count: number; has_graph: boolean; path: string | null; sync_enabled: boolean };
  genProgress: {
    generating: boolean;
    status: string | null;
    taskId: string | null;
    progress: number;
    message: string;
    trajectory: ProgressTrajectoryEvent[];
    lastUpdateAt: string | null;
  };
  onGenerate: (opts?: { model?: string; doc_depth?: number; language?: string }) => void;
  onResume: (taskId?: string | null) => void;
  theme: string;
}) {
  const [selectedModel, setSelectedModel] = useState('');
  const [docDepth, setDocDepth] = useState(2);
  const [language, setLanguage] = useState<'en' | 'zh'>('en');
  const [focus, setFocus] = useState('');
  const { tiers, defaultModel } = useModels();

  const c = GS_COLORS[theme as keyof typeof GS_COLORS] || GS_COLORS.dark;
  const modelTiers = useMemo(
    () => buildModelSelectorTiers(tiers, defaultModel),
    [tiers, defaultModel]
  );

  // Generation progress view
  if (genProgress.generating) {
    const trajectory = genProgress.trajectory.slice(-8);
    return (
      <div style={{
        flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'center', padding: '48px 32px',
        fontFamily: "'Inter', " + GS_FONTS.ui, background: c.bg.primary,
      }}>
        <div style={{ maxWidth: 480, width: '100%' }}>
          <div style={{ textAlign: 'center', marginBottom: 32 }}>
            <div style={{
              width: 56, height: 56, borderRadius: 14, margin: '0 auto 16px',
              background: `linear-gradient(135deg, ${c.accent.blueBg}, ${c.accent.purpleBg})`,
              border: `1px solid ${c.border.secondary}`,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke={c.accent.blue} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
                <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
              </svg>
            </div>
            <h2 style={{ fontSize: 18, fontWeight: 600, color: c.text.primary, margin: 0 }}>
              Generating Documentation
            </h2>
          </div>
          <div style={{
            background: c.bg.secondary, borderRadius: 12, padding: 20,
            border: `1px solid ${c.border.secondary}`,
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 10 }}>
              <span style={{ fontSize: 12, fontWeight: 500, color: c.text.secondary }}>
                {genProgress.message || 'Generating...'}
              </span>
              <span style={{ fontSize: 12, fontWeight: 600, color: c.accent.blue, fontFamily: GS_FONTS.mono }}>
                {genProgress.progress}%
              </span>
            </div>
            <div style={{
              height: 6, borderRadius: 3, background: c.border.secondary, overflow: 'hidden',
            }}>
              <div style={{
                height: '100%', borderRadius: 3, background: c.accent.blue,
                width: `${genProgress.progress}%`, transition: 'width 0.4s ease',
              }} />
            </div>
            {genProgress.lastUpdateAt && (
              <div style={{
                marginTop: 10,
                fontSize: 11,
                color: c.text.muted,
                display: 'flex',
                justifyContent: 'space-between',
              }}>
                <span>Last update</span>
                <span>{formatRelativeTime(genProgress.lastUpdateAt)}</span>
              </div>
            )}
            {trajectory.length > 0 && (
              <div style={{
                marginTop: 16,
                paddingTop: 14,
                borderTop: `1px solid ${c.border.secondary}`,
                display: 'flex',
                flexDirection: 'column',
                gap: 10,
              }}>
                {trajectory.map((event, index) => {
                  const detailSummary = summarizeTrajectoryDetails(event.details);
                  const toolCalls = extractToolCalls(event.details);
                  return (
                    <div
                      key={`${event.timestamp}-${index}`}
                      style={{
                        display: 'grid',
                        gridTemplateColumns: '72px 1fr',
                        gap: 10,
                        alignItems: 'start',
                      }}
                    >
                      <div style={{
                        fontSize: 11,
                        color: c.text.muted,
                        fontFamily: GS_FONTS.mono,
                        paddingTop: 1,
                      }}>
                        {formatClockTime(event.timestamp)}
                      </div>
                      <div>
                        <div style={{ fontSize: 12, color: c.text.primary, lineHeight: 1.45 }}>
                          {event.message || event.step || event.status}
                        </div>
                        <div style={{ marginTop: 2, fontSize: 11, color: c.text.muted }}>
                          {event.status} • {event.progress}%{event.step ? ` • ${event.step}` : ''}
                        </div>
                        {detailSummary && (
                          <div style={{ marginTop: 2, fontSize: 11, color: c.text.secondary }}>
                            {detailSummary}
                          </div>
                        )}
                        {toolCalls.length > 0 && (
                          <div style={{
                            marginTop: 6,
                            display: 'flex',
                            flexDirection: 'column',
                            gap: 6,
                          }}>
                            {toolCalls.map((toolCall, toolIndex) => (
                              <div key={`${toolCall.display}-${toolIndex}`}>
                                <code style={{
                                  fontSize: 11,
                                  fontFamily: GS_FONTS.mono,
                                  color: c.accent.blue,
                                  background: c.accent.blueBg,
                                  padding: '2px 6px',
                                  borderRadius: 6,
                                  display: 'inline-block',
                                }}>
                                  {toolCall.display}
                                </code>
                                {toolCall.resultPreview && (
                                  <div style={{ marginTop: 4, fontSize: 11, color: c.text.secondary, lineHeight: 1.4 }}>
                                    {toolCall.resultPreview}
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                        {event.error && (
                          <div style={{ marginTop: 2, fontSize: 11, color: '#cf222e' }}>
                            {event.error}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={{
      flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center', padding: '40px 32px',
      fontFamily: "'Inter', " + GS_FONTS.ui, background: c.bg.primary,
      boxSizing: 'border-box',
    }}>
      {/* Hero icon + title */}
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', marginBottom: 28 }}>
        <div style={{ position: 'relative', width: 72, height: 72, marginBottom: 18 }}>
          <div style={{
            width: 72, height: 72, borderRadius: 18,
            background: `linear-gradient(135deg, ${c.accent.blueBg}, ${c.accent.purpleBg})`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            border: `1px solid ${c.border.secondary}`,
          }}>
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke={c.accent.blue} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
              <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
            </svg>
          </div>
          <div style={{
            position: 'absolute', bottom: -3, right: -3, width: 24, height: 24,
            borderRadius: 7, background: c.accent.greenBg,
            border: `2px solid ${c.bg.primary}`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={c.accent.green} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>
            </svg>
          </div>
        </div>
        <h2 style={{
          fontSize: 20, fontWeight: 700, color: c.text.primary,
          margin: 0, letterSpacing: '-0.02em', textAlign: 'center',
        }}>
          {repoName.replace(/_claude$/, '')}
        </h2>
        <p style={{
          fontSize: 13, color: c.text.muted, margin: '6px 0 0',
          textAlign: 'center', lineHeight: 1.5,
        }}>
          Generate structured documentation from your knowledge graph
        </p>
      </div>

      <div style={{ maxWidth: 520, width: '100%' }}>
        {/* Graph stats — compact inline */}
        <div style={{
          display: 'flex', gap: 8, marginBottom: 18, justifyContent: 'center',
        }}>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 6,
            background: c.accent.blueBg, borderRadius: 8, padding: '5px 12px',
          }}>
            <span style={{ fontSize: 14, fontWeight: 700, color: c.accent.blue, fontFamily: GS_FONTS.mono }}>
              {graphStats.node_count.toLocaleString()}
            </span>
            <span style={{ fontSize: 11, color: c.text.muted, fontWeight: 500 }}>nodes</span>
          </div>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 6,
            background: c.accent.purpleBg, borderRadius: 8, padding: '5px 12px',
          }}>
            <span style={{ fontSize: 14, fontWeight: 700, color: c.accent.purple, fontFamily: GS_FONTS.mono }}>
              {graphStats.relationship_count.toLocaleString()}
            </span>
            <span style={{ fontSize: 11, color: c.text.muted, fontWeight: 500 }}>edges</span>
          </div>
          {graphStats.sync_enabled && (
            <div style={{
              display: 'flex', alignItems: 'center', gap: 4,
              background: c.accent.greenBg, borderRadius: 8, padding: '5px 10px',
            }}>
              <div style={{ width: 6, height: 6, borderRadius: '50%', background: c.accent.green }} />
              <span style={{ fontSize: 11, color: c.accent.green, fontWeight: 500 }}>Sync</span>
            </div>
          )}
        </div>

        {/* Error message */}
        {genProgress.message && !genProgress.generating && (genProgress.progress === 0 || genProgress.status === 'stalled') && (
          <div style={{
            padding: '12px 16px', marginBottom: 16, borderRadius: 10,
            background: genProgress.status === 'stalled'
              ? (theme === 'dark' ? 'rgba(96, 165, 250, 0.08)' : 'rgba(29, 78, 216, 0.06)')
              : (theme === 'dark' ? 'rgba(248, 81, 73, 0.08)' : 'rgba(207, 34, 46, 0.06)'),
            border: `1px solid ${genProgress.status === 'stalled'
              ? (theme === 'dark' ? 'rgba(96, 165, 250, 0.25)' : 'rgba(29, 78, 216, 0.15)')
              : (theme === 'dark' ? 'rgba(248, 81, 73, 0.2)' : 'rgba(207, 34, 46, 0.15)')}`,
            color: genProgress.status === 'stalled'
              ? (theme === 'dark' ? '#93c5fd' : '#1d4ed8')
              : (theme === 'dark' ? '#ff7b72' : '#cf222e'),
            fontSize: 13, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0 }}>
                <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
              </svg>
              <span>{genProgress.message}</span>
            </div>
            {genProgress.status === 'stalled' && genProgress.taskId && (
              <button
                onClick={() => onResume(genProgress.taskId)}
                style={{
                  border: 'none',
                  borderRadius: 8,
                  padding: '8px 12px',
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: 'pointer',
                  color: '#fff',
                  background: theme === 'dark' ? '#2563eb' : '#1d4ed8',
                }}
              >
                Resume
              </button>
            )}
          </div>
        )}

        {/* Configuration card */}
        {graphStats.has_graph && (
          <>
            <div style={{
              width: '100%', background: c.bg.secondary, borderRadius: 14,
              padding: 24, marginBottom: 18, border: `1px solid ${c.border.secondary}`,
            }}>
              <div style={{
                fontSize: 11, fontWeight: 600, color: c.text.muted, marginBottom: 20,
                textTransform: 'uppercase', letterSpacing: '0.06em',
                display: 'flex', alignItems: 'center', gap: 6,
              }}>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.6 }}>
                  <circle cx="12" cy="12" r="3"/>
                  <path d="M12 1v2m0 18v2M4.22 4.22l1.42 1.42m12.72 12.72l1.42 1.42M1 12h2m18 0h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>
                </svg>
                Configuration
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '20px 28px' }}>
                {/* Language */}
                <div>
                  <div style={{ fontSize: 13, fontWeight: 500, color: c.text.primary, marginBottom: 10 }}>
                    Language
                  </div>
                  <div style={{
                    display: 'flex', background: c.bg.primary, borderRadius: 6,
                    padding: 2, gap: 2, border: `1px solid ${c.border.secondary}`,
                  }}>
                    {([{ value: 'zh', label: 'Chinese' }, { value: 'en', label: 'English' }] as const).map(opt => (
                      <button
                        key={opt.value}
                        onClick={() => setLanguage(opt.value as 'en' | 'zh')}
                        style={{
                          padding: '5px 12px', background: language === opt.value ? c.bg.secondary : 'transparent',
                          border: 'none', borderRadius: 4, fontSize: 12, fontWeight: 500,
                          fontFamily: GS_FONTS.ui, cursor: 'pointer',
                          color: language === opt.value ? c.text.primary : c.text.muted,
                          transition: 'all 0.15s ease',
                          boxShadow: language === opt.value ? `0 1px 2px ${theme === 'dark' ? 'rgba(0,0,0,0.2)' : 'rgba(0,0,0,0.05)'}` : 'none',
                        }}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Depth */}
                <div>
                  <div style={{ fontSize: 13, fontWeight: 500, color: c.text.primary, marginBottom: 10 }}>
                    Depth
                  </div>
                  <div style={{ display: 'flex', gap: 5 }}>
                    {[0, 1, 2, 3, 4].map(d => (
                      <button
                        key={d}
                        onClick={() => setDocDepth(d)}
                        style={{
                          width: 34, height: 34, display: 'flex', alignItems: 'center', justifyContent: 'center',
                          background: docDepth === d ? c.accent.blue : 'transparent',
                          border: `1px solid ${docDepth === d ? c.accent.blue : c.border.primary}`,
                          borderRadius: 8, fontSize: 13, fontWeight: 600, fontFamily: GS_FONTS.mono,
                          cursor: 'pointer', color: docDepth === d ? '#fff' : c.text.secondary,
                          transition: 'all 0.15s ease',
                        }}
                        onMouseEnter={e => { if (docDepth !== d) { e.currentTarget.style.borderColor = c.text.muted; e.currentTarget.style.color = c.text.primary; } }}
                        onMouseLeave={e => { if (docDepth !== d) { e.currentTarget.style.borderColor = c.border.primary; e.currentTarget.style.color = c.text.secondary; } }}
                      >
                        {d}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Focus */}
                <div>
                  <div style={{ fontSize: 13, fontWeight: 500, color: c.text.primary, marginBottom: 10 }}>
                    Focus
                    <span style={{ color: c.text.muted, fontWeight: 400, marginLeft: 6, fontSize: 12 }}>(optional)</span>
                  </div>
                  <textarea
                    value={focus}
                    onChange={e => setFocus(e.target.value)}
                    placeholder="API design, performance..."
                    style={{
                      width: '100%', minHeight: 34, height: 34, padding: '7px 12px',
                      background: c.bg.primary, border: `1px solid ${c.border.primary}`,
                      borderRadius: 8, fontSize: 13, fontFamily: "'Inter', " + GS_FONTS.ui,
                      color: c.text.primary, resize: 'none', outline: 'none',
                      transition: 'border-color 0.15s ease', boxSizing: 'border-box',
                    }}
                    onFocus={e => { e.currentTarget.style.borderColor = c.accent.blue; }}
                    onBlur={e => { e.currentTarget.style.borderColor = c.border.primary; }}
                  />
                </div>

                {/* Model */}
                <div>
                  <div style={{ fontSize: 13, fontWeight: 500, color: c.text.primary, marginBottom: 10 }}>
                    Model
                  </div>
                  <ModelCombobox
                    value={selectedModel}
                    onChange={setSelectedModel}
                    theme={theme as any}
                    tiers={modelTiers}
                    placeholder="Select or type model..."
                    style={{ width: '100%' }}
                  />
                </div>
              </div>
            </div>

            {/* Generate button */}
            <button
              onClick={() => onGenerate({ model: selectedModel || undefined, doc_depth: docDepth, language })}
              style={{
                width: '100%', padding: '12px 28px', background: c.accent.blue,
                border: 'none', borderRadius: 10, fontSize: 14, fontWeight: 600,
                fontFamily: "'Inter', " + GS_FONTS.ui, letterSpacing: '-0.01em',
                color: '#fff', cursor: 'pointer', transition: 'all 0.2s ease',
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
              }}
              onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-1px)'; e.currentTarget.style.boxShadow = `0 4px 16px ${c.accent.blueBg}`; }}
              onMouseLeave={e => { e.currentTarget.style.transform = 'translateY(0)'; e.currentTarget.style.boxShadow = 'none'; }}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>
              </svg>
              Generate Documentation
            </button>

            <p style={{ marginTop: 12, fontSize: 12, color: c.text.muted, textAlign: 'center' }}>
              Estimated time: ~2-5 minutes depending on repository size
            </p>

            {/* Feature hints */}
            <div style={{ display: 'flex', gap: 16, marginTop: 12, width: '100%' }}>
              {[
                { icon: 'M9 12l2 2 4-4', label: 'Knowledge graph analysis' },
                { icon: 'M12 3v18M3 12h18', label: 'Cross-reference linking' },
                { icon: 'M4 6h16M4 12h16M4 18h10', label: 'Multi-level depth' },
              ].map((feat, i) => (
                <div key={i} style={{
                  flex: 1, display: 'flex', alignItems: 'center', gap: 6,
                  fontSize: 11, color: c.text.muted,
                }}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={c.accent.green} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
                    <path d={feat.icon}/>
                  </svg>
                  {feat.label}
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

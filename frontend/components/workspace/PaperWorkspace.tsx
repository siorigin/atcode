'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect, useLayoutEffect, useMemo, useCallback, useRef } from 'react';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';
import { useOverviewData } from '@/lib/hooks/useOverviewData';
import { listGraphProjects } from '@/lib/graph-api';
import { useLayoutTree } from '@/lib/hooks/useLayoutTree';
import type { PanelId } from '@/lib/layout-tree';
import { PanelWorkspace, type PanelSlot } from './PanelWorkspace';
import { PaperInfoPanel } from './PaperInfoPanel';
import { OverviewDocV2, LeftNavigation } from '@/components/OverviewDocV2';
import { RepoViewerLayout, type RepoViewerHandle } from '@/components/repo-viewer';
import { FloatingChatWidget } from '@/components/FloatingChatWidget';
import { getPaperPdfUrl } from '@/lib/papers-api';
import type { PaperInfo } from '@/components/papers/PaperDetailView';

// SVG icons
const InfoIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="10" /><line x1="12" y1="16" x2="12" y2="12" /><line x1="12" y1="8" x2="12.01" y2="8" />
  </svg>
);

const PdfIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" />
  </svg>
);

const OverviewIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M4 6h16M4 12h16M4 18h10" />
  </svg>
);

const CodeIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" />
  </svg>
);

const ChatIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
  </svg>
);

interface PaperWorkspaceProps {
  paper: PaperInfo;
  hasDoc: boolean;
  onBack: () => void;
  onPipelineComplete?: () => void;
}

export function PaperWorkspace({ paper, hasDoc, onBack, onPipelineComplete }: PaperWorkspaceProps) {
  const { theme } = useTheme();
  const colors = getThemeColors(theme);

  // Signal GlobalChatProvider to hide while this workspace is mounted
  useLayoutEffect(() => {
    window.dispatchEvent(new CustomEvent('atcode:managed-chat', { detail: true }));
    return () => {
      window.dispatchEvent(new CustomEvent('atcode:managed-chat', { detail: false }));
    };
  }, []);

  const [associatedRepo, setAssociatedRepo] = useState<string | null>(null);
  const [docAvailable, setDocAvailable] = useState(hasDoc);

  useEffect(() => { setDocAvailable(hasDoc); }, [hasDoc]);

  // Try to find associated repo from github URLs
  const [repoCheckTrigger, setRepoCheckTrigger] = useState(0);
  useEffect(() => {
    if (paper.githubUrls.length === 0) { setAssociatedRepo(null); return; }
    const checkRepos = async () => {
      try {
        const data = await listGraphProjects();
        const projects: string[] = (data.projects || []).map((p: any) => p.name);
        for (const url of paper.githubUrls) {
          const match = url.match(/github\.com\/([^/]+\/[^/]+)/);
          if (!match) continue;
          const repoPath = match[1];
          const repoName = repoPath.split('/').pop() || '';
          const candidates = [repoName, `${repoName}_claude`, repoPath.replace('/', '_'), `${repoPath.replace('/', '_')}_claude`];
          for (const candidate of candidates) {
            if (projects.includes(candidate)) { setAssociatedRepo(candidate); return; }
          }
        }
        setAssociatedRepo(null);
      } catch { setAssociatedRepo(null); }
    };
    checkRepos();
  }, [paper.githubUrls, repoCheckTrigger]);

  const hasRepo = !!associatedRepo;
  const overviewData = useOverviewData(associatedRepo);
  const [navCollapsed, setNavCollapsed] = useState(() => {
    if (typeof window === 'undefined') return false;
    try { return localStorage.getItem('workspace:nav-collapsed') === 'true'; } catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem('workspace:nav-collapsed', String(navCollapsed)); } catch {}
  }, [navCollapsed]);

  const availablePanels = useMemo<PanelId[]>(() => {
    const panels: PanelId[] = ['info'];
    if (docAvailable) panels.push('pdf');
    if (hasRepo) panels.push('overview', 'code');
    panels.push('chat');
    return panels;
  }, [docAvailable, hasRepo]);

  const defaultActive = useMemo<PanelId[]>(() => {
    if (hasRepo && docAvailable) return ['pdf', 'code', 'chat'];
    if (docAvailable) return ['pdf', 'chat'];
    return ['info', 'chat'];
  }, [hasRepo, docAvailable]);

  const workspace = useLayoutTree(
    `workspace:paper:${paper.paperId}`,
    availablePanels,
    defaultActive
  );

  // When context changes (doc becomes available, repo detected), ensure default panels are shown
  const prevDocRef = useRef(docAvailable);
  const prevRepoRef = useRef(hasRepo);
  useEffect(() => {
    const docChanged = docAvailable && !prevDocRef.current;
    const repoChanged = hasRepo && !prevRepoRef.current;
    prevDocRef.current = docAvailable;
    prevRepoRef.current = hasRepo;
    if (docChanged || repoChanged) {
      for (const p of defaultActive) {
        if (availablePanels.includes(p)) {
          workspace.ensurePanelActive(p);
        }
      }
    }
  }, [docAvailable, hasRepo, defaultActive, availablePanels, workspace.ensurePanelActive]);

  const chatRepoName = associatedRepo || '__papers__';
  const activeContext = useMemo(() => ({
    documentTitle: paper.title,
    filePath: paper.paperId,
    pageType: 'paper' as const,
    sourcePaperId: paper.paperId,
    sourcePaperTitle: paper.title,
  }), [paper.title, paper.paperId]);

  // Ref for the workspace's code panel RepoViewer — used to navigate from chat clicks
  const repoViewerRef = useRef<RepoViewerHandle>(null);

  // When chat clicks a code entity, navigate within the workspace code panel (not the floating one)
  const handleNavigateToNode = useCallback((qualifiedName: string) => {
    // Ensure the code panel is visible
    if (hasRepo) {
      workspace.ensurePanelActive('code' as PanelId);
    }
    // Navigate within the embedded RepoViewer
    repoViewerRef.current?.navigateTo(qualifiedName);
  }, [hasRepo, workspace.ensurePanelActive]);

  const handlePipelineComplete = useCallback(() => {
    setDocAvailable(true);
    // Re-check for associated repos (pipeline may have built one with auto_build_repos)
    setRepoCheckTrigger(n => n + 1);
    onPipelineComplete?.();
  }, [onPipelineComplete]);

  const panels: PanelSlot[] = useMemo(() => {
    const slots: PanelSlot[] = [
      {
        id: 'info' as PanelId,
        title: 'Info',
        icon: <InfoIcon />,
        render: () => (
          <PaperInfoPanel paper={paper} hasDoc={docAvailable} onPipelineComplete={handlePipelineComplete} />
        ),
      },
    ];

    if (docAvailable) {
      slots.push({
        id: 'pdf' as PanelId,
        title: 'PDF',
        icon: <PdfIcon />,
        render: () => (
          <iframe src={getPaperPdfUrl(paper.paperId)} style={{ width: '100%', height: '100%', border: 'none' }} title={paper.title} />
        ),
      });
    }

    if (hasRepo && associatedRepo) {
      slots.push({
        id: 'overview' as PanelId,
        title: 'Overview',
        icon: <OverviewIcon />,
        render: () => (
          <div style={{ display: 'flex', height: '100%', overflow: 'hidden', position: 'relative' }}>
            {overviewData.overviewIndex?.tree && overviewData.overviewIndex.tree.length > 0 && (
              <div style={{
                width: navCollapsed ? 0 : 200, minWidth: navCollapsed ? 0 : 160,
                borderRight: navCollapsed ? 'none' : `1px solid ${colors.border}`,
                overflow: navCollapsed ? 'hidden' : 'auto', flexShrink: 0,
                transition: 'width 0.15s ease, min-width 0.15s ease',
              }}>
                {!navCollapsed && (
                  <LeftNavigation
                    theme={theme}
                    tree={overviewData.overviewIndex.tree}
                    currentPath={overviewData.currentDocPath}
                    onNavigate={overviewData.handleNavigate}
                    isCollapsed={false}
                    onToggle={() => {}}
                  />
                )}
              </div>
            )}
            {overviewData.overviewIndex?.tree && overviewData.overviewIndex.tree.length > 0 && (
              <button
                onClick={() => setNavCollapsed(c => !c)}
                style={{
                  position: 'absolute', top: 4, left: navCollapsed ? 4 : 196, zIndex: 20,
                  width: 24, height: 24, padding: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
                  background: colors.accent, border: 'none', borderRadius: '50%',
                  cursor: 'pointer', color: '#fff', fontSize: 11, lineHeight: 1,
                  boxShadow: '0 2px 6px rgba(0,0,0,0.2)',
                  transition: 'left 0.15s ease',
                }}
                title={navCollapsed ? 'Show sidebar' : 'Hide sidebar'}
              >
                {navCollapsed ? '\u25B6' : '\u25C0'}
              </button>
            )}
            <div style={{ flex: 1, overflow: 'hidden', minWidth: 0 }}>
              <OverviewDocV2
                repoName={associatedRepo}
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
              />
            </div>
          </div>
        ),
      });

      slots.push({
        id: 'code' as PanelId,
        title: 'Code',
        icon: <CodeIcon />,
        render: () => <RepoViewerLayout ref={repoViewerRef} repoName={associatedRepo} />,
      });
    }

    slots.push({
      id: 'chat' as PanelId,
      title: 'Chat',
      icon: <ChatIcon />,
      render: () => (
        <FloatingChatWidget
          repoName={chatRepoName}
          isOpen={true}
          onToggle={() => {}}
          embedded
          activeContext={activeContext}
          contextId={`paper:${paper.paperId}`}
          onNavigateToNode={hasRepo ? handleNavigateToNode : undefined}
        />
      ),
    });

    return slots;
  }, [paper, docAvailable, hasRepo, associatedRepo, overviewData, chatRepoName, activeContext, theme, colors, handlePipelineComplete, navCollapsed, handleNavigateToNode]);

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{
        height: 36, minHeight: 36, display: 'flex', alignItems: 'center',
        padding: '0 12px', borderBottom: `1px solid ${colors.border}`,
        background: colors.card, flexShrink: 0, gap: 8,
      }}>
        <button
          onClick={onBack}
          style={{
            background: 'none', border: 'none', color: colors.accent,
            cursor: 'pointer', fontSize: 13, padding: '4px 8px',
            fontFamily: "'Inter', sans-serif",
          }}
        >
          &larr; Back
        </button>
        <span style={{
          fontSize: 13, fontWeight: 600, color: colors.text,
          flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {paper.title}
        </span>
      </div>
      <div style={{ flex: 1, overflow: 'hidden' }}>
        <PanelWorkspace
          panels={panels}
          layout={workspace.layout}
          activePanels={workspace.activePanels}
          onTogglePanel={workspace.togglePanel}
          onMovePanel={workspace.movePanel}
          onUpdateSizes={workspace.updateSizes}
        />
      </div>
    </div>
  );
}

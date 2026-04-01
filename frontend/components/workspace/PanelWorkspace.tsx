'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { useRef, useCallback, useEffect } from 'react';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';
import type { LayoutNode, PanelId, DropPosition } from '@/lib/layout-tree';
import { getActivePanels } from '@/lib/layout-tree';
import { WorkspaceToolbar } from './WorkspaceToolbar';
import { PanelHeader } from './PanelHeader';
import { ResizeDivider } from './ResizeDivider';
import { DockingProvider, useDocking } from './DockingContext';
import { DropZoneOverlay } from './DropZoneOverlay';

export interface PanelSlot {
  id: PanelId;
  title: string;
  icon: React.ReactNode;
  render: () => React.ReactNode;
}

interface PanelWorkspaceProps {
  panels: PanelSlot[];
  layout: LayoutNode | null;
  activePanels: PanelId[];
  onTogglePanel: (id: PanelId) => void;
  onMovePanel: (panelId: PanelId, targetId: PanelId, pos: DropPosition) => void;
  onUpdateSizes: (path: number[], sizes: number[]) => void;
}

// --- Panel Container (leaf node renderer) ---

function PanelContainer({
  panel,
  onClose,
}: {
  panel: PanelSlot;
  onClose: () => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const { dragState, registerTarget, unregisterTarget } = useDocking();

  useEffect(() => {
    const el = containerRef.current;
    if (el) {
      registerTarget(panel.id, el);
      return () => unregisterTarget(panel.id);
    }
  }, [panel.id, registerTarget, unregisterTarget]);

  return (
    <div
      ref={containerRef}
      style={{
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        height: '100%',
        width: '100%',
        position: 'relative',
        opacity: dragState?.panelId === panel.id ? 0.4 : 1,
        transition: 'opacity 0.15s ease',
      }}
    >
      <PanelHeader
        title={panel.title}
        icon={panel.icon}
        panelId={panel.id}
        onClose={onClose}
      />
      <div style={{ flex: 1, overflow: 'hidden', minHeight: 0, minWidth: 0 }}>
        {panel.render()}
      </div>
      <DropZoneOverlay panelId={panel.id} containerRef={containerRef} />
    </div>
  );
}

// --- Recursive Layout Renderer ---

function LayoutRenderer({
  node,
  path,
  panels,
  onTogglePanel,
  onUpdateSizes,
}: {
  node: LayoutNode;
  path: number[];
  panels: PanelSlot[];
  onTogglePanel: (id: PanelId) => void;
  onUpdateSizes: (path: number[], sizes: number[]) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);

  if (node.type === 'leaf') {
    const panel = panels.find(p => p.id === node.panelId);
    if (!panel) return null;
    return <PanelContainer panel={panel} onClose={() => onTogglePanel(panel.id)} />;
  }

  const isHorizontal = node.direction === 'horizontal';

  return (
    <div
      ref={containerRef}
      style={{
        display: 'flex',
        flexDirection: isHorizontal ? 'row' : 'column',
        flex: 1,
        overflow: 'hidden',
        height: '100%',
        width: '100%',
      }}
    >
      {node.children.map((child, i) => {
        // Use stable key: panelId for leaves, sorted contained panel IDs for splits
        const childKey = child.type === 'leaf'
          ? child.panelId
          : getActivePanels(child).sort().join('+');
        return (
        <React.Fragment key={childKey}>
          <div
            style={{
              [isHorizontal ? 'width' : 'height']: `${node.sizes[i]}%`,
              overflow: 'hidden',
              display: 'flex',
              flexDirection: 'column',
            }}
          >
            <LayoutRenderer
              node={child}
              path={[...path, i]}
              panels={panels}
              onTogglePanel={onTogglePanel}
              onUpdateSizes={onUpdateSizes}
            />
          </div>
          {i < node.children.length - 1 && (
            <ResizeDivider
              direction={isHorizontal ? 'vertical' : 'horizontal'}
              path={path}
              childIndex={i}
              sizes={node.sizes}
              onResizeEnd={onUpdateSizes}
              containerRef={containerRef}
            />
          )}
        </React.Fragment>
        );
      })}
    </div>
  );
}

// --- Main Component ---

export function PanelWorkspace({
  panels,
  layout,
  activePanels,
  onTogglePanel,
  onMovePanel,
  onUpdateSizes,
}: PanelWorkspaceProps) {
  const { theme } = useTheme();
  const colors = getThemeColors(theme);

  const toolbarPanels = panels.map(p => ({
    id: p.id,
    label: p.title,
    icon: p.icon,
  }));

  const handleDrop = useCallback(
    (panelId: PanelId, targetId: PanelId, pos: DropPosition) => {
      onMovePanel(panelId, targetId, pos);
    },
    [onMovePanel]
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <WorkspaceToolbar panels={toolbarPanels} activePanels={activePanels} onToggle={onTogglePanel} />
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {layout ? (
          <DockingProvider onDrop={handleDrop}>
            <LayoutRenderer
              node={layout}
              path={[]}
              panels={panels}
              onTogglePanel={onTogglePanel}
              onUpdateSizes={onUpdateSizes}
            />
          </DockingProvider>
        ) : (
          <div style={{
            flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
            color: colors.textDimmed, fontSize: 14,
          }}>
            Toggle a panel from the toolbar above
          </div>
        )}
      </div>
    </div>
  );
}

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

export type PanelId = 'pdf' | 'overview' | 'code' | 'chat' | 'info' | 'research' | 'papers';

export type DropPosition = 'top' | 'bottom' | 'left' | 'right';

export type LayoutNode =
  | { type: 'leaf'; panelId: PanelId }
  | { type: 'split'; direction: 'horizontal' | 'vertical'; children: LayoutNode[]; sizes: number[] };

// --- Helpers ---

function normalizeSizes(sizes: number[]): number[] {
  const total = sizes.reduce((a, b) => a + b, 0);
  if (total === 0) return sizes.map(() => 100 / sizes.length);
  return sizes.map(s => (s / total) * 100);
}

function equalSizes(n: number): number[] {
  return Array(n).fill(100 / n);
}

// --- Public API ---

export function containsPanel(node: LayoutNode | null, panelId: PanelId): boolean {
  if (!node) return false;
  if (node.type === 'leaf') return node.panelId === panelId;
  return node.children.some(c => containsPanel(c, panelId));
}

export function getActivePanels(node: LayoutNode | null): PanelId[] {
  if (!node) return [];
  if (node.type === 'leaf') return [node.panelId];
  return node.children.flatMap(c => getActivePanels(c));
}

export function addPanel(root: LayoutNode | null, panelId: PanelId): LayoutNode {
  const newLeaf: LayoutNode = { type: 'leaf', panelId };
  if (!root) return newLeaf;
  if (containsPanel(root, panelId)) return root;

  if (root.type === 'split' && root.direction === 'horizontal') {
    const newSizes = equalSizes(root.children.length + 1);
    return {
      type: 'split',
      direction: 'horizontal',
      children: [...root.children, newLeaf],
      sizes: newSizes,
    };
  }

  return {
    type: 'split',
    direction: 'horizontal',
    children: [root, newLeaf],
    sizes: [66, 34],
  };
}

export function removePanel(root: LayoutNode, panelId: PanelId): LayoutNode | null {
  if (root.type === 'leaf') {
    return root.panelId === panelId ? null : root;
  }

  const newChildren: LayoutNode[] = [];
  const newSizes: number[] = [];

  for (let i = 0; i < root.children.length; i++) {
    const result = removePanel(root.children[i], panelId);
    if (result) {
      newChildren.push(result);
      newSizes.push(root.sizes[i]);
    }
  }

  if (newChildren.length === 0) return null;
  if (newChildren.length === 1) return newChildren[0];

  return {
    type: 'split',
    direction: root.direction,
    children: newChildren,
    sizes: normalizeSizes(newSizes),
  };
}

export function movePanel(
  root: LayoutNode,
  panelId: PanelId,
  targetId: PanelId,
  pos: DropPosition
): LayoutNode {
  if (panelId === targetId) return root;

  // Fast path: if source and target are siblings in the same split node,
  // reorder within that node instead of remove+insert (which loses structure).
  const swapped = tryReorderSiblings(root, panelId, targetId, pos);
  if (swapped) return swapped;

  // Remove the panel first
  let cleaned = removePanel(root, panelId);
  if (!cleaned) {
    // Tree was only the moved panel — shouldn't happen in practice
    return { type: 'leaf', panelId };
  }

  // Insert adjacent to target
  return insertAdjacentToLeaf(cleaned, panelId, targetId, pos);
}

/**
 * If source and target are direct leaf children of the same split node,
 * reorder them in-place. This handles the common 2-panel swap case
 * and also multi-panel reorder within the same direction.
 */
function tryReorderSiblings(
  node: LayoutNode,
  sourceId: PanelId,
  targetId: PanelId,
  pos: DropPosition
): LayoutNode | null {
  if (node.type === 'leaf') return null;

  const sourceIdx = node.children.findIndex(c => c.type === 'leaf' && c.panelId === sourceId);
  const targetIdx = node.children.findIndex(c => c.type === 'leaf' && c.panelId === targetId);

  if (sourceIdx !== -1 && targetIdx !== -1) {
    // Both are direct children — check if the drop direction matches the split direction
    const dropDir: 'horizontal' | 'vertical' =
      pos === 'left' || pos === 'right' ? 'horizontal' : 'vertical';

    if (dropDir === node.direction) {
      // Reorder: remove source, insert at target position
      const newChildren = [...node.children];
      const newSizes = [...node.sizes];
      const [movedChild] = newChildren.splice(sourceIdx, 1);
      const [movedSize] = newSizes.splice(sourceIdx, 1);

      // Recalculate target index after removal
      let insertIdx = newChildren.findIndex(c => c.type === 'leaf' && c.panelId === targetId);
      if (insertIdx === -1) return null;

      // Insert before or after target depending on drop position
      if (pos === 'right' || pos === 'bottom') insertIdx += 1;

      newChildren.splice(insertIdx, 0, movedChild);
      newSizes.splice(insertIdx, 0, movedSize);

      return { ...node, children: newChildren, sizes: normalizeSizes(newSizes) };
    }
    // Drop direction differs from split direction — fall through to create a nested split
  }

  // Recurse into children
  for (let i = 0; i < node.children.length; i++) {
    const result = tryReorderSiblings(node.children[i], sourceId, targetId, pos);
    if (result) {
      const newChildren = [...node.children];
      newChildren[i] = result;
      return { ...node, children: newChildren };
    }
  }

  return null;
}

function insertAdjacentToLeaf(
  node: LayoutNode,
  insertId: PanelId,
  targetId: PanelId,
  pos: DropPosition
): LayoutNode {
  if (node.type === 'leaf') {
    if (node.panelId !== targetId) return node;

    const newLeaf: LayoutNode = { type: 'leaf', panelId: insertId };
    const dir: 'horizontal' | 'vertical' =
      pos === 'left' || pos === 'right' ? 'horizontal' : 'vertical';
    const before = pos === 'left' || pos === 'top';

    return {
      type: 'split',
      direction: dir,
      children: before ? [newLeaf, node] : [node, newLeaf],
      sizes: [50, 50],
    };
  }

  // Check if target is a direct child leaf — if so, we can potentially avoid nesting
  const targetIdx = node.children.findIndex(
    c => c.type === 'leaf' && c.panelId === targetId
  );

  const neededDir: 'horizontal' | 'vertical' =
    pos === 'left' || pos === 'right' ? 'horizontal' : 'vertical';

  if (targetIdx !== -1 && node.direction === neededDir) {
    // Insert in same direction — splice into children
    const newLeaf: LayoutNode = { type: 'leaf', panelId: insertId };
    const insertAt = pos === 'right' || pos === 'bottom' ? targetIdx + 1 : targetIdx;
    const newChildren = [...node.children];
    newChildren.splice(insertAt, 0, newLeaf);
    const newSizes = equalSizes(newChildren.length);
    return {
      type: 'split',
      direction: node.direction,
      children: newChildren,
      sizes: newSizes,
    };
  }

  // Recurse into children
  return {
    ...node,
    children: node.children.map(c => insertAdjacentToLeaf(c, insertId, targetId, pos)),
  };
}

export function updateSizes(root: LayoutNode, path: number[], newSizes: number[]): LayoutNode {
  if (path.length === 0) {
    if (root.type === 'split') {
      return { ...root, sizes: newSizes };
    }
    return root;
  }

  if (root.type !== 'split') return root;

  const [head, ...rest] = path;
  return {
    ...root,
    children: root.children.map((c, i) =>
      i === head ? updateSizes(c, rest, newSizes) : c
    ),
  };
}

export function validateLayout(node: LayoutNode, available: PanelId[]): LayoutNode | null {
  if (!node || !node.type) return null;
  if (node.type === 'leaf') {
    return available.includes(node.panelId) ? node : null;
  }
  if (!node.children || !Array.isArray(node.children)) return null;

  const validChildren: LayoutNode[] = [];
  const validSizes: number[] = [];

  for (let i = 0; i < node.children.length; i++) {
    const result = validateLayout(node.children[i], available);
    if (result) {
      validChildren.push(result);
      validSizes.push(node.sizes[i]);
    }
  }

  if (validChildren.length === 0) return null;
  if (validChildren.length === 1) return validChildren[0];

  return {
    type: 'split',
    direction: node.direction,
    children: validChildren,
    sizes: normalizeSizes(validSizes),
  };
}

export function buildDefaultLayout(panels: PanelId[]): LayoutNode | null {
  if (panels.length === 0) return null;
  if (panels.length === 1) return { type: 'leaf', panelId: panels[0] };

  // Two panels: side by side
  if (panels.length === 2) {
    return {
      type: 'split',
      direction: 'horizontal',
      children: panels.map(p => ({ type: 'leaf' as const, panelId: p })),
      sizes: [50, 50],
    };
  }

  // 3+ panels: first panel on left, rest stacked vertically on right
  const [first, ...rest] = panels;
  const rightChildren: LayoutNode[] = rest.map(p => ({ type: 'leaf' as const, panelId: p }));

  return {
    type: 'split',
    direction: 'horizontal',
    children: [
      { type: 'leaf', panelId: first },
      rest.length === 1
        ? { type: 'leaf' as const, panelId: rest[0] }
        : {
            type: 'split' as const,
            direction: 'vertical' as const,
            children: rightChildren,
            sizes: equalSizes(rightChildren.length),
          },
    ],
    sizes: [55, 45],
  };
}

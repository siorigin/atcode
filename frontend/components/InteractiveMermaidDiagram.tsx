'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';

// ==================== Constants ====================

const FONTS = {
  ui: '-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif',
  mono: '"JetBrains Mono", "Fira Code", "SF Mono", Monaco, Consolas, "Liberation Mono", monospace',
};

const COLORS = {
  dark: {
    bg: { primary: '#0d1117', secondary: '#161b22', tertiary: '#21262d', elevated: '#1c2128' },
    border: { primary: '#30363d', secondary: '#21262d', muted: '#373e47' },
    text: { primary: '#e6edf3', secondary: '#8b949e', muted: '#6e7681', link: '#58a6ff' },
    accent: { blue: '#58a6ff', blueBg: 'rgba(56, 139, 253, 0.15)', green: '#3fb950', greenBg: 'rgba(46, 160, 67, 0.15)', purple: '#a371f7', purpleBg: 'rgba(163, 113, 247, 0.15)' },
    // Mermaid specific colors - high contrast
    nodeBorder: '#6cb6ff',      // Lighter for better contrast
    lineStroke: '#9ca3af',       // More visible
    nodeFill: '#1c2333',         // Darker for contrast
    nodeText: '#f0f6fc',         // High contrast text
    // Multi-color node palette — low saturation, high contrast
    nodePalette: [
      { fill: '#1c2433', border: '#7ba3cc' }, // 灰蓝
      { fill: '#1e2e24', border: '#7bab8a' }, // 灰绿
      { fill: '#26202f', border: '#9b8baf' }, // 灰紫
      { fill: '#2c2520', border: '#b89b7a' }, // 灰棕
      { fill: '#1e2b2b', border: '#7aaba3' }, // 灰青
      { fill: '#2c2028', border: '#ab8a9b' }, // 灰粉
    ],
  },
  light: {
    bg: { primary: '#ffffff', secondary: '#f6f8fa', tertiary: '#f0f3f6', elevated: '#ffffff' },
    border: { primary: '#d0d7de', secondary: '#e8ebef', muted: '#d8dee4' },
    text: { primary: '#1f2328', secondary: '#656d76', muted: '#8c959f', link: '#0969da' },
    accent: { blue: '#0969da', blueBg: 'rgba(9, 105, 218, 0.08)', green: '#1a7f37', greenBg: 'rgba(26, 127, 55, 0.08)', purple: '#8250df', purpleBg: 'rgba(130, 80, 223, 0.08)' },
    // Mermaid specific colors - high contrast
    nodeBorder: '#0756c0',      // Darker for contrast
    lineStroke: '#434a54',       // More visible
    nodeFill: '#e8f0fe',         // Light blue tint
    nodeText: '#0a1628',         // High contrast text
    // Multi-color node palette — low saturation, high contrast
    nodePalette: [
      { fill: '#e4e9f0', border: '#4a6a8a' }, // 灰蓝
      { fill: '#e2ece5', border: '#4a7a5a' }, // 灰绿
      { fill: '#e8e3ed', border: '#6a5a7a' }, // 灰紫
      { fill: '#ede6df', border: '#7a6548' }, // 灰棕
      { fill: '#e0eae8', border: '#4a7a72' }, // 灰青
      { fill: '#ede0e6', border: '#7a4a62' }, // 灰粉
    ],
  },
  // Beige/Eye Comfort theme
  beige: {
    bg: { primary: '#faf8f5', secondary: '#f5f0e8', tertiary: '#ebe5d9', elevated: '#ffffff' },
    border: { primary: '#d4c8b8', secondary: '#e5ddd0', muted: '#c9bca8' },
    text: { primary: '#3d3632', secondary: '#6b5f54', muted: '#8a7b6c', link: '#8b5a2b' },
    accent: { blue: '#8b5a2b', blueBg: 'rgba(139, 90, 43, 0.12)', green: '#5d7a3a', greenBg: 'rgba(93, 122, 58, 0.12)', purple: '#7a5a8a', purpleBg: 'rgba(122, 90, 138, 0.12)' },
    // Mermaid specific colors - high contrast warm tones
    nodeBorder: '#7a4a1f',       // Darker brown
    lineStroke: '#6b5f54',       // More visible
    nodeFill: '#f5ede3',         // Warmer
    nodeText: '#2a2018',         // High contrast text
    // Multi-color node palette — low saturation, warm tones
    nodePalette: [
      { fill: '#efe8df', border: '#6b5840' }, // 暖棕
      { fill: '#e6ece2', border: '#4f6842' }, // 暖绿
      { fill: '#ebe4ec', border: '#6b5068' }, // 暖紫
      { fill: '#f0e6da', border: '#8a6a3a' }, // 暖橙
      { fill: '#e2ebe6', border: '#4a6b5e' }, // 暖青
      { fill: '#ede2e4', border: '#7a4a50' }, // 暖粉
    ],
  },
};

const getColors = (theme: 'dark' | 'light' | 'beige') => COLORS[theme];

// ==================== Types ====================

export interface NodeMetadata {
  id: string;
  label: string;
  type?: string;
  callCount?: number;
  description?: string;
  path?: string;
  edges?: string[];
}

export interface InteractiveMermaidDiagramProps {
  code: string;
  theme: 'dark' | 'light' | 'beige';
  nodeMetadata?: Record<string, NodeMetadata>;
  onNodeClick?: (nodeId: string, metadata?: NodeMetadata) => void;
  enableZoomPan?: boolean;
  enableNodeInteraction?: boolean;
  className?: string;
  /** Min width for the diagram container. Default: 600px. Prevents narrow containers for tall/thin diagrams */
  minWidth?: number;
  /** Max height for the diagram. Default: 600px */
  maxHeight?: number | string;
  /** Map of link basename to full qualified link (for making only specific text clickable) */
  linkMap?: Map<string, string>;
}

interface Transform {
  x: number;
  y: number;
  scale: number;
}

// ==================== Mermaid Validation ====================

/**
 * Check if Mermaid code is complete and ready for rendering.
 * This prevents rendering incomplete code during streaming.
 */
function isMermaidCodeComplete(code: string): boolean {
  if (!code || code.trim().length === 0) return false;

  const trimmed = code.trim();

  // Remove directives (%%) and frontmatter for type detection
  // This allows code starting with %%{init: ...}%% to be recognized
  const cleanForCheck = trimmed
    .replace(/^%%.*$/gm, '') // Remove single line directives/comments
    .replace(/^\s*[\r\n]/gm, '') // Remove empty lines
    .trim();

  // Must start with a valid diagram type declaration
  const validStartPatterns = [
    /^graph\s+(TB|BT|LR|RL|TD)/i,
    /^flowchart\s+(TB|BT|LR|RL|TD)/i,
    /^sequenceDiagram/i,
    /^classDiagram/i,
    /^stateDiagram/i,
    /^stateDiagram-v2/i,
    /^erDiagram/i,
    /^gantt/i,
    /^pie/i,
    /^mindmap/i,
    /^timeline/i,
    /^gitGraph/i,
    /^journey/i,
    /^quadrantChart/i,
    /^requirementDiagram/i,
    /^C4Context/i,
    /^C4Container/i,
    /^C4Component/i,
    /^C4Dynamic/i,
    /^C4Deployment/i,
    /^sankey-beta/i,
    /^xychart-beta/i,
    /^block-beta/i,
  ];

  const hasValidStart = validStartPatterns.some(pattern => pattern.test(cleanForCheck));
  if (!hasValidStart) return false;

  // Only check for obvious incomplete patterns at the end of the code
  // These indicate streaming is definitely in progress
  const incompletePatterns = [
    /-->\s*$/,              // Arrow without target
    /-->\|[^|]*$/,          // Arrow with incomplete label (no closing |)
    /\[\s*$/,               // Opening bracket at end with nothing after
    /\["[^"]*$/,            // Opening ["... without closing "]
    /\(\s*$/,               // Opening paren at end with nothing after
    /\{"[^"]*$/,            // Opening {"... without closing "}
    /\{\s*$/,               // Opening brace at end with nothing after
    /subgraph\s*$/i,        // Just "subgraph" at end
    /subgraph\s+\S+\s*$/i,  // "subgraph name" without content
  ];

  if (incompletePatterns.some(pattern => pattern.test(trimmed))) {
    return false;
  }

  // Check for unbalanced brackets/quotes on the last line (most common streaming truncation)
  const lastLine = trimmed.split('\n').pop()?.trim() || '';
  const openBrackets = (lastLine.match(/\[/g) || []).length;
  const closeBrackets = (lastLine.match(/\]/g) || []).length;
  const openQuotes = (lastLine.match(/"/g) || []).length;
  if (openBrackets > closeBrackets || openQuotes % 2 !== 0) {
    return false;
  }

  return true;
}

/**
 * Sanitize Mermaid code to handle special characters and common LLM generation errors.
 * This is especially important for HTML tags like <br/> which break rendering.
 *
 * COMPREHENSIVE APPROACH: Fix all known LLM error patterns to ensure reliable rendering.
 */
/**
 * Style group info extracted from LLM-generated style/classDef lines.
 * Maps node IDs to a group index (nodes in the same group share one palette color).
 */
interface StyleGroupInfo {
  /** node ID → group index (0-based). Empty map = LLM had no styles */
  nodeGroups: Map<string, number>;
  /** Number of distinct color groups the LLM used */
  groupCount: number;
}

const MERMAID_LINE_BREAK_TOKEN = '@@MERMAID_BR@@';

function normalizeMermaidLabelBreaks(content: string): string {
  let normalized = content
    .replace(/<br\s*\/?>/gi, MERMAID_LINE_BREAK_TOKEN)
    .replace(/\s*\\n\s*/g, MERMAID_LINE_BREAK_TOKEN)
    .replace(/\s*\r?\n\s*/g, MERMAID_LINE_BREAK_TOKEN);

  normalized = normalized
    .replace(new RegExp(`(?:${MERMAID_LINE_BREAK_TOKEN}){2,}`, 'g'), MERMAID_LINE_BREAK_TOKEN)
    .replace(new RegExp(`\\s*${MERMAID_LINE_BREAK_TOKEN}\\s*`, 'g'), MERMAID_LINE_BREAK_TOKEN)
    .replace(new RegExp(`^(?:${MERMAID_LINE_BREAK_TOKEN}|\\s)+|(?:${MERMAID_LINE_BREAK_TOKEN}|\\s)+$`, 'g'), '');

  return normalized;
}

function restoreMermaidLabelBreaks(content: string): string {
  return content.replace(new RegExp(MERMAID_LINE_BREAK_TOKEN, 'g'), '<br/>');
}

function sanitizeMermaidLabelContent(
  content: string,
  { convertNestedBrackets = false }: { convertNestedBrackets?: boolean } = {}
): string {
  let cleaned = normalizeMermaidLabelBreaks(content);

  // Mermaid accepts <br/> as a line-break token; strip other HTML while keeping that marker.
  cleaned = cleaned.replace(/<\/?[a-z][^>]*>/gi, ' ');

  if (convertNestedBrackets) {
    cleaned = cleaned.replace(/\[([^\]]*)\]/g, '($1)');
  }

  cleaned = cleaned.replace(/\s+/g, ' ');
  cleaned = normalizeMermaidLabelBreaks(cleaned);

  return restoreMermaidLabelBreaks(cleaned);
}

function sanitizeMermaidCode(code: string): { code: string; styleGroups: StyleGroupInfo } {
  let result = code;

  // 0. Extract LLM style grouping, then remove the lines.
  // Two patterns: (a) `style NodeId fill:...` per-node, (b) `classDef Name fill:...` + `class Node1,Node2 Name`
  const nodeGroups = new Map<string, number>();

  // -- (a) Inline `style NodeId fill:#color,...` → group by the fill color value
  const fillColorToGroup = new Map<string, number>();
  let nextGroup = 0;
  const styleLineRegex = /^\s*style\s+(\w+)\s+.*?fill\s*:\s*(#[0-9a-fA-F]{3,8}|[a-z]+)/gm;
  let m: RegExpExecArray | null;
  while ((m = styleLineRegex.exec(result)) !== null) {
    const nodeId = m[1];
    const fillColor = m[2].toLowerCase();
    if (!fillColorToGroup.has(fillColor)) {
      fillColorToGroup.set(fillColor, nextGroup++);
    }
    nodeGroups.set(nodeId, fillColorToGroup.get(fillColor)!);
  }

  // -- (b) `classDef ClassName fill:#color,...` + `class Node1,Node2 ClassName`
  const classDefColors = new Map<string, string>(); // className → fillColor
  const classDefRegex = /^\s*classDef\s+(\w+)\s+.*?fill\s*:\s*(#[0-9a-fA-F]{3,8}|[a-z]+)/gm;
  while ((m = classDefRegex.exec(result)) !== null) {
    classDefColors.set(m[1], m[2].toLowerCase());
  }
  const classAssignRegex = /^\s*class\s+([\w,]+)\s+(\w+)\s*$/gm;
  while ((m = classAssignRegex.exec(result)) !== null) {
    const nodeIds = m[1].split(',').map(s => s.trim()).filter(Boolean);
    const className = m[2];
    const fillColor = classDefColors.get(className);
    if (fillColor) {
      if (!fillColorToGroup.has(fillColor)) {
        fillColorToGroup.set(fillColor, nextGroup++);
      }
      const groupIdx = fillColorToGroup.get(fillColor)!;
      nodeIds.forEach(id => nodeGroups.set(id, groupIdx));
    }
  }

  // Now strip all style/classDef/class lines
  result = result.replace(/^\s*style\s+\w+\s+(?:fill|stroke|color|stroke-width|stroke-dasharray).*$/gm, '');
  result = result.replace(/^\s*classDef\s+\w+\s+.*$/gm, '');
  result = result.replace(/^\s*class\s+[\w,]+\s+\w+\s*$/gm, '');

  // 2. Remove any lines that are clearly not mermaid syntax (file paths, etc.)
  result = result.split('\n').filter(line => {
    const trimmed = line.trim();
    if (/^[@\/].*\.(ts|tsx|js|jsx|py|md)/.test(trimmed)) return false;
    if (/^```(?!mermaid)/.test(trimmed)) return false;
    return true;
  }).join('\n');

  // 3. Fix HTML tags inside quoted node labels: ["content<br/>more"]
  // Preserve line breaks, strip other HTML, and handle nested [] by converting to ().
  result = result.replace(/\["([^"]*)"\]/g, (match, content) => {
    if (!/(?:<br\s*\/?>|\\n|\n|<[a-z])/i.test(content) && !/\[/.test(content)) {
      return match; // No issues, return unchanged
    }

    const cleaned = sanitizeMermaidLabelContent(content, { convertNestedBrackets: true });
    return `["${cleaned}"]`;
  });

  // 4. Handle edge labels with HTML tags: -->|label<br/>more|
  result = result.replace(/(--[->]|==+>|-\.+->?)\|([^|]*)\|/g, (match, arrow, content) => {
    if (!/(?:<br\s*\/?>|\\n|\n|<[a-z])/i.test(content)) {
      return match; // No HTML tags, return unchanged
    }

    const cleaned = sanitizeMermaidLabelContent(content);
    return `${arrow}|${cleaned}|`;
  });

  // 5. Fix quoted edge labels: |"text"| → |text|
  // LLMs sometimes add quotes inside edge labels which can cause parse errors
  result = result.replace(/(--[->]|==+>|-\.+->?)\|"([^"]*)"\|/g, '$1|$2|');

  // 5b. NOTE: [[...]] hyperlink processing is now handled by processMermaidCode in WikiDoc.tsx
  // We don't process [[...]] here anymore to avoid interference with link extraction

  // 6. Fix unquoted Chinese characters in node labels
  // Convert A[中文内容] to A["中文内容"], also clean HTML tags like <br/>
  result = result.replace(/\[([^\]"]*[\u4e00-\u9fa5][^\]"]*)\]/g, (match, content) => {
    // Skip if it looks like a subgraph or other special syntax
    if (/^(subgraph|end|style|class|click)/.test(content)) {
      return match;
    }
    const cleaned = sanitizeMermaidLabelContent(content);
    return `["${cleaned}"]`;
  });

  // 6b. Fix unquoted labels with HTML tags (no Chinese) — e.g. B[text<br/>more]
  // Preserve line breaks, strip other HTML, and quote the label.
  result = result.replace(/\[([^\]"]*<[a-z][^>]*>[^\]"]*)\]/gi, (match, content) => {
    if (/^(subgraph|end|style|class|click)/.test(content)) {
      return match;
    }
    const cleaned = sanitizeMermaidLabelContent(content);
    return `["${cleaned}"]`;
  });

  // 7. Unicode arrows: keep as-is inside labels (mermaid handles Unicode text fine).
  // Only a problem if used as actual flow arrows outside labels, which is rare.
  // Previous approach of replacing → with -> BROKE edge labels like |text→more|
  // by introducing mermaid arrow syntax inside label content.

  // 7b. Fix LaTeX-style math that LLMs sometimes add
  result = result.replace(/\$([^$]+)\$/g, '$1');

  // 8. Clean up any double quotes that got doubled
  result = result.replace(/\[""+/g, '["');
  result = result.replace(/""+\]/g, '"]');

  // 9. Fix sequence diagram messages with Chinese text
  // Mermaid sometimes has issues with non-ASCII characters in message text
  // Wrap the message text in quotes to ensure proper parsing
  result = result.replace(/^(\s*)(\w+)(\s*(?:->>|-->>|\-\->>|\->)(?:\[[^\]]*\])?\s*)(\w+)(\s*:\s*)([^\n]*?)$/gm, (match, indent, from, arrow, to, colon, message) => {
    // If message contains Chinese or special characters and isn't wrapped, wrap it
    if (message && /[\u4e00-\u9fa5\u3000-\u303f\uff00-\uffef]/.test(message) && !message.startsWith('"')) {
      // Also escape any quotes inside the message
      const escaped = message.replace(/"/g, '\\"');
      return `${indent}${from}${arrow}${to}${colon}"${escaped}"`;
    }
    return match;
  });

  // 10. Fix flowchart edge labels using sequence diagram syntax
  // LLMs sometimes use "A --> B : label" instead of "A -->|label| B"
  // This only applies to flowchart/graph diagrams, not sequence diagrams
  const isFlowchart = /^(flowchart|graph)\s+(TB|BT|LR|RL|TD)/im.test(result);
  if (isFlowchart) {
    // Pattern: NodeA arrow NodeB : "label" or NodeA arrow NodeB : label
    // Arrows can be: -->, --->, ---->, <-->, <--->, -.->. ==>, etc.
    // Convert to: NodeA arrow|label| NodeB
    result = result.replace(
      /^(\s*)(\w+)(\s*)(<?-+>|<?\.+-\.?>|<?=+>|<-+>)(\s*)(\w+)(\s*:\s*)("?)([^"\n]+)\8\s*$/gm,
      (match, indent, nodeA, space1, arrow, space2, nodeB, colonPart, quote, label) => {
        // Clean up the label (remove extra whitespace)
        const cleanLabel = label.trim();
        // Return correct flowchart syntax: NodeA arrow|label| NodeB
        return `${indent}${nodeA}${space1}${arrow}|${cleanLabel}|${space2}${nodeB}`;
      }
    );
  }

  // 11. Final safety net: remove stray HTML globally, but keep <br/> because Mermaid
  // uses it as a cross-diagram line-break token in multiple render paths.
  result = result.replace(/<(?!br\s*\/?>)[a-z][^>]*>/gi, '');
  result = result.replace(/<\/(?!br\s*\/?>)[a-z][^>]*>/gi, '');

  // 12. Canonicalize quoted square-label breaks to <br/> so the line break survives
  // both parsing and rendering across Mermaid diagram types.
  result = result.replace(/\["([^"]*)"\]/g, (match, content) => {
    if (!/(?:<br\s*\/?>|\\n|\n)/i.test(content)) return match;
    const fixed = restoreMermaidLabelBreaks(normalizeMermaidLabelBreaks(content));
    return `["${fixed}"]`;
  });

  // 13. Canonicalize edge-label breaks for the same reason.
  result = result.replace(/(--[->]|==+>|-\.+->?)\|([^|]*)\|/g, (match, arrow, content) => {
    if (!/(?:<br\s*\/?>|\\n|\n)/i.test(content)) return match;
    const fixed = restoreMermaidLabelBreaks(normalizeMermaidLabelBreaks(content));
    return `${arrow}|${fixed}|`;
  });

  return { code: result, styleGroups: { nodeGroups, groupCount: nextGroup } };
}

// ==================== Mermaid Cache ====================

// Global cache for rendered SVGs to avoid re-rendering
const svgCache = new Map<string, string>();

function getCacheKey(code: string, theme: string): string {
  return `${theme}:${code}`;
}

function getTextVariantsFromElement(el: Element): string[] {
  const variants = new Set<string>();
  const directText = el.textContent?.trim();
  if (directText) variants.add(directText);

  const tspans = el.querySelectorAll('tspan');
  tspans.forEach((tspan) => {
    const text = tspan.textContent?.trim();
    if (text) variants.add(text);
  });

  return Array.from(variants);
}

function extractMermaidLabelFragments(text: string): string[] {
  const normalized = text.replace(/\s+/g, ' ').trim();
  if (!normalized) return [];

  const fragments = new Set<string>([normalized]);
  normalized
    .split(/\s*(?:•|·|\/|\||,)\s*|\s*\n\s*/g)
    .map(part => part.trim())
    .filter(Boolean)
    .forEach(part => fragments.add(part));

  return Array.from(fragments);
}

// ==================== Main Component ====================

export const InteractiveMermaidDiagram: React.FC<InteractiveMermaidDiagramProps> = ({
  code,
  theme,
  nodeMetadata: _nodeMetadata = {},
  onNodeClick: _onNodeClick,
  enableZoomPan = true,
  enableNodeInteraction: _enableNodeInteraction = false,
  className = '',
  maxHeight = 1000,
  linkMap,
}) => {
  const [svg, setSvg] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [isClient, setIsClient] = useState(false);
  const [loading, setLoading] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [svgDimensions, setSvgDimensions] = useState<{ width: number; height: number } | null>(null);

  // Multi-link menu state
  const [multiLinkMenu, setMultiLinkMenu] = useState<{
    x: number;
    y: number;
    links: string[];
    displayText: string;
  } | null>(null);

  // Transform state for zoom/pan
  const [transform, setTransform] = useState<Transform>({ x: 0, y: 0, scale: 1 });
  // Track whether user has interacted with zoom/pan (so we can preserve it across theme re-renders)
  const userHasInteractedRef = useRef(false);
  // Track the last code that was rendered, to distinguish code-change vs theme-only change
  const lastRenderedCodeRef = useRef<string>('');

  // Hover-based scroll activation state
  const [isScrollActive, setIsScrollActive] = useState(false);
  const hoverTimerRef = useRef<NodeJS.Timeout | null>(null);
  const HOVER_ACTIVATION_DELAY = 300; // ms before scroll zoom activates

  // Refs
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const isPanningRef = useRef(false);
  const lastPanPointRef = useRef({ x: 0, y: 0 });
  const fullscreenContainerRef = useRef<HTMLDivElement>(null);

  // Min/max scale - allow up to 500% zoom
  const minScale = 0.1;
  const maxScale = 5;

  // ==================== Mermaid Rendering ====================

  useEffect(() => {
    setIsClient(true);
  }, []);

  // Memoize clean code to avoid recalculation
  const cleanCode = useMemo(() => {
    return code
      .replace(/^```mermaid\n?/i, '')
      .replace(/\n?```$/i, '')
      .trim();
  }, [code]);

  // Track previous valid SVG for fallback during streaming
  const lastValidSvgRef = useRef<string>('');
  const lastValidCodeRef = useRef<string>('');
  const renderTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const safetyTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const isRenderingRef = useRef(false);
  const styleGroupsRef = useRef<StyleGroupInfo>({ nodeGroups: new Map(), groupCount: 0 });

  // Safety net: if loading stays true for more than 5 seconds, force it to false.
  // This catches ANY edge case where loading gets stuck.
  useEffect(() => {
    if (!loading) return;
    const timer = setTimeout(() => {
      console.warn('Mermaid: loading stuck for 5s, forcing to false');
      setLoading(false);
      isRenderingRef.current = false;
    }, 5000);
    return () => clearTimeout(timer);
  }, [loading]);

  useEffect(() => {
    if (!isClient || !cleanCode) return;

    // Each effect invocation gets a unique "cancelled" flag so that stale
    // async work from a previous run never touches state.
    let cancelled = false;

    // Sanitize the code first
    const { code: sanitizedCode, styleGroups } = sanitizeMermaidCode(cleanCode);
    styleGroupsRef.current = styleGroups;

    // Skip if code hasn't changed from last successful render
    if (sanitizedCode === lastValidCodeRef.current && lastValidSvgRef.current) {
      setSvg(lastValidSvgRef.current);
      setError(null);
      setLoading(false);
      return;
    }

    // Check cache first for better performance
    const cacheKey = getCacheKey(sanitizedCode, theme);
    if (svgCache.has(cacheKey)) {
      const cachedSvg = svgCache.get(cacheKey)!;
      setSvg(cachedSvg);
      lastValidSvgRef.current = cachedSvg;
      lastValidCodeRef.current = sanitizedCode;
      lastRenderedCodeRef.current = sanitizedCode;
      setError(null);
      setLoading(false);
      return;
    }

    // Check if code is complete before attempting to render
    const isComplete = isMermaidCodeComplete(cleanCode);

    if (!isComplete) {
      // Keep showing previous valid render while streaming
      if (lastValidSvgRef.current) {
        setSvg(lastValidSvgRef.current);
        setError(null);
        setLoading(false);
      }
      // Don't attempt to render incomplete code
      return;
    }

    // Clear any pending render
    if (renderTimeoutRef.current) {
      clearTimeout(renderTimeoutRef.current);
    }

    // Clear any existing safety timeout
    if (safetyTimeoutRef.current) {
      clearTimeout(safetyTimeoutRef.current);
    }

    // Always reset rendering lock when a new effect starts — the previous
    // async renderDiagram is now stale (cancelled=true) and will no-op.
    isRenderingRef.current = false;

    // Always show loading when starting a new render attempt
    setLoading(true);

    // Safety timeout — uses ref check (not stale closure) and fires sooner
    safetyTimeoutRef.current = setTimeout(() => {
      if (cancelled) return;
      // Check ref (always current) instead of stale `loading` closure
      if (isRenderingRef.current) {
        console.warn('Mermaid rendering timed out', cleanCode.substring(0, 50));
        setError('Rendering timed out');
        setLoading(false);
        isRenderingRef.current = false;
      }
    }, 8000);

    const renderDiagram = async () => {
      if (cancelled) return;
      isRenderingRef.current = true;

      try {
        let mermaid;
        try {
          mermaid = (await import('mermaid')).default;
        } catch {
          console.warn('Mermaid module not available');
          if (!cancelled) {
            setError('Mermaid diagram support not available');
            setLoading(false);
          }
          isRenderingRef.current = false;
          return;
        }

        if (cancelled) { isRenderingRef.current = false; return; }

        const c = getColors(theme);

        // Use theme-specific colors for Mermaid diagrams
        const nodeBorderColor = c.nodeBorder;
        const lineStrokeColor = c.lineStroke;
        const nodeFillColor = c.nodeFill;

        mermaid.initialize({
          startOnLoad: false,
          theme: 'base', // Use base theme for full customization
          securityLevel: 'loose',
          suppressErrorRendering: true, // Prevent error messages from being rendered to DOM
          fontFamily: FONTS.ui,
          flowchart: {
            useMaxWidth: true,  // Changed to true to prevent overflow
            htmlLabels: false,
            curve: 'basis', // Smoother curves
            padding: 20,
            nodeSpacing: 50,
            rankSpacing: 50,
          },
          sequence: {
            useMaxWidth: true,
            actorMargin: 80,
            noteMargin: 12,
            messageMargin: 40,
            mirrorActors: true,
            actorFontFamily: FONTS.ui,
            noteFontFamily: FONTS.ui,
            messageFontFamily: FONTS.ui,
          },
          themeVariables: {
            darkMode: theme === 'dark',
            background: c.bg.primary,
            textColor: c.text.primary,
            primaryColor: nodeFillColor,
            primaryTextColor: c.nodeText,
            primaryBorderColor: nodeBorderColor,
            lineColor: lineStrokeColor,
            secondaryColor: c.bg.tertiary,
            secondaryTextColor: c.text.primary,
            tertiaryColor: c.bg.primary,
            tertiaryTextColor: c.text.primary,
            fontFamily: FONTS.ui,
            fontSize: '18px',  // Increased from 16px to 18px
            edgeLabelBackground: 'transparent',
            clusterBkg: c.bg.elevated,
            clusterBorder: c.border.primary,
            clusterTextColor: c.text.primary,
            actorTextColor: c.text.primary,
            actorBorder: c.border.primary,
            actorBkg: c.bg.elevated,
            signalColor: c.text.primary,
            signalTextColor: c.text.primary,
            labelBoxBkgColor: c.bg.elevated,
            labelBoxBorderColor: c.border.primary,
            labelTextColor: c.text.primary,
            loopTextColor: c.text.primary,
            noteTextColor: c.text.primary,
            noteBkgColor: c.bg.elevated,
            noteBorderColor: c.border.primary,
            activationBorderColor: c.border.primary,
            activationBkgColor: c.bg.tertiary,
            sequenceNumberColor: c.text.primary,
            // More visible node styling
            nodeBorder: nodeBorderColor,
            mainBkg: nodeFillColor,
          },
        });

        const id = `mermaid-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;

        // Wrap mermaid.render with a timeout to catch hangs
        let renderedSvg: string;
        try {
          const result = await Promise.race([
            mermaid.render(id, sanitizedCode),
            new Promise<never>((_, reject) =>
              setTimeout(() => reject(new Error('Mermaid render timed out')), 6000)
            ),
          ]);
          renderedSvg = result.svg;
        } catch (parseError) {
          // Log as warning (not error) — parse failures are expected during streaming
          console.warn('⚠️ Mermaid parse error:', (parseError as Error)?.message?.slice(0, 120));
          console.debug('⚠️ Failed code:', sanitizedCode);

          // Clean up any error elements mermaid might have created
          const errorElements = document.querySelectorAll(`#d${id}, .mermaid-error, [id^="mermaid-"]`);
          errorElements.forEach(el => {
            if (el.textContent?.includes('Syntax error') || el.textContent?.includes('mermaid version')) {
              el.remove();
            }
          });
          throw parseError;
        }

        if (cancelled) { isRenderingRef.current = false; return; }

        // Post-process: Rewrite hardcoded rect highlight backgrounds for dark/beige themes.
        // Mermaid's `rect rgb(R,G,B)` directive produces inline-styled <rect> fills
        // that are always light-colored. In dark mode this makes text invisible.
        // We replace them with semi-transparent theme-appropriate colors.
        if (theme === 'dark' || theme === 'beige') {
          // Sequence diagram highlight rects have inline style="fill: rgb(...)" or fill="rgb(...)"
          // Replace any light-colored fill with a theme-appropriate translucent overlay
          const highlightColors = theme === 'dark'
            ? [
                'rgba(56, 139, 253, 0.08)',   // blue tint
                'rgba(63, 185, 80, 0.08)',     // green tint
                'rgba(163, 113, 247, 0.08)',   // purple tint
                'rgba(227, 179, 65, 0.08)',    // amber tint
              ]
            : [
                'rgba(139, 90, 43, 0.06)',     // warm brown tint
                'rgba(93, 122, 58, 0.06)',     // warm green tint
                'rgba(122, 90, 138, 0.06)',    // warm purple tint
                'rgba(180, 120, 40, 0.06)',    // warm amber tint
              ];
          let colorIdx = 0;
          // Match fill="rgb(...)" or style="...fill: rgb(...)..." on rect elements
          renderedSvg = renderedSvg.replace(
            /<rect([^>]*?)(?:fill="rgb\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)"|style="[^"]*fill:\s*rgb\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)[^"]*")/gi,
            (match, prefix) => {
              const replacement = highlightColors[colorIdx % highlightColors.length];
              colorIdx++;
              // Check if the original used fill= attribute or style=
              if (match.includes('style=')) {
                // Replace fill inside style attribute
                return match.replace(/fill:\s*rgb\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)/i, `fill: ${replacement}`);
              }
              // Replace fill= attribute
              return `<rect${prefix}fill="${replacement}"`;
            }
          );
        }

        // Post-process: Apply rounded corners using string replacement
        // This is more reliable than DOMParser across different environments
        // Add rx/ry attributes to <rect> elements (excluding clusters)
        renderedSvg = renderedSvg.replace(
          /<rect(?![^>]*class="[^"]*cluster[^"]*")(?![^>]*class="[^"]*subgraph[^"]*")([^>]*)>/gi,
          '<rect$1 rx="12" ry="12">'
        );
        // Also handle rects that might have existing rx/ry (override them)
        renderedSvg = renderedSvg.replace(
          /<rect((?:(?!rx=)[^>])*)(?:rx="[^"]*")?((?:(?!ry=)[^>])*)(?:ry="[^"]*")?([^>]*)>/gi,
          (match, prefix1, prefix2, suffix) => {
            // Check if it's a cluster or subgraph
            if (match.includes('cluster') || match.includes('subgraph')) {
              return match;
            }
            return `<rect${prefix1}${prefix2} rx="12" ry="12"${suffix}>`;
          }
        );
        // Add rx/ry to polygon elements (decision diamonds) - slight rounding
        renderedSvg = renderedSvg.replace(
          /<polygon([^>]*)>/gi,
          '<polygon$1 rx="4" ry="4">'
        );

        // Save as last valid render for fallback during streaming
        lastValidSvgRef.current = renderedSvg;
        lastValidCodeRef.current = sanitizedCode;
        lastRenderedCodeRef.current = sanitizedCode;

        // Save to cache for future renders
        svgCache.set(cacheKey, renderedSvg);

        if (!cancelled) {
          setSvg(renderedSvg);
          setError(null);
          setLoading(false);
        }
      } catch (e: unknown) {
        // Clean up any error elements mermaid might have created in the DOM
        const errorElements = document.querySelectorAll('.mermaid-error, .error');
        errorElements.forEach(el => {
          if (el.textContent?.includes('Syntax error') || el.textContent?.includes('mermaid version')) {
            el.remove();
          }
        });

        if (!cancelled) {
          // If rendering fails, keep showing last valid SVG if available
          if (lastValidSvgRef.current) {
            setSvg(lastValidSvgRef.current);
            setError(null);
            setLoading(false);
          } else {
            setError(e instanceof Error ? e.message : 'Failed to render diagram');
            setLoading(false);
          }
        }
      } finally {
        isRenderingRef.current = false;
        if (safetyTimeoutRef.current) {
          clearTimeout(safetyTimeoutRef.current);
        }
      }
    };

    // Debounce render to avoid rapid re-renders during streaming
    renderTimeoutRef.current = setTimeout(() => {
      renderDiagram();
    }, 100);

    return () => {
      cancelled = true;
      if (renderTimeoutRef.current) {
        clearTimeout(renderTimeoutRef.current);
      }
      if (safetyTimeoutRef.current) {
        clearTimeout(safetyTimeoutRef.current);
      }
    };
  }, [cleanCode, theme, isClient]);

  // ==================== Zoom Controls ====================

  const zoomIn = useCallback(() => {
    userHasInteractedRef.current = true;
    setTransform((prev) => ({
      ...prev,
      scale: Math.min(maxScale, prev.scale * 1.25),
    }));
  }, []);

  const zoomOut = useCallback(() => {
    userHasInteractedRef.current = true;
    setTransform((prev) => ({
      ...prev,
      scale: Math.max(minScale, prev.scale / 1.25),
    }));
  }, []);

  // Reset view - center the SVG at 100% scale
  const resetView = useCallback(() => {
    userHasInteractedRef.current = false;
    const container = containerRef.current;
    const svgElement = svgRef.current;
    if (!container || !svgElement) {
      setTransform({ x: 0, y: 0, scale: 1 });
      return;
    }

    // Center the SVG at 100% scale
    try {
      const bbox = svgElement.getBBox();
      const containerRect = container.getBoundingClientRect();
      if (bbox.width > 0 && bbox.height > 0 && containerRect.width > 0) {
        // Center horizontally, align to top with small padding
        const x = Math.max(0, (containerRect.width - bbox.width) / 2);
        const y = 10; // Small top padding
        setTransform({ x, y, scale: 1 });
        return;
      }
    } catch {
      // Fall through to default reset
    }
    setTransform({ x: 0, y: 0, scale: 1 });
  }, []);

  // Center and fit the diagram in fullscreen mode
  const centerInFullscreen = useCallback(() => {
    const svgElement = svgRef.current || containerRef.current?.querySelector('svg');
    if (!svgElement) return;

    const screenWidth = window.innerWidth;
    const screenHeight = window.innerHeight;

    // Get dimensions from viewBox or getBBox
    let contentWidth: number;
    let contentHeight: number;

    const viewBox = svgElement.getAttribute('viewBox');
    if (viewBox) {
      const parts = viewBox.split(/[\s,]+/).map(Number);
      contentWidth = parts[2] || 800;
      contentHeight = parts[3] || 600;
    } else {
      try {
        const bbox = svgElement.getBBox();
        contentWidth = bbox.width || 800;
        contentHeight = bbox.height || 600;
      } catch {
        contentWidth = 800;
        contentHeight = 600;
      }
    }

    // Center the content at 100% scale in fullscreen
    const x = (screenWidth - contentWidth) / 2;
    const y = (screenHeight - contentHeight) / 2;

    setTransform({ x, y, scale: 1 });
  }, []);

  // Fit to view - recalculate optimal fit for current mode
  const fitToView = useCallback(() => {
    if (isFullscreen) {
      centerInFullscreen();
    } else {
      resetView();
    }
  }, [isFullscreen, centerInFullscreen, resetView]);

  // ==================== Fullscreen ====================

  const toggleFullscreen = useCallback(() => {
    setIsFullscreen(!isFullscreen);

    if (!isFullscreen) {
      // Entering CSS fullscreen - center the diagram after DOM updates
      // Use longer delay to ensure layout is complete
      setTimeout(centerInFullscreen, 200);
      // Also try again after a bit longer in case first attempt was too early
      setTimeout(centerInFullscreen, 500);
    } else {
      // Exiting CSS fullscreen - reset to default view
      setTimeout(() => {
        setTransform({ x: 0, y: 0, scale: 1 });
      }, 100);
    }
  }, [isFullscreen, centerInFullscreen]);

  // ==================== Hover Activation Handlers ====================

  const handleMouseEnterContainer = useCallback(() => {
    if (!enableZoomPan) return;

    // Start timer to activate scroll zoom after delay
    hoverTimerRef.current = setTimeout(() => {
      setIsScrollActive(true);
    }, HOVER_ACTIVATION_DELAY);
  }, [enableZoomPan, HOVER_ACTIVATION_DELAY]);

  const handleMouseLeaveContainer = useCallback(() => {
    // Clear timer and deactivate scroll zoom
    if (hoverTimerRef.current) {
      clearTimeout(hoverTimerRef.current);
      hoverTimerRef.current = null;
    }
    setIsScrollActive(false);
  }, []);

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (hoverTimerRef.current) {
        clearTimeout(hoverTimerRef.current);
      }
    };
  }, []);

  // ==================== Pan & Zoom Handlers ====================

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (!enableZoomPan || e.button !== 0) return;
    const target = e.target as HTMLElement;
    if (target.closest('.node, g[class*="node"]')) return;

    e.preventDefault();
    isPanningRef.current = true;
    lastPanPointRef.current = { x: e.clientX, y: e.clientY };

    // Immediately activate scroll when user starts panning
    setIsScrollActive(true);
    if (hoverTimerRef.current) {
      clearTimeout(hoverTimerRef.current);
      hoverTimerRef.current = null;
    }

    // Disable transition on link text elements during dragging to prevent flicker
    if (containerRef.current) {
      containerRef.current.style.cursor = 'grabbing';
      containerRef.current.style.setProperty('--link-transition', '0s');
    }
  }, [enableZoomPan]);

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (!enableZoomPan || !isPanningRef.current) return;

    const dx = e.clientX - lastPanPointRef.current.x;
    const dy = e.clientY - lastPanPointRef.current.y;

    userHasInteractedRef.current = true;
    setTransform((prev) => ({
      ...prev,
      x: prev.x + dx,
      y: prev.y + dy,
    }));

    lastPanPointRef.current = { x: e.clientX, y: e.clientY };
  }, [enableZoomPan]);

  const handleMouseUp = useCallback(() => {
    isPanningRef.current = false;
    if (containerRef.current) {
      containerRef.current.style.cursor = 'grab';
      // Restore transition on link text elements after dragging
      containerRef.current.style.removeProperty('--link-transition');
    }
  }, []);

  // Wheel zoom - using native event listener for better control
  // Only intercept scroll when isScrollActive is true (after hover delay)
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !svg || !enableZoomPan) return;

    const handleWheel = (e: WheelEvent) => {
      // Only intercept scroll when scroll zoom is active (after hover delay)
      // This prevents page scroll freezing when quickly scrolling through documents
      if (!isScrollActive && !isFullscreen) {
        // Allow normal page scroll to continue
        return;
      }

      // Prevent default only when we're actively handling zoom
      e.preventDefault();
      e.stopPropagation();

      const delta = -e.deltaY / 500;
      const newScale = Math.max(minScale, Math.min(maxScale, transform.scale * (1 + delta)));

      if (Math.abs(newScale - transform.scale) < 0.001) return;

      // Zoom towards cursor
      const rect = container.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;

      const scaleFactor = newScale / transform.scale;
      const newX = x - (x - transform.x) * scaleFactor;
      const newY = y - (y - transform.y) * scaleFactor;

      userHasInteractedRef.current = true;
      setTransform({ x: newX, y: newY, scale: newScale });
    };

    container.addEventListener('wheel', handleWheel, { passive: false });

    return () => {
      container.removeEventListener('wheel', handleWheel);
    };
  }, [svg, enableZoomPan, transform, isScrollActive, isFullscreen]);

  // ==================== Keyboard Handlers ====================

  // ESC key to exit fullscreen
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isFullscreen) {
        toggleFullscreen();
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [isFullscreen, toggleFullscreen]);

  // ==================== SVG Setup & Auto-fit ====================

  useEffect(() => {
    if (!svg) return;

    const timeout = setTimeout(() => {
      const svgElement = containerRef.current?.querySelector('svg');
      const container = containerRef.current;
      if (!svgElement || !container) return;

      svgRef.current = svgElement as SVGSVGElement;

      // Get natural dimensions from SVG
      const bbox = svgElement.getBBox?.();
      if (bbox && bbox.width > 0 && bbox.height > 0) {
        // Use the original viewBox if present, otherwise calculate from bbox
        const existingViewBox = svgElement.getAttribute('viewBox');
        let viewBoxWidth: number, viewBoxHeight: number;

        if (existingViewBox) {
          const parts = existingViewBox.split(/[\s,]+/).map(Number);
          viewBoxWidth = parts[2] || bbox.width + bbox.x;
          viewBoxHeight = parts[3] || bbox.height + bbox.y;
        } else {
          // Set viewBox to include all content from origin
          viewBoxWidth = bbox.width + bbox.x;
          viewBoxHeight = bbox.height + bbox.y;
          svgElement.setAttribute('viewBox', `0 0 ${viewBoxWidth} ${viewBoxHeight}`);
        }

        // Store dimensions for container sizing
        setSvgDimensions({ width: viewBoxWidth, height: viewBoxHeight });

        // Set SVG to its natural size, capped at container width.
        // Don't stretch beyond natural width to avoid blurry upscaling.
        svgElement.removeAttribute('width');
        svgElement.removeAttribute('height');
        svgElement.style.width = `${viewBoxWidth}px`;
        svgElement.style.maxWidth = '100%';
        svgElement.style.height = 'auto';
        svgElement.style.display = 'block';
      }
    }, 50);

    return () => clearTimeout(timeout);
  }, [svg]);

  // ==================== Text Click Handling ====================
  // Make only specific text elements (link basenames) clickable
  // Store all props in refs to avoid triggering useEffect when they change
  const linkMapRef = useRef<Map<string, string> | undefined>(linkMap);
  const onNodeClickRef = useRef(_onNodeClick);
  const nodeMetadataRef = useRef(_nodeMetadata);
  const enableNodeInteractionRef = useRef(_enableNodeInteraction);

  linkMapRef.current = linkMap;
  onNodeClickRef.current = _onNodeClick;
  nodeMetadataRef.current = _nodeMetadata;
  enableNodeInteractionRef.current = _enableNodeInteraction;

  // Function to apply styles to SVG elements
  const applyStylesToSvg = useCallback((svgElement: SVGSVGElement) => {
    const clickableBasenames = linkMapRef.current ? Array.from(linkMapRef.current.keys()) : [];
    const clickableBasenameSet = new Set(clickableBasenames);

    const resolveLinksForTexts = (texts: string[]): string[] => {
      const fullLinks = new Set<string>();
      texts.forEach((text) => {
        extractMermaidLabelFragments(text).forEach((fragment) => {
          if (!clickableBasenameSet.has(fragment)) return;
          const fullLink = linkMapRef.current?.get(fragment);
          if (fullLink) fullLinks.add(fullLink);
        });
      });
      return Array.from(fullLinks);
    };

    // ==================== Apply palette to replace LLM styles ====================
    // If LLM had inline styles (which we stripped), replace with our unified palette.
    // - groupCount === 1 → LLM used one color for all → apply palette[0] uniformly
    // - groupCount > 1  → LLM used N colors → map each group to a different palette color
    // - groupCount === 0 → LLM had no styles → keep default theme (do nothing)
    const c = getColors(theme);
    const { nodeGroups: styleNodeGroups, groupCount } = styleGroupsRef.current;
    if (c.nodePalette && groupCount > 0) {
      const svgNodes = Array.from(svgElement.querySelectorAll('.node'));
      svgNodes.forEach((node) => {
        // Extract mermaid node ID from the DOM element
        // Mermaid sets id like "flowchart-NodeId-123" or class includes the node id
        const nodeId = node.id?.replace(/^flowchart-/, '').replace(/-\d+$/, '') || '';
        const groupIdx = styleNodeGroups.get(nodeId);

        let palette: { fill: string; border: string };
        if (groupIdx !== undefined) {
          // Node was explicitly styled by LLM → use its group's palette color
          palette = c.nodePalette[groupIdx % c.nodePalette.length];
        } else if (groupCount === 1) {
          // LLM used one color → all nodes get palette[0]
          palette = c.nodePalette[0];
        } else {
          // Node wasn't in any style group → keep default (don't override)
          return;
        }

        const shapes = node.querySelectorAll('rect, circle, polygon, ellipse, path.border');
        shapes.forEach((shape) => {
          (shape as SVGElement).style.fill = palette.fill;
          (shape as SVGElement).style.stroke = palette.border;
        });
      });
    }

    const bindClickableElement = (el: Element, texts: string[]) => {
      const fullLinks = resolveLinksForTexts(texts);
      const isClickable = fullLinks.length > 0;

      if ((el as any)._clickHandler) {
        el.removeEventListener('click', (el as any)._clickHandler);
        delete (el as any)._clickHandler;
      }

      if (isClickable) {
        (el as HTMLElement).classList.add('mermaid-link-text');
        el.closest('.node')?.classList.add('clickable');
        if (el.tagName.toLowerCase() === 'text') {
          (el as SVGTextElement).style.fill = getColors(theme).accent.blue;
          (el as SVGTextElement).style.cursor = 'pointer';
        }
        if (el instanceof HTMLElement) {
          el.style.cursor = 'pointer';
        }

        const clickHandler = (e: Event) => {
          e.stopPropagation();
          e.preventDefault();
          const target = e.currentTarget as any;
          if (target._isProcessing) return;
          target._isProcessing = true;
          setTimeout(() => {
            if (target) target._isProcessing = false;
          }, 100);

          if (fullLinks.length === 1) {
            const fullLink = fullLinks[0];
            onNodeClickRef.current?.(fullLink, nodeMetadataRef.current?.[fullLink]);
            return;
          }

          const mouseEvent = e as MouseEvent;
          const containerRect = containerRef.current?.getBoundingClientRect();
          setMultiLinkMenu({
            x: containerRect ? mouseEvent.clientX - containerRect.left : 0,
            y: containerRect ? mouseEvent.clientY - containerRect.top : 0,
            links: fullLinks,
            displayText: texts[0] || '',
          });
        };
        el.addEventListener('click', clickHandler);
        (el as any)._clickHandler = clickHandler;
      } else {
        (el as HTMLElement).classList.remove('mermaid-link-text');
        if (el.tagName.toLowerCase() === 'text') {
          (el as SVGTextElement).style.cursor = '';
        }
      }
    };

    // ==================== Handle any foreignObject labels that still appear ====================
    const foreignObjects = svgElement.querySelectorAll('foreignObject');
    foreignObjects.forEach((fo) => {
      const pElement = fo.querySelector('p');
      if (pElement) {
        bindClickableElement(pElement, getTextVariantsFromElement(pElement));
      }
    });

    // ==================== Pure SVG text labels ====================
    const textElements = svgElement.querySelectorAll('text');
    textElements.forEach((textEl) => {
      bindClickableElement(textEl, getTextVariantsFromElement(textEl));
    });
  }, [theme]);

  // Use MutationObserver to detect when SVG is replaced by React
  useEffect(() => {
    if (!svg || !containerRef.current || !onNodeClickRef.current || !enableNodeInteractionRef.current) {
      return;
    }

    const container = containerRef.current;

    // Apply styles immediately to current SVG
    const svgElement = container.querySelector('svg');
    if (svgElement) {
      applyStylesToSvg(svgElement as SVGSVGElement);
    }

    // Watch for DOM changes (when React replaces the SVG)
    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        if (mutation.type === 'childList') {
          const newSvg = container.querySelector('svg');
          if (newSvg && newSvg !== svgElement) {
            applyStylesToSvg(newSvg as SVGSVGElement);
          }
        }
      }
    });

    observer.observe(container, {
      childList: true,
      subtree: true
    });

    return () => {
      observer.disconnect();
    };
  }, [svg, applyStylesToSvg]);

  // Re-center when svgDimensions changes (container size updates)
  // Skip recentering if user has interacted with zoom/pan and this is just a theme change (same code)
  useEffect(() => {
    if (!svgDimensions || !containerRef.current) return;

    // If user has zoomed/panned and the diagram code hasn't changed (e.g. theme-only re-render),
    // preserve their current transform position
    const currentCode = sanitizeMermaidCode(cleanCode).code;
    if (userHasInteractedRef.current && lastRenderedCodeRef.current === currentCode) {
      return;
    }

    // New diagram code — reset interaction flag and recenter
    userHasInteractedRef.current = false;

    // Use double RAF to ensure layout is complete
    const recenter = () => {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          const container = containerRef.current;
          if (!container) return;

          const containerRect = container.getBoundingClientRect();
          if (containerRect.width > 0 && containerRect.height > 0) {
            // Calculate actual display dimensions (SVG is capped at natural width)
            const displayWidth = Math.min(svgDimensions.width, containerRect.width);
            const scaleFactor = displayWidth / svgDimensions.width;
            const displayHeight = svgDimensions.height * scaleFactor;

            // Center horizontally if SVG is narrower than container
            const x = displayWidth < containerRect.width
              ? (containerRect.width - displayWidth) / 2
              : 0;
            // Center vertically if SVG is shorter than container
            const y = displayHeight < containerRect.height
              ? (containerRect.height - displayHeight) / 2
              : 0;
            setTransform({ x, y, scale: 1 });
          }
        });
      });
    };

    // Small delay for React to update DOM
    const timeout = setTimeout(recenter, 100);
    return () => clearTimeout(timeout);
  }, [svgDimensions, cleanCode]);

  // ==================== Render ====================

  // Calculate container style based on SVG dimensions and constraints
  const getContainerStyle = (): React.CSSProperties => {
    const c = getColors(theme);

    if (isFullscreen) {
      return {
        position: 'fixed',
        top: 0,
        left: 0,
        width: '100vw',
        height: '100vh',
        zIndex: 9999,
        background: c.bg.primary,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      };
    }

    const maxH = typeof maxHeight === 'number' ? maxHeight : parseInt(maxHeight as string) || 600;

    // Default dimensions before SVG loads
    if (!svgDimensions || svgDimensions.width <= 0 || svgDimensions.height <= 0) {
      return {
        position: 'relative',
        width: '100%',
        height: '300px',
        minHeight: '150px',
        maxHeight: `${maxH}px`,
        margin: '0 auto',
      };
    }

    // Calculate the actual rendered height of the SVG.
    // The SVG is set to its natural width (capped at container width via max-width: 100%).
    // We need the container to measure its available width to compute the correct height.
    const containerWidth = containerRef.current?.clientWidth || 700;
    const padding = 24;

    // The SVG will display at min(naturalWidth, containerWidth).
    // If it fits, use its natural height; otherwise scale proportionally.
    const displayWidth = Math.min(svgDimensions.width, containerWidth);
    const scaleFactor = displayWidth / svgDimensions.width;
    const displayHeight = svgDimensions.height * scaleFactor;
    const containerHeight = Math.max(150, Math.min(displayHeight + padding, maxH));

    return {
      position: 'relative',
      width: '100%',
      maxWidth: '100%',
      height: `${containerHeight}px`,
      minHeight: '150px',
      maxHeight: `${maxH}px`,
      margin: '0 auto',
    };
  };

  const c = getColors(theme);

  if (!isClient) {
    return (
      <div className={className} style={{ ...getContainerStyle(), display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <span style={{ color: c.text.secondary }}>Loading diagram...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div
        className={className}
        style={{
          ...getContainerStyle(),
          padding: '16px',
          background: c.bg.secondary,
          borderRadius: '8px',
          border: `1px solid ${c.border.primary}`,
          overflow: 'auto',
          height: 'auto',
        }}
      >
        <p style={{ color: theme === 'dark' ? '#f87171' : (theme === 'beige' ? '#a04030' : '#dc2626'), marginBottom: '8px', fontSize: '14px' }}>
          Failed to render diagram
        </p>
        <pre style={{
          fontSize: '12px',
          background: c.bg.primary,
          padding: '12px',
          borderRadius: '6px',
          overflow: 'auto',
          whiteSpace: 'pre-wrap',
          color: c.text.secondary,
        }}>
          {cleanCode}
        </pre>
      </div>
    );
  }

  if (loading) {
    return (
      <div
        className={className}
        style={{
          ...getContainerStyle(),
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          background: c.bg.primary,
          borderRadius: '8px',
          border: `1px solid ${c.border.primary}`,
        }}
      >
        <div style={{
          width: '24px',
          height: '24px',
          border: `2px solid ${c.border.primary}`,
          borderTopColor: c.accent.blue,
          borderRadius: '50%',
          animation: 'spin 1s linear infinite',
        }} />
        <span style={{ marginTop: '12px', fontSize: '13px', color: c.text.secondary }}>
          Rendering diagram...
        </span>
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </div>
    );
  }

  if (!svg) {
    // Render finished but produced no SVG — show the raw code as fallback
    return (
      <div
        className={className}
        style={{
          ...getContainerStyle(),
          padding: '16px',
          background: c.bg.secondary,
          borderRadius: '8px',
          border: `1px solid ${c.border.primary}`,
          overflow: 'auto',
          height: 'auto',
        }}
      >
        <p style={{ color: c.text.secondary, marginBottom: '8px', fontSize: '14px' }}>
          Diagram could not be rendered
        </p>
        <pre style={{
          fontSize: '12px',
          background: c.bg.primary,
          padding: '12px',
          borderRadius: '6px',
          overflow: 'auto',
          whiteSpace: 'pre-wrap',
          color: c.text.secondary,
        }}>
          {cleanCode}
        </pre>
      </div>
    );
  }

  const containerStyle = getContainerStyle();

  return (
    <div
      ref={fullscreenContainerRef}
      className={`${className} ${isFullscreen ? 'interactive-mermaid-fullscreen' : ''}`}
      style={{
        ...containerStyle,
        background: isFullscreen ? containerStyle.background : c.bg.primary,
        borderRadius: isFullscreen ? 0 : '8px',
        border: isFullscreen ? 'none' : `1px solid ${c.border.primary}`,
        overflow: 'hidden',
        transition: isFullscreen ? 'none' : 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
      }}
    >
      {/* SVG Container */}
      <div
        ref={containerRef}
        className="interactive-mermaid-container"
        style={{
          width: '100%',
          height: '100%',
          maxWidth: '100%',  // Force container to never exceed parent width
          overflow: 'hidden',
          cursor: enableZoomPan ? 'grab' : 'default',
          position: 'relative',
          // Visual indicator when scroll zoom is active
          outline: isScrollActive && !isFullscreen ? `2px solid ${getColors(theme).accent.blue}` : 'none',
          outlineOffset: '-2px',
          transition: 'outline 0.2s ease',
        }}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={() => {
          handleMouseUp();
          handleMouseLeaveContainer();
        }}
        onMouseEnter={handleMouseEnterContainer}
        onDoubleClick={toggleFullscreen}
      >
        <div
          style={{
            width: '100%',
            height: '100%',
            transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.scale})`,
            transformOrigin: '0 0',
            transition: isPanningRef.current ? 'none' : 'transform 0.1s ease-out',
          }}
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      </div>

      {/* Controls */}
      {enableZoomPan && (
        <div
          style={{
            position: 'absolute',
            bottom: '16px',
            right: '16px',
            display: 'flex',
            gap: '6px',
            background: getColors(theme).bg.elevated,
            border: `1px solid ${getColors(theme).border.secondary}`,
            borderRadius: '12px',
            padding: '8px',
            backdropFilter: 'blur(12px)',
            boxShadow: '0 8px 32px rgba(0,0,0,0.12)',
            transition: 'all 0.2s ease',
            zIndex: 10,
          }}
        >
          <ControlButton onClick={zoomOut} title="Zoom out" theme={theme}>−</ControlButton>
          <span style={{
            fontSize: '12px',
            fontWeight: 500,
            color: getColors(theme).text.secondary,
            display: 'flex',
            alignItems: 'center',
            padding: '0 8px',
            minWidth: '44px',
            justifyContent: 'center',
            fontFamily: FONTS.mono,
          }}>
            {Math.round(transform.scale * 100)}%
          </span>
          <ControlButton onClick={zoomIn} title="Zoom in" theme={theme}>+</ControlButton>
          <div style={{ width: '1px', background: c.border.primary, margin: '4px 2px' }} />
          <ControlButton onClick={fitToView} title="Fit to view" theme={theme}>⊡</ControlButton>
          <ControlButton onClick={resetView} title="Reset" theme={theme}>↺</ControlButton>
          <ControlButton onClick={toggleFullscreen} title={isFullscreen ? "Exit fullscreen (ESC)" : "Fullscreen"} theme={theme}>
            {isFullscreen ? '✕' : '⤢'}
          </ControlButton>
        </div>
      )}

      {/* Hint for non-fullscreen */}
      {!isFullscreen && enableZoomPan && (
        <div style={{
          position: 'absolute',
          top: '12px',
          left: '12px',
          fontSize: '12px',
          color: isScrollActive
            ? getColors(theme).accent.blue
            : getColors(theme).text.muted,
          pointerEvents: 'none',
          transition: 'all 0.3s ease',
          background: isScrollActive ? getColors(theme).bg.elevated : 'transparent',
          padding: isScrollActive ? '6px 12px' : '0',
          borderRadius: '20px',
          border: isScrollActive ? `1px solid ${getColors(theme).border.secondary}` : 'none',
          boxShadow: isScrollActive ? '0 4px 12px rgba(0,0,0,0.1)' : 'none',
          backdropFilter: isScrollActive ? 'blur(8px)' : 'none',
          fontWeight: isScrollActive ? 500 : 400,
          zIndex: 10,
        }}>
          {/* <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            {isScrollActive ? '🎯' : '👁️'}
            {isScrollActive
              ? 'Zoom active • Scroll to zoom • Drag to pan'
              : 'Hover to zoom • Drag to pan • Double-click fullscreen'}
          </span> */}
        </div>
      )}

      {/* Global Mermaid Styling */}
      <style jsx global>{`
        /* Custom scrollbar for zoom/pan container */
        .interactive-mermaid-container::-webkit-scrollbar {
          width: 8px;
          height: 8px;
        }
        .interactive-mermaid-container::-webkit-scrollbar-track {
          background: transparent;
        }
        .interactive-mermaid-container::-webkit-scrollbar-thumb {
          background-color: ${getColors(theme).border.muted};
          border-radius: 4px;
        }

        /* Mermaid SVG optimizations */
        .interactive-mermaid-container svg {
           max-width: 100% !important;
           height: auto !important;
        }

        .interactive-mermaid-container svg text,
        .interactive-mermaid-container .label text,
        .interactive-mermaid-container .cluster text,
        .interactive-mermaid-container .cluster-label text,
        .interactive-mermaid-container .edgeLabel text,
        .interactive-mermaid-container .messageText,
        .interactive-mermaid-container .loopText,
        .interactive-mermaid-container .noteText,
        .interactive-mermaid-container .sectionTitle,
        .interactive-mermaid-container .actor text,
        .interactive-mermaid-container .label foreignObject,
        .interactive-mermaid-container .label foreignObject *,
        .interactive-mermaid-container foreignObject div,
        .interactive-mermaid-container foreignObject span,
        .interactive-mermaid-container foreignObject p {
           fill: ${c.text.primary} !important;
           color: ${c.text.primary} !important;
        }

        /* Edge/arrow styling - make more visible */
        .interactive-mermaid-container .edgePath .path,
        .interactive-mermaid-container path.path {
           stroke: ${c.lineStroke} !important;
           stroke-width: 2px !important;
           stroke-linecap: round !important;
        }

        /* Arrow markers */
        .interactive-mermaid-container marker path {
           fill: ${c.lineStroke} !important;
        }

        /* Edge labels - make background transparent to avoid white blocks */
        .interactive-mermaid-container .edgeLabel {
           background: transparent !important;
           border: none !important;
           padding: 0 !important;
           box-shadow: none !important;
        }

        .interactive-mermaid-container .edgeLabel rect,
        .interactive-mermaid-container .labelBkg {
           fill: transparent !important;
           stroke: none !important;
        }

        /* Remove white backgrounds from all label containers */
        .interactive-mermaid-container .label-container,
        .interactive-mermaid-container .edgeLabel foreignObject,
        .interactive-mermaid-container .edgeLabel foreignObject div {
           background: transparent !important;
           background-color: transparent !important;
        }

        /* Default node style - not clickable */
        .interactive-mermaid-container .node {
           cursor: default !important;
        }

        .interactive-mermaid-container .node.clickable:active rect,
        .interactive-mermaid-container .node.clickable:active circle,
        .interactive-mermaid-container .node.clickable:active polygon,
        .interactive-mermaid-container .node.clickable:active ellipse {
           filter: drop-shadow(0 1px 3px rgba(0,0,0,0.2)) !important;
           stroke: ${getColors(theme).accent.blue} !important;
           stroke-width: 2.5px !important;
        }

        /* ==================== ENHANCED: Modern Node Styling ==================== */
        /* Node fill/stroke set by JS palette in applyStylesToSvg — do not override here */
        .interactive-mermaid-container .node rect {
           stroke-width: 2px !important;
           rx: 16px !important;
           ry: 16px !important;
           filter: drop-shadow(0 2px 4px rgba(0,0,0,0.08)) !important;
           transition: all 0.2s ease !important;
        }

        .interactive-mermaid-container .node:hover rect {
           filter: drop-shadow(0 4px 12px rgba(0,0,0,0.15)) !important;
           stroke: ${getColors(theme).accent.blue} !important;
           transform: translateY(-1px);
        }

        /* Enhanced cluster/subgraph styling */
        .interactive-mermaid-container .cluster rect {
           fill: ${getColors(theme).bg.elevated} !important;
           stroke: ${getColors(theme).border.secondary} !important;
           stroke-width: 2px !important;
           stroke-dasharray: 5,5 !important;
           rx: 12px !important;
           ry: 12px !important;
           filter: drop-shadow(0 1px 3px rgba(0,0,0,0.05)) !important;
        }

        /* ==================== ENHANCED: Text Styling ==================== */
        /* Improved text rendering with better font settings */
        .interactive-mermaid-container .node text {
           font-family: ${FONTS.ui} !important;
           font-weight: 500 !important;
           font-size: 18px !important;
           fill: ${getColors(theme).text.primary} !important;
           text-shadow: 0 1px 2px rgba(0,0,0,0.05) !important;
        }

        .interactive-mermaid-container .cluster text,
        .interactive-mermaid-container .cluster-label text,
        .interactive-mermaid-container .edgeLabel text,
        .interactive-mermaid-container .messageText,
        .interactive-mermaid-container .loopText,
        .interactive-mermaid-container .noteText,
        .interactive-mermaid-container .sectionTitle,
        .interactive-mermaid-container .actor text {
           font-family: ${FONTS.ui} !important;
           font-size: 16px !important;
           fill: ${getColors(theme).text.primary} !important;
        }

        /* ==================== Sequence Diagram Specific ==================== */
        /* Actor boxes (participant headers) */
        .interactive-mermaid-container .actor-man,
        .interactive-mermaid-container rect.actor {
           fill: ${c.bg.elevated} !important;
           stroke: ${c.border.primary} !important;
        }

        /* Actor lifeline */
        .interactive-mermaid-container line.actor-line {
           stroke: ${c.border.muted} !important;
        }

        /* Message text on arrows */
        .interactive-mermaid-container text.messageText {
           fill: ${c.text.primary} !important;
           font-size: 16px !important;
        }

        /* Sequence number badges */
        .interactive-mermaid-container .sequenceNumber {
           fill: ${c.text.primary} !important;
        }

        /* Message lines (arrows between participants) */
        .interactive-mermaid-container .messageLine0,
        .interactive-mermaid-container .messageLine1 {
           stroke: ${c.lineStroke} !important;
        }

        /* Activation bars */
        .interactive-mermaid-container .activation0,
        .interactive-mermaid-container .activation1,
        .interactive-mermaid-container .activation2 {
           fill: ${c.bg.tertiary} !important;
           stroke: ${c.border.primary} !important;
        }

        /* Note boxes */
        .interactive-mermaid-container .note {
           fill: ${c.bg.elevated} !important;
           stroke: ${c.border.primary} !important;
        }

        /* Loop/alt/opt boxes - label and body */
        .interactive-mermaid-container .loopLine {
           stroke: ${c.border.primary} !important;
        }

        .interactive-mermaid-container .labelBox {
           fill: ${c.bg.tertiary} !important;
           stroke: ${c.border.primary} !important;
        }

        .interactive-mermaid-container .labelText,
        .interactive-mermaid-container .loopText tspan {
           fill: ${c.text.primary} !important;
        }

        /* rect highlight regions (e.g. rect rgb(...)) — text inside must be readable */
        .interactive-mermaid-container .rect text {
           fill: ${c.text.primary} !important;
        }

        /* Simple clickable link text styling for SVG <text> elements - MUST come after .node text */
        /* Higher specificity to override .node text styles */
        .interactive-mermaid-container .node text.mermaid-link-text,
        .interactive-mermaid-container text.mermaid-link-text {
           cursor: pointer !important;
           fill: ${getColors(theme).accent.blue} !important;
           font-family: ${FONTS.ui} !important;
           font-weight: 500 !important;
           font-size: 18px !important;
        }

        .interactive-mermaid-container .node text.mermaid-link-text:hover,
        .interactive-mermaid-container text.mermaid-link-text:hover {
           fill: ${getColors(theme).accent.blue} !important;
           opacity: 0.8 !important;
        }

        /* ==================== Simple foreignObject Link Styling ==================== */
        /* Simple clickable link styling for foreignObject HTML elements - color only */
        .interactive-mermaid-container foreignObject .mermaid-link-text {
           cursor: pointer !important;
           color: ${getColors(theme).accent.blue} !important;
           text-decoration: none !important;
           font-family: ${FONTS.ui} !important;
           font-weight: 500 !important;
           font-size: 18px !important;
        }

        .interactive-mermaid-container foreignObject .mermaid-link-text:hover {
           color: ${getColors(theme).accent.blue} !important;
           opacity: 0.8 !important;
        }

        /* Description text styling (non-clickable) */
        .interactive-mermaid-container foreignObject p {
           color: ${getColors(theme).text.primary} !important;
           font-size: 16px !important;
           line-height: 1.6 !important;
           margin: 0 !important;
           padding: 0 !important;
        }

        /* Preserve foreignObject content - critical for HTML labels */
        .interactive-mermaid-container foreignObject {
           overflow: visible !important;
        }

        .interactive-mermaid-container foreignObject * {
           box-sizing: border-box !important;
        }

        /* Ensure label content is properly displayed */
        .interactive-mermaid-container .label {
           display: block !important;
           visibility: visible !important;
        }

        /* Subtle background pattern - applied via pseudo-element to avoid affecting SVG content */
        .interactive-mermaid-container::before {
           content: '';
           position: absolute;
           top: 0;
           left: 0;
           right: 0;
           bottom: 0;
           background-image:
             radial-gradient(circle at 1px 1px, ${getColors(theme).border.muted} 0.5px, transparent 0);
           background-size: 16px 16px;
           background-position: 0 0;
           opacity: 0.3;
           pointer-events: none;
           z-index: 0;
        }

        /* Ensure SVG content is above the background pattern */
        .interactive-mermaid-container > div {
           position: relative;
           z-index: 1;
        }
      `}</style>

      {/* Multi-link menu - shows when clicking a node with multiple links */}
      {multiLinkMenu && (
        <div
          style={{
            position: 'absolute',
            left: `${multiLinkMenu.x}px`,
            top: `${multiLinkMenu.y}px`,
            transform: 'translateX(-50%)',
            background: getColors(theme).bg.elevated,
            border: `1px solid ${getColors(theme).border.primary}`,
            borderRadius: '12px',
            padding: '8px',
            boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
            zIndex: 100,
            minWidth: '200px',
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <div style={{
            fontSize: '12px',
            fontWeight: 600,
            color: getColors(theme).text.secondary,
            marginBottom: '8px',
            paddingBottom: '8px',
            borderBottom: `1px solid ${getColors(theme).border.secondary}`,
          }}>
            Select a link:
          </div>
          {multiLinkMenu.links.map((link, index) => (
            <button
              key={index}
              onClick={() => {
                _onNodeClick?.(link, _nodeMetadata?.[link]);
                setMultiLinkMenu(null);
              }}
              style={{
                display: 'block',
                width: '100%',
                padding: '8px 12px',
                background: 'transparent',
                border: 'none',
                borderRadius: '6px',
                color: getColors(theme).text.primary,
                fontSize: '13px',
                fontFamily: FONTS.mono,
                textAlign: 'left',
                cursor: 'pointer',
                transition: 'background 0.15s ease',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = getColors(theme).bg.tertiary;
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = 'transparent';
              }}
            >
              {link}
            </button>
          ))}
        </div>
      )}

      {/* Click outside to close multi-link menu */}
      {multiLinkMenu && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 99,
          }}
          onClick={() => setMultiLinkMenu(null)}
        />
      )}
    </div>
  );
};

// Control button component
const ControlButton: React.FC<{
  onClick: () => void;
  title: string;
  theme: 'dark' | 'light' | 'beige';
  children: React.ReactNode;
}> = ({ onClick, title, theme, children }) => {
  const c = getColors(theme);
  return (
    <button
      onClick={onClick}
      title={title}
      style={{
        width: '32px',
        height: '32px',
        background: 'transparent',
        border: 'none',
        borderRadius: '8px',
        cursor: 'pointer',
        fontSize: '14px',
        color: c.text.secondary,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        transition: 'all 0.2s ease',
        fontWeight: 500,
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = c.bg.tertiary;
        e.currentTarget.style.color = c.text.primary;
        e.currentTarget.style.transform = 'scale(1.05)';
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = 'transparent';
        e.currentTarget.style.color = c.text.secondary;
        e.currentTarget.style.transform = 'scale(1)';
      }}
    >
      {children}
    </button>
  );
};

export default InteractiveMermaidDiagram;

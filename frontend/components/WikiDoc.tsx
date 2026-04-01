'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { useCallback, useMemo, useRef, lazy, Suspense } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import 'katex/dist/katex.min.css';
import { useWikiStore, type CodeBlock } from '@/lib/store';
import { customSyntaxTheme, customSyntaxThemeLight, customSyntaxThemeBeige } from '@/lib/syntax-theme';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';
import { useTranslation } from '@/lib/i18n';
import { apiFetch } from '@/lib/api-client';
import { useRepoViewer } from '@/lib/repo-viewer-context';

// Simple MD5 implementation for generating consistent anchors
// This matches Python's hashlib.md5().hexdigest()[:8]
function md5(string: string): string {
  function md5cycle(x: number[], k: number[]) {
    let a = x[0], b = x[1], c = x[2], d = x[3];

    a = ff(a, b, c, d, k[0], 7, -680876936);
    d = ff(d, a, b, c, k[1], 12, -389564586);
    c = ff(c, d, a, b, k[2], 17, 606105819);
    b = ff(b, c, d, a, k[3], 22, -1044525330);
    a = ff(a, b, c, d, k[4], 7, -176418897);
    d = ff(d, a, b, c, k[5], 12, 1200080426);
    c = ff(c, d, a, b, k[6], 17, -1473231341);
    b = ff(b, c, d, a, k[7], 22, -45705983);
    a = ff(a, b, c, d, k[8], 7, 1770035416);
    d = ff(d, a, b, c, k[9], 12, -1958414417);
    c = ff(c, d, a, b, k[10], 17, -42063);
    b = ff(b, c, d, a, k[11], 22, -1990404162);
    a = ff(a, b, c, d, k[12], 7, 1804603682);
    d = ff(d, a, b, c, k[13], 12, -40341101);
    c = ff(c, d, a, b, k[14], 17, -1502002290);
    b = ff(b, c, d, a, k[15], 22, 1236535329);

    a = gg(a, b, c, d, k[1], 5, -165796510);
    d = gg(d, a, b, c, k[6], 9, -1069501632);
    c = gg(c, d, a, b, k[11], 14, 643717713);
    b = gg(b, c, d, a, k[0], 20, -373897302);
    a = gg(a, b, c, d, k[5], 5, -701558691);
    d = gg(d, a, b, c, k[10], 9, 38016083);
    c = gg(c, d, a, b, k[15], 14, -660478335);
    b = gg(b, c, d, a, k[4], 20, -405537848);
    a = gg(a, b, c, d, k[9], 5, 568446438);
    d = gg(d, a, b, c, k[14], 9, -1019803690);
    c = gg(c, d, a, b, k[3], 14, -187363961);
    b = gg(b, c, d, a, k[8], 20, 1163531501);
    a = gg(a, b, c, d, k[13], 5, -1444681467);
    d = gg(d, a, b, c, k[2], 9, -51403784);
    c = gg(c, d, a, b, k[7], 14, 1735328473);
    b = gg(b, c, d, a, k[12], 20, -1926607734);

    a = hh(a, b, c, d, k[5], 4, -378558);
    d = hh(d, a, b, c, k[8], 11, -2022574463);
    c = hh(c, d, a, b, k[11], 16, 1839030562);
    b = hh(b, c, d, a, k[14], 23, -35309556);
    a = hh(a, b, c, d, k[1], 4, -1530992060);
    d = hh(d, a, b, c, k[4], 11, 1272893353);
    c = hh(c, d, a, b, k[7], 16, -155497632);
    b = hh(b, c, d, a, k[10], 23, -1094730640);
    a = hh(a, b, c, d, k[13], 4, 681279174);
    d = hh(d, a, b, c, k[0], 11, -358537222);
    c = hh(c, d, a, b, k[3], 16, -722521979);
    b = hh(b, c, d, a, k[6], 23, 76029189);
    a = hh(a, b, c, d, k[9], 4, -640364487);
    d = hh(d, a, b, c, k[12], 11, -421815835);
    c = hh(c, d, a, b, k[15], 16, 530742520);
    b = hh(b, c, d, a, k[2], 23, -995338651);

    a = ii(a, b, c, d, k[0], 6, -198630844);
    d = ii(d, a, b, c, k[7], 10, 1126891415);
    c = ii(c, d, a, b, k[14], 15, -1416354905);
    b = ii(b, c, d, a, k[5], 21, -57434055);
    a = ii(a, b, c, d, k[12], 6, 1700485571);
    d = ii(d, a, b, c, k[3], 10, -1894986606);
    c = ii(c, d, a, b, k[10], 15, -1051523);
    b = ii(b, c, d, a, k[1], 21, -2054922799);
    a = ii(a, b, c, d, k[8], 6, 1873313359);
    d = ii(d, a, b, c, k[15], 10, -30611744);
    c = ii(c, d, a, b, k[6], 15, -1560198380);
    b = ii(b, c, d, a, k[13], 21, 1309151649);
    a = ii(a, b, c, d, k[4], 6, -145523070);
    d = ii(d, a, b, c, k[11], 10, -1120210379);
    c = ii(c, d, a, b, k[2], 15, 718787259);
    b = ii(b, c, d, a, k[9], 21, -343485551);

    x[0] = add32(a, x[0]);
    x[1] = add32(b, x[1]);
    x[2] = add32(c, x[2]);
    x[3] = add32(d, x[3]);
  }

  function cmn(q: number, a: number, b: number, x: number, s: number, t: number) {
    a = add32(add32(a, q), add32(x, t));
    return add32((a << s) | (a >>> (32 - s)), b);
  }

  function ff(a: number, b: number, c: number, d: number, x: number, s: number, t: number) {
    return cmn((b & c) | ((~b) & d), a, b, x, s, t);
  }

  function gg(a: number, b: number, c: number, d: number, x: number, s: number, t: number) {
    return cmn((b & d) | (c & (~d)), a, b, x, s, t);
  }

  function hh(a: number, b: number, c: number, d: number, x: number, s: number, t: number) {
    return cmn(b ^ c ^ d, a, b, x, s, t);
  }

  function ii(a: number, b: number, c: number, d: number, x: number, s: number, t: number) {
    return cmn(c ^ (b | (~d)), a, b, x, s, t);
  }

  function md51(s: string) {
    const n = s.length;
    const state = [1732584193, -271733879, -1732584194, 271733878];
    let i: number;
    for (i = 64; i <= s.length; i += 64) {
      md5cycle(state, md5blk(s.substring(i - 64, i)));
    }
    s = s.substring(i - 64);
    const tail = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
    for (i = 0; i < s.length; i++)
      tail[i >> 2] |= s.charCodeAt(i) << ((i % 4) << 3);
    tail[i >> 2] |= 0x80 << ((i % 4) << 3);
    if (i > 55) {
      md5cycle(state, tail);
      for (i = 0; i < 16; i++) tail[i] = 0;
    }
    tail[14] = n * 8;
    md5cycle(state, tail);
    return state;
  }

  function md5blk(s: string) {
    const md5blks = [];
    for (let i = 0; i < 64; i += 4) {
      md5blks[i >> 2] = s.charCodeAt(i)
        + (s.charCodeAt(i + 1) << 8)
        + (s.charCodeAt(i + 2) << 16)
        + (s.charCodeAt(i + 3) << 24);
    }
    return md5blks;
  }

  const hex_chr = '0123456789abcdef'.split('');

  function rhex(n: number) {
    let s = '';
    for (let j = 0; j < 4; j++)
      s += hex_chr[(n >> (j * 8 + 4)) & 0x0F] + hex_chr[(n >> (j * 8)) & 0x0F];
    return s;
  }

  function hex(x: number[]) {
    const result: string[] = [];
    for (let i = 0; i < x.length; i++)
      result[i] = rhex(x[i]);
    return result.join('');
  }

  function add32(a: number, b: number) {
    return (a + b) & 0xFFFFFFFF;
  }

  // Convert string to UTF-8 bytes for proper MD5 hashing
  const utf8String = unescape(encodeURIComponent(string));
  return hex(md51(utf8String));
}

// Generate anchor ID matching backend's logic exactly
// This must match extract_headings_from_content in recursive_doc_orchestrator.py
// Extract plain text from React children (handles nested elements like <code>)
function extractTextFromChildren(children: React.ReactNode): string {
  if (typeof children === 'string') {
    return children;
  }
  if (typeof children === 'number') {
    return String(children);
  }
  if (Array.isArray(children)) {
    return children.map(extractTextFromChildren).join('');
  }
  if (children && typeof children === 'object' && 'props' in children) {
    const element = children as React.ReactElement<{ children?: React.ReactNode }>;
    return extractTextFromChildren(element.props.children);
  }
  return '';
}

function generateAnchorId(title: string): string {
  // Keep Chinese characters, letters, numbers, spaces and hyphens
  // Modern browsers support Unicode in element IDs
  let anchor = title
    .toLowerCase()
    .replace(/[^\u4e00-\u9fa5\w\s-]/g, '')  // Keep Chinese, word chars, spaces, hyphens
    .replace(/\s+/g, '-')                    // Replace spaces with hyphens
    .replace(/-+/g, '-')                     // Collapse multiple hyphens
    .replace(/^-+|-+$/g, '');                // Trim leading/trailing hyphens

  // If anchor is empty, use MD5 hash
  if (!anchor) {
    const hashVal = md5(title).slice(0, 8);
    return `section-${hashVal}`;
  }

  return anchor;
}


// Lazy load InteractiveMermaidDiagram to avoid import issues
const InteractiveMermaidDiagram = lazy(() =>
  import('./InteractiveMermaidDiagram')
    .then(mod => ({ default: mod.InteractiveMermaidDiagram }))
    .catch(err => {
      console.warn('Failed to load InteractiveMermaidDiagram:', err);
      return { default: () => <div style={{ color: '#f87171', padding: '16px' }}>Unable to load diagram component</div> };
    })
);

// Helper function to extract [[link]] from mermaid code and clean the code
// Returns: clean code, and a map of basename -> full link for click handling.
// Keep labels plain-text only; HTML inside Mermaid labels is fragile and often
// breaks rendering, especially when the model emits complex mixed content.
function processMermaidCode(code: string): {
  cleanCode: string;
  linkMap: Map<string, string>;  // basename -> full qualified link
} {
  const linkMap = new Map<string, string>();
  let cleanCode = code;

  // Helper to extract basename from a qualified name
  const getBasename = (qualifiedName: string): string => {
    const parts = qualifiedName.split('.');
    return parts[parts.length - 1];
  };

  // Helper to clean a captured link
  const cleanLink = (link: string): string => {
    let cleaned = link;
    // Remove trailing ) characters
    while (cleaned.endsWith(')')) {
      cleaned = cleaned.slice(0, -1);
    }
    // Remove trailing ] characters
    while (cleaned.endsWith(']')) {
      cleaned = cleaned.slice(0, -1);
    }
    cleaned = cleaned.trim();
    return cleaned;
  };

  const normalizeLabelText = (text: string): string => {
    return text
      .replace(/<\/?[a-z][^>]*>/gi, ' ')
      .replace(/"/g, "'")
      .split(/\r?\n/)
      .map(line => line.replace(/\s+/g, ' ').trim())
      .filter(Boolean)
      .join('\\n');
  };

  // Process nodes with [[...]] links - both square and parentheses labels
  const processNode = (match: string, nodeVar: string, labelContent: string): string => {
    // Find all [[...]] patterns
    const linkRegex = /\[\[([^\]]*)\]\]/g;
    const links: { fullLink: string; basename: string }[] = [];
    let linkMatch;

    while ((linkMatch = linkRegex.exec(labelContent)) !== null) {
      const rawLink = linkMatch[1];
      const cleanedLink = cleanLink(rawLink);
      const basename = getBasename(cleanedLink);
      links.push({ fullLink: cleanedLink, basename });
      // Store the mapping from basename to full link
      linkMap.set(basename, cleanedLink);
    }

    // Remove [[...]] from content to get description
    let cleanedContent = labelContent.replace(/\[\[[^\]]*\]\]/g, '');
    cleanedContent = cleanedContent
      .replace(/\\n/g, '\n')
      .replace(/^[,\s\n#\-•]+/, '')
      .replace(/[, \n#\-•]+$/, '')
      .trim();

    const linkText = links.map(l => l.basename).join(' • ');
    const displayContent = normalizeLabelText(
      cleanedContent ? `${linkText}\n${cleanedContent}` : linkText
    );

    // Return properly formatted node definition
    return `${nodeVar}["${displayContent}"]`;
  };

  // Match square bracket labels: A["..."]
  const squarePattern = /(\w+)\["((?:[^"\\]|\\.)*)"\]/g;
  cleanCode = cleanCode.replace(squarePattern, (match, nodeVar, content) => {
    return processNode(match, nodeVar, content);
  });

  // Match parentheses labels: A("...")
  const parenPattern = /(\w+)\("((?:[^"\\]|\\.)*)"\)/g;
  cleanCode = cleanCode.replace(parenPattern, (match, nodeVar, content) => {
    return processNode(match, nodeVar, content);
  });

  // Match raw [[NodeName]]: A[[NodeName]]
  cleanCode = cleanCode.replace(/(\w+)\[\[([^\]]*)\]\](?!["])/g, (match, nodeVar, nodeId) => {
    const cleanedLink = cleanLink(nodeId);
    const basename = getBasename(cleanedLink);
    linkMap.set(basename, cleanedLink);
    return `${nodeVar}["${normalizeLabelText(basename)}"]`;
  });

  return { cleanCode, linkMap };
}

// MermaidWrapper component - provides a width-constrained container for diagrams
interface MermaidWrapperProps {
  theme: 'dark' | 'light' | 'beige';
  code: string;
  onNodeClick?: (nodeId: string) => void;
  linkMap?: Map<string, string>;  // basename -> full qualified link
}

const MermaidWrapper: React.FC<MermaidWrapperProps> = ({
  theme,
  code,
  onNodeClick,
  linkMap,
}) => {
  const colors = getThemeColors(theme);

  // Use the linkMap passed from parent (WikiDoc already processed the code)
  // The code prop is already clean (without [[...]]), so we should NOT re-process it
  const finalLinkMap = linkMap;

  return (
    <div
      style={{
        margin: '24px 0',
        borderRadius: '8px',
        overflow: 'auto',
        maxWidth: 'min(100%, 800px)',
        display: 'block',
      }}
    >
      <Suspense fallback={
        <div style={{
          height: '200px',
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: colors.card,
          borderRadius: '8px',
          border: `1px solid ${colors.border}`,
        }}>
          <span style={{
            color: colors.textMuted,
            fontSize: '14px',
          }}>
            Loading diagram...
          </span>
        </div>
      }>
        <InteractiveMermaidDiagram
          code={code}
          theme={theme}
          enableZoomPan={true}
          enableNodeInteraction={true}
          onNodeClick={onNodeClick}
          linkMap={finalLinkMap}
          maxHeight={450}
        />
      </Suspense>
    </div>
  );
};

// Memoize MermaidWrapper to prevent unnecessary re-renders
// Only re-render when theme or code actually changes
const MemoizedMermaidWrapper = React.memo(MermaidWrapper, (prevProps, nextProps) => {
  return (
    prevProps.theme === nextProps.theme &&
    prevProps.code === nextProps.code
    // Intentionally ignore onNodeClick and linkMap to prevent re-renders from callback/object changes
  );
});

// Node info type for the /node/{repo}/info API response
interface NodeInfo {
  qualified_name: string;
  name: string;
  node_type: string;
  exists: boolean;
  has_code: boolean;
  file?: string | null;
  path?: string | null;
  start_line?: number | null;
  end_line?: number | null;
  docstring?: string | null;
  child_count: number;
  action: 'view_code' | 'browse_folder' | 'view_file' | 'not_found';
}

interface WikiDocProps {
  markdown: string;
  codeBlocks?: CodeBlock[];
  references?: Array<{
    name?: string;
    identifier?: string;
    qualified_name?: string;
    file?: string | null;  // Support 'file' field (old format)
    path?: string | null;  // Support 'path' field (new format)
    startLine?: number;
    endLine?: number;
    start_line?: number | null;
    end_line?: number | null;
    ref: string;
    nodeType?: string;
    type?: string;
    code?: string;  // Embedded code for offline access (no API call needed)
    language?: string;  // Programming language of the code
    repo_name?: string;  // Repository name for cross-repo references
    node_only?: boolean;  // Marker for File/Folder nodes without code (show node info instead)
  }>;
  layoutMode?: 'split' | 'full';
  onAddCodeBlock?: (block: CodeBlock) => void;  // For adding code blocks to local state
  isStreaming?: boolean;  // Whether streaming generation is in progress
  onRegenerateSection?: (sectionId: string, sectionTitle: string, path: string) => void;  // For section regeneration
  sectionFiles?: string[];  // List of section files for ID extraction
  repoName?: string;  // Current repository name for resolving path-style node identifiers
  onNavigateToNode?: (qualifiedName: string) => void;  // Navigate to node in RepoViewer
  onNavigateToPaper?: (paperId: string) => void;  // Navigate to paper detail page
}

// Custom PreTag component that accepts any props but only renders children
const CustomPreTag = ({ children, ...props }: any) => <>{children}</>;

// Helper: get short display name from qualified name (last 2 segments)
function getBasenameDisplay(qualifiedName: string): string {
  const parts = qualifiedName.split('.');
  return parts.length > 1 ? parts.slice(-2).join('.') : qualifiedName;
}

// Helper to create a node link button element
function createNodeLinkButton(nodeId: string, onClick: (nodeId: string) => void): HTMLButtonElement {
  const button = document.createElement('button');
  button.textContent = getBasenameDisplay(nodeId);
  button.title = nodeId;
  button.dataset.nodeId = nodeId;
  button.className = 'path-tag inline-flex items-center cursor-pointer hover:opacity-80 transition-opacity mr-2 mb-1';
  button.type = 'button';
  button.onclick = (e) => {
    e.preventDefault();
    e.stopPropagation();
    onClick(nodeId);
  };
  return button;
}

// Helper to parse node links from text
function parseNodeLinks(text: string, onClick: (nodeId: string) => void): (Node | string)[] {
  // Match [text](#node:id) pattern
  const nodeLinkRegex = /\[([^\]]+)\]\(#node:([^)]+)\)/g;
  const parts: (Node | string)[] = [];
  let lastIndex = 0;
  let match;

  while ((match = nodeLinkRegex.exec(text)) !== null) {
    // Add text before the link
    if (match.index > lastIndex) {
      parts.push(text.substring(lastIndex, match.index));
    }

    // Add the node link button
    const [, label, nodeId] = match;
    // Use nodeId as the text for the button to match previous behavior
    parts.push(createNodeLinkButton(nodeId, onClick));

    lastIndex = match.index + match[0].length;
  }

  // Add remaining text
  if (lastIndex < text.length) {
    parts.push(text.substring(lastIndex));
  }

  return parts;
}

// Helper function to extract section ID from title and section files
function extractSectionIdFromTitle(title: string, sectionFiles?: string[]): string | null {
  if (!sectionFiles || sectionFiles.length === 0) return null;

  // Normalize title for matching
  const normalizedTitle = title.toLowerCase()
    .replace(/[^\w\s\u4e00-\u9fa5-]/g, '')  // Keep word chars, spaces, Chinese, hyphens
    .replace(/\s+/g, '_')                      // Replace spaces with underscores
    .replace(/-+/g, '_');                      // Replace hyphens with underscores

  // First, try exact match with section files
  for (const file of sectionFiles) {
    const fileName = file.split('/').pop()?.replace('.md', '').replace('.messages.json', '') || '';
    if (fileName.toLowerCase() === normalizedTitle) {
      return fileName;
    }
  }

  // Try partial match (contains)
  for (const file of sectionFiles) {
    const fileName = file.split('/').pop()?.replace('.md', '').replace('.messages.json', '') || '';
    const normalizedFileName = fileName.toLowerCase();
    if (normalizedFileName.includes(normalizedTitle) || normalizedTitle.includes(normalizedFileName)) {
      return fileName;
    }
  }

  // If no match found, try to match by removing common prefixes/suffixes
  const cleanTitle = normalizedTitle.replace(/^(\d+_)/, '');  // Remove prefix like "001_"
  for (const file of sectionFiles) {
    const fileName = file.split('/').pop()?.replace('.md', '').replace('.messages.json', '') || '';
    const cleanFileName = fileName.toLowerCase().replace(/^(\d+_)/, '');
    if (cleanFileName === cleanTitle) {
      return fileName;  // Return original filename with prefix
    }
  }

  return null;
}

// Wrap WikiDoc with React.memo to prevent unnecessary re-renders
const WikiDocComponent = ({ markdown, codeBlocks, references, layoutMode, onAddCodeBlock, isStreaming, onRegenerateSection, sectionFiles, repoName, onNavigateToNode, onNavigateToPaper }: WikiDocProps) => {
  const { addCodeBlock } = useWikiStore();
  // Use global theme context instead of local detection
  const { theme } = useTheme();
  const colors = getThemeColors(theme);
  const { openRepoViewer } = useRepoViewer();

  // Store callbacks in refs to prevent context re-renders from cascading to ReactMarkdown.
  // When openRepoViewer updates repo-viewer context, WikiDoc re-renders (useContext subscriber),
  // but by keeping handlers in refs, the memoized ReactMarkdown components stay stable
  // and mermaid diagrams don't remount/lose zoom/pan state.
  const openRepoViewerRef = useRef(openRepoViewer);
  openRepoViewerRef.current = openRepoViewer;
  const onRegenerateSectionRef = useRef(onRegenerateSection);
  onRegenerateSectionRef.current = onRegenerateSection;

  // Helper function to extract repo name from a node identifier
  // Handles both qualified names (repo.module.class) and paths (module/class)
  const extractRepoName = useCallback((nodeId: string): string => {
    // If nodeId contains dots, it's a qualified name - extract first part
    if (nodeId.includes('.') && !nodeId.includes('/')) {
      return nodeId.split('.')[0];
    }
    // If nodeId contains slashes, it's a path - use the provided repoName prop
    if (nodeId.includes('/')) {
      return repoName || nodeId.split('/')[0];
    }
    // Single word - could be either, prefer repoName if available
    return repoName || nodeId;
  }, [repoName]);

  // Helper function to convert a path-style identifier to a qualified name
  // Handles file extensions properly - backend uses file stem (without extension)
  const toQualifiedName = useCallback((nodeId: string): string => {
    // If already uses dots and no slashes, assume it's already a qualified name
    if (nodeId.includes('.') && !nodeId.includes('/')) {
      return nodeId;
    }
    // Convert path to qualified name: frontend/app/api/route.ts -> repoName.frontend.app.api.route
    if (nodeId.includes('/')) {
      // Split by slash
      const parts = nodeId.split('/');
      // For the last part, if it has a file extension, remove it (backend uses stem)
      const processedParts = parts.map((part, index) => {
        if (index === parts.length - 1 && part.includes('.')) {
          // Last part with extension: route.ts -> route
          const lastDot = part.lastIndexOf('.');
          return part.substring(0, lastDot);
        }
        return part;
      });
      const pathQualified = processedParts.join('.');
      if (repoName && !pathQualified.startsWith(repoName + '.')) {
        return `${repoName}.${pathQualified}`;
      }
      return pathQualified;
    }
    return nodeId;
  }, [repoName]);

  // Helper: hash a string to a 12-char hex block ID
  const hashToBlockId = useCallback((str: string) => {
    let hash = 0;
    for (let i = 0; i < str.length; i++) {
      const char = str.charCodeAt(i);
      hash = ((hash << 5) - hash) + char;
      hash = hash & hash;
    }
    return `block-${Math.abs(hash).toString(16).padStart(12, '0').slice(0, 12)}`;
  }, []);

  // Process markdown to convert node identifiers to standard markdown links with custom protocol
  // Protect ALL code blocks (not just mermaid) to prevent wikilink conversion in code
  const processedMarkdown = (() => {
    let md = markdown || '';
    const codeBlocks: Array<{ lang: string; code: string }> = [];

    // Step 1: Extract ALL code blocks and replace with placeholders
    // This protects python, js, typescript, etc. code blocks from having [[...]] converted
    md = md.replace(/```(\w*)\n([\s\S]*?)```/g, (match, lang, code) => {
      const placeholder = `__CODE_BLOCK_${codeBlocks.length}__`;
      codeBlocks.push({ lang, code });
      return placeholder;
    });

    // Step 2a: Convert [[paper:ID|Title]] to paper citation links.
    // In markdown tables the AI escapes | as \|, so we match both \| and |.
    // The captured paperId may have a trailing backslash which we strip.
    md = md.replace(/\[\[paper:([^\]]+?)\\?\|([^\]]+)\]\]/g, (match, paperId, title) => {
      return `[${title}](#paper:${paperId.replace(/\\+$/, '')})`;
    });

    // Step 2b: Convert [[...]] to markdown links in non-code content only
    md = md.replace(/\[\[([^\]]+)\]\]/g, (match, nodeId) => {
      return `[${nodeId}](#node:${nodeId})`;
    });

    // Step 3: Restore all code blocks (with original [[...]] intact)
    md = md.replace(/__CODE_BLOCK_(\d+)__/g, (match, idx) => {
      const block = codeBlocks[parseInt(idx)];
      return `\`\`\`${block.lang}\n${block.code}\`\`\``;
    });

    return md;
  })();

  // Resolve a nodeId to a qualified name using references
  const resolveQualifiedName = useCallback((nodeId: string): string => {
    if (!references) return toQualifiedName(nodeId);
    const getName = (ref: any) => ref.qualified_name || ref.name || ref.identifier || '';

    // Try multiple matching strategies
    const ref =
      references.find(r => r.identifier === nodeId) ||
      references.find(r => getName(r) === nodeId) ||
      references.find(r => { const n = getName(r); const p = n.split('.'); return p.length >= 2 && p.slice(-2).join('.') === nodeId; }) ||
      references.find(r => { const n = getName(r); return n.endsWith('.' + nodeId) || n === nodeId; }) ||
      references.find(r => { const n = getName(r); const p = n.split('.'); return p[p.length - 1] === nodeId; });

    if (ref) {
      return ref.qualified_name || ref.identifier || ref.name || toQualifiedName(nodeId);
    }
    return toQualifiedName(nodeId);
  }, [references, toQualifiedName]);

  // Store code block for export (without displaying it)
  const storeCodeBlockForExport = useCallback((ref: any, nodeId: string) => {
    const refFile = ref?.file || ref?.path;
    const startLine = ref?.startLine || ref?.start_line;
    const endLine = ref?.endLine || ref?.end_line;
    if (!refFile || !startLine || !endLine || !ref?.code) return;

    const blockId = hashToBlockId(`${refFile}:${startLine}:${endLine}`);
    const newBlock: CodeBlock = {
      id: blockId,
      file: refFile,
      startLine,
      endLine,
      code: ref.code,
      language: ref.language || 'python',
    };
    if (onAddCodeBlock) {
      onAddCodeBlock(newBlock);
    } else {
      addCodeBlock(newBlock);
    }
  }, [hashToBlockId, onAddCodeBlock, addCodeBlock]);

  // Handle [[node link]] clicks — navigate to RepoViewer or floating panel
  const handleNodeLinkClick = useCallback(async (nodeId: string) => {
    const qualifiedName = resolveQualifiedName(nodeId);

    // Find matching reference (for export storage)
    const getName = (ref: any) => ref.qualified_name || ref.name || ref.identifier || '';
    const ref = references?.find(r =>
      r.identifier === nodeId || getName(r) === nodeId ||
      getName(r).endsWith('.' + nodeId) || getName(r).split('.').pop() === nodeId
    );

    // Store code block for export if available
    if (ref) storeCodeBlockForExport(ref, nodeId);

    // Navigate: prefer embedded RepoViewer, fall back to floating panel
    if (onNavigateToNode) {
      onNavigateToNode(qualifiedName);
    } else {
      const targetRepo = repoName || extractRepoName(nodeId);
      openRepoViewerRef.current(targetRepo, qualifiedName);
    }
  }, [resolveQualifiedName, references, storeCodeBlockForExport, onNavigateToNode, repoName, extractRepoName]);

  // Handle code link clicks — navigate to file in RepoViewer
  const handleCodeLinkClick = useCallback((file: string, startLine: number, endLine: number) => {
    // Find matching reference to get its qualified_name
    const ref = references?.find(r => {
      const refFile = r.file || r.path;
      const refStart = r.startLine || r.start_line;
      const refEnd = r.endLine || r.end_line;
      return refFile === file && refStart === startLine && refEnd === endLine;
    });

    const qualifiedName = ref?.qualified_name || toQualifiedName(file);
    const targetRepo = repoName || extractRepoName(qualifiedName);

    if (onNavigateToNode) {
      onNavigateToNode(qualifiedName);
    } else {
      openRepoViewerRef.current(targetRepo, qualifiedName);
    }
  }, [references, toQualifiedName, repoName, extractRepoName, onNavigateToNode]);

  // Store click handlers in refs so the memoized components object stays stable
  const handleNodeLinkClickRef = useRef(handleNodeLinkClick);
  handleNodeLinkClickRef.current = handleNodeLinkClick;
  const handleCodeLinkClickRef = useRef(handleCodeLinkClick);
  handleCodeLinkClickRef.current = handleCodeLinkClick;
  const onNavigateToPaperRef = useRef(onNavigateToPaper);
  onNavigateToPaperRef.current = onNavigateToPaper;

  // Memoize ReactMarkdown components to prevent remounting children (especially mermaid diagrams)
  // when WikiDoc re-renders due to context changes (e.g. repo-viewer state update).
  // All click handlers use refs so they don't appear in deps.
  const markdownComponents = useMemo(() => ({
    h1: ({children}: any) => {
      const text = extractTextFromChildren(children);
      const anchor = generateAnchorId(text);
      return <h1 id={anchor} data-anchor={anchor} data-heading-text={text}>{children}</h1>;
    },
    h2: ({children}: any) => {
      const text = extractTextFromChildren(children);
      const anchor = generateAnchorId(text);
      const sectionId = extractSectionIdFromTitle(text, sectionFiles);
      return (
        <h2 id={anchor} data-anchor={anchor} data-heading-text={text} style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          {children}
          {onRegenerateSectionRef.current && sectionId && (
            <button
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                onRegenerateSectionRef.current?.(sectionId, text, sectionId);
              }}
              style={{
                padding: '2px 8px',
                fontSize: '11px',
                background: 'transparent',
                border: `1px solid ${colors.border}`,
                borderRadius: '4px',
                color: colors.textMuted,
                cursor: 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '4px',
                opacity: 0.7,
                transition: 'all 0.15s',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.opacity = '1';
                e.currentTarget.style.background = colors.bgSecondary;
                e.currentTarget.style.color = colors.accent;
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.opacity = '0.7';
                e.currentTarget.style.background = 'transparent';
                e.currentTarget.style.color = colors.textMuted;
              }}
              title="Regenerate this section"
            >
              🔄 {text.length > 20 ? '' : 'Regenerate'}
            </button>
          )}
        </h2>
      );
    },
    h3: ({children}: any) => {
      const text = extractTextFromChildren(children);
      const anchor = generateAnchorId(text);
      return <h3 id={anchor} data-anchor={anchor} data-heading-text={text}>{children}</h3>;
    },
    h4: ({children}: any) => {
      const text = extractTextFromChildren(children);
      const anchor = generateAnchorId(text);
      return <h4 id={anchor} data-anchor={anchor} data-heading-text={text}>{children}</h4>;
    },
    h5: ({children}: any) => {
      const text = extractTextFromChildren(children);
      const anchor = generateAnchorId(text);
      return <h5 id={anchor} data-anchor={anchor} data-heading-text={text}>{children}</h5>;
    },
    h6: ({children}: any) => {
      const text = extractTextFromChildren(children);
      const anchor = generateAnchorId(text);
      return <h6 id={anchor} data-anchor={anchor} data-heading-text={text}>{children}</h6>;
    },
    a: ({ href, children, className }: any) => {
      if (href?.startsWith('#paper:')) {
        const paperId = href.replace('#paper:', '');
        return (
          <button
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              if (onNavigateToPaperRef.current) {
                onNavigateToPaperRef.current(paperId);
              }
            }}
            className="paper-cite-tag inline-flex items-center cursor-pointer hover:opacity-80 transition-opacity mr-1 mb-1"
            type="button"
            title={paperId}
          >
            {children}
          </button>
        );
      }
      if (href?.startsWith('#node:')) {
        const nodeId = href.replace('#node:', '');
        const parts = nodeId.split('.');
        const basename = parts.length > 1 ? parts.slice(-2).join('.') : nodeId;
        return (
          <button
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              handleNodeLinkClickRef.current(nodeId);
            }}
            className="path-tag inline-flex items-center cursor-pointer hover:opacity-80 transition-opacity mr-2 mb-1"
            type="button"
            title={nodeId}
          >
            {basename}
          </button>
        );
      }
      const codeRefMatch = href?.match(/^(.+)#(\d+)-(\d+)$/);
      if (codeRefMatch || className === 'code-link') {
        const [, file, start, end] = codeRefMatch || [];
        if (file && start && end) {
          return (
            <button
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                handleCodeLinkClickRef.current(file, parseInt(start), parseInt(end));
              }}
              className="path-tag inline-flex items-center gap-1 cursor-pointer hover:opacity-80 transition-opacity"
              type="button"
            >
              {children}
            </button>
          );
        }
      }
      return (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[var(--accent-blue)] hover:underline transition-colors"
          onClick={(e) => {
            if (href?.startsWith('#')) {
              e.preventDefault();
            }
          }}
        >
          {children}
        </a>
      );
    },
    code: ({ className, children, ref: _ref, ...props }: any) => {
      const isInline = !className;
      const hasNodeLink = typeof children === 'string' && /\[[^\]]+\]\(#node:[^)]+\)/.test(children);
      if (hasNodeLink) {
        const parts = parseNodeLinks(children, handleNodeLinkClickRef.current);
        return (
          <span className="inline">
            {parts.map((part, idx) => {
              if (typeof part === 'string') {
                return part;
              } else {
                const button = part as HTMLButtonElement;
                const nodeId = button.dataset?.nodeId || button.textContent || '';
                return (
                  <button
                    key={idx}
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      handleNodeLinkClickRef.current(nodeId);
                    }}
                    className="path-tag inline-flex items-center cursor-pointer hover:opacity-80 transition-opacity mr-2 mb-1"
                    type="button"
                    title={nodeId}
                  >
                    {button.textContent}
                  </button>
                );
              }
            })}
          </span>
        );
      }
      if (isInline) {
        return <code>{children}</code>;
      }
      const language = className ? className.replace('language-', '').trim().toLowerCase() : 'text';
      if (language === 'mermaid') {
        const mermaidCode = String(children).replace(/\n$/, '');
        const mermaidResult = processMermaidCode(mermaidCode);
        return (
          <MemoizedMermaidWrapper
            theme={theme}
            code={mermaidResult.cleanCode}
            onNodeClick={handleNodeLinkClickRef.current}
            linkMap={mermaidResult.linkMap}
          />
        );
      }
      const getSyntaxStyle = () => {
        switch (theme) {
          case 'light': return customSyntaxThemeLight;
          case 'beige': return customSyntaxThemeBeige;
          default: return customSyntaxTheme;
        }
      };
      return (
        <SyntaxHighlighter
          language={language}
          style={getSyntaxStyle() as any}
          PreTag={CustomPreTag}
          customStyle={{
            margin: 0,
            padding: 0,
            background: 'transparent',
            fontSize: 'var(--left-font-size, 14px)',
            lineHeight: '1.5',
            fontFamily: 'var(--font-jetbrains-mono), Menlo, Monaco, Consolas',
          }}
          codeTagProps={{
            style: {
              background: 'transparent',
              border: 'none',
              padding: 0,
            }
          }}
          {...props}
        >
          {String(children).replace(/\n$/, '')}
        </SyntaxHighlighter>
      );
    },
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [theme, colors.border, colors.textMuted, colors.bgSecondary, colors.accent, sectionFiles]);

  return (
    <>
      <section
        className="doc-card"
        style={
          layoutMode === 'full'
            ? { maxHeight: 'calc(100vh - 100px)', overflow: 'auto' }
            : { width: '100%', maxWidth: '100%', overflow: 'hidden' }
        }
      >
        <div className="prose" style={{ maxWidth: '100%', width: '100%' }}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm, [remarkMath, { singleDollarTextMath: true }]]}
          rehypePlugins={[rehypeKatex]}
          components={markdownComponents}
        >
          {processedMarkdown}
        </ReactMarkdown>
      </div>
    </section>

  </>
  );
};

// Export memoized version to prevent unnecessary re-renders
// Custom comparison function to prevent re-renders when only callbacks change
export const WikiDoc = React.memo(WikiDocComponent, (prevProps, nextProps) => {
  // Only re-render if these key props actually change

  // Quick checks for primitive props
  if (prevProps.markdown !== nextProps.markdown) return false;
  if (prevProps.isStreaming !== nextProps.isStreaming) return false;
  if (prevProps.layoutMode !== nextProps.layoutMode) return false;
  if (prevProps.repoName !== nextProps.repoName) return false;

  // Compare codeBlocks by length
  if (prevProps.codeBlocks?.length !== nextProps.codeBlocks?.length) return false;

  // Compare sectionFiles array by length
  if (prevProps.sectionFiles?.length !== nextProps.sectionFiles?.length) return false;

  // Compare references more thoroughly - check length and first item's identifier
  // This catches the case where references go from undefined/empty to having content
  const prevRefs = prevProps.references;
  const nextRefs = nextProps.references;
  if (prevRefs?.length !== nextRefs?.length) return false;
  // If both have items, check if the first item's identifier changed (quick content check)
  if (prevRefs?.length && nextRefs?.length) {
    if (prevRefs[0]?.identifier !== nextRefs[0]?.identifier) return false;
    if (prevRefs[0]?.qualified_name !== nextRefs[0]?.qualified_name) return false;
  }

  // Intentionally ignore callback props (onActivateBlock, onAddCodeBlock, etc.)
  // to prevent re-renders when parent component re-creates these functions
  return true;
});

// Helper function: Extract code references from markdown
function extractCodeReferences(markdown: string): Array<{ file: string; start: number; end: number }> {
  const regex = /\[([^\]]+)\]\(([^#]+)#(\d+)-(\d+)\)/g;
  const references: Array<{ file: string; start: number; end: number }> = [];
  let match;

  while ((match = regex.exec(markdown)) !== null) {
    references.push({
      file: match[2],
      start: parseInt(match[3]),
      end: parseInt(match[4]),
    });
  }

  return references;
}

// Helper function: Deduplicate code blocks
function deduplicateCodeBlocks(
  references: Array<{ file: string; start: number; end: number }>
): Array<{ file: string; start: number; end: number }> {
  const seen = new Set<string>();
  const unique: Array<{ file: string; start: number; end: number }> = [];

  references.forEach((ref) => {
    const key = `${ref.file}:${ref.start}-${ref.end}`;
    if (!seen.has(key)) {
      seen.add(key);
      unique.push(ref);
    }
  });

  return unique;
}

// Load code from calflops project (mock implementation)
function loadCalflopsCode(file: string, start: number, end: number): string {
  // Simplified mock implementation for demo
  return `# Code from ${file} (lines ${start}-${end})\n# This is a placeholder for actual code loading`;
}

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Export utilities for converting markdown content with code blocks to downloadable formats.
 */

export interface CodeBlockData {
  id: string;
  file: string;
  startLine: number;
  endLine: number;
  code: string;
  language: string;
}

export interface ReferenceData {
  name: string;
  file: string;
  startLine: number;
  endLine: number;
  ref: string;
  nodeType?: string;
}

export interface ExportData {
  title?: string;
  markdown: string;
  codeBlocks?: CodeBlockData[];
  references?: ReferenceData[];
  metadata?: {
    repoName?: string;
    timestamp?: string;
    [key: string]: any;
  };
}

// ===== Enhanced Export Types =====

/**
 * Extended code block data with export-specific fields
 */
export interface FetchedCodeBlock extends CodeBlockData {
  nodeName: string;      // Original node name from [[NodeName]]
  nodeType?: string;     // Node type (Class, Function, Method, etc.)
  fetchError?: string;   // Error message if fetch failed
  qualifiedName?: string; // Fully qualified name
}

/**
 * Options for exportWithAllCode function
 */
export interface ExportWithAllCodeOptions {
  title?: string;
  markdown: string;
  repoName: string;
  references?: ReferenceData[];
  codeBlocks?: CodeBlockData[];
  metadata?: Record<string, any>;
  onProgress?: (status: string) => void;
  /** Whether to embed code inline with collapsible details (default: false = appendix at end) */
  inlineCode?: boolean;
  /** Whether to make code blocks collapsible (default: true) */
  collapsibleCode?: boolean;
}

// ===== Node Reference Extraction =====

/**
 * Extract all [[NodeName]] references from markdown
 */
export function extractNodeReferences(markdown: string): string[] {
  const pattern = /\[\[([^\]]+)\]\]/g;
  const refs = new Set<string>();
  let match;
  while ((match = pattern.exec(markdown)) !== null) {
    refs.add(match[1]);
  }
  return Array.from(refs);
}

/**
 * Generate a URL-safe anchor ID for code references
 */
export function generateCodeAnchorId(nodeName: string): string {
  return `code-ref-${nodeName.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
}

/**
 * Generate a URL-safe anchor ID for back references (from code to text)
 */
export function generateBackRefAnchorId(nodeName: string): string {
  return `ref-${nodeName.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
}

/**
 * Convert [[NodeName]] references to markdown anchor links with back-reference anchors
 * This creates bidirectional links: text -> code and code -> text
 */
export function convertNodeLinksToAnchors(markdown: string): string {
  const seenNodes = new Set<string>();

  return markdown.replace(/\[\[([^\]]+)\]\]/g, (match, nodeName) => {
    const codeAnchorId = generateCodeAnchorId(nodeName);
    const backRefAnchorId = generateBackRefAnchorId(nodeName);

    // Only add back-reference anchor for the first occurrence of each node
    if (!seenNodes.has(nodeName)) {
      seenNodes.add(nodeName);
      // Add anchor before the link so user can return to this position
      return `<a id="${backRefAnchorId}"></a>[${nodeName}](#${codeAnchorId})`;
    }

    // Subsequent occurrences just get the link without anchor
    return `[${nodeName}](#${codeAnchorId})`;
  });
}

/**
 * Create a collapsible details block for code
 */
function createCodeDetailsBlock(
  nodeName: string,
  block: FetchedCodeBlock,
  collapsible: boolean
): string {
  const summary = `${nodeName} - \`${block.file}\` (lines ${block.startLine}-${block.endLine})`;

  if (block.fetchError) {
    return `**${nodeName}** - ⚠️ ${block.fetchError}\n\n`;
  }

  const codeContent = `\`\`\`${block.language}\n${block.code}\n\`\`\``;

  if (collapsible) {
    return `<details>\n<summary>${summary}</summary>\n\n${codeContent}\n\n</details>\n\n`;
  } else {
    return `### ${nodeName}\n\n**File:** \`${block.file}\` (lines ${block.startLine}-${block.endLine})\n\n${codeContent}\n\n`;
  }
}

/**
 * Convert [[NodeName]] references to anchor links with inline collapsible code blocks below.
 * Code blocks are added at line breaks, not in the middle of sentences.
 */
export function convertNodeLinksToInlineCode(
  markdown: string,
  codeBlocksMap: Map<string, FetchedCodeBlock>,
  collapsible: boolean
): string {
  const lines = markdown.split('\n');
  const result: string[] = [];
  const nodePattern = /\[\[([^\]]+)\]\]/g;

  for (const line of lines) {
    // Collect all node references in this line
    const refsInLine: string[] = [];
    let match;
    while ((match = nodePattern.exec(line)) !== null) {
      refsInLine.push(match[1]);
    }
    nodePattern.lastIndex = 0; // Reset regex state

    // Convert [[NodeName]] to [NodeName](#anchor) in the line
    const convertedLine = line.replace(/\[\[([^\]]+)\]\]/g, (_, nodeName) => {
      const anchorId = generateCodeAnchorId(nodeName);
      return `[${nodeName}](#${anchorId})`;
    });

    result.push(convertedLine);

    // Add code blocks after the line (at line break)
    for (const nodeName of refsInLine) {
      const block = codeBlocksMap.get(nodeName);
      if (block && !block.fetchError) {
        const anchorId = generateCodeAnchorId(nodeName);
        const summary = `📄 ${nodeName} - \`${block.file}\` (lines ${block.startLine}-${block.endLine})`;
        const codeContent = `\`\`\`${block.language}\n${block.code}\n\`\`\``;

        if (collapsible) {
          result.push('');
          result.push(`<details>`);
          result.push(`<summary><a id="${anchorId}"></a>${summary}</summary>`);
          result.push('');
          result.push(codeContent);
          result.push('');
          result.push(`</details>`);
        } else {
          result.push('');
          result.push(`<a id="${anchorId}"></a>**${nodeName}** - \`${block.file}\` (lines ${block.startLine}-${block.endLine})`);
          result.push('');
          result.push(codeContent);
        }
      }
    }
  }

  return result.join('\n');
}

/**
 * Convert markdown with code references to markdown with embedded code blocks.
 * This replaces code reference links with actual code content.
 */
export function enrichMarkdownWithCode(data: ExportData): string {
  const { markdown, codeBlocks = [], references = [], title, metadata } = data;
  let enriched = markdown;

  // Add title and metadata header
  let header = '';
  if (title) {
    header += `# ${title}\n\n`;
  }
  if (metadata?.repoName) {
    header += `> Repository: ${metadata.repoName}\n`;
  }
  if (metadata?.timestamp) {
    header += `> Generated: ${metadata.timestamp}\n`;
  }
  if (header) {
    header += '\n---\n\n';
  }

  // Find and replace code reference patterns with actual code blocks
  // Pattern 1: [code](file.py#10-20) format
  enriched = enriched.replace(/\[([^\]]+)\]\(([^)]+\.py[#\d\-]+)\)/gi, (match, label, fileRef) => {
    const codeRefMatch = fileRef.match(/^(.+\.py)#(\d+)-(\d+)$/);
    if (codeRefMatch) {
      const [, file, start, end] = codeRefMatch;
      const block = findCodeBlock(codeBlocks, file, parseInt(start), parseInt(end));
      if (block) {
        return `\n\`\`\`${block.language}\n// ${label}\n// File: ${file} (lines ${start}-${end})\n${block.code}\n\`\`\`\n`;
      }
    }
    return match;
  });

  // Pattern 2: Inline code references like [[ClassName]] - add references section at the end
  const referencedNodes = new Set<string>();
  const nodeLinkPattern = /\[\[([^\]]+)\]\]/g;
  let match;
  while ((match = nodeLinkPattern.exec(enriched)) !== null) {
    referencedNodes.add(match[1]);
  }

  // If we have references data, add a references section
  if (referencedNodes.size > 0 && references.length > 0) {
    enriched += '\n\n---\n\n## References\n\n';
    for (const nodeName of referencedNodes) {
      const ref = references.find(r =>
        r.ref === nodeName ||
        r.name === nodeName ||
        r.name.endsWith(nodeName) ||
        nodeName.endsWith(r.name)
      );
      if (ref) {
        enriched += `- **${nodeName}**: \`${ref.file}\` (lines ${ref.startLine}-${ref.endLine})\n`;
      }
    }
  }

  // Append all code blocks as appendices for reference
  if (codeBlocks.length > 0) {
    enriched += '\n\n---\n\n## Code References\n\n';
    for (const block of codeBlocks) {
      enriched += `### ${block.file} (lines ${block.startLine}-${block.endLine})\n\n`;
      enriched += `\`\`\`${block.language}\n${block.code}\n\`\`\`\n\n`;
    }
  }

  return header + enriched;
}

/**
 * Find a code block by file, start line, and end line.
 */
function findCodeBlock(
  codeBlocks: CodeBlockData[],
  file: string,
  startLine: number,
  endLine: number
): CodeBlockData | undefined {
  // Try exact match first
  let block = codeBlocks.find(
    b => b.file === file && b.startLine === startLine && b.endLine === endLine
  );
  if (block) return block;

  // Try fuzzy match (same file, overlapping range)
  block = codeBlocks.find(
    b => b.file === file &&
    b.startLine <= endLine &&
    b.endLine >= startLine
  );
  return block;
}

/**
 * Download content as a markdown file.
 */
export function downloadMarkdown(content: string, filename: string) {
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename.endsWith('.md') ? filename : `${filename}.md`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

/**
 * Download content as a PDF file using html2pdf.js library.
 * This function should be called after the library is loaded.
 */
export async function downloadPDF(
  element: HTMLElement,
  filename: string,
  options?: {
    orientation?: 'portrait' | 'landscape';
    format?: 'a4' | 'letter';
    margin?: number;
    // Advanced passthrough options for html2pdf/html2canvas
    html2canvas?: Record<string, unknown>;
    jsPDF?: Record<string, unknown>;
    image?: Record<string, unknown>;
    pagebreak?: Record<string, unknown>;
  }
): Promise<void> {
  // Dynamically import html2pdf.js
  const html2pdf = (await import('html2pdf.js')).default;

  const defaultOptions = {
    margin: options?.margin || 10,
    filename: filename.endsWith('.pdf') ? filename : `${filename}.pdf`,
    image: { type: 'jpeg', quality: 0.98, ...(options?.image || {}) },
    html2canvas: {
      scale: 2,
      useCORS: true,
      letterRendering: true,
      logging: false,
      scrollX: 0,
      scrollY: 0,
      ...(options?.html2canvas || {}),
    },
    jsPDF: {
      unit: 'mm',
      format: options?.format || 'a4',
      orientation: options?.orientation || 'portrait',
      ...(options?.jsPDF || {}),
    },
    pagebreak: {
      // Try to avoid splitting code blocks/tables across pages
      mode: ['css', 'legacy'],
      avoid: ['pre', 'table', 'blockquote'],
      ...(options?.pagebreak || {}),
    },
  };

  await html2pdf().set(defaultOptions).from(element).save();
}

/**
 * Wait for mermaid diagrams to finish rendering
 * Checks for the presence of SVG elements within mermaid containers
 */
async function waitForMermaidRender(element: HTMLElement, timeoutMs: number = 5000): Promise<void> {
  const startTime = Date.now();
  const checkInterval = 100;

  while (Date.now() - startTime < timeoutMs) {
    const mermaidContainers = element.querySelectorAll('.mermaid');
    let allRendered = true;

    for (const container of mermaidContainers) {
      // Check if mermaid has rendered SVG content
      const svg = container.querySelector('svg');
      if (!svg || svg.children.length === 0) {
        allRendered = false;
        break;
      }
    }

    if (allRendered || mermaidContainers.length === 0) {
      return; // All rendered or no mermaid diagrams
    }

    await new Promise(resolve => setTimeout(resolve, checkInterval));
  }
}

/**
 * Export an already-rendered DOM subtree to PDF.
 *
 * This is more faithful than converting markdown -> HTML, and also avoids
 * clipping when the on-screen container is scrollable (we clone it into an
 * offscreen, non-scrollable wrapper).
 */
export async function downloadRenderedPDF(
  sourceElement: HTMLElement,
  filename: string,
  options?: {
    orientation?: 'portrait' | 'landscape';
    format?: 'a4' | 'letter';
    margin?: number;
    paddingPx?: number;
    waitForMermaid?: boolean;
  }
): Promise<void> {
  // Wait for mermaid diagrams to render if requested
  if (options?.waitForMermaid !== false) {
    await waitForMermaidRender(sourceElement);
  }

  const wrapper = document.createElement('div');
  const rect = sourceElement.getBoundingClientRect();
  const widthPx = Math.max(320, Math.ceil(rect.width));
  const padding = options?.paddingPx ?? 24;

  // Offscreen wrapper to avoid affecting layout while capturing full content
  wrapper.style.position = 'fixed';
  wrapper.style.left = '-100000px';
  wrapper.style.top = '0';
  wrapper.style.width = `${widthPx}px`;
  wrapper.style.padding = `${padding}px`;
  wrapper.style.boxSizing = 'border-box';
  wrapper.style.background = getComputedStyle(document.body).backgroundColor || '#ffffff';
  wrapper.style.color = getComputedStyle(document.body).color || '#111111';
  wrapper.style.overflow = 'visible';

  const clone = sourceElement.cloneNode(true) as HTMLElement;
  // Ensure we don't keep any viewport-clipping styles from the on-screen container
  clone.style.maxHeight = 'none';
  clone.style.height = 'auto';
  clone.style.overflow = 'visible';

  // Process mermaid SVGs to ensure inline styles are included
  const mermaidSvgs = clone.querySelectorAll('.mermaid svg');
  for (const svg of mermaidSvgs) {
    const svgEl = svg as SVGSVGElement;
    // Ensure the SVG has proper dimensions
    if (!svg.getAttribute('width') || !svg.getAttribute('height')) {
      const bbox = svgEl.getBBox();
      svg.setAttribute('width', String(bbox.width || '100%'));
      svg.setAttribute('height', String(bbox.height || '100%'));
    }
    // Force inline styling for better PDF compatibility
    svgEl.style.display = 'block';
    svgEl.style.maxWidth = '100%';
  }

  wrapper.appendChild(clone);
  document.body.appendChild(wrapper);

  try {
    await downloadPDF(wrapper, filename, {
      orientation: options?.orientation,
      format: options?.format,
      margin: options?.margin,
      html2canvas: {
        // Make sure the full offscreen content is captured
        windowWidth: wrapper.scrollWidth,
        // Improve image quality and rendering
        scale: 2,
        useCORS: true,
        logging: false,
      },
      pagebreak: {
        // Avoid splitting code blocks and mermaid diagrams across pages
        mode: ['css', 'legacy'],
        avoid: ['pre', 'table', 'blockquote', '.mermaid'],
      },
    });
  } finally {
    document.body.removeChild(wrapper);
  }
}

/**
 * Create a temporary HTML element with styled content for PDF export.
 * Uses marked to convert markdown to HTML.
 */
export async function createExportableHTML(markdown: string, theme: 'light' | 'dark' | 'beige'): Promise<HTMLElement> {
  // Dynamically import marked for markdown to HTML conversion
  const { marked } = await import('marked');

  // Configure marked for GFM (GitHub Flavored Markdown)
  marked.setOptions({
    gfm: true,
    breaks: true,
  });

  const div = document.createElement('div');
  div.className = 'export-content';

  // Basic styling for export
  const styles = `
    <style>
      .export-content {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif;
        line-height: 1.6;
        max-width: 800px;
        margin: 0 auto;
        padding: 20px;
        color: ${theme === 'dark' ? '#e6edf3' : '#1f2328'};
        background: ${theme === 'dark' ? '#0d1117' : '#ffffff'};
      }
      .export-content h1, .export-content h2, .export-content h3, .export-content h4 {
        margin-top: 1.5em;
        margin-bottom: 0.5em;
        font-weight: 600;
      }
      .export-content h1 { font-size: 2em; border-bottom: 1px solid ${theme === 'dark' ? '#30363d' : '#d0d7de'}; padding-bottom: 0.3em; }
      .export-content h2 { font-size: 1.5em; border-bottom: 1px solid ${theme === 'dark' ? '#30363d' : '#d0d7de'}; padding-bottom: 0.3em; }
      .export-content h3 { font-size: 1.25em; }
      .export-content h4 { font-size: 1.1em; }
      .export-content p { margin-bottom: 1em; }
      .export-content ul, .export-content ol { margin-bottom: 1em; padding-left: 2em; }
      .export-content li { margin-bottom: 0.25em; }
      .export-content code {
        background: ${theme === 'dark' ? '#161b22' : '#f6f8fa'};
        padding: 0.2em 0.4em;
        border-radius: 3px;
        font-family: "JetBrains Mono", "Fira Code", Consolas, monospace;
        font-size: 0.9em;
      }
      .export-content pre {
        background: ${theme === 'dark' ? '#161b22' : '#f6f8fa'};
        border: 1px solid ${theme === 'dark' ? '#30363d' : '#d0d7de'};
        border-radius: 6px;
        padding: 16px;
        overflow-x: auto;
        margin-bottom: 1em;
        white-space: pre-wrap;
        word-wrap: break-word;
      }
      .export-content pre code {
        background: transparent;
        padding: 0;
        white-space: pre-wrap;
        word-wrap: break-word;
      }
      .export-content blockquote {
        border-left: 4px solid ${theme === 'dark' ? '#30363d' : '#d0d7de'};
        padding-left: 1em;
        margin-left: 0;
        margin-bottom: 1em;
        color: ${theme === 'dark' ? '#8b949e' : '#656d76'};
      }
      .export-content hr {
        border: none;
        border-top: 1px solid ${theme === 'dark' ? '#30363d' : '#d0d7de'};
        margin: 2em 0;
      }
      .export-content table {
        border-collapse: collapse;
        width: 100%;
        margin-bottom: 1em;
      }
      .export-content th, .export-content td {
        border: 1px solid ${theme === 'dark' ? '#30363d' : '#d0d7de'};
        padding: 8px 12px;
        text-align: left;
      }
      .export-content th {
        background: ${theme === 'dark' ? '#161b22' : '#f6f8fa'};
        font-weight: 600;
      }
      .export-content a {
        color: ${theme === 'dark' ? '#58a6ff' : '#0969da'};
        text-decoration: none;
      }
      .export-content strong { font-weight: 600; }
      .export-content em { font-style: italic; }
    </style>
  `;

  // Convert markdown to HTML using marked
  const htmlContent = await marked(markdown);

  div.innerHTML = styles + htmlContent;
  return div;
}

/**
 * Export chat messages to markdown format.
 */
export function exportChatToMarkdown(
  messages: Array<{ role: string; content: string; metadata?: any }>,
  options: {
    repoName?: string;
    includeCodeBlocks?: boolean;
  } = {}
): string {
  let markdown = '';

  // Add header
  markdown += `# Chat Export\n\n`;
  if (options.repoName) {
    markdown += `**Repository:** ${options.repoName}\n`;
  }
  markdown += `**Date:** ${new Date().toLocaleString()}\n\n`;
  markdown += `---\n\n`;

  // Process each message
  for (const msg of messages) {
    const isUser = msg.role === 'user';
    markdown += `## ${isUser ? '👤 User' : '🤖 Assistant'}\n\n`;
    markdown += `${msg.content}\n\n`;

    // Add code blocks if available and requested
    if (options.includeCodeBlocks && msg.metadata?.code_blocks) {
      for (const block of msg.metadata.code_blocks) {
        markdown += `<details>\n`;
        markdown += `<summary>${block.file} (lines ${block.startLine}-${block.endLine})</summary>\n\n`;
        markdown += `\`\`\`${block.language}\n${block.code}\n\`\`\`\n`;
        markdown += `</details>\n\n`;
      }
    }

    markdown += `---\n\n`;
  }

  return markdown;
}

// ===== Enhanced Export Functions =====

/**
 * Detect programming language from file extension
 */
function detectLanguage(filePath: string): string {
  const ext = filePath.split('.').pop()?.toLowerCase() || '';
  const languageMap: Record<string, string> = {
    'py': 'python',
    'js': 'javascript',
    'ts': 'typescript',
    'tsx': 'tsx',
    'jsx': 'jsx',
    'java': 'java',
    'cpp': 'cpp',
    'c': 'c',
    'h': 'c',
    'hpp': 'cpp',
    'rs': 'rust',
    'go': 'go',
    'rb': 'ruby',
    'php': 'php',
    'swift': 'swift',
    'kt': 'kotlin',
    'scala': 'scala',
    'sh': 'bash',
    'bash': 'bash',
    'zsh': 'bash',
    'yaml': 'yaml',
    'yml': 'yaml',
    'json': 'json',
    'xml': 'xml',
    'html': 'html',
    'css': 'css',
    'scss': 'scss',
    'md': 'markdown',
  };
  return languageMap[ext] || 'text';
}

/**
 * Generate hash code for block ID
 */
function hashCode(str: string): string {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = ((hash << 5) - hash) + char;
    hash = hash & hash;
  }
  return Math.abs(hash).toString(16).padStart(12, '0').slice(0, 12);
}

/**
 * Batch fetch all code blocks for node references (PARALLEL VERSION)
 * - First checks existing references for embedded code
 * - Then fetches from API for nodes without code IN PARALLEL
 * - Returns all fetched blocks (including errors)
 */
export async function fetchAllCodeBlocks(
  repoName: string,
  nodeRefs: string[],
  existingRefs?: ReferenceData[],
  onProgress?: (status: string) => void
): Promise<FetchedCodeBlock[]> {
  const results: FetchedCodeBlock[] = [];
  const seenQualifiedNames = new Set<string>();

  // Helper: Try to find a reference by various matching strategies
  const findReference = (nodeName: string) => {
    if (!existingRefs) return null;

    // Strategy 1: Exact identifier match
    let ref = existingRefs.find(r => r.ref === nodeName);
    if (ref) return ref;

    // Strategy 2: Exact name match
    ref = existingRefs.find(r => r.name === nodeName);
    if (ref) return ref;

    // Strategy 3: Endswith match (for qualified names)
    ref = existingRefs.find(r => nodeName.endsWith('.' + r.name) || nodeName === r.name);
    if (ref) return ref;

    // Strategy 4: Last part match
    ref = existingRefs.find(r => {
      const parts = r.name?.split('.') || [];
      const lastPart = parts[parts.length - 1];
      return lastPart === nodeName;
    });
    return ref;
  };

  // Step 1: Process nodes with embedded code (no API call needed)
  const nodesToFetch: string[] = [];

  for (const nodeName of nodeRefs) {
    // Skip duplicates
    if (seenQualifiedNames.has(nodeName)) {
      console.log(`⚠️ Skipping duplicate node: ${nodeName}`);
      continue;
    }

    // Check for embedded code
    const existingRef = findReference(nodeName);
    if (existingRef && (existingRef as any).code) {
      const refData = existingRef as any;
      const blockId = `block-${hashCode(`${refData.file}:${refData.startLine}-${refData.endLine}`)}`;

      results.push({
        id: blockId,
        nodeName: nodeName,
        file: refData.file,
        startLine: refData.startLine,
        endLine: refData.endLine,
        code: refData.code,
        language: refData.language || detectLanguage(refData.file),
        nodeType: refData.nodeType,
        qualifiedName: refData.qualified_name || refData.name || nodeName,
      });
      seenQualifiedNames.add(nodeName);
    } else {
      // Need to fetch from API
      nodesToFetch.push(nodeName);
    }
  }

  // Step 2: Batch fetch remaining nodes (grouped by repo)
  if (nodesToFetch.length > 0) {
    onProgress?.(`Fetching ${nodesToFetch.length} code blocks...`);

    // Group nodes by target repo
    const byRepo = new Map<string, string[]>();
    for (const nodeName of nodesToFetch) {
      const repoNameFromNode = nodeName.split('.')[0];
      const targetRepo = repoNameFromNode || repoName;
      if (!byRepo.has(targetRepo)) byRepo.set(targetRepo, []);
      byRepo.get(targetRepo)!.push(nodeName);
    }

    // One batch request per repo (usually just 1 repo)
    const batchPromises = Array.from(byRepo.entries()).map(async ([targetRepo, names]) => {
      try {
        const response = await fetch(`/api/graph/node/${encodeURIComponent(targetRepo)}/batch-code`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ qualified_names: names }),
        });

        if (response.ok) {
          const items: any[] = await response.json();
          return items.map((data: any) => {
            if (data.error) {
              return {
                id: `error-${hashCode(data.qualified_name)}`,
                nodeName: data.qualified_name,
                file: '',
                startLine: 0,
                endLine: 0,
                code: '',
                language: 'text',
                fetchError: data.error,
              };
            }
            const blockId = `block-${hashCode(`${data.file}:${data.start_line}-${data.end_line}`)}`;
            return {
              id: blockId,
              nodeName: data.qualified_name,
              file: data.file,
              startLine: data.start_line,
              endLine: data.end_line,
              code: data.code,
              language: data.language || detectLanguage(data.file),
              nodeType: data.node_type,
              qualifiedName: data.qualified_name || data.name,
            };
          });
        } else {
          console.warn(`Batch fetch failed for repo ${targetRepo}: HTTP ${response.status}`);
          return names.map(n => ({
            id: `error-${hashCode(n)}`,
            nodeName: n,
            file: '',
            startLine: 0,
            endLine: 0,
            code: '',
            language: 'text',
            fetchError: `Batch fetch failed (HTTP ${response.status})`,
          }));
        }
      } catch (error) {
        console.error(`Batch fetch error for repo ${targetRepo}:`, error);
        return names.map(n => ({
          id: `error-${hashCode(n)}`,
          nodeName: n,
          file: '',
          startLine: 0,
          endLine: 0,
          code: '',
          language: 'text',
          fetchError: error instanceof Error ? error.message : 'Unknown error',
        }));
      }
    });

    const batchResults = await Promise.all(batchPromises);
    for (const batch of batchResults) {
      results.push(...batch);
    }
  }

  return results;
}

/**
 * Generate code appendix markdown
 */
export function generateCodeAppendix(
  codeBlocks: FetchedCodeBlock[],
  options?: { includeLineNumbers?: boolean; collapsible?: boolean }
): string {
  if (codeBlocks.length === 0) {
    return '';
  }

  const collapsible = options?.collapsible !== false; // Default to true
  let appendix = '\n\n---\n\n## Code References\n\n';

  for (const block of codeBlocks) {
    const anchorId = generateCodeAnchorId(block.nodeName);
    const backRefId = generateBackRefAnchorId(block.nodeName);
    const backLink = `[↩ Back](#${backRefId})`;

    if (block.fetchError) {
      // Error entry
      appendix += `### <a id="${anchorId}"></a>${block.nodeName} ${backLink}\n\n`;
      appendix += `**⚠️ Code fetch failed:** ${block.fetchError}\n\n`;
    } else {
      const codeContent = `\`\`\`${block.language}\n${block.code}\n\`\`\``;

      if (collapsible) {
        // Collapsible format
        const summary = `${block.nodeName} - \`${block.file}\` (lines ${block.startLine}-${block.endLine})`;
        appendix += `<details>\n`;
        appendix += `<summary><a id="${anchorId}"></a>${summary}</summary>\n\n`;
        appendix += `${backLink}\n\n`;
        if (block.nodeType) {
          appendix += `**Type:** ${block.nodeType}\n`;
        }
        if (block.qualifiedName && block.qualifiedName !== block.nodeName) {
          appendix += `**Qualified Name:** \`${block.qualifiedName}\`\n`;
        }
        appendix += '\n';
        appendix += `${codeContent}\n\n`;
        appendix += `</details>\n\n`;
      } else {
        // Non-collapsible format with back link in header
        appendix += `### <a id="${anchorId}"></a>${block.nodeName} ${backLink}\n\n`;
        if (block.nodeType) {
          appendix += `**Type:** ${block.nodeType}\n`;
        }
        appendix += `**File:** \`${block.file}\` (lines ${block.startLine}-${block.endLine})\n`;
        if (block.qualifiedName && block.qualifiedName !== block.nodeName) {
          appendix += `**Qualified Name:** \`${block.qualifiedName}\`\n`;
        }
        appendix += '\n';
        appendix += `${codeContent}\n\n`;
      }
    }
  }

  return appendix;
}

/**
 * Enhanced export function - automatically fetches all code and generates complete document
 *
 * This function:
 * 1. Extracts all [[NodeName]] references from markdown
 * 2. Batch fetches all referenced code blocks
 * 3. If inlineCode=true: Embeds code inline with collapsible details
 * 4. If inlineCode=false: Converts [[NodeName]] to clickable anchor links and appends code at end
 */
export async function exportWithAllCode(
  options: ExportWithAllCodeOptions
): Promise<string> {
  const {
    title,
    markdown,
    repoName,
    references = [],
    codeBlocks = [],
    metadata = {},
    onProgress,
    inlineCode = false,
    collapsibleCode = true,
  } = options;

  onProgress?.('Extracting node references...');

  // Extract all node references
  const nodeRefs = extractNodeReferences(markdown);

  if (nodeRefs.length === 0) {
    onProgress?.('No node references found, using standard export...');
    // No node references, use standard enrichment
    return enrichMarkdownWithCode({
      title,
      markdown,
      codeBlocks,
      references,
      metadata: {
        ...metadata,
        repoName,
        timestamp: metadata.timestamp || new Date().toLocaleString(),
      },
    });
  }

  onProgress?.(`Found ${nodeRefs.length} references, fetching code...`);

  // Fetch all code blocks
  const fetchedBlocks = await fetchAllCodeBlocks(
    repoName,
    nodeRefs,
    references,
    (msg) => onProgress?.(msg)
  );

  onProgress?.('Generating export...');

  // Build the enhanced markdown
  let result = '';

  // Add title and metadata header
  if (title) {
    result += `# ${title}\n\n`;
  }

  result += `> Repository: ${repoName}\n`;
  result += `> Generated: ${new Date().toLocaleString()}\n`;
  if (metadata.operator) {
    result += `> Operator: ${metadata.operator}\n`;
  }

  result += '\n---\n\n';

  // Create a map of node names to code blocks for quick lookup
  const codeBlocksMap = new Map<string, FetchedCodeBlock>();
  for (const block of fetchedBlocks) {
    codeBlocksMap.set(block.nodeName, block);
  }

  if (inlineCode) {
    // Inline mode: Replace [[NodeName]] with collapsible code blocks (collapsed for readability)
    result += convertNodeLinksToInlineCode(markdown, codeBlocksMap, collapsibleCode);

    // Add any unfetched blocks at the end as appendix
    const appendedBlocks = fetchedBlocks.filter(block => {
      // Check if this block was referenced in the original markdown
      return !nodeRefs.includes(block.nodeName);
    });

    if (appendedBlocks.length > 0) {
      result += generateCodeAppendix(appendedBlocks, { collapsible: collapsibleCode });
    }
  } else {
    // Appendix mode: Convert [[NodeName]] to anchor links
    const convertedMarkdown = convertNodeLinksToAnchors(markdown);
    result += convertedMarkdown;

    // Add code appendix - NOT collapsible so users can see code directly when clicking links
    result += generateCodeAppendix(fetchedBlocks, { collapsible: false });
  }

  return result;
}

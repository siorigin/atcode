'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState, useRef, useCallback, useMemo } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { WikiDoc } from '@/components/WikiDoc';
import { useTheme } from '@/lib/theme-context';
import { CodeBlock } from '@/lib/store';
import { getThemeColors } from '@/lib/theme-colors';
import { enrichMarkdownWithCode, downloadMarkdown, downloadPDF, downloadRenderedPDF, createExportableHTML, exportWithAllCode, extractNodeReferences, fetchAllCodeBlocks, type CodeBlockData } from '@/lib/export-utils';

// Helper function to fetch code blocks from references
async function fetchCodeBlocksFromReferences(
  references: Array<{
    qualified_name: string;
    path?: string | null;
    file?: string | null;
    start_line?: number | null;
    end_line?: number | null;
    startLine?: number | null;
    endLine?: number | null;
    repo_name?: string;
  }>,
  defaultRepo: string
): Promise<Array<{
  id: string;
  file: string;
  startLine: number;
  endLine: number;
  code: string;
  language: string;
  qualified_name?: string;
  repo_name?: string;
}>> {
  const codeBlocks = [];
  const seenIds = new Set<string>(); // Track IDs to prevent duplicates
  
  for (const ref of references) {
    const filePath = ref.path || ref.file;
    const startLine = ref.start_line ?? ref.startLine ?? null;
    const endLine = ref.end_line ?? ref.endLine ?? null;

    if (!filePath || startLine == null || endLine == null) {
      console.warn('Skipping reference without path/lines:', ref.qualified_name);
      continue;
    }
    
    try {
      // Determine target repo (use repo_name from reference if available, otherwise default)
      const targetRepo = ref.repo_name || defaultRepo;
      
      // Generate stable ID based on repo, file path, line range, AND qualified name
      // Include qualified_name to ensure uniqueness when multiple references point to same location
      const blockId = `block-${targetRepo}-${filePath.replace(/[^a-zA-Z0-9]/g, '-')}-${startLine}-${endLine}-${ref.qualified_name.replace(/[^a-zA-Z0-9]/g, '-')}`;
      
      // Skip if we've already processed this ID
      if (seenIds.has(blockId)) {
        console.warn('⚠️ Skipping duplicate block ID:', blockId);
        continue;
      }
      seenIds.add(blockId);
      
      // Fetch code from API
      const response = await fetch(`/api/code`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          repo: targetRepo,
          path: filePath,
          startLine,
          endLine,
        })
      });
      
      if (!response.ok) {
        console.warn(`Failed to fetch code for ${ref.qualified_name}:`, response.statusText);
        continue;
      }
      
      const code = await response.text();
      
      // Detect language from file extension
      const language = detectLanguage(filePath);
      
      codeBlocks.push({
        id: blockId,
        file: filePath,
        startLine,
        endLine,
        code: code,
        language: language,
        qualified_name: ref.qualified_name,
        repo_name: targetRepo
      });
      
      console.log(`✅ Fetched code block for ${ref.qualified_name} from ${targetRepo}`);
    } catch (error) {
      console.error(`Error fetching code for ${ref.qualified_name}:`, error);
    }
  }
  
  return codeBlocks;
}

// Helper function to detect language from file path
function detectLanguage(filePath: string): string {
  const ext = filePath.split('.').pop()?.toLowerCase();
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
  return languageMap[ext || ''] || 'text';
}

interface DocData {
  id: string;
  markdown: string;
  codeBlocks: Array<{
    id: string;
    file: string;
    startLine: number;
    endLine: number;
    code: string;
    language: string;
  }>;
  references: Array<{
    identifier: string;
    qualified_name: string;
    name: string;
    type: string;
    file: string | null;  // Changed from 'path' to 'file' to match WikiDoc component expectations
    start_line: number | null;
    end_line: number | null;
    ref: string;
  }>;
  repo_name?: string;
  operator_name?: string;
}

export default function OperatorDocPage() {
  const params = useParams();
  const router = useRouter();
  const repoName = params.repo as string;
  const operatorName = params.operator as string;

  // Decode URL-encoded operator name for display
  const decodedOperatorName = useMemo(() => {
    try {
      return decodeURIComponent(operatorName);
    } catch {
      return operatorName;
    }
  }, [operatorName]);

  const { theme } = useTheme();
  const colors = getThemeColors(theme);
  // Manage code blocks with local state, isolated from other pages
  const [operatorCodeBlocks, setOperatorCodeBlocks] = useState<CodeBlock[]>([]);
  const [docData, setDocData] = useState<DocData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exportDropdownOpen, setExportDropdownOpen] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [exportMode, setExportMode] = useState<'appendix' | 'inline'>('appendix');
  const exportDropdownRef = useRef<HTMLDivElement>(null);
  const docExportRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    async function loadDoc() {
      try {
        console.log(`Loading documentation for ${repoName}/${operatorName}`);

        // Load from hierarchical API
        const response = await fetch(`/api/repos/${repoName}/${operatorName}`);

        if (!response.ok) {
          throw new Error(`Failed to load documentation: ${response.status} - ${response.statusText}`);
        }

        const data = await response.json();
        console.log('Documentation loaded successfully');
        console.log('📊 Data structure:', {
          hasCodeBlocks: !!data.codeBlocks,
          codeBlocksCount: data.codeBlocks?.length || 0,
          hasReferences: !!data.references,
          referencesCount: data.references?.length || 0,
          referencesWithPath: data.references?.filter((r: any) => (r.path || r.file)).length || 0
        });

        // Preload all [[link]] code blocks to avoid re-rendering when clicking links
        let enrichedData = data;
        if (data.markdown) {
          try {
            // Extract all [[NodeName]] references from markdown
            const nodeRefs = extractNodeReferences(data.markdown);
            console.log(`🔗 Found ${nodeRefs.length} [[link]] references in markdown:`, nodeRefs);

            if (nodeRefs.length > 0) {
              // Batch fetch all code blocks
              const fetchedBlocks = await fetchAllCodeBlocks(
                repoName,
                nodeRefs,
                data.references || [],  // Pass empty array if no references
                (msg) => console.log('📦 Preloading:', msg)
              );
              console.log(`✅ Preloaded ${fetchedBlocks.length} code blocks`);

              // Create synthetic references from fetched blocks if no references exist
              if (!data.references || data.references.length === 0) {
                const syntheticReferences = fetchedBlocks
                  .filter(b => !b.fetchError && b.code)
                  .map(b => ({
                    identifier: b.nodeName,
                    qualified_name: b.qualifiedName || b.nodeName,
                    name: b.nodeName,
                    type: b.nodeType || 'Unknown',
                    file: b.file,
                    start_line: b.startLine,
                    end_line: b.endLine,
                    ref: b.nodeName,
                    code: b.code,
                    language: b.language
                  }));
                enrichedData = { ...data, references: syntheticReferences };
                console.log(`✅ Created ${syntheticReferences.length} synthetic references with code`);
              } else {
                // Enrich existing references with fetched code
                const enrichedReferences = data.references.map((ref: any) => {
                  // Find matching block by various name strategies
                  const block = fetchedBlocks.find(b =>
                    b.nodeName === ref.identifier ||
                    b.nodeName === ref.qualified_name ||
                    b.nodeName === ref.name ||
                    ref.identifier === b.nodeName ||
                    ref.qualified_name?.endsWith('.' + b.nodeName) ||
                    b.nodeName?.endsWith('.' + ref.name)
                  );
                  if (block && block.code && !block.fetchError) {
                    return {
                      ...ref,
                      code: block.code,
                      language: block.language
                    };
                  }
                  return ref;
                });

                enrichedData = { ...data, references: enrichedReferences };
                console.log(`✅ Enriched ${enrichedReferences.filter((r: any) => r.code).length} references with code`);
              }
            }
          } catch (preloadError) {
            console.warn('⚠️ Failed to preload code blocks, will fetch on demand:', preloadError);
            // Continue with original data if preloading fails
          }
        }

        setDocData(enrichedData);

        // Handle both old format (with codeBlocks) and new format (only references)
        if (enrichedData.codeBlocks && enrichedData.codeBlocks.length > 0) {
          // Old format: use codeBlocks directly
          console.log('📦 Using existing codeBlocks from data');
          setOperatorCodeBlocks(enrichedData.codeBlocks);
          // Auto-activate first code block
        } else if (enrichedData.references && enrichedData.references.length > 0) {
          // Check if references already have embedded code (from preloading)
          const referencesWithCode = enrichedData.references.filter((r: any) => r.code);

          if (referencesWithCode.length > 0) {
            // Use embedded code directly, no need to fetch again
            console.log(`📦 Using ${referencesWithCode.length} preloaded code blocks from references`);
            const codeBlocks = referencesWithCode
              .filter((ref: any) => (ref.path || ref.file) && ref.qualified_name)  // Filter out refs without path or qualified_name
              .map((ref: any) => {
              const filePath = ref.path || ref.file;
              const startLine = ref.start_line ?? ref.startLine;
              const endLine = ref.end_line ?? ref.endLine;
              const blockId = `block-${repoName}-${filePath.replace(/[^a-zA-Z0-9]/g, '-')}-${startLine}-${endLine}-${ref.qualified_name.replace(/[^a-zA-Z0-9]/g, '-')}`;

              return {
                id: blockId,
                file: filePath,
                startLine: startLine,
                endLine: endLine,
                code: ref.code,
                language: ref.language || detectLanguage(filePath),
                qualified_name: ref.qualified_name,
                repo_name: repoName
              };
            });
            setOperatorCodeBlocks(codeBlocks);
          } else {
            // Fallback: fetch code blocks from references dynamically
            const referencesWithPath = enrichedData.references.filter((r: any) =>
              (r.path || r.file) &&
              (r.start_line != null || r.startLine != null) &&
              (r.end_line != null || r.endLine != null)
            );
            console.log(`🔍 Fetching code blocks from ${referencesWithPath.length}/${enrichedData.references.length} references (${enrichedData.references.length - referencesWithPath.length} references have no source location)...`);
            const codeBlocks = await fetchCodeBlocksFromReferences(referencesWithPath, repoName);
            console.log('✅ Fetched', codeBlocks.length, 'code blocks');
            setOperatorCodeBlocks(codeBlocks);
          }
        } else {
          // No code blocks or references
          console.log('⚠️ No code blocks or references found');
          setOperatorCodeBlocks([]);
        }
      } catch (err) {
        console.error('Error loading documentation:', err);
        setError(err instanceof Error ? err.message : 'Failed to load documentation');
      } finally {
        setLoading(false);
      }
    }

    if (repoName && operatorName) {
      loadDoc();
    }
  }, [repoName, operatorName]);

  // Callback: activate code block
  // 🔧 回调函数：添加代码块
  const handleAddCodeBlock = useCallback((block: CodeBlock) => {
    // First, check if block already exists by ID or by file+lines combination
    setOperatorCodeBlocks(prev => {
      const exists = prev.some(b => b.id === block.id);
      if (exists) return prev;
      return [...prev, block];
    });
  }, []);

  // Close export dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (exportDropdownRef.current && !exportDropdownRef.current.contains(event.target as Node)) {
        setExportDropdownOpen(false);
      }
    };
    if (exportDropdownOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [exportDropdownOpen]);

  // Export current document as markdown
  const handleExportMarkdown = useCallback(async () => {
    if (!docData) return;

    setExportDropdownOpen(false);
    setIsExporting(true);

    try {
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
      const filename = `${repoName}-${decodedOperatorName}-${timestamp}`;

      // Convert references to export format with embedded code
      const referencesForExport = docData.references?.map(ref => ({
        name: ref.name || ref.identifier || ref.qualified_name || '',
        file: ref.file || '',
        startLine: ref.start_line || 0,
        endLine: ref.end_line || 0,
        ref: ref.identifier || ref.qualified_name || ref.name || '',
        nodeType: ref.type,
        code: (ref as any).code,
        language: (ref as any).language,
        qualified_name: ref.qualified_name,
      })) || [];

      // Use enhanced export with automatic code fetching
      const enriched = await exportWithAllCode({
        title: `${repoName} / ${decodedOperatorName}`,
        markdown: docData.markdown,
        repoName,
        references: referencesForExport,
        codeBlocks: operatorCodeBlocks.map(block => ({
          id: block.id,
          file: block.file,
          startLine: block.startLine,
          endLine: block.endLine,
          code: block.code,
          language: block.language,
        })),
        metadata: {
          repoName,
          timestamp: new Date().toLocaleString(),
          operator: decodedOperatorName,
        },
        onProgress: (status) => console.log('Export progress:', status),
        inlineCode: exportMode === 'inline',
        collapsibleCode: true,
      });

      downloadMarkdown(enriched, filename);
    } finally {
      setIsExporting(false);
    }
  }, [docData, operatorCodeBlocks, repoName, operatorName, exportMode]);

  // Export current document as PDF
  const handleExportPDF = useCallback(async () => {
    if (!docData || isExporting) return;

    setExportDropdownOpen(false);
    setIsExporting(true);

    try {
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
      const filename = `${repoName}-${decodedOperatorName}-${timestamp}`;

      // Convert references to export format with embedded code
      const referencesForExport = docData.references?.map(ref => ({
        name: ref.name || ref.identifier || ref.qualified_name || '',
        file: ref.file || '',
        startLine: ref.start_line || 0,
        endLine: ref.end_line || 0,
        ref: ref.identifier || ref.qualified_name || ref.name || '',
        nodeType: ref.type,
        code: (ref as any).code,
        language: (ref as any).language,
        qualified_name: ref.qualified_name,
      })) || [];

      // Use enhanced export with automatic code fetching for fallback
      const enriched = await exportWithAllCode({
        title: `${repoName} / ${decodedOperatorName}`,
        markdown: docData.markdown,
        repoName,
        references: referencesForExport,
        codeBlocks: operatorCodeBlocks.map(block => ({
          id: block.id,
          file: block.file,
          startLine: block.startLine,
          endLine: block.endLine,
          code: block.code,
          language: block.language,
        })),
        metadata: {
          repoName,
          timestamp: new Date().toLocaleString(),
          operator: decodedOperatorName,
        },
        onProgress: (status) => console.log('Export progress:', status),
        inlineCode: exportMode === 'inline',
        collapsibleCode: true,
      });

      // Prefer exporting the already-rendered document DOM for best fidelity.
      if (docExportRef.current) {
        await downloadRenderedPDF(docExportRef.current, filename, { paddingPx: 24 });
      } else {
        // Fallback: export from enriched markdown if DOM ref is missing
        const tempDiv = await createExportableHTML(enriched, theme);
        document.body.appendChild(tempDiv);
        await downloadPDF(tempDiv, filename);
        document.body.removeChild(tempDiv);
      }
    } catch (error) {
      console.error('Failed to export PDF:', error);
    } finally {
      setIsExporting(false);
    }
  }, [docData, operatorCodeBlocks, repoName, operatorName, theme, isExporting, exportMode]);

  if (loading) {
    return (
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100%',
        background: colors.bg
      }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{
            width: '48px',
            height: '48px',
            border: `3px solid ${colors.border}`,
            borderTopColor: colors.accent,
            borderRadius: '50%',
            animation: 'spin 0.8s linear infinite',
            margin: '0 auto 16px'
          }} />
          <p style={{ color: colors.textMuted }}>Loading documentation...</p>
        </div>
      </div>
    );
  }

  if (error || !docData) {
    return (
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100%',
        background: colors.bg
      }}>
        <div style={{ textAlign: 'center', maxWidth: '400px', margin: '0 auto', padding: '24px' }}>
          <div style={{ fontSize: '64px', marginBottom: '16px' }}>📄</div>
          <h1 style={{ fontSize: '24px', fontWeight: '600', color: colors.text, marginBottom: '12px' }}>
            Documentation Not Found
          </h1>
          <p style={{ color: colors.textMuted, marginBottom: '24px' }}>
            {error || 'The requested documentation could not be found.'}
          </p>
          <div style={{ display: 'flex', gap: '12px', justifyContent: 'center' }}>
            <button
              onClick={() => router.push(`/repos/${repoName}`)}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '8px',
                background: colors.buttonPrimaryBg,
                color: '#ffffff',
                padding: '12px 24px',
                borderRadius: '8px',
                border: 'none',
                fontWeight: '500',
                cursor: 'pointer',
                transition: 'all 0.2s'
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = colors.buttonPrimaryHover;
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = colors.buttonPrimaryBg;
              }}
            >
              <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
              </svg>
              <span>Back to Operators</span>
            </button>
            <button
              onClick={() => router.push('/repos')}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '8px',
                background: colors.buttonSecondaryBg,
                color: colors.text,
                padding: '12px 24px',
                borderRadius: '8px',
                border: `1px solid ${colors.border}`,
                fontWeight: '500',
                cursor: 'pointer',
                transition: 'all 0.2s'
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = colors.buttonSecondaryHover;
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = colors.buttonSecondaryBg;
              }}
            >
              <span>All Repos</span>
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={{
      maxWidth: 'var(--max-width)',
      margin: '0 auto',
      height: '100%',
      overflow: 'auto',
      paddingBottom: '40px',
      background: colors.bg,
      color: colors.text
    }}>
      {/* Operator title bar with export */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '12px 24px',
        borderBottom: `1px solid ${colors.border}`,
        flexShrink: 0,
      }}>
        <span style={{
          fontSize: '13px',
          color: colors.textMuted,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}>
          {decodeURIComponent(repoName)} / {decodedOperatorName}
        </span>
        {/* Export Button */}
        <div ref={exportDropdownRef} style={{ position: 'relative' }}>
          <button
            onClick={() => setExportDropdownOpen(!exportDropdownOpen)}
            disabled={!docData || isExporting}
            title="Export document"
            style={{
              padding: '6px 12px',
              background: colors.buttonSecondaryBg,
              border: `1px solid ${colors.border}`,
              borderRadius: '6px',
              fontSize: '13px',
              fontWeight: '500',
              cursor: !docData || isExporting ? 'not-allowed' : 'pointer',
              transition: 'all 0.2s',
              color: !docData ? colors.textMuted : colors.text,
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              opacity: !docData ? 0.5 : 1,
            }}
            onMouseEnter={(e) => {
              if (docData && !isExporting) {
                e.currentTarget.style.background = colors.buttonSecondaryHover;
              }
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = colors.buttonSecondaryBg;
            }}
          >
            {isExporting ? (
              <span style={{
                width: '14px',
                height: '14px',
                border: `2px solid ${colors.border}`,
                borderTopColor: colors.accent,
                borderRadius: '50%',
                animation: 'spin 0.8s linear infinite',
              }} />
            ) : (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                <polyline points="7,10 12,15 17,10"/>
                <line x1="12" y1="15" x2="12" y2="3"/>
              </svg>
            )}
            <span>Export</span>
          </button>
          {exportDropdownOpen && (
            <div
              style={{
                position: 'absolute',
                top: '100%',
                right: 0,
                marginTop: '4px',
                background: colors.card,
                border: `1px solid ${colors.border}`,
                borderRadius: '8px',
                boxShadow: `0 4px 12px ${colors.shadowColor}`,
                minWidth: '180px',
                zIndex: 300,
                overflow: 'hidden',
              }}
            >
              {/* Code Mode Selection */}
              <div style={{ padding: '4px 8px', fontSize: '11px', color: colors.textMuted, fontWeight: 500 }}>
                Code Placement
              </div>
              <button
                onClick={() => setExportMode('appendix')}
                style={{
                  width: '100%',
                  padding: '8px 10px',
                  background: exportMode === 'appendix' ? colors.accentBg : 'transparent',
                  border: 'none',
                  borderRadius: '6px',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  fontSize: '12px',
                  color: exportMode === 'appendix' ? colors.accent : colors.text,
                  transition: 'all 150ms ease-out',
                }}
                onMouseEnter={(e) => { if (exportMode !== 'appendix') e.currentTarget.style.background = colors.bgHover; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = exportMode === 'appendix' ? colors.accentBg : 'transparent'; }}
              >
                <span style={{ fontSize: '14px' }}>📋</span>
                <span>At end (appendix)</span>
                {exportMode === 'appendix' && (
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={colors.accent} strokeWidth="2.5" style={{ marginLeft: 'auto' }}>
                    <polyline points="20,6 9,17 4,12"/>
                  </svg>
                )}
              </button>
              <button
                onClick={() => setExportMode('inline')}
                style={{
                  width: '100%',
                  padding: '8px 10px',
                  background: exportMode === 'inline' ? colors.accentBg : 'transparent',
                  border: 'none',
                  borderRadius: '6px',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  fontSize: '12px',
                  color: exportMode === 'inline' ? colors.accent : colors.text,
                  transition: 'all 150ms ease-out',
                }}
                onMouseEnter={(e) => { if (exportMode !== 'inline') e.currentTarget.style.background = colors.bgHover; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = exportMode === 'inline' ? colors.accentBg : 'transparent'; }}
              >
                <span style={{ fontSize: '14px' }}>📝</span>
                <span>Inline in doc</span>
                {exportMode === 'inline' && (
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={colors.accent} strokeWidth="2.5" style={{ marginLeft: 'auto' }}>
                    <polyline points="20,6 9,17 4,12"/>
                  </svg>
                )}
              </button>
              {/* Divider */}
              <div style={{ height: '1px', background: colors.borderLight, margin: '4px 8px' }} />
              {/* Export Options */}
              <button
                onClick={handleExportMarkdown}
                style={{
                  width: '100%',
                  padding: '10px 14px',
                  background: 'transparent',
                  border: 'none',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  fontSize: '13px',
                  color: colors.text,
                  transition: 'background 0.15s',
                }}
                onMouseEnter={(e) => { e.currentTarget.style.background = colors.bgHover; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                  <polyline points="14,2 14,8 20,8"/>
                  <line x1="16" y1="13" x2="8" y2="13"/>
                  <line x1="16" y1="17" x2="8" y2="17"/>
                  <polyline points="10,9 9,9 8,9"/>
                </svg>
                <span>Markdown (.md)</span>
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Main content — full width, code opens in floating panel */}
      <main style={{ width: '100%' }}>
        <div ref={docExportRef}>
          <WikiDoc
            markdown={docData.markdown}
            codeBlocks={operatorCodeBlocks}
            references={docData.references}
            onAddCodeBlock={handleAddCodeBlock}
            repoName={repoName}
          />
        </div>
      </main>

      <style jsx>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}


'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import { WikiDoc } from '@/components/WikiDoc';
import { AppHeader } from '@/components/AppHeader';

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
  exploredNodes?: Array<{
    qualified_name: string;
    type: string;
    path: string;
    start_line?: number;
    end_line?: number;
    has_code: boolean;
    has_docstring: boolean;
    tool_used: string;
    timestamp?: string;
  }>;
  query: string;
  metadata: {
    code_blocks_count: number;
    references_count: number;
    total_lines: number;
    files_referenced: string[];
    explored_nodes_count?: number;
  };
}

export default function DocPage() {
  const params = useParams();
  const slug = params.slug as string;
  const [docData, setDocData] = useState<DocData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Use local state for Doc page codeBlocks (for export)
  const [docCodeBlocks, setDocCodeBlocks] = useState<DocData['codeBlocks']>([]);

  // Extract repo name from references (first qualified_name segment)
  const docRepoName = docData?.references?.[0]?.qualified_name?.split('.')[0] || '';

  useEffect(() => {
    async function loadDoc() {
      try {
        let docId = slug;
        let shouldRedirect = false;

        // Handle different slug formats:
        // 1. Just the doc ID (e.g., "c3f1fd8499e7") - preferred format
        // 2. Slug with doc ID at the end (e.g., "some-query-c3f1fd8499e7") - redirect to simple format

        if (slug.includes('-')) {
          const parts = slug.split('-');
          const potentialDocId = parts[parts.length - 1];

          // Check if the last part looks like a doc ID (12 hex chars)
          if (potentialDocId.length === 12 && /^[a-f0-9]+$/.test(potentialDocId)) {
            if (slug !== potentialDocId) {
              // This is a complex slug, redirect to simple format
              shouldRedirect = true;
              docId = potentialDocId;
            }
          }
        }

        // Redirect to simple URL format
        if (shouldRedirect && typeof window !== 'undefined') {
          const simpleUrl = `/docs/${docId}`;
          console.log('Redirecting to simple URL:', simpleUrl);
          window.location.href = simpleUrl;
          return;
        }

        // Validate doc ID format
        if (!docId || docId.length !== 12 || !/^[a-f0-9]+$/.test(docId)) {
          setError(`Invalid documentation ID: ${docId}. Expected 12 hexadecimal characters.`);
          setLoading(false);
          return;
        }

        console.log('Loading documentation for ID:', docId);

        // Load documentation data from API endpoint (with fallback to static file)
        let response;
        try {
          // Try API endpoint first
          response = await fetch(`/api/docs/${docId}`);
        } catch (apiError) {
          console.log('API endpoint failed, trying static file:', apiError);
          // Fallback to static file
          response = await fetch(`/${docId}.json`);
        }

        if (!response.ok) {
          if (response.status === 404) {
            throw new Error(`Documentation not found. The file ${docId}.json may not exist.`);
          } else {
            throw new Error(`Failed to load documentation: ${response.status} - ${response.statusText}`);
          }
        }

        const data = await response.json();
        console.log('Documentation loaded successfully');
        setDocData(data);
        
        // Set local codeBlocks state
        setDocCodeBlocks(data.codeBlocks || []);
      } catch (err) {
        console.error('Error loading documentation:', err);
        setError(err instanceof Error ? err.message : 'Failed to load documentation');
      } finally {
        setLoading(false);
      }
    }

    if (slug) {
      loadDoc();
    }
  }, [slug]);

  // Handle adding code block callback (for export)
  const handleAddCodeBlock = (block: DocData['codeBlocks'][0]) => {
    setDocCodeBlocks(prev => {
      const exists = prev.some(b => b.id === block.id);
      if (exists) return prev;
      return [...prev, block];
    });
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-[var(--background)]">
        <div className="text-center space-y-4">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-[var(--accent-blue)] mx-auto"></div>
          <p className="text-[var(--text-secondary)]">Loading documentation...</p>
        </div>
      </div>
    );
  }

  if (error || !docData) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-[var(--background)]">
        <div className="text-center space-y-4 max-w-md mx-auto">
          <div style={{ width: '64px', height: '64px', margin: '0 auto', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ color: 'var(--text-secondary)' }}>
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
              <polyline points="14 2 14 8 20 8"/>
              <line x1="16" y1="13" x2="8" y2="13"/>
              <line x1="16" y1="17" x2="8" y2="17"/>
            </svg>
          </div>
          <h1 className="text-2xl font-bold text-[var(--text-primary)]">Documentation Not Found</h1>
          <p className="text-[var(--text-secondary)]">
            {error || 'The requested documentation could not be found.'}
          </p>
          <div className="mt-6">
            <a
              href="/"
              className="inline-flex items-center space-x-2 bg-gradient-to-r from-[var(--accent-blue)] to-[var(--accent-purple)] text-white px-6 py-3 rounded-lg font-medium hover:shadow-lg transition-all duration-200"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
              </svg>
              <span>Back to Home</span>
            </a>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full" style={{ minHeight: '100vh' }}>
      <AppHeader
        backHref="/"
        backLabel="Home"
        subtitle={docData.id.slice(0, 8)}
      />
      <main style={{ width: '100%', paddingBottom: '40px' }}>
        <WikiDoc
          markdown={docData.markdown}
          codeBlocks={docCodeBlocks}
          references={docData.references}
          onAddCodeBlock={handleAddCodeBlock}
          repoName={docRepoName}
        />
      </main>
    </div>
  );
}

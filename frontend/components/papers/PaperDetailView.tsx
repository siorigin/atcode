'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';
import { getPaperDoc, getPaperPdfUrl, startPaperRead } from '@/lib/papers-api';
import type { PaperReadingDoc } from '@/lib/papers-api';
import PaperTaskProgress from './PaperTaskProgress';
import { usePaperNotes } from './usePaperNotes';
import { getFastApiUrl } from '@/lib/api-client';

// --- Standardized paper info interface ---

export interface PaperInfo {
  paperId: string;
  title: string;
  abstract?: string;
  authors: string[];
  aiSummary?: string;
  aiKeywords?: string[];
  upvotes?: number;
  organization?: string;
  githubUrls: string[];
  githubStars?: number;
  numComments?: number;
  source?: string;
  citations?: number;
  url?: string;
}

type DetailTab = 'info' | 'document' | 'notes' | 'pdf';

interface PaperDetailViewProps {
  paper: PaperInfo;
  hasDoc: boolean;
  onBack: () => void;
  onSendToChat?: (text: string) => void;
  onPipelineComplete?: () => void;
}

export default function PaperDetailView({
  paper,
  hasDoc: initialHasDoc,
  onBack,
  onSendToChat,
  onPipelineComplete,
}: PaperDetailViewProps) {
  const { theme } = useTheme();
  const colors = getThemeColors(theme);
  const router = useRouter();

  const [activeTab, setActiveTab] = useState<DetailTab>('info');
  const [hasDoc, setHasDoc] = useState(initialHasDoc);

  // Pipeline state
  const [taskId, setTaskId] = useState<string | null>(null);
  const [pipelineStatus, setPipelineStatus] = useState<'idle' | 'running' | 'completed' | 'failed'>('idle');
  const [starting, setStarting] = useState(false);

  // Document state
  const [paperDoc, setPaperDoc] = useState<PaperReadingDoc | null>(null);
  const [loadingDoc, setLoadingDoc] = useState(false);
  const [docError, setDocError] = useState<string | null>(null);

  // Notes
  const { notes, setNotes, saving, clearNotes } = usePaperNotes(paper.paperId);
  const [generatingNotes, setGeneratingNotes] = useState(false);

  // Sync hasDoc from parent
  useEffect(() => {
    setHasDoc(initialHasDoc);
  }, [initialHasDoc]);

  // Load document when Document tab is selected
  useEffect(() => {
    if (activeTab === 'document' && hasDoc && !paperDoc && !loadingDoc) {
      const load = async () => {
        setLoadingDoc(true);
        setDocError(null);
        try {
          const doc = await getPaperDoc(paper.paperId);
          setPaperDoc(doc);
        } catch (e: any) {
          setDocError(e.message || 'Failed to load document');
        } finally {
          setLoadingDoc(false);
        }
      };
      load();
    }
  }, [activeTab, hasDoc, paperDoc, loadingDoc, paper.paperId]);

  const handleStartPipeline = async () => {
    setStarting(true);
    try {
      const result = await startPaperRead({ arxiv_id: paper.paperId, auto_build_repos: true });
      setTaskId(result.task_id);
      setPipelineStatus('running');
    } catch {
      setPipelineStatus('failed');
    } finally {
      setStarting(false);
    }
  };

  const handlePipelineComplete = useCallback(() => {
    setPipelineStatus('completed');
    setHasDoc(true);
    setActiveTab('document');
    onPipelineComplete?.();
  }, [onPipelineComplete]);

  const handleDiscuss = (sectionTitle: string, content: string) => {
    const snippet = content.length > 300 ? content.slice(0, 300) + '...' : content;
    const prompt = `Regarding the section "${sectionTitle}" in the paper "${paper.title}":\n\n> ${snippet}\n\n`;
    onSendToChat?.(prompt);
  };

  const handleGenerateNotes = async () => {
    if (generatingNotes) return;
    setGeneratingNotes(true);

    const sectionTitles = paperDoc?.sections.map((s) => s.title).join(', ') || '';
    const context = `Paper: ${paper.title}\nAbstract: ${paper.abstract || 'N/A'}\nSections: ${sectionTitles}`;
    const prompt = `Generate structured reading notes for this paper. Include key contributions, methodology, results, and your critical analysis.\n\nContext:\n${context}`;

    try {
      const resp = await fetch(`${getFastApiUrl()}/api/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          repo_name: '__papers__',
          message: prompt,
          model: 'claude-haiku-4-5-ssvip',
        }),
      });

      if (!resp.ok || !resp.body) throw new Error('Stream failed');

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let accumulated = '';
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const data = line.slice(6);
          if (data === '[DONE]') continue;
          try {
            const evt = JSON.parse(data);
            if (evt.type === 'token' && evt.content) {
              accumulated += evt.content;
              setNotes(accumulated);
            }
          } catch {}
        }
      }
    } catch (e) {
      console.error('Failed to generate notes:', e);
    } finally {
      setGeneratingNotes(false);
    }
  };

  const getRepoNameFromUrl = (url: string): string | null => {
    const match = url.match(/github\.com\/([^/]+\/[^/]+)/);
    return match ? match[1] : null;
  };

  const arxivUrl = paper.url || `https://arxiv.org/abs/${paper.paperId}`;

  const tabStyle = (tab: DetailTab) => {
    const disabled = tab !== 'info' && tab !== 'notes' && !hasDoc;
    return {
      padding: '6px 16px',
      fontSize: 13,
      fontWeight: activeTab === tab ? 600 : 400,
      color: activeTab === tab ? colors.accent : colors.textMuted,
      background: 'none',
      border: 'none',
      borderBottom: activeTab === tab ? `2px solid ${colors.accent}` : '2px solid transparent',
      cursor: disabled ? 'default' : ('pointer' as const),
      opacity: disabled ? 0.4 : 1,
      fontFamily: "'Inter', sans-serif",
    };
  };

  const wordCount = notes.trim() ? notes.trim().split(/\s+/).length : 0;

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', background: colors.bg }}>
      {/* Header */}
      <div style={{ borderBottom: `1px solid ${colors.border}`, flexShrink: 0 }}>
        <div style={{ padding: '8px 16px', display: 'flex', alignItems: 'center', gap: 8 }}>
          <button
            onClick={onBack}
            style={{
              background: 'none',
              border: 'none',
              color: colors.accent,
              cursor: 'pointer',
              fontSize: 13,
              padding: '4px 8px',
              fontFamily: "'Inter', sans-serif",
            }}
          >
            &larr; Back
          </button>
          <span
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: colors.text,
              flex: 1,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {paper.title}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 0, paddingLeft: 16 }}>
          <button style={tabStyle('info')} onClick={() => setActiveTab('info')}>
            Info
          </button>
          <button
            style={tabStyle('document')}
            onClick={() => hasDoc && setActiveTab('document')}
            title={!hasDoc ? 'Run pipeline first to view document' : ''}
          >
            Document
          </button>
          <button style={tabStyle('notes')} onClick={() => setActiveTab('notes')}>
            Notes
          </button>
          <button
            style={tabStyle('pdf')}
            onClick={() => hasDoc && setActiveTab('pdf')}
            title={!hasDoc ? 'Run pipeline first to view PDF' : ''}
          >
            PDF
          </button>
        </div>
      </div>

      {/* Tab content */}
      <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
        {/* ===== INFO TAB ===== */}
        {activeTab === 'info' && (
          <div style={{ padding: '28px 32px', maxWidth: 860, margin: '0 auto', width: '100%' }}>
            <h2
              style={{
                fontSize: 26,
                fontWeight: 700,
                color: colors.text,
                lineHeight: 1.35,
                marginBottom: 16,
                fontFamily: "'Inter', sans-serif",
                letterSpacing: '-0.02em',
              }}
            >
              {paper.title}
            </h2>

            {/* Authors */}
            {paper.authors.length > 0 && (
              <p style={{ fontSize: 15, color: colors.textMuted, marginBottom: 14, lineHeight: 1.6, margin: '0 0 14px' }}>
                {paper.authors.join(', ')}
              </p>
            )}

            {/* Meta line — subtle, inline style */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 20, alignItems: 'center' }}>
              <span
                style={{
                  fontSize: 13,
                  color: colors.textMuted,
                  fontWeight: 500,
                  fontFamily: "'Inter', sans-serif",
                }}
              >
                {paper.source || 'arXiv'}: {paper.paperId}
              </span>
              {paper.upvotes != null && (
                <>
                  <span style={{ color: colors.border }}>·</span>
                  <span style={{ fontSize: 13, color: colors.textMuted, fontWeight: 500 }}>
                    &#9650; {paper.upvotes}
                  </span>
                </>
              )}
              {paper.citations != null && paper.citations > 0 && (
                <>
                  <span style={{ color: colors.border }}>·</span>
                  <span style={{ fontSize: 13, color: colors.textMuted, fontWeight: 500 }}>
                    {paper.citations} citations
                  </span>
                </>
              )}
              {paper.organization && (
                <>
                  <span style={{ color: colors.border }}>·</span>
                  <span style={{ fontSize: 13, color: colors.textMuted, fontWeight: 500 }}>
                    {paper.organization}
                  </span>
                </>
              )}
              {paper.githubUrls.length > 0 && (
                <>
                  <span style={{ color: colors.border }}>·</span>
                  <a
                    href={paper.githubUrls[0]}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{
                      fontSize: 13,
                      color: colors.textMuted,
                      fontWeight: 500,
                      textDecoration: 'none',
                    }}
                    onMouseEnter={(e) => { e.currentTarget.style.color = colors.accent; }}
                    onMouseLeave={(e) => { e.currentTarget.style.color = colors.textMuted; }}
                  >
                    GitHub{paper.githubStars != null ? ` ${paper.githubStars}` : ''}
                  </a>
                </>
              )}
              {paper.numComments != null && paper.numComments > 0 && (
                <>
                  <span style={{ color: colors.border }}>·</span>
                  <span style={{ fontSize: 13, color: colors.textDimmed }}>{paper.numComments} comments</span>
                </>
              )}
            </div>

            {/* AI Keywords — subtle chips */}
            {paper.aiKeywords && paper.aiKeywords.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 20 }}>
                {paper.aiKeywords.map((kw, i) => (
                  <span
                    key={i}
                    style={{
                      fontSize: 12,
                      padding: '3px 10px',
                      borderRadius: 6,
                      border: `1px solid ${colors.border}`,
                      color: colors.textMuted,
                      fontWeight: 400,
                      fontFamily: "'Inter', sans-serif",
                    }}
                  >
                    {kw}
                  </span>
                ))}
              </div>
            )}

            {/* AI Summary */}
            {paper.aiSummary && (
              <div style={{ marginBottom: 28 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, color: colors.text, marginBottom: 10, fontFamily: "'Inter', sans-serif" }}>AI Summary</h3>
                <p style={{ fontSize: 15, color: colors.textSecondary, lineHeight: 1.75, margin: 0 }}>
                  {paper.aiSummary}
                </p>
              </div>
            )}

            {/* Abstract */}
            <div style={{ marginBottom: 28 }}>
              <h3 style={{ fontSize: 16, fontWeight: 600, color: colors.text, marginBottom: 10, fontFamily: "'Inter', sans-serif" }}>Abstract</h3>
              <p style={{ fontSize: 15, color: colors.textSecondary, lineHeight: 1.75, margin: 0 }}>
                {paper.abstract || 'No abstract available.'}
              </p>
            </div>

            {/* Actions */}
            <div style={{ display: 'flex', gap: 10, marginBottom: 24, flexWrap: 'wrap' }}>
              {!hasDoc && pipelineStatus === 'idle' && (
                <button
                  onClick={handleStartPipeline}
                  disabled={starting}
                  style={{
                    background: colors.accent,
                    color: '#fff',
                    border: 'none',
                    borderRadius: 8,
                    padding: '10px 22px',
                    fontSize: 14,
                    fontWeight: 600,
                    cursor: starting ? 'default' : 'pointer',
                    opacity: starting ? 0.6 : 1,
                    fontFamily: "'Inter', sans-serif",
                  }}
                >
                  {starting ? 'Starting...' : 'Start Reading Pipeline'}
                </button>
              )}
              <a
                href={arxivUrl}
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  background: colors.card,
                  color: colors.text,
                  border: `1px solid ${colors.border}`,
                  borderRadius: 8,
                  padding: '10px 22px',
                  fontSize: 14,
                  fontWeight: 500,
                  textDecoration: 'none',
                  fontFamily: "'Inter', sans-serif",
                }}
              >
                View on arXiv
              </a>
              {paper.githubUrls.map((url, i) => (
                <React.Fragment key={i}>
                  <a
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{
                      background: colors.card,
                      color: colors.text,
                      border: `1px solid ${colors.border}`,
                      borderRadius: 8,
                      padding: '10px 22px',
                      fontSize: 14,
                      fontWeight: 500,
                      textDecoration: 'none',
                      fontFamily: "'Inter', sans-serif",
                    }}
                  >
                    {url.replace('https://github.com/', '')}
                  </a>
                  {hasDoc && (
                    <button
                      onClick={() => {
                        const repoName = getRepoNameFromUrl(url);
                        if (repoName) router.push(`/repos?repo=${encodeURIComponent(repoName)}&paper_id=${encodeURIComponent(paper.paperId)}`);
                      }}
                      style={{
                        background: colors.accent,
                        color: '#fff',
                        border: 'none',
                        borderRadius: 8,
                        padding: '10px 22px',
                        fontSize: 14,
                        fontWeight: 600,
                        cursor: 'pointer',
                        fontFamily: "'Inter', sans-serif",
                      }}
                    >
                      Explore Code in Repo
                    </button>
                  )}
                </React.Fragment>
              ))}
            </div>

            {/* Pipeline progress */}
            {taskId && pipelineStatus === 'running' && (
              <PaperTaskProgress
                taskId={taskId}
                onComplete={handlePipelineComplete}
                onDismiss={() => setPipelineStatus('idle')}
              />
            )}
          </div>
        )}

        {/* ===== DOCUMENT TAB ===== */}
        {activeTab === 'document' && (
          <div style={{ display: 'flex', height: '100%' }}>
            {/* TOC sidebar */}
            {paperDoc && paperDoc.sections.length > 0 && (
              <div
                style={{
                  width: 180,
                  minWidth: 180,
                  borderRight: `1px solid ${colors.border}`,
                  overflow: 'auto',
                  padding: '12px 0',
                  flexShrink: 0,
                  position: 'sticky',
                  top: 0,
                  alignSelf: 'flex-start',
                  maxHeight: '100%',
                }}
              >
                <div style={{ padding: '0 12px 8px', fontSize: 11, fontWeight: 600, color: colors.textMuted, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                  Contents
                </div>
                {paperDoc.sections.map((section, i) => (
                  <button
                    key={i}
                    onClick={() => {
                      document.getElementById(`section-${i}`)?.scrollIntoView({ behavior: 'smooth' });
                    }}
                    style={{
                      display: 'block',
                      width: '100%',
                      textAlign: 'left',
                      background: 'none',
                      border: 'none',
                      padding: `3px 12px 3px ${12 + (section.level - 1) * 10}px`,
                      fontSize: 11,
                      color: colors.textMuted,
                      cursor: 'pointer',
                      lineHeight: 1.4,
                      fontFamily: "'Inter', sans-serif",
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.color = colors.accent;
                      e.currentTarget.style.background = colors.accent + '10';
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.color = colors.textMuted;
                      e.currentTarget.style.background = 'none';
                    }}
                    title={section.title}
                  >
                    {section.title}
                  </button>
                ))}
              </div>
            )}

            {/* Document content */}
            <div style={{ flex: 1, overflow: 'auto', padding: '20px 24px', minWidth: 0 }}>
              {loadingDoc && (
                <div style={{ textAlign: 'center', padding: '40px 0', color: colors.textMuted, fontSize: 13 }}>
                  Loading document...
                </div>
              )}
              {docError && (
                <div style={{ textAlign: 'center', padding: '40px 0', color: colors.error, fontSize: 13 }}>
                  {docError}
                </div>
              )}
              {paperDoc && (
                <div style={{ maxWidth: 800 }}>
                  {paperDoc.sections.map((section, i) => (
                    <div key={i} id={`section-${i}`} style={{ marginBottom: 24 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                        <h3
                          style={{
                            fontSize: section.level === 1 ? 18 : section.level === 2 ? 15 : 13,
                            fontWeight: 600,
                            color: colors.text,
                            margin: 0,
                            fontFamily: "'Inter', sans-serif",
                            flex: 1,
                          }}
                        >
                          {section.title}
                        </h3>
                        {onSendToChat && (
                          <button
                            onClick={() => handleDiscuss(section.title, section.content)}
                            style={{
                              background: 'none',
                              border: `1px solid ${colors.border}`,
                              borderRadius: 4,
                              padding: '2px 8px',
                              fontSize: 11,
                              color: colors.textMuted,
                              cursor: 'pointer',
                              fontFamily: "'Inter', sans-serif",
                              flexShrink: 0,
                              transition: 'all 0.15s',
                            }}
                            onMouseEnter={(e) => {
                              e.currentTarget.style.borderColor = colors.accent;
                              e.currentTarget.style.color = colors.accent;
                            }}
                            onMouseLeave={(e) => {
                              e.currentTarget.style.borderColor = colors.border;
                              e.currentTarget.style.color = colors.textMuted;
                            }}
                            title="Discuss this section in chat"
                          >
                            Discuss &#8599;
                          </button>
                        )}
                      </div>
                      <div
                        style={{
                          fontSize: 13,
                          color: colors.textSecondary,
                          lineHeight: 1.7,
                          whiteSpace: 'pre-wrap',
                        }}
                      >
                        {section.content}
                      </div>
                    </div>
                  ))}

                  {/* Figures & Tables */}
                  {paperDoc.figures.length > 0 && (
                    <div style={{ marginTop: 32 }}>
                      <h3 style={{ fontSize: 15, fontWeight: 600, color: colors.text, marginBottom: 12 }}>
                        Figures & Tables
                      </h3>
                      {paperDoc.figures.map((fig, i) => (
                        <div
                          key={i}
                          style={{
                            marginBottom: 16,
                            padding: 12,
                            background: colors.card,
                            border: `1px solid ${colors.border}`,
                            borderRadius: 8,
                          }}
                        >
                          <div style={{ fontSize: 12, fontWeight: 600, color: colors.textMuted, marginBottom: 4 }}>
                            {fig.figure_type} (Page {fig.page})
                          </div>
                          <div style={{ fontSize: 13, color: colors.textSecondary, lineHeight: 1.5 }}>
                            {fig.caption || fig.markdown}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Code Analysis */}
                  {paperDoc.code_analysis && (
                    <div style={{ marginTop: 32 }}>
                      <h3 style={{ fontSize: 15, fontWeight: 600, color: colors.text, marginBottom: 12 }}>
                        Code Analysis
                      </h3>

                      {paperDoc.code_analysis.architecture_diagram && (
                        <div
                          style={{
                            marginBottom: 16,
                            padding: 12,
                            background: colors.card,
                            border: `1px solid ${colors.border}`,
                            borderRadius: 8,
                          }}
                        >
                          <div
                            style={{ fontSize: 12, fontWeight: 600, color: colors.textMuted, marginBottom: 8 }}
                          >
                            Architecture
                          </div>
                          <pre
                            style={{
                              fontSize: 12,
                              color: colors.textSecondary,
                              whiteSpace: 'pre-wrap',
                              margin: 0,
                              lineHeight: 1.5,
                            }}
                          >
                            {paperDoc.code_analysis.architecture_diagram}
                          </pre>
                        </div>
                      )}

                      {paperDoc.code_analysis.paper_code_mapping.length > 0 && (
                        <div
                          style={{
                            padding: 12,
                            background: colors.card,
                            border: `1px solid ${colors.border}`,
                            borderRadius: 8,
                          }}
                        >
                          <div
                            style={{ fontSize: 12, fontWeight: 600, color: colors.textMuted, marginBottom: 8 }}
                          >
                            Paper-Code Mapping
                          </div>
                          <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                            <thead>
                              <tr style={{ borderBottom: `1px solid ${colors.border}` }}>
                                <th
                                  style={{
                                    textAlign: 'left',
                                    padding: '4px 8px',
                                    color: colors.textMuted,
                                    fontWeight: 600,
                                  }}
                                >
                                  Paper Concept
                                </th>
                                <th
                                  style={{
                                    textAlign: 'left',
                                    padding: '4px 8px',
                                    color: colors.textMuted,
                                    fontWeight: 600,
                                  }}
                                >
                                  Code Entity
                                </th>
                                <th
                                  style={{
                                    textAlign: 'left',
                                    padding: '4px 8px',
                                    color: colors.textMuted,
                                    fontWeight: 600,
                                  }}
                                >
                                  Explanation
                                </th>
                              </tr>
                            </thead>
                            <tbody>
                              {paperDoc.code_analysis.paper_code_mapping.map((m, i) => (
                                <tr key={i} style={{ borderBottom: `1px solid ${colors.border}` }}>
                                  <td style={{ padding: '4px 8px', color: colors.text }}>{m.paper_concept}</td>
                                  <td
                                    style={{
                                      padding: '4px 8px',
                                      color: colors.accent,
                                      fontFamily: 'monospace',
                                    }}
                                  >
                                    {m.code_entity}
                                  </td>
                                  <td style={{ padding: '4px 8px', color: colors.textSecondary }}>
                                    {m.explanation}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {/* ===== NOTES TAB ===== */}
        {activeTab === 'notes' && (
          <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '12px 24px' }}>
            {/* Toolbar */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                marginBottom: 8,
                flexShrink: 0,
              }}
            >
              <span style={{ fontSize: 11, color: colors.textMuted }}>
                {wordCount} words{saving ? ' · Saving...' : ''}
              </span>
              <div style={{ flex: 1 }} />
              <button
                onClick={handleGenerateNotes}
                disabled={generatingNotes}
                style={{
                  background: colors.accent,
                  color: '#fff',
                  border: 'none',
                  borderRadius: 6,
                  padding: '4px 12px',
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: generatingNotes ? 'default' : 'pointer',
                  opacity: generatingNotes ? 0.6 : 1,
                  fontFamily: "'Inter', sans-serif",
                }}
              >
                {generatingNotes ? 'Generating...' : 'Generate Reading Notes'}
              </button>
              <button
                onClick={() => {
                  if (notes && confirm('Clear all notes?')) clearNotes();
                }}
                style={{
                  background: 'none',
                  border: `1px solid ${colors.border}`,
                  borderRadius: 6,
                  padding: '4px 12px',
                  fontSize: 12,
                  color: colors.textMuted,
                  cursor: 'pointer',
                  fontFamily: "'Inter', sans-serif",
                }}
              >
                Clear
              </button>
            </div>
            {/* Textarea */}
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Write your reading notes here..."
              style={{
                flex: 1,
                width: '100%',
                resize: 'none',
                background: colors.card,
                color: colors.text,
                border: `1px solid ${colors.border}`,
                borderRadius: 8,
                padding: 16,
                fontSize: 13,
                lineHeight: 1.7,
                fontFamily: "'Inter', sans-serif",
                outline: 'none',
              }}
              onFocus={(e) => {
                e.currentTarget.style.borderColor = colors.accent;
              }}
              onBlur={(e) => {
                e.currentTarget.style.borderColor = colors.border;
              }}
            />
          </div>
        )}

        {/* ===== PDF TAB ===== */}
        {activeTab === 'pdf' && (
          <iframe
            src={getPaperPdfUrl(paper.paperId)}
            style={{ width: '100%', height: '100%', border: 'none' }}
            title={paper.title}
          />
        )}
      </div>
    </div>
  );
}

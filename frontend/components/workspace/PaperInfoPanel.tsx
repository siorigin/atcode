'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useCallback, useEffect } from 'react';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';
import { startPaperRead } from '@/lib/papers-api';
import PaperTaskProgress from '@/components/papers/PaperTaskProgress';
import type { PaperInfo } from '@/components/papers/PaperDetailView';

interface PaperInfoPanelProps {
  paper: PaperInfo;
  hasDoc: boolean;
  onPipelineComplete?: () => void;
}

export function PaperInfoPanel({ paper, hasDoc: initialHasDoc, onPipelineComplete }: PaperInfoPanelProps) {
  const { theme } = useTheme();
  const colors = getThemeColors(theme);

  const [hasDoc, setHasDoc] = useState(initialHasDoc);
  useEffect(() => { setHasDoc(initialHasDoc); }, [initialHasDoc]);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [pipelineStatus, setPipelineStatus] = useState<'idle' | 'running' | 'completed' | 'failed'>('idle');
  const [starting, setStarting] = useState(false);

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
    onPipelineComplete?.();
  }, [onPipelineComplete]);

  const arxivUrl = paper.url || `https://arxiv.org/abs/${paper.paperId}`;

  return (
    <div style={{ height: '100%', overflow: 'auto', background: colors.bg }}>
      <div style={{ padding: '24px 28px', maxWidth: 860, margin: '0 auto', width: '100%' }}>
        <h2 style={{
          fontSize: 22, fontWeight: 700, color: colors.text,
          lineHeight: 1.4, marginBottom: 14,
          fontFamily: "'Inter', sans-serif", letterSpacing: '-0.01em',
        }}>
          {paper.title}
        </h2>

        {/* Meta badges */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 18, fontSize: 12 }}>
          <span style={{
            background: colors.error + '18', color: colors.error,
            padding: '2px 10px', borderRadius: 12, fontWeight: 600,
          }}>
            {paper.source || 'arXiv'}: {paper.paperId}
          </span>
          {paper.upvotes != null && (
            <span style={{
              background: colors.accent + '20', color: colors.accent,
              padding: '2px 10px', borderRadius: 12, fontWeight: 600,
            }}>
              &#9650; {paper.upvotes}
            </span>
          )}
          {paper.citations != null && paper.citations > 0 && (
            <span style={{
              background: colors.accent + '20', color: colors.accent,
              padding: '2px 10px', borderRadius: 12, fontWeight: 600,
            }}>
              {paper.citations} citations
            </span>
          )}
          {paper.organization && (
            <span style={{
              background: colors.border, color: colors.textMuted,
              padding: '2px 10px', borderRadius: 12, fontWeight: 500,
            }}>
              {paper.organization}
            </span>
          )}
          {paper.githubUrls.length > 0 && (
            <a href={paper.githubUrls[0]} target="_blank" rel="noopener noreferrer" style={{
              background: colors.success + '18', color: colors.success,
              padding: '2px 10px', borderRadius: 12, fontWeight: 600, textDecoration: 'none',
            }}>
              GitHub{paper.githubStars != null ? ` ${paper.githubStars}` : ''}
            </a>
          )}
          {paper.numComments != null && paper.numComments > 0 && (
            <span style={{ color: colors.textMuted }}>{paper.numComments} comments</span>
          )}
        </div>

        {/* Authors */}
        {paper.authors.length > 0 && (
          <p style={{ fontSize: 12, color: colors.textMuted, marginBottom: 16, lineHeight: 1.5 }}>
            {paper.authors.join(', ')}
          </p>
        )}

        {/* AI Keywords */}
        {paper.aiKeywords && paper.aiKeywords.length > 0 && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 16 }}>
            {paper.aiKeywords.map((kw, i) => (
              <span key={i} style={{
                fontSize: 11, padding: '2px 10px', borderRadius: 12,
                background: colors.border, color: colors.textMuted, fontWeight: 500,
              }}>
                {kw}
              </span>
            ))}
          </div>
        )}

        {/* AI Summary */}
        {paper.aiSummary && (
          <div style={{ marginBottom: 24 }}>
            <h3 style={{ fontSize: 15, fontWeight: 600, color: colors.text, marginBottom: 10 }}>AI Summary</h3>
            <p style={{ fontSize: 14, color: colors.textSecondary, lineHeight: 1.7, margin: 0 }}>
              {paper.aiSummary}
            </p>
          </div>
        )}

        {/* Abstract */}
        <div style={{ marginBottom: 24 }}>
          <h3 style={{ fontSize: 15, fontWeight: 600, color: colors.text, marginBottom: 10 }}>Abstract</h3>
          <p style={{ fontSize: 14, color: colors.textSecondary, lineHeight: 1.7, margin: 0 }}>
            {paper.abstract || 'No abstract available.'}
          </p>
        </div>

        {/* Actions */}
        <div style={{ display: 'flex', gap: 10, marginBottom: 20, flexWrap: 'wrap' }}>
          {!hasDoc && pipelineStatus === 'idle' && (
            <button
              onClick={handleStartPipeline}
              disabled={starting}
              style={{
                background: colors.accent, color: '#fff', border: 'none',
                borderRadius: 8, padding: '8px 20px', fontSize: 13, fontWeight: 600,
                cursor: starting ? 'default' : 'pointer', opacity: starting ? 0.6 : 1,
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
              background: colors.card, color: colors.accent,
              border: `1px solid ${colors.border}`, borderRadius: 8,
              padding: '8px 20px', fontSize: 13, fontWeight: 500,
              textDecoration: 'none', fontFamily: "'Inter', sans-serif",
            }}
          >
            View on arXiv
          </a>
          {paper.githubUrls.map((url, i) => (
            <a
              key={i}
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                background: colors.card, color: colors.success,
                border: `1px solid ${colors.border}`, borderRadius: 8,
                padding: '8px 20px', fontSize: 13, fontWeight: 500,
                textDecoration: 'none', fontFamily: "'Inter', sans-serif",
              }}
            >
              {url.replace('https://github.com/', '')}
            </a>
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
    </div>
  );
}

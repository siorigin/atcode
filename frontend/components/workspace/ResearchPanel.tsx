'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';
import { apiFetch } from '@/lib/api-client';
import { ModelCombobox } from '@/components/ModelCombobox';
import { DOC_GENERATION_MODELS } from '@/lib/model-config';
import { WikiDoc } from '@/components/WikiDoc';

interface Research {
  name: string;
  lastUpdated: string;
  metadata: {
    id?: string;
    referencesCount?: number;
    codeBlocksCount?: number;
    query?: string;
  };
}

interface ResearchPanelProps {
  repoName: string;
  onNavigateToNode?: (qualifiedName: string) => void;
  onResearchSelect?: (name: string | null) => void;
}

const METADATA_FILES = ['_folders', '_meta', '_index', 'overview'];

const RESEARCH_TEMPLATES = {
  operator: {
    id: 'operator',
    name: 'Operator Analysis',
    template_zh: `分析 [在此输入算子名称] 的完整实现：
- 函数签名和参数说明
- 依赖关系和调用链
- 底层实现（Triton/CUDA/C++ 内核）
- 性能优化策略
- 使用示例`,
    template_en: `Analyze the complete implementation of [enter operator name here]:
- Function signature and parameters
- Dependencies and call chains
- Low-level implementation (Triton/CUDA/C++ kernels)
- Performance optimization strategies
- Usage examples`,
  },
  research: {
    id: 'research',
    name: 'Deep Research',
    template_zh: `深度研究 [在此输入研究主题]：
- 核心概念和设计理念
- 架构设计和组件关系
- 关键实现细节
- 数据流和调用链
- 最佳实践和使用模式`,
    template_en: `Deep research on [enter topic here]:
- Core concepts and design philosophy
- Architecture and component relationships
- Key implementation details
- Data flow and call chains
- Best practices and usage patterns`,
  },
  architecture: {
    id: 'architecture',
    name: 'Architecture Analysis',
    template_zh: `分析 [在此输入模块/系统名称] 的架构设计：
- 整体架构和分层结构
- 核心组件和职责划分
- 组件间通信和数据流
- 使用的设计模式
- 扩展点和接口设计`,
    template_en: `Analyze the architecture of [enter module/system name here]:
- Overall architecture and layering
- Core components and responsibilities
- Inter-component communication and data flow
- Design patterns used
- Extension points and interface design`,
  },
  custom: {
    id: 'custom',
    name: 'Custom',
    template_zh: `[在此输入你的研究需求]`,
    template_en: `[Enter your research requirements here]`,
  },
} as const;

type TemplateId = keyof typeof RESEARCH_TEMPLATES;

export function ResearchPanel({ repoName, onNavigateToNode, onResearchSelect }: ResearchPanelProps) {
  const { theme } = useTheme();
  const colors = getThemeColors(theme);

  const [researchList, setResearchList] = useState<Research[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedResearch, setSelectedResearch] = useState<string | null>(null);

  // For viewing a selected research doc — full JSON data for WikiDoc
  const [docData, setDocData] = useState<any>(null);
  const [docLoading, setDocLoading] = useState(false);

  // New research form
  const [showNewForm, setShowNewForm] = useState(false);
  const [newName, setNewName] = useState('');
  const [newQuery, setNewQuery] = useState('');
  const [selectedTemplate, setSelectedTemplate] = useState<TemplateId>('operator');
  const [templateLang, setTemplateLang] = useState<'en' | 'zh'>('en');
  const [selectedModel, setSelectedModel] = useState('');
  const [isGenerating, setIsGenerating] = useState(false);
  const [genError, setGenError] = useState('');
  const [genTaskId, setGenTaskId] = useState<string | null>(null);
  const [genProgress, setGenProgress] = useState({ progress: 0, message: '' });
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Load research list
  useEffect(() => {
    if (!repoName) return;
    loadResearchList();
  }, [repoName]);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  async function loadResearchList() {
    setLoading(true);
    try {
      const response = await apiFetch(`/api/docs/operators/${repoName}`);
      const data = await response.json();
      setResearchList(data.operators || []);
    } catch (error) {
      console.error('Error loading research list:', error);
    } finally {
      setLoading(false);
    }
  }

  const filtered = useMemo(() => {
    return researchList
      .filter(item => !METADATA_FILES.includes(item.name))
      .filter(item => {
        if (!searchQuery.trim()) return true;
        const q = searchQuery.toLowerCase();
        return item.name.toLowerCase().includes(q) ||
          (item.metadata?.query || '').toLowerCase().includes(q);
      });
  }, [researchList, searchQuery]);

  // Load doc content when a research is selected
  // Uses plain fetch (Next.js API route), not apiFetch (FastAPI backend)
  const handleSelectResearch = useCallback(async (name: string) => {
    setSelectedResearch(name);
    onResearchSelect?.(name);
    setDocLoading(true);
    setDocData(null);
    try {
      const resp = await fetch(`/api/repos/${repoName}/${encodeURIComponent(name)}`);
      if (resp.ok) {
        const data = await resp.json();
        setDocData(data);
      }
    } catch (e) {
      console.error('Error loading research doc:', e);
    } finally {
      setDocLoading(false);
    }
  }, [repoName, onResearchSelect]);

  // Handle template selection
  const handleTemplateSelect = (id: TemplateId) => {
    setSelectedTemplate(id);
    const tpl = RESEARCH_TEMPLATES[id];
    setNewQuery(templateLang === 'zh' ? tpl.template_zh : tpl.template_en);
  };

  const handleTemplateLangChange = (lang: 'en' | 'zh') => {
    setTemplateLang(lang);
    const tpl = RESEARCH_TEMPLATES[selectedTemplate];
    setNewQuery(lang === 'zh' ? tpl.template_zh : tpl.template_en);
  };

  // Start new research generation
  async function handleStartResearch() {
    if (!newName.trim() || !newQuery.trim()) {
      setGenError('Please enter both title and description');
      return;
    }
    setIsGenerating(true);
    setGenError('');

    try {
      const response = await apiFetch('/api/docs/generate/async', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          repo: repoName,
          operator: newName.trim(),
          query_template: newQuery.trim(),
          model: selectedModel || undefined,
        }),
      });
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.detail || data.message || 'Failed to start research');
      }

      setGenTaskId(data.task_id);
      setGenProgress({ progress: 0, message: 'Starting...' });

      // Poll for progress (FastAPI backend)
      const poll = setInterval(async () => {
        try {
          const resp = await apiFetch(`/api/tasks/${data.task_id}`);
          const status = await resp.json();
          if (status.status === 'completed') {
            clearInterval(poll);
            pollRef.current = null;
            setGenTaskId(null);
            setIsGenerating(false);
            setGenProgress({ progress: 100, message: 'Done!' });
            setShowNewForm(false);
            setNewName('');
            setNewQuery('');
            setSelectedModel('');
            loadResearchList();
          } else if (status.status === 'failed') {
            clearInterval(poll);
            pollRef.current = null;
            setGenTaskId(null);
            setIsGenerating(false);
            setGenError(status.error || 'Generation failed');
            setGenProgress({ progress: 0, message: '' });
          } else {
            setGenProgress({
              progress: status.progress ?? 0,
              message: status.message || status.step || 'Generating...',
            });
          }
        } catch {
          // ignore polling errors
        }
      }, 2000);
      pollRef.current = poll;

    } catch (error: any) {
      setGenError(error.message || 'Failed to start research');
      setIsGenerating(false);
    }
  }

  // ── New research form view ──
  if (showNewForm) {
    return (
      <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{
          height: 32, minHeight: 32, display: 'flex', alignItems: 'center',
          padding: '0 8px', borderBottom: `1px solid ${colors.border}`,
          background: colors.card, gap: 6, flexShrink: 0,
        }}>
          <button
            onClick={() => { if (!isGenerating) { setShowNewForm(false); setGenError(''); } }}
            disabled={isGenerating}
            style={{
              background: 'none', border: 'none', color: isGenerating ? colors.textMuted : colors.accent,
              cursor: isGenerating ? 'default' : 'pointer', fontSize: 12, padding: '2px 6px',
              fontFamily: "'Inter', sans-serif",
            }}
          >
            &larr; Back
          </button>
          <span style={{ fontSize: 11, fontWeight: 600, color: colors.text }}>New Research</span>
        </div>
        <div style={{ flex: 1, overflow: 'auto', padding: '12px 14px' }}>
          {/* Title */}
          <label style={{ fontSize: 11, fontWeight: 600, color: colors.textMuted, display: 'block', marginBottom: 4 }}>
            Title
          </label>
          <input
            type="text"
            placeholder="e.g. Attention Mechanism"
            value={newName}
            onChange={e => setNewName(e.target.value)}
            disabled={isGenerating}
            style={{
              width: '100%', background: colors.card, border: `1px solid ${colors.border}`,
              borderRadius: 6, color: colors.text, padding: '6px 10px', fontSize: 12,
              fontFamily: "'Inter', sans-serif", outline: 'none', boxSizing: 'border-box',
              marginBottom: 12,
            }}
          />

          {/* Template selector */}
          <label style={{ fontSize: 11, fontWeight: 600, color: colors.textMuted, display: 'block', marginBottom: 4 }}>
            Template
          </label>
          <div style={{ display: 'flex', gap: 4, marginBottom: 8, flexWrap: 'wrap' }}>
            {(Object.keys(RESEARCH_TEMPLATES) as TemplateId[]).map(id => (
              <button
                key={id}
                onClick={() => handleTemplateSelect(id)}
                disabled={isGenerating}
                style={{
                  padding: '3px 10px', fontSize: 11, fontWeight: selectedTemplate === id ? 600 : 400,
                  color: selectedTemplate === id ? '#fff' : colors.textMuted,
                  background: selectedTemplate === id ? colors.accent : 'transparent',
                  border: `1px solid ${selectedTemplate === id ? colors.accent : colors.border}`,
                  borderRadius: 5, cursor: isGenerating ? 'default' : 'pointer',
                  fontFamily: "'Inter', sans-serif",
                }}
              >
                {RESEARCH_TEMPLATES[id].name}
              </button>
            ))}
          </div>

          {/* Language toggle */}
          <div style={{ display: 'flex', gap: 4, marginBottom: 10 }}>
            {(['en', 'zh'] as const).map(lang => (
              <button
                key={lang}
                onClick={() => handleTemplateLangChange(lang)}
                disabled={isGenerating}
                style={{
                  padding: '2px 10px', fontSize: 11, fontWeight: templateLang === lang ? 600 : 400,
                  color: templateLang === lang ? '#fff' : colors.textMuted,
                  background: templateLang === lang ? colors.accent : 'transparent',
                  border: `1px solid ${templateLang === lang ? colors.accent : colors.border}`,
                  borderRadius: 5, cursor: isGenerating ? 'default' : 'pointer',
                  fontFamily: "'Inter', sans-serif",
                }}
              >
                {lang === 'en' ? 'English' : '中文'}
              </button>
            ))}
          </div>

          {/* Query text area */}
          <label style={{ fontSize: 11, fontWeight: 600, color: colors.textMuted, display: 'block', marginBottom: 4 }}>
            Description
          </label>
          <textarea
            value={newQuery}
            onChange={e => setNewQuery(e.target.value)}
            disabled={isGenerating}
            rows={6}
            style={{
              width: '100%', background: colors.card, border: `1px solid ${colors.border}`,
              borderRadius: 6, color: colors.text, padding: '8px 10px', fontSize: 12,
              fontFamily: "'Inter', sans-serif", outline: 'none', boxSizing: 'border-box',
              resize: 'vertical', lineHeight: 1.5, marginBottom: 12,
            }}
          />

          {/* Model selector */}
          <label style={{ fontSize: 11, fontWeight: 600, color: colors.textMuted, display: 'block', marginBottom: 4 }}>
            Model
          </label>
          <ModelCombobox
            value={selectedModel}
            onChange={setSelectedModel}
            theme={theme as any}
            presets={DOC_GENERATION_MODELS}
            showTiers={false}
            placeholder="Default"
            style={{ width: '100%', marginBottom: 14 }}
          />

          {/* Error */}
          {genError && (
            <div style={{
              fontSize: 11, color: colors.error, marginBottom: 10,
              padding: '6px 10px', background: colors.card, border: `1px solid ${colors.border}`,
              borderRadius: 6,
            }}>
              {genError}
            </div>
          )}

          {/* Progress */}
          {isGenerating && genTaskId && (
            <div style={{
              marginBottom: 12, padding: '10px 12px',
              background: colors.card, border: `1px solid ${colors.border}`, borderRadius: 8,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <div style={{
                  width: 6, height: 6, borderRadius: '50%', background: colors.accent,
                  animation: 'pulse 1.5s ease-in-out infinite',
                }} />
                <span style={{ fontSize: 11, fontWeight: 600, color: colors.text }}>Generating</span>
              </div>
              <div style={{ height: 3, borderRadius: 2, background: colors.border, overflow: 'hidden', marginBottom: 4 }}>
                <div style={{
                  height: '100%', borderRadius: 2, background: colors.accent,
                  width: `${genProgress.progress}%`, transition: 'width 0.3s ease',
                }} />
              </div>
              <div style={{ fontSize: 10, color: colors.textMuted }}>{genProgress.message}</div>
              <style>{`@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }`}</style>
            </div>
          )}

          {/* Generate button */}
          <button
            onClick={handleStartResearch}
            disabled={isGenerating || !newName.trim() || !newQuery.trim()}
            style={{
              width: '100%', padding: '8px 20px', fontSize: 13, fontWeight: 600, color: '#fff',
              background: isGenerating ? colors.textMuted : colors.accent,
              border: 'none', borderRadius: 7, cursor: isGenerating ? 'default' : 'pointer',
              fontFamily: "'Inter', sans-serif", transition: 'opacity 0.15s ease',
            }}
          >
            {isGenerating ? 'Generating...' : 'Start Research'}
          </button>
        </div>
      </div>
    );
  }

  // ── Document viewer (using WikiDoc for full formatting) ──
  if (selectedResearch) {
    const markdown = docData?.markdown || docData?.content || null;
    return (
      <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{
          height: 32, minHeight: 32, display: 'flex', alignItems: 'center',
          padding: '0 8px', borderBottom: `1px solid ${colors.border}`,
          background: colors.card, gap: 6, flexShrink: 0,
        }}>
          <button
            onClick={() => { setSelectedResearch(null); setDocData(null); onResearchSelect?.(null); }}
            style={{
              background: 'none', border: 'none', color: colors.accent,
              cursor: 'pointer', fontSize: 12, padding: '2px 6px',
              fontFamily: "'Inter', sans-serif",
            }}
          >
            &larr; Back
          </button>
          <span style={{
            fontSize: 11, fontWeight: 600, color: colors.text,
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}>
            {selectedResearch}
          </span>
        </div>
        <div style={{ flex: 1, overflow: 'auto', padding: '12px 16px' }}>
          {docLoading ? (
            <div style={{ textAlign: 'center', padding: '40px 0', color: colors.textMuted, fontSize: 12 }}>
              Loading...
            </div>
          ) : markdown ? (
            <WikiDoc
              markdown={markdown}
              references={docData?.references}
              codeBlocks={docData?.code_blocks || docData?.codeBlocks}
              repoName={repoName}
              onNavigateToNode={onNavigateToNode}
              layoutMode="full"
            />
          ) : (
            <div style={{ textAlign: 'center', padding: '40px 0', color: colors.textMuted, fontSize: 12 }}>
              No content available
            </div>
          )}
        </div>
      </div>
    );
  }

  // ── List view ──
  return (
    <div style={{ height: '100%', overflow: 'auto', background: colors.bg }}>
      <div style={{ padding: '8px 12px' }}>
        {/* Search + New button row */}
        <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
          <input
            type="text"
            placeholder="Search research..."
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            style={{
              flex: 1, background: colors.card, border: `1px solid ${colors.border}`,
              borderRadius: 6, color: colors.text, padding: '5px 10px', fontSize: 12,
              fontFamily: "'Inter', sans-serif", outline: 'none', boxSizing: 'border-box',
            }}
            onFocus={e => { e.currentTarget.style.borderColor = colors.accent; }}
            onBlur={e => { e.currentTarget.style.borderColor = colors.border; }}
          />
          <button
            onClick={() => {
              setShowNewForm(true);
              setGenError('');
              // Initialize template text
              const tpl = RESEARCH_TEMPLATES[selectedTemplate];
              setNewQuery(templateLang === 'zh' ? tpl.template_zh : tpl.template_en);
            }}
            style={{
              background: colors.accent, border: 'none', borderRadius: 6,
              color: '#fff', padding: '0 10px', fontSize: 16, fontWeight: 600,
              cursor: 'pointer', lineHeight: 1, display: 'flex', alignItems: 'center',
              flexShrink: 0,
            }}
            title="New Research"
          >
            +
          </button>
        </div>

        {/* Count */}
        <div style={{ fontSize: 11, color: colors.textMuted, marginBottom: 8, fontWeight: 500 }}>
          {filtered.length} research document{filtered.length !== 1 ? 's' : ''}
        </div>

        {loading && (
          <div style={{ padding: '20px 0', textAlign: 'center', color: colors.textMuted, fontSize: 12 }}>
            Loading...
          </div>
        )}

        {!loading && filtered.length === 0 && (
          <div style={{ padding: '20px 0', textAlign: 'center', color: colors.textMuted, fontSize: 12 }}>
            {searchQuery ? 'No matching research' : 'No research documents yet'}
          </div>
        )}

        {!loading && filtered.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {filtered.map(item => (
              <ResearchItem
                key={item.name}
                item={item}
                colors={colors}
                onClick={() => handleSelectResearch(item.name)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ResearchItem({
  item,
  colors,
  onClick,
}: {
  item: Research;
  colors: ReturnType<typeof getThemeColors>;
  onClick: () => void;
}) {
  const timeAgo = useMemo(() => {
    if (!item.lastUpdated) return '';
    try {
      const d = new Date(item.lastUpdated);
      const now = new Date();
      const diffMs = now.getTime() - d.getTime();
      const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
      if (diffDays === 0) return 'today';
      if (diffDays === 1) return 'yesterday';
      if (diffDays < 7) return `${diffDays}d ago`;
      if (diffDays < 30) return `${Math.floor(diffDays / 7)}w ago`;
      return d.toLocaleDateString();
    } catch { return ''; }
  }, [item.lastUpdated]);

  return (
    <div
      onClick={onClick}
      style={{
        background: colors.card,
        border: `1px solid ${colors.border}`,
        borderRadius: 8,
        padding: '8px 10px',
        cursor: 'pointer',
        transition: 'border-color 0.15s',
      }}
      onMouseEnter={e => { e.currentTarget.style.borderColor = colors.accent; }}
      onMouseLeave={e => { e.currentTarget.style.borderColor = colors.border; }}
    >
      <div style={{
        fontSize: 12, fontWeight: 600, color: colors.text,
        lineHeight: 1.4, marginBottom: 4,
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>
        {item.name}
      </div>
      {item.metadata?.query && (
        <div style={{
          fontSize: 10, color: colors.textDimmed, lineHeight: 1.3,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          marginBottom: 4,
        }}>
          {item.metadata.query}
        </div>
      )}
      <div style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 10, color: colors.textMuted }}>
        {timeAgo && <span>{timeAgo}</span>}
        {item.metadata?.referencesCount != null && item.metadata.referencesCount > 0 && (
          <span>{item.metadata.referencesCount} refs</span>
        )}
        {item.metadata?.codeBlocksCount != null && item.metadata.codeBlocksCount > 0 && (
          <span>{item.metadata.codeBlocksCount} blocks</span>
        )}
      </div>
    </div>
  );
}


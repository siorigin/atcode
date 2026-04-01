'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect, useCallback, useMemo, lazy, Suspense } from 'react';
import { createPortal } from 'react-dom';
import { useBackgroundTask } from '@/lib/hooks';
import { triggerTaskRefresh } from '@/lib/hooks/useGlobalTasks';
import { buildModelSelectorTiers, useModels } from '@/lib/hooks/useModels';
import { useTheme } from '@/lib/theme-context';
import { useTranslation } from '@/lib/i18n';
import { ModelCombobox } from './ModelCombobox';
import { apiFetch } from '@/lib/api-client';
import { enrichMarkdownWithCode, downloadMarkdown, downloadPDF, downloadRenderedPDF, createExportableHTML, exportWithAllCode, type CodeBlockData } from '@/lib/export-utils';

// Lazy load WikiDoc to avoid importing heavy dependencies (react-markdown, katex, syntax-highlighter) upfront
const WikiDoc = lazy(() => import('./WikiDoc').then(m => ({ default: m.WikiDoc })));

// ==================== Design System Constants ====================

const FONTS = {
  // Primary font for UI elements
  ui: '-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif',
  // Monospace font for code
  mono: '"JetBrains Mono", "Fira Code", "SF Mono", Monaco, Consolas, "Liberation Mono", monospace',
};

// Theme colors that map to the global theme system
const COLORS = {
  dark: {
    bg: {
      primary: '#0d1117',
      secondary: '#161b22',
      tertiary: '#21262d',
      elevated: '#1c2128',
    },
    border: {
      primary: '#30363d',
      secondary: '#21262d',
      muted: '#373e47',
    },
    text: {
      primary: '#e6edf3',
      secondary: '#8b949e',
      muted: '#6e7681',
      link: '#58a6ff',
    },
    accent: {
      blue: '#58a6ff',
      blueBg: 'rgba(56, 139, 253, 0.15)',
      green: '#3fb950',
      greenBg: 'rgba(46, 160, 67, 0.15)',
      purple: '#a371f7',
      purpleBg: 'rgba(163, 113, 247, 0.15)',
    },
  },
  light: {
    bg: {
      primary: '#ffffff',
      secondary: '#f6f8fa',
      tertiary: '#f0f3f6',
      elevated: '#ffffff',
    },
    border: {
      primary: '#d0d7de',
      secondary: '#e8ebef',
      muted: '#d8dee4',
    },
    text: {
      primary: '#1f2328',
      secondary: '#656d76',
      muted: '#8c959f',
      link: '#0969da',
    },
    accent: {
      blue: '#0969da',
      blueBg: 'rgba(9, 105, 218, 0.08)',
      green: '#1a7f37',
      greenBg: 'rgba(26, 127, 55, 0.08)',
      purple: '#8250df',
      purpleBg: 'rgba(130, 80, 223, 0.08)',
    },
  },
  // Beige/Eye Comfort theme colors
  beige: {
    bg: {
      primary: '#faf8f5',
      secondary: '#f5f0e8',
      tertiary: '#ebe5d9',
      elevated: '#ffffff',
    },
    border: {
      primary: '#d4c8b8',
      secondary: '#e5ddd0',
      muted: '#c9bca8',
    },
    text: {
      primary: '#3d3632',
      secondary: '#6b5f54',
      muted: '#8a7b6c',
      link: '#8b5a2b',
    },
    accent: {
      blue: '#8b5a2b',
      blueBg: 'rgba(139, 90, 43, 0.12)',
      green: '#5d7a3a',
      greenBg: 'rgba(93, 122, 58, 0.12)',
      purple: '#7a5a8a',
      purpleBg: 'rgba(122, 90, 138, 0.12)',
    },
  },
};

const getColors = (theme: 'dark' | 'light' | 'beige') => COLORS[theme];

// Helper for modal backdrops
const getBackdropBg = (theme: 'dark' | 'light' | 'beige') => {
  if (theme === 'dark') return 'rgba(0, 0, 0, 0.6)';
  if (theme === 'beige') return 'rgba(80, 60, 40, 0.4)';
  return 'rgba(0, 0, 0, 0.4)';
};

function formatRelativeTime(dateStr: string | null | undefined): string {
  if (!dateStr) return '';
  try {
    const date = new Date(dateStr);
    const diffMs = Date.now() - date.getTime();
    const diffSec = Math.floor(diffMs / 1000);
    const diffMin = Math.floor(diffSec / 60);
    const diffHr = Math.floor(diffMin / 60);

    if (diffSec < 60) return 'just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHr < 24) return `${diffHr}h ago`;
    return date.toLocaleDateString();
  } catch {
    return '';
  }
}

function formatClockTime(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return '';
  }
}

function summarizeTrajectoryDetails(details?: Record<string, unknown> | null): string {
  if (!details) return '';

  const parts: string[] = [];
  if (typeof details.tool_call_count === 'number' && details.tool_call_count > 0) {
    parts.push(`${details.tool_call_count} tool calls`);
  }
  if (typeof details.explored_node_count === 'number' && details.explored_node_count > 0) {
    parts.push(`${details.explored_node_count} nodes`);
  }
  if (typeof details.outline_count === 'number' && details.outline_count > 0) {
    parts.push(`${details.outline_count} planned sections`);
  }
  if (typeof details.completed_section_count === 'number' && details.completed_section_count > 0) {
    parts.push(`${details.completed_section_count} completed sections`);
  }

  return parts.join(' • ');
}

function extractToolCalls(details?: Record<string, unknown> | null): Array<{ display: string; resultPreview?: string }> {
  const recentToolCalls = details?.recent_tool_calls;
  if (!Array.isArray(recentToolCalls)) return [];

  return recentToolCalls.flatMap((call) => {
    if (!call || typeof call !== 'object') return [];

    const display = typeof call.display === 'string' ? call.display : '';
    if (!display) return [];

    return [{
      display,
      resultPreview: typeof call.result_preview === 'string' ? call.result_preview : undefined,
    }];
  });
}

// Helper for modal backgrounds
const getModalBg = (theme: 'dark' | 'light' | 'beige') => {
  if (theme === 'dark') return 'rgba(28, 33, 40, 0.95)';
  if (theme === 'beige') return 'rgba(250, 248, 245, 0.95)';
  return 'rgba(255, 255, 255, 0.95)';
};

// Helper for sticky header backgrounds
const getStickyBg = (theme: 'dark' | 'light' | 'beige') => {
  if (theme === 'dark') return 'rgba(13, 17, 23, 0.8)';
  if (theme === 'beige') return 'rgba(250, 248, 245, 0.8)';
  return 'rgba(255, 255, 255, 0.8)';
};

// Helper for error colors
const getErrorColors = (theme: 'dark' | 'light' | 'beige') => ({
  bg: theme === 'dark' ? 'rgba(248, 81, 73, 0.1)' : (theme === 'beige' ? 'rgba(180, 80, 60, 0.1)' : '#ffebe9'),
  border: theme === 'dark' ? 'rgba(248, 81, 73, 0.4)' : (theme === 'beige' ? 'rgba(180, 80, 60, 0.4)' : '#ff818266'),
  text: theme === 'dark' ? '#ff7b72' : (theme === 'beige' ? '#a04030' : '#cf222e'),
  button: theme === 'dark' ? '#da3633' : (theme === 'beige' ? '#a04030' : '#cf222e'),
});

// Helper for shadows
const getShadowStyle = (theme: 'dark' | 'light' | 'beige', intensity: 'light' | 'medium' | 'heavy') => {
  const shadows = {
    dark: {
      light: '0 4px 12px rgba(0,0,0,0.2)',
      medium: '0 8px 24px rgba(0,0,0,0.3)',
      heavy: '0 24px 48px rgba(0,0,0,0.5)',
    },
    light: {
      light: '0 4px 12px rgba(0,0,0,0.04)',
      medium: '0 8px 24px rgba(0,0,0,0.08)',
      heavy: '0 24px 48px rgba(0,0,0,0.15)',
    },
    beige: {
      light: '0 4px 12px rgba(80,60,40,0.08)',
      medium: '0 8px 24px rgba(80,60,40,0.12)',
      heavy: '0 24px 48px rgba(80,60,40,0.2)',
    },
  };
  return shadows[theme][intensity];
};

// ==================== Types ====================

export interface TreeNode {
  name: string;
  path: string | null;
  type: 'overview' | 'package' | 'module' | 'section';
  node_count?: number;
  order?: number;
  headings?: Array<{
    name: string;
    anchor: string;
    level: number;
    file_path?: string;
    children?: any[];
  }>;
  children?: TreeNode[];
}

export interface RightNavItem {
  name: string;
  anchor: string;
  children?: RightNavItem[];
}

export interface IndexData {
  repo: string;
  generated_at: string;
  version: string;
  generation_mode?: string;
  regeneration_enabled?: boolean;
  statistics: {
    total_packages?: number;
    total_modules?: number;
    total_functions?: number;
    total_methods?: number;
    total_classes?: number;
    total_calls?: number;
    sections_generated?: number;
    total_files?: number;
    total_explored_nodes?: number;
    max_depth_reached?: number;
  };
  tree: TreeNode[];
  right_nav?: {
    overview: RightNavItem[];
  };
  section_files?: string[];
}

export interface GenerateOptions {
  docDepth: number;
  language: 'zh' | 'en';
  mode: 'overview' | 'detailed';
  focus: string;  // Optional focus area for documentation generation
  model: string;  // Model to use for generation (empty string = default)
}

export interface VersionInfo {
  version_id: string;
  mode: string;
  doc_depth: number;
  generated_at: string;
  statistics?: {
    sections_generated?: number;
    total_files?: number;
  };
}

export interface PaperContext {
  paperId: string;
  title: string;
  abstract?: string;
}

export interface OverviewDocV2Props {
  repoName: string;
  index: IndexData | null;
  currentPath: string;
  content: string | null;
  loading: boolean;
  versions?: VersionInfo[];
  currentVersionId?: string;
  defaultVersionId?: string;  // The version marked as default in _meta.json
  paperContext?: PaperContext;  // Optional paper context for paper-repo linking
  onNavigate: (path: string) => void;
  onRefresh: () => void;
  onSectionLoad?: (path: string, versionId?: string) => Promise<string | void>;
  onVersionChange?: (versionId: string) => void;
  onVersionsUpdate?: () => void;  // Callback to refresh versions list after changes
  onNavigateToNode?: (qualifiedName: string) => void;  // Navigate to node in shared RepoViewer
}

// ==================== CSS Keyframes ====================

const keyframesStyle = `
  @keyframes spin {
    to { transform: rotate(360deg); }
  }
  @keyframes pulse {
    0%, 100% { transform: scale(1); opacity: 1; }
    50% { transform: scale(1.05); opacity: 0.8; }
  }
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(-4px); }
    to { opacity: 1; transform: translateY(0); }
  }
  @keyframes shimmer {
    0% { background-position: -200% 0; }
    100% { background-position: 200% 0; }
  }
`;

// ==================== Sub-Components ====================

// Generate Configuration Panel
const GenerateConfigPanel: React.FC<{
  theme: 'dark' | 'light' | 'beige';
  options: GenerateOptions;
  modelTiers: Record<string, readonly { label: string; model: string }[]>;
  onOptionsChange: (options: GenerateOptions) => void;
  onGenerate: () => void;
  onResume?: () => void;
  isGenerating: boolean;
  canResume?: boolean;
  progress: number;
  statusMessage: string;
  trajectory?: Array<{
    timestamp: string;
    status: string;
    progress: number;
    step: string;
    message: string;
    error?: string | null;
    details?: Record<string, unknown> | null;
  }>;
  lastUpdateAt?: string | null;
  error?: string | null;
}> = ({ theme, options, modelTiers, onOptionsChange, onGenerate, onResume, isGenerating, canResume = false, progress, statusMessage, trajectory, lastUpdateAt, error }) => {
  const c = getColors(theme);

  if (isGenerating) {
    return (
      <GenerationProgress
        theme={theme}
        progress={progress}
        statusMessage={statusMessage}
        language={options.language}
        trajectory={trajectory}
        lastUpdateAt={lastUpdateAt}
      />
    );
  }

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      width: '100%',
      minHeight: '100%',
      justifyContent: 'center',
      fontFamily: "'Inter', " + FONTS.ui,
      background: c.bg.primary,
      transition: 'background 0.3s ease',
      overflow: 'auto',
      padding: '40px 32px',
      boxSizing: 'border-box',
    }}>
      {/* Hero illustration area */}
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        marginBottom: '32px',
      }}>
        {/* Decorative icon cluster */}
        <div style={{
          position: 'relative',
          width: '80px',
          height: '80px',
          marginBottom: '20px',
        }}>
          <div style={{
            width: '80px',
            height: '80px',
            borderRadius: '20px',
            background: `linear-gradient(135deg, ${c.accent.blueBg}, ${c.accent.purpleBg})`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            border: `1px solid ${c.border.secondary}`,
          }}>
            <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke={c.accent.blue} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
              <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
            </svg>
          </div>
          {/* Small floating badge */}
          <div style={{
            position: 'absolute',
            bottom: '-4px',
            right: '-4px',
            width: '28px',
            height: '28px',
            borderRadius: '8px',
            background: c.accent.greenBg,
            border: `2px solid ${c.bg.primary}`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={c.accent.green} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>
            </svg>
          </div>
        </div>

        <h2 style={{
          fontSize: '22px',
          fontWeight: '700',
          color: c.text.primary,
          margin: 0,
          letterSpacing: '-0.02em',
          textAlign: 'center',
        }}>
          Generate Documentation
        </h2>
        <p style={{
          fontSize: '14px',
          color: c.text.muted,
          margin: '6px 0 0',
          textAlign: 'center',
          lineHeight: '1.5',
          maxWidth: '420px',
        }}>
          Auto-generate structured documentation from your repository's knowledge graph
        </p>
      </div>

      {/* Main card */}
      <div style={{
        width: '100%',
        maxWidth: '560px',
      }}>
        {/* Error Message */}
        {error && (
          <div style={{
            padding: '12px 16px',
            background: canResume
              ? (theme === 'dark' ? 'rgba(96, 165, 250, 0.08)' : 'rgba(29, 78, 216, 0.06)')
              : (theme === 'dark' ? 'rgba(248, 81, 73, 0.08)' : 'rgba(207, 34, 46, 0.06)'),
            border: `1px solid ${canResume
              ? (theme === 'dark' ? 'rgba(96, 165, 250, 0.25)' : 'rgba(29, 78, 216, 0.15)')
              : (theme === 'dark' ? 'rgba(248, 81, 73, 0.2)' : 'rgba(207, 34, 46, 0.15)')}`,
            borderRadius: '10px',
            color: canResume
              ? (theme === 'dark' ? '#93c5fd' : '#1d4ed8')
              : (theme === 'dark' ? '#ff7b72' : '#cf222e'),
            fontSize: '13px',
            marginBottom: '16px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '10px',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0 }}>
                <circle cx="12" cy="12" r="10"/>
                <line x1="12" y1="8" x2="12" y2="12"/>
                <line x1="12" y1="16" x2="12.01" y2="16"/>
              </svg>
              <span>{error}</span>
            </div>
            {canResume && onResume && (
              <button
                onClick={onResume}
                style={{
                  border: 'none',
                  borderRadius: '8px',
                  padding: '8px 12px',
                  fontSize: '12px',
                  fontWeight: '600',
                  cursor: 'pointer',
                  color: '#fff',
                  background: theme === 'dark' ? '#2563eb' : '#1d4ed8',
                }}
              >
                Resume
              </button>
            )}
          </div>
        )}

        {/* Configuration card */}
        <div style={{
          width: '100%',
          background: c.bg.secondary,
          borderRadius: '14px',
          padding: '24px',
          marginBottom: '20px',
          border: `1px solid ${c.border.secondary}`,
        }}>
          <div style={{
            fontSize: '11px',
            fontWeight: '600',
            color: c.text.muted,
            marginBottom: '20px',
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
          }}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.6 }}>
              <circle cx="12" cy="12" r="3"/>
              <path d="M12 1v2m0 18v2M4.22 4.22l1.42 1.42m12.72 12.72l1.42 1.42M1 12h2m18 0h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>
            </svg>
            Configuration
          </div>

          {/* Options grid */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, 1fr)',
            gap: '20px 28px',
            width: '100%',
          }}>
            {/* Language */}
            <div>
              <div style={{
                fontSize: '13px',
                fontWeight: '500',
                color: c.text.primary,
                marginBottom: '10px',
              }}>
                Language
              </div>
              <SegmentedControl
                theme={theme}
                options={[
                  { value: 'zh', label: 'Chinese' },
                  { value: 'en', label: 'English' },
                ]}
                value={options.language}
                onChange={(v) => onOptionsChange({ ...options, language: v as 'zh' | 'en' })}
              />
            </div>

            {/* Depth */}
            <div>
              <div style={{
                fontSize: '13px',
                fontWeight: '500',
                color: c.text.primary,
                marginBottom: '10px',
              }}>
                Depth
              </div>
              <div style={{ display: 'flex', gap: '5px' }}>
                {[0, 1, 2, 3, 4].map((d) => (
                  <button
                    key={d}
                    onClick={() => onOptionsChange({ ...options, docDepth: d })}
                    style={{
                      width: '34px',
                      height: '34px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      background: options.docDepth === d ? c.accent.blue : 'transparent',
                      border: `1px solid ${options.docDepth === d ? c.accent.blue : c.border.primary}`,
                      borderRadius: '8px',
                      fontSize: '13px',
                      fontWeight: '600',
                      fontFamily: FONTS.mono,
                      cursor: 'pointer',
                      color: options.docDepth === d ? '#fff' : c.text.secondary,
                      transition: 'all 0.15s ease',
                    }}
                    onMouseEnter={(e) => {
                      if (options.docDepth !== d) {
                        e.currentTarget.style.borderColor = c.text.muted;
                        e.currentTarget.style.color = c.text.primary;
                      }
                    }}
                    onMouseLeave={(e) => {
                      if (options.docDepth !== d) {
                        e.currentTarget.style.borderColor = c.border.primary;
                        e.currentTarget.style.color = c.text.secondary;
                      }
                    }}
                  >
                    {d}
                  </button>
                ))}
              </div>
            </div>

            {/* Focus Area */}
            <div>
              <div style={{
                fontSize: '13px',
                fontWeight: '500',
                color: c.text.primary,
                marginBottom: '10px',
              }}>
                Focus
                <span style={{ color: c.text.muted, fontWeight: '400', marginLeft: '6px', fontSize: '12px' }}>
                  (optional)
                </span>
              </div>
              <textarea
                value={options.focus}
                onChange={(e) => onOptionsChange({ ...options, focus: e.target.value })}
                placeholder="API design, performance..."
                style={{
                  width: '100%',
                  minHeight: '34px',
                  height: '34px',
                  padding: '7px 12px',
                  background: c.bg.primary,
                  border: `1px solid ${c.border.primary}`,
                  borderRadius: '8px',
                  fontSize: '13px',
                  fontFamily: "'Inter', " + FONTS.ui,
                  color: c.text.primary,
                  resize: 'none',
                  outline: 'none',
                  transition: 'border-color 0.15s ease',
                }}
                onFocus={(e) => e.currentTarget.style.borderColor = c.accent.blue}
                onBlur={(e) => e.currentTarget.style.borderColor = c.border.primary}
              />
            </div>

            {/* Model Selection */}
            <div>
              <div style={{
                fontSize: '13px',
                fontWeight: '500',
                color: c.text.primary,
                marginBottom: '10px',
              }}>
                Model
              </div>
              <ModelCombobox
                value={options.model}
                onChange={(model) => onOptionsChange({ ...options, model })}
                theme={theme}
                tiers={modelTiers}
                placeholder="Select or type model..."
                style={{ width: '100%' }}
              />
            </div>
          </div>
        </div>

        {/* Generate Button - full width inside card area */}
        <button
          onClick={onGenerate}
          style={{
            width: '100%',
            padding: '12px 28px',
            background: c.accent.blue,
            border: 'none',
            borderRadius: '10px',
            fontSize: '14px',
            fontWeight: '600',
            fontFamily: "'Inter', " + FONTS.ui,
            letterSpacing: '-0.01em',
            color: '#ffffff',
            cursor: 'pointer',
            transition: 'all 0.2s ease',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '8px',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.transform = 'translateY(-1px)';
            e.currentTarget.style.boxShadow = `0 4px 16px ${c.accent.blueBg}`;
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.transform = 'translateY(0)';
            e.currentTarget.style.boxShadow = 'none';
          }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>
          </svg>
          Generate Documentation
        </button>

        <p style={{
          marginTop: '12px',
          fontSize: '12px',
          color: c.text.muted,
          textAlign: 'center',
        }}>
          Estimated time: ~2-5 minutes depending on repository size
        </p>
      </div>

      {/* Feature hints */}
      <div style={{
        display: 'flex',
        gap: '16px',
        marginTop: '8px',
        maxWidth: '560px',
        width: '100%',
      }}>
        {[
          { icon: 'M9 12l2 2 4-4', label: 'Knowledge graph analysis' },
          { icon: 'M12 3v18M3 12h18', label: 'Cross-reference linking' },
          { icon: 'M4 6h16M4 12h16M4 18h10', label: 'Multi-level depth' },
        ].map((feat, i) => (
          <div key={i} style={{
            flex: 1,
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            fontSize: '11px',
            color: c.text.muted,
          }}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={c.accent.green} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
              <path d={feat.icon}/>
            </svg>
            {feat.label}
          </div>
        ))}
      </div>

      <style>{keyframesStyle}</style>
    </div>
  );
};

// Option Row Component
const OptionRow: React.FC<{
  theme: 'dark' | 'light' | 'beige';
  label: string;
  hint?: string;
  children: React.ReactNode;
}> = ({ theme, label, hint, children }) => {
  const c = getColors(theme);
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <span style={{
          fontSize: '14px',
          fontWeight: '500',
          color: c.text.primary,
        }}>
          {label}
        </span>
        {hint && (
          <span style={{
            fontSize: '11px',
            padding: '2px 6px',
            background: c.accent.blueBg,
            color: c.accent.blue,
            borderRadius: '4px',
            fontWeight: '500',
          }}>
            {hint}
          </span>
        )}
      </div>
      {children}
    </div>
  );
};

// Segmented Control Component - Clean minimal style
const SegmentedControl: React.FC<{
  theme: 'dark' | 'light' | 'beige';
  options: Array<{ value: string; label: string }>;
  value: string;
  onChange: (value: string) => void;
}> = ({ theme, options, value, onChange }) => {
  const c = getColors(theme);
  return (
    <div style={{
      display: 'flex',
      background: c.bg.secondary,
      borderRadius: '6px',
      padding: '2px',
      gap: '2px',
      border: `1px solid ${c.border.secondary}`,
    }}>
      {options.map((opt) => (
        <button
          key={opt.value}
          onClick={() => onChange(opt.value)}
          style={{
            padding: '5px 12px',
            background: value === opt.value ? c.bg.primary : 'transparent',
            border: 'none',
            borderRadius: '4px',
            fontSize: '12px',
            fontWeight: '500',
            fontFamily: FONTS.ui,
            cursor: 'pointer',
            color: value === opt.value ? c.text.primary : c.text.muted,
            transition: 'all 0.15s ease',
            boxShadow: value === opt.value ? `0 1px 2px ${theme === 'dark' ? 'rgba(0,0,0,0.2)' : 'rgba(0,0,0,0.05)'}` : 'none',
          }}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
};

// Generation Progress Panel - Clean minimal style
const GenerationProgress: React.FC<{
  theme: 'dark' | 'light' | 'beige';
  progress: number;
  statusMessage: string;
  language: 'zh' | 'en';
  trajectory?: Array<{
    timestamp: string;
    status: string;
    progress: number;
    step: string;
    message: string;
    error?: string | null;
    details?: Record<string, unknown> | null;
  }>;
  lastUpdateAt?: string | null;
}> = ({ theme, progress, statusMessage, language, trajectory = [], lastUpdateAt = null }) => {
  const c = getColors(theme);

  const currentItem = useMemo(() => {
    if (statusMessage.includes('Generated:') || statusMessage.includes('generated:')) {
      const parts = statusMessage.split(':');
      if (parts.length >= 2) {
        return parts.slice(1).join(':').trim() || '';
      }
    }
    return '';
  }, [statusMessage]);

  const getPhaseInfo = () => {
    if (progress < 15) return { phase: language === 'zh' ? 'Initializing' : 'Initializing' };
    if (progress < 60) return { phase: language === 'zh' ? 'Modules' : 'Modules' };
    if (progress < 85) return { phase: language === 'zh' ? 'Packages' : 'Packages' };
    if (progress < 95) return { phase: language === 'zh' ? 'Overview' : 'Overview' };
    return { phase: language === 'zh' ? 'Finalizing' : 'Finalizing' };
  };

  const phaseInfo = getPhaseInfo();
  const recentTrajectory = trajectory.slice(-8);

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'flex-start',
      padding: '48px 24px',
      minHeight: '100%',
      fontFamily: FONTS.ui,
      background: c.bg.primary,
      overflow: 'auto',
    }}>
      {/* Spinner */}
      <div style={{
        width: '48px',
        height: '48px',
        border: `2px solid ${c.border.secondary}`,
        borderTopColor: c.accent.blue,
        borderRadius: '50%',
        animation: 'spin 0.8s linear infinite',
        marginBottom: '20px',
        flexShrink: 0,
      }} />

      {/* Title */}
      <h3 style={{
        fontSize: '18px',
        fontWeight: '600',
        color: c.text.primary,
        marginBottom: '8px',
        letterSpacing: '-0.01em',
      }}>
        {language === 'zh' ? 'Generating' : 'Generating'}
      </h3>

      {/* Phase */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        marginBottom: '24px',
      }}>
        <span style={{
          fontSize: '13px',
          color: c.text.muted,
        }}>
          {phaseInfo.phase}
        </span>
        <span style={{
          fontSize: '13px',
          fontFamily: FONTS.mono,
          color: c.accent.blue,
          fontWeight: '500',
        }}>
          {progress}%
        </span>
      </div>

      {/* Progress Bar */}
      <div style={{ width: '100%', maxWidth: '320px', marginBottom: '16px' }}>
        <div style={{
          height: '4px',
          background: c.bg.tertiary,
          borderRadius: '2px',
          overflow: 'hidden',
        }}>
          <div style={{
            width: `${progress}%`,
            height: '100%',
            background: c.accent.blue,
            borderRadius: '2px',
            transition: 'width 0.3s ease-out',
          }} />
        </div>
      </div>

      {/* Status Message */}
      <p style={{
        fontSize: '13px',
        color: c.text.muted,
        textAlign: 'center',
        maxWidth: '360px',
        minHeight: '20px',
      }}>
        {statusMessage || (language === 'zh' ? 'Initializing...' : 'Initializing...')}
      </p>

      {lastUpdateAt && (
        <div style={{
          marginTop: '8px',
          fontSize: '11px',
          color: c.text.muted,
        }}>
          {language === 'zh' ? 'Last update' : 'Last update'} {formatRelativeTime(lastUpdateAt)}
        </div>
      )}

      {/* Current Item */}
      {currentItem && (
        <div style={{
          marginTop: '16px',
          padding: '8px 12px',
          background: c.bg.secondary,
          borderRadius: '6px',
          border: `1px solid ${c.border.secondary}`,
        }}>
          <code style={{
            fontSize: '12px',
            fontFamily: FONTS.mono,
            color: c.text.secondary,
          }}>
            {currentItem}
          </code>
        </div>
      )}

      {recentTrajectory.length > 0 && (
        <div style={{
          width: '100%',
          maxWidth: '560px',
          marginTop: '24px',
          paddingTop: '16px',
          borderTop: `1px solid ${c.border.primary}`,
          display: 'flex',
          flexDirection: 'column',
          gap: '10px',
        }}>
          {recentTrajectory.map((event, index) => {
            const detailSummary = summarizeTrajectoryDetails(event.details);
            const toolCalls = extractToolCalls(event.details);
            return (
              <div
                key={`${event.timestamp}-${index}`}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '76px 1fr',
                  gap: '10px',
                  alignItems: 'start',
                }}
              >
                <div style={{
                  fontSize: '11px',
                  color: c.text.muted,
                  fontFamily: FONTS.mono,
                  paddingTop: '1px',
                }}>
                  {formatClockTime(event.timestamp)}
                </div>
                <div>
                  <div style={{
                    fontSize: '12px',
                    color: c.text.primary,
                    lineHeight: 1.45,
                  }}>
                    {event.message || event.step || event.status}
                  </div>
                  <div style={{
                    marginTop: '2px',
                    fontSize: '11px',
                    color: c.text.muted,
                  }}>
                    {event.status} • {event.progress}%{event.step ? ` • ${event.step}` : ''}
                  </div>
                  {detailSummary && (
                    <div style={{
                      marginTop: '2px',
                      fontSize: '11px',
                      color: c.text.secondary,
                    }}>
                      {detailSummary}
                    </div>
                  )}
                  {toolCalls.length > 0 && (
                    <div style={{
                      marginTop: '6px',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '6px',
                    }}>
                      {toolCalls.map((toolCall, toolIndex) => (
                        <div key={`${toolCall.display}-${toolIndex}`}>
                          <code style={{
                            fontSize: '11px',
                            fontFamily: FONTS.mono,
                            color: c.accent.blue,
                            background: c.accent.blueBg,
                            padding: '2px 6px',
                            borderRadius: '6px',
                            display: 'inline-block',
                          }}>
                            {toolCall.display}
                          </code>
                          {toolCall.resultPreview && (
                            <div style={{
                              marginTop: '4px',
                              fontSize: '11px',
                              color: c.text.secondary,
                              lineHeight: 1.4,
                            }}>
                              {toolCall.resultPreview}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                  {event.error && (
                    <div style={{
                      marginTop: '2px',
                      fontSize: '11px',
                      color: theme === 'dark' ? '#ff7b72' : '#cf222e',
                    }}>
                      {event.error}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <style>{keyframesStyle}</style>
    </div>
  );
};

// Regenerate Modal - Clean minimal style
const RegenerateModal: React.FC<{
  theme: 'dark' | 'light' | 'beige';
  isOpen: boolean;
  onClose: () => void;
  options: GenerateOptions;
  modelTiers: Record<string, readonly { label: string; model: string }[]>;
  onOptionsChange: (options: GenerateOptions) => void;
  onConfirm: () => void;
}> = ({ theme, isOpen, onClose, options, modelTiers, onOptionsChange, onConfirm }) => {
  const c = getColors(theme);
  if (!isOpen) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        style={{
          position: 'fixed',
          inset: 0,
          background: getBackdropBg(theme),
          backdropFilter: 'blur(4px)',
          zIndex: 10000,
        }}
        onClick={onClose}
      />

      {/* Modal */}
      <div style={{
        position: 'fixed',
        top: '50%',
        left: '50%',
        transform: 'translate(-50%, -50%)',
        background: c.bg.primary,
        border: `1px solid ${c.border.primary}`,
        borderRadius: '12px',
        boxShadow: getShadowStyle(theme, 'heavy'),
        zIndex: 10001,
        width: '380px',
        maxWidth: '90vw',
        fontFamily: FONTS.ui,
        animation: 'fadeIn 0.15s ease-out',
      }}>
        {/* Header */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '16px 20px',
          borderBottom: `1px solid ${c.border.secondary}`,
        }}>
          <h3 style={{
            fontSize: '15px',
            fontWeight: '600',
            color: c.text.primary,
            margin: 0,
          }}>
            {options.language === 'zh' ? 'Regenerate' : 'Regenerate'}
          </h3>
          <button
            onClick={onClose}
            style={{
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              width: '24px',
              height: '24px',
              borderRadius: '4px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: c.text.muted,
              transition: 'all 0.15s',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = c.bg.secondary;
              e.currentTarget.style.color = c.text.primary;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'transparent';
              e.currentTarget.style.color = c.text.muted;
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18"/>
              <line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>

        {/* Content */}
        <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
          {/* Info */}
          <div style={{
            fontSize: '12px',
            color: c.text.muted,
            padding: '8px 10px',
            background: c.bg.secondary,
            borderRadius: '6px',
            lineHeight: '1.4',
          }}>
            {options.language === 'zh'
              ? 'Create new version, existing versions preserved'
              : 'Creates new version, existing preserved'
            }
          </div>

          {/* Language */}
          <OptionRow theme={theme} label={options.language === 'zh' ? 'Language' : 'Language'}>
            <SegmentedControl
              theme={theme}
              options={[
                { value: 'zh', label: 'Chinese' },
                { value: 'en', label: 'EN' },
              ]}
              value={options.language}
              onChange={(v) => onOptionsChange({ ...options, language: v as 'zh' | 'en' })}
            />
          </OptionRow>

          {/* Depth */}
          <OptionRow theme={theme} label={options.language === 'zh' ? 'Depth' : 'Depth'}>
            <div style={{ display: 'flex', gap: '4px' }}>
              {[0, 1, 2, 3, 4].map((d) => (
                <button
                  key={d}
                  onClick={() => onOptionsChange({ ...options, docDepth: d })}
                  style={{
                    width: '28px',
                    height: '28px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    background: options.docDepth === d ? c.accent.blue : 'transparent',
                    border: `1px solid ${options.docDepth === d ? c.accent.blue : c.border.primary}`,
                    borderRadius: '5px',
                    fontSize: '12px',
                    fontWeight: '500',
                    fontFamily: FONTS.mono,
                    cursor: 'pointer',
                    color: options.docDepth === d ? '#fff' : c.text.secondary,
                    transition: 'all 0.15s',
                  }}
                  onMouseEnter={(e) => {
                    if (options.docDepth !== d) {
                      e.currentTarget.style.borderColor = c.text.muted;
                    }
                  }}
                  onMouseLeave={(e) => {
                    if (options.docDepth !== d) {
                      e.currentTarget.style.borderColor = c.border.primary;
                    }
                  }}
                >
                  {d}
                </button>
              ))}
            </div>
          </OptionRow>

          {/* Focus Area */}
          <div>
            <div style={{
              fontSize: '13px',
              fontWeight: '500',
              color: c.text.primary,
              marginBottom: '6px',
            }}>
              {options.language === 'zh' ? 'Focus' : 'Focus'}
              <span style={{ color: c.text.muted, fontWeight: '400', marginLeft: '4px' }}>
                ({options.language === 'zh' ? 'optional' : 'optional'})
              </span>
            </div>
            <textarea
              value={options.focus}
              onChange={(e) => onOptionsChange({ ...options, focus: e.target.value })}
              placeholder={options.language === 'zh'
                ? 'API design, performance...'
                : 'API design, performance...'
              }
              style={{
                width: '100%',
                minHeight: '56px',
                padding: '8px 10px',
                background: c.bg.secondary,
                border: `1px solid ${c.border.primary}`,
                borderRadius: '6px',
                fontSize: '13px',
                fontFamily: FONTS.ui,
                color: c.text.primary,
                resize: 'vertical',
                outline: 'none',
                transition: 'border-color 0.15s',
              }}
              onFocus={(e) => e.currentTarget.style.borderColor = c.accent.blue}
              onBlur={(e) => e.currentTarget.style.borderColor = c.border.primary}
            />
          </div>

          {/* Model Selection */}
          <div>
            <div style={{
              fontSize: '13px',
              fontWeight: '500',
              color: c.text.primary,
              marginBottom: '6px',
            }}>
              {options.language === 'zh' ? 'Model' : 'Model'}
            </div>
            <ModelCombobox
              value={options.model}
              onChange={(model) => onOptionsChange({ ...options, model })}
              theme={theme}
              tiers={modelTiers}
              placeholder="Select or type model..."
              style={{ width: '100%' }}
            />
          </div>
        </div>

        {/* Footer */}
        <div style={{
          display: 'flex',
          justifyContent: 'flex-end',
          gap: '8px',
          padding: '12px 20px',
          borderTop: `1px solid ${c.border.secondary}`,
        }}>
          <button
            onClick={onClose}
            style={{
              padding: '8px 14px',
              background: 'transparent',
              border: `1px solid ${c.border.primary}`,
              borderRadius: '6px',
              fontSize: '13px',
              fontWeight: '500',
              fontFamily: FONTS.ui,
              cursor: 'pointer',
              color: c.text.primary,
              transition: 'all 0.15s',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = c.bg.secondary;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'transparent';
            }}
          >
            {options.language === 'zh' ? 'Cancel' : 'Cancel'}
          </button>
          <button
            onClick={() => { onConfirm(); onClose(); }}
            style={{
              padding: '8px 14px',
              background: c.accent.blue,
              border: 'none',
              borderRadius: '6px',
              fontSize: '13px',
              fontWeight: '500',
              fontFamily: FONTS.ui,
              cursor: 'pointer',
              color: '#fff',
              transition: 'all 0.15s',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.opacity = '0.9';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.opacity = '1';
            }}
          >
            {options.language === 'zh' ? 'Generate' : 'Generate'}
          </button>
        </div>
      </div>

      <style>{keyframesStyle}</style>
    </>
  );
};

// Feedback Modal for Section Regeneration
const FeedbackModal: React.FC<{
  isOpen: boolean;
  onClose: () => void;
  onSubmit: () => void;
  sectionTitle: string;
  theme: 'dark' | 'light' | 'beige';
  isSubmitting: boolean;
  error: string | null;
  feedback: string;
  onFeedbackChange: (value: string) => void;
  language: 'zh' | 'en';
}> = ({ isOpen, onClose, onSubmit, sectionTitle, theme, isSubmitting, error, feedback, onFeedbackChange, language }) => {
  const c = getColors(theme);

  if (!isOpen) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        style={{
          position: 'fixed',
          inset: 0,
          background: getBackdropBg(theme),
          backdropFilter: 'blur(4px)',
          zIndex: 10000,
        }}
        onClick={onClose}
      />

      {/* Modal */}
      <div
        style={{
          position: 'fixed',
          top: '50%',
          left: '50%',
          transform: 'translate(-50%, -50%)',
          background: c.bg.primary,
          border: `1px solid ${c.border.primary}`,
          borderRadius: '12px',
          boxShadow: getShadowStyle(theme, 'heavy'),
          zIndex: 10001,
          width: '500px',
          maxWidth: '90vw',
          maxHeight: '80vh',
          overflow: 'auto',
          fontFamily: FONTS.ui,
          animation: 'fadeIn 0.15s ease-out',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          style={{
            padding: '16px 20px',
            borderBottom: `1px solid ${c.border.primary}`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <h3
            style={{
              margin: 0,
              fontSize: '15px',
              fontWeight: '600',
              color: c.text.primary,
            }}
          >
            {language === 'zh' ? '重新生成: ' : 'Regenerate: '}{sectionTitle}
          </h3>
          <button
            onClick={onClose}
            style={{
              background: 'transparent',
              border: 'none',
              color: c.text.secondary,
              cursor: 'pointer',
              fontSize: '20px',
              padding: '0',
              width: '24px',
              height: '24px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            ×
          </button>
        </div>

        {/* Content */}
        <div style={{ padding: '20px' }}>
          <label
            style={{
              display: 'block',
              marginBottom: '8px',
              fontSize: '13px',
              fontWeight: '500',
              color: c.text.primary,
            }}
          >
            {language === 'zh' ? '反馈意见' : 'Feedback for regeneration'}
          </label>
          <textarea
            value={feedback}
            onChange={(e) => onFeedbackChange(e.target.value)}
            placeholder={
              language === 'zh'
                ? '例如: 添加更多关于缓存机制的细节，重点关注性能优化...'
                : 'e.g., Add more details about the caching mechanism, Focus on performance optimization...'
            }
            style={{
              width: '100%',
              minHeight: '100px',
              padding: '10px 12px',
              fontSize: '13px',
              fontFamily: FONTS.ui,
              background: c.bg.secondary,
              border: `1px solid ${c.border.primary}`,
              borderRadius: '8px',
              color: c.text.primary,
              resize: 'vertical',
              outline: 'none',
            }}
            onFocus={(e) => e.currentTarget.style.borderColor = c.accent.blue}
            onBlur={(e) => e.currentTarget.style.borderColor = c.border.primary}
          />

          {error && (
            <div
              style={{
                marginTop: '12px',
                padding: '10px 12px',
                background: theme === 'dark' ? 'rgba(248, 81, 73, 0.08)' : 'rgba(207, 34, 46, 0.06)',
                border: `1px solid ${theme === 'dark' ? 'rgba(248, 81, 73, 0.2)' : 'rgba(207, 34, 46, 0.15)'}`,
                borderRadius: '8px',
                color: theme === 'dark' ? '#ff7b72' : '#cf222e',
                fontSize: '12px',
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
              }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10"/>
                <line x1="12" y1="8" x2="12" y2="12"/>
                <line x1="12" y1="16" x2="12.01" y2="16"/>
              </svg>
              {error}
            </div>
          )}

          <div style={{
            marginTop: '12px',
            fontSize: '11px',
            color: c.text.muted,
            lineHeight: '1.4',
          }}>
            {language === 'zh'
              ? '基于之前的生成上下文和您的反馈重新生成此部分。'
              : 'Regenerate this section based on previous context and your feedback.'
            }
          </div>
        </div>

        {/* Footer */}
        <div
          style={{
            padding: '12px 20px',
            borderTop: `1px solid ${c.border.primary}`,
            display: 'flex',
            gap: '8px',
            justifyContent: 'flex-end',
          }}
        >
          <button
            onClick={onClose}
            style={{
              padding: '7px 14px',
              fontSize: '13px',
              fontWeight: '500',
              background: 'transparent',
              border: `1px solid ${c.border.primary}`,
              borderRadius: '6px',
              color: c.text.secondary,
              cursor: 'pointer',
            }}
            onMouseEnter={(e) => e.currentTarget.style.background = c.bg.secondary}
            onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
          >
            {language === 'zh' ? '取消' : 'Cancel'}
          </button>
          <button
            onClick={onSubmit}
            disabled={isSubmitting || !feedback.trim()}
            style={{
              padding: '7px 14px',
              fontSize: '13px',
              fontWeight: '500',
              background: isSubmitting || !feedback.trim() ? c.bg.tertiary : c.accent.blue,
              border: 'none',
              borderRadius: '6px',
              color: '#fff',
              cursor: isSubmitting || !feedback.trim() ? 'not-allowed' : 'pointer',
              opacity: isSubmitting || !feedback.trim() ? 0.6 : 1,
            }}
          >
            {isSubmitting
              ? (language === 'zh' ? '生成中...' : 'Regenerating...')
              : (language === 'zh' ? '重新生成' : 'Regenerate')
            }
          </button>
        </div>
      </div>

      <style>{keyframesStyle}</style>
    </>
  );
};

// Version Manager Modal - Full screen modal for managing versions
// Uses React Portal to render at document.body level to avoid z-index issues
const VersionManagerModal: React.FC<{
  theme: 'dark' | 'light' | 'beige';
  isOpen: boolean;
  onClose: () => void;
  repoName: string;
  versions: VersionInfo[];
  currentVersionId?: string;
  defaultVersionId?: string;  // The version marked as default in _meta.json
  onVersionChange: (versionId: string) => void;
  onVersionsUpdate: () => void;  // Callback to refresh versions list
  language: 'zh' | 'en';
}> = ({ theme, isOpen, onClose, repoName, versions, currentVersionId, defaultVersionId, onVersionChange, onVersionsUpdate, language }) => {
  const c = getColors(theme);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mounted, setMounted] = useState(false);

  // Ensure we're on the client side for Portal
  useEffect(() => {
    setMounted(true);
  }, []);

  if (!isOpen || !mounted) return null;

  const formatDate = (dateStr: string) => {
    try {
      const date = new Date(dateStr);
      return date.toLocaleDateString(language === 'zh' ? 'zh-CN' : 'en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return dateStr;
    }
  };

  // Mode label removed - now always using detailed mode

  const handleSetDefault = async (versionId: string) => {
    setLoading(versionId);
    setError(null);
    try {
      const response = await apiFetch(`/api/overview/${repoName}/version/current`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version_id: versionId }),
      });
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || 'Failed to set default version');
      }
      onVersionsUpdate();
    } catch (err: any) {
      setError(err.message || 'Failed to set default version');
    } finally {
      setLoading(null);
    }
  };

  const handleDelete = async (versionId: string) => {
    setLoading(versionId);
    setError(null);
    try {
      const response = await apiFetch(`/api/overview/${repoName}/version/${versionId}`, {
        method: 'DELETE',
      });
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || 'Failed to delete version');
      }
      setDeleteConfirm(null);
      onVersionsUpdate();
    } catch (err: any) {
      setError(err.message || 'Failed to delete version');
    } finally {
      setLoading(null);
    }
  };

  const modalContent = (
    <>
      {/* Backdrop */}
      <div
        style={{
          position: 'fixed',
          inset: 0,
          background: getBackdropBg(theme),
          backdropFilter: 'blur(4px)',
          zIndex: 100000,
        }}
        onClick={onClose}
      />

      {/* Modal - Clean minimal style */}
      <div style={{
        position: 'fixed',
        top: '50%',
        left: '50%',
        transform: 'translate(-50%, -50%)',
        background: c.bg.primary,
        border: `1px solid ${c.border.primary}`,
        borderRadius: '12px',
        boxShadow: getShadowStyle(theme, 'heavy'),
        zIndex: 100001,
        width: '480px',
        maxWidth: '90vw',
        maxHeight: '80vh',
        display: 'flex',
        flexDirection: 'column',
        fontFamily: FONTS.ui,
        animation: 'fadeIn 0.15s ease-out',
      }}>
        {/* Header - simplified */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '16px 20px',
          borderBottom: `1px solid ${c.border.secondary}`,
          flexShrink: 0,
        }}>
          <div>
            <h2 style={{
              fontSize: '15px',
              fontWeight: '600',
              color: c.text.primary,
              margin: 0,
            }}>
              {language === 'zh' ? 'Versions' : 'Versions'}
            </h2>
            <p style={{
              fontSize: '12px',
              color: c.text.muted,
              margin: '2px 0 0 0',
            }}>
              {versions.length} {language === 'zh' ? 'available' : 'available'}
            </p>
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              width: '24px',
              height: '24px',
              borderRadius: '4px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: c.text.muted,
              transition: 'all 0.15s',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = c.bg.secondary;
              e.currentTarget.style.color = c.text.primary;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'transparent';
              e.currentTarget.style.color = c.text.muted;
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18"/>
              <line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>

        {/* Error message */}
        {error && (
          <div style={{
            margin: '12px 20px 0',
            padding: '10px 12px',
            background: theme === 'dark' ? 'rgba(248, 81, 73, 0.08)' : 'rgba(207, 34, 46, 0.06)',
            border: `1px solid ${theme === 'dark' ? 'rgba(248, 81, 73, 0.2)' : 'rgba(207, 34, 46, 0.15)'}`,
            borderRadius: '6px',
            color: theme === 'dark' ? '#ff7b72' : '#cf222e',
            fontSize: '12px',
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
          }}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10"/>
              <line x1="12" y1="8" x2="12" y2="12"/>
              <line x1="12" y1="16" x2="12.01" y2="16"/>
            </svg>
            {error}
            <button
              onClick={() => setError(null)}
              style={{
                marginLeft: 'auto',
                background: 'transparent',
                border: 'none',
                color: 'inherit',
                cursor: 'pointer',
                padding: '2px',
              }}
            >
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="18" y1="6" x2="6" y2="18"/>
                <line x1="6" y1="6" x2="18" y2="18"/>
              </svg>
            </button>
          </div>
        )}

        {/* Version list - Clean table style */}
        <div style={{
          flex: 1,
          overflow: 'auto',
          padding: '12px 20px',
        }}>
          {versions.map((version, idx) => {
            const isDefault = version.version_id === defaultVersionId;
            const isCurrent = version.version_id === currentVersionId;
            const isDeleting = deleteConfirm === version.version_id;
            const isLoading = loading === version.version_id;

            return (
              <div
                key={version.version_id}
                style={{
                  padding: '12px 0',
                  borderBottom: idx < versions.length - 1 ? `1px solid ${c.border.secondary}` : 'none',
                  transition: 'all 0.15s',
                }}
              >
                {/* Version row - Clean inline layout */}
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  {/* Left: Info */}
                  <div style={{ flex: 1 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                      <span style={{
                        fontSize: '13px',
                        fontWeight: '500',
                        color: c.text.primary,
                      }}>
                        {formatDate(version.generated_at)}
                      </span>
                      {/* Default indicator */}
                      {isDefault && (
                        <span style={{
                          fontSize: '11px',
                          color: c.accent.blue,
                          fontWeight: '500',
                        }}>
                          {language === 'zh' ? 'default' : 'default'}
                        </span>
                      )}
                      {/* Current indicator */}
                      {isCurrent && !isDefault && (
                        <span style={{
                          fontSize: '11px',
                          color: c.accent.green,
                          fontWeight: '500',
                        }}>
                          {language === 'zh' ? 'viewing' : 'viewing'}
                        </span>
                      )}
                    </div>
                    <div style={{
                      fontSize: '11px',
                      color: c.text.muted,
                      fontFamily: FONTS.mono,
                    }}>
                      depth {version.doc_depth}
                      {version.statistics?.sections_generated && ` · ${version.statistics.sections_generated} sections`}
                    </div>
                  </div>

                  {/* Right: Actions */}
                  {isDeleting ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span style={{ fontSize: '12px', color: theme === 'dark' ? '#ff7b72' : '#cf222e' }}>
                        {language === 'zh' ? 'Delete?' : 'Delete?'}
                      </span>
                      <button
                        onClick={() => setDeleteConfirm(null)}
                        disabled={isLoading}
                        style={{
                          padding: '4px 8px',
                          background: 'transparent',
                          border: 'none',
                          fontSize: '12px',
                          cursor: 'pointer',
                          color: c.text.muted,
                        }}
                      >
                        {language === 'zh' ? 'No' : 'No'}
                      </button>
                      <button
                        onClick={() => handleDelete(version.version_id)}
                        disabled={isLoading}
                        style={{
                          padding: '4px 8px',
                          background: 'transparent',
                          border: 'none',
                          fontSize: '12px',
                          cursor: isLoading ? 'not-allowed' : 'pointer',
                          color: theme === 'dark' ? '#ff7b72' : '#cf222e',
                          fontWeight: '500',
                          opacity: isLoading ? 0.6 : 1,
                        }}
                      >
                        {isLoading ? '...' : (language === 'zh' ? 'Yes' : 'Yes')}
                      </button>
                    </div>
                  ) : (
                    <div style={{ display: 'flex', gap: '4px' }}>
                      {/* View button - text only */}
                      {!isCurrent && (
                        <button
                          onClick={() => { onVersionChange(version.version_id); onClose(); }}
                          style={{
                            padding: '4px 10px',
                            background: 'transparent',
                            border: 'none',
                            borderRadius: '4px',
                            fontSize: '12px',
                            fontWeight: '500',
                            cursor: 'pointer',
                            color: c.accent.blue,
                            transition: 'all 0.15s',
                          }}
                          onMouseEnter={(e) => e.currentTarget.style.background = c.bg.secondary}
                          onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
                        >
                          {language === 'zh' ? 'View' : 'View'}
                        </button>
                      )}

                      {/* Set as default - text only */}
                      {!isDefault && (
                        <button
                          onClick={() => handleSetDefault(version.version_id)}
                          disabled={isLoading}
                          style={{
                            padding: '4px 10px',
                            background: 'transparent',
                            border: 'none',
                            borderRadius: '4px',
                            fontSize: '12px',
                            fontWeight: '500',
                            cursor: isLoading ? 'not-allowed' : 'pointer',
                            color: c.text.muted,
                            transition: 'all 0.15s',
                            opacity: isLoading ? 0.6 : 1,
                          }}
                          onMouseEnter={(e) => {
                            if (!isLoading) {
                              e.currentTarget.style.background = c.bg.secondary;
                              e.currentTarget.style.color = c.text.primary;
                            }
                          }}
                          onMouseLeave={(e) => {
                            e.currentTarget.style.background = 'transparent';
                            e.currentTarget.style.color = c.text.muted;
                          }}
                        >
                          {isLoading ? '...' : (language === 'zh' ? 'Set default' : 'Set default')}
                        </button>
                      )}

                      {/* Delete - text only */}
                      {versions.length > 1 && (
                        <button
                          onClick={() => setDeleteConfirm(version.version_id)}
                          style={{
                            padding: '4px 10px',
                            background: 'transparent',
                            border: 'none',
                            borderRadius: '4px',
                            fontSize: '12px',
                            fontWeight: '500',
                            cursor: 'pointer',
                            color: c.text.muted,
                            transition: 'all 0.15s',
                          }}
                          onMouseEnter={(e) => {
                            e.currentTarget.style.background = theme === 'dark' ? 'rgba(248, 81, 73, 0.1)' : 'rgba(207, 34, 46, 0.08)';
                            e.currentTarget.style.color = theme === 'dark' ? '#ff7b72' : '#cf222e';
                          }}
                          onMouseLeave={(e) => {
                            e.currentTarget.style.background = 'transparent';
                            e.currentTarget.style.color = c.text.muted;
                          }}
                        >
                          {language === 'zh' ? 'Delete' : 'Delete'}
                        </button>
                      )}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        {/* Footer - simplified */}
        <div style={{
          padding: '12px 20px',
          borderTop: `1px solid ${c.border.secondary}`,
          display: 'flex',
          justifyContent: 'flex-end',
          flexShrink: 0,
        }}>
          <button
            onClick={onClose}
            style={{
              padding: '6px 14px',
              background: 'transparent',
              border: `1px solid ${c.border.primary}`,
              borderRadius: '6px',
              fontSize: '13px',
              fontWeight: '500',
              cursor: 'pointer',
              color: c.text.primary,
              transition: 'all 0.15s',
            }}
            onMouseEnter={(e) => e.currentTarget.style.background = c.bg.secondary}
            onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
          >
            {language === 'zh' ? 'Done' : 'Done'}
          </button>
        </div>
      </div>

      <style>{keyframesStyle}</style>
    </>
  );

  // Use Portal to render modal at document.body level
  return createPortal(modalContent, document.body);
};

// Version Selector (Icon Button Style) - Now opens VersionManagerModal
const VersionSelector: React.FC<{
  theme: 'dark' | 'light' | 'beige';
  versions: VersionInfo[];
  currentVersionId?: string;
  defaultVersionId?: string;
  repoName: string;
  onVersionChange: (versionId: string) => void;
  onVersionsUpdate: () => void;
  language: 'zh' | 'en';
}> = ({ theme, versions, currentVersionId, defaultVersionId, repoName, onVersionChange, onVersionsUpdate, language }) => {
  const [isOpen, setIsOpen] = useState(false);
  const c = getColors(theme);

  if (!versions || versions.length === 0) return null;

  return (
    <>
      {/* Icon button trigger */}
      <button
        onClick={() => setIsOpen(true)}
        title={language === 'zh' ? 'Version Manager' : 'Version Manager'}
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: '32px',
          height: '32px',
          background: 'transparent',
          border: 'none',
          borderRadius: '6px',
          cursor: 'pointer',
          color: c.text.secondary,
          transition: 'all 0.15s',
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = c.bg.secondary;
          e.currentTarget.style.color = c.text.primary;
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = 'transparent';
          e.currentTarget.style.color = c.text.secondary;
        }}
      >
        {/* Clock/History icon */}
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="10"/>
          <polyline points="12,6 12,12 16,14"/>
        </svg>
      </button>

      {/* Version Manager Modal */}
      <VersionManagerModal
        theme={theme}
        isOpen={isOpen}
        onClose={() => setIsOpen(false)}
        repoName={repoName}
        versions={versions}
        currentVersionId={currentVersionId}
        defaultVersionId={defaultVersionId}
        onVersionChange={onVersionChange}
        onVersionsUpdate={onVersionsUpdate}
        language={language}
      />
    </>
  );
};

// Markdown Content Wrapper
const MarkdownContent: React.FC<{
  content: string;
  onRegenerateSection?: (sectionId: string, sectionTitle: string, path: string) => void;
  sectionFiles?: string[];
  regenerationEnabled?: boolean;
  repoName?: string;
  onNavigateToNode?: (qualifiedName: string) => void;
}> = ({ content, onRegenerateSection, sectionFiles, regenerationEnabled, repoName, onNavigateToNode }) => {
  return (
    <div className="markdown-content" style={{ width: '100%' }}>
      <style>{`
        /* Force all markdown content to respect container width */
        .markdown-content,
        .markdown-content * {
          max-width: 100% !important;
          box-sizing: border-box !important;
        }

        /* Ensure mermaid wrapper doesn't overflow */
        .markdown-content > div > div[style*="justify-content: center"],
        .markdown-content > div > div[style*="display: block"] {
          max-width: 100% !important;
          width: 100% !important;
          overflow-x: auto !important;
        }

        /* Ensure SVG doesn't break layout */
        .markdown-content .interactive-mermaid-container,
        .markdown-content .interactive-mermaid-container svg,
        .markdown-content .interactive-mermaid-container * {
          max-width: 100% !important;
        }

        /* Force prose container to respect width */
        .markdown-content .prose,
        .markdown-content .doc-card {
          max-width: 100% !important;
          width: 100% !important;
          overflow-x: auto !important;
        }

        /* Ensure non-mermaid SVGs scale properly on small screens */
        .markdown-content svg:not(.interactive-mermaid-container svg) {
          max-width: 100% !important;
        }
      `}</style>
      <Suspense fallback={
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '60px 20px',
          color: '#8b949e',
          fontSize: '14px',
        }}>
          Loading document viewer...
        </div>
      }>
        <WikiDoc
          markdown={content}
          onRegenerateSection={regenerationEnabled ? onRegenerateSection : undefined}
          sectionFiles={sectionFiles}
          repoName={repoName}
          onNavigateToNode={onNavigateToNode}
        />
      </Suspense>
    </div>
  );
};

// Left Navigation
export const LeftNavigation: React.FC<{
  theme: 'dark' | 'light' | 'beige';
  tree: TreeNode[];
  currentPath: string;
  onNavigate: (path: string) => void;
  isCollapsed: boolean;
  onToggle: () => void;
}> = ({ theme, tree, currentPath, onNavigate, isCollapsed, onToggle }) => {
  const c = getColors(theme);
  const [expandedItems, setExpandedItems] = useState<Set<string>>(() => {
    const initial = new Set<string>();
    tree.forEach(node => {
      if (node.path) initial.add(node.path);
      else if (node.name) initial.add(node.name);
    });
    return initial;
  });

  useEffect(() => {
    if (!currentPath) return;
    const findParents = (nodes: TreeNode[], target: string, parents: string[] = []): string[] => {
      for (const node of nodes) {
        const nodePath = node.path || node.name;
        if (nodePath === target) return parents;
        if (node.children && node.children.length > 0) {
          const result = findParents(node.children, target, [...parents, nodePath]);
          if (result.length > 0) return result;
        }
      }
      return [];
    };
    const parents = findParents(tree, currentPath);
    if (parents.length > 0) {
      setExpandedItems(prev => {
        const newSet = new Set(prev);
        parents.forEach(p => newSet.add(p));
        return newSet;
      });
    }
  }, [currentPath, tree]);

  const toggleExpand = (path: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setExpandedItems(prev => {
      const newSet = new Set(prev);
      if (newSet.has(path)) newSet.delete(path);
      else newSet.add(path);
      return newSet;
    });
  };

  const renderTreeNode = (node: TreeNode, depth: number = 0, siblingIndex: number = 0): React.ReactNode => {
    const nodePath = node.path || node.name;
    const isActive = currentPath === nodePath;
    const hasChildren = node.children && node.children.length > 0;
    const isExpanded = expandedItems.has(nodePath);
    const isClickable = node.path !== null;
    const indent = 6 + depth * 14;

    // Section numbering: top-level "1." "2.", nested "·"
    const prefix = depth === 0 ? `${siblingIndex + 1}.` : '';

    return (
      <div key={nodePath + '-' + depth}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '5px',
            padding: '5px 8px',
            paddingLeft: `${indent}px`,
            borderRadius: '0 7px 7px 0',
            cursor: isClickable ? 'pointer' : 'default',
            background: isActive ? c.accent.blueBg : 'transparent',
            color: isActive ? c.accent.blue : c.text.primary,
            marginBottom: '1px',
            transition: 'all 0.12s ease',
            opacity: isClickable ? 1 : 0.65,
            position: 'relative',
            borderLeft: isActive ? `2px solid ${c.accent.blue}` : '2px solid transparent',
          }}
          onClick={() => isClickable && node.path && onNavigate(node.path)}
          onMouseEnter={(e) => {
            if (!isActive && isClickable) {
              e.currentTarget.style.background = c.bg.secondary;
              e.currentTarget.style.color = c.text.primary;
            }
          }}
          onMouseLeave={(e) => {
            if (!isActive) {
              e.currentTarget.style.background = 'transparent';
              e.currentTarget.style.color = isActive ? c.accent.blue : c.text.primary;
            }
          }}
        >
          {/* Prefix number or dot */}
          {depth === 0 ? (
            <span style={{
              fontSize: '11px',
              fontWeight: 600,
              fontFamily: FONTS.mono,
              color: isActive ? c.accent.blue : c.text.muted,
              minWidth: '18px',
              textAlign: 'right',
              flexShrink: 0,
              opacity: 0.7,
            }}>
              {prefix}
            </span>
          ) : (
            <span style={{
              minWidth: '10px',
              flexShrink: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}>
              <span style={{
                width: '3px',
                height: '3px',
                borderRadius: '50%',
                background: isActive ? c.accent.blue : c.border.muted,
                opacity: 0.5,
              }} />
            </span>
          )}
          {hasChildren && (
            <button
              onClick={(e) => toggleExpand(nodePath, e)}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: '16px',
                height: '16px',
                padding: 0,
                border: 'none',
                background: 'transparent',
                cursor: 'pointer',
                color: isActive ? c.accent.blue : c.text.muted,
                transition: 'transform 0.2s ease',
                transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)',
                flexShrink: 0,
                borderRadius: '3px',
              }}
            >
              <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="9 18 15 12 9 6" />
              </svg>
            </button>
          )}
          <span
            style={{
              fontSize: depth === 0 ? '13px' : '12px',
              fontWeight: isActive ? 600 : (depth === 0 ? 500 : 400),
              fontFamily: "'Inter', " + FONTS.ui,
              color: isActive ? c.accent.blue : (depth === 0 ? c.text.primary : c.text.secondary),
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              flex: 1,
              lineHeight: '1.45',
              letterSpacing: '-0.005em',
            }}
            title={node.name}
          >
            {node.name}
          </span>
        </div>
        {hasChildren && isExpanded && node.children && (
          <div>{node.children.map((child, idx) => renderTreeNode(child, depth + 1, idx))}</div>
        )}
      </div>
    );
  };

  if (isCollapsed) {
    return (
      <div style={{ position: 'fixed', left: '16px', top: '80px', zIndex: 100 }}>
        <button
          onClick={onToggle}
          style={{
            width: '42px',
            height: '42px',
            borderRadius: '10px',
            background: c.bg.elevated,
            border: `1px solid ${c.border.primary}`,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            boxShadow: theme === 'dark' ? '0 4px 12px rgba(0,0,0,0.3)' : '0 4px 12px rgba(0,0,0,0.08)',
            color: c.text.secondary,
          }}
          title="Show navigation"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 6h16"/>
            <path d="M4 12h16"/>
            <path d="M4 18h12"/>
          </svg>
        </button>
      </div>
    );
  }

  return (
    <div style={{
      width: '100%',
      flexShrink: 0,
      background: 'transparent',
      padding: '6px 6px 12px',
      overflow: 'auto',
      height: '100%',
      maxHeight: '100%',
      scrollbarWidth: 'thin',
      scrollbarColor: `${c.border.primary} transparent`,
      fontFamily: "'Inter', " + FONTS.ui,
    }}>
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '10px 12px 8px',
        marginBottom: '4px',
        position: 'sticky',
        top: 0,
        background: getStickyBg(theme),
        backdropFilter: 'blur(8px)',
        zIndex: 10,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke={c.text.muted} strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.7 }}>
            <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
            <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
          </svg>
          <span style={{
            fontSize: '11px',
            fontWeight: '600',
            color: c.text.muted,
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
          }}>
            Documents
          </span>
        </div>
      </div>
      <nav style={{ padding: '0 2px' }}>{tree.map((node, idx) => renderTreeNode(node, 0, idx))}</nav>
    </div>
  );
};

// Table of Contents (Right Sidebar) - Flat outline style, no collapsible sections
const TableOfContents: React.FC<{
  theme: 'dark' | 'light' | 'beige';
  items: RightNavItem[];
  activeSection: string;
  isCollapsed: boolean;
  onToggle: () => void;
  // Toolbar props - version & regenerate controls moved here
  versions?: VersionInfo[];
  currentVersionId?: string;
  defaultVersionId?: string;
  repoName: string;
  onVersionChange?: (versionId: string) => void;
  onVersionsUpdate?: () => void;
  language: 'zh' | 'en';
  isGenerating: boolean;
  generateProgress: number;
  onRegenerateClick: () => void;
}> = ({ theme, items, activeSection, isCollapsed, onToggle, versions, currentVersionId, defaultVersionId, repoName, onVersionChange, onVersionsUpdate, language, isGenerating, generateProgress, onRegenerateClick }) => {
  const c = getColors(theme);

  const scrollToSection = (anchor: string, itemName?: string) => {
    let element: HTMLElement | null = document.getElementById(anchor);
    if (!element) element = document.querySelector(`[data-anchor="${anchor}"]`) as HTMLElement;
    if (!element && itemName) element = document.querySelector(`[data-heading-text="${itemName}"]`) as HTMLElement;
    if (!element && itemName) {
      const headings = document.querySelectorAll('h1, h2, h3, h4, h5, h6');
      for (const heading of headings) {
        const headingText = (heading.textContent || '').trim();
        if (headingText === itemName || headingText.toLowerCase() === itemName.toLowerCase()) {
          element = heading as HTMLElement;
          break;
        }
      }
    }
    if (element) {
      const contentContainer = document.querySelector('[data-content-container]');
      if (contentContainer) {
        const containerRect = contentContainer.getBoundingClientRect();
        const elementRect = element.getBoundingClientRect();
        const scrollTop = contentContainer.scrollTop + (elementRect.top - containerRect.top) - 60;
        contentContainer.scrollTo({ top: scrollTop, behavior: 'smooth' });
      } else {
        const y = element.getBoundingClientRect().top + window.pageYOffset - 80;
        window.scrollTo({ top: y, behavior: 'smooth' });
      }
    }
  };

  // Flatten items recursively with depth info for rendering
  const flattenItems = (navItems: RightNavItem[], depth: number = 0, parentKey: string = ''): Array<{ item: RightNavItem; depth: number; key: string }> => {
    const result: Array<{ item: RightNavItem; depth: number; key: string }> = [];
    navItems.forEach((item, idx) => {
      const key = parentKey ? `${parentKey}-${item.anchor}-${idx}` : `${item.anchor}-${idx}`;
      result.push({ item, depth, key });
      if (item.children && item.children.length > 0) {
        result.push(...flattenItems(item.children, depth + 1, key));
      }
    });
    return result;
  };

  const flatItems = flattenItems(items);

  if (!items || items.length === 0) return null;

  if (isCollapsed) {
    return (
      <div style={{
        position: 'fixed',
        right: '16px',
        top: '80px',
        zIndex: 100,
        display: 'flex',
        flexDirection: 'column',
        gap: '8px',
      }}>
        {/* Regenerate button */}
        <button
          onClick={onRegenerateClick}
          disabled={isGenerating}
          title={language === 'zh' ? 'Regenerate' : 'Regenerate'}
          style={{
            width: '40px',
            height: '40px',
            borderRadius: '10px',
            background: c.bg.elevated,
            border: `1px solid ${c.border.primary}`,
            cursor: isGenerating ? 'not-allowed' : 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            boxShadow: theme === 'dark' ? '0 4px 12px rgba(0,0,0,0.3)' : '0 4px 12px rgba(0,0,0,0.08)',
            color: isGenerating ? c.accent.blue : c.text.secondary,
          }}
        >
          {isGenerating ? (
            <span style={{
              width: '16px',
              height: '16px',
              border: `2px solid ${c.border.primary}`,
              borderTopColor: c.accent.blue,
              borderRadius: '50%',
              animation: 'spin 0.8s linear infinite',
            }} />
          ) : (
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 2v6h-6"/>
              <path d="M3 12a9 9 0 0 1 15-6.7L21 8"/>
              <path d="M3 22v-6h6"/>
              <path d="M21 12a9 9 0 0 1-15 6.7L3 16"/>
            </svg>
          )}
        </button>

        {/* Version selector */}
        {versions && versions.length > 0 && onVersionChange && (
          <div style={{
            background: c.bg.elevated,
            border: `1px solid ${c.border.primary}`,
            borderRadius: '10px',
            boxShadow: theme === 'dark' ? '0 4px 12px rgba(0,0,0,0.3)' : '0 4px 12px rgba(0,0,0,0.08)',
          }}>
            <VersionSelector
              theme={theme}
              versions={versions}
              currentVersionId={currentVersionId}
              defaultVersionId={defaultVersionId}
              repoName={repoName}
              onVersionChange={onVersionChange}
              onVersionsUpdate={onVersionsUpdate || (() => {})}
              language={language}
            />
          </div>
        )}

        {/* Show TOC button */}
        <button
          onClick={onToggle}
          style={{
            width: '40px',
            height: '40px',
            borderRadius: '10px',
            background: c.bg.elevated,
            border: `1px solid ${c.border.primary}`,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            boxShadow: theme === 'dark' ? '0 4px 12px rgba(0,0,0,0.3)' : '0 4px 12px rgba(0,0,0,0.08)',
            color: c.text.secondary,
          }}
          title={language === 'zh' ? 'Show TOC' : 'Show TOC'}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="3" y1="6" x2="21" y2="6"/>
            <line x1="3" y1="12" x2="15" y2="12"/>
            <line x1="3" y1="18" x2="18" y2="18"/>
          </svg>
        </button>
        <style>{keyframesStyle}</style>
      </div>
    );
  }

  return (
    <div style={{
      width: '220px',
      minWidth: '220px',
      maxWidth: '220px',
      flexShrink: 0,
      borderLeft: `1px solid ${c.border.secondary}`,
      background: c.bg.primary,
      padding: '0 0 16px',
      overflow: 'auto',
      height: '100%',
      maxHeight: '100%',
      scrollbarWidth: 'thin',
      scrollbarColor: `${c.border.primary} transparent`,
      fontFamily: "'Inter', " + FONTS.ui,
    }}>
      {/* Header with toolbar icons */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '12px 14px 8px',
        marginBottom: '4px',
        position: 'sticky',
        top: 0,
        background: getStickyBg(theme),
        backdropFilter: 'blur(8px)',
        zIndex: 10,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={c.text.muted} strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.7 }}>
            <line x1="3" y1="6" x2="21" y2="6"/>
            <line x1="3" y1="12" x2="15" y2="12"/>
            <line x1="3" y1="18" x2="18" y2="18"/>
          </svg>
          <span style={{
            fontSize: '11px',
            fontWeight: '600',
            color: c.text.muted,
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
          }}>
            On this page
          </span>
        </div>

        {/* Toolbar icons */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '1px' }}>
          {/* Regenerate icon button */}
          <button
            onClick={onRegenerateClick}
            disabled={isGenerating}
            title="Regenerate"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: '26px',
              height: '26px',
              background: 'transparent',
              border: 'none',
              borderRadius: '6px',
              cursor: isGenerating ? 'not-allowed' : 'pointer',
              color: isGenerating ? c.accent.blue : c.text.muted,
              transition: 'all 0.15s',
            }}
            onMouseEnter={(e) => {
              if (!isGenerating) {
                e.currentTarget.style.background = c.bg.secondary;
                e.currentTarget.style.color = c.text.primary;
              }
            }}
            onMouseLeave={(e) => {
              if (!isGenerating) {
                e.currentTarget.style.background = 'transparent';
                e.currentTarget.style.color = c.text.muted;
              }
            }}
          >
            {isGenerating ? (
              <span style={{
                width: '12px',
                height: '12px',
                border: `2px solid ${c.border.primary}`,
                borderTopColor: c.accent.blue,
                borderRadius: '50%',
                animation: 'spin 0.8s linear infinite',
              }} />
            ) : (
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 2v6h-6"/>
                <path d="M3 12a9 9 0 0 1 15-6.7L21 8"/>
                <path d="M3 22v-6h6"/>
                <path d="M21 12a9 9 0 0 1-15 6.7L3 16"/>
              </svg>
            )}
          </button>

          {isGenerating && (
            <span style={{
              fontSize: '10px',
              fontFamily: FONTS.mono,
              color: c.accent.blue,
              fontWeight: '600',
            }}>
              {generateProgress}%
            </span>
          )}

          {versions && versions.length > 0 && onVersionChange && (
            <VersionSelector
              theme={theme}
              versions={versions}
              currentVersionId={currentVersionId}
              defaultVersionId={defaultVersionId}
              repoName={repoName}
              onVersionChange={onVersionChange}
              onVersionsUpdate={onVersionsUpdate || (() => {})}
              language={language}
            />
          )}

          <button
            onClick={onToggle}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: '26px',
              height: '26px',
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              color: c.text.muted,
              borderRadius: '6px',
              transition: 'all 0.15s',
            }}
            title="Hide"
            onMouseEnter={(e) => {
              e.currentTarget.style.background = c.bg.secondary;
              e.currentTarget.style.color = c.text.primary;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'transparent';
              e.currentTarget.style.color = c.text.muted;
            }}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18"/>
              <line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>
      </div>
      <nav style={{ padding: '0 6px' }}>
        {(() => {
          // Track top-level numbering for section numbers
          let topIdx = 0;
          return flatItems.map(({ item, depth, key }) => {
            const isActive = activeSection === item.anchor;
            const indent = 6 + depth * 14;
            const fontSize = depth === 0 ? '12.5px' : '11.5px';

            // Section numbering: top-level gets "1." "2." etc, nested gets "·"
            let prefix = '';
            if (depth === 0) {
              topIdx++;
              prefix = `${topIdx}.`;
            }

            return (
              <div
                key={key}
                style={{
                  display: 'flex',
                  alignItems: 'baseline',
                  gap: depth === 0 ? '6px' : '5px',
                  padding: '4px 8px',
                  paddingLeft: `${indent}px`,
                  cursor: 'pointer',
                  color: isActive ? c.accent.blue : (depth === 0 ? c.text.secondary : c.text.muted),
                  background: isActive ? c.accent.blueBg : 'transparent',
                  borderLeft: isActive ? `2px solid ${c.accent.blue}` : '2px solid transparent',
                  borderRadius: '0 6px 6px 0',
                  transition: 'all 0.12s ease',
                  position: 'relative',
                  marginBottom: depth === 0 ? '1px' : '0px',
                }}
                onClick={() => scrollToSection(item.anchor, item.name)}
                onMouseEnter={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.color = c.text.primary;
                    e.currentTarget.style.background = c.bg.secondary;
                  }
                }}
                onMouseLeave={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.color = depth === 0 ? c.text.secondary : c.text.muted;
                    e.currentTarget.style.background = isActive ? c.accent.blueBg : 'transparent';
                  }
                }}
              >
                {/* Prefix: number for top-level, dot for nested */}
                <span style={{
                  fontSize: depth === 0 ? '11px' : '9px',
                  fontWeight: depth === 0 ? 600 : 400,
                  fontFamily: FONTS.mono,
                  color: isActive ? c.accent.blue : c.text.muted,
                  flexShrink: 0,
                  minWidth: depth === 0 ? '18px' : '8px',
                  textAlign: 'right',
                  opacity: depth === 0 ? 0.7 : 0.5,
                  lineHeight: '1.5',
                }}>
                  {prefix || '\u00B7'}
                </span>
                <span
                  style={{
                    fontSize,
                    fontWeight: isActive ? 600 : (depth === 0 ? 500 : 400),
                    fontFamily: "'Inter', " + FONTS.ui,
                    letterSpacing: '-0.005em',
                    color: 'inherit',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                    lineHeight: '1.5',
                    flex: 1,
                    minWidth: 0,
                  }}
                  title={item.name}
                >
                  {item.name}
                </span>
              </div>
            );
          });
        })()}
      </nav>
    </div>
  );
};

// ==================== Main Component ====================

export function OverviewDocV2({
  repoName,
  index,
  currentPath,
  content,
  loading,
  versions,
  currentVersionId,
  defaultVersionId,
  paperContext,
  onNavigate,
  onRefresh,
  onVersionChange,
  onVersionsUpdate,
  onNavigateToNode,
}: OverviewDocV2Props) {
  // Use global theme from context
  const { theme } = useTheme();
  const { tiers, defaultModel } = useModels();
  const c = getColors(theme);
  const modelTiers = useMemo(
    () => buildModelSelectorTiers(tiers, defaultModel),
    [tiers, defaultModel]
  );

  const [generateOptions, setGenerateOptions] = useState<GenerateOptions>({
    docDepth: 2,
    language: 'zh',
    mode: 'detailed',  // Always use detailed mode
    focus: '',
    model: '',  // Empty string = use server default
  });
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [showRegenerateModal, setShowRegenerateModal] = useState(false);
  const [activeSection, setActiveSection] = useState<string>('');
  const [tocCollapsed, setTocCollapsed] = useState(false);
  const [exportDropdownOpen, setExportDropdownOpen] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [exportMode, setExportMode] = useState<'appendix' | 'inline'>('appendix');
  const exportDropdownRef = React.useRef<HTMLDivElement>(null);

  // Section regeneration state
  const [showFeedbackModal, setShowFeedbackModal] = useState(false);
  const [selectedSection, setSelectedSection] = useState<{
    sectionId: string;
    sectionTitle: string;
    path: string;
  } | null>(null);
  const [feedbackText, setFeedbackText] = useState('');
  const [regenerateError, setRegenerateError] = useState<string | null>(null);
  const [isRegenerating, setIsRegenerating] = useState(false);

  const isMultiFileMode = index?.version === '5.0' || index?.generation_mode === 'multi-file-recursive';

  useEffect(() => {
    const checkWidth = () => {
      setTocCollapsed(window.innerWidth < 1200);
    };
    checkWidth();
    window.addEventListener('resize', checkWidth);
    return () => window.removeEventListener('resize', checkWidth);
  }, []);

  const taskMonitor = useBackgroundTask({
    pollInterval: 2000,
    onComplete: () => { onRefresh(); },
    onError: (error) => { setGenerateError(error); },
  });

  const handleGenerate = useCallback(async () => {
    setGenerateError(null);
    try {
      const response = await apiFetch(`/api/overview/${repoName}/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          language: generateOptions.language,
          doc_depth: generateOptions.docDepth,
          mode: generateOptions.mode,
          focus: generateOptions.focus || undefined,  // Only send if not empty
          model: generateOptions.model || undefined,  // Only send if not empty (use server default)
          paper_id: paperContext?.paperId || undefined,  // Inject paper context if available
        }),
      });
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Failed: ${response.statusText}`);
      }
      const { task_id } = await response.json();
      taskMonitor.startMonitoring(task_id, `/api/overview/${repoName}/generate`);
      // Trigger task refresh to show the new task in TaskStatusPanel
      triggerTaskRefresh();
    } catch (err) {
      setGenerateError(err instanceof Error ? err.message : 'Unknown error');
    }
  }, [repoName, generateOptions, taskMonitor, paperContext]);

  const handleResume = useCallback(async () => {
    if (!taskMonitor.taskId) return;
    setGenerateError(null);
    try {
      const response = await apiFetch(
        `/api/overview/${repoName}/generate/${taskMonitor.taskId}/resume`,
        { method: 'POST' }
      );
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || errorData.error || `Failed: ${response.statusText}`);
      }
      taskMonitor.startMonitoring(taskMonitor.taskId, `/api/overview/${repoName}/generate`);
      triggerTaskRefresh();
    } catch (err) {
      setGenerateError(err instanceof Error ? err.message : 'Unknown error');
    }
  }, [repoName, taskMonitor]);

  // Section regeneration handlers
  const openRegenerateModal = useCallback((sectionId: string, sectionTitle: string, path: string) => {
    setSelectedSection({ sectionId, sectionTitle, path });
    setShowFeedbackModal(true);
    setFeedbackText('');
    setRegenerateError(null);
  }, []);

  const handleRegenerateSection = useCallback(async () => {
    if (!selectedSection || !feedbackText.trim()) return;

    setRegenerateError(null);
    setIsRegenerating(true);
    try {
      const response = await apiFetch(
        `/api/docs/${repoName}/sections/${selectedSection.sectionId}/regenerate`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            version_id: currentVersionId,
            feedback: feedbackText,
            preserve_structure: true,
          }),
        }
      );

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Failed: ${response.statusText}`);
      }

      const { task_id } = await response.json();

      // Start monitoring the task
      taskMonitor.startMonitoring(task_id, `/api/tasks`);

      // Trigger global task refresh
      triggerTaskRefresh();

      // Close modal
      setShowFeedbackModal(false);
      setFeedbackText('');
      setSelectedSection(null);
    } catch (err) {
      setRegenerateError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setIsRegenerating(false);
    }
  }, [repoName, selectedSection, feedbackText, currentVersionId, taskMonitor]);

  const closeFeedbackModal = useCallback(() => {
    setShowFeedbackModal(false);
    setSelectedSection(null);
    setFeedbackText('');
    setRegenerateError(null);
  }, []);

  const rightNavItems = useMemo(() => {
    if (content) {
      const headingRegex = /^(#{1,6})\s+(.+)$/gm;
      const navItems: RightNavItem[] = [];
      const headingStack: Array<{ level: number; item: RightNavItem }> = [];
      const seenTitles = new Set<string>();
      const seenAnchors = new Set<string>();
      let match;

      const simpleHash = (str: string): string => {
        let hash = 0;
        for (let i = 0; i < str.length; i++) {
          hash = ((hash << 5) - hash) + str.charCodeAt(i);
          hash = hash & hash;
        }
        return Math.abs(hash).toString(16).slice(0, 8);
      };

      while ((match = headingRegex.exec(content)) !== null) {
        const level = match[1].length;
        const title = match[2].trim().replace(/`/g, '');
        if (title.toLowerCase() === 'table of contents' || title === 'Table of Contents') continue;
        if (seenTitles.has(title)) continue;
        seenTitles.add(title);

        let anchor = title
          .toLowerCase()
          .replace(/[^\u4e00-\u9fa5\w\s-]/g, '')
          .replace(/\s+/g, '-')
          .replace(/-+/g, '-')
          .replace(/^-+|-+$/g, '');
        if (!anchor) anchor = `section-${simpleHash(title)}`;

        const originalAnchor = anchor;
        let counter = 1;
        while (seenAnchors.has(anchor)) {
          anchor = `${originalAnchor}-${counter}`;
          counter++;
        }
        seenAnchors.add(anchor);

        const navItem: RightNavItem = { name: title, anchor, children: [] };
        while (headingStack.length > 0 && headingStack[headingStack.length - 1].level >= level) {
          headingStack.pop();
        }
        if (headingStack.length > 0) {
          const parent = headingStack[headingStack.length - 1].item;
          if (!parent.children) parent.children = [];
          parent.children.push(navItem);
        } else {
          navItems.push(navItem);
        }
        headingStack.push({ level, item: navItem });
      }
      return navItems;
    }
    if (index?.right_nav?.overview && index.right_nav.overview.length > 0) {
      return index.right_nav.overview;
    }
    return [];
  }, [index?.right_nav, content]);

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
    if (!content) return;

    setExportDropdownOpen(false);
    setIsExporting(true);

    try {
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
      const filename = `${repoName}-${currentPath.replace(/\//g, '-')}-${timestamp}`;

      // Use enhanced export with automatic code fetching
      const enriched = await exportWithAllCode({
        title: currentPath,
        markdown: content,
        repoName,
        references: [],
        metadata: {
          timestamp: new Date().toLocaleString(),
        },
        onProgress: (status) => console.log('Export progress:', status),
        inlineCode: exportMode === 'inline',
        collapsibleCode: true,
      });

      downloadMarkdown(enriched, filename);
    } finally {
      setIsExporting(false);
    }
  }, [content, repoName, currentPath, exportMode]);

  // Export current document as PDF
  const handleExportPDF = useCallback(async () => {
    if (!content || isExporting) return;

    setExportDropdownOpen(false);
    setIsExporting(true);

    try {
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
      const filename = `${repoName}-${currentPath.replace(/\//g, '-')}-${timestamp}`;

      // Use enhanced export with automatic code fetching for fallback
      const enriched = await exportWithAllCode({
        title: currentPath,
        markdown: content,
        repoName,
        references: [],
        metadata: {
          timestamp: new Date().toLocaleString(),
        },
        onProgress: (status) => console.log('Export progress:', status),
        inlineCode: exportMode === 'inline',
        collapsibleCode: true,
      });

      // Prefer exporting the already-rendered document for best fidelity.
      const rendered = document.querySelector('[data-content-container] .markdown-content') as HTMLElement | null;
      if (rendered) {
        await downloadRenderedPDF(rendered, filename, { paddingPx: 24 });
      } else {
        // Fallback: render from markdown
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
  }, [content, repoName, currentPath, theme, isExporting, exportMode]);

  useEffect(() => {
    if (!rightNavItems || rightNavItems.length === 0) return;
    const contentContainer = document.querySelector('[data-content-container]');
    if (!contentContainer) return;

    const handleScroll = () => {
      let currentActive = '';
      let closestDistance = Infinity;
      const findAnchors = (items: RightNavItem[]): string[] => {
        const anchors: string[] = [];
        items.forEach(item => {
          anchors.push(item.anchor);
          if (item.children && item.children.length > 0) {
            anchors.push(...findAnchors(item.children));
          }
        });
        return anchors;
      };
      const anchors = findAnchors(rightNavItems);
      const containerRect = contentContainer.getBoundingClientRect();
      const targetY = containerRect.top + 80;

      for (const anchor of anchors) {
        const element = document.getElementById(anchor);
        if (element) {
          const rect = element.getBoundingClientRect();
          const distance = Math.abs(rect.top - targetY);
          if (rect.top <= targetY + 100 && distance < closestDistance) {
            closestDistance = distance;
            currentActive = anchor;
          }
        }
      }
      setActiveSection(currentActive);
    };

    contentContainer.addEventListener('scroll', handleScroll);
    handleScroll();
    return () => contentContainer.removeEventListener('scroll', handleScroll);
  }, [rightNavItems]);

  const currentSectionHeadings = useMemo(() => {
    if (!isMultiFileMode || !index?.tree) return null;
    const findSection = (nodes: TreeNode[]): TreeNode | null => {
      for (const node of nodes) {
        if (node.path === currentPath) return node;
        if (node.children) {
          const found = findSection(node.children);
          if (found) return found;
        }
      }
      return null;
    };
    return findSection(index.tree)?.headings || null;
  }, [isMultiFileMode, index?.tree, currentPath]);

  const effectiveRightNavItems = useMemo(() => {
    const parsedFromContent = rightNavItems || [];
    if (currentSectionHeadings && currentSectionHeadings.length > 0) {
      const fromIndex = currentSectionHeadings.map(h => ({
        name: h.name,
        anchor: h.anchor,
        children: h.children?.filter((ch: any) => ch != null).map((ch: any) => ({
          name: ch.name,
          anchor: ch.anchor,
          children: ch.children?.filter((grandchild: any) => grandchild != null) || []
        })) || [],
      })) as RightNavItem[];
      if (parsedFromContent.length >= fromIndex.length) return parsedFromContent;
      return fromIndex;
    }
    return parsedFromContent;
  }, [currentSectionHeadings, rightNavItems]);

  // Loading state
  if (loading) {
    return (
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '80px 32px',
        height: '100%',
        fontFamily: FONTS.ui,
        background: c.bg.primary,
      }}>
        <div style={{
          width: '44px',
          height: '44px',
          border: `3px solid ${c.border.primary}`,
          borderTopColor: c.accent.blue,
          borderRadius: '50%',
          animation: 'spin 0.8s linear infinite',
          marginBottom: '20px',
        }} />
        <p style={{ fontSize: '15px', color: c.text.secondary }}>
          Loading documentation...
        </p>
        <style>{keyframesStyle}</style>
      </div>
    );
  }

  // No index - show generate UI
  if (!index) {
    return (
      <GenerateConfigPanel
        theme={theme}
        options={generateOptions}
        modelTiers={modelTiers}
        onOptionsChange={setGenerateOptions}
        onGenerate={handleGenerate}
        onResume={handleResume}
        isGenerating={taskMonitor.isMonitoring}
        canResume={taskMonitor.status === 'stalled' && !!taskMonitor.taskId}
        progress={taskMonitor.progress}
        statusMessage={taskMonitor.message}
        trajectory={taskMonitor.trajectory}
        lastUpdateAt={taskMonitor.lastUpdateAt}
        error={generateError || taskMonitor.error}
      />
    );
  }

  // Main 2-column layout (LeftNavigation is now in PageShell sidebar)
  return (
    <div style={{
      display: 'flex',
      height: '100%',
      overflow: 'hidden',
      background: c.bg.primary,
      width: '100%',
      fontFamily: FONTS.ui,
    }}>
      {/* Main Content - directly renders WikiDoc without extra wrapper */}
      <div
        data-content-container
        style={{
          flex: 1,
          overflow: 'auto',
          minWidth: 0,
          background: c.bg.primary,
        }}
      >
        {content ? (
          <MarkdownContent
            content={content}
            onRegenerateSection={openRegenerateModal}
            sectionFiles={index?.section_files}
            regenerationEnabled={index?.regeneration_enabled}
            repoName={repoName}
            onNavigateToNode={onNavigateToNode}
          />
        ) : loading ? (
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '80px 32px',
            color: c.text.muted,
          }}>
            <div style={{
              width: '32px',
              height: '32px',
              border: `2px solid ${c.border.primary}`,
              borderTopColor: c.accent.blue,
              borderRadius: '50%',
              animation: 'spin 0.8s linear infinite',
              marginBottom: '16px',
            }} />
            <p style={{ fontSize: '14px' }}>Loading content...</p>
          </div>
        ) : (
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '80px 32px',
            color: c.text.muted,
          }}>
            <p style={{ fontSize: '14px', marginBottom: '8px' }}>Failed to load document content.</p>
            <button
              onClick={() => onNavigate(currentPath)}
              style={{
                background: c.accent.blue,
                color: '#fff',
                border: 'none',
                borderRadius: '6px',
                padding: '6px 16px',
                fontSize: '13px',
                cursor: 'pointer',
              }}
            >Retry</button>
          </div>
        )}
      </div>

      {/* Right TOC with toolbar icons */}
      {effectiveRightNavItems.length > 0 ? (
        <TableOfContents
          theme={theme}
          items={effectiveRightNavItems}
          activeSection={activeSection}
          isCollapsed={tocCollapsed}
          onToggle={() => setTocCollapsed(!tocCollapsed)}
          versions={versions}
          currentVersionId={currentVersionId}
          defaultVersionId={defaultVersionId}
          repoName={repoName}
          onVersionChange={onVersionChange}
          onVersionsUpdate={onVersionsUpdate}
          language={generateOptions.language}
          isGenerating={taskMonitor.isMonitoring}
          generateProgress={taskMonitor.progress}
          onRegenerateClick={() => setShowRegenerateModal(true)}
        />
      ) : (
        /* Floating toolbar when no TOC content */
        <div style={{
          position: 'fixed',
          right: '16px',
          top: '80px',
          zIndex: 100,
          display: 'flex',
          flexDirection: 'column',
          gap: '8px',
        }}>
          {/* Regenerate button */}
          <button
            onClick={() => setShowRegenerateModal(true)}
            disabled={taskMonitor.isMonitoring}
            title={generateOptions.language === 'zh' ? 'Regenerate' : 'Regenerate'}
            style={{
              width: '40px',
              height: '40px',
              borderRadius: '10px',
              background: c.bg.elevated,
              border: `1px solid ${c.border.primary}`,
              cursor: taskMonitor.isMonitoring ? 'not-allowed' : 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              boxShadow: theme === 'dark' ? '0 4px 12px rgba(0,0,0,0.3)' : '0 4px 12px rgba(0,0,0,0.08)',
              color: taskMonitor.isMonitoring ? c.accent.blue : c.text.secondary,
            }}
          >
            {taskMonitor.isMonitoring ? (
              <span style={{
                width: '16px',
                height: '16px',
                border: `2px solid ${c.border.primary}`,
                borderTopColor: c.accent.blue,
                borderRadius: '50%',
                animation: 'spin 0.8s linear infinite',
              }} />
            ) : (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 2v6h-6"/>
                <path d="M3 12a9 9 0 0 1 15-6.7L21 8"/>
                <path d="M3 22v-6h6"/>
                <path d="M21 12a9 9 0 0 1-15 6.7L3 16"/>
              </svg>
            )}
          </button>

          {/* Export button */}
          <div ref={exportDropdownRef} style={{ position: 'relative' }}>
            <button
              onClick={() => setExportDropdownOpen(!exportDropdownOpen)}
              disabled={!content || isExporting}
              title="Export document"
              style={{
                width: '40px',
                height: '40px',
                borderRadius: '10px',
                background: c.bg.elevated,
                border: `1px solid ${c.border.primary}`,
                cursor: !content || isExporting ? 'not-allowed' : 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                boxShadow: theme === 'dark' ? '0 4px 12px rgba(0,0,0,0.3)' : '0 4px 12px rgba(0,0,0,0.08)',
                color: !content ? c.text.muted : c.text.secondary,
                opacity: !content ? 0.5 : 1,
              }}
            >
              {isExporting ? (
                <span style={{
                  width: '16px',
                  height: '16px',
                  border: `2px solid ${c.border.primary}`,
                  borderTopColor: c.accent.blue,
                  borderRadius: '50%',
                  animation: 'spin 0.8s linear infinite',
                }} />
              ) : (
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                  <polyline points="7,10 12,15 17,10"/>
                  <line x1="12" y1="15" x2="12" y2="3"/>
                </svg>
              )}
            </button>
            {exportDropdownOpen && (
              <div
                style={{
                  position: 'absolute',
                  top: '100%',
                  right: 0,
                  marginTop: '6px',
                  background: c.bg.elevated,
                  border: `1px solid ${c.border.primary}`,
                  borderRadius: '12px',
                  boxShadow: theme === 'dark' ? '0 8px 24px rgba(0,0,0,0.4)' : '0 8px 24px rgba(0,0,0,0.12)',
                  zIndex: 1000,
                  minWidth: '180px',
                  overflow: 'hidden',
                  padding: '4px',
                }}
              >
                {/* Code Mode Selection */}
                <div style={{ padding: '4px 8px', fontSize: '11px', color: c.text.muted, fontWeight: 500 }}>
                  Code Placement
                </div>
                <button
                  onClick={() => setExportMode('appendix')}
                  style={{
                    width: '100%',
                    padding: '8px 10px',
                    background: exportMode === 'appendix' ? c.accent.blueBg : 'transparent',
                    border: 'none',
                    borderRadius: '6px',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    fontSize: '12px',
                    color: exportMode === 'appendix' ? c.accent.blue : c.text.primary,
                    transition: 'all 150ms ease-out',
                  }}
                  onMouseEnter={(e) => { if (exportMode !== 'appendix') e.currentTarget.style.background = c.bg.tertiary; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = exportMode === 'appendix' ? c.accent.blueBg : 'transparent'; }}
                >
                  <span style={{ fontSize: '14px' }}>📋</span>
                  <span>At end (appendix)</span>
                  {exportMode === 'appendix' && (
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={c.accent.blue} strokeWidth="2.5" style={{ marginLeft: 'auto' }}>
                      <polyline points="20,6 9,17 4,12"/>
                    </svg>
                  )}
                </button>
                <button
                  onClick={() => setExportMode('inline')}
                  style={{
                    width: '100%',
                    padding: '8px 10px',
                    background: exportMode === 'inline' ? c.accent.blueBg : 'transparent',
                    border: 'none',
                    borderRadius: '6px',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    fontSize: '12px',
                    color: exportMode === 'inline' ? c.accent.blue : c.text.primary,
                    transition: 'all 150ms ease-out',
                  }}
                  onMouseEnter={(e) => { if (exportMode !== 'inline') e.currentTarget.style.background = c.bg.tertiary; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = exportMode === 'inline' ? c.accent.blueBg : 'transparent'; }}
                >
                  <span style={{ fontSize: '14px' }}>📝</span>
                  <span>Inline in doc</span>
                  {exportMode === 'inline' && (
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={c.accent.blue} strokeWidth="2.5" style={{ marginLeft: 'auto' }}>
                      <polyline points="20,6 9,17 4,12"/>
                    </svg>
                  )}
                </button>
                {/* Divider */}
                <div style={{ height: '1px', background: c.border.secondary, margin: '4px 8px' }} />
                {/* Export Options */}
                <button
                  onClick={handleExportMarkdown}
                  style={{
                    width: '100%',
                    padding: '10px 12px',
                    background: 'transparent',
                    border: 'none',
                    borderRadius: '8px',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '10px',
                    fontSize: '13px',
                    color: c.text.primary,
                    transition: 'all 150ms ease-out',
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = c.bg.tertiary; }}
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
                {/* PDF export hidden for now - uncomment when ready
                <button
                  onClick={handleExportPDF}
                  disabled={isExporting}
                  style={{
                    width: '100%',
                    padding: '10px 12px',
                    background: 'transparent',
                    border: 'none',
                    borderRadius: '8px',
                    cursor: isExporting ? 'not-allowed' : 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '10px',
                    fontSize: '13px',
                    color: isExporting ? c.text.muted : c.text.primary,
                    opacity: isExporting ? 0.5 : 1,
                    transition: 'all 150ms ease-out',
                  }}
                  onMouseEnter={(e) => { if (!isExporting) e.currentTarget.style.background = c.bg.tertiary; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                    <polyline points="14,2 14,8 20,8"/>
                  </svg>
                  <span>PDF</span>
                </button>
                */}
              </div>
            )}
          </div>

          {/* Version selector */}
          {versions && versions.length > 0 && onVersionChange && (
            <div style={{
              background: c.bg.elevated,
              border: `1px solid ${c.border.primary}`,
              borderRadius: '10px',
              boxShadow: theme === 'dark' ? '0 4px 12px rgba(0,0,0,0.3)' : '0 4px 12px rgba(0,0,0,0.08)',
            }}>
              <VersionSelector
                theme={theme}
                versions={versions}
                currentVersionId={currentVersionId}
                defaultVersionId={defaultVersionId}
                repoName={repoName}
                onVersionChange={onVersionChange}
                onVersionsUpdate={onVersionsUpdate || (() => {})}
                language={generateOptions.language}
              />
            </div>
          )}
        </div>
      )}

      {/* Regenerate Modal */}
      <RegenerateModal
        theme={theme}
        isOpen={showRegenerateModal}
        onClose={() => setShowRegenerateModal(false)}
        options={generateOptions}
        modelTiers={modelTiers}
        onOptionsChange={setGenerateOptions}
        onConfirm={handleGenerate}
      />

      {/* Feedback Modal for Section Regeneration */}
      <FeedbackModal
        isOpen={showFeedbackModal}
        onClose={closeFeedbackModal}
        onSubmit={handleRegenerateSection}
        sectionTitle={selectedSection?.sectionTitle || ''}
        theme={theme}
        isSubmitting={isRegenerating}
        error={regenerateError}
        feedback={feedbackText}
        onFeedbackChange={setFeedbackText}
        language={generateOptions.language}
      />

      <style>{keyframesStyle}</style>
    </div>
  );
}

export default OverviewDocV2;

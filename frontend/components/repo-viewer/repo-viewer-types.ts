// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

export interface FolderChildItem {
  qualified_name: string;
  name: string;
  node_type: string;
  is_package: boolean;
  child_count: number;
}

export interface Breadcrumb {
  qualified_name: string;
  name: string;
}

export interface FilePreview {
  qualifiedName: string;
  filePath: string;
  language: string;
  visibleCode: string;
  visibleStart: number;
  visibleEnd: number;
  highlightStart?: number;
  highlightEnd?: number;
  fullLines?: string[];
  totalLines?: number;
}

export interface SymbolItem {
  name: string;
  type: 'Function' | 'Class' | 'Method' | 'Variable' | string;
  qualified_name: string;
  start_line?: number;
  end_line?: number;
  file_path?: string;
}

export interface SymbolCallsData {
  callers: CallItem[];
  callees: CallItem[];
}

export interface CallItem {
  qualified_name: string;
  name: string;
  node_type: string;
  file_path?: string;
}

export interface BlameLineInfo {
  line: number;
  sha: string;
  short_sha: string;
  author: string;
  date: string;
  message: string;
}

export type ViewMode = 'code' | 'blame';

export type LayoutMode = 'full' | 'compact' | 'narrow';

export interface RepoViewerHandle {
  navigateTo: (qualifiedName: string) => Promise<void>;
}

export interface ThemeColors {
  bg: string;
  bgSecondary: string;
  bgHover: string;
  border: string;
  text: string;
  textMuted: string;
  textDimmed: string;
  accent: string;
  accentBg: string;
}

// Symbol type colors for the outline panel
export const SYMBOL_TYPE_COLORS: Record<string, string> = {
  Function: '#dcdcaa',
  Class: '#4ec9b0',
  Method: '#569cd6',
  Variable: '#9cdcfe',
  Module: '#c586c0',
  Package: '#ce9178',
};

// Layout constants
export const SIDEBAR_LEFT_DEFAULT = 240;
export const SIDEBAR_LEFT_COMPACT = 200;
export const SIDEBAR_RIGHT_DEFAULT = 260;
export const BREAKPOINT_FULL = 900;
export const BREAKPOINT_COMPACT = 600;
export const FILE_LIST_PAGE_SIZE = 80;
export const EXPAND_LINES = 200;
export const MAX_INITIAL_LINES = 1500;
export const SHOW_FULL_THRESHOLD = 1000;
export const HIGHLIGHT_FADE_MS = 3000;

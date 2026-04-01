'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { apiFetch } from '@/lib/api-client';
import type {
  FolderChildItem, FilePreview, SymbolItem, SymbolCallsData,
  BlameLineInfo, CallItem,
} from './repo-viewer-types';
import {
  EXPAND_LINES, MAX_INITIAL_LINES, SHOW_FULL_THRESHOLD, FILE_LIST_PAGE_SIZE,
} from './repo-viewer-types';

// --- Helpers ---

export function detectLanguage(filePath: string): string {
  const ext = filePath.split('.').pop()?.toLowerCase();
  const map: Record<string, string> = {
    py: 'python', pyx: 'python', js: 'javascript', jsx: 'javascript',
    ts: 'typescript', tsx: 'typescript', rs: 'rust', cpp: 'cpp', c: 'c',
    h: 'cpp', hpp: 'cpp', java: 'java',
    go: 'go', rb: 'ruby', sh: 'bash', yaml: 'yaml', yml: 'yaml',
    json: 'json', html: 'html', css: 'css', md: 'markdown', cu: 'cpp',
    cuh: 'cpp', cc: 'cpp',
  };
  return map[ext || ''] || 'text';
}

export function filePathToQN(filePath: string): string {
  return filePath
    .replace(/\.(py|pyx|js|jsx|ts|tsx|java|go|rs|cpp|c|h|hpp|cc|cu|cuh|rb|sh)$/, '')
    .replace(/\//g, '.');
}

/** Compute foldable regions from indentation. Returns Map<startLine, endLine> (1-based). */
export function computeFoldRegions(code: string, startingLine: number): Map<number, number> {
  const lines = code.split('\n');
  const regions = new Map<number, number>();
  const indents: number[] = lines.map(line => {
    if (line.trim().length === 0) return -1;
    const match = line.match(/^(\s*)/);
    return match ? match[1].replace(/\t/g, '    ').length : 0;
  });

  for (let i = 0; i < lines.length; i++) {
    if (indents[i] === -1) continue;
    let nextNonBlank = -1;
    for (let j = i + 1; j < lines.length; j++) {
      if (indents[j] !== -1) { nextNonBlank = j; break; }
    }
    if (nextNonBlank === -1) continue;
    if (indents[nextNonBlank] <= indents[i]) continue;
    const baseIndent = indents[i];
    let endIdx = nextNonBlank;
    for (let j = nextNonBlank + 1; j < lines.length; j++) {
      if (indents[j] === -1) continue;
      if (indents[j] <= baseIndent) break;
      endIdx = j;
    }
    while (endIdx + 1 < lines.length && indents[endIdx + 1] === -1) endIdx++;
    if (endIdx - i >= 2) {
      regions.set(startingLine + i, startingLine + endIdx);
    }
  }
  return regions;
}

// --- Data Fetching Hooks ---

interface CodeResult {
  code: string;
  language: string;
  filePath: string;
  startLine?: number;
  endLine?: number;
}

export function useFileTree(repoName: string) {
  const [children, setChildren] = useState<FolderChildItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fileListLimit, setFileListLimit] = useState(FILE_LIST_PAGE_SIZE);
  const cache = useRef<Map<string, FolderChildItem[]>>(new Map());

  useEffect(() => { setFileListLimit(FILE_LIST_PAGE_SIZE); }, [children]);

  const fetchChildren = useCallback(async (qualifiedName: string): Promise<FolderChildItem[]> => {
    if (cache.current.has(qualifiedName)) return cache.current.get(qualifiedName)!;
    const response = await apiFetch(
      `/api/graph/node/${encodeURIComponent(repoName)}/children?qualified_name=${encodeURIComponent(qualifiedName)}`
    );
    if (!response.ok) throw new Error(`Failed to fetch children: ${response.status}`);
    const data = await response.json();
    const items = data.children || [];
    cache.current.set(qualifiedName, items);
    return items;
  }, [repoName]);

  const loadFolder = useCallback(async (qualifiedName: string) => {
    setLoading(true);
    setError(null);
    try {
      const items = await fetchChildren(qualifiedName);
      setChildren(items);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, [fetchChildren]);

  const showMore = useCallback(() => {
    setFileListLimit(prev => prev + FILE_LIST_PAGE_SIZE);
  }, []);

  return { children, loading, error, fileListLimit, fetchChildren, loadFolder, showMore, setChildren };
}

export function useCodeContent(repoName: string) {
  const [filePreview, setFilePreview] = useState<FilePreview | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const [expandLoading, setExpandLoading] = useState<'up' | 'down' | null>(null);
  const cache = useRef<Map<string, CodeResult>>(new Map());

  const fetchCode = useCallback(async (qualifiedName: string): Promise<CodeResult> => {
    if (cache.current.has(qualifiedName)) return cache.current.get(qualifiedName)!;
    const response = await apiFetch(
      `/api/graph/node/${encodeURIComponent(repoName)}/code?qualified_name=${encodeURIComponent(qualifiedName)}`
    );
    if (!response.ok) throw new Error(`Failed to fetch code: ${response.status}`);
    const data = await response.json();
    const result: CodeResult = {
      code: data.source_code || data.code || '',
      language: detectLanguage(data.file_path || data.file || qualifiedName),
      filePath: data.file_path || data.file || qualifiedName,
      startLine: data.start_line,
      endLine: data.end_line,
    };
    cache.current.set(qualifiedName, result);
    return result;
  }, [repoName]);

  const loadFile = useCallback(async (qn: string) => {
    setFileLoading(true);
    try {
      const { code, language, filePath } = await fetchCode(qn);
      const lines = code.split('\n');
      const showFull = lines.length <= SHOW_FULL_THRESHOLD;
      const visEnd = showFull ? lines.length : Math.min(lines.length, MAX_INITIAL_LINES);
      const visibleCode = visEnd < lines.length ? lines.slice(0, visEnd).join('\n') : code;
      setFilePreview({
        qualifiedName: qn, filePath, language, visibleCode,
        visibleStart: 1, visibleEnd: visEnd,
        fullLines: lines, totalLines: lines.length,
      });
    } catch (err) {
      setFilePreview({
        qualifiedName: qn, filePath: qn, language: 'text',
        visibleCode: `// Failed to load: ${err instanceof Error ? err.message : 'Unknown error'}`,
        visibleStart: 1, visibleEnd: 1,
      });
    } finally {
      setFileLoading(false);
    }
  }, [fetchCode]);

  const navigateToCode = useCallback(async (qualifiedName: string) => {
    setFileLoading(true);
    try {
      const result = await fetchCode(qualifiedName);
      const { code, language, filePath, startLine, endLine } = result;
      const codeLines = code.split('\n');
      const totalLines = codeLines.length;
      const isFullFile = startLine === 1 && endLine && endLine >= totalLines - 1;

      if (!isFullFile && startLine && endLine) {
        setFilePreview({
          qualifiedName, filePath, language,
          visibleCode: code,
          visibleStart: startLine, visibleEnd: endLine,
          highlightStart: startLine, highlightEnd: endLine,
        });
      } else {
        const showFull = totalLines <= SHOW_FULL_THRESHOLD;
        const visEnd = showFull ? totalLines : Math.min(totalLines, MAX_INITIAL_LINES);
        const visibleCode = visEnd < totalLines ? codeLines.slice(0, visEnd).join('\n') : code;
        setFilePreview({
          qualifiedName, filePath, language, visibleCode,
          visibleStart: 1, visibleEnd: visEnd,
          fullLines: codeLines, totalLines,
        });
      }
    } catch {
      setFilePreview(null);
    } finally {
      setFileLoading(false);
    }
  }, [fetchCode]);

  const expandCode = useCallback(async (direction: 'up' | 'down') => {
    if (!filePreview) return;
    setExpandLoading(direction);
    try {
      let fullLines = filePreview.fullLines;
      if (!fullLines) {
        const fileQN = filePathToQN(filePreview.filePath);
        const result = await fetchCode(fileQN);
        fullLines = result.code.split('\n');
      }
      const totalLines = fullLines.length;
      const newStart = direction === 'up'
        ? Math.max(1, filePreview.visibleStart - EXPAND_LINES)
        : filePreview.visibleStart;
      const newEnd = direction === 'down'
        ? Math.min(totalLines, filePreview.visibleEnd + EXPAND_LINES)
        : filePreview.visibleEnd;
      const visibleCode = fullLines.slice(newStart - 1, newEnd).join('\n');
      setFilePreview({
        ...filePreview, visibleCode,
        visibleStart: newStart, visibleEnd: newEnd,
        fullLines, totalLines,
      });
    } finally {
      setExpandLoading(null);
    }
  }, [filePreview, fetchCode]);

  return {
    filePreview, fileLoading, expandLoading, fetchCode,
    loadFile, navigateToCode, expandCode, setFilePreview,
  };
}

/**
 * Parse symbols from source code using regex patterns (client-side fallback).
 * Works like GitHub's symbol outline — extracts function/class/method definitions.
 */
export function parseSymbolsFromCode(code: string, language: string, fileQN: string): SymbolItem[] {
  const lines = code.split('\n');
  const symbols: SymbolItem[] = [];

  // Language-specific regex patterns
  const patterns: Array<{ regex: RegExp; type: string; nameGroup: number }> = [];

  switch (language) {
    case 'python':
      patterns.push(
        { regex: /^(\s*)class\s+(\w+)/, type: 'Class', nameGroup: 2 },
        { regex: /^(\s*)(?:async\s+)?def\s+(\w+)/, type: 'Function', nameGroup: 2 },
      );
      break;
    case 'javascript':
    case 'typescript':
      patterns.push(
        { regex: /^(\s*)(?:export\s+)?class\s+(\w+)/, type: 'Class', nameGroup: 2 },
        { regex: /^(\s*)(?:export\s+)?(?:async\s+)?function\s+(\w+)/, type: 'Function', nameGroup: 2 },
        { regex: /^(\s*)(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(/, type: 'Function', nameGroup: 2 },
        { regex: /^(\s*)(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\([^)]*\)|[a-zA-Z_]\w*)\s*=>/, type: 'Function', nameGroup: 2 },
        { regex: /^(\s*)(?:public|private|protected|static|async|get|set)\s+(\w+)\s*\(/, type: 'Method', nameGroup: 2 },
        { regex: /^(\s*)(?:export\s+)?interface\s+(\w+)/, type: 'Class', nameGroup: 2 },
        { regex: /^(\s*)(?:export\s+)?type\s+(\w+)/, type: 'Class', nameGroup: 2 },
      );
      break;
    case 'rust':
      patterns.push(
        { regex: /^(\s*)(?:pub\s+)?struct\s+(\w+)/, type: 'Class', nameGroup: 2 },
        { regex: /^(\s*)(?:pub\s+)?enum\s+(\w+)/, type: 'Class', nameGroup: 2 },
        { regex: /^(\s*)(?:pub\s+)?(?:async\s+)?fn\s+(\w+)/, type: 'Function', nameGroup: 2 },
        { regex: /^(\s*)impl(?:<[^>]*>)?\s+(\w+)/, type: 'Class', nameGroup: 2 },
        { regex: /^(\s*)(?:pub\s+)?trait\s+(\w+)/, type: 'Class', nameGroup: 2 },
      );
      break;
    case 'go':
      patterns.push(
        { regex: /^(\s*)type\s+(\w+)\s+struct/, type: 'Class', nameGroup: 2 },
        { regex: /^(\s*)type\s+(\w+)\s+interface/, type: 'Class', nameGroup: 2 },
        { regex: /^(\s*)func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(/, type: 'Function', nameGroup: 2 },
      );
      break;
    case 'java':
      patterns.push(
        { regex: /^(\s*)(?:public|private|protected)?\s*(?:static\s+)?(?:abstract\s+)?class\s+(\w+)/, type: 'Class', nameGroup: 2 },
        { regex: /^(\s*)(?:public|private|protected)?\s*(?:static\s+)?interface\s+(\w+)/, type: 'Class', nameGroup: 2 },
        { regex: /^(\s*)(?:public|private|protected)?\s*(?:static\s+)?(?:synchronized\s+)?(?:\w+(?:<[^>]*>)?\s+)+(\w+)\s*\(/, type: 'Method', nameGroup: 2 },
      );
      break;
    case 'cpp':
    case 'c':
      patterns.push(
        { regex: /^(\s*)class\s+(\w+)/, type: 'Class', nameGroup: 2 },
        { regex: /^(\s*)struct\s+(\w+)/, type: 'Class', nameGroup: 2 },
        { regex: /^(\s*)(?:static\s+)?(?:inline\s+)?(?:virtual\s+)?(?:const\s+)?(?:\w+(?:<[^>]*>)?[*&\s]+)+(\w+)\s*\(/, type: 'Function', nameGroup: 2 },
        { regex: /^(\s*)namespace\s+(\w+)/, type: 'Module', nameGroup: 2 },
      );
      break;
    case 'lua':
      patterns.push(
        { regex: /^(\s*)(?:local\s+)?function\s+(?:(\w+(?:\.\w+)*):)?(\w+)\s*\(/, type: 'Function', nameGroup: 3 },
        { regex: /^(\s*)(\w+)\s*=\s*function\s*\(/, type: 'Function', nameGroup: 2 },
      );
      break;
    default:
      // Generic: look for function-like patterns
      patterns.push(
        { regex: /^(\s*)(?:def|function|fn|func|pub fn|async function|export function)\s+(\w+)/, type: 'Function', nameGroup: 2 },
        { regex: /^(\s*)(?:class|struct|enum|interface|type)\s+(\w+)/, type: 'Class', nameGroup: 2 },
      );
  }

  // Track class context for detecting methods
  let currentClass: string | null = null;
  let classIndent = -1;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim().length === 0) continue;

    const indent = (line.match(/^(\s*)/)?.[1] || '').replace(/\t/g, '    ').length;

    // Exit class context if we've dedented
    if (currentClass && indent <= classIndent) {
      currentClass = null;
      classIndent = -1;
    }

    for (const { regex, type, nameGroup } of patterns) {
      const match = line.match(regex);
      if (!match) continue;
      const name = match[nameGroup];
      if (!name) continue;

      // Determine if this is a method (inside a class)
      let effectiveType = type;
      if (type === 'Function' && currentClass && indent > classIndent) {
        effectiveType = 'Method';
      }

      // Find end line (next line with same or less indentation)
      let endLine = i + 1;
      for (let j = i + 1; j < lines.length; j++) {
        const nextLine = lines[j];
        if (nextLine.trim().length === 0) continue;
        const nextIndent = (nextLine.match(/^(\s*)/)?.[1] || '').replace(/\t/g, '    ').length;
        if (nextIndent <= indent) { endLine = j; break; }
        endLine = j + 1;
      }

      symbols.push({
        name,
        type: effectiveType,
        qualified_name: `${fileQN}.${name}`,
        start_line: i + 1,
        end_line: endLine,
      });

      // Track class context
      if (type === 'Class') {
        currentClass = name;
        classIndent = indent;
      }

      break; // Only match first pattern per line
    }
  }

  return symbols;
}

export function useSymbols(repoName: string) {
  const [symbols, setSymbols] = useState<SymbolItem[]>([]);
  const [loading, setLoading] = useState(false);
  const cache = useRef<Map<string, SymbolItem[]>>(new Map());

  const loadSymbols = useCallback(async (fileQN: string) => {
    if (cache.current.has(fileQN)) {
      setSymbols(cache.current.get(fileQN)!);
      return;
    }
    setLoading(true);
    try {
      // Try graph API first
      const response = await apiFetch(
        `/api/graph/node/${encodeURIComponent(repoName)}/children/enhanced?identifier=${encodeURIComponent(fileQN)}&identifier_type=file&depth=2`
      );
      if (response.ok) {
        const data = await response.json();
        const items: SymbolItem[] = (data.children || [])
          .filter((c: any) => ['Function', 'Class', 'Method'].some(t => (c.labels || c.type || []).includes(t)))
          .map((c: any) => ({
            name: c.name,
            type: (c.labels || c.type || ['Function'])[0],
            qualified_name: c.qualified_name,
            start_line: c.start_line,
            end_line: c.end_line,
            file_path: c.file_path,
          }));
        if (items.length > 0) {
          cache.current.set(fileQN, items);
          setSymbols(items);
          return;
        }
      }
      // Graph returned empty — symbols will be set by loadSymbolsFromCode below
      setSymbols([]);
    } catch {
      setSymbols([]);
    } finally {
      setLoading(false);
    }
  }, [repoName]);

  /** Client-side fallback: parse symbols directly from source code */
  const loadSymbolsFromCode = useCallback((code: string, language: string, fileQN: string) => {
    const cacheKey = `local:${fileQN}`;
    if (cache.current.has(cacheKey)) {
      setSymbols(cache.current.get(cacheKey)!);
      return;
    }
    const parsed = parseSymbolsFromCode(code, language, fileQN);
    if (parsed.length > 0) {
      cache.current.set(cacheKey, parsed);
      setSymbols(parsed);
    }
  }, []);

  const clearSymbols = useCallback(() => setSymbols([]), []);

  return { symbols, loading, loadSymbols, loadSymbolsFromCode, clearSymbols };
}

export function useSymbolCalls(repoName: string) {
  const cache = useRef<Map<string, SymbolCallsData>>(new Map());

  const fetchCalls = useCallback(async (qn: string): Promise<SymbolCallsData> => {
    if (cache.current.has(qn)) return cache.current.get(qn)!;
    const [outRes, inRes] = await Promise.all([
      apiFetch(`/api/graph/node/${encodeURIComponent(repoName)}/calls?qualified_name=${encodeURIComponent(qn)}&direction=outgoing`),
      apiFetch(`/api/graph/node/${encodeURIComponent(repoName)}/calls?qualified_name=${encodeURIComponent(qn)}&direction=incoming`),
    ]);
    const outData = outRes.ok ? await outRes.json() : { results: [] };
    const inData = inRes.ok ? await inRes.json() : { results: [] };
    const result: SymbolCallsData = {
      callees: outData.results || [],
      callers: inData.results || [],
    };
    cache.current.set(qn, result);
    return result;
  }, [repoName]);

  return { fetchCalls };
}

export function useBlame(repoName: string) {
  const [blameData, setBlameData] = useState<BlameLineInfo[] | null>(null);
  const [loading, setLoading] = useState(false);
  const cache = useRef<Map<string, BlameLineInfo[]>>(new Map());

  const loadBlame = useCallback(async (filePath: string) => {
    if (cache.current.has(filePath)) {
      setBlameData(cache.current.get(filePath)!);
      return;
    }
    setLoading(true);
    try {
      const response = await apiFetch(
        `/api/sync/${encodeURIComponent(repoName)}/git/blame?file_path=${encodeURIComponent(filePath)}`
      );
      if (!response.ok) { setBlameData(null); return; }
      const data = await response.json();
      cache.current.set(filePath, data.lines || []);
      setBlameData(data.lines || []);
    } catch {
      setBlameData(null);
    } finally {
      setLoading(false);
    }
  }, [repoName]);

  const clearBlame = useCallback(() => setBlameData(null), []);

  return { blameData, loading, loadBlame, clearBlame };
}

export interface GitRef {
  name: string;
  ref_type: string;
  commit_sha: string;
  short_sha: string;
  is_current: boolean;
}

export function useBranches(repoName: string) {
  const [branches, setBranches] = useState<GitRef[]>([]);
  const [currentBranch, setCurrentBranch] = useState<string>('');
  const [loading, setLoading] = useState(false);

  const loadBranches = useCallback(async () => {
    setLoading(true);
    try {
      const branchRes = await apiFetch(`/api/sync/${encodeURIComponent(repoName)}/git/branches`);
      if (branchRes.ok) {
        const data: GitRef[] = await branchRes.json();
        setBranches(data || []);
        // Extract current branch from the list (is_current flag)
        const current = data?.find((b: GitRef) => b.is_current);
        if (current) setCurrentBranch(current.name);
      }
    } catch {
      // ignore — sync manager may not be initialized for this repo
    } finally {
      setLoading(false);
    }
  }, [repoName]);

  return { branches, currentBranch, loading, loadBranches };
}

export function useGraphSearch(repoName: string) {
  const [results, setResults] = useState<Array<{ qualified_name: string; name: string; node_type: string; file_path?: string }>>([]);
  const [loading, setLoading] = useState(false);
  const cache = useRef<Map<string, typeof results>>(new Map());
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const search = useCallback(async (query: string) => {
    if (!query.trim()) { setResults([]); return; }
    if (cache.current.has(query)) { setResults(cache.current.get(query)!); return; }
    setLoading(true);
    try {
      const response = await apiFetch(
        `/api/graph/node/${encodeURIComponent(repoName)}/find`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query, node_type: 'All' }) }
      );
      if (!response.ok) throw new Error(`Search failed`);
      const data = await response.json();
      const items = data.nodes || data.results || [];
      cache.current.set(query, items);
      setResults(items);
    } catch {
      setResults([]);
    } finally {
      setLoading(false);
    }
  }, [repoName]);

  const debouncedSearch = useCallback((query: string) => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!query.trim()) { setResults([]); return; }
    if (cache.current.has(query)) { setResults(cache.current.get(query)!); return; }
    debounceRef.current = setTimeout(() => search(query), 300);
  }, [search]);

  const clear = useCallback(() => {
    setResults([]);
    if (debounceRef.current) clearTimeout(debounceRef.current);
  }, []);

  return { results, loading, search: debouncedSearch, clear };
}

// --- Symbol Navigation (Definitions & References popup) ---

export interface SymbolNavResult {
  qualified_name: string;
  name: string;
  file_path: string;
  start_line?: number;
  node_type: string;
}

export interface InFileMatch {
  line: number;
  text: string;           // the matched line
  contextBefore: string[]; // up to 3 lines before
  contextAfter: string[];  // up to 3 lines after
}

export interface SymbolNavData {
  symbolName: string;
  definitions: SymbolNavResult[];
  references: SymbolNavResult[];
  inThisFile: InFileMatch[];
}

export function useSymbolNavigation(repoName: string) {
  const [data, setData] = useState<SymbolNavData | null>(null);
  const [loading, setLoading] = useState(false);
  const [position, setPosition] = useState<{ x: number; y: number } | null>(null);
  const cache = useRef<Map<string, SymbolNavData>>(new Map());

  const lookup = useCallback(async (symbolName: string, clickX: number, clickY: number, currentCode?: string, currentFilePath?: string) => {
    setPosition({ x: clickX, y: clickY });

    if (cache.current.has(symbolName)) {
      setData(cache.current.get(symbolName)!);
      return;
    }

    setLoading(true);
    setData({ symbolName, definitions: [], references: [], inThisFile: [] });
    try {
      // Search for definitions (exact name match)
      const findRes = await apiFetch(
        `/api/graph/node/${encodeURIComponent(repoName)}/find`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: symbolName, node_type: 'Code', search_strategy: 'auto' }),
        }
      );

      let definitions: SymbolNavResult[] = [];
      if (findRes.ok) {
        const findData = await findRes.json();
        definitions = (findData.results || findData.nodes || [])
          .filter((n: any) => n.name === symbolName || n.qualified_name?.endsWith(`.${symbolName}`))
          .slice(0, 20)
          .map((n: any) => ({
            qualified_name: n.qualified_name,
            name: n.name,
            file_path: n.path || n.file_path || '',
            start_line: n.start_line,
            node_type: (Array.isArray(n.type) ? n.type[0] : n.type) || n.node_type || 'Code',
          }));
      }

      // Search for incoming calls (references)
      let references: SymbolNavResult[] = [];
      if (definitions.length > 0) {
        const qn = definitions[0].qualified_name;
        const callsRes = await apiFetch(
          `/api/graph/node/${encodeURIComponent(repoName)}/calls?qualified_name=${encodeURIComponent(qn)}&direction=incoming&depth=1`
        );
        if (callsRes.ok) {
          const callsData = await callsRes.json();
          references = (callsData.results || []).slice(0, 20).map((r: any) => ({
            qualified_name: r.qualified_name,
            name: r.name,
            file_path: r.path || r.file_path || '',
            start_line: r.start_line,
            node_type: (Array.isArray(r.type) ? r.type[0] : r.type) || r.node_type || 'Function',
          }));
        }
      }

      // Find occurrences in current file with context
      const CONTEXT_LINES = 3;
      const inThisFile: InFileMatch[] = [];
      if (currentCode) {
        const lines = currentCode.split('\n');
        const regex = new RegExp(`\\b${symbolName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`);
        for (let i = 0; i < lines.length; i++) {
          if (regex.test(lines[i])) {
            const contextBefore: string[] = [];
            for (let j = Math.max(0, i - CONTEXT_LINES); j < i; j++) {
              contextBefore.push(lines[j]);
            }
            const contextAfter: string[] = [];
            for (let j = i + 1; j <= Math.min(lines.length - 1, i + CONTEXT_LINES); j++) {
              contextAfter.push(lines[j]);
            }
            inThisFile.push({
              line: i + 1,
              text: lines[i],
              contextBefore,
              contextAfter,
            });
          }
          if (inThisFile.length >= 15) break;
        }
      }

      const result: SymbolNavData = { symbolName, definitions, references, inThisFile };
      // Only cache graph results, not inThisFile (file-dependent)
      if (!currentCode) cache.current.set(symbolName, result);
      setData(result);
    } catch {
      setData({ symbolName, definitions: [], references: [], inThisFile: [] });
    } finally {
      setLoading(false);
    }
  }, [repoName]);

  const close = useCallback(() => {
    setData(null);
    setPosition(null);
  }, []);

  return { data, loading, position, lookup, close };
}

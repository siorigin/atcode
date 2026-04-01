// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * Papers API client for paper reading feature.
 */

import { apiFetch } from './api-client';

// --- Types ---

export interface PaperMetadata {
  paper_id: string;
  title: string;
  authors: string[];
  abstract: string;
  source: string;
  url: string;
  pdf_url: string | null;
  published_date: string | null;
  github_urls: string[];
  citations: number;
}

export interface PaperSearchResponse {
  papers: PaperMetadata[];
  total: number;
}

export interface PaperReadResponse {
  task_id: string;
  message: string;
}

export interface PaperStatus {
  task_id: string;
  status: string;
  progress: number;
  step: string;
  status_message: string;
  error: string | null;
  result: any;
}

export interface DocSection {
  title: string;
  content: string;
  level: number;
  collapsible: boolean;
  code_refs: Array<{
    qualified_name: string;
    display_name: string;
    file_path: string;
    line: number;
  }>;
}

export interface FigureBlock {
  figure_type: string;
  path: string;
  caption: string;
  markdown: string;
  page: number;
}

export interface CodeAnalysis {
  repo_url: string;
  repo_name: string;
  project_name: string;
  structure_overview: string;
  key_components: Array<{
    qualified_name: string;
    docstring: string;
    role: string;
  }>;
  architecture_diagram: string;
  paper_code_mapping: Array<{
    paper_concept: string;
    code_entity: string;
    explanation: string;
  }>;
}

export interface PaperReadingDoc {
  paper: PaperMetadata;
  sections: DocSection[];
  figures: FigureBlock[];
  code_analysis: CodeAnalysis | null;
  references: Array<{ title: string; authors: string; url: string }>;
}

export interface PaperListItem {
  paper_id: string;
  title: string;
  authors: string[];
  source: string;
  status: string;
}

// --- HuggingFace Daily Papers Types ---

export interface HFPaperEntry {
  paper_id: string;
  title: string;
  summary: string;
  authors: string[];
  published_at: string;
  upvotes: number;
  num_comments: number;
  ai_summary: string | null;
  ai_keywords: string[];
  github_repo: string | null;
  github_stars: number | null;
  organization: string | null;
  thumbnail_url: string | null;
  submitted_by: string | null;
  submitted_at: string;
  source: string;
}

export interface DailyIndex {
  date: string;
  papers: HFPaperEntry[];
  crawled_at: string;
  total: number;
}

export interface DailyRangeResponse {
  start_date: string;
  end_date: string;
  min_upvotes: number;
  total: number;
  papers: HFPaperEntry[];
}

// --- Repo-Paper Linking ---

export async function getPaperByRepo(projectName: string): Promise<{ paper_id: string; title: string } | null> {
  try {
    const resp = await apiFetch(`/api/papers/by-repo/${encodeURIComponent(projectName)}`);
    if (!resp.ok) return null;
    return resp.json();
  } catch {
    return null;
  }
}

// --- API Functions ---

export async function searchPapers(
  query: string,
  sources: string[] = ['arxiv', 'semantic_scholar'],
  maxResults: number = 10
): Promise<PaperSearchResponse> {
  const resp = await apiFetch('/api/papers/search', {
    method: 'POST',
    body: JSON.stringify({ query, sources, max_results: maxResults }),
  });
  if (!resp.ok) throw new Error(`Search failed: ${resp.statusText}`);
  return resp.json();
}

export interface LocalSearchResult {
  paper_id: string;
  title: string;
  summary?: string;
  authors?: string[];
  upvotes?: number;
  ai_keywords?: string[];
  github_repo?: string | null;
  organization?: string | null;
  source_type: 'daily' | 'library';
  date?: string;
  is_processed?: boolean;
  status?: string;
  // HF paper fields (when from daily)
  num_comments?: number;
  github_stars?: number | null;
  thumbnail_url?: string | null;
  submitted_by?: string | null;
  submitted_at?: string;
  source?: string;
  ai_summary?: string | null;
  published_at?: string;
}

export async function searchLocalPapers(
  query: string,
  maxResults: number = 50
): Promise<{ papers: LocalSearchResult[]; total: number; query: string }> {
  const resp = await apiFetch(`/api/papers/daily/search?q=${encodeURIComponent(query)}&max_results=${maxResults}`);
  if (!resp.ok) throw new Error(`Local search failed: ${resp.statusText}`);
  return resp.json();
}

export async function startPaperRead(params: {
  query?: string;
  paper_url?: string;
  arxiv_id?: string;
  auto_build_repos?: boolean;
  max_papers?: number;
}): Promise<PaperReadResponse> {
  const resp = await apiFetch('/api/papers/read', {
    method: 'POST',
    body: JSON.stringify(params),
  });
  if (!resp.ok) throw new Error(`Read paper failed: ${resp.statusText}`);
  return resp.json();
}

export async function getPaperStatus(taskId: string): Promise<PaperStatus> {
  const resp = await apiFetch(`/api/papers/status/${encodeURIComponent(taskId)}`);
  if (!resp.ok) throw new Error(`Status check failed: ${resp.statusText}`);
  return resp.json();
}

export async function listPapers(): Promise<{ papers: PaperListItem[]; total: number }> {
  const resp = await apiFetch('/api/papers/list');
  if (!resp.ok) throw new Error(`List papers failed: ${resp.statusText}`);
  return resp.json();
}

export async function getPaperDoc(paperId: string): Promise<PaperReadingDoc> {
  const resp = await apiFetch(`/api/papers/${encodeURIComponent(paperId)}/doc`);
  if (!resp.ok) throw new Error(`Get paper doc failed: ${resp.statusText}`);
  return resp.json();
}

export async function getPaperDetail(paperId: string): Promise<PaperMetadata & { _status?: any; _doc?: any; _has_doc?: boolean }> {
  const resp = await apiFetch(`/api/papers/${encodeURIComponent(paperId)}`);
  if (!resp.ok) throw new Error(`Get paper failed: ${resp.statusText}`);
  return resp.json();
}

export async function deletePaper(paperId: string): Promise<void> {
  const resp = await apiFetch(`/api/papers/${encodeURIComponent(paperId)}`, {
    method: 'DELETE',
  });
  if (!resp.ok) throw new Error(`Delete paper failed: ${resp.statusText}`);
}

export function getPaperPdfUrl(paperId: string): string {
  return `/api/papers/${encodeURIComponent(paperId)}/pdf`;
}

// --- HuggingFace Daily Papers API ---

export async function crawlDailyPapers(date?: string, force?: boolean): Promise<{ status: string; date: string; total: number; message: string }> {
  const params = new URLSearchParams();
  if (date) params.set('date', date);
  if (force) params.set('force', 'true');
  const qs = params.toString();
  const resp = await apiFetch(`/api/papers/crawl${qs ? '?' + qs : ''}`, { method: 'POST' });
  if (!resp.ok) throw new Error(`Crawl failed: ${resp.statusText}`);
  return resp.json();
}

export async function getDailyPapers(date?: string): Promise<DailyIndex> {
  const params = date ? `?date=${encodeURIComponent(date)}` : '';
  const resp = await apiFetch(`/api/papers/daily${params}`);
  if (!resp.ok) throw new Error(`Get daily papers failed: ${resp.statusText}`);
  return resp.json();
}

export async function getDailyPapersRange(
  startDate: string,
  endDate: string,
  minUpvotes: number = 0
): Promise<DailyRangeResponse> {
  const params = new URLSearchParams({
    start_date: startDate,
    end_date: endDate,
    min_upvotes: String(minUpvotes),
  });
  const resp = await apiFetch(`/api/papers/daily/range?${params}`);
  if (!resp.ok) throw new Error(`Get papers range failed: ${resp.statusText}`);
  return resp.json();
}

export interface AllPapersPage {
  papers: (HFPaperEntry & { date?: string })[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export async function getAllPapersPaginated(page: number = 1, pageSize: number = 100): Promise<AllPapersPage> {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  const resp = await apiFetch(`/api/papers/daily/all?${params}`);
  if (!resp.ok) throw new Error(`Get all papers failed: ${resp.statusText}`);
  return resp.json();
}

export async function getCrawledDates(): Promise<{ dates: string[]; total: number }> {
  const resp = await apiFetch('/api/papers/daily/dates');
  if (!resp.ok) throw new Error(`Get crawled dates failed: ${resp.statusText}`);
  return resp.json();
}

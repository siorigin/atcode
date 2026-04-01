'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { useTheme } from '@/lib/theme-context';
import { getThemeColors } from '@/lib/theme-colors';
import { getPaperDetail, listPapers, deletePaper, crawlDailyPapers, getDailyPapers, getDailyPapersRange, startPaperRead, getAllPapersPaginated, searchLocalPapers } from '@/lib/papers-api';
import type { PaperMetadata, PaperListItem, HFPaperEntry, DailyIndex, LocalSearchResult } from '@/lib/papers-api';
import type { PaperInfo } from './PaperDetailView';
import { PaperWorkspace } from '@/components/workspace/PaperWorkspace';
import { useToast } from '@/components/Toast';

type ViewTab = 'daily' | 'library';
type RangeMode = 'daily' | 'weekly' | 'monthly' | 'all';
type ViewMode = { type: 'none' } | { type: 'hf'; paper: HFPaperEntry } | { type: 'library'; paperId: string };
type SortMode = 'upvotes' | 'recent';

interface PapersContentProps {
  // Props kept for backwards compat but no longer used
}

function hfPaperToInfo(p: HFPaperEntry): PaperInfo {
  return {
    paperId: p.paper_id,
    title: p.title,
    abstract: p.summary || undefined,
    authors: p.authors,
    aiSummary: p.ai_summary || undefined,
    aiKeywords: p.ai_keywords,
    upvotes: p.upvotes,
    organization: p.organization || undefined,
    githubUrls: p.github_repo ? [p.github_repo] : [],
    githubStars: p.github_stars || undefined,
    numComments: p.num_comments,
    source: p.source,
  };
}

function metadataToInfo(m: PaperMetadata): PaperInfo {
  return {
    paperId: m.paper_id,
    title: m.title,
    abstract: m.abstract || undefined,
    authors: m.authors,
    githubUrls: m.github_urls || [],
    source: m.source,
    citations: m.citations,
    url: m.url,
  };
}

function localSearchResultToHfPaper(p: LocalSearchResult): HFPaperEntry {
  return {
    paper_id: p.paper_id,
    title: p.title,
    summary: p.summary || '',
    authors: p.authors || [],
    published_at: p.published_at || p.date || '',
    upvotes: p.upvotes || 0,
    num_comments: p.num_comments || 0,
    ai_summary: p.ai_summary || null,
    ai_keywords: p.ai_keywords || [],
    github_repo: p.github_repo || null,
    github_stars: p.github_stars ?? null,
    organization: p.organization || null,
    thumbnail_url: p.thumbnail_url || null,
    submitted_by: p.submitted_by || null,
    submitted_at: p.submitted_at || '',
    source: p.source || 'huggingface_daily',
  };
}

// --- Date range helpers ---

function getWeekRange(d: Date): { start: string; end: string; label: string } {
  const day = d.getDay();
  const diff = day === 0 ? -6 : 1 - day; // Monday start
  const monday = new Date(d);
  monday.setDate(d.getDate() + diff);
  const sunday = new Date(monday);
  sunday.setDate(monday.getDate() + 6);
  return {
    start: monday.toISOString().slice(0, 10),
    end: sunday.toISOString().slice(0, 10),
    label: `Week of ${monday.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`,
  };
}

function getMonthRange(d: Date): { start: string; end: string; label: string } {
  const start = new Date(d.getFullYear(), d.getMonth(), 1);
  const end = new Date(d.getFullYear(), d.getMonth() + 1, 0);
  return {
    start: start.toISOString().slice(0, 10),
    end: end.toISOString().slice(0, 10),
    label: d.toLocaleDateString('en-US', { month: 'long', year: 'numeric' }),
  };
}

function shiftWeek(d: Date, dir: number): Date {
  const n = new Date(d);
  n.setDate(n.getDate() + dir * 7);
  return n;
}

function shiftMonth(d: Date, dir: number): Date {
  const n = new Date(d);
  n.setMonth(n.getMonth() + dir);
  return n;
}

export default function PapersContent({}: PapersContentProps) {
  const { theme } = useTheme();
  const colors = getThemeColors(theme);
  const { showToast } = useToast();
  const searchParams = useSearchParams();
  const router = useRouter();

  const [activeTab, setActiveTab] = useState<ViewTab>('daily');
  const [rangeMode, setRangeMode] = useState<RangeMode>('daily');
  const [viewMode, setViewMode] = useState<ViewMode>({ type: 'none' });
  const [allPapers, setAllPapers] = useState<PaperListItem[]>([]);
  const [loadingPapers, setLoadingPapers] = useState(false);

  // Daily papers state
  const [dailyDate, setDailyDate] = useState('');
  const [refDate, setRefDate] = useState<Date>(new Date()); // reference date for week/month
  const [dailyPapers, setDailyPapers] = useState<HFPaperEntry[]>([]);
  const [loadingDaily, setLoadingDaily] = useState(false);
  const [dailyError, setDailyError] = useState<string | null>(null);

  // Search & sort state
  const [searchQuery, setSearchQuery] = useState('');
  const [sortMode, setSortMode] = useState<SortMode>('upvotes');
  const [showKeywordDropdown, setShowKeywordDropdown] = useState(false);
  const searchContainerRef = useRef<HTMLDivElement>(null);

  // Global search state (cross-date + library)

  // Library filter
  const [libraryFilter, setLibraryFilter] = useState('');

  // "All" mode pagination state
  const [allModePapers, setAllModePapers] = useState<HFPaperEntry[]>([]);
  const [allModePage, setAllModePage] = useState(1);
  const [allModeTotalPages, setAllModeTotalPages] = useState(0);
  const [allModeTotal, setAllModeTotal] = useState(0);
  const [allModeLoading, setAllModeLoading] = useState(false);

  // Close dropdown on click outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (searchContainerRef.current && !searchContainerRef.current.contains(e.target as Node)) {
        setShowKeywordDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Compute unique keywords with counts from current daily papers
  const keywordStats = useMemo(() => {
    const counts = new Map<string, number>();
    for (const p of dailyPapers) {
      for (const kw of (p.ai_keywords || [])) {
        const normalized = kw.toLowerCase();
        counts.set(normalized, (counts.get(normalized) || 0) + 1);
      }
    }
    return Array.from(counts.entries())
      .map(([kw, count]) => ({ keyword: kw, count }))
      .sort((a, b) => b.count - a.count || a.keyword.localeCompare(b.keyword));
  }, [dailyPapers]);

  // Filter keywords by current search input
  const filteredKeywords = useMemo(() => {
    if (!searchQuery.trim()) return keywordStats;
    const q = searchQuery.toLowerCase();
    return keywordStats.filter(k => k.keyword.includes(q));
  }, [keywordStats, searchQuery]);

  useEffect(() => {
    const today = new Date().toISOString().slice(0, 10);
    setDailyDate(today);
    setRefDate(new Date());
  }, []);

  // Load all papers on mount
  useEffect(() => {
    const load = async () => {
      setLoadingPapers(true);
      try {
        const data = await listPapers();
        setAllPapers(data.papers || []);
      } catch (e) {
        console.error('Failed to load papers:', e);
      } finally {
        setLoadingPapers(false);
      }
    };
    load();
  }, []);

  // Restore view from URL query params on mount
  const initializedFromUrl = useRef(false);
  useEffect(() => {
    if (initializedFromUrl.current) return;
    const paperId = searchParams.get('paper');
    const tab = searchParams.get('tab') as ViewTab | null;
    if (!paperId) return;
    initializedFromUrl.current = true;
    if (tab) setActiveTab(tab);
    if (tab === 'library') {
      setViewMode({ type: 'library', paperId });
    } else {
      // For daily/HF papers, we don't have the full HFPaperEntry object from the URL alone.
      // Try to find it in loaded daily papers, otherwise open as library fallback.
      // We'll set a pending paper ID and resolve it once daily papers load.
      setPendingPaperId(paperId);
    }
  }, [searchParams]);

  const [pendingPaperId, setPendingPaperId] = useState<string | null>(null);
  const [resolvingPendingPaper, setResolvingPendingPaper] = useState(false);

  // Resolve pending paper ID from current daily list or cross-cache search.
  useEffect(() => {
    if (!pendingPaperId) return;

    const foundInCurrentList = dailyPapers.find(p => p.paper_id === pendingPaperId);
    if (foundInCurrentList) {
      setActiveTab('daily');
      setViewMode({ type: 'hf', paper: foundInCurrentList });
      setPendingPaperId(null);
      setResolvingPendingPaper(false);
      return;
    }

    let cancelled = false;

    const resolvePendingPaper = async () => {
      setResolvingPendingPaper(true);
      try {
        const result = await searchLocalPapers(pendingPaperId, 20);
        if (cancelled) return;

        const exactMatch = result.papers.find(p => p.paper_id === pendingPaperId);
        if (exactMatch?.source_type === 'daily') {
          if (exactMatch.date) {
            setDailyDate(exactMatch.date);
          }
          setActiveTab('daily');
          setViewMode({ type: 'hf', paper: localSearchResultToHfPaper(exactMatch) });
          return;
        }

        if (exactMatch) {
          setActiveTab('library');
          setViewMode({ type: 'library', paperId: exactMatch.paper_id });
          return;
        }

        try {
          await getPaperDetail(pendingPaperId);
          if (cancelled) return;
          setActiveTab('library');
          setViewMode({ type: 'library', paperId: pendingPaperId });
          return;
        } catch {}

        setDailyError(`Paper ${pendingPaperId} was not found in cached daily papers or the local library.`);
      } catch (e: any) {
        if (!cancelled) {
          setDailyError(e.message || `Failed to resolve paper ${pendingPaperId}`);
        }
      } finally {
        if (!cancelled) {
          setPendingPaperId(null);
          setResolvingPendingPaper(false);
        }
      }
    };

    resolvePendingPaper();

    return () => {
      cancelled = true;
    };
  }, [pendingPaperId, dailyPapers]);

  // Update URL when viewMode changes (after initial load)
  const updateUrl = useCallback((mode: ViewMode) => {
    if (mode.type === 'none') {
      router.replace('/repos/papers', { scroll: false });
    } else if (mode.type === 'hf') {
      router.replace(`/repos/papers?paper=${encodeURIComponent(mode.paper.paper_id)}&tab=daily`, { scroll: false });
    } else if (mode.type === 'library') {
      router.replace(`/repos/papers?paper=${encodeURIComponent(mode.paperId)}&tab=library`, { scroll: false });
    }
  }, [router]);

  // Wrapper to set viewMode and sync URL
  const setViewModeWithUrl = useCallback((mode: ViewMode) => {
    setViewMode(mode);
    if (initializedFromUrl.current || mode.type !== 'none') {
      updateUrl(mode);
    }
  }, [updateUrl]);

  const loadDailyPapers = useCallback(async (date: string) => {
    setLoadingDaily(true);
    setDailyError(null);
    try {
      let data: DailyIndex;
      try {
        data = await getDailyPapers(date);
      } catch {
        await crawlDailyPapers(date);
        data = await getDailyPapers(date);
      }
      setDailyPapers(data.papers || []);
    } catch (e: any) {
      setDailyError(e.message || 'Failed to load daily papers');
      setDailyPapers([]);
    } finally {
      setLoadingDaily(false);
    }
  }, []);

  const loadRangePapers = useCallback(async (start: string, end: string) => {
    setLoadingDaily(true);
    setDailyError(null);
    try {
      const data = await getDailyPapersRange(start, end, 0);
      setDailyPapers(data.papers || []);
    } catch (e: any) {
      setDailyError(e.message || 'Failed to load papers');
      setDailyPapers([]);
    } finally {
      setLoadingDaily(false);
    }
  }, []);

  const loadAllPapersPage = useCallback(async (page: number) => {
    setAllModeLoading(true);
    setDailyError(null);
    try {
      const data = await getAllPapersPaginated(page, 100);
      setAllModePapers(data.papers as HFPaperEntry[]);
      setAllModePage(data.page);
      setAllModeTotalPages(data.total_pages);
      setAllModeTotal(data.total);
    } catch (e: any) {
      setDailyError(e.message || 'Failed to load papers');
      setAllModePapers([]);
    } finally {
      setAllModeLoading(false);
    }
  }, []);

  useEffect(() => {
    if (activeTab !== 'daily') return;
    if (rangeMode === 'daily' && dailyDate) {
      loadDailyPapers(dailyDate);
    } else if (rangeMode === 'weekly') {
      const { start, end } = getWeekRange(refDate);
      loadRangePapers(start, end);
    } else if (rangeMode === 'monthly') {
      const { start, end } = getMonthRange(refDate);
      loadRangePapers(start, end);
    } else if (rangeMode === 'all') {
      loadAllPapersPage(1);
    }
  }, [dailyDate, refDate, rangeMode, activeTab, loadDailyPapers, loadRangePapers, loadAllPapersPage]);

  // Auto-refresh: re-crawl today's papers every hour
  useEffect(() => {
    const HOUR_MS = 60 * 60 * 1000;
    const interval = setInterval(() => {
      const todayStr = new Date().toISOString().slice(0, 10);
      // Only auto-refresh when viewing today in daily mode
      if (activeTab === 'daily' && rangeMode === 'daily' && dailyDate === todayStr) {
        crawlDailyPapers(todayStr, true)
          .then(() => loadDailyPapers(todayStr))
          .catch(() => {});
      }
    }, HOUR_MS);
    return () => clearInterval(interval);
  }, [activeTab, rangeMode, dailyDate, loadDailyPapers]);

  const handleDeletePaper = useCallback(async (paperId: string) => {
    try {
      await deletePaper(paperId);
      showToast('success', 'Paper deleted');
      setAllPapers((prev) => prev.filter((p) => p.paper_id !== paperId));
    } catch (e: any) {
      showToast('error', e.message || 'Failed to delete');
    }
  }, [showToast]);

  // Use state for today to avoid SSR/client hydration mismatch
  const [today, setToday] = useState('');
  useEffect(() => { setToday(new Date().toISOString().slice(0, 10)); }, []);

  const shiftDate = (days: number) => {
    if (!dailyDate) return;
    const d = new Date(dailyDate);
    d.setDate(d.getDate() + days);
    const next = d.toISOString().slice(0, 10);
    if (next <= today) setDailyDate(next);
  };

  // Filtered & sorted daily papers
  const filteredDailyPapers = useMemo(() => {
    let papers = [...dailyPapers];

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      papers = papers.filter(p =>
        p.title.toLowerCase().includes(q) ||
        (p.ai_keywords || []).some(kw => kw.toLowerCase().includes(q)) ||
        (p.organization || '').toLowerCase().includes(q) ||
        (p.summary || '').toLowerCase().includes(q)
      );
    }

    if (sortMode === 'upvotes') {
      papers.sort((a, b) => b.upvotes - a.upvotes);
    }

    return papers;
  }, [dailyPapers, searchQuery, sortMode]);

  // Emit filtered papers context to chat via custom event
  useEffect(() => {
    if (filteredDailyPapers.length === 0) {
      window.dispatchEvent(new CustomEvent('atcode:papers-context', { detail: null }));
      return;
    }
    const summary = filteredDailyPapers.map(p => {
      const kw = (p.ai_keywords || []).slice(0, 5).join(', ');
      const org = p.organization ? ` [${p.organization}]` : '';
      const gh = p.github_repo ? ` GitHub: ${p.github_repo}` : '';
      return `- ${p.title}${org} (⬆${p.upvotes})${kw ? ` | Keywords: ${kw}` : ''}${gh}`;
    }).join('\n');
    const label = searchQuery.trim()
      ? `Filtered by "${searchQuery}" — ${filteredDailyPapers.length} / ${dailyPapers.length} papers`
      : `${filteredDailyPapers.length} papers`;
    window.dispatchEvent(new CustomEvent('atcode:papers-context', {
      detail: { label, summary },
    }));
  }, [filteredDailyPapers, dailyPapers.length, searchQuery]);

  // Clean up on unmount
  useEffect(() => {
    return () => {
      window.dispatchEvent(new CustomEvent('atcode:papers-context', { detail: null }));
    };
  }, []);

  // Filtered library papers
  const filteredLibraryPapers = useMemo(() => {
    if (!libraryFilter.trim()) return allPapers;
    const q = libraryFilter.toLowerCase();
    return allPapers.filter(p =>
      (p.title || '').toLowerCase().includes(q) ||
      (p.paper_id || '').toLowerCase().includes(q) ||
      (p.authors || []).some(a => a.toLowerCase().includes(q))
    );
  }, [allPapers, libraryFilter]);

  const refreshLibrary = useCallback(async () => {
    try {
      const data = await listPapers();
      setAllPapers(data.papers || []);
    } catch {}
  }, []);


  // HF paper detail view
  if (viewMode.type === 'hf') {
    return (
      <HFPaperDetailWrapper
        paper={viewMode.paper}
        onBack={() => { setViewModeWithUrl({ type: 'none' }); refreshLibrary(); }}
        onPipelineComplete={refreshLibrary}
      />
    );
  }

  // Library paper detail view
  if (viewMode.type === 'library') {
    return (
      <LibraryPaperDetailWrapper
        paperId={viewMode.paperId}
        onBack={() => { setViewModeWithUrl({ type: 'none' }); refreshLibrary(); }}
        onPipelineComplete={refreshLibrary}
      />
    );
  }

  if (resolvingPendingPaper) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: colors.textMuted, fontSize: 13 }}>
        Loading paper...
      </div>
    );
  }

  const tabStyle = (tab: ViewTab) => ({
    padding: '8px 18px',
    fontSize: 14,
    fontWeight: activeTab === tab ? 600 : 400,
    color: activeTab === tab ? colors.accent : colors.textMuted,
    background: 'none',
    border: 'none',
    borderBottom: activeTab === tab ? `2px solid ${colors.accent}` : '2px solid transparent',
    cursor: 'pointer' as const,
    fontFamily: "'Inter', sans-serif",
  });

  const rangeModeStyle = (mode: RangeMode) => ({
    padding: '4px 12px',
    fontSize: 12,
    fontWeight: rangeMode === mode ? 600 : 400,
    color: rangeMode === mode ? '#fff' : colors.textMuted,
    background: rangeMode === mode ? colors.accent : colors.card,
    border: `1px solid ${rangeMode === mode ? colors.accent : colors.border}`,
    borderRadius: 14,
    cursor: 'pointer' as const,
    fontFamily: "'Inter', sans-serif",
  });

  const weekRange = getWeekRange(refDate);
  const monthRange = getMonthRange(refDate);

  return (
    <div style={{ height: '100%', background: colors.bg, overflow: 'auto' }}>
      <div style={{ padding: '12px 24px 0', maxWidth: 1200, margin: '0 auto' }}>
        <div style={{ display: 'flex', gap: 4, borderBottom: `1px solid ${colors.border}`, marginBottom: 16 }}>
          <button style={tabStyle('daily')} onClick={() => setActiveTab('daily')}>
            Daily Papers{dailyPapers.length > 0 ? ` (${dailyPapers.length})` : ''}
          </button>
          <button style={tabStyle('library')} onClick={() => setActiveTab('library')}>
            Library{allPapers.length > 0 ? ` (${allPapers.length})` : ''}
          </button>
        </div>
      </div>

      <div style={{ padding: '0 24px 20px', maxWidth: 1200, margin: '0 auto' }}>
        {activeTab === 'daily' && (
          <>
            {/* Range mode tabs + navigator */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
              {/* Range mode pills */}
              <div style={{ display: 'flex', gap: 4 }}>
                <button style={rangeModeStyle('daily')} onClick={() => setRangeMode('daily')}>Daily</button>
                <button style={rangeModeStyle('weekly')} onClick={() => setRangeMode('weekly')}>Weekly</button>
                <button style={rangeModeStyle('monthly')} onClick={() => setRangeMode('monthly')}>Monthly</button>
                <button style={rangeModeStyle('all')} onClick={() => setRangeMode('all')}>All</button>
              </div>

              {rangeMode !== 'all' && (
                <div style={{ width: 1, height: 20, background: colors.border, margin: '0 4px' }} />
              )}

              {/* Navigator */}
              {rangeMode === 'daily' && (
                <>
                  <button
                    onClick={() => shiftDate(-1)}
                    style={{
                      background: colors.card, border: `1px solid ${colors.border}`,
                      borderRadius: 6, color: colors.text, cursor: 'pointer', padding: '4px 10px', fontSize: 14,
                    }}
                  >
                    &lsaquo;
                  </button>
                  <input
                    type="date"
                    value={dailyDate}
                    onChange={(e) => setDailyDate(e.target.value)}
                    max={today}
                    style={{
                      background: colors.card, border: `1px solid ${colors.border}`,
                      borderRadius: 6, color: colors.text, padding: '4px 10px',
                      fontSize: 13, fontFamily: "'Inter', sans-serif",
                    }}
                  />
                  <button
                    onClick={() => shiftDate(1)}
                    disabled={dailyDate >= today}
                    style={{
                      background: colors.card, border: `1px solid ${colors.border}`,
                      borderRadius: 6, color: dailyDate >= today ? colors.textDimmed : colors.text,
                      cursor: dailyDate >= today ? 'default' : 'pointer', padding: '4px 10px', fontSize: 14,
                    }}
                  >
                    &rsaquo;
                  </button>
                  <button
                    onClick={() => setDailyDate(today)}
                    style={{
                      background: colors.card, border: `1px solid ${colors.border}`,
                      borderRadius: 6, color: colors.textMuted, cursor: 'pointer',
                      padding: '4px 10px', fontSize: 12, fontFamily: "'Inter', sans-serif",
                    }}
                  >
                    Today
                  </button>
                </>
              )}

              {rangeMode === 'weekly' && (
                <>
                  <button
                    onClick={() => setRefDate(shiftWeek(refDate, -1))}
                    style={{
                      background: colors.card, border: `1px solid ${colors.border}`,
                      borderRadius: 6, color: colors.text, cursor: 'pointer', padding: '4px 10px', fontSize: 14,
                    }}
                  >
                    &lsaquo;
                  </button>
                  <span style={{ fontSize: 13, color: colors.text, fontWeight: 500, fontFamily: "'Inter', sans-serif", minWidth: 130, textAlign: 'center' }}>
                    {weekRange.label}
                  </span>
                  <button
                    onClick={() => setRefDate(shiftWeek(refDate, 1))}
                    disabled={weekRange.end >= today}
                    style={{
                      background: colors.card, border: `1px solid ${colors.border}`,
                      borderRadius: 6, color: weekRange.end >= today ? colors.textDimmed : colors.text,
                      cursor: weekRange.end >= today ? 'default' : 'pointer', padding: '4px 10px', fontSize: 14,
                    }}
                  >
                    &rsaquo;
                  </button>
                  <button
                    onClick={() => setRefDate(new Date())}
                    style={{
                      background: colors.card, border: `1px solid ${colors.border}`,
                      borderRadius: 6, color: colors.textMuted, cursor: 'pointer',
                      padding: '4px 10px', fontSize: 12, fontFamily: "'Inter', sans-serif",
                    }}
                  >
                    This Week
                  </button>
                </>
              )}

              {rangeMode === 'monthly' && (
                <>
                  <button
                    onClick={() => setRefDate(shiftMonth(refDate, -1))}
                    style={{
                      background: colors.card, border: `1px solid ${colors.border}`,
                      borderRadius: 6, color: colors.text, cursor: 'pointer', padding: '4px 10px', fontSize: 14,
                    }}
                  >
                    &lsaquo;
                  </button>
                  <span style={{ fontSize: 13, color: colors.text, fontWeight: 500, fontFamily: "'Inter', sans-serif", minWidth: 130, textAlign: 'center' }}>
                    {monthRange.label}
                  </span>
                  <button
                    onClick={() => setRefDate(shiftMonth(refDate, 1))}
                    disabled={monthRange.end >= today}
                    style={{
                      background: colors.card, border: `1px solid ${colors.border}`,
                      borderRadius: 6, color: monthRange.end >= today ? colors.textDimmed : colors.text,
                      cursor: monthRange.end >= today ? 'default' : 'pointer', padding: '4px 10px', fontSize: 14,
                    }}
                  >
                    &rsaquo;
                  </button>
                  <button
                    onClick={() => setRefDate(new Date())}
                    style={{
                      background: colors.card, border: `1px solid ${colors.border}`,
                      borderRadius: 6, color: colors.textMuted, cursor: 'pointer',
                      padding: '4px 10px', fontSize: 12, fontFamily: "'Inter', sans-serif",
                    }}
                  >
                    This Month
                  </button>
                </>
              )}

              <div style={{ flex: 1 }} />

              {rangeMode !== 'all' && <>
              {/* Refresh button — force re-crawl from HF */}
              <button
                onClick={async () => {
                  setLoadingDaily(true);
                  setDailyError(null);
                  try {
                    const targetDate = rangeMode === 'daily' ? dailyDate : undefined;
                    if (rangeMode === 'daily' && targetDate) {
                      await crawlDailyPapers(targetDate, true);
                      await loadDailyPapers(targetDate);
                    } else {
                      // For week/month, force crawl each day then reload
                      const range = rangeMode === 'weekly' ? getWeekRange(refDate) : getMonthRange(refDate);
                      await crawlDailyPapers(range.start, true);
                      await loadRangePapers(range.start, range.end);
                    }
                    showToast('success', 'Papers refreshed');
                  } catch (e: any) {
                    setDailyError(e.message || 'Refresh failed');
                  } finally {
                    setLoadingDaily(false);
                  }
                }}
                disabled={loadingDaily}
                style={{
                  background: colors.card, border: `1px solid ${colors.border}`,
                  borderRadius: 6, color: loadingDaily ? colors.textDimmed : colors.accent, cursor: loadingDaily ? 'default' : 'pointer',
                  padding: '4px 10px', fontSize: 12, fontFamily: "'Inter', sans-serif",
                  display: 'flex', alignItems: 'center', gap: 4,
                }}
                title="Force refresh from HuggingFace"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                  style={{ animation: loadingDaily ? 'spin 1s linear infinite' : 'none' }}>
                  <polyline points="23 4 23 10 17 10" /><polyline points="1 20 1 14 7 14" />
                  <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
                </svg>
                Refresh
              </button>

              {/* Sort toggle */}
              <button
                onClick={() => setSortMode(sortMode === 'upvotes' ? 'recent' : 'upvotes')}
                style={{
                  background: colors.card, border: `1px solid ${colors.border}`,
                  borderRadius: 6, color: colors.textMuted, cursor: 'pointer',
                  padding: '4px 10px', fontSize: 12, fontFamily: "'Inter', sans-serif",
                }}
                title={`Sort by ${sortMode === 'upvotes' ? 'recent' : 'upvotes'}`}
              >
                {sortMode === 'upvotes' ? 'Top' : 'Recent'}
              </button>

              {dailyPapers.length > 0 && (
                <span style={{ fontSize: 12, color: colors.textMuted }}>
                  {filteredDailyPapers.length === dailyPapers.length
                    ? `${dailyPapers.length} papers`
                    : `${filteredDailyPapers.length} / ${dailyPapers.length}`}
                </span>
              )}
              </>}
            </div>

            {/* "All" mode — paginated view of all papers */}
            {rangeMode === 'all' ? (
              <>
                {allModeLoading && (
                  <div style={{ textAlign: 'center', padding: '40px 0', color: colors.textMuted, fontSize: 13 }}>
                    Loading papers...
                  </div>
                )}

                {dailyError && (
                  <div style={{ textAlign: 'center', padding: '40px 0', color: colors.error, fontSize: 13 }}>
                    {dailyError}
                  </div>
                )}

                {!allModeLoading && !dailyError && allModePapers.length === 0 && (
                  <div style={{ textAlign: 'center', padding: '40px 0', color: colors.textMuted, fontSize: 13 }}>
                    No papers found
                  </div>
                )}

                {!allModeLoading && allModePapers.length > 0 && (
                  <>
                    {/* Pagination info */}
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                      <span style={{ fontSize: 12, color: colors.textMuted }}>
                        {allModeTotal} papers total — Page {allModePage} / {allModeTotalPages}
                      </span>
                      <div style={{ display: 'flex', gap: 4 }}>
                        <button
                          onClick={() => loadAllPapersPage(allModePage - 1)}
                          disabled={allModePage <= 1}
                          style={{
                            background: colors.card, border: `1px solid ${colors.border}`,
                            borderRadius: 6, color: allModePage <= 1 ? colors.textDimmed : colors.text,
                            cursor: allModePage <= 1 ? 'default' : 'pointer', padding: '4px 10px', fontSize: 13,
                          }}
                        >
                          &lsaquo; Prev
                        </button>
                        <button
                          onClick={() => loadAllPapersPage(allModePage + 1)}
                          disabled={allModePage >= allModeTotalPages}
                          style={{
                            background: colors.card, border: `1px solid ${colors.border}`,
                            borderRadius: 6, color: allModePage >= allModeTotalPages ? colors.textDimmed : colors.text,
                            cursor: allModePage >= allModeTotalPages ? 'default' : 'pointer', padding: '4px 10px', fontSize: 13,
                          }}
                        >
                          Next &rsaquo;
                        </button>
                      </div>
                    </div>

                    <div style={{
                      display: 'grid',
                      gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
                      gap: 16,
                    }}>
                      {allModePapers.map((paper) => (
                        <HFPaperCard
                          key={paper.paper_id}
                          paper={paper}
                          colors={colors}
                          theme={theme}
                          onReadPaper={() => setViewModeWithUrl({ type: 'hf', paper })}
                        />
                      ))}
                    </div>

                    {/* Bottom pagination */}
                    {allModeTotalPages > 1 && (
                      <div style={{ display: 'flex', justifyContent: 'center', gap: 4, marginTop: 16 }}>
                        <button
                          onClick={() => loadAllPapersPage(allModePage - 1)}
                          disabled={allModePage <= 1}
                          style={{
                            background: colors.card, border: `1px solid ${colors.border}`,
                            borderRadius: 6, color: allModePage <= 1 ? colors.textDimmed : colors.text,
                            cursor: allModePage <= 1 ? 'default' : 'pointer', padding: '6px 14px', fontSize: 13,
                          }}
                        >
                          &lsaquo; Prev
                        </button>
                        <span style={{ padding: '6px 12px', fontSize: 13, color: colors.textMuted }}>
                          {allModePage} / {allModeTotalPages}
                        </span>
                        <button
                          onClick={() => loadAllPapersPage(allModePage + 1)}
                          disabled={allModePage >= allModeTotalPages}
                          style={{
                            background: colors.card, border: `1px solid ${colors.border}`,
                            borderRadius: 6, color: allModePage >= allModeTotalPages ? colors.textDimmed : colors.text,
                            cursor: allModePage >= allModeTotalPages ? 'default' : 'pointer', padding: '6px 14px', fontSize: 13,
                          }}
                        >
                          Next &rsaquo;
                        </button>
                      </div>
                    )}
                  </>
                )}
              </>
            ) : (
              <>
                {/* Search input with keyword dropdown (for daily/weekly/monthly) */}
                <div style={{ marginBottom: 14, position: 'relative' }} ref={searchContainerRef}>
                  <input
                    type="text"
                    placeholder="Filter by title, keyword, or organization..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    onFocus={(e) => {
                      e.currentTarget.style.borderColor = colors.accent;
                      if (keywordStats.length > 0) setShowKeywordDropdown(true);
                    }}
                    onBlur={(e) => { e.currentTarget.style.borderColor = colors.border; }}
                    style={{
                      width: '100%',
                      background: colors.card,
                      border: `1px solid ${colors.border}`,
                      borderRadius: 8,
                      color: colors.text,
                      padding: '8px 14px',
                      fontSize: 13,
                      fontFamily: "'Inter', sans-serif",
                      outline: 'none',
                      boxSizing: 'border-box',
                    }}
                  />
                  {searchQuery && (
                    <button
                      onClick={() => { setSearchQuery(''); setShowKeywordDropdown(false); }}
                      style={{
                        position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)',
                        background: 'none', border: 'none', color: colors.textMuted,
                        cursor: 'pointer', fontSize: 14, padding: '2px 4px', lineHeight: 1,
                      }}
                      title="Clear filter"
                    >×</button>
                  )}
                  {showKeywordDropdown && filteredKeywords.length > 0 && (
                    <div style={{
                      position: 'absolute', top: '100%', left: 0, right: 0,
                      background: colors.card, border: `1px solid ${colors.border}`,
                      borderRadius: 8, marginTop: 4, maxHeight: 280, overflowY: 'auto',
                      zIndex: 100, boxShadow: '0 4px 16px rgba(0,0,0,0.2)',
                      padding: '6px 0',
                    }}>
                      <div style={{
                        padding: '4px 12px 6px', fontSize: 11, color: colors.textMuted,
                        fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px',
                      }}>
                        Keywords
                      </div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, padding: '2px 10px 8px' }}>
                        {filteredKeywords.map(({ keyword, count }) => (
                          <button
                            key={keyword}
                            onMouseDown={(e) => {
                              e.preventDefault();
                              setSearchQuery(keyword);
                              setShowKeywordDropdown(false);
                            }}
                            style={{
                              background: searchQuery.toLowerCase() === keyword ? colors.accent : colors.bg,
                              color: searchQuery.toLowerCase() === keyword ? '#fff' : colors.text,
                              border: `1px solid ${colors.border}`,
                              borderRadius: 14, padding: '3px 10px', fontSize: 12,
                              cursor: 'pointer', fontFamily: "'Inter', sans-serif",
                              display: 'flex', alignItems: 'center', gap: 4,
                              transition: 'background 0.15s, color 0.15s',
                            }}
                          >
                            {keyword}
                            <span style={{
                              fontSize: 10, opacity: 0.6, fontWeight: 600,
                            }}>{count}</span>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                {loadingDaily && (
                  <div style={{ textAlign: 'center', padding: '40px 0', color: colors.textMuted, fontSize: 13 }}>
                    Loading papers...
                  </div>
                )}

                {dailyError && (
                  <div style={{ textAlign: 'center', padding: '40px 0', color: colors.error, fontSize: 13 }}>
                    {dailyError}
                  </div>
                )}

                {!loadingDaily && !dailyError && dailyPapers.length === 0 && (
                  <div style={{ textAlign: 'center', padding: '40px 0', color: colors.textMuted, fontSize: 13 }}>
                    No papers found for {rangeMode === 'daily' ? dailyDate : rangeMode === 'weekly' ? weekRange.label : monthRange.label}
                  </div>
                )}

                {!loadingDaily && filteredDailyPapers.length > 0 && (
                  <div style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
                    gap: 16,
                  }}>
                    {filteredDailyPapers.map((paper) => (
                      <HFPaperCard
                        key={paper.paper_id}
                        paper={paper}
                        colors={colors}
                        theme={theme}
                        onReadPaper={() => setViewModeWithUrl({ type: 'hf', paper })}
                      />
                    ))}
                  </div>
                )}

                {!loadingDaily && searchQuery && filteredDailyPapers.length === 0 && dailyPapers.length > 0 && (
                  <div style={{ textAlign: 'center', padding: '40px 0', color: colors.textMuted, fontSize: 13 }}>
                    No papers match &ldquo;{searchQuery}&rdquo;
                  </div>
                )}
              </>
            )}
          </>
        )}

        {activeTab === 'library' && (
          <>
            {/* URL input for starting paper pipeline */}
            <PaperUrlInput colors={colors} showToast={showToast} onPipelineStarted={refreshLibrary} />

            {allPapers.length > 0 && (
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
                  <h3 style={{ fontSize: 14, fontWeight: 600, color: colors.text, margin: 0, fontFamily: "'Inter', sans-serif" }}>
                    All Papers ({filteredLibraryPapers.length}{libraryFilter ? ` / ${allPapers.length}` : ''})
                  </h3>
                  <div style={{ flex: 1, position: 'relative' }}>
                    <input
                      type="text"
                      placeholder="Filter library by title, ID, or author..."
                      value={libraryFilter}
                      onChange={(e) => setLibraryFilter(e.target.value)}
                      style={{
                        width: '100%',
                        background: colors.card,
                        border: `1px solid ${colors.border}`,
                        borderRadius: 6,
                        color: colors.text,
                        padding: '5px 28px 5px 10px',
                        fontSize: 12,
                        fontFamily: "'Inter', sans-serif",
                        outline: 'none',
                        boxSizing: 'border-box',
                      }}
                      onFocus={(e) => { e.currentTarget.style.borderColor = colors.accent; }}
                      onBlur={(e) => { e.currentTarget.style.borderColor = colors.border; }}
                    />
                    {libraryFilter && (
                      <button
                        onClick={() => setLibraryFilter('')}
                        style={{
                          position: 'absolute', right: 6, top: '50%', transform: 'translateY(-50%)',
                          background: 'none', border: 'none', color: colors.textMuted,
                          cursor: 'pointer', fontSize: 13, padding: '0 2px', lineHeight: 1,
                        }}
                      >×</button>
                    )}
                  </div>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 10 }}>
                  {filteredLibraryPapers.map((paper) => (
                    <PaperMiniCard
                      key={paper.paper_id}
                      paper={paper}
                      colors={colors}
                      onClick={() => paper.status === 'completed' && setViewModeWithUrl({ type: 'library', paperId: paper.paper_id })}
                      onDelete={() => handleDeletePaper(paper.paper_id)}
                    />
                  ))}
                </div>
                {libraryFilter && filteredLibraryPapers.length === 0 && (
                  <div style={{ textAlign: 'center', padding: '30px 0', color: colors.textMuted, fontSize: 13 }}>
                    No library papers match &ldquo;{libraryFilter}&rdquo;
                  </div>
                )}
              </div>
            )}

            {allPapers.length === 0 && !loadingPapers && (
              <div style={{ textAlign: 'center', padding: '60px 24px' }}>
                <div style={{ fontSize: 48, marginBottom: 16, opacity: 0.2 }}>&#128218;</div>
                <p style={{ color: colors.textMuted, fontSize: 15, fontWeight: 500, marginBottom: 6 }}>
                  Start chatting to discover papers
                </p>
                <p style={{ color: colors.textDimmed, fontSize: 13 }}>
                  Ask the AI to search for papers, read arXiv IDs, or explore topics
                </p>
              </div>
            )}

            {loadingPapers && (
              <div style={{ textAlign: 'center', padding: '40px 0', color: colors.textMuted, fontSize: 13 }}>
                Loading papers...
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ---- HF Daily Paper card (HuggingFace-style vertical card with thumbnail) ----

function HFPaperCard({
  paper,
  colors,
  theme,
  onReadPaper,
}: {
  paper: HFPaperEntry;
  colors: ReturnType<typeof getThemeColors>;
  theme: string;
  onReadPaper: () => void;
}) {
  const [imgError, setImgError] = useState(false);
  const [hovered, setHovered] = useState(false);

  const thumbnailSrc = paper.thumbnail_url
    ? `/api/papers/thumbnail?url=${encodeURIComponent(paper.thumbnail_url)}`
    : null;

  const githubColor = colors.success;
  const arxivColor = theme === 'dark' ? '#e8685d' : '#c44035';

  // Generate a deterministic gradient for papers without thumbnails
  const gradientBg = useMemo(() => {
    let hash = 0;
    for (let i = 0; i < paper.paper_id.length; i++) {
      hash = paper.paper_id.charCodeAt(i) + ((hash << 5) - hash);
    }
    const h1 = Math.abs(hash) % 360;
    const h2 = (h1 + 40) % 360;
    return `linear-gradient(135deg, hsl(${h1}, 40%, ${theme === 'dark' ? '25%' : '85%'}), hsl(${h2}, 50%, ${theme === 'dark' ? '18%' : '75%'}))`;
  }, [paper.paper_id, theme]);

  return (
    <div
      onClick={onReadPaper}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        background: colors.card,
        border: `1px solid ${hovered ? colors.accent : colors.border}`,
        borderRadius: 12,
        overflow: 'hidden',
        cursor: 'pointer',
        transition: 'border-color 0.2s, box-shadow 0.2s, transform 0.15s',
        transform: hovered ? 'translateY(-2px)' : 'none',
        boxShadow: hovered ? `0 4px 16px ${colors.accent}20` : 'none',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* Thumbnail area */}
      <div style={{
        position: 'relative',
        width: '100%',
        aspectRatio: '16 / 9',
        background: gradientBg,
        overflow: 'hidden',
      }}>
        {thumbnailSrc && !imgError ? (
          <img
            src={thumbnailSrc}
            alt=""
            onError={() => setImgError(true)}
            style={{
              width: '100%',
              height: '100%',
              objectFit: 'cover',
              display: 'block',
            }}
          />
        ) : (
          <div style={{
            width: '100%',
            height: '100%',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}>
            <span style={{
              fontSize: 13,
              color: theme === 'dark' ? 'rgba(255,255,255,0.3)' : 'rgba(0,0,0,0.15)',
              fontWeight: 600,
              fontFamily: "'Inter', sans-serif",
            }}>
              {paper.paper_id}
            </span>
          </div>
        )}
        {/* Submitted by overlay */}
        {paper.submitted_by && (
          <div style={{
            position: 'absolute',
            bottom: 6,
            left: 6,
            background: 'rgba(0,0,0,0.6)',
            color: '#fff',
            fontSize: 10,
            padding: '2px 8px',
            borderRadius: 8,
            fontWeight: 500,
          }}>
            {paper.submitted_by}
          </div>
        )}
        {/* Organization overlay */}
        {paper.organization && !paper.submitted_by && (
          <div style={{
            position: 'absolute',
            bottom: 6,
            left: 6,
            background: 'rgba(0,0,0,0.6)',
            color: '#fff',
            fontSize: 10,
            padding: '2px 8px',
            borderRadius: 8,
            fontWeight: 500,
          }}>
            {paper.organization}
          </div>
        )}
      </div>

      {/* Card body */}
      <div style={{ padding: '12px 14px', flex: 1, display: 'flex', flexDirection: 'column', gap: 6 }}>
        {/* Upvotes + Title row */}
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            minWidth: 36,
            padding: '2px 0',
            background: colors.accent + '10',
            borderRadius: 6,
            flexShrink: 0,
          }}>
            <span style={{ fontSize: 10, lineHeight: 1, color: colors.accent }}>&#9650;</span>
            <span style={{ fontSize: 13, fontWeight: 700, color: colors.accent }}>{paper.upvotes}</span>
          </div>
          <div style={{
            fontSize: 14,
            fontWeight: 600,
            color: colors.text,
            lineHeight: 1.4,
            display: '-webkit-box',
            WebkitLineClamp: 3,
            WebkitBoxOrient: 'vertical' as any,
            overflow: 'hidden',
          }}>
            {paper.title}
          </div>
        </div>

        {/* Authors */}
        {paper.authors && paper.authors.length > 0 && (
          <div style={{
            fontSize: 11,
            color: colors.textDimmed,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}>
            {paper.authors.slice(0, 3).join(', ')}{paper.authors.length > 3 ? ` +${paper.authors.length - 3}` : ''}
          </div>
        )}

        {/* Bottom row: github stars + comments */}
        <div style={{ marginTop: 'auto', display: 'flex', gap: 8, fontSize: 11, color: colors.textMuted, alignItems: 'center' }}>
          <span style={{
            background: arxivColor + '18',
            color: arxivColor,
            padding: '1px 6px',
            borderRadius: 8,
            fontWeight: 600,
            fontSize: 10,
          }}>
            {paper.paper_id}
          </span>
          {paper.github_repo && (
            <span
              onClick={(e) => {
                e.stopPropagation();
                window.open(paper.github_repo!, '_blank');
              }}
              style={{
                background: githubColor + '18',
                color: githubColor,
                padding: '1px 6px',
                borderRadius: 8,
                fontWeight: 500,
                fontSize: 10,
                cursor: 'pointer',
              }}
            >
              {paper.github_stars != null ? `${paper.github_stars}` : 'GitHub'}
            </span>
          )}
          {paper.num_comments > 0 && (
            <span style={{ fontSize: 10, color: colors.textDimmed }}>{paper.num_comments} comments</span>
          )}
        </div>
      </div>
    </div>
  );
}

// ---- Wrapper: HF paper -> PaperWorkspace ----
function HFPaperDetailWrapper({
  paper,
  onBack,
  onPipelineComplete,
}: {
  paper: HFPaperEntry;
  onBack: () => void;
  onPipelineComplete?: () => void;
}) {
  const [hasDoc, setHasDoc] = useState(false);

  const checkDoc = useCallback(async () => {
    try {
      const resp = await getPaperDetail(paper.paper_id);
      // Check if the reading doc has been generated (not just metadata)
      if (resp && (resp._has_doc || resp._doc)) {
        setHasDoc(true);
        return true;
      }
    } catch {}
    return false;
  }, [paper.paper_id]);

  useEffect(() => { checkDoc(); }, [checkDoc]);

  return (
    <PaperWorkspace
      paper={hfPaperToInfo(paper)}
      hasDoc={hasDoc}
      onBack={onBack}
      onPipelineComplete={() => {
        setHasDoc(true);
        onPipelineComplete?.();
      }}
    />
  );
}

// ---- Wrapper: Library paper -> PaperWorkspace ----
function LibraryPaperDetailWrapper({
  paperId,
  onBack,
  onPipelineComplete,
}: {
  paperId: string;
  onBack: () => void;
  onPipelineComplete?: () => void;
}) {
  const { theme } = useTheme();
  const colors = getThemeColors(theme);
  const [metadata, setMetadata] = useState<PaperMetadata | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const data = await getPaperDetail(paperId);
        setMetadata(data);
      } catch (e: any) {
        setError(e.message || 'Failed to load paper');
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [paperId]);

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: colors.textMuted, fontSize: 13 }}>
        Loading...
      </div>
    );
  }

  if (error || !metadata) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: colors.error, fontSize: 13 }}>
        {error || 'Paper not found'}
      </div>
    );
  }

  return (
    <PaperWorkspace
      paper={metadataToInfo(metadata)}
      hasDoc={true}
      onBack={onBack}
      onPipelineComplete={onPipelineComplete}
    />
  );
}

// ---- Mini paper card for grid (library tab) ----
function PaperMiniCard({
  paper,
  colors,
  onClick,
  onDelete,
}: {
  paper: PaperListItem;
  colors: ReturnType<typeof getThemeColors>;
  onClick: () => void;
  onDelete: () => void;
}) {
  const statusColor = paper.status === 'completed' ? colors.success
    : paper.status === 'failed' ? colors.error
    : colors.textMuted;

  return (
    <div
      onClick={onClick}
      style={{
        background: colors.card,
        border: `1px solid ${colors.border}`,
        borderRadius: 10,
        padding: 16,
        cursor: paper.status === 'completed' ? 'pointer' : 'default',
        transition: 'border-color 0.2s',
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
      }}
      onMouseEnter={(e) => {
        if (paper.status === 'completed') e.currentTarget.style.borderColor = colors.accent;
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = colors.border;
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div
          style={{
            fontSize: 14, fontWeight: 600, color: colors.text,
            overflow: 'hidden', textOverflow: 'ellipsis',
            display: '-webkit-box', WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical' as any,
            flex: 1, lineHeight: 1.4,
          }}
        >
          {paper.title || paper.paper_id}
        </div>
      </div>

      {paper.authors && paper.authors.length > 0 && (
        <div style={{
          fontSize: 11, color: colors.textDimmed,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {paper.authors.slice(0, 3).join(', ')}{paper.authors.length > 3 ? ` +${paper.authors.length - 3}` : ''}
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 11 }}>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <span style={{
            background: colors.accent + '15', color: colors.accent,
            padding: '2px 8px', borderRadius: 10, fontWeight: 600,
          }}>
            {paper.source === 'semantic_scholar' ? 'S2' : paper.source === 'papers_with_code' ? 'PWC' : paper.source === 'huggingface' ? 'HF' : 'arXiv'}
          </span>
          <span style={{
            background: statusColor + '15', color: statusColor,
            padding: '2px 8px', borderRadius: 10, fontWeight: 600,
          }}>
            {paper.status}
          </span>
        </div>
        <button
          onClick={(e) => { e.stopPropagation(); onDelete(); }}
          style={{
            background: 'none', border: 'none', color: colors.textDimmed,
            cursor: 'pointer', padding: '2px 4px', fontSize: 13, opacity: 0.5,
          }}
          onMouseEnter={(e) => { e.currentTarget.style.opacity = '1'; e.currentTarget.style.color = colors.error; }}
          onMouseLeave={(e) => { e.currentTarget.style.opacity = '0.5'; e.currentTarget.style.color = colors.textDimmed; }}
          title="Delete paper"
        >
          &times;
        </button>
      </div>
    </div>
  );
}

// ---- URL input for starting paper reading pipeline ----
function PaperUrlInput({
  colors,
  showToast,
  onPipelineStarted,
}: {
  colors: ReturnType<typeof getThemeColors>;
  showToast: (type: 'success' | 'error' | 'info', message: string) => void;
  onPipelineStarted: () => void;
}) {
  const [urlInput, setUrlInput] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    const val = urlInput.trim();
    if (!val || submitting) return;

    // Parse input: arxiv URL (abs/pdf/html with optional version), raw arxiv ID, or general URL
    const arxivUrlMatch = val.match(/arxiv\.org\/(?:abs|pdf|html)\/(\d{4}\.\d{4,5})(?:v\d+)?/);
    const arxivIdMatch = val.match(/^(\d{4}\.\d{4,5})(?:v\d+)?$/);

    let params: { paper_url?: string; arxiv_id?: string };
    if (arxivUrlMatch) {
      params = { arxiv_id: arxivUrlMatch[1] };
    } else if (arxivIdMatch) {
      params = { arxiv_id: arxivIdMatch[1] };
    } else {
      params = { paper_url: val };
    }

    setSubmitting(true);
    try {
      const resp = await startPaperRead(params);
      showToast('success', `Pipeline started (${resp.task_id.slice(0, 8)}...)`);
      setUrlInput('');
      // Refresh library after a delay to pick up the new entry
      setTimeout(onPipelineStarted, 2000);
    } catch (e: any) {
      showToast('error', e.message || 'Failed to start pipeline');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <input
          type="text"
          placeholder="Paste arxiv URL or paper ID (e.g., 2504.20073)"
          value={urlInput}
          onChange={(e) => setUrlInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') handleSubmit(); }}
          style={{
            flex: 1,
            background: colors.card,
            border: `1px solid ${colors.border}`,
            borderRadius: 8,
            color: colors.text,
            padding: '8px 14px',
            fontSize: 13,
            fontFamily: "'Inter', sans-serif",
            outline: 'none',
          }}
          onFocus={(e) => { e.currentTarget.style.borderColor = colors.accent; }}
          onBlur={(e) => { e.currentTarget.style.borderColor = colors.border; }}
        />
        <button
          onClick={handleSubmit}
          disabled={!urlInput.trim() || submitting}
          style={{
            padding: '8px 16px',
            background: !urlInput.trim() || submitting ? colors.card : colors.accent,
            color: !urlInput.trim() || submitting ? colors.textDimmed : '#fff',
            border: `1px solid ${!urlInput.trim() || submitting ? colors.border : colors.accent}`,
            borderRadius: 8,
            fontSize: 13,
            fontWeight: 600,
            fontFamily: "'Inter', sans-serif",
            cursor: !urlInput.trim() || submitting ? 'not-allowed' : 'pointer',
            whiteSpace: 'nowrap',
          }}
        >
          {submitting ? 'Starting...' : 'Read Paper'}
        </button>
      </div>
    </div>
  );
}

# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""API routes for paper reading feature."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

from api.services.task_queue import TaskStatus, TaskType, get_task_manager
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from loguru import logger
from paper.downloader import PaperDownloader
from paper.hf_crawler import (
    HFDailyPapersCrawler,
    _get_daily_dir,
    get_available_dates,
    get_daily_index,
    get_papers_in_range,
)
from paper.models import (
    PaperReadRequest,
    PaperReadResponse,
    PaperSearchRequest,
    PaperSearchResponse,
)
from paper.pipeline import PaperReadingPipeline
from paper.search import PaperSearchService

router = APIRouter()


def _detect_headings(content: str) -> list[tuple[int, str, int]]:
    """Detect section headings in a content blob. Returns (line_index, title, level)."""
    import re

    lines = content.split("\n")
    headings: list[tuple[int, str, int]] = []

    # Pattern 1: numbered headings "1. Introduction", "2.1. Method"
    # Each number segment must be 1-2 digits to avoid matching years like "2023."
    numbered_re = re.compile(
        r"^(\d{1,2}(?:\.\d{1,2})*)[.\s]+([A-Z][A-Za-z\s:,&()\-/]{2,80})$"
    )
    # Pattern 2: standalone title lines preceded by blank (common in some MinerU outputs)
    # Must be short, capitalized, and look like a heading
    standalone_titles = {
        "abstract", "introduction", "related work", "background", "method", "methods",
        "methodology", "approach", "experiments", "experiment", "evaluation",
        "results", "discussion", "conclusion", "conclusions", "limitations",
        "acknowledgments", "acknowledgements", "appendix", "references",
        "bibliography", "future work", "analysis", "setup", "dataset",
        "implementation", "training", "inference", "ablation", "ablation study",
    }

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        # Try numbered heading
        m = numbered_re.match(stripped)
        if m:
            num_part = m.group(1)
            title = m.group(2).strip()
            level = min(num_part.count(".") + 1, 3)
            headings.append((i, title, level))
            continue

        # Try standalone heading: short line, preceded by blank, looks like a section title
        if (
            3 < len(stripped) < 60
            and stripped[0].isupper()
            and i > 0
            and len(lines[i - 1].strip()) < 3
            and stripped.lower() in standalone_titles
        ):
            headings.append((i, stripped, 1))

    return headings


def _extract_subsection(content: str, title_query: str, _heading_re=None) -> dict | None:
    """Extract a subsection from a large content blob by matching heading title."""
    headings = _detect_headings(content)
    lines = content.split("\n")

    for idx, (line_i, title, level) in enumerate(headings):
        if title.lower() == title_query.lower():
            start = line_i + 1
            # End at next heading or end of content
            end = headings[idx + 1][0] if idx + 1 < len(headings) else len(lines)
            sub_content = "\n".join(lines[start:end]).strip()
            return {"title": title, "level": level, "content": sub_content}

    return None

# Module-level cache: project_name -> {paper_id, title} or None
_repo_paper_cache: dict[str, dict | None] = {}


def _scan_papers_for_repo(project_name: str) -> dict | None:
    """Scan paper docs on disk for one whose code_analysis.project_name matches."""
    if project_name in _repo_paper_cache:
        return _repo_paper_cache[project_name]

    import json as _json

    from paper.downloader import get_papers_dir

    papers_dir = get_papers_dir()
    if not papers_dir.exists():
        _repo_paper_cache[project_name] = None
        return None

    for paper_dir in papers_dir.iterdir():
        if not paper_dir.is_dir():
            continue
        doc_path = paper_dir / "doc" / "paper_reading.json"
        if not doc_path.exists():
            continue
        try:
            doc = _json.loads(doc_path.read_text(encoding="utf-8"))
            ca = doc.get("code_analysis")
            if ca and ca.get("project_name") == project_name:
                paper = doc.get("paper", {})
                result = {
                    "paper_id": paper.get("paper_id", paper_dir.name),
                    "title": paper.get("title", ""),
                }
                _repo_paper_cache[project_name] = result
                return result
        except Exception:
            continue

    _repo_paper_cache[project_name] = None
    return None


@router.get("/by-repo/{project_name}")
async def get_paper_by_repo(project_name: str):
    """Find the source paper for a repo built from the paper reading pipeline."""
    result = _scan_papers_for_repo(project_name)
    if not result:
        raise HTTPException(status_code=404, detail="No paper found for this repo")
    return result


@router.post("/search", response_model=PaperSearchResponse)
async def search_papers(req: PaperSearchRequest):
    """Search for papers across multiple sources."""
    try:
        from core.config import settings
        service = PaperSearchService(s2_api_key=settings.S2_API_KEY)
        papers = await service.search(
            query=req.query,
            sources=req.sources,
            max_results=req.max_results,
        )
        return PaperSearchResponse(papers=papers, total=len(papers))
    except Exception as e:
        logger.error(f"Paper search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/read", response_model=PaperReadResponse)
async def read_paper(req: PaperReadRequest):
    """Start the complete paper reading pipeline. Returns a task_id for progress tracking."""
    task_manager = get_task_manager()

    task_id = await task_manager.create_task(
        task_type=TaskType.PAPER_READ.value,
        repo_name=req.query or req.arxiv_id or req.paper_url or "paper",
        initial_message="Starting paper reading pipeline...",
    )

    async def run_pipeline(t_id: str) -> None:
        try:
            await task_manager.update_task(
                t_id,
                status=TaskStatus.RUNNING,
                progress=0,
                step="starting",
                status_message="Starting paper reading pipeline...",
            )

            async def on_progress(p: float, msg: str) -> None:
                await task_manager.update_task(
                    t_id,
                    progress=int(p * 100),
                    step=msg,
                    status_message=msg,
                )

            pipeline = PaperReadingPipeline(on_progress=on_progress)
            docs = await pipeline.run(
                query=req.query,
                paper_url=req.paper_url,
                arxiv_id=req.arxiv_id,
                auto_build_repos=req.auto_build_repos,
                max_papers=req.max_papers,
            )

            result = {
                "papers_processed": len(docs),
                "paper_ids": [d.paper.paper_id for d in docs],
            }

            await task_manager.update_task(
                t_id,
                status=TaskStatus.COMPLETED,
                progress=100,
                step="completed",
                status_message=f"Completed processing {len(docs)} paper(s)",
                result=result,
            )
        except Exception as e:
            logger.error(f"Paper pipeline failed: {e}")
            await task_manager.update_task(
                t_id,
                status=TaskStatus.FAILED,
                error=str(e),
                status_message=f"Pipeline failed: {e}",
            )

    queue_position = await task_manager.run_task(task_id, run_pipeline)
    if queue_position > 0:
        message = f"Paper reading pipeline queued (position {queue_position})"
    else:
        message = "Paper reading pipeline started"

    return PaperReadResponse(task_id=task_id, message=message)


@router.get("/status/{task_id}")
async def get_paper_status(task_id: str):
    """Get pipeline task status."""
    task_manager = get_task_manager()
    state = await task_manager.get_task_status(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "task_id": state.task_id,
        "status": state.status.value,
        "progress": state.progress,
        "step": state.step,
        "status_message": state.status_message,
        "error": state.error,
        "result": state.result,
    }


@router.get("/list")
async def list_papers():
    """List all processed papers."""
    downloader = PaperDownloader()
    papers = downloader.list_papers()
    return {"papers": papers, "total": len(papers)}


# =========================================================================
# HuggingFace Daily Papers endpoints
# =========================================================================


@router.post("/crawl")
async def crawl_daily_papers(
    date: str | None = Query(default=None, description="Date in YYYY-MM-DD format (default: today)"),
    force: bool = Query(default=False, description="Force re-crawl even if cache exists"),
):
    """Crawl HF daily papers for a date (default: today). Idempotent unless force=True."""
    from datetime import date as date_type

    date_str = date or date_type.today().isoformat()
    try:
        crawler = HFDailyPapersCrawler()
        result = await crawler.crawl_and_save(date_str, force=force)
        return result
    except Exception as e:
        logger.error(f"Crawl failed for {date_str}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/crawl/range")
async def crawl_date_range(
    start_date: str = Query(..., description="Start date YYYY-MM-DD"),
    end_date: str = Query(..., description="End date YYYY-MM-DD"),
):
    """Crawl HF daily papers for a date range. Runs in background, returns task_id."""
    task_manager = get_task_manager()
    task_id = await task_manager.create_task(
        task_type=TaskType.PAPER_READ.value,
        repo_name=f"crawl_{start_date}_{end_date}",
        initial_message=f"Crawling HF papers from {start_date} to {end_date}...",
    )

    async def run_crawl(t_id: str) -> None:
        try:
            await task_manager.update_task(t_id, status=TaskStatus.RUNNING, progress=0, status_message="Starting crawl...")
            crawler = HFDailyPapersCrawler()
            result = await crawler.crawl_date_range(start_date, end_date)
            await task_manager.update_task(
                t_id,
                status=TaskStatus.COMPLETED,
                progress=100,
                status_message=f"Crawled {result['total_papers']} papers across {result['days_crawled']} days",
                result=result,
            )
        except Exception as e:
            logger.error(f"Range crawl failed: {e}")
            await task_manager.update_task(t_id, status=TaskStatus.FAILED, error=str(e))

    background_task = asyncio.create_task(run_crawl(task_id))
    task_manager.register_task(task_id, background_task)
    return {"task_id": task_id, "message": f"Crawling {start_date} to {end_date}"}


@router.get("/daily")
async def get_daily_papers(
    date: str | None = Query(default=None, description="Date in YYYY-MM-DD format (default: today)"),
):
    """Get cached daily papers for a date (from local index)."""
    from datetime import date as date_type

    date_str = date or date_type.today().isoformat()
    index = get_daily_index(date_str)
    if not index:
        raise HTTPException(status_code=404, detail=f"No papers cached for {date_str}. Use POST /crawl to fetch.")
    return index.model_dump()


@router.get("/daily/range")
async def get_papers_in_date_range(
    start_date: str = Query(..., description="Start date YYYY-MM-DD"),
    end_date: str = Query(..., description="End date YYYY-MM-DD"),
    min_upvotes: int = Query(default=0, ge=0, description="Minimum upvotes filter"),
    auto_crawl: bool = Query(default=True, description="Auto-crawl missing dates from HF API"),
):
    """Get papers across a date range with optional filtering.

    If auto_crawl is True (default), any dates not yet cached will be crawled
    from the HuggingFace API before returning results.
    """
    if auto_crawl:
        # Check which dates are missing and crawl them
        from datetime import date as date_type, timedelta as td

        s = date_type.fromisoformat(start_date)
        e = min(date_type.fromisoformat(end_date), date_type.today())
        daily_dir = _get_daily_dir()
        missing = []
        cur = s
        while cur <= e:
            if not (daily_dir / f"{cur.isoformat()}.json").exists():
                missing.append(cur.isoformat())
            cur += td(days=1)

        if missing:
            logger.info(f"Auto-crawling {len(missing)} missing dates in range {start_date}..{end_date}")
            crawler = HFDailyPapersCrawler()

            async def _crawl_one(d: str) -> None:
                try:
                    await crawler.crawl_and_save(d)
                except Exception as exc:
                    logger.warning(f"Auto-crawl failed for {d}: {exc}")

            # Crawl in batches of 5 to avoid overwhelming HF API
            for i in range(0, len(missing), 5):
                batch = missing[i : i + 5]
                await asyncio.gather(*[_crawl_one(d) for d in batch])

    papers = get_papers_in_range(start_date, end_date, min_upvotes)
    return {
        "start_date": start_date,
        "end_date": end_date,
        "min_upvotes": min_upvotes,
        "total": len(papers),
        "papers": [p.model_dump() for p in papers],
    }


@router.get("/daily/search")
async def search_local_papers(
    q: str = Query(..., min_length=1, description="Search query (supports | for OR)"),
    max_results: int = Query(default=50, ge=1, le=200),
):
    """Search across all crawled daily papers and library by keyword.

    Returns papers matching the query from all cached dates (newest first)
    and the processed library, with deduplication.
    """
    terms = [t.strip().lower() for t in q.split("|") if t.strip()]
    if not terms:
        return {"papers": [], "total": 0, "query": q}

    def _matches(paper_dict: dict) -> bool:
        title = (paper_dict.get("title") or "").lower()
        summary = (paper_dict.get("summary") or paper_dict.get("abstract") or "").lower()
        keywords = " ".join(paper_dict.get("ai_keywords") or []).lower()
        pid = (paper_dict.get("paper_id") or "").lower()
        haystack = f"{title} {summary} {keywords} {pid}"
        return any(t in haystack for t in terms)

    matched: list[dict] = []
    seen_ids: set[str] = set()

    # Library paper IDs for is_processed tagging
    downloader = PaperDownloader()
    library_papers = downloader.list_papers()
    library_ids = {p.get("paper_id", "") for p in library_papers if p.get("paper_id")}

    # 1. Search daily papers (all dates, newest first)
    dates = get_available_dates()
    for date_str in dates:
        if len(matched) >= max_results:
            break
        index = get_daily_index(date_str)
        if not index:
            continue
        for p in index.papers:
            pid = p.paper_id
            if pid in seen_ids:
                continue
            pd = p.model_dump()
            if _matches(pd):
                pd["source_type"] = "daily"
                pd["date"] = date_str
                if pid in library_ids:
                    pd["is_processed"] = True
                matched.append(pd)
                seen_ids.add(pid)
                if len(matched) >= max_results:
                    break

    # 2. Search library (dedup)
    if len(matched) < max_results:
        for p in library_papers:
            pid = p.get("paper_id", "")
            if pid in seen_ids:
                continue
            if _matches(p):
                p["source_type"] = "library"
                p["is_processed"] = True
                matched.append(p)
                seen_ids.add(pid)
                if len(matched) >= max_results:
                    break

    return {"papers": matched, "total": len(matched), "query": q}


@router.get("/daily/all")
async def get_all_papers_paginated(
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(default=100, ge=10, le=200, description="Papers per page"),
):
    """Get all crawled papers across all dates, paginated, newest first.

    Returns papers sorted by date (newest first), then by upvotes within each date.
    No auto-crawl — only returns already-cached data.
    """
    dates = get_available_dates()  # sorted desc (newest first)
    all_papers: list[dict] = []
    date_for_paper: dict[str, str] = {}  # paper_id -> date for dedup

    for date_str in dates:
        index = get_daily_index(date_str)
        if not index:
            continue
        for p in index.papers:
            if p.paper_id not in date_for_paper:
                pd = p.model_dump()
                pd["date"] = date_str
                all_papers.append(pd)
                date_for_paper[p.paper_id] = date_str

    total = len(all_papers)
    start = (page - 1) * page_size
    end = start + page_size
    page_papers = all_papers[start:end]

    return {
        "papers": page_papers,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


@router.get("/daily/dates")
async def get_crawled_dates():
    """List all dates that have been crawled."""
    dates = get_available_dates()
    return {"dates": dates, "total": len(dates)}


@router.get("/{paper_id}")
async def get_paper(paper_id: str):
    """Get paper metadata and status."""
    downloader = PaperDownloader()
    paper = downloader.get_paper(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    return paper


@router.get("/{paper_id}/doc")
async def get_paper_doc(
    paper_id: str,
    skeleton: bool = Query(default=False, description="Return lightweight skeleton instead of full document"),
    sections: str | None = Query(default=None, description="Comma-separated section indices or titles to return"),
):
    """Get the generated interactive reading document.

    Three modes:
    - No params: full document (backward compatible)
    - skeleton=true: lightweight outline with section previews
    - sections=0,2,Introduction: return specific sections' full content
    """
    import json

    from paper.downloader import get_paper_dir

    paper_dir = get_paper_dir(paper_id)
    doc_path = paper_dir / "doc" / "paper_reading.json"
    if not doc_path.exists():
        raise HTTPException(status_code=404, detail="Document not generated yet")

    doc = json.loads(doc_path.read_text())

    # Full doc mode (default, backward compatible)
    if not skeleton and sections is None:
        return doc

    doc_sections = doc.get("sections", [])

    # Sections mode: return specific sections by index or title
    # Also supports extracting subsections from large content blobs
    if sections is not None:
        requested = [s.strip() for s in sections.split(",") if s.strip()]
        result_sections = []
        for req in requested:
            matched = None
            # Try as integer index first
            try:
                idx = int(req)
                if 0 <= idx < len(doc_sections):
                    matched = (idx, doc_sections[idx])
            except ValueError:
                # Match by top-level section title (case-insensitive)
                for i, sec in enumerate(doc_sections):
                    if sec.get("title", "").lower() == req.lower():
                        matched = (i, sec)
                        break
                # If not found, search for a sub-heading inside large sections
                if not matched:
                    for i, sec in enumerate(doc_sections):
                        content = sec.get("content", "")
                        if len(content) < 2000:
                            continue
                        sub = _extract_subsection(content, req)
                        if sub:
                            result_sections.append({
                                "index": i,
                                "title": sub["title"],
                                "level": sub["level"],
                                "content": sub["content"],
                                "content_length": len(sub["content"]),
                                "code_refs": sec.get("code_refs", []),
                                "parent_section": sec.get("title", ""),
                            })
                            break
                    continue  # already handled

            if matched:
                idx, sec = matched
                result_sections.append({
                    "index": idx,
                    "title": sec.get("title", ""),
                    "level": sec.get("level", 1),
                    "content": sec.get("content", ""),
                    "content_length": len(sec.get("content", "")),
                    "code_refs": sec.get("code_refs", []),
                })
        return {"paper_id": paper_id, "sections": result_sections}

    # Skeleton mode
    # Preview lengths: short sections get full content, subsections get 500 chars
    _PREVIEW_LEN = 500

    paper_meta = doc.get("paper", {})
    section_outlines = []
    for i, sec in enumerate(doc_sections):
        content = sec.get("content", "")
        level = sec.get("level", 1)

        # Small sections (Overview, Code Analysis): include full content
        if len(content) <= 3000:
            entry = {
                "index": i,
                "title": sec.get("title", ""),
                "level": level,
                "content": content,
                "content_length": len(content),
            }
            section_outlines.append(entry)
            continue

        # Large sections: detect sub-headings to build a tree
        subsections = []
        if len(content) > 5000:
            headings = _detect_headings(content)
            lines = content.split("\n")
            for hi, (line_i, title, hlevel) in enumerate(headings):
                start = line_i + 1
                end = headings[hi + 1][0] if hi + 1 < len(headings) else len(lines)
                sub_content = "\n".join(lines[start:end]).strip()
                subsections.append({
                    "title": title,
                    "level": hlevel,
                    "content_preview": sub_content[:_PREVIEW_LEN] + ("..." if len(sub_content) > _PREVIEW_LEN else ""),
                    "content_length": len(sub_content),
                })

        entry = {
            "index": i,
            "title": sec.get("title", ""),
            "level": level,
            "content_preview": content[:_PREVIEW_LEN] + ("..." if len(content) > _PREVIEW_LEN else ""),
            "content_length": len(content),
        }
        if subsections:
            entry["subsections"] = subsections
        section_outlines.append(entry)

    figures = []
    for fig in doc.get("figures", []):
        figures.append({
            "figure_type": fig.get("figure_type", ""),
            "caption": fig.get("caption", ""),
        })

    code_analysis = doc.get("code_analysis")
    code_summary = None
    if code_analysis:
        project = code_analysis.get("project_name", "")
        components = code_analysis.get("key_components", [])
        code_summary = f"project_name={project}, {len(components)} key components"

    references = doc.get("references", [])

    return {
        "paper": {
            k: paper_meta[k]
            for k in ("title", "authors", "abstract", "paper_id", "arxiv_id", "source", "url")
            if k in paper_meta
        },
        "sections": section_outlines,
        "figures": figures,
        "code_analysis_summary": code_summary,
        "references_count": len(references),
    }


@router.get("/{paper_id}/pdf")
async def get_paper_pdf(paper_id: str):
    """Serve the raw PDF file for embedding."""
    from paper.downloader import get_paper_dir

    paper_dir = get_paper_dir(paper_id)
    pdf_path = paper_dir / "raw.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found")

    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        content_disposition_type="inline",
    )


@router.delete("/{paper_id}")
async def delete_paper(paper_id: str):
    """Delete a paper and all its artifacts."""
    downloader = PaperDownloader()
    if downloader.delete_paper(paper_id):
        return {"success": True, "message": f"Paper {paper_id} deleted"}
    raise HTTPException(status_code=404, detail="Paper not found")

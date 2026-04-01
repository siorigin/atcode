# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""HuggingFace Daily Papers crawler.

Fetches papers from the HF Daily Papers API and saves them as local daily indices.
Storage: data/wiki_papers/daily/{date}.json
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from loguru import logger

from paper.downloader import get_papers_dir
from paper.models import DailyIndex, HFPaperEntry


HF_API = "https://huggingface.co/api/daily_papers"


def _get_daily_dir() -> Path:
    """Get the daily index directory."""
    d = get_papers_dir() / "daily"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _parse_hf_paper(raw: dict) -> HFPaperEntry:
    """Parse a single paper entry from HF API response."""
    paper = raw.get("paper", {})
    github_repo = paper.get("githubRepo")

    return HFPaperEntry(
        paper_id=paper.get("id", ""),
        title=paper.get("title", ""),
        summary=paper.get("summary", ""),
        authors=[a.get("name", a.get("user", {}).get("user", "")) for a in paper.get("authors", [])],
        published_at=paper.get("publishedAt", ""),
        upvotes=paper.get("upvotes", 0),
        num_comments=raw.get("numComments", 0),  # numComments is on the top-level item
        ai_summary=paper.get("ai_summary"),
        ai_keywords=paper.get("ai_keywords", []),
        github_repo=github_repo if isinstance(github_repo, str) else (github_repo.get("url") if isinstance(github_repo, dict) else None),
        github_stars=paper.get("githubStars") if isinstance(github_repo, str) else (github_repo.get("stars") if isinstance(github_repo, dict) else None),
        organization=paper.get("organization", {}).get("name") if isinstance(paper.get("organization"), dict) else None,
        thumbnail_url=raw.get("thumbnail"),  # thumbnail is on the top-level item
        submitted_by=raw.get("submittedBy", {}).get("fullname") or raw.get("submittedBy", {}).get("user") if isinstance(raw.get("submittedBy"), dict) else None,
        submitted_at=raw.get("publishedAt", paper.get("submittedOnDailyAt", "")),
    )


class HFDailyPapersCrawler:
    """Crawl papers from HuggingFace Daily Papers API."""

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    async def fetch_date(self, date_str: str) -> list[HFPaperEntry]:
        """Fetch papers for a specific date (YYYY-MM-DD).

        Args:
            date_str: Date string in YYYY-MM-DD format.

        Returns:
            List of HFPaperEntry objects.
        """
        params = {"date": date_str}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(HF_API, params=params)
            resp.raise_for_status()
            data = resp.json()

        if not isinstance(data, list):
            logger.warning(f"Unexpected HF API response for {date_str}: {type(data)}")
            return []

        papers = []
        for item in data:
            try:
                papers.append(_parse_hf_paper(item))
            except Exception as e:
                logger.warning(f"Failed to parse HF paper entry: {e}")
        return papers

    async def fetch_date_range(self, start: str, end: str) -> list[HFPaperEntry]:
        """Fetch papers for a date range, one request per day.

        Args:
            start: Start date (YYYY-MM-DD), inclusive.
            end: End date (YYYY-MM-DD), inclusive.

        Returns:
            Deduplicated list of papers across all dates.
        """
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)

        seen_ids: set[str] = set()
        all_papers: list[HFPaperEntry] = []

        current = start_date
        while current <= end_date:
            date_str = current.isoformat()
            try:
                papers = await self.fetch_date(date_str)
                for p in papers:
                    if p.paper_id not in seen_ids:
                        seen_ids.add(p.paper_id)
                        all_papers.append(p)
            except Exception as e:
                logger.warning(f"Failed to fetch papers for {date_str}: {e}")
            current += timedelta(days=1)

        return all_papers

    async def crawl_and_save(self, date_str: str, force: bool = False) -> dict:
        """Fetch + save to local storage. Idempotent unless force=True.

        Args:
            date_str: Date string in YYYY-MM-DD format.
            force: If True, re-crawl even if cache exists.

        Returns:
            Dict with crawl result info.
        """
        daily_dir = _get_daily_dir()
        index_path = daily_dir / f"{date_str}.json"

        # Idempotent: if already crawled and not forced, return cached data
        if not force and index_path.exists():
            try:
                cached = json.loads(index_path.read_text())
                return {
                    "status": "cached",
                    "date": date_str,
                    "total": cached.get("total", 0),
                    "message": f"Already crawled {date_str}, {cached.get('total', 0)} papers cached.",
                }
            except Exception:
                pass  # Re-crawl if cache is corrupted

        papers = await self.fetch_date(date_str)

        index = DailyIndex(
            date=date_str,
            papers=papers,
            crawled_at=datetime.now(timezone.utc).isoformat(),
            total=len(papers),
        )

        index_path.write_text(
            json.dumps(index.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        logger.info(f"Crawled {len(papers)} papers for {date_str}")

        return {
            "status": "crawled",
            "date": date_str,
            "total": len(papers),
            "message": f"Crawled {len(papers)} papers for {date_str}.",
        }

    async def crawl_date_range(self, start: str, end: str) -> dict:
        """Crawl and save papers for a date range.

        Args:
            start: Start date (YYYY-MM-DD), inclusive.
            end: End date (YYYY-MM-DD), inclusive.

        Returns:
            Dict with summary info.
        """
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)

        results = []
        current = start_date
        while current <= end_date:
            date_str = current.isoformat()
            result = await self.crawl_and_save(date_str)
            results.append(result)
            current += timedelta(days=1)

        total_papers = sum(r.get("total", 0) for r in results)
        crawled = sum(1 for r in results if r.get("status") == "crawled")
        cached = sum(1 for r in results if r.get("status") == "cached")

        return {
            "status": "completed",
            "start_date": start,
            "end_date": end,
            "days_crawled": crawled,
            "days_cached": cached,
            "total_papers": total_papers,
            "details": results,
        }


async def crawl_recent_days(days: int = 7) -> dict:
    """Crawl the last N days of HF daily papers, skipping dates already cached.

    Intended to be called on server startup as a background task.

    Args:
        days: Number of past days to crawl (default: 7).

    Returns:
        Summary dict with counts.
    """
    crawler = HFDailyPapersCrawler()
    today = date.today()
    daily_dir = _get_daily_dir()

    crawled = 0
    skipped = 0
    failed = 0

    for i in range(days):
        d = today - timedelta(days=i)
        date_str = d.isoformat()
        index_path = daily_dir / f"{date_str}.json"

        if index_path.exists():
            skipped += 1
            continue

        try:
            await crawler.crawl_and_save(date_str)
            crawled += 1
        except Exception as e:
            logger.warning(f"Auto-crawl failed for {date_str}: {e}")
            failed += 1

    logger.info(
        f"Auto-crawl complete: {crawled} crawled, {skipped} cached, {failed} failed"
    )
    return {"crawled": crawled, "skipped": skipped, "failed": failed}


def get_daily_index(date_str: str) -> DailyIndex | None:
    """Read cached daily index for a date. Returns None if not crawled."""
    index_path = _get_daily_dir() / f"{date_str}.json"
    if not index_path.exists():
        return None
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        return DailyIndex(**data)
    except Exception as e:
        logger.warning(f"Failed to read daily index for {date_str}: {e}")
        return None


def get_papers_in_range(
    start: str, end: str, min_upvotes: int = 0
) -> list[HFPaperEntry]:
    """Get papers across a date range from cached daily indices.

    Args:
        start: Start date (YYYY-MM-DD), inclusive.
        end: End date (YYYY-MM-DD), inclusive.
        min_upvotes: Minimum upvotes filter.

    Returns:
        Deduplicated list of papers sorted by upvotes (descending).
    """
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)

    seen_ids: set[str] = set()
    all_papers: list[HFPaperEntry] = []

    current = start_date
    while current <= end_date:
        index = get_daily_index(current.isoformat())
        if index:
            for p in index.papers:
                if p.paper_id not in seen_ids and p.upvotes >= min_upvotes:
                    seen_ids.add(p.paper_id)
                    all_papers.append(p)
        current += timedelta(days=1)

    all_papers.sort(key=lambda p: p.upvotes, reverse=True)
    return all_papers


def get_available_dates() -> list[str]:
    """List all dates that have been crawled, sorted descending."""
    daily_dir = _get_daily_dir()
    if not daily_dir.exists():
        return []
    dates = [
        f.stem for f in daily_dir.glob("*.json")
        if f.stem and len(f.stem) == 10  # YYYY-MM-DD format
    ]
    dates.sort(reverse=True)
    return dates

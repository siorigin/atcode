# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Unified paper search across arXiv, Semantic Scholar, and Papers With Code."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

import httpx
from loguru import logger

from paper.models import PaperMetadata


class PaperSearchService:
    """Search for academic papers across multiple sources."""

    def __init__(self, timeout: float = 30.0, s2_api_key: str | None = None):
        self.timeout = timeout
        self.s2_api_key = s2_api_key

    async def search(
        self,
        query: str,
        sources: list[str] | None = None,
        max_results: int = 10,
    ) -> list[PaperMetadata]:
        """Search papers across specified sources, deduplicate and rank."""
        if sources is None:
            sources = ["arxiv", "semantic_scholar"]

        all_papers: list[PaperMetadata] = []
        source_map = {
            "arxiv": self._search_arxiv,
            "semantic_scholar": self._search_semantic_scholar,
            "papers_with_code": self._search_papers_with_code,
        }

        for source in sources:
            fn = source_map.get(source)
            if fn is None:
                logger.warning(f"Unknown search source: {source}")
                continue
            try:
                papers = await fn(query, max_results)
                all_papers.extend(papers)
            except Exception as e:
                logger.error(f"Error searching {source}: {e}")

        # Deduplicate by title similarity
        seen_titles: set[str] = set()
        unique: list[PaperMetadata] = []
        for p in all_papers:
            key = _normalize_title(p.title)
            if key not in seen_titles:
                seen_titles.add(key)
                unique.append(p)

        # Rank by citations descending, then by source priority
        unique.sort(key=lambda p: (-p.citations, p.source != "arxiv"))
        return unique[:max_results]

    async def _search_arxiv(self, query: str, max_results: int) -> list[PaperMetadata]:
        url = "http://export.arxiv.org/api/query"
        params = {
            "search_query": f"all:{query}",
            "sortBy": "relevance",
            "sortOrder": "descending",
            "start": 0,
            "max_results": max_results,
        }

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()

        ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
        root = ET.fromstring(resp.text)
        papers: list[PaperMetadata] = []

        for entry in root.findall("atom:entry", ns):
            title = (entry.findtext("atom:title", "", ns) or "").strip().replace("\n", " ")
            abstract = (entry.findtext("atom:summary", "", ns) or "").strip().replace("\n", " ")
            authors = [a.findtext("atom:name", "", ns) for a in entry.findall("atom:author", ns)]
            published = entry.findtext("atom:published", "", ns)[:10] if entry.findtext("atom:published", "", ns) else None

            # Extract arxiv ID from entry id URL
            entry_id = entry.findtext("atom:id", "", ns) or ""
            arxiv_id = entry_id.split("/abs/")[-1] if "/abs/" in entry_id else entry_id

            # Find PDF link
            pdf_url = None
            for link in entry.findall("atom:link", ns):
                if link.get("title") == "pdf":
                    pdf_url = link.get("href")
                    break

            papers.append(
                PaperMetadata(
                    paper_id=arxiv_id,
                    title=title,
                    authors=authors,
                    abstract=abstract,
                    source="arxiv",
                    url=f"https://arxiv.org/abs/{arxiv_id}",
                    pdf_url=pdf_url or f"https://arxiv.org/pdf/{arxiv_id}",
                    published_date=published,
                )
            )

        return papers

    async def _search_semantic_scholar(self, query: str, max_results: int) -> list[PaperMetadata]:
        url = "https://api.semanticscholar.org/graph/v1/paper/search"
        params = {
            "query": query,
            "limit": min(max_results, 100),
            "fields": "title,authors,abstract,url,externalIds,citationCount,year,openAccessPdf",
        }

        headers = {}
        if self.s2_api_key:
            headers["x-api-key"] = self.s2_api_key

        import asyncio

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            for attempt in range(3):
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code == 429:
                    wait = 2 ** attempt
                    logger.warning(f"Semantic Scholar rate limited, retrying in {wait}s (attempt {attempt + 1}/3)")
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                break
            else:
                raise httpx.HTTPStatusError("Rate limited after 3 retries", request=resp.request, response=resp)

        data = resp.json()
        papers: list[PaperMetadata] = []

        for item in data.get("data", []):
            ext_ids = item.get("externalIds") or {}
            arxiv_id = ext_ids.get("ArXiv", "")
            paper_id = arxiv_id or ext_ids.get("DOI", "") or item.get("paperId", "")

            pdf_url = None
            if oap := item.get("openAccessPdf"):
                pdf_url = oap.get("url")
            elif arxiv_id:
                pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

            authors = [a.get("name", "") for a in (item.get("authors") or [])]

            papers.append(
                PaperMetadata(
                    paper_id=paper_id,
                    title=item.get("title", ""),
                    authors=authors,
                    abstract=item.get("abstract") or "",
                    source="semantic_scholar",
                    url=item.get("url", ""),
                    pdf_url=pdf_url,
                    published_date=str(item.get("year", "")),
                    citations=item.get("citationCount") or 0,
                )
            )

        return papers

    async def _search_papers_with_code(self, query: str, max_results: int) -> list[PaperMetadata]:
        url = f"https://paperswithcode.com/api/v1/search/?q={quote_plus(query)}"

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if "application/json" not in content_type:
            logger.warning(f"Papers with Code returned non-JSON response ({content_type}), skipping")
            return []

        data = resp.json()
        papers: list[PaperMetadata] = []

        for item in data.get("results", [])[:max_results]:
            paper = item.get("paper", {}) or {}
            paper_id = paper.get("id", "") or str(item.get("id", ""))

            # Extract arxiv ID from URL if available
            paper_url = paper.get("url_abs", "") or ""
            if "arxiv.org/abs/" in paper_url:
                paper_id = paper_url.split("/abs/")[-1]

            repos = item.get("repositories", []) or []
            github_urls = [r.get("url", "") for r in repos if r.get("url", "").startswith("https://github.com")]

            papers.append(
                PaperMetadata(
                    paper_id=paper_id,
                    title=paper.get("title", item.get("title", "")),
                    authors=[],  # PWC search doesn't return authors
                    abstract=paper.get("abstract", ""),
                    source="papers_with_code",
                    url=paper_url,
                    pdf_url=paper.get("url_pdf"),
                    github_urls=github_urls,
                )
            )

        return papers


def _normalize_title(title: str) -> str:
    """Normalize title for deduplication."""
    return re.sub(r"[^a-z0-9]", "", title.lower())

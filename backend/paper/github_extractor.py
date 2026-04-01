# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Extract GitHub repository URLs from papers using multiple strategies."""

from __future__ import annotations

import re
from urllib.parse import quote_plus

import httpx
from loguru import logger

from paper.models import PaperMetadata


class RepoInfo:
    """Info about a discovered GitHub repository."""

    def __init__(self, url: str, source: str = "regex", stars: int = 0, description: str = ""):
        self.url = url
        self.source = source  # "regex" | "papers_with_code" | "semantic_scholar"
        self.stars = stars
        self.description = description

    def __repr__(self) -> str:
        return f"RepoInfo(url={self.url!r}, source={self.source!r})"


# GitHub reserved system paths that are never valid repo owners
_GITHUB_NON_REPO_PREFIXES = {
    "orgs", "features", "apps", "marketplace", "settings", "sponsors",
    "topics", "collections", "trending", "explore", "notifications",
    "login", "signup", "enterprise", "pricing", "about",
}


class GitHubExtractor:
    """Extract GitHub repository URLs from papers using multiple strategies.

    Four-layer strategy:
    1. Regex extraction from parsed text
    2. Papers With Code API query
    3. Semantic Scholar API for external IDs
    4. Project page crawling (fetch *.github.io and other project pages, extract GitHub links)
    """

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    async def extract(
        self,
        paper: PaperMetadata,
        parsed_text: str = "",
    ) -> list[RepoInfo]:
        """Extract GitHub repos using all strategies, deduplicated."""
        all_repos: list[RepoInfo] = []

        # Strategy 1: Regex from paper text + existing metadata
        all_repos.extend(self._regex_extract(parsed_text))
        for url in paper.github_urls:
            all_repos.append(RepoInfo(url=url, source="metadata"))

        # Strategy 2: Papers With Code API
        try:
            pwc_repos = await self._query_papers_with_code(paper.title, paper.paper_id)
            all_repos.extend(pwc_repos)
        except Exception as e:
            logger.debug(f"Papers With Code query failed: {e}")

        # Strategy 3: Semantic Scholar
        try:
            ss_repos = await self._query_semantic_scholar(paper.paper_id)
            all_repos.extend(ss_repos)
        except Exception as e:
            logger.debug(f"Semantic Scholar query failed: {e}")

        # Strategy 4: Crawl project pages found in text (*.github.io, etc.)
        try:
            project_page_urls = self._extract_project_page_urls(parsed_text)
            if project_page_urls:
                page_repos = await self._extract_from_project_pages(project_page_urls)
                all_repos.extend(page_repos)
        except Exception as e:
            logger.debug(f"Project page crawling failed: {e}")

        # Deduplicate by normalized URL
        seen: set[str] = set()
        unique: list[RepoInfo] = []
        for repo in all_repos:
            norm_url = self._normalize_url(repo.url)
            if norm_url and norm_url not in seen:
                seen.add(norm_url)
                unique.append(repo)

        # Validate repos exist
        validated = await self._validate_repos(unique)

        logger.info(f"Found {len(validated)} valid GitHub repos for paper '{paper.title[:60]}'")
        return validated

    def _regex_extract(self, text: str) -> list[RepoInfo]:
        """Extract GitHub URLs from text using regex."""
        pattern = re.compile(r"https?://github\.com/([\w\-\.]+)/([\w\-\.]+)")
        repos = []
        seen = set()
        for match in pattern.finditer(text):
            owner = match.group(1)
            if owner.lower() in _GITHUB_NON_REPO_PREFIXES:
                continue
            url = f"https://github.com/{owner}/{match.group(2)}"
            url = url.rstrip("/.")
            if url not in seen:
                seen.add(url)
                repos.append(RepoInfo(url=url, source="regex"))
        return repos

    async def _query_papers_with_code(self, title: str, paper_id: str) -> list[RepoInfo]:
        """Query Papers With Code API for associated repositories."""
        repos: list[RepoInfo] = []

        # Try by arxiv ID first
        if re.match(r"\d{4}\.\d{4,5}", paper_id):
            url = f"https://paperswithcode.com/api/v1/papers/?arxiv_id={paper_id}"
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("results", []) if isinstance(data, dict) else data if isinstance(data, list) else []
                    for paper_data in results:
                        paper_pwc_id = paper_data.get("id")
                        if paper_pwc_id:
                            repos.extend(await self._get_pwc_repos(client, paper_pwc_id))

        # Fallback: search by title (skip if title looks like an arxiv ID or is empty)
        if not repos and title and not re.match(r"^\d{4}\.\d{4,5}(?:v\d+)?$", title.strip()):
            search_url = f"https://paperswithcode.com/api/v1/search/?q={quote_plus(title)}"
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(search_url)
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get("results", [])[:3]:
                        for r in item.get("repositories", []) or []:
                            if r.get("url", "").startswith("https://github.com"):
                                repos.append(
                                    RepoInfo(
                                        url=r["url"],
                                        source="papers_with_code",
                                        stars=r.get("stars", 0),
                                    )
                                )

        return repos

    async def _get_pwc_repos(self, client: httpx.AsyncClient, paper_id: str) -> list[RepoInfo]:
        """Get repositories for a specific PWC paper ID."""
        url = f"https://paperswithcode.com/api/v1/papers/{paper_id}/repositories/"
        resp = await client.get(url)
        if resp.status_code != 200:
            return []

        repos = []
        for r in resp.json().get("results", []):
            if r.get("url", "").startswith("https://github.com"):
                repos.append(
                    RepoInfo(
                        url=r["url"],
                        source="papers_with_code",
                        stars=r.get("stars", 0),
                        description=r.get("description", ""),
                    )
                )
        return repos

    async def _query_semantic_scholar(self, paper_id: str) -> list[RepoInfo]:
        """Query Semantic Scholar for code links."""
        if not re.match(r"\d{4}\.\d{4,5}", paper_id):
            return []

        url = f"https://api.semanticscholar.org/graph/v1/paper/ARXIV:{paper_id}"
        params = {"fields": "externalIds,url"}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                return []

        data = resp.json()
        # Semantic Scholar doesn't directly link to GitHub,
        # but externalIds may contain useful cross-references
        return []

    def _extract_project_page_urls(self, text: str) -> list[str]:
        """Extract project page URLs from paper text (*.github.io, common project sites)."""
        urls: list[str] = []
        seen: set[str] = set()

        # Match *.github.io URLs (most common for ML papers)
        github_io_pattern = re.compile(
            r"https?://[\w\-]+\.github\.io(?:/[\w\-\./?&#=%~]*)?",
            re.IGNORECASE,
        )
        for match in github_io_pattern.finditer(text):
            url = match.group(0).rstrip("/.")
            if url not in seen:
                seen.add(url)
                urls.append(url)

        # Match common project page domains often used in papers
        project_domains = re.compile(
            r"https?://(?:[\w\-]+\.)*(?:project-page|project|demo)s?\.[\w]+(?:/[\w\-\./?&#=%~]*)?",
            re.IGNORECASE,
        )
        for match in project_domains.finditer(text):
            url = match.group(0).rstrip("/.")
            if url not in seen:
                seen.add(url)
                urls.append(url)

        return urls

    async def _extract_from_project_pages(self, page_urls: list[str]) -> list[RepoInfo]:
        """Fetch project pages and extract GitHub repo URLs from their HTML."""
        repos: list[RepoInfo] = []
        github_pattern = re.compile(r"https?://github\.com/([\w\-\.]+)/([\w\-\.]+)")

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            for page_url in page_urls[:5]:  # Limit to 5 pages to avoid excessive requests
                try:
                    resp = await client.get(page_url)
                    if resp.status_code != 200:
                        continue

                    html = resp.text
                    seen: set[str] = set()
                    for match in github_pattern.finditer(html):
                        owner = match.group(1)
                        if owner.lower() in _GITHUB_NON_REPO_PREFIXES:
                            continue
                        url = f"https://github.com/{owner}/{match.group(2)}"
                        url = url.rstrip("/.")
                        if url not in seen:
                            seen.add(url)
                            repos.append(RepoInfo(url=url, source="project_page"))
                    if repos:
                        logger.info(f"Found {len(repos)} GitHub URLs from project page {page_url}")
                except Exception as e:
                    logger.debug(f"Failed to fetch project page {page_url}: {e}")

        return repos

    async def _validate_repos(self, repos: list[RepoInfo]) -> list[RepoInfo]:
        """Validate that GitHub repos exist by checking API. If API fails, assume valid."""
        valid = []
        async with httpx.AsyncClient(timeout=10.0) as client:
            for repo in repos:
                match = re.match(r"https://github\.com/([\w\-\.]+)/([\w\-\.]+)", repo.url)
                if not match:
                    continue
                try:
                    api_url = f"https://api.github.com/repos/{match.group(1)}/{match.group(2)}"
                    resp = await client.get(api_url)
                    if resp.status_code == 200:
                        data = resp.json()
                        repo.stars = data.get("stargazers_count", 0)
                        repo.description = data.get("description", "")
                    elif resp.status_code == 404:
                        # Repo doesn't exist, skip
                        continue
                    # For 403 (rate limited) or other errors, assume valid
                except Exception:
                    pass  # Network error, assume valid
                valid.append(repo)
        return valid

    def _normalize_url(self, url: str) -> str:
        """Normalize GitHub URL for deduplication."""
        url = url.rstrip("/.")
        match = re.match(r"https?://github\.com/([\w\-\.]+)/([\w\-\.]+)", url)
        if match:
            if match.group(1).lower() in _GITHUB_NON_REPO_PREFIXES:
                return ""
            return f"https://github.com/{match.group(1)}/{match.group(2)}".lower()
        return ""

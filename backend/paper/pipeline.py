# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Paper reading pipeline: search → download → parse → extract → build graph → generate doc."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from paper.doc_generator import PaperDocGenerator
from paper.downloader import PaperDownloader, get_paper_dir
from paper.github_extractor import GitHubExtractor
from paper.models import CodeAnalysis, PaperMetadata, PaperParseResult, PaperReadingDoc
from paper.pdf_parser import MinerUPDFParser
from paper.search import PaperSearchService


class PaperReadingPipeline:
    """Orchestrate the complete paper reading pipeline."""

    def __init__(
        self,
        api_base_url: str = "http://127.0.0.1:8008",
        on_progress: Callable[[float, str], Any] | None = None,
    ):
        self.api_base_url = api_base_url
        from core.config import settings
        self.search_service = PaperSearchService(s2_api_key=settings.S2_API_KEY)
        self.downloader = PaperDownloader()
        self.pdf_parser = MinerUPDFParser(use_mineru=True)
        self.github_extractor = GitHubExtractor()
        self.doc_generator = PaperDocGenerator()
        self._on_progress = on_progress

    async def run(
        self,
        query: str | None = None,
        paper_url: str | None = None,
        arxiv_id: str | None = None,
        auto_build_repos: bool = True,
        max_papers: int = 1,
    ) -> list[PaperReadingDoc]:
        """Run the complete pipeline.

        Args:
            query: Search query string.
            paper_url: Direct URL to a paper (arxiv, semantic scholar, etc.).
            arxiv_id: Direct arXiv ID.
            auto_build_repos: Whether to build code graph for discovered repos.
            max_papers: Maximum number of papers to process.

        Returns:
            List of generated PaperReadingDoc objects.
        """
        results: list[PaperReadingDoc] = []

        # Step 1: Search / resolve papers
        await self._report_progress(0.0, "Searching for papers...")
        papers = await self._resolve_papers(query, paper_url, arxiv_id, max_papers)

        if not papers:
            await self._report_progress(1.0, "No papers found")
            return results

        for i, paper in enumerate(papers):
            try:
                doc = await self._process_single_paper(
                    paper,
                    auto_build_repos=auto_build_repos,
                    paper_index=i,
                    total_papers=len(papers),
                )
                results.append(doc)
            except Exception as e:
                logger.error(f"Failed to process paper '{paper.title}': {e}")
                self._update_paper_status(paper.paper_id, "failed", error=str(e))

        await self._report_progress(1.0, f"Completed processing {len(results)} paper(s)")
        return results

    async def _resolve_papers(
        self,
        query: str | None,
        paper_url: str | None,
        arxiv_id: str | None,
        max_papers: int,
    ) -> list[PaperMetadata]:
        """Resolve input into a list of PaperMetadata."""
        if arxiv_id:
            # Strip version suffix (e.g., "2511.20785v2" → "2511.20785")
            arxiv_id = re.sub(r"v\d+$", "", arxiv_id)
            # Direct arXiv ID — construct metadata directly, no search API needed
            # Try to get richer metadata from HF daily index first
            hf_meta = self._get_hf_metadata(arxiv_id)
            if hf_meta:
                return [hf_meta]
            # Fallback: construct minimal metadata with known arXiv PDF URL
            return [
                PaperMetadata(
                    paper_id=arxiv_id,
                    title=arxiv_id,
                    source="arxiv",
                    url=f"https://arxiv.org/abs/{arxiv_id}",
                    pdf_url=f"https://arxiv.org/pdf/{arxiv_id}.pdf",
                )
            ]

        if paper_url:
            # Try to extract arXiv ID from URL
            arxiv_match = re.search(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})(?:v\d+)?", paper_url)
            if arxiv_match:
                return await self._resolve_papers(None, None, arxiv_match.group(1), 1)

            # For other URLs, search by URL content
            return [
                PaperMetadata(
                    paper_id=paper_url.split("/")[-1],
                    title="",
                    source="url",
                    url=paper_url,
                    pdf_url=paper_url if paper_url.endswith(".pdf") else None,
                )
            ]

        if query:
            return await self.search_service.search(query, max_results=max_papers)

        return []

    def _get_hf_metadata(self, arxiv_id: str) -> PaperMetadata | None:
        """Try to find paper metadata from cached HF daily indices."""
        try:
            from paper.hf_crawler import get_available_dates, get_daily_index

            for date_str in get_available_dates():
                index = get_daily_index(date_str)
                if not index:
                    continue
                for p in index.papers:
                    if p.paper_id == arxiv_id:
                        return PaperMetadata(
                            paper_id=arxiv_id,
                            title=p.title,
                            authors=p.authors,
                            abstract=p.summary,
                            source="huggingface",
                            url=f"https://arxiv.org/abs/{arxiv_id}",
                            pdf_url=f"https://arxiv.org/pdf/{arxiv_id}.pdf",
                            github_urls=[p.github_repo] if p.github_repo else [],
                        )
        except Exception as e:
            logger.warning(f"Failed to get HF metadata for {arxiv_id}: {e}")
        return None

    async def _process_single_paper(
        self,
        paper: PaperMetadata,
        auto_build_repos: bool,
        paper_index: int,
        total_papers: int,
    ) -> PaperReadingDoc:
        """Process a single paper through the full pipeline."""
        base_progress = paper_index / total_papers
        scale = 1.0 / total_papers

        async def progress(pct: float, msg: str) -> None:
            await self._report_progress(base_progress + pct * scale, msg)

        # Step 2: Download PDF
        await progress(0.10, f"Downloading paper: {paper.title[:60]}...")
        self._update_paper_status(paper.paper_id, "downloading", progress=0.1)

        downloaded_path = await self.downloader.download(paper)

        # Step 3: Parse PDF
        await progress(0.25, "Parsing paper content with MinerU...")
        self._update_paper_status(paper.paper_id, "parsing", progress=0.25)

        paper_dir = get_paper_dir(paper.paper_id)
        parsed_dir = paper_dir / "parsed"
        parse_result = await self.pdf_parser.parse(downloaded_path, parsed_dir, paper.paper_id)

        # Step 4: Extract GitHub repos
        await progress(0.50, "Extracting GitHub repositories...")
        self._update_paper_status(paper.paper_id, "extracting", progress=0.5)

        repos = await self.github_extractor.extract(paper, parse_result.markdown_content)
        github_urls = [r.url for r in repos]

        # Update paper metadata with discovered repos
        paper.github_urls = list(set(paper.github_urls + github_urls))
        # Re-save metadata
        meta_path = paper_dir / "metadata.json"
        meta_path.write_text(paper.model_dump_json(indent=2))

        # Update parse result with discovered URLs
        parse_result.github_urls = list(set(parse_result.github_urls + github_urls))

        # Step 5: Build code graph (if repos found and auto_build enabled)
        code_analysis = None
        if auto_build_repos and github_urls:
            await progress(0.60, "Building code knowledge graph...")
            self._update_paper_status(paper.paper_id, "building", progress=0.6)
            code_analysis = await self._build_and_analyze_repo(github_urls[0], progress)

        # Step 6: Generate interactive document
        await progress(0.90, "Generating interactive document...")
        self._update_paper_status(paper.paper_id, "generating", progress=0.9)

        doc = await self.doc_generator.generate(paper, parse_result, code_analysis)

        # Step 7: Save document
        self.doc_generator.save_doc(doc, paper.paper_id)
        self._update_paper_status(paper.paper_id, "completed", progress=1.0)

        await progress(1.0, f"Completed: {paper.title[:60]}")
        return doc

    async def _build_and_analyze_repo(
        self,
        repo_url: str,
        progress_fn: Callable[[float, str], Any],
    ) -> CodeAnalysis | None:
        """Build code graph and analyze repository."""
        import httpx

        try:
            # Step 5a: Add repo via AtCode API
            await progress_fn(0.65, f"Adding repo: {repo_url}")
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(
                    f"{self.api_base_url}/api/repos/add",
                    json={"repo_url": repo_url, "skip_embeddings": True},
                )

                task_id = None
                project_name = ""

                if resp.status_code == 409:
                    # Repo directory exists — check if graph is built
                    repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
                    project_name = repo_name
                    logger.info(f"Repo '{repo_name}' already cloned, checking graph status...")

                    stats_resp = await client.get(
                        f"{self.api_base_url}/api/graph/{repo_name}/stats"
                    )
                    has_graph = (
                        stats_resp.status_code == 200
                        and stats_resp.json().get("node_count", 0) > 0
                    )

                    if not has_graph:
                        # Repo cloned but graph not built — trigger build via local path
                        from core.config import get_wiki_repos_dir
                        local_path = str(get_wiki_repos_dir() / repo_name)
                        logger.info(f"Graph missing for '{repo_name}', triggering build from {local_path}")
                        build_resp = await client.post(
                            f"{self.api_base_url}/api/repos/add-multiple-local",
                            json={
                                "local_path": local_path,
                                "project_name": repo_name,
                                "subdirs": [],
                                "skip_embeddings": True,
                            },
                            timeout=30.0,
                        )
                        if build_resp.status_code in (200, 201):
                            build_data = build_resp.json()
                            task_id = build_data.get("task_id") or build_data.get("job_id")
                        else:
                            logger.warning(f"Failed to trigger build for {repo_name}: {build_resp.status_code}")
                    else:
                        logger.info(f"Graph already exists for '{repo_name}', skipping build")

                elif resp.status_code in (200, 201):
                    data = resp.json()
                    task_id = data.get("task_id") or data.get("job_id")
                    project_name = data.get("project_name", "")
                else:
                    logger.warning(f"Failed to add repo {repo_url}: {resp.status_code}")
                    return None

                # Wait for repo clone + graph build
                if task_id:
                    await progress_fn(0.70, "Building knowledge graph (this may take a while)...")
                    await self._wait_for_task(client, task_id)

            # Step 5b: Analyze code using graph API
            await progress_fn(0.80, "Analyzing code structure...")
            analysis = await self._analyze_repo_code(project_name or repo_url.split("/")[-1])

            if analysis:
                analysis.repo_url = repo_url
                analysis.repo_name = repo_url.split("/")[-1]

            return analysis

        except Exception as e:
            logger.error(f"Failed to build/analyze repo {repo_url}: {e}")
            return None

    async def _wait_for_task(self, client: httpx.AsyncClient, task_id: str, timeout: float = 600.0) -> None:
        """Wait for a background task to complete."""
        import asyncio

        elapsed = 0.0
        interval = 5.0
        while elapsed < timeout:
            try:
                resp = await client.get(f"{self.api_base_url}/api/tasks/{task_id}")
                if resp.status_code == 200:
                    data = resp.json()
                    status = data.get("status", "")
                    progress = data.get("progress", 0)
                    msg = data.get("status_message", "")
                    logger.debug(f"Task {task_id}: status={status}, progress={progress}, msg={msg}")
                    if status in ("completed", "failed", "cancelled"):
                        if status == "failed":
                            logger.warning(f"Task {task_id} failed: {data.get('error', 'unknown')}")
                        return
                else:
                    logger.warning(f"Task status poll returned {resp.status_code} for {task_id}")
            except Exception as e:
                logger.warning(f"Failed to poll task {task_id}: {e}")
            await asyncio.sleep(interval)
            elapsed += interval

        logger.error(f"Task {task_id} timed out after {timeout}s")

    async def _analyze_repo_code(self, project_name: str) -> CodeAnalysis | None:
        """Analyze repository code using AtCode graph API."""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Get graph stats
                resp = await client.get(f"{self.api_base_url}/api/graph/{project_name}/stats")
                if resp.status_code != 200:
                    return None
                stats = resp.json()

                # Get top-level module structure
                modules: list[str] = []
                try:
                    children_resp = await client.get(
                        f"{self.api_base_url}/api/graph/node/{project_name}/children",
                        params={"qualified_name": project_name},
                    )
                    if children_resp.status_code == 200:
                        children_data = children_resp.json()
                        for child in children_data.get("children", []):
                            name = child.get("name", "")
                            if name and not name.startswith("_"):
                                modules.append(name)
                except Exception as e:
                    logger.debug(f"Failed to get children for {project_name}: {e}")

                # Find key code entities (classes and functions)
                key_components: list[dict[str, str]] = []
                for search_query in ["*", "main|engine|model|config|train|serve"]:
                    try:
                        find_resp = await client.post(
                            f"{self.api_base_url}/api/graph/node/{project_name}/find",
                            json={"query": search_query, "search_strategy": "auto", "node_type": "Code"},
                        )
                        if find_resp.status_code == 200:
                            find_data = find_resp.json()
                            for item in find_data.get("results", [])[:20]:
                                qn = item.get("qualified_name", "")
                                node_type = item.get("type", [])
                                if isinstance(node_type, list):
                                    node_type = [t for t in node_type if t not in ("Node",)]
                                # Only include classes and functions
                                if not any(t in ("Class", "Function", "Method") for t in node_type):
                                    continue
                                # Avoid duplicates
                                if any(c["qualified_name"] == qn for c in key_components):
                                    continue
                                role = node_type[0] if node_type else "Unknown"
                                key_components.append({
                                    "qualified_name": qn,
                                    "docstring": item.get("docstring") or "",
                                    "role": role,
                                })
                                if len(key_components) >= 15:
                                    break
                    except Exception as e:
                        logger.debug(f"find_nodes query '{search_query}' failed: {e}")
                    if len(key_components) >= 15:
                        break

                # Build structure overview
                overview_parts = [
                    f"Nodes: {stats.get('node_count', 0)}, Edges: {stats.get('edge_count', 0)}",
                ]
                if modules:
                    overview_parts.append(f"Top-level modules: {', '.join(modules[:15])}")

                return CodeAnalysis(
                    project_name=project_name,
                    structure_overview="\n".join(overview_parts),
                    key_components=key_components,
                )

        except Exception as e:
            logger.error(f"Failed to analyze repo {project_name}: {e}")
            return None

    async def _report_progress(self, progress: float, message: str) -> None:
        """Report pipeline progress (supports both sync and async callbacks)."""
        logger.info(f"Pipeline [{progress:.0%}] {message}")
        if self._on_progress:
            result = self._on_progress(progress, message)
            if inspect.isawaitable(result):
                await result

    def _update_paper_status(
        self,
        paper_id: str,
        status: str,
        progress: float = 0.0,
        error: str | None = None,
    ) -> None:
        """Update paper status file."""
        paper_dir = get_paper_dir(paper_id)
        status_path = paper_dir / "status.json"
        status_data = {
            "status": status,
            "progress": progress,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if error:
            status_data["error"] = error
        status_path.write_text(json.dumps(status_data))

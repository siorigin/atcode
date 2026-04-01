# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Paper reading tools — search, read, and manage academic papers via internal HTTP API.

Shared by both MCP and native (LangChain) tool layers.
"""

import asyncio
import json

import httpx
import requests
from langchain_core.tools import StructuredTool
from loguru import logger
from pydantic import BaseModel, Field

from .tool_registry import TOOL_DESCRIPTIONS


class PaperTools:
    """Paper reading tools via internal HTTP API calls.

    Provides both sync helpers (``_get`` / ``_post``, used by LangChain tools)
    and async helpers (``_aget`` / ``_apost``, used by MCP tools) so that the
    caller can choose the right variant for their runtime.
    """

    def __init__(self, api_port: int | None = None):
        from core.config import settings

        self.api_port = api_port or settings.API_PORT
        self.base_url = f"http://localhost:{self.api_port}"

    # ── Sync HTTP helpers (for LangChain / thread-pool callers) ──────────

    def _post(self, path: str, json_data: dict | None = None, timeout: int = 30) -> dict:
        try:
            resp = requests.post(f"{self.base_url}{path}", json=json_data, timeout=timeout)
            if resp.status_code in (200, 201):
                return resp.json()
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            return {"error": f"API error ({resp.status_code}): {detail}"}
        except requests.exceptions.ConnectionError:
            return {"error": "Cannot connect to AtCode API. Is the server running?"}
        except Exception as e:
            return {"error": str(e)}

    def _get(self, path: str, timeout: int = 10) -> dict:
        try:
            resp = requests.get(f"{self.base_url}{path}", timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"API error ({resp.status_code}): {resp.text}"}
        except requests.exceptions.ConnectionError:
            return {"error": "Cannot connect to AtCode API. Is the server running?"}
        except Exception as e:
            return {"error": str(e)}

    # ── Async HTTP helpers (for MCP tools running on the event loop) ─────

    async def _aget(self, path: str, timeout: int = 10) -> dict:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=timeout) as client:
                resp = await client.get(path)
                if resp.status_code == 200:
                    return resp.json()
                return {"error": f"API error ({resp.status_code}): {resp.text}"}
        except httpx.ConnectError:
            return {"error": "Cannot connect to AtCode API. Is the server running?"}
        except Exception as e:
            return {"error": str(e)}

    async def _apost(self, path: str, json_data: dict | None = None, timeout: int = 30) -> dict:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=timeout) as client:
                resp = await client.post(path, json=json_data)
                if resp.status_code in (200, 201):
                    return resp.json()
                try:
                    detail = resp.json().get("detail", resp.text)
                except Exception:
                    detail = resp.text
                return {"error": f"API error ({resp.status_code}): {detail}"}
        except httpx.ConnectError:
            return {"error": "Cannot connect to AtCode API. Is the server running?"}
        except Exception as e:
            return {"error": str(e)}

    def read_paper(
        self,
        query: str | None = None,
        paper_url: str | None = None,
        arxiv_id: str | None = None,
        auto_build_repos: bool = True,
        max_papers: int = 1,
    ) -> str:
        """Start the complete paper reading pipeline."""
        data = {
            "auto_build_repos": auto_build_repos,
            "max_papers": max_papers,
        }
        if query:
            data["query"] = query
        if paper_url:
            data["paper_url"] = paper_url
        if arxiv_id:
            data["arxiv_id"] = arxiv_id

        result = self._post("/api/papers/read", data, timeout=60)
        return json.dumps(result, default=str)

    def get_paper_doc(self, paper_id: str, sections: str | None = None) -> str:
        """Get paper document. Returns skeleton by default, or specific sections if requested."""
        if sections:
            result = self._get(f"/api/papers/{paper_id}/doc?sections={sections}", timeout=15)
        else:
            result = self._get(f"/api/papers/{paper_id}/doc?skeleton=true", timeout=15)
        return json.dumps(result, default=str)

    def search_papers(
        self,
        query: str,
        start_date: str | None = None,
        end_date: str | None = None,
        max_results: int = 20,
    ) -> str:
        """Search locally cached daily papers and processed library by keyword.

        Supports multiple queries separated by ``|`` — each term is matched
        independently (OR logic) so the caller can search for several topics
        in a single call.

        Results include ``is_processed: true`` for papers already in the
        library (have a reading doc).  Use ``get_paper_doc`` directly for
        those instead of calling ``read_paper`` again.

        Args:
            query: Keyword(s) to match against title, summary, ai_keywords,
                   paper_id.  Use ``|`` to separate multiple terms, e.g.
                   ``"flash attention|sparse mixture|MoE"``.
            start_date: Only include daily papers from this date onward (YYYY-MM-DD).
            end_date: Only include daily papers up to this date (YYYY-MM-DD).
            max_results: Max papers to return (default 20, max 50).
        """
        terms = [t.strip().lower() for t in query.split("|") if t.strip()]
        if not terms:
            return json.dumps({"papers": [], "total": 0, "query": query})

        capped = min(max(max_results, 1), 50)
        matched: list[dict] = []
        seen_ids: set[str] = set()

        # Pre-fetch library paper IDs to mark processed papers
        library_ids: set[str] = set()
        lib = self._get("/api/papers/list")
        if "error" not in lib:
            for p in lib.get("papers", []):
                pid = p.get("paper_id") or p.get("arxiv_id") or ""
                if pid:
                    library_ids.add(pid)

        def _matches(paper: dict) -> bool:
            title = (paper.get("title") or "").lower()
            summary = (paper.get("summary") or paper.get("abstract") or "").lower()
            keywords = " ".join(paper.get("ai_keywords") or []).lower()
            pid = (paper.get("paper_id") or "").lower()
            haystack = f"{title} {summary} {keywords} {pid}"
            return any(t in haystack for t in terms)

        # 1. Search daily papers (all crawled dates, newest first)
        daily_dates = self._get("/api/papers/daily/dates")
        if "error" not in daily_dates:
            for date_str in daily_dates.get("dates", []):
                # Date range filtering
                if start_date and date_str < start_date:
                    continue  # dates are sorted desc, but there may be gaps
                if end_date and date_str > end_date:
                    continue
                if len(matched) >= capped:
                    break
                index = self._get(f"/api/papers/daily?date={date_str}")
                if "error" in index:
                    continue
                for p in index.get("papers", []):
                    pid = p.get("paper_id", "")
                    if pid in seen_ids:
                        continue
                    if _matches(p):
                        p["source_type"] = "daily"
                        p["date"] = date_str
                        if pid in library_ids:
                            p["is_processed"] = True
                        matched.append(p)
                        seen_ids.add(pid)
                        if len(matched) >= capped:
                            break

        # 2. Search processed library (dedup against daily results)
        if len(matched) < capped and "error" not in lib:
            for p in lib.get("papers", []):
                pid = p.get("paper_id") or p.get("arxiv_id") or ""
                if pid in seen_ids:
                    continue
                if _matches(p):
                    p["source_type"] = "library"
                    p["is_processed"] = True
                    matched.append(p)
                    seen_ids.add(pid)
                    if len(matched) >= capped:
                        break

        return json.dumps(
            self._slim_papers({"papers": matched, "total": len(matched), "query": query}),
            default=str,
        )

    def list_papers(self) -> str:
        """List all processed papers."""
        result = self._get("/api/papers/list")
        return json.dumps(result, default=str)

    # ── Async versions of tool methods (for MCP) ────────────────────────

    async def aread_paper(
        self,
        query: str | None = None,
        paper_url: str | None = None,
        arxiv_id: str | None = None,
        auto_build_repos: bool = True,
        max_papers: int = 1,
    ) -> str:
        """Async version of read_paper."""
        data: dict = {"auto_build_repos": auto_build_repos, "max_papers": max_papers}
        if query:
            data["query"] = query
        if paper_url:
            data["paper_url"] = paper_url
        if arxiv_id:
            data["arxiv_id"] = arxiv_id
        result = await self._apost("/api/papers/read", data, timeout=60)
        return json.dumps(result, default=str)

    async def aget_paper_doc(self, paper_id: str, sections: str | None = None) -> str:
        """Async version of get_paper_doc."""
        if sections:
            result = await self._aget(f"/api/papers/{paper_id}/doc?sections={sections}", timeout=15)
        else:
            result = await self._aget(f"/api/papers/{paper_id}/doc?skeleton=true", timeout=15)
        return json.dumps(result, default=str)

    async def asearch_papers(
        self,
        query: str,
        start_date: str | None = None,
        end_date: str | None = None,
        max_results: int = 20,
    ) -> str:
        """Async version of search_papers — non-blocking loopback HTTP."""
        terms = [t.strip().lower() for t in query.split("|") if t.strip()]
        if not terms:
            return json.dumps({"papers": [], "total": 0, "query": query})

        capped = min(max(max_results, 1), 50)
        matched: list[dict] = []
        seen_ids: set[str] = set()

        library_ids: set[str] = set()
        lib = await self._aget("/api/papers/list")
        if "error" not in lib:
            for p in lib.get("papers", []):
                pid = p.get("paper_id") or p.get("arxiv_id") or ""
                if pid:
                    library_ids.add(pid)

        def _matches(paper: dict) -> bool:
            title = (paper.get("title") or "").lower()
            summary = (paper.get("summary") or paper.get("abstract") or "").lower()
            keywords = " ".join(paper.get("ai_keywords") or []).lower()
            pid = (paper.get("paper_id") or "").lower()
            haystack = f"{title} {summary} {keywords} {pid}"
            return any(t in haystack for t in terms)

        daily_dates = await self._aget("/api/papers/daily/dates")
        if "error" not in daily_dates:
            for date_str in daily_dates.get("dates", []):
                if start_date and date_str < start_date:
                    continue
                if end_date and date_str > end_date:
                    continue
                if len(matched) >= capped:
                    break
                index = await self._aget(f"/api/papers/daily?date={date_str}")
                if "error" in index:
                    continue
                for p in index.get("papers", []):
                    pid = p.get("paper_id", "")
                    if pid in seen_ids:
                        continue
                    if _matches(p):
                        p["source_type"] = "daily"
                        p["date"] = date_str
                        if pid in library_ids:
                            p["is_processed"] = True
                        matched.append(p)
                        seen_ids.add(pid)
                        if len(matched) >= capped:
                            break

        if len(matched) < capped and "error" not in lib:
            for p in lib.get("papers", []):
                pid = p.get("paper_id") or p.get("arxiv_id") or ""
                if pid in seen_ids:
                    continue
                if _matches(p):
                    p["source_type"] = "library"
                    p["is_processed"] = True
                    matched.append(p)
                    seen_ids.add(pid)
                    if len(matched) >= capped:
                        break

        return json.dumps(
            self._slim_papers({"papers": matched, "total": len(matched), "query": query}),
            default=str,
        )

    async def alist_papers(self) -> str:
        """Async version of list_papers."""
        result = await self._aget("/api/papers/list")
        return json.dumps(result, default=str)

    async def abrowse_daily_papers(self, date: str | None = None) -> str:
        """Async version of browse_daily_papers."""
        from datetime import date as date_type

        date_str = date or date_type.today().isoformat()

        result = await self._aget(f"/api/papers/daily?date={date_str}")
        if "error" in result and "404" in str(result.get("error", "")):
            crawl_result = await self._apost(f"/api/papers/crawl?date={date_str}", timeout=60)
            if "error" in crawl_result:
                return json.dumps(crawl_result, default=str)
            result = await self._aget(f"/api/papers/daily?date={date_str}")

        return json.dumps(self._slim_papers(result), default=str)

    async def abrowse_papers_range(
        self, start_date: str, end_date: str, min_upvotes: int = 0
    ) -> str:
        """Async version of browse_papers_range — uses asyncio.sleep instead of time.sleep."""
        crawl_result = await self._apost(
            f"/api/papers/crawl/range?start_date={start_date}&end_date={end_date}",
            timeout=120,
        )
        if "task_id" in crawl_result:
            import time as _time

            task_id = crawl_result["task_id"]
            deadline = _time.monotonic() + 15
            interval = 0.5
            while _time.monotonic() < deadline:
                await asyncio.sleep(interval)
                status = await self._aget(f"/api/papers/status/{task_id}")
                if status.get("status") in ("completed", "failed"):
                    break
                interval = min(interval * 2, 2)

        result = await self._aget(
            f"/api/papers/daily/range?start_date={start_date}&end_date={end_date}&min_upvotes={min_upvotes}"
        )
        return json.dumps(self._slim_papers(result), default=str)

    async def acrawl_papers(self, date: str | None = None) -> str:
        """Async version of crawl_papers."""
        from datetime import date as date_type

        date_str = date or date_type.today().isoformat()
        result = await self._apost(f"/api/papers/crawl?date={date_str}", timeout=60)
        return json.dumps(result, default=str)

    async def abrowse_papers(
        self,
        mode: str = "daily",
        date: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        min_upvotes: int = 0,
    ) -> str:
        """Async version of browse_papers."""
        if mode == "daily":
            return await self.abrowse_daily_papers(date)
        elif mode == "range":
            if not start_date or not end_date:
                return json.dumps({"error": "start_date and end_date are required for range mode"})
            return await self.abrowse_papers_range(start_date, end_date, min_upvotes)
        elif mode == "crawl":
            return await self.acrawl_papers(date)
        return json.dumps({"error": f"Unknown browse_papers mode: {mode}. Use: daily, range, crawl"})

    @staticmethod
    def _slim_papers(result: dict, max_papers: int = 50, summary_len: int = 200) -> dict:
        """Trim paper list for LLM context: truncate summaries, cap count, drop heavy fields."""
        papers = result.get("papers", [])
        slim = []
        for p in papers[:max_papers]:
            entry = {
                "paper_id": p.get("paper_id", ""),
                "title": p.get("title", ""),
                "upvotes": p.get("upvotes", 0),
                "github_repo": p.get("github_repo"),
                "ai_keywords": p.get("ai_keywords", []),
            }
            summary = p.get("summary", "")
            if summary:
                entry["summary"] = summary[:summary_len] + ("..." if len(summary) > summary_len else "")
            # Preserve source_type and is_processed so the LLM knows
            # whether get_paper_doc can be used directly
            if p.get("source_type"):
                entry["source_type"] = p["source_type"]
            if p.get("is_processed"):
                entry["is_processed"] = True
            slim.append(entry)
        out = {"date": result.get("date", ""), "total": len(papers), "papers": slim}
        if "crawled_at" in result:
            out["crawled_at"] = result["crawled_at"]
        return out

    def browse_daily_papers(self, date: str | None = None) -> str:
        """Browse HuggingFace daily papers for a specific date.

        If papers for the date are not yet crawled, automatically triggers a crawl first.
        """
        from datetime import date as date_type

        date_str = date or date_type.today().isoformat()

        # Try to get cached papers first
        result = self._get(f"/api/papers/daily?date={date_str}")
        if "error" in result and "404" in str(result.get("error", "")):
            # Not cached, trigger crawl
            crawl_result = self._post(f"/api/papers/crawl?date={date_str}", timeout=60)
            if "error" in crawl_result:
                return json.dumps(crawl_result, default=str)
            # Now fetch the cached data
            result = self._get(f"/api/papers/daily?date={date_str}")

        return json.dumps(self._slim_papers(result), default=str)

    def browse_papers_range(
        self, start_date: str, end_date: str, min_upvotes: int = 0
    ) -> str:
        """Browse papers over a date range with filtering.

        Automatically crawls any missing dates before returning results.
        """
        # First crawl any missing dates
        crawl_result = self._post(
            f"/api/papers/crawl/range?start_date={start_date}&end_date={end_date}",
            timeout=120,
        )
        # The range crawl is async — poll with short sleeps and a cap so we
        # don't monopolise a thread-pool slot for 30 straight seconds.
        if "task_id" in crawl_result:
            import time

            task_id = crawl_result["task_id"]
            deadline = time.monotonic() + 15  # cap at 15s, not 30
            interval = 0.5
            while time.monotonic() < deadline:
                time.sleep(interval)
                status = self._get(f"/api/papers/status/{task_id}")
                if status.get("status") in ("completed", "failed"):
                    break
                # Back off: 0.5 → 1 → 2 → 2 → 2 ...
                interval = min(interval * 2, 2)

        result = self._get(
            f"/api/papers/daily/range?start_date={start_date}&end_date={end_date}&min_upvotes={min_upvotes}"
        )
        return json.dumps(self._slim_papers(result), default=str)

    def crawl_papers(self, date: str | None = None) -> str:
        """Trigger crawl for a specific date's papers from HuggingFace."""
        from datetime import date as date_type

        date_str = date or date_type.today().isoformat()
        result = self._post(f"/api/papers/crawl?date={date_str}", timeout=60)
        return json.dumps(result, default=str)

    # -------------------------------------------------------------------------
    # Compound tool (shared by MCP and native)
    # -------------------------------------------------------------------------

    def browse_papers(
        self,
        mode: str = "daily",
        date: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        min_upvotes: int = 0,
    ) -> str:
        """Browse HuggingFace daily papers: daily, range, or crawl."""
        if mode == "daily":
            return self.browse_daily_papers(date)
        elif mode == "range":
            if not start_date or not end_date:
                return json.dumps({"error": "start_date and end_date are required for range mode"})
            return self.browse_papers_range(start_date, end_date, min_upvotes)
        elif mode == "crawl":
            return self.crawl_papers(date)
        return json.dumps({"error": f"Unknown browse_papers mode: {mode}. Use: daily, range, crawl"})


# --- LangChain Tool Wrappers (compound tools) ---


class ReadPaperInput(BaseModel):
    query: str | None = Field(default=None, description="Search query to find the paper")
    paper_url: str | None = Field(default=None, description="Direct URL to the paper")
    arxiv_id: str | None = Field(default=None, description="arXiv paper ID (e.g., 2504.20073)")
    auto_build_repos: bool = Field(default=True, description="Auto-build code graph for discovered repos")
    max_papers: int = Field(default=1, description="Max number of papers to process")


class GetPaperDocInput(BaseModel):
    paper_id: str = Field(description="Paper ID to retrieve the document for")
    sections: str | None = Field(default=None, description="Comma-separated section indices or titles to fetch full content (e.g. '0,2,Introduction'). Omit for skeleton overview.")


class SearchPapersInput(BaseModel):
    query: str = Field(description="Search query (e.g. 'flash attention', 'mixture of experts')")
    start_date: str | None = Field(default=None, description="Only include papers from this date onward (YYYY-MM-DD)")
    end_date: str | None = Field(default=None, description="Only include papers up to this date (YYYY-MM-DD)")
    max_results: int = Field(default=20, description="Max papers to return (default: 20)")


class ListPapersInput(BaseModel):
    pass


class BrowsePapersInput(BaseModel):
    mode: str = Field(default="daily", description='"daily" (default) | "range" | "crawl"')
    date: str | None = Field(default=None, description="YYYY-MM-DD (for daily/crawl, default: today)")
    start_date: str | None = Field(default=None, description="Start date YYYY-MM-DD (for range)")
    end_date: str | None = Field(default=None, description="End date YYYY-MM-DD (for range)")
    min_upvotes: int = Field(default=0, description="Min upvotes filter (for range)")


def create_paper_tools(api_port: int | None = None) -> list:
    """Create LangChain tools for paper reading (compound tools)."""
    tools = PaperTools(api_port)

    return [
        StructuredTool.from_function(
            func=tools.search_papers,
            name="search_papers",
            description=TOOL_DESCRIPTIONS.get("search_papers", "Search for papers across academic sources"),
            args_schema=SearchPapersInput,
        ),
        StructuredTool.from_function(
            func=tools.read_paper,
            name="read_paper",
            description=TOOL_DESCRIPTIONS.get("read_paper", "Start paper reading pipeline"),
            args_schema=ReadPaperInput,
        ),
        StructuredTool.from_function(
            func=tools.get_paper_doc,
            name="get_paper_doc",
            description=TOOL_DESCRIPTIONS.get("get_paper_doc", "Get generated paper document"),
            args_schema=GetPaperDocInput,
        ),
        StructuredTool.from_function(
            func=tools.list_papers,
            name="list_papers",
            description=TOOL_DESCRIPTIONS.get("list_papers", "List processed papers"),
            args_schema=ListPapersInput,
        ),
        StructuredTool.from_function(
            func=tools.browse_papers,
            name="browse_papers",
            description=TOOL_DESCRIPTIONS.get("browse_papers", "Browse HuggingFace daily papers"),
            args_schema=BrowsePapersInput,
        ),
    ]

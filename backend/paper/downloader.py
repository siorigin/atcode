# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""PDF download and storage management."""

from __future__ import annotations

import asyncio
import json
import re
import tarfile
import io
from datetime import datetime, timezone
from pathlib import Path

import httpx
from loguru import logger

from paper.models import PaperMetadata


def get_papers_dir() -> Path:
    """Get the papers data directory."""
    from core.config import get_data_dir

    d = get_data_dir() / "wiki_papers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_paper_dir(paper_id: str) -> Path:
    """Get directory for a specific paper."""
    safe_id = _sanitize_id(paper_id)
    d = get_papers_dir() / safe_id
    d.mkdir(parents=True, exist_ok=True)
    return d


class PaperDownloader:
    """Download and manage paper PDFs."""

    def __init__(self, timeout: float = 120.0):
        self.timeout = timeout

    async def download(self, paper: PaperMetadata) -> Path:
        """Download PDF and save metadata. Returns path to PDF file."""
        paper_dir = get_paper_dir(paper.paper_id)
        pdf_path = paper_dir / "raw.pdf"
        meta_path = paper_dir / "metadata.json"

        # Save metadata
        meta_path.write_text(paper.model_dump_json(indent=2))

        # Try ar5iv HTML first for arxiv papers (better quality)
        is_arxiv = paper.source == "arxiv" or "arxiv.org" in (paper.url or "")
        if is_arxiv:
            html_path = paper_dir / "ar5iv.html"
            if await self._try_ar5iv(paper.paper_id, html_path):
                logger.info(f"Downloaded ar5iv HTML for {paper.paper_id}")
                # Still download PDF as fallback
                if paper.pdf_url:
                    await self._download_file(paper.pdf_url, pdf_path)
                return html_path

            # Try LaTeX source (better than PDF for ~89% of arxiv papers)
            latex_dir = paper_dir / "latex_src"
            if await self._try_latex_source(paper.paper_id, latex_dir):
                logger.info(f"Downloaded LaTeX source for {paper.paper_id}")
                # Still download PDF for figures
                if paper.pdf_url:
                    try:
                        await self._download_file(paper.pdf_url, pdf_path)
                    except Exception as e:
                        logger.debug(f"PDF download failed (non-critical, have LaTeX): {e}")
                return latex_dir

        # Download PDF
        if not paper.pdf_url:
            raise ValueError(f"No PDF URL for paper {paper.paper_id}")

        await self._download_file(paper.pdf_url, pdf_path)
        logger.info(f"Downloaded PDF for {paper.paper_id}: {pdf_path}")

        # Write status
        status_path = paper_dir / "status.json"
        status_path.write_text(
            json.dumps(
                {
                    "status": "downloaded",
                    "progress": 0.2,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        )

        return pdf_path

    async def _try_ar5iv(self, arxiv_id: str, output_path: Path) -> bool:
        """Try to download ar5iv HTML version of an arXiv paper."""
        # Clean up arxiv ID (remove version suffix like v1, v2)
        clean_id = re.sub(r"v\d+$", "", arxiv_id)
        url = f"https://ar5iv.labs.arxiv.org/html/{clean_id}"

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code == 200 and len(resp.text) > 1000:
                    # Check for ar5iv conversion errors
                    if "ltx_ERROR" in resp.text or "Conversion failed" in resp.text:
                        logger.debug(f"ar5iv conversion has errors for {arxiv_id}, skipping")
                        return False
                    # Verify this is actually an ar5iv page (has ltx_ classes)
                    # and not a redirect to the plain arxiv abstract page
                    if "ltx_document" not in resp.text and "ltx_page" not in resp.text:
                        logger.debug(f"ar5iv page for {arxiv_id} has no ltx_ content — not a valid ar5iv page")
                        return False
                    output_path.write_text(resp.text, encoding="utf-8")
                    return True
        except Exception as e:
            logger.debug(f"ar5iv download failed for {arxiv_id}: {e}")
        return False

    async def _try_latex_source(self, arxiv_id: str, output_dir: Path) -> bool:
        """Try to download and extract LaTeX source from arxiv e-print endpoint."""
        clean_id = re.sub(r"v\d+$", "", arxiv_id)
        url = f"https://arxiv.org/e-print/{clean_id}"

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.debug(f"LaTeX source not available for {arxiv_id}: HTTP {resp.status_code}")
                    return False

                content = resp.content
                if len(content) < 100:
                    return False

                output_dir.mkdir(parents=True, exist_ok=True)

                # arxiv e-print returns gzipped tar (or sometimes plain tex)
                try:
                    with tarfile.open(fileobj=io.BytesIO(content), mode="r:*") as tar:
                        # Security: filter out absolute paths and path traversal
                        safe_members = []
                        for member in tar.getmembers():
                            if member.name.startswith("/") or ".." in member.name:
                                continue
                            safe_members.append(member)
                        tar.extractall(path=str(output_dir), members=safe_members)
                except tarfile.TarError:
                    # Might be a single .tex file (gzipped or plain)
                    import gzip
                    try:
                        tex_content = gzip.decompress(content).decode("utf-8", errors="replace")
                    except (gzip.BadGzipFile, OSError):
                        tex_content = content.decode("utf-8", errors="replace")

                    if "\\documentclass" in tex_content or "\\begin{document}" in tex_content:
                        (output_dir / "main.tex").write_text(tex_content, encoding="utf-8")
                    else:
                        logger.debug(f"Downloaded content for {arxiv_id} is not valid LaTeX")
                        return False

                # Validate: must have at least one .tex file
                tex_files = list(output_dir.rglob("*.tex"))
                if not tex_files:
                    logger.debug(f"No .tex files found in extracted source for {arxiv_id}")
                    return False

                logger.info(f"Extracted {len(tex_files)} .tex file(s) for {arxiv_id}")
                return True

        except Exception as e:
            logger.debug(f"LaTeX source download failed for {arxiv_id}: {e}")
        return False

    async def _download_file(self, url: str, output_path: Path) -> None:
        """Download a file from URL without buffering the full response in memory."""
        await asyncio.to_thread(self._download_file_sync, url, output_path)

    def _download_file_sync(self, url: str, output_path: Path) -> None:
        """Synchronous streaming download implementation for thread offload."""
        tmp_path = output_path.with_suffix(f"{output_path.suffix}.part")
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with tmp_path.open("wb") as f:
                        for chunk in resp.iter_bytes():
                            if chunk:
                                f.write(chunk)
            tmp_path.replace(output_path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise

    def list_papers(self) -> list[dict]:
        """List all downloaded papers."""
        papers_dir = get_papers_dir()
        result = []
        for paper_dir in sorted(papers_dir.iterdir()):
            if not paper_dir.is_dir():
                continue
            meta_path = paper_dir / "metadata.json"
            status_path = paper_dir / "status.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text())
                status = "unknown"
                if status_path.exists():
                    status = json.loads(status_path.read_text()).get("status", "unknown")
                result.append({**meta, "status": status})
        return result

    def get_paper(self, paper_id: str) -> dict | None:
        """Get metadata and status for a specific paper."""
        paper_dir = get_paper_dir(paper_id)
        meta_path = paper_dir / "metadata.json"
        if not meta_path.exists():
            return None
        meta = json.loads(meta_path.read_text())
        status_path = paper_dir / "status.json"
        if status_path.exists():
            meta["_status"] = json.loads(status_path.read_text())
        doc_path = paper_dir / "doc" / "paper_reading.json"
        meta["_has_doc"] = doc_path.exists()
        return meta

    def delete_paper(self, paper_id: str) -> bool:
        """Delete a paper and all its artifacts."""
        import shutil

        paper_dir = get_paper_dir(paper_id)
        if paper_dir.exists():
            shutil.rmtree(paper_dir)
            return True
        return False


def _sanitize_id(paper_id: str) -> str:
    """Sanitize paper ID for use as directory name."""
    return re.sub(r"[^\w\-.]", "_", paper_id)

# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Data models for paper reading feature."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PaperMetadata(BaseModel):
    """Metadata for a paper from any search source."""

    paper_id: str  # arxiv ID or sanitized identifier
    title: str
    authors: list[str] = []
    abstract: str = ""
    source: str  # "arxiv" | "semantic_scholar" | "papers_with_code"
    url: str = ""
    pdf_url: str | None = None
    published_date: str | None = None
    github_urls: list[str] = []
    citations: int = 0


class ImageInfo(BaseModel):
    path: str
    caption: str = ""
    page: int = 0


class TableInfo(BaseModel):
    caption: str = ""
    markdown: str = ""
    page: int = 0


class PaperParseResult(BaseModel):
    """Result of parsing a paper PDF."""

    paper_id: str
    markdown_content: str = ""
    images: list[ImageInfo] = []
    tables: list[TableInfo] = []
    formulas: list[str] = []
    github_urls: list[str] = []


class CodeRef(BaseModel):
    """A reference to a code entity in the knowledge graph."""

    qualified_name: str
    display_name: str = ""
    file_path: str = ""
    line: int = 0


class DocSection(BaseModel):
    """A section in the interactive reading document."""

    title: str
    content: str  # Markdown content
    level: int = 1  # Heading level 1-3
    collapsible: bool = True
    code_refs: list[CodeRef] = []


class FigureBlock(BaseModel):
    """A figure/table block in the document."""

    figure_type: str = "image"  # "image" | "table"
    path: str = ""
    caption: str = ""
    markdown: str = ""  # For tables
    page: int = 0


class CodeAnalysis(BaseModel):
    """Code analysis result from AtCode knowledge graph."""

    repo_url: str = ""
    repo_name: str = ""
    project_name: str = ""
    structure_overview: str = ""
    key_components: list[dict] = []
    architecture_diagram: str = ""  # Mermaid diagram
    paper_code_mapping: list[dict] = []  # [{paper_concept, code_entity, explanation}]


class Reference(BaseModel):
    title: str = ""
    authors: str = ""
    url: str = ""


class PaperReadingDoc(BaseModel):
    """Complete interactive reading document."""

    paper: PaperMetadata
    sections: list[DocSection] = []
    figures: list[FigureBlock] = []
    code_analysis: CodeAnalysis | None = None
    references: list[Reference] = []


# --- API Request/Response Models ---


class PaperSearchRequest(BaseModel):
    query: str
    sources: list[str] = Field(default=["arxiv", "semantic_scholar"])
    max_results: int = Field(default=10, ge=1, le=50)


class PaperSearchResponse(BaseModel):
    papers: list[PaperMetadata]
    total: int = 0


class PaperReadRequest(BaseModel):
    query: str | None = None
    paper_url: str | None = None
    arxiv_id: str | None = None
    auto_build_repos: bool = True
    max_papers: int = Field(default=1, ge=1, le=5)


class PaperReadResponse(BaseModel):
    task_id: str
    message: str = "Paper reading pipeline started"


class PaperStatusResponse(BaseModel):
    paper_id: str
    status: str  # "searching" | "downloading" | "parsing" | "extracting" | "building" | "analyzing" | "generating" | "completed" | "failed"
    progress: float = 0.0
    step: str = ""
    error: str | None = None


class PaperListItem(BaseModel):
    paper_id: str
    title: str
    authors: list[str] = []
    source: str = ""
    status: str = "completed"
    created_at: str = ""


# --- HuggingFace Daily Papers Models ---


class HFPaperEntry(BaseModel):
    """Single paper entry from HF Daily Papers API."""

    paper_id: str  # arXiv ID
    title: str
    summary: str = ""  # abstract
    authors: list[str] = []
    published_at: str = ""
    upvotes: int = 0
    num_comments: int = 0
    ai_summary: str | None = None
    ai_keywords: list[str] = []
    github_repo: str | None = None
    github_stars: int | None = None
    organization: str | None = None
    thumbnail_url: str | None = None
    submitted_by: str | None = None  # HF submitter username
    submitted_at: str = ""  # when submitted to HF
    source: str = "huggingface"


class DailyIndex(BaseModel):
    """Index of papers for a single day."""

    date: str
    papers: list[HFPaperEntry]
    crawled_at: str = ""
    total: int = 0

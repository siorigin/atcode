# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Generate interactive paper reading documents."""

from __future__ import annotations

import json
import re
from pathlib import Path

from loguru import logger

from paper.models import (
    CodeAnalysis,
    CodeRef,
    DocSection,
    FigureBlock,
    PaperMetadata,
    PaperParseResult,
    PaperReadingDoc,
    Reference,
)


class PaperDocGenerator:
    """Generate interactive reading documents combining paper content and code analysis."""

    def __init__(self, llm_config: dict | None = None):
        self.llm_config = llm_config

    async def generate(
        self,
        paper: PaperMetadata,
        parse_result: PaperParseResult,
        code_analysis: CodeAnalysis | None = None,
    ) -> PaperReadingDoc:
        """Generate a complete interactive reading document."""
        sections = self._build_sections(paper, parse_result, code_analysis)
        figures = self._build_figures(parse_result)
        references = self._extract_references(parse_result.markdown_content)

        doc = PaperReadingDoc(
            paper=paper,
            sections=sections,
            figures=figures,
            code_analysis=code_analysis,
            references=references,
        )

        return doc

    def _build_sections(
        self,
        paper: PaperMetadata,
        parse_result: PaperParseResult,
        code_analysis: CodeAnalysis | None,
    ) -> list[DocSection]:
        """Build document sections from parsed content."""
        sections: list[DocSection] = []

        # 1. Overview section
        overview_content = f"**{paper.title}**\n\n"
        if paper.authors:
            overview_content += f"*Authors: {', '.join(paper.authors[:10])}*\n\n"
        if paper.published_date:
            overview_content += f"*Published: {paper.published_date}*\n\n"
        if paper.citations > 0:
            overview_content += f"*Citations: {paper.citations}*\n\n"
        if paper.abstract:
            overview_content += f"## Abstract\n\n{paper.abstract}\n"

        sections.append(
            DocSection(
                title="Overview",
                content=overview_content,
                level=1,
                collapsible=False,
            )
        )

        # 2. Parse markdown content into sections by headings
        content_sections = self._split_by_headings(parse_result.markdown_content)
        for title, content, level in content_sections:
            code_refs = self._find_code_refs(content, code_analysis) if code_analysis else []
            sections.append(
                DocSection(
                    title=title,
                    content=content,
                    level=min(level, 3),
                    collapsible=True,
                    code_refs=code_refs,
                )
            )

        # 3. Code analysis section (if available)
        if code_analysis and code_analysis.repo_url:
            code_content = self._format_code_analysis(code_analysis)
            sections.append(
                DocSection(
                    title="Code Implementation Analysis",
                    content=code_content,
                    level=1,
                    collapsible=True,
                )
            )

        return sections

    # Known academic section titles for standalone heading detection
    _STANDALONE_TITLES = {
        "abstract", "introduction", "related work", "background", "method", "methods",
        "methodology", "approach", "experiments", "experiment", "evaluation",
        "results", "discussion", "conclusion", "conclusions", "limitations",
        "acknowledgments", "acknowledgements", "appendix", "references",
        "bibliography", "future work", "analysis", "setup", "dataset",
        "implementation", "training", "inference", "ablation", "ablation study",
    }

    def _split_by_headings(self, markdown: str) -> list[tuple[str, str, int]]:
        """Split markdown content into sections by headings.

        Detects three heading styles:
        1. Markdown headings: ## Title
        2. Numbered section headings: 1. Introduction, 2.1 Method
        3. Standalone title lines: Introduction (preceded by blank line, known title)
        """
        if not markdown.strip():
            return []

        lines = markdown.split("\n")
        sections: list[tuple[str, str, int]] = []
        current_title = "Introduction"
        current_level = 2
        current_lines: list[str] = []

        numbered_heading_re = re.compile(
            r"^(\d{1,2}(?:\.\d{1,2})*)[.\s]+([A-Z][A-Za-z\s:,&()\-/]{2,80})$"
        )

        def _numbered_level(num_str: str) -> int:
            depth = num_str.count(".") + 1
            return min(depth, 3)

        for i, line in enumerate(lines):
            heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
            stripped = line.strip()
            numbered_match = numbered_heading_re.match(stripped) if not heading_match else None

            # Standalone heading: short title, preceded by blank line, known section name
            standalone_match = False
            if (
                not heading_match
                and not numbered_match
                and 3 < len(stripped) < 60
                and stripped[0].isupper()
                and i > 0
                and len(lines[i - 1].strip()) < 3
                and stripped.lower() in self._STANDALONE_TITLES
            ):
                standalone_match = True

            if heading_match or numbered_match or standalone_match:
                # Save previous section
                if current_lines:
                    content = "\n".join(current_lines).strip()
                    if content:
                        sections.append((current_title, content, current_level))

                if heading_match:
                    current_title = heading_match.group(2).strip()
                    current_level = len(heading_match.group(1))
                elif numbered_match:
                    num_part = numbered_match.group(1)
                    current_title = numbered_match.group(2).strip()
                    current_level = _numbered_level(num_part)
                else:
                    current_title = stripped
                    current_level = 1
                current_lines = []
            else:
                current_lines.append(line)

        # Save last section
        if current_lines:
            content = "\n".join(current_lines).strip()
            if content:
                sections.append((current_title, content, current_level))

        return sections

    def _build_figures(self, parse_result: PaperParseResult) -> list[FigureBlock]:
        """Build figure blocks from parsed content."""
        figures: list[FigureBlock] = []

        for img in parse_result.images:
            figures.append(
                FigureBlock(
                    figure_type="image",
                    path=img.path,
                    caption=img.caption,
                    page=img.page,
                )
            )

        for table in parse_result.tables:
            figures.append(
                FigureBlock(
                    figure_type="table",
                    caption=table.caption,
                    markdown=table.markdown,
                    page=table.page,
                )
            )

        return figures

    def _find_code_refs(self, text: str, code_analysis: CodeAnalysis | None) -> list[CodeRef]:
        """Find references to code entities in text."""
        if not code_analysis or not code_analysis.key_components:
            return []

        refs: list[CodeRef] = []
        seen: set[str] = set()

        for comp in code_analysis.key_components:
            qname = comp.get("qualified_name", "")
            display = qname.split(".")[-1] if qname else ""
            if display and display.lower() in text.lower() and qname not in seen:
                seen.add(qname)
                refs.append(
                    CodeRef(
                        qualified_name=qname,
                        display_name=display,
                        file_path=comp.get("file_path", ""),
                    )
                )

        return refs

    def _format_code_analysis(self, analysis: CodeAnalysis) -> str:
        """Format code analysis as markdown."""
        parts = []

        parts.append(f"**Repository:** [{analysis.repo_name}]({analysis.repo_url})")
        parts.append(f"**AtCode Project:** `{analysis.project_name}`\n")

        if analysis.structure_overview:
            parts.append("### Repository Structure\n")
            parts.append(analysis.structure_overview)

        if analysis.architecture_diagram:
            parts.append("\n### Architecture\n")
            parts.append(f"```mermaid\n{analysis.architecture_diagram}\n```")

        if analysis.key_components:
            parts.append("\n### Key Components\n")
            parts.append("| Component | Description | Role |")
            parts.append("|-----------|-------------|------|")
            for comp in analysis.key_components:
                qname = comp.get("qualified_name", "")
                doc = comp.get("docstring", "")[:100]
                role = comp.get("role", "")
                parts.append(f"| `{qname}` | {doc} | {role} |")

        if analysis.paper_code_mapping:
            parts.append("\n### Paper ↔ Code Mapping\n")
            parts.append("| Paper Concept | Code Entity | Explanation |")
            parts.append("|---------------|-------------|-------------|")
            for m in analysis.paper_code_mapping:
                parts.append(f"| {m.get('paper_concept', '')} | `{m.get('code_entity', '')}` | {m.get('explanation', '')} |")

        return "\n".join(parts)

    def _extract_references(self, markdown: str) -> list[Reference]:
        """Extract references from markdown content."""
        refs: list[Reference] = []

        # Look for a references section
        ref_section = ""
        parts = re.split(r"#+\s+(?:References|Bibliography)\s*\n", markdown, flags=re.IGNORECASE)
        if len(parts) > 1:
            ref_section = parts[-1]

        if not ref_section:
            return refs

        # Extract numbered references [1], [2], etc.
        for match in re.finditer(r"\[(\d+)\]\s*(.+?)(?=\[\d+\]|\Z)", ref_section, re.DOTALL):
            text = match.group(2).strip()
            url = ""
            url_match = re.search(r"https?://\S+", text)
            if url_match:
                url = url_match.group(0).rstrip(".")
            refs.append(Reference(title=text[:200], url=url))

        return refs[:50]  # Limit to 50 references

    def save_doc(self, doc: PaperReadingDoc, paper_id: str) -> Path:
        """Save the generated document to disk."""
        from paper.downloader import get_paper_dir

        paper_dir = get_paper_dir(paper_id)
        doc_dir = paper_dir / "doc"
        doc_dir.mkdir(parents=True, exist_ok=True)

        doc_path = doc_dir / "paper_reading.json"
        doc_path.write_text(doc.model_dump_json(indent=2), encoding="utf-8")

        logger.info(f"Saved paper reading doc: {doc_path}")
        return doc_path

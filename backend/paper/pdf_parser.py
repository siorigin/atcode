# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""PDF parsing using MinerU for academic papers, with LaTeX source support."""

from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path

from loguru import logger

from paper.models import ImageInfo, PaperParseResult, TableInfo


class MinerUPDFParser:
    """Parse academic PDFs using MinerU (magic-pdf), with LaTeX source fallback."""

    def __init__(self, use_mineru: bool = False):
        """Args:
            use_mineru: If True, use heavyweight MinerU model for PDF parsing.
                        If False (default), use PyMuPDF text extraction instead.
        """
        self.use_mineru = use_mineru

    async def parse(self, input_path: Path, output_dir: Path, paper_id: str = "") -> PaperParseResult:
        """Parse PDF, HTML, or LaTeX source directory into structured content.

        Args:
            input_path: Path to PDF file, ar5iv HTML file, or LaTeX source directory.
            output_dir: Directory to store parsed output.
            paper_id: Paper identifier for the result.

        Returns:
            PaperParseResult with markdown content, images, tables, formulas, and GitHub URLs.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        if input_path.suffix == ".html":
            result = await self._parse_html(input_path, output_dir, paper_id)
            # Validate HTML parse quality — fall back to PDF if content is too short
            # or looks like arxiv navigation junk
            if len(result.markdown_content) < 2000 or "arxivLabs" in result.markdown_content:
                logger.warning(f"HTML parse produced poor content ({len(result.markdown_content)} chars), falling back to PDF")
                pdf_path = input_path.parent / "raw.pdf"
                if pdf_path.exists():
                    return await self._parse_pdf(pdf_path, output_dir, paper_id)
            return result

        # LaTeX source directory
        if input_path.is_dir():
            result = await self._parse_latex(input_path, output_dir, paper_id)
            if len(result.markdown_content) > 2000:
                return result
            logger.warning(f"LaTeX parse produced short content ({len(result.markdown_content)} chars), falling back to PDF")
            pdf_path = input_path.parent / "raw.pdf"
            if pdf_path.exists():
                return await self._parse_pdf(pdf_path, output_dir, paper_id)
            return result

        return await self._parse_pdf(input_path, output_dir, paper_id)

    async def _parse_pdf(self, pdf_path: Path, output_dir: Path, paper_id: str) -> PaperParseResult:
        """Parse PDF using MinerU."""
        # Run MinerU in a thread to avoid blocking the event loop
        markdown_content = await asyncio.to_thread(self._run_mineru, pdf_path, output_dir)

        images = self._extract_images(output_dir)
        tables = self._extract_tables(markdown_content)
        formulas = self._extract_formulas(markdown_content)
        github_urls = self._extract_github_urls(markdown_content)

        # Save parsed content
        content_path = output_dir / "content.md"
        content_path.write_text(markdown_content, encoding="utf-8")

        return PaperParseResult(
            paper_id=paper_id,
            markdown_content=markdown_content,
            images=images,
            tables=tables,
            formulas=formulas,
            github_urls=github_urls,
        )

    async def _parse_html(self, html_path: Path, output_dir: Path, paper_id: str) -> PaperParseResult:
        """Parse ar5iv HTML (simpler than PDF, better quality)."""
        html_content = html_path.read_text(encoding="utf-8")

        # Convert HTML to markdown using a simple approach
        markdown_content = await asyncio.to_thread(self._html_to_markdown, html_content)

        content_path = output_dir / "content.md"
        content_path.write_text(markdown_content, encoding="utf-8")

        tables = self._extract_tables(markdown_content)
        formulas = self._extract_formulas(markdown_content)
        github_urls = self._extract_github_urls(markdown_content)

        return PaperParseResult(
            paper_id=paper_id,
            markdown_content=markdown_content,
            images=[],  # HTML images are external links, not extracted
            tables=tables,
            formulas=formulas,
            github_urls=github_urls,
        )

    async def _parse_latex(self, latex_dir: Path, output_dir: Path, paper_id: str) -> PaperParseResult:
        """Parse LaTeX source directory into markdown via pandoc."""
        markdown_content = await asyncio.to_thread(self._run_latex_parse, latex_dir)

        content_path = output_dir / "content.md"
        content_path.write_text(markdown_content, encoding="utf-8")

        tables = self._extract_tables(markdown_content)
        formulas = self._extract_formulas(markdown_content)
        github_urls = self._extract_github_urls(markdown_content)

        return PaperParseResult(
            paper_id=paper_id,
            markdown_content=markdown_content,
            images=[],
            tables=tables,
            formulas=formulas,
            github_urls=github_urls,
        )

    def _run_latex_parse(self, latex_dir: Path) -> str:
        """Parse LaTeX source to markdown (synchronous, called in thread)."""
        try:
            # Find main .tex file
            main_tex = self._find_main_tex(latex_dir)
            if not main_tex:
                logger.warning(f"No main .tex file found in {latex_dir}")
                return ""

            logger.info(f"Parsing LaTeX from {main_tex.name}")

            # Read and preprocess
            tex_content = main_tex.read_text(encoding="utf-8", errors="replace")

            # Extract body content (between \begin{document} and \end{document})
            body = self._extract_latex_body(tex_content)

            # Resolve \input{} and \include{} directives
            body = self._resolve_latex_inputs(body, main_tex.parent)

            # Expand simple custom macros from preamble
            preamble = self._extract_preamble(tex_content)
            macros = self._parse_newcommands(preamble, latex_dir)
            body = self._expand_macros(body, macros)

            # Convert via pandoc
            md_content = self._run_pandoc(body, latex_dir)

            # Post-process pandoc output
            md_content = self._postprocess_pandoc(md_content)

            return md_content

        except Exception as e:
            logger.error(f"LaTeX parsing failed: {e}")
            return ""

    def _find_main_tex(self, latex_dir: Path) -> Path | None:
        """Find the main .tex file containing \\documentclass."""
        tex_files = list(latex_dir.rglob("*.tex"))
        if not tex_files:
            return None

        # Prefer file with \documentclass
        for f in tex_files:
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
                if r"\documentclass" in content and r"\begin{document}" in content:
                    return f
            except Exception:
                continue

        # Fallback: common names
        for name in ["main.tex", "paper.tex", "article.tex", "manuscript.tex"]:
            candidate = latex_dir / name
            if candidate.exists():
                return candidate

        # Last resort: largest .tex file
        return max(tex_files, key=lambda f: f.stat().st_size)

    def _extract_latex_body(self, tex_content: str) -> str:
        """Extract content between \\begin{document} and \\end{document}."""
        match = re.search(
            r"\\begin\{document\}(.*?)\\end\{document\}",
            tex_content,
            re.DOTALL,
        )
        if match:
            return match.group(1).strip()
        # No document environment — return as-is (might be a fragment)
        return tex_content

    def _extract_preamble(self, tex_content: str) -> str:
        """Extract preamble (before \\begin{document})."""
        match = re.search(r"^(.*?)\\begin\{document\}", tex_content, re.DOTALL)
        return match.group(1) if match else ""

    def _resolve_latex_inputs(self, body: str, base_dir: Path, depth: int = 0) -> str:
        """Resolve \\input{} and \\include{} directives inline."""
        if depth > 10:
            return body

        def replace_input(match: re.Match) -> str:
            filename = match.group(1)
            if not filename.endswith(".tex"):
                filename += ".tex"
            filepath = base_dir / filename
            if not filepath.exists():
                # Try without .tex extension that we added
                filepath = base_dir / match.group(1)
            if filepath.exists():
                try:
                    included = filepath.read_text(encoding="utf-8", errors="replace")
                    return self._resolve_latex_inputs(included, filepath.parent, depth + 1)
                except Exception:
                    pass
            return match.group(0)  # keep original if file not found

        body = re.sub(r"\\input\{([^}]+)\}", replace_input, body)
        body = re.sub(r"\\include\{([^}]+)\}", replace_input, body)
        return body

    def _parse_newcommands(self, preamble: str, latex_dir: Path) -> dict[str, str]:
        """Parse \\newcommand and \\def definitions for simple text macros."""
        macros: dict[str, str] = {}

        # Also read .sty files for macro definitions
        all_text = preamble
        for sty_file in latex_dir.glob("*.sty"):
            try:
                all_text += "\n" + sty_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

        # Match \newcommand{\name}{replacement} (no args only)
        for m in re.finditer(r"\\(?:newcommand|renewcommand)\{(\\[a-zA-Z]+)\}\s*\{([^{}]*)\}", all_text):
            name = m.group(1)
            replacement = m.group(2).strip()
            # Only expand simple text macros, skip math-heavy ones
            if len(replacement) < 100 and "\\" not in replacement:
                macros[name] = replacement

        # Match \def\name{replacement} (no args only)
        for m in re.finditer(r"\\def(\\[a-zA-Z]+)\s*\{([^{}]*)\}", all_text):
            name = m.group(1)
            replacement = m.group(2).strip()
            if len(replacement) < 100 and "\\" not in replacement:
                macros[name] = replacement

        return macros

    def _expand_macros(self, body: str, macros: dict[str, str]) -> str:
        """Expand simple text macros in body."""
        for name, replacement in macros.items():
            # Replace \name{} or \name followed by space/non-letter
            body = re.sub(
                re.escape(name) + r"(?:\{\})?(?=[\s\W]|$)",
                replacement,
                body,
            )
        return body

    def _run_pandoc(self, latex_body: str, working_dir: Path) -> str:
        """Convert LaTeX body to markdown using pandoc."""
        try:
            result = subprocess.run(
                [
                    "pandoc",
                    "-f", "latex",
                    "-t", "markdown",
                    "--wrap=none",
                ],
                input=latex_body,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(working_dir),
            )
            if result.returncode == 0:
                return result.stdout
            logger.warning(f"pandoc returned {result.returncode}: {result.stderr[:500]}")
            # Still return what we got — pandoc often succeeds partially
            return result.stdout if result.stdout else ""

        except subprocess.TimeoutExpired:
            logger.warning("pandoc timed out on full document, trying section-by-section")
            return self._run_pandoc_chunked(latex_body, working_dir)
        except FileNotFoundError:
            logger.error("pandoc not found — install with: apt install pandoc")
            return ""

    def _run_pandoc_chunked(self, latex_body: str, working_dir: Path) -> str:
        """Fallback: split LaTeX into sections and convert each separately."""
        # Split on \section
        sections = re.split(r"(\\section\{[^}]*\})", latex_body)
        result_parts = []

        for chunk in sections:
            if not chunk.strip():
                continue
            try:
                proc = subprocess.run(
                    ["pandoc", "-f", "latex", "-t", "markdown", "--wrap=none"],
                    input=chunk,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    cwd=str(working_dir),
                )
                if proc.stdout:
                    result_parts.append(proc.stdout)
            except (subprocess.TimeoutExpired, Exception) as e:
                logger.debug(f"pandoc chunk failed: {e}")
                result_parts.append(chunk)

        return "\n\n".join(result_parts)

    def _postprocess_pandoc(self, md: str) -> str:
        """Clean up pandoc-generated markdown."""
        # Remove ::: div wrappers
        md = re.sub(r"^:::\s*\{[^}]*\}\s*$", "", md, flags=re.MULTILINE)
        md = re.sub(r"^:::\s*$", "", md, flags=re.MULTILINE)

        # Remove pandoc reference attributes: {reference-type="ref" ...}
        md = re.sub(r"\{reference-type=\"[^\"]*\"[^}]*\}", "", md)

        # Clean citation format: [@key] → [key]
        md = re.sub(r"\[@([^\]]+)\]", r"[\1]", md)

        # Remove \[\]{} label artifacts
        md = re.sub(r"\\\[\]\{[^}]*\}", "", md)

        # Clean excessive blank lines
        md = re.sub(r"\n{3,}", "\n\n", md)

        return md.strip()

    def _run_mineru(self, pdf_path: Path, output_dir: Path) -> str:
        """Run MinerU PDF parser (synchronous, called in thread).

        When USE_MINERU is False (default), skips the heavyweight MinerU model
        and goes straight to PyMuPDF text extraction.
        """
        if not self.use_mineru:
            logger.info("MinerU disabled, using PyMuPDF fallback")
            return self._fallback_extract(pdf_path)

        # Try MinerU 2.x (mineru package) first
        try:
            return self._run_mineru_v2(pdf_path, output_dir)
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"MinerU 2.x failed: {e}, trying 1.x fallback")

        # Try MinerU 1.x (magic-pdf package)
        try:
            return self._run_mineru_v1(pdf_path, output_dir)
        except ImportError:
            logger.warning("Neither mineru nor magic-pdf installed, falling back to basic extraction")
            return self._fallback_extract(pdf_path)
        except Exception as e:
            logger.error(f"MinerU 1.x parsing failed: {e}")
            return self._fallback_extract(pdf_path)

    def _run_mineru_v2(self, pdf_path: Path, output_dir: Path) -> str:
        """Run MinerU 2.x parser."""
        from mineru.cli.common import prepare_env, read_fn
        from mineru.backend.hybrid.hybrid_analyze import doc_analyze as hybrid_doc_analyze
        from mineru.backend.vlm.vlm_middle_json_mkcontent import union_make
        from mineru.data.data_reader_writer import FileBasedDataWriter
        from mineru.utils.enum_class import MakeMode

        pdf_bytes = read_fn(str(pdf_path))
        local_image_dir, local_md_dir = prepare_env(str(output_dir), pdf_path.stem, "hybrid_auto")
        image_writer = FileBasedDataWriter(local_image_dir)

        middle_json, infer_result, vlm_ocr_enable = hybrid_doc_analyze(
            pdf_bytes,
            image_writer=image_writer,
            backend="transformers",
            parse_method="auto",
            language="en",
        )

        # Generate markdown from middle_json
        pdf_info = middle_json["pdf_info"]
        image_dir = str(Path(local_image_dir).name)
        md_content = union_make(pdf_info, MakeMode.MM_MD, image_dir)

        # Also save the .md file
        md_writer = FileBasedDataWriter(local_md_dir)
        md_writer.write_string(f"{pdf_path.stem}.md", md_content)

        return md_content

    def _run_mineru_v1(self, pdf_path: Path, output_dir: Path) -> str:
        """Run MinerU 1.x (magic-pdf) parser."""
        from magic_pdf.data.data_reader_writer import FileBasedDataReader, FileBasedDataWriter
        from magic_pdf.data.dataset import PymuDocDataset
        from magic_pdf.model.doc_analyze_by_custom_model import doc_analyze

        reader = FileBasedDataReader("")
        pdf_bytes = reader.read(str(pdf_path))

        ds = PymuDocDataset(pdf_bytes)
        images_dir = output_dir / "images"
        images_dir.mkdir(exist_ok=True)

        writer = FileBasedDataWriter(str(output_dir))
        img_writer = FileBasedDataWriter(str(images_dir))

        # Run model inference
        infer_result = ds.apply(doc_analyze, ocr=True)

        # Extract content as markdown
        pipe_result = infer_result.pipe_ocr_mode(writer, img_writer)
        md_content = pipe_result.get_markdown(img_writer)

        return md_content

    def _fallback_extract(self, pdf_path: Path) -> str:
        """Fallback PDF text extraction when MinerU is not available."""
        try:
            import fitz  # PyMuPDF

            doc = fitz.open(str(pdf_path))
            text_parts = []
            for page in doc:
                text_parts.append(page.get_text("text"))
            doc.close()
            return "\n\n".join(text_parts)
        except ImportError:
            logger.warning("PyMuPDF not installed either, returning empty content")
            return ""

    def _html_to_markdown(self, html: str) -> str:
        """Convert ar5iv HTML to markdown, extracting only article content."""
        # Extract article body from ar5iv HTML (skip nav, scripts, styles)
        import re as re_mod

        # Try to find the main article content
        article_match = re_mod.search(
            r'<article[^>]*>(.*?)</article>',
            html,
            flags=re_mod.DOTALL,
        )
        if article_match:
            html = article_match.group(1)
        else:
            # Fallback: find ltx_page or main content div
            for tag in ['ltx_page', 'ltx_document', 'main']:
                match = re_mod.search(
                    rf'<div[^>]*class="[^"]*{tag}[^"]*"[^>]*>(.*?)</div>\s*(?:<footer|<script|$)',
                    html,
                    flags=re_mod.DOTALL,
                )
                if match:
                    html = match.group(1)
                    break

        # Remove script and style tags
        html = re_mod.sub(r'<script[^>]*>.*?</script>', '', html, flags=re_mod.DOTALL)
        html = re_mod.sub(r'<style[^>]*>.*?</style>', '', html, flags=re_mod.DOTALL)
        html = re_mod.sub(r'<nav[^>]*>.*?</nav>', '', html, flags=re_mod.DOTALL)

        try:
            from markdownify import markdownify

            md = markdownify(html, heading_style="ATX", strip=["script", "style", "nav", "footer"])
            # Clean up excessive blank lines
            md = re_mod.sub(r'\n{3,}', '\n\n', md)
            return md.strip()
        except ImportError:
            # Very basic fallback: strip all HTML tags
            text = re_mod.sub(r'<[^>]+>', '', html)
            text = re_mod.sub(r'\n{3,}', '\n\n', text)
            return text.strip()

    def _extract_images(self, output_dir: Path) -> list[ImageInfo]:
        """Extract image info from parsed output directory."""
        images_dir = output_dir / "images"
        if not images_dir.exists():
            return []

        result = []
        for img_file in sorted(images_dir.iterdir()):
            if img_file.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".svg"):
                result.append(
                    ImageInfo(
                        path=str(img_file),
                        caption=img_file.stem,
                    )
                )
        return result

    def _extract_tables(self, markdown: str) -> list[TableInfo]:
        """Extract markdown tables from content."""
        tables = []
        # Match markdown table blocks (lines starting with |)
        table_pattern = re.compile(r"((?:^\|.+\|$\n?)+)", re.MULTILINE)
        for match in table_pattern.finditer(markdown):
            table_md = match.group(1).strip()
            if "|" in table_md and len(table_md.split("\n")) >= 2:
                tables.append(TableInfo(markdown=table_md))
        return tables

    def _extract_formulas(self, markdown: str) -> list[str]:
        """Extract LaTeX formulas from content."""
        formulas = []
        # Display math: $$ ... $$ or \[ ... \]
        for m in re.finditer(r"\$\$(.*?)\$\$", markdown, re.DOTALL):
            formulas.append(m.group(1).strip())
        for m in re.finditer(r"\\\[(.*?)\\\]", markdown, re.DOTALL):
            formulas.append(m.group(1).strip())
        return formulas

    def _extract_github_urls(self, text: str) -> list[str]:
        """Extract GitHub repository URLs from text."""
        from paper.github_extractor import _GITHUB_NON_REPO_PREFIXES

        # Match both with and without protocol prefix
        pattern = re.compile(r"(?:https?://)?github\.com/([\w\-\.]+)/([\w\-\.]+)")
        urls = set()
        for match in pattern.finditer(text):
            if match.group(1).lower() in _GITHUB_NON_REPO_PREFIXES:
                continue
            url = f"https://github.com/{match.group(1)}/{match.group(2)}"
            url = url.rstrip("/.")
            urls.add(url)
        return list(urls)

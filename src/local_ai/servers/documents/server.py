"""Documents MCP server: the model writes Markdown (formulas in LaTeX, tables, code) and gets a PDF or Word file the
user can download, in any chat ("give it to me as a PDF").

Tools: make_pdf(markdown, title, filename), make_docx(markdown, title, filename)
Files go to <workspace>/outputs/ (the web UI serves them under /files/outputs/ and shows a download link).
Needs pandoc (https://pandoc.org; on Windows: winget install JohnMacFarlane.Pandoc), and for PDF a LaTeX engine
(xelatex, lualatex, pdflatex or tectonic).

Run:  local-ai-documents   (or: python -m local_ai.servers.documents.server)
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from local_ai.servers.common import FastMCP, slugify, workspace_root
from local_ai.servers.export import run_pandoc

mcp = FastMCP("documents")


def outputs_dir() -> Path:
    folder = workspace_root() / "outputs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def make(markdown: str, title: str, filename: str, fmt: str) -> str:
    """Write the document and return the line the user (and the web UI) reads: "Saved PDF: <path>"."""
    if not markdown.strip():
        raise ValueError("Give the content of the document as Markdown.")
    folder = outputs_dir()
    base = slugify(filename or title or "document")
    out, n = folder / f"{base}.{fmt}", 2
    while out.exists():  # an earlier document keeps its name
        out, n = folder / f"{base}-{n}.{fmt}", n + 1
    src = out.with_suffix(".md")
    front = [f"title: {json.dumps(title, ensure_ascii=False)}"] if title.strip() else []
    front.append(f"date: {datetime.date.today().isoformat()}")
    src.write_text("---\n" + "\n".join(front) + "\n---\n\n" + markdown.strip() + "\n", encoding="utf-8")
    run_pandoc(src, out, fmt, folder, [])
    return f"Saved {fmt.upper()}: {out}"


@mcp.tool()
def make_pdf(markdown: str, title: str = "", filename: str = "", also_docx: bool = False) -> str:
    """Make a PDF file of a text for the user to download. Use it when the user asks for a PDF.

    markdown: the whole content in Markdown: headings (#, ##), lists, tables, code blocks, formulas in LaTeX
              ($...$ inline, $$...$$ on their own line). Write the full text here, not a summary of it.
    title: shown at the top of the document. filename: a short name for the file (without extension).
    also_docx: true when the user wants the same document as a Word (.docx) file too.
    Returns "Saved PDF: <path>" (and "Saved DOCX: <path>"); tell the user the file is ready (the chat shows a
    download link).
    """
    saved = [make(markdown, title, filename, "pdf")]
    if also_docx:
        saved.append(make(markdown, title, filename, "docx"))
    return "\n".join(saved)


@mcp.tool()
def make_docx(markdown: str, title: str = "", filename: str = "") -> str:
    """Make a Word (.docx) file of a text for the user to download. Use it when the user asks for a Word file.

    markdown: the whole content in Markdown (headings, lists, tables, code, LaTeX formulas become Word equations).
    title: shown at the top. filename: a short name for the file (without extension).
    Returns "Saved DOCX: <path>"; tell the user the file is ready (the chat shows a download link).
    """
    return make(markdown, title, filename, "docx")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

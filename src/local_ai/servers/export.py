"""Export a research project's report: md, html (self-contained), bib, and tex/docx/pdf via pandoc."""

from __future__ import annotations

import base64
import html
import json
import mimetypes
import re
import shutil
import subprocess
from pathlib import Path

from local_ai.servers.bibtex import format_entry, make_key

FORMATS = ("md", "html", "bib", "tex", "docx", "pdf")
REFERENCES_RE = re.compile(r"\n#{1,6}\s*(References|Kaynakça|Kaynaklar)\s*\n.*", re.DOTALL)
CITE_RE = re.compile(r"\[(\d+(?:\s*[,–-]\s*\d+)*)\]")
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")


class ExportError(RuntimeError):
    pass


def load_project(project_dir: Path) -> tuple[str, list[dict]]:
    report = project_dir / "report.md"
    if not report.is_file():
        raise ExportError(f"No report in {project_dir}; finish a research run first.")
    sources_path = project_dir / "sources.json"
    sources = json.loads(sources_path.read_text(encoding="utf-8")) if sources_path.is_file() else []
    return report.read_text(encoding="utf-8"), sources


def _cited_numbers(marker: str) -> list[int]:
    numbers: list[int] = []
    for part in re.split(r"\s*,\s*", marker):
        if re.fullmatch(r"\d+\s*[–-]\s*\d+", part):
            a, b = (int(x) for x in re.split(r"\s*[–-]\s*", part))
            numbers += list(range(a, b + 1))
        elif part.strip().isdigit():
            numbers.append(int(part))
    return numbers


# -- BibTeX ------------------------------------------------------------------------


def bibtex(sources: list[dict]) -> tuple[str, dict[int, str]]:
    """BibTeX for the registered sources and a map citation number -> key."""
    entries, keys, used = [], {}, set()
    for src in sources:
        key = src.get("citekey") or make_key(src.get("authors", ""), src.get("year"), src["title"])
        base, i = key, 2
        while key in used:
            key, i = f"{base}{chr(ord('a') + i - 2)}", i + 1
        used.add(key)
        keys[src["n"]] = key
        fields = {
            "title": src["title"],
            "author": " and ".join(a.strip() for a in src.get("authors", "").split(";")) if src.get("authors") else "",
            "year": src.get("year", ""),
            "doi": src.get("doi", ""),
        }
        origin = src.get("origin", "other")
        locator = src.get("locator", "")
        if origin == "arxiv":
            arxiv_id = re.sub(r"^arxiv:", "", src["source_id"], flags=re.I)
            entry_type = "article"
            fields.update(eprint=arxiv_id, archiveprefix="arXiv", url=f"https://arxiv.org/abs/{arxiv_id}")
        elif origin == "pdf":
            entry_type = "book"
            fields["note"] = locator
        else:
            entry_type = "misc"
            fields["howpublished"] = {
                "wikipedia": "Wikipedia (offline)",
                "zim": "Offline archive (ZIM)",
                "python": "Computation",
            }.get(origin, origin)
            fields["note"] = locator
        entries.append(format_entry(entry_type, key, fields))
    return "\n\n".join(entries) + ("\n" if entries else ""), keys


# -- HTML --------------------------------------------------------------------------

CSS = """
:root{--bg:#fff;--fg:#1c1c1c;--muted:#666;--accent:#2457c5;--border:#ddd;--code:#f5f5f5}
@media (prefers-color-scheme:dark){:root{--bg:#161616;--fg:#e8e8e8;--muted:#999;--accent:#7ea6ff;--border:#333;--code:#222}}
body{background:var(--bg);color:var(--fg);font:16px/1.6 system-ui,sans-serif;max-width:860px;margin:0 auto;padding:24px 16px}
a{color:var(--accent)} img{max-width:100%} pre,code{background:var(--code);border-radius:4px}
pre{padding:12px;overflow-x:auto} table{border-collapse:collapse} td,th{border:1px solid var(--border);padding:4px 8px}
.cite{text-decoration:none;font-size:.85em} .refs li{margin:.3em 0} .muted{color:var(--muted)}
"""


def _embed_images(markdown: str, base: Path) -> str:
    def repl(m: re.Match) -> str:
        alt, src = m.group(1), m.group(2)
        path = Path(src) if Path(src).is_absolute() else base / src
        if src.startswith(("data:", "http:", "https:")) or not path.is_file():
            return m.group(0)
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        return f"![{alt}](data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()})"

    return IMAGE_RE.sub(repl, markdown)


def render_markdown(markdown: str) -> str:
    """Markdown -> HTML fragment (raw HTML in the input is escaped)."""
    from markdown_it import MarkdownIt

    return MarkdownIt("commonmark", {"html": False}).enable("table").render(markdown)


def render_body(markdown: str, sources: list[dict], base: Path | None = None) -> tuple[str, str]:
    """Report body and references list as HTML fragments, with [n] linked to the references."""
    body_md = REFERENCES_RE.sub("", markdown).rstrip()
    body_md = _embed_images(body_md, base or Path("."))
    body = render_markdown(body_md)
    by_n = {s["n"]: s for s in sources}

    def cite(m: re.Match) -> str:
        links = []
        for n in _cited_numbers(m.group(1)):
            src = by_n.get(n)
            tip = html.escape(
                f"{src['title']} — {src['quotes'][0]}" if src and src.get("quotes") else (src or {}).get("title", "")
            )
            links.append(f'<a class="cite" href="#ref-{n}" title="{tip}">{n}</a>')
        return "[" + ", ".join(links) + "]"

    body = CITE_RE.sub(cite, body)
    refs = []
    for s in sources:
        who = f"{html.escape(s['authors'])} ({html.escape(s.get('year') or 'n.d.')}). " if s.get("authors") else ""
        loc = f" — {html.escape(s['locator'])}" if s.get("locator") else ""
        doi = (
            f' <a href="https://doi.org/{html.escape(s["doi"])}">doi:{html.escape(s["doi"])}</a>'
            if s.get("doi")
            else ""
        )
        refs.append(
            f'<li id="ref-{s["n"]}">[{s["n"]}] {who}<em>{html.escape(s["title"])}</em> '
            f'<span class="muted">({html.escape(s["origin"])}{loc})</span>{doi}</li>'
        )
    refs_html = (
        f'<h2>References</h2><ol class="refs" style="list-style:none;padding:0">{"".join(refs)}</ol>' if refs else ""
    )
    return body, refs_html


def render_html(markdown: str, sources: list[dict], title: str = "Research report", base: Path | None = None) -> str:
    body, refs_html = render_body(markdown, sources, base)
    return (
        f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>{body}{refs_html}</body></html>"
    )


# -- pandoc ------------------------------------------------------------------------


def _pandoc_markdown(markdown: str, keys: dict[int, str]) -> str:
    body = REFERENCES_RE.sub("", markdown).rstrip()

    def cite(m: re.Match) -> str:
        found = [keys[n] for n in _cited_numbers(m.group(1)) if n in keys]
        return "[" + "; ".join(f"@{k}" for k in found) + "]" if found else m.group(0)

    return CITE_RE.sub(cite, body) + "\n\n# References\n"


def _pdf_engine() -> str | None:
    for engine in ("xelatex", "lualatex", "pdflatex", "tectonic"):
        if shutil.which(engine):
            return engine
    return None


def export(project_dir: Path, fmt: str, title: str | None = None) -> Path:
    fmt = fmt.lower().strip(".")
    if fmt not in FORMATS:
        raise ExportError(f"Unknown format {fmt!r}; choose one of {', '.join(FORMATS)}.")
    markdown, sources = load_project(project_dir)
    title = title or project_dir.name
    out_dir = project_dir / "exports"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"report.{fmt}"
    if fmt == "md":
        out.write_text(markdown, encoding="utf-8")
        return out
    bib, keys = bibtex(sources)
    if fmt == "bib":
        out.write_text(bib, encoding="utf-8")
        return out
    if fmt == "html":
        out.write_text(render_html(markdown, sources, title, project_dir), encoding="utf-8")
        return out

    pandoc = shutil.which("pandoc")
    if not pandoc:
        raise ExportError("pandoc is not installed (https://pandoc.org); html, md and bib work without it.")
    bib_path = out_dir / "references.bib"
    bib_path.write_text(bib, encoding="utf-8")
    src = out_dir / "report.pandoc.md"
    src.write_text(f'---\ntitle: "{title}"\n---\n\n' + _pandoc_markdown(markdown, keys), encoding="utf-8")
    cmd = [
        pandoc,
        str(src),
        "-o",
        str(out),
        "--citeproc",
        "--bibliography",
        str(bib_path),
        "--resource-path",
        str(project_dir),
    ]
    if fmt == "tex":
        cmd.append("--standalone")
    if fmt == "pdf":
        engine = _pdf_engine()
        if not engine:
            raise ExportError(
                "PDF export needs a LaTeX engine (xelatex, lualatex, pdflatex or tectonic). "
                "Export html and print it to PDF, or install TeX Live / tectonic."
            )
        cmd += ["--pdf-engine", engine]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise ExportError(f"pandoc failed: {result.stderr.strip()[-2000:]}")
    return out

"""The document of a research run in document mode, as Markdown for pandoc (.docx, .pdf): a chapter per
sub-question, the report as the synthesis, then the numbered sources on a page of their own. The [n] citations stay
as they are, so the numbers match the web UI."""

from __future__ import annotations

import datetime
import json
import re

from local_ai.servers.export import REFERENCES_RE

HEADING_RE = re.compile(r"^(#{1,6})([ \t])", re.MULTILINE)
FENCE_RE = re.compile(r"(```.*?```)", re.DOTALL)
TEXTS = {
    "tr": {"findings": "Bölüm I — Araştırma bulguları", "synthesis": "Bölüm II — Sentez", "references": "Kaynakça",
           "toc": "İçindekiler", "lang": "tr-TR"},
    "en": {"findings": "Part I — Findings", "synthesis": "Part II — Synthesis", "references": "References",
           "toc": "Contents", "lang": "en-US"},
}
# Each format keeps its own raw block and drops the other one.
PAGE_BREAK = '```{=openxml}\n<w:p><w:r><w:br w:type="page"/></w:r></w:p>\n```\n\n```{=latex}\n\\newpage\n```'


def shift_headings(markdown: str, top: int = 2) -> str:
    """The headings moved so that the highest one is at level `top`; code blocks are left alone."""
    parts = FENCE_RE.split(markdown)  # odd items are code blocks
    levels = [len(m.group(1)) for p in parts[::2] for m in HEADING_RE.finditer(p)]
    if not levels:
        return markdown
    delta = top - min(levels)

    def move(m: re.Match) -> str:
        return "#" * max(1, min(6, len(m.group(1)) + delta)) + m.group(2)

    return "".join(p if i % 2 else HEADING_RE.sub(move, p) for i, p in enumerate(parts))


def references_markdown(sources: list[dict], heading: str) -> str:
    lines = [f"# {heading}"]
    for s in sources:
        who = f"{s['authors']} ({s.get('year') or 'n.d.'}). " if s.get("authors") else ""
        where = f"{s['origin']} — {s['locator']}" if s.get("locator") else s["origin"]
        doi = f" doi:{s['doi']}" if s.get("doi") else ""
        lines.append(f"[{s['n']}] {who}*{s['title']}* ({where}){doi}")
    return "\n\n".join(lines)


def build(question: str, language: str, chapters: list[str], synthesis: str, sources: list[dict]) -> str:
    texts = TEXTS["tr" if language.lower().startswith("tr") else "en"]
    front = "\n".join(
        [
            "---",
            f"title: {json.dumps(question, ensure_ascii=False)}",
            f"date: {datetime.date.today().isoformat()}",
            f"lang: {texts['lang']}",
            f"toc-title: {json.dumps(texts['toc'], ensure_ascii=False)}",
            "---",
        ]
    )
    parts = [front]
    if chapters:
        parts += [f"# {texts['findings']}", *(shift_headings(c.strip()) for c in chapters)]
    parts += [f"# {texts['synthesis']}", shift_headings(REFERENCES_RE.sub("", synthesis).strip())]
    if sources:
        parts += [PAGE_BREAK, references_markdown(sources, texts["references"])]
    return "\n\n".join(parts) + "\n"

"""arXiv online: search the API and read paper PDFs (cached in <workspace>/cache/arxiv)."""

from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET

from local_ai.servers.common import cache_dir, http_get, truncate


def pdf_pages(path, page: int, pages: int) -> tuple[int, list[str]]:
    """(page count, ["--- page n ---\\n<text>", ...]) for pages page .. page + pages - 1 of a PDF."""
    import pymupdf

    with pymupdf.open(path) as doc:
        last = min(page + pages - 1, doc.page_count)
        return doc.page_count, [f"--- page {n} ---\n{doc[n - 1].get_text()}" for n in range(max(page, 1), last + 1)]


ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def search_arxiv(query: str, max_results: int = 5, newest_first: bool = False) -> str:
    """Search arXiv for papers (title, authors, date, abstract, id). Use newest_first=True for recent results.
    Example queries: 'gravitational wave memory', 'cat:quant-ph AND ti:entanglement'."""
    # Without a field prefix the terms are ANDed (the arXiv API matches plain multi-word queries loosely)
    q = query if re.search(r"\b(ti|au|abs|cat|all):", query) else " AND ".join(f"all:{t}" for t in query.split())
    params = urllib.parse.urlencode(
        {
            "search_query": q,
            "start": 0,
            "max_results": max(1, min(max_results, 20)),
            "sortBy": "submittedDate" if newest_first else "relevance",
            "sortOrder": "descending",
        }
    )
    root = ET.fromstring(http_get(f"https://export.arxiv.org/api/query?{params}"))
    out = []
    for e in root.findall("a:entry", ATOM):
        arxiv_id = e.findtext("a:id", "", ATOM).rsplit("/abs/", 1)[-1]
        authors = [a.findtext("a:name", "", ATOM) for a in e.findall("a:author", ATOM)]
        cats = [c.get("term") for c in e.findall("a:category", ATOM)]
        out.append(
            f"{arxiv_id} | {e.findtext('a:published', '', ATOM)[:10]} | {', '.join(cats[:3])}\n"
            f"{' '.join(e.findtext('a:title', '', ATOM).split())}\n"
            f"{', '.join(authors[:5])}{' et al.' if len(authors) > 5 else ''}\n"
            f"{' '.join(e.findtext('a:summary', '', ATOM).split())}"
        )
    return truncate("\n\n".join(out)) if out else "No results."


def read_arxiv(arxiv_id: str, page: int = 1, pages: int = 3) -> str:
    """Read pages of an arXiv paper's PDF as text (e.g. arxiv_id='2401.01234'). Start with page 1 for the
    abstract and introduction; read further pages as needed."""
    arxiv_id = arxiv_id.strip().removeprefix("arXiv:")
    if not re.fullmatch(r"[\w.\-/]+", arxiv_id):
        return f"Invalid arXiv id: {arxiv_id}"
    path = cache_dir("arxiv") / (arxiv_id.replace("/", "_") + ".pdf")
    if not path.exists():
        path.write_bytes(http_get(f"https://arxiv.org/pdf/{arxiv_id}", timeout=60))
    total, selected = pdf_pages(path, page, pages)
    return truncate(f"arXiv:{arxiv_id} ({total} pages)\n\n" + "\n\n".join(selected))

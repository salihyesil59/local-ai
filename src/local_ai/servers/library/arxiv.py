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


def extract_arxiv_id(raw: str) -> str:
    s = raw.strip()
    m = re.search(r"(\d{4}\.\d{4,5}(?:v\d+)?|[a-z\-]+(?:\.[a-z\-]+)?/\d{7}(?:v\d+)?)", s, re.IGNORECASE)
    if m:
        return m.group(1)
    return s.removeprefix("arXiv:").removeprefix("arxiv:").strip()


def read_arxiv(arxiv_id: str, page: int = 1, pages: int = 4, query: str = "") -> str:
    """Read pages of an arXiv paper's PDF as text. Accepts an arXiv ID (e.g. '2401.01234') or full arXiv URL.
    page/pages: 1-based page range to read.
    query: optional search term inside the paper (e.g. 'algorithm', 'pseudocode', 'definition');
           if provided, returns pages containing the query."""
    aid = extract_arxiv_id(arxiv_id)
    if not re.fullmatch(r"[\w.\-/]+", aid):
        return f"Invalid arXiv id: {arxiv_id}"
    path = cache_dir("arxiv") / (aid.replace("/", "_") + ".pdf")
    if not path.exists():
        path.write_bytes(http_get(f"https://arxiv.org/pdf/{aid}", timeout=60))

    if query.strip():
        q = query.strip().lower()
        import pymupdf

        with pymupdf.open(path) as doc:
            matching = [n for n in range(1, doc.page_count + 1) if q in doc[n - 1].get_text().lower()]
            if matching:
                selected_pages = matching[:pages]
                pages_text = [f"--- page {n} ---\n{doc[n - 1].get_text()}" for n in selected_pages]
                return truncate(
                    f"arXiv:{aid} ({doc.page_count} pages, found '{query}' on pages {matching}):\n\n"
                    + "\n\n".join(pages_text)
                )
            return f"arXiv:{aid} ({doc.page_count} pages): No pages found containing '{query}'."

    total, selected = pdf_pages(path, page, pages)
    return truncate(f"arXiv:{aid} ({total} pages)\n\n" + "\n\n".join(selected))

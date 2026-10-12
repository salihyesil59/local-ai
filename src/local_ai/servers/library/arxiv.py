"""Papers: search the arXiv API, read arXiv PDFs (cached in <workspace>/cache/arxiv) and local PDF files."""

from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

from local_ai.servers.common import MAX_OUTPUT, cache_dir, http_get, library_dir, truncate

PAGE_BUDGET = MAX_OUTPUT - 400  # characters of whole pages in one reply (the rest is the header and a note)


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
    page/pages: 1-based page range to read. Whole pages are returned, about two or three per call; when more
    were asked for, the reply ends with the page to go on with.
    query: optional search term inside the paper (e.g. 'algorithm', 'pseudocode', 'definition');
           if provided, returns pages containing the query."""
    aid = extract_arxiv_id(arxiv_id)
    if not re.fullmatch(r"[\w.\-/]+", aid):
        return f"Invalid arXiv id: {arxiv_id}"
    path = cache_dir("arxiv") / (aid.replace("/", "_") + ".pdf")
    if not path.exists():
        path.write_bytes(http_get(f"https://arxiv.org/pdf/{aid}", timeout=60))

    return read_pages(path, f"arXiv:{aid}", page, pages, query)


def read_pages(path, label: str, page: int, pages: int, query: str = "") -> str:
    """Pages page .. page + pages - 1 of a PDF as text, or with a query the first pages that contain it.
    Only whole pages are returned: when the pages asked for are longer than one reply, the reply ends with the
    page to go on with."""
    import pymupdf

    with pymupdf.open(path) as doc:
        total = doc.page_count
        if query.strip():
            q = query.strip().lower()
            matching = [n for n in range(1, total + 1) if q in doc[n - 1].get_text().lower()]
            if not matching:
                return f"{label} ({total} pages): No pages found containing '{query}'."
            head = f"{label} ({total} pages, found '{query}' on pages {matching}):"
            wanted = matching[:pages]
        else:
            head = f"{label} ({total} pages)"
            wanted = list(range(max(page, 1), min(page + pages - 1, total) + 1))
        shown, used = [], len(head)
        for n in wanted:
            text = f"--- page {n} ---\n{doc[n - 1].get_text()}"
            if shown and used + len(text) > PAGE_BUDGET:
                break
            shown.append(text)
            used += len(text) + 2
    rest = wanted[len(shown) :]
    more = (
        f"\n\n[Pages {', '.join(map(str, rest))} are not shown: they did not fit into this reply. "
        f"Go on with page={rest[0]}.]"
        if rest
        else ""
    )
    return truncate(head + "\n\n" + "\n\n".join(shown), MAX_OUTPUT - len(more)) + more


def read_pdf(path: str, page: int = 1, pages: int = 2, query: str = "") -> str:
    """Read pages of a PDF file on this computer as text: a paper the user points to that is not in the library.
    path: absolute path of the .pdf file (a relative path is looked up in the library folder).
    page/pages: 1-based page range to read. The first line shows the page count. Whole pages are returned, about
    two or three per call; when more were asked for, the reply ends with the page to go on with.
    query: optional search term inside the file (e.g. 'algorithm', 'pseudocode'); returns the pages containing it."""
    file = Path(path.strip().strip('"')).expanduser()
    if not file.is_absolute():
        file = library_dir() / file
    if file.suffix.lower() != ".pdf" or not file.is_file():
        return f"Error: no PDF file at {file}. Pass the absolute path of a .pdf file."
    try:
        return read_pages(file, file.name, page, max(1, min(pages, 6)), query)
    except Exception as e:  # damaged or password-protected file
        return f"Error: cannot read {file.name}: {e}"

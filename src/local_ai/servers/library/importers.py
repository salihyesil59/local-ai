"""Read reference collections into uniform records for the library index.

Record fields: uid, title, authors (list), year, doi, url, citekey, abstract, pdf (Path | None), kind.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path

from local_ai.servers.bibtex import clean_latex, make_key, parse_bibtex, pdf_paths_from_file_field, split_authors


def _year(text: str | None) -> str:
    m = re.search(r"(1[5-9]\d\d|20\d\d)", text or "")
    return m.group(1) if m else ""


def from_bibtex(path: str | Path) -> list[dict]:
    path = Path(path).expanduser().resolve()
    records = []
    for e in parse_bibtex(path.read_text(encoding="utf-8", errors="replace")):
        pdf = None
        for p in pdf_paths_from_file_field(e.get("file", "")):
            candidate = Path(p).expanduser()
            if not candidate.is_absolute():
                candidate = path.parent / candidate
            if candidate.is_file():
                pdf = candidate
                break
        url = e.get("url", "")
        if not url and e.get("eprint") and e.get("archiveprefix", "").lower() == "arxiv":
            url = f"https://arxiv.org/abs/{e['eprint']}"
        records.append(
            {
                "uid": f"bib:{e['ID']}",
                "title": clean_latex(e.get("title", e["ID"])),
                "authors": split_authors(e.get("author", e.get("editor", ""))),
                "year": _year(e.get("year") or e.get("date")),
                "doi": e.get("doi", ""),
                "url": url,
                "citekey": e["ID"],
                "abstract": clean_latex(e.get("abstract", "")),
                "pdf": pdf,
                "kind": "bib",
            }
        )
    return records


def from_zotero(data_dir: str | Path) -> list[dict]:
    """Read a Zotero data directory (with zotero.sqlite and storage/). The database is copied first
    because Zotero keeps the live file locked while running."""
    data_dir = Path(data_dir).expanduser().resolve()
    db = data_dir / "zotero.sqlite"
    if not db.is_file():
        raise ValueError(f"No zotero.sqlite in {data_dir}")
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "zotero.sqlite"
        shutil.copy2(db, copy)
        conn = sqlite3.connect(copy)
        conn.row_factory = sqlite3.Row
        try:
            return _zotero_records(conn, data_dir)
        finally:
            conn.close()


def _zotero_records(conn: sqlite3.Connection, data_dir: Path) -> list[dict]:
    deleted = set()
    if conn.execute("SELECT name FROM sqlite_master WHERE name = 'deletedItems'").fetchone():
        deleted = {r[0] for r in conn.execute("SELECT itemID FROM deletedItems")}

    def fields(item_id: int) -> dict[str, str]:
        rows = conn.execute(
            """SELECT f.fieldName, v.value FROM itemData d
               JOIN fields f ON f.fieldID = d.fieldID JOIN itemDataValues v ON v.valueID = d.valueID
               WHERE d.itemID = ?""",
            (item_id,),
        )
        return {r["fieldName"]: str(r["value"]) for r in rows}

    def creators(item_id: int) -> list[str]:
        rows = conn.execute(
            """SELECT c.firstName, c.lastName FROM itemCreators ic JOIN creators c ON c.creatorID = ic.creatorID
               WHERE ic.itemID = ? ORDER BY ic.orderIndex""",
            (item_id,),
        )
        return [f"{r['lastName']}, {r['firstName']}".strip(", ") for r in rows]

    records = []
    attachments = conn.execute(
        """SELECT a.itemID, a.parentItemID, a.path, i.key FROM itemAttachments a
           JOIN items i ON i.itemID = a.itemID
           WHERE a.contentType = 'application/pdf' AND a.path IS NOT NULL"""
    ).fetchall()
    for att in attachments:
        if att["itemID"] in deleted or att["parentItemID"] in deleted:
            continue
        raw = att["path"]
        if raw.startswith("storage:"):
            pdf = data_dir / "storage" / att["key"] / raw[len("storage:") :]
        else:  # linked file (absolute path, or relative to a base dir we can't know)
            pdf = Path(raw.replace("attachments:", "")).expanduser()
        meta_id = att["parentItemID"] or att["itemID"]
        meta = fields(meta_id)
        authors = creators(meta_id)
        year = _year(meta.get("date"))
        title = meta.get("title") or pdf.stem
        records.append(
            {
                "uid": f"zotero:{att['key']}",
                "title": title,
                "authors": authors,
                "year": year,
                "doi": meta.get("DOI", ""),
                "url": meta.get("url", ""),
                "citekey": make_key(authors, year, title),
                "abstract": meta.get("abstractNote", ""),
                "pdf": pdf if pdf.is_file() else None,
                "kind": "bib",
            }
        )
    return records


def from_arxiv_jsonl(path: str | Path, categories: list[str] | None = None, since: str = "") -> list[dict]:
    """Records from an arXiv metadata JSONL (as written by `local-ai data arxiv harvest`, or the
    Kaggle arXiv snapshot format)."""
    records = []
    with Path(path).expanduser().open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            cats = d.get("categories", "")
            cats = cats.split() if isinstance(cats, str) else list(cats)
            if categories and not any(c == want or c.startswith(want + ".") for c in cats for want in categories):
                continue
            date = d.get("published") or d.get("update_date") or d.get("created") or ""
            if since and date and date[:10] < since:
                continue
            authors = d.get("authors", [])
            if isinstance(authors, str):
                authors = split_authors(authors.replace(", and ", " and ").replace(", ", " and "))
            arxiv_id = str(d["id"])
            year = _year(date) or _year(arxiv_id[:2] and f"20{arxiv_id[:2]}")
            title = clean_latex(d.get("title", arxiv_id))
            records.append(
                {
                    "uid": f"arxiv:{arxiv_id}",
                    "title": title,
                    "authors": authors,
                    "year": year,
                    "doi": d.get("doi") or "",
                    "url": f"https://arxiv.org/abs/{arxiv_id}",
                    "citekey": make_key(authors, year, title),
                    "abstract": clean_latex(d.get("abstract", "")),
                    "pdf": None,
                    "kind": "arxiv",
                    "arxiv_id": arxiv_id,
                }
            )
    return records

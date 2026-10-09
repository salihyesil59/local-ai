"""Library MCP server: everything the model can look up.

Tools:
  search_library  passages from your library (LOCAL_AI_LIBRARY), offline arXiv abstracts and, on request, ZIM archives
  read_library    pages of a library file, or a search hit with its neighbours
  wikipedia     a Wikipedia article (or one section) from the offline archives, online as fallback
  docs          offline programming documentation (DevDocs archives)
  search_arxiv  arXiv online search;  read_arxiv  pages of an arXiv paper

Search index (SQLite in <workspace>/library.sqlite): text chunked per page with an FTS5 (BM25) index, plus
embeddings from LM Studio when an embedding model is available (LOCAL_AI_EMBED_BACKEND=none turns them off).
LOCAL_AI_LIBRARY is indexed automatically: changed files are picked up on the next search (a large first index is
spread over several searches; `local-ai-index` does it in one go). Zotero, BibTeX and arXiv metadata are added with
`local-ai-index`. ZIM archives are too large to index fully: a search with sources=zim fetches candidate articles
through each archive's own full-text index and caches their sections. BM25 and embedding rankings are merged with
reciprocal-rank fusion, then optionally re-ranked with a cross-encoder (LOCAL_AI_RERANK_MODEL).

Environment:
  LOCAL_AI_WORKSPACE         workspace root (default ~/local-ai-workspace)
  LOCAL_AI_LIBRARY           your PDF / .md / .txt files (default ~/physics-library)
  LOCAL_AI_ZIM_DIR           ZIM folders and/or files, separated by os.pathsep (default ~/wiki-zim)
  LOCAL_AI_BASE_URL          LM Studio server (default http://localhost:1234/v1)
  LOCAL_AI_EMBED_MODEL       embedding model id in LM Studio (default text-embedding-qwen3-embedding-0.6b)
  LOCAL_AI_EMBED_QUERY_TASK  instruction put before queries (default: one for Qwen3-Embedding models)
  LOCAL_AI_RERANK_MODEL      optional cross-encoder, e.g. BAAI/bge-reranker-v2-m3 (local path or cache)

Vectors are only comparable within one embedding model: the model is recorded in the database, and after a
model change semantic ranking is off (keyword search still works) until `local-ai-index --reembed`.

Run:  local-ai-library            (MCP server, stdio)
      local-ai-index              (index LOCAL_AI_LIBRARY now; see --help for Zotero, BibTeX, arXiv)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

import numpy as np

from local_ai.servers.common import (
    FastMCP,
    connect,
    from_blob,
    fts_query,
    library_dir,
    make_embedder,
    rrf,
    set_embedding_model,
    stored_embedding_model,
    to_blob,
    truncate,
    workspace_root,
)
from local_ai.servers.common import (
    zim_paths as configured_zim_paths,
)
from local_ai.servers.library import arxiv, importers, zim
from local_ai.servers.library.zim import html_sections

CHUNK_CHARS = 2000
CHUNK_OVERLAP = 200
TEXT_SUFFIXES = {".pdf", ".md", ".txt"}
META_COLUMNS = ("authors", "year", "doi", "url", "citekey")
# `sources` names accepted by search -> doc kinds ("pdf" is the old name of "library")
LIBRARY_KINDS = {"pdf", "text", "bib"}
SOURCE_KINDS = {"library": LIBRARY_KINDS, "pdf": LIBRARY_KINDS, "zim": {"zim"}, "arxiv": {"arxiv"}}
AUTO_INDEX_EVERY = 30  # seconds between checks of LOCAL_AI_LIBRARY for changed files
AUTO_INDEX_BUDGET = 20  # seconds of indexing per search; a large first index continues on later searches

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL,              -- pdf | text | zim
    title TEXT,
    mtime REAL, size INTEGER,
    indexed_at REAL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    doc_id INTEGER NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    ord INTEGER NOT NULL,
    locator TEXT,
    text TEXT NOT NULL,
    embedding BLOB
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id, ord);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, tokenize='unicode61 remove_diacritics 2');
"""


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks, preferring paragraph/sentence boundaries."""
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) <= size:
        return [text] if text else []
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = max(text.rfind("\n\n", start, end), text.rfind(". ", start, end))
            if cut > start + size // 2:
                end = cut + 1
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


class Library:
    def __init__(self, db_path: Path | None = None, zim_paths: list[str] | None = None, embedder=None):
        self.db_path = db_path or workspace_root() / "library.sqlite"
        self.conn = connect(self.db_path)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        existing = {r["name"] for r in self.conn.execute("PRAGMA table_info(docs)")}
        for column in META_COLUMNS:
            if column not in existing:
                self.conn.execute(f"ALTER TABLE docs ADD COLUMN {column} TEXT")
        self.embedder = embedder or make_embedder()
        self.stale_model = stored_embedding_model(self.conn, self.embedder)
        if self.stale_model:
            print(
                f"warning: library vectors come from {self.stale_model!r}, not {self.embedder.model!r}; "
                "semantic ranking is off until `local-ai-index --reembed`",
                file=sys.stderr,
            )
        if zim_paths is None:
            zim_paths = [str(p) for p in configured_zim_paths()]
        self.zim_files = self._expand_zims(zim_paths)
        self._archives: dict[str, object] = {}
        self._reranker = None
        self._reranker_tried = False
        self._auto_checked = 0.0
        self.auto_pending = 0  # library files still waiting for the automatic index

    # -- storage -------------------------------------------------------------

    @staticmethod
    def _expand_zims(paths: list[str]) -> list[Path]:
        files = []
        for p in paths:
            p = Path(p).expanduser()
            if p.is_dir():
                files += sorted(p.rglob("*.zim"))
            elif p.suffix == ".zim" and p.is_file():
                files.append(p)
        return files

    def _upsert_doc(self, path: str, kind: str, title: str, mtime=None, size=None, meta: dict | None = None) -> int:
        meta = meta or {}
        values = ["; ".join(meta["authors"]) if isinstance(meta.get("authors"), list) else meta.get("authors")]
        values += [meta.get(c) for c in META_COLUMNS[1:]]
        row = self.conn.execute("SELECT id FROM docs WHERE path = ?", (path,)).fetchone()
        if row:
            self._delete_chunks(row["id"])
            self.conn.execute(
                f"UPDATE docs SET kind=?, title=?, mtime=?, size=?, indexed_at=?, "
                f"{', '.join(f'{c} = COALESCE(?, {c})' for c in META_COLUMNS)} WHERE id=?",
                (kind, title, mtime, size, time.time(), *values, row["id"]),
            )
            return row["id"]
        cur = self.conn.execute(
            f"INSERT INTO docs (path, kind, title, mtime, size, indexed_at, {', '.join(META_COLUMNS)}) "
            f"VALUES (?, ?, ?, ?, ?, ?, {', '.join('?' * len(META_COLUMNS))})",
            (path, kind, title, mtime, size, time.time(), *values),
        )
        return cur.lastrowid

    def _delete_chunks(self, doc_id: int) -> None:
        ids = [r["id"] for r in self.conn.execute("SELECT id FROM chunks WHERE doc_id = ?", (doc_id,))]
        self.conn.executemany("DELETE FROM chunks_fts WHERE rowid = ?", [(i,) for i in ids])
        self.conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))

    def _add_chunks(self, doc_id: int, items: list[tuple[str, str]]) -> int:
        """items: (locator, text). Embeds in batches when an embedder is available."""
        ord_ = 0
        for batch_start in range(0, len(items), 32):
            batch = items[batch_start : batch_start + 32]
            vectors = None if self.stale_model else self.embedder.embed([t for _, t in batch])
            for i, (locator, text) in enumerate(batch):
                blob = to_blob(vectors[i]) if vectors is not None else None
                cur = self.conn.execute(
                    "INSERT INTO chunks (doc_id, ord, locator, text, embedding) VALUES (?, ?, ?, ?, ?)",
                    (doc_id, ord_, locator, text, blob),
                )
                self.conn.execute("INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)", (cur.lastrowid, text))
                ord_ += 1
        self.conn.commit()
        return ord_

    # -- PDFs and text files -------------------------------------------------

    def index_folder(self, folder: str, force: bool = False, budget: float | None = None) -> dict:
        """Index new and changed files under a folder; unchanged files are skipped, deleted ones removed.
        With `budget` (seconds) it stops after the first file that exceeds it and reports the rest as `pending`."""
        root = Path(folder).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"Not a folder: {folder}")
        stats = {"indexed": 0, "unchanged": 0, "failed": 0, "chunks": 0, "removed": 0, "pending": 0}
        started = time.monotonic()
        seen = set()
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in TEXT_SUFFIXES or not path.is_file():
                continue
            seen.add(str(path))
            st = path.stat()
            row = self.conn.execute("SELECT mtime, size FROM docs WHERE path = ?", (str(path),)).fetchone()
            if row and not force and row["mtime"] == st.st_mtime and row["size"] == st.st_size:
                stats["unchanged"] += 1
                continue
            if budget is not None and stats["indexed"] and time.monotonic() - started > budget:
                stats["pending"] += 1
                continue
            try:
                items, title = self._extract(path)
            except Exception as e:  # corrupt or encrypted file
                print(f"skip {path}: {e}", file=sys.stderr)
                stats["failed"] += 1
                continue
            kind = "pdf" if path.suffix.lower() == ".pdf" else "text"
            doc_id = self._upsert_doc(str(path), kind, title, st.st_mtime, st.st_size)
            stats["chunks"] += self._add_chunks(doc_id, items)
            stats["indexed"] += 1
            self.conn.commit()
        # drop files that were deleted from this folder
        for row in self.conn.execute(
            "SELECT id, path FROM docs WHERE kind IN ('pdf', 'text') AND path LIKE ?", (f"{root}{os.sep}%",)
        ).fetchall():
            if row["path"] not in seen:
                self._delete_chunks(row["id"])
                self.conn.execute("DELETE FROM docs WHERE id = ?", (row["id"],))
                stats["removed"] += 1
        self.conn.commit()
        stats["embedded_later"] = self.embed_missing()
        return stats

    def ensure_library(self) -> None:
        """Pick up new and changed files in LOCAL_AI_LIBRARY (checked at most every AUTO_INDEX_EVERY seconds)."""
        root = library_dir()
        if not root.is_dir() or (time.monotonic() - self._auto_checked < AUTO_INDEX_EVERY and not self.auto_pending):
            return
        stats = self.index_folder(str(root), budget=AUTO_INDEX_BUDGET)
        self._auto_checked = time.monotonic()
        self.auto_pending = stats["pending"]

    def read_file(self, file: str, page: int = 1, pages: int = 2) -> str:
        """Pages of a library file: real pages for PDFs, ~3000-character parts for text files. `file` is a path
        relative to LOCAL_AI_LIBRARY or an indexed path, as search shows it; nothing else can be read."""
        root = library_dir().resolve()
        candidate = Path(file).expanduser()
        path = (candidate if candidate.is_absolute() else root / candidate).resolve()
        indexed = self.conn.execute("SELECT 1 FROM docs WHERE path = ?", (str(path),)).fetchone()
        if not path.is_file() or not (root in path.parents or indexed):
            raise FileNotFoundError(file)
        page, pages = max(page, 1), max(1, min(pages, 10))
        if path.suffix.lower() == ".pdf":
            total, selected = arxiv.pdf_pages(path, page, pages)
        else:
            body = path.read_text(encoding="utf-8", errors="replace")
            parts = [body[i : i + 3000] for i in range(0, len(body), 3000)] or [""]
            total = len(parts)
            selected = [f"--- part {n} ---\n{parts[n - 1]}" for n in range(page, min(page + pages, total + 1))]
        unit = "pages" if path.suffix.lower() == ".pdf" else "parts"
        return truncate(f"{display_path(str(path))} ({total} {unit})\n\n" + "\n\n".join(selected))

    def embed_missing(self, batch: int = 32) -> int:
        """Embed chunks indexed while the embedding endpoint was unavailable."""
        done = 0
        while self.embedder.available and not self.stale_model:
            rows = self.conn.execute("SELECT id, text FROM chunks WHERE embedding IS NULL LIMIT ?", (batch,)).fetchall()
            if not rows:
                break
            vectors = self.embedder.embed([r["text"] for r in rows])
            if vectors is None:
                break
            self.conn.executemany(
                "UPDATE chunks SET embedding = ? WHERE id = ?",
                [(to_blob(v), r["id"]) for v, r in zip(vectors, rows, strict=True)],
            )
            self.conn.commit()
            done += len(rows)
        return done

    def reembed(self) -> int:
        """Drop all vectors and embed every chunk again with the current model (after a model change)."""
        if self.embedder.embed(["ping"]) is None:
            raise RuntimeError(f"Embedding model {self.embedder.model!r} is not reachable; load it in LM Studio first.")
        self.conn.execute("UPDATE chunks SET embedding = NULL")
        set_embedding_model(self.conn, self.embedder.model)
        self.stale_model = None
        return self.embed_missing()

    def index_records(self, records: list[dict], force: bool = False) -> dict:
        """Index reference records (from Zotero, BibTeX or arXiv metadata). A record with a PDF is
        indexed in full with its metadata; otherwise its title + abstract are indexed."""
        stats = {"indexed": 0, "unchanged": 0, "failed": 0, "chunks": 0, "abstract_only": 0}
        for rec in records:
            pdf = rec.get("pdf")
            try:
                if pdf:
                    st = pdf.stat()
                    row = self.conn.execute(
                        "SELECT mtime, size, citekey FROM docs WHERE path = ?", (str(pdf),)
                    ).fetchone()
                    if (
                        row
                        and not force
                        and row["mtime"] == st.st_mtime
                        and row["size"] == st.st_size
                        and row["citekey"]
                    ):
                        stats["unchanged"] += 1
                        continue
                    items, _ = self._extract(pdf)
                    doc_id = self._upsert_doc(str(pdf), "pdf", rec["title"], st.st_mtime, st.st_size, rec)
                else:
                    text = "\n\n".join(t for t in (rec["title"], rec.get("abstract", "")) if t)
                    if not text.strip():
                        continue
                    if not force and self.conn.execute("SELECT 1 FROM docs WHERE path = ?", (rec["uid"],)).fetchone():
                        stats["unchanged"] += 1
                        continue
                    items = [("abstract", text)]
                    doc_id = self._upsert_doc(rec["uid"], rec["kind"], rec["title"], meta=rec)
                    stats["abstract_only"] += 1
            except Exception as e:
                print(f"skip {rec.get('uid')}: {e}", file=sys.stderr)
                stats["failed"] += 1
                continue
            stats["chunks"] += self._add_chunks(doc_id, items)
            stats["indexed"] += 1
        self.conn.commit()
        stats["embedded_later"] = self.embed_missing()
        return stats

    @staticmethod
    def _extract(path: Path) -> tuple[list[tuple[str, str]], str]:
        if path.suffix.lower() == ".pdf":
            import pymupdf

            items = []
            with pymupdf.open(path) as doc:
                title = (doc.metadata or {}).get("title") or path.stem
                for page_no, page in enumerate(doc, 1):
                    for chunk in chunk_text(page.get_text()):
                        items.append((f"page {page_no}", chunk))
            return items, title
        text = path.read_text(encoding="utf-8", errors="replace")
        return [(f"part {i}", c) for i, c in enumerate(chunk_text(text), 1)], path.stem

    # -- ZIM -----------------------------------------------------------------

    def _archive(self, path: Path):
        shared = {name: archive for name, _, archive in zim.archives()}
        if path.stem in shared:
            return shared[path.stem]
        from libzim.reader import Archive  # an archive outside LOCAL_AI_ZIM_DIR (tests, explicit zim_paths)

        key = str(path)
        if key not in self._archives:
            self._archives[key] = Archive(path)
        return self._archives[key]

    def _zim_candidates(self, queries: list[str], per_query: int = 10) -> None:
        """Fetch candidate articles via each archive's full-text index and cache their chunks."""
        if not self.zim_files:
            return
        from libzim.search import Query, Searcher

        for zim_path in self.zim_files:
            archive = self._archive(zim_path)
            if not archive.has_fulltext_index:
                continue
            searcher = Searcher(archive)
            for q in queries:
                if not q.strip():
                    continue
                for entry_path in searcher.search(Query().set_query(q)).getResults(0, per_query):
                    key = f"{zim_path}::{entry_path}"
                    if self.conn.execute("SELECT 1 FROM docs WHERE path = ?", (key,)).fetchone():
                        continue
                    try:
                        entry = archive.get_entry_by_path(entry_path)
                        if entry.is_redirect:
                            entry = entry.get_redirect_entry()
                        item = entry.get_item()
                        raw = bytes(item.content).decode("utf-8", errors="replace")
                    except Exception:
                        continue
                    items = []
                    for heading, text in html_sections(raw):
                        for chunk in chunk_text(text):
                            items.append((heading or "intro", chunk))
                    if items:
                        doc_id = self._upsert_doc(key, "zim", entry.title)
                        self._add_chunks(doc_id, items)

    # -- search --------------------------------------------------------------

    def search(self, query: str, k: int = 8, sources: str = "library,arxiv", english_query: str = "") -> list[dict]:
        wanted = {s.strip().lower() for s in sources.split(",") if s.strip()} or {"library", "arxiv"}
        kinds = set().union(*(SOURCE_KINDS.get(w, {w}) for w in wanted))
        if kinds & LIBRARY_KINDS:
            self.ensure_library()
        if "zim" in kinds:
            self._zim_candidates([query, english_query])
        placeholders = ",".join("?" * len(kinds))

        # 1) keyword ranking (BM25)
        bm25_ids: list[int] = []
        match = fts_query(query, english_query)
        if match:
            bm25_ids = [
                r["id"]
                for r in self.conn.execute(
                    f"""SELECT c.id FROM chunks_fts f JOIN chunks c ON c.id = f.rowid
                        JOIN docs d ON d.id = c.doc_id
                        WHERE chunks_fts MATCH ? AND d.kind IN ({placeholders})
                        ORDER BY bm25(chunks_fts) LIMIT 50""",
                    (match, *kinds),
                )
            ]

        # 2) embedding ranking
        dense_ids: list[int] = []
        qtexts = [t for t in (query, english_query) if t.strip()]
        qvecs = None if self.stale_model else self.embedder.embed(qtexts, query=True)
        if qvecs is not None:
            qvec = qvecs.mean(axis=0)
            rows = self.conn.execute(
                f"""SELECT c.id, c.embedding FROM chunks c JOIN docs d ON d.id = c.doc_id
                    WHERE c.embedding IS NOT NULL AND d.kind IN ({placeholders})""",
                tuple(kinds),
            ).fetchall()
            if rows:
                matrix = np.stack([from_blob(r["embedding"]) for r in rows])
                if matrix.shape[1] == qvec.shape[0]:
                    scores = matrix @ qvec
                    top = np.argsort(-scores)[:50]
                    dense_ids = [rows[i]["id"] for i in top]

        ranked = rrf(bm25_ids, dense_ids)[: max(k * 3, 20)]
        hits = [self._chunk(cid) for cid in ranked]
        hits = self._rerank(query if not english_query else f"{query} / {english_query}", hits)
        return hits[:k]

    def _rerank(self, query: str, hits: list[dict]) -> list[dict]:
        model_name = os.environ.get("LOCAL_AI_RERANK_MODEL")
        if not model_name or len(hits) < 2:
            return hits
        if not self._reranker_tried:
            self._reranker_tried = True
            try:
                from sentence_transformers import CrossEncoder

                self._reranker = CrossEncoder(model_name)
            except Exception as e:
                print(f"reranker disabled: {e}", file=sys.stderr)
        if self._reranker is None:
            return hits
        scores = self._reranker.predict([(query, h["text"]) for h in hits])
        return [h for _, h in sorted(zip(scores, hits, strict=True), key=lambda p: -p[0])]

    def _chunk(self, chunk_id: int) -> dict:
        row = self.conn.execute(
            f"""SELECT c.id, c.doc_id, c.ord, c.locator, c.text, d.path, d.kind, d.title, {", ".join("d." + c for c in META_COLUMNS)}
               FROM chunks c JOIN docs d ON d.id = c.doc_id WHERE c.id = ?""",
            (chunk_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown chunk id {chunk_id}")
        return dict(row)

    def read_chunk(self, chunk_id: int, neighbors: int = 1) -> dict:
        hit = self._chunk(chunk_id)
        rows = self.conn.execute(
            "SELECT text FROM chunks WHERE doc_id = ? AND ord BETWEEN ? AND ? ORDER BY ord",
            (hit["doc_id"], hit["ord"] - neighbors, hit["ord"] + neighbors),
        ).fetchall()
        hit["text"] = "\n\n".join(r["text"] for r in rows)
        return hit

    def status(self) -> dict:
        rows = self.conn.execute(
            """SELECT d.kind, COUNT(DISTINCT d.id) AS docs, COUNT(c.id) AS chunks,
                      SUM(c.embedding IS NOT NULL) AS embedded
               FROM docs d LEFT JOIN chunks c ON c.doc_id = d.id GROUP BY d.kind"""
        ).fetchall()
        return {
            "db": str(self.db_path),
            "by_kind": [dict(r) for r in rows],
            "zim_archives": [str(z) for z in self.zim_files],
            "embedding_model": getattr(self.embedder, "model", None),
            "stale_vectors_from": self.stale_model,
            "embeddings_available": self.embedder.embed(["ping"]) is not None,
        }


def display_path(path: str) -> str:
    """Library files relative to LOCAL_AI_LIBRARY (what `read` accepts), other paths unchanged."""
    try:
        return str(Path(path).resolve().relative_to(library_dir().resolve()))
    except ValueError:
        return path


def format_hit(hit: dict, max_chars: int | None = 700) -> str:
    if hit["kind"] == "zim":
        source = Path(hit["path"].split("::")[0]).stem
    elif hit["kind"] in LIBRARY_KINDS:
        source = display_path(hit["path"])
    else:
        source = hit["path"]
    text = hit["text"]
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rstrip() + " …"
    meta = ""
    if hit.get("authors") or hit.get("year"):
        meta = f"by: {hit.get('authors') or 'unknown'} ({hit.get('year') or 'n.d.'})"
        for key in ("doi", "url", "citekey"):
            if hit.get(key):
                meta += f" | {key}: {hit[key]}"
        meta += "\n"
    return f"[chunk {hit['id']}] {hit['kind']} | {hit['title']} | {hit['locator']}\n{meta}source: {source}\n{text}"


def build_server(library: Library | None = None) -> FastMCP:
    lib = library or Library()
    mcp = FastMCP(
        "library",
        instructions=(
            "Everything the model can look up: the user's own library, offline Wikipedia and documentation, arXiv. "
            "Queries may be in any language; pass English key terms in english_query for best recall."
        ),
    )

    @mcp.tool()
    def search_library(query: str, english_query: str = "", sources: str = "library,arxiv", k: int = 8) -> str:
        """Find passages about a topic in the user's own library (books, lecture notes, papers) and in the offline
        arXiv abstracts. Use this before answering from memory.

        query: the question or topic, any language.
        english_query: English key terms (recommended when the query is not English; most sources are English).
        sources: comma-separated subset of library, arxiv, zim. zim searches inside the offline Wikipedia and
        documentation archives (slower); for a known Wikipedia article use the wikipedia tool instead.
        Each hit shows a chunk id, title, page and source; read either with read_library. Authors, year, DOI and
        citekey are shown when known: copy them when you cite.
        """
        hits = lib.search(query, k=max(1, min(k, 20)), sources=sources, english_query=english_query)
        note = (
            f"\n\n(Note: {lib.auto_pending} library files are not indexed yet; they are added on the next searches.)"
            if lib.auto_pending
            else ""
        )
        if not hits:
            return "No matches. Try other terms or an English query." + note
        return "\n\n---\n\n".join(format_hit(h) for h in hits) + note

    @mcp.tool()
    def read_library(source: str | int, page: int = 1, pages: int = 2) -> str:
        """Read more of something search_library found.

        source: a chunk id from a search hit (e.g. 123) to read that passage with the passages around it, or a
        file as shown after "source:" (e.g. "tong/cosmology.pdf") to read its pages page .. page + pages - 1.
        """
        ref = str(source).strip().removeprefix("chunk").strip()
        try:
            if ref.isdigit():
                return format_hit(lib.read_chunk(int(ref), neighbors=1), max_chars=None)
            return lib.read_file(str(source), page, pages)
        except (FileNotFoundError, ValueError):
            return f"Not found: {source}. Pass a chunk id or a source path exactly as search shows it."

    mcp.tool()(zim.wikipedia)
    mcp.tool()(zim.docs)
    mcp.tool()(arxiv.search_arxiv)
    mcp.tool()(arxiv.read_arxiv)
    return mcp


def main() -> None:
    build_server().run()


def index_main(argv: list[str] | None = None) -> None:
    """local-ai-index [folder] [--zotero DIR] [--bib FILE] [--arxiv JSONL] [--force] [--status] [--reembed]

    Without a folder or collection it indexes LOCAL_AI_LIBRARY."""
    parser = argparse.ArgumentParser(
        prog="local-ai-index", description="Index documents and reference collections for semantic search"
    )
    parser.add_argument("folder", nargs="?", help="folder with PDF/.md/.txt files (default: LOCAL_AI_LIBRARY)")
    parser.add_argument("--zotero", metavar="DIR", help="Zotero data folder (contains zotero.sqlite)")
    parser.add_argument("--bib", metavar="FILE", action="append", default=[], help=".bib file (repeatable)")
    parser.add_argument("--arxiv", metavar="JSONL", help="arXiv metadata JSONL (abstracts)")
    parser.add_argument("--categories", help="with --arxiv: comma-separated categories, e.g. hep-th,quant-ph")
    parser.add_argument("--since", default="", help="with --arxiv: only papers from this date (YYYY-MM-DD)")
    parser.add_argument("--force", action="store_true", help="re-index unchanged items too")
    parser.add_argument(
        "--reembed", action="store_true", help="embed everything again with the current model (library and memory)"
    )
    parser.add_argument("--status", action="store_true", help="show index status")
    args = parser.parse_args(argv)
    if args.reembed:
        from local_ai.servers.notebook.memory import Memory

        print("library chunks re-embedded:", Library().reembed())
        print("memories re-embedded:", Memory(workspace_root() / "memory.sqlite").reembed())
        return
    if args.status:
        print(Library().status())
        return
    if not (args.folder or args.zotero or args.bib or args.arxiv):
        args.folder = str(library_dir())
    lib = Library()
    if lib.embedder.embed(["ping"]) is None:
        print("warning: embedding endpoint unavailable; indexing for keyword search only", file=sys.stderr)
    if args.folder:
        print("folder:", lib.index_folder(args.folder, args.force))
    if args.zotero:
        print("zotero:", lib.index_records(importers.from_zotero(args.zotero), args.force))
    for bib in args.bib:
        print(f"bib {bib}:", lib.index_records(importers.from_bibtex(bib), args.force))
    if args.arxiv:
        cats = [c.strip() for c in args.categories.split(",")] if args.categories else None
        records = importers.from_arxiv_jsonl(args.arxiv, cats, args.since)
        print(f"arxiv: {len(records)} records selected")
        print("arxiv:", lib.index_records(records, args.force))


if __name__ == "__main__":
    main()

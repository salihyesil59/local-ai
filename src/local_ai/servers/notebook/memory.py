"""Long-term memory shared by all research projects.

Stores verified facts (with their sources), lessons learned about how to research,
and user preferences. Retrieval is hybrid: BM25 (SQLite FTS5) plus embeddings from
LM Studio when available.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

from local_ai.servers.common import (
    connect,
    from_blob,
    fts_query,
    make_embedder,
    rrf,
    set_embedding_model,
    stored_embedding_model,
    to_blob,
)

KINDS = ("fact", "lesson", "preference")

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    sources TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    project TEXT DEFAULT '',
    created REAL,
    embedding BLOB
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(text, tags, tokenize='unicode61 remove_diacritics 2');
"""


class Memory:
    def __init__(self, path: Path, embedder=None):
        self.conn = connect(path)
        self.conn.executescript(SCHEMA)
        self.embedder = embedder or make_embedder()
        self.stale_model = stored_embedding_model(self.conn, self.embedder)
        if self.stale_model:
            print(
                f"warning: memory vectors come from {self.stale_model!r}, not {self.embedder.model!r}; "
                "semantic recall is off until `local-ai-index --reembed`",
                file=sys.stderr,
            )

    def reembed(self) -> int:
        """Embed every memory again with the current model (after a model change)."""
        rows = self.conn.execute("SELECT id, text FROM memories").fetchall()
        vectors = self.embedder.embed([r["text"] for r in rows]) if rows else None
        if rows and vectors is None:
            raise RuntimeError(f"Embedding model {self.embedder.model!r} is not reachable; load it in LM Studio first.")
        self.conn.executemany(
            "UPDATE memories SET embedding = ? WHERE id = ?",
            [(to_blob(v), r["id"]) for v, r in zip(vectors if rows else [], rows, strict=True)],
        )
        set_embedding_model(self.conn, self.embedder.model)
        self.stale_model = None
        return len(rows)

    def remember(self, text: str, kind: str = "fact", sources: str = "", tags: str = "", project: str = "") -> int:
        kind = kind if kind in KINDS else "fact"
        text = text.strip()
        if not text:
            raise ValueError("Nothing to remember.")
        existing = self.conn.execute("SELECT id FROM memories WHERE text = ? AND kind = ?", (text, kind)).fetchone()
        if existing:
            return existing["id"]
        vec = None if self.stale_model else self.embedder.embed([text])
        cur = self.conn.execute(
            "INSERT INTO memories (kind, text, sources, tags, project, created, embedding) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (kind, text, sources, tags, project, time.time(), to_blob(vec[0]) if vec is not None else None),
        )
        self.conn.execute("INSERT INTO memories_fts (rowid, text, tags) VALUES (?, ?, ?)", (cur.lastrowid, text, tags))
        self.conn.commit()
        return cur.lastrowid

    def forget(self, memory_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self.conn.execute("DELETE FROM memories_fts WHERE rowid = ?", (memory_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def recall(self, query: str, k: int = 5, kind: str = "") -> list[dict]:
        kinds = [kind] if kind in KINDS else list(KINDS)
        placeholders = ",".join("?" * len(kinds))
        bm25_ids: list[int] = []
        match = fts_query(query)
        if match:
            bm25_ids = [
                r["id"]
                for r in self.conn.execute(
                    f"""SELECT m.id FROM memories_fts f JOIN memories m ON m.id = f.rowid
                        WHERE memories_fts MATCH ? AND m.kind IN ({placeholders})
                        ORDER BY bm25(memories_fts) LIMIT 30""",
                    (match, *kinds),
                )
            ]
        dense_ids: list[int] = []
        qvec = self.embedder.embed([query], query=True) if query.strip() and not self.stale_model else None
        if qvec is not None:
            rows = self.conn.execute(
                f"SELECT id, embedding FROM memories WHERE embedding IS NOT NULL AND kind IN ({placeholders})",
                kinds,
            ).fetchall()
            if rows:
                matrix = np.stack([from_blob(r["embedding"]) for r in rows])
                if matrix.shape[1] == qvec.shape[1]:
                    scores = matrix @ qvec[0]
                    # keep only reasonably similar memories
                    dense_ids = [rows[i]["id"] for i in np.argsort(-scores)[:30] if scores[i] > 0.3]
        ids = rrf(bm25_ids, dense_ids)[:k]
        if not ids:
            return []
        rows = {
            r["id"]: dict(r)
            for r in self.conn.execute(
                f"SELECT id, kind, text, sources, tags, project, created FROM memories WHERE id IN ({','.join('?' * len(ids))})",
                ids,
            )
        }
        return [rows[i] for i in ids if i in rows]

    def preferences(self) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute("SELECT id, kind, text FROM memories WHERE kind = 'preference' ORDER BY id")
        ]


def format_memory(m: dict) -> str:
    line = f"[m{m['id']}] ({m['kind']}) {m['text']}"
    if m.get("sources"):
        line += f"  — sources: {m['sources']}"
    return line

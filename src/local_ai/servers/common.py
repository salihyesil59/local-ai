"""Shared helpers for the MCP servers: settings from LOCAL_AI_* environment variables, FastMCP import
compatibility, embeddings and small text utilities."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

try:
    from mcp.server.fastmcp import FastMCP  # noqa: F401  (re-exported)
except ImportError:  # mcp >= 2 renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as FastMCP  # noqa: F401

BUILTIN_SKILLS = Path(__file__).resolve().parent.parent / "skills"


def workspace_root() -> Path:
    """LOCAL_AI_WORKSPACE (default ~/local-ai-workspace): notes, reports, indexes, memory and caches."""
    root = Path(os.environ.get("LOCAL_AI_WORKSPACE") or "~/local-ai-workspace").expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def cache_dir(name: str) -> Path:
    path = workspace_root() / "cache" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def library_dir() -> Path:
    """LOCAL_AI_LIBRARY (default ~/physics-library): the user's own PDF / .md / .txt files."""
    return Path(os.environ.get("LOCAL_AI_LIBRARY") or "~/physics-library").expanduser()


def zim_paths() -> list[Path]:
    """LOCAL_AI_ZIM_DIR (default ~/wiki-zim): folders and/or .zim files, separated by os.pathsep."""
    value = os.environ.get("LOCAL_AI_ZIM_DIR")
    if value is None:
        value = str(Path("~/wiki-zim"))
    return [Path(p).expanduser() for p in value.split(os.pathsep) if p.strip()]


def zim_files() -> list[Path]:
    """All .zim files found in zim_paths() (folders searched recursively), sorted by name."""
    files = set()
    for path in zim_paths():
        if path.is_dir():
            files.update(path.rglob("*.zim"))
        elif path.suffix == ".zim" and path.is_file():
            files.add(path)
    return sorted(files, key=lambda p: p.name)


def slugify(text: str, max_len: int = 60) -> str:
    """Turn arbitrary (also Turkish) text into a safe file/directory name."""
    text = text.replace("ı", "i").replace("İ", "I")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:max_len].strip("-") or "untitled"


def fts_query(*texts: str) -> str:
    """Build a safe SQLite FTS5 OR-query from free text (any language)."""
    words = []
    for text in texts:
        for w in re.findall(r"\w+", text or "", flags=re.UNICODE):
            w = w.lower()
            if len(w) > 1 and w not in words:
                words.append(w)
    return " OR ".join(f'"{w}"' for w in words[:64])


def rrf(*rankings: list[int], k: int = 60) -> list[int]:
    """Reciprocal-rank fusion of several ranked id lists."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=scores.get, reverse=True)


MAX_OUTPUT = 12000  # characters of tool output returned to the model
USER_AGENT = "local-ai/0.1 (personal research assistant)"


def log(msg: str) -> None:
    """On a stdio MCP server stdout belongs to the protocol: logs go to stderr."""
    print(msg, file=sys.stderr, flush=True)


def truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n...[{len(text) - limit} characters truncated]"


def http_get(url: str, timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# -- embeddings -------------------------------------------------------------------


DEFAULT_EMBED_MODEL = "text-embedding-qwen3-embedding-0.6b"
DEFAULT_QUERY_TASK = "Given a search query, retrieve relevant passages that answer the query"


def default_query_task(model: str) -> str:
    """Qwen3-Embedding expects an instruction before queries (documents are embedded as they are)."""
    return DEFAULT_QUERY_TASK if "qwen3-embedding" in model.lower() else ""


class Embedder:
    """Embeddings from LM Studio's OpenAI-compatible /v1/embeddings endpoint.

    Load a multilingual embedding model in LM Studio (default: Qwen3-Embedding-0.6B) so
    Turkish queries match English passages. If the endpoint is unreachable,
    `available` turns False and callers fall back to keyword (BM25) search.

    Queries get the instruction `LOCAL_AI_EMBED_QUERY_TASK` (default: a retrieval instruction for
    Qwen3-Embedding models, none for other models; set it to an empty string to turn it off).
    """

    def __init__(self, base_url: str | None = None, model: str | None = None, timeout: float = 120):
        self.base_url = (base_url or os.environ.get("LOCAL_AI_BASE_URL") or "http://localhost:1234/v1").rstrip("/")
        self.model = model or os.environ.get("LOCAL_AI_EMBED_MODEL") or DEFAULT_EMBED_MODEL
        task = os.environ.get("LOCAL_AI_EMBED_QUERY_TASK")
        self.query_task = default_query_task(self.model) if task is None else task
        self.timeout = timeout
        self.available = True

    def embed(self, texts: list[str], query: bool = False) -> np.ndarray | None:
        if not self.available or not texts:
            return None
        if query and self.query_task:
            texts = [f"Instruct: {self.query_task}\nQuery: {t}" for t in texts]
        body = json.dumps({"model": self.model, "input": texts}).encode()
        req = urllib.request.Request(
            f"{self.base_url}/embeddings", data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError):
            self.available = False
            return None
        vectors = [item["embedding"] for item in sorted(data["data"], key=lambda d: d["index"])]
        return normalize(np.asarray(vectors, dtype=np.float32))


class HashEmbedder(Embedder):
    """Deterministic bag-of-words embedder for tests (LOCAL_AI_EMBED_BACKEND=hash). Not semantic."""

    model = "hash"
    query_task = ""

    def __init__(self, dim: int = 256):
        self.dim = dim
        self.available = True

    def embed(self, texts: list[str], query: bool = False) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            for w in re.findall(r"\w+", text.lower()):
                out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1.0
        return normalize(out)


def normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


def make_embedder() -> Embedder:
    backend = os.environ.get("LOCAL_AI_EMBED_BACKEND", "lmstudio").lower()
    if backend == "hash":
        return HashEmbedder()
    if backend == "none":
        e = Embedder()
        e.available = False
        return e
    return Embedder()


def stored_embedding_model(conn: sqlite3.Connection, embedder: Embedder) -> str | None:
    """Record which model produced the vectors in this database. Returns the recorded model when it differs from
    the embedder's: vectors of different models are not comparable, so callers must not mix them."""
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    row = conn.execute("SELECT value FROM meta WHERE key = 'embedding_model'").fetchone()
    if row is None:
        set_embedding_model(conn, embedder.model)
        return None
    return row["value"] if row["value"] != embedder.model else None


def set_embedding_model(conn: sqlite3.Connection, model: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('embedding_model', ?)", (model,))
    conn.commit()


def to_blob(v: np.ndarray) -> bytes:
    return v.astype(np.float16).tobytes()


def from_blob(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float16).astype(np.float32)


# -- Jupyter notebooks -------------------------------------------------------------


def notebook_json(cells: list[dict], title: str = "") -> dict:
    """Build an nbformat-v4 notebook from executed cells.

    Each cell: {"code": str, "stdout": str, "results": [str], "error": str | None, "figures_png": [base64]}.
    """

    def lines(text: str) -> list[str]:
        return text.splitlines(keepends=True)

    nb_cells = []
    if title:
        nb_cells.append({"cell_type": "markdown", "metadata": {}, "source": lines(f"# {title}\n")})
    for count, cell in enumerate(cells, 1):
        outputs = []
        if cell.get("stdout"):
            outputs.append({"output_type": "stream", "name": "stdout", "text": lines(cell["stdout"])})
        for result in cell.get("results", []):
            outputs.append(
                {
                    "output_type": "execute_result",
                    "execution_count": count,
                    "metadata": {},
                    "data": {"text/plain": lines(result)},
                }
            )
        for png in cell.get("figures_png", []):
            outputs.append({"output_type": "display_data", "metadata": {}, "data": {"image/png": png}})
        if cell.get("error"):
            outputs.append(
                {
                    "output_type": "error",
                    "ename": "Error",
                    "evalue": cell["error"].splitlines()[-1] if cell["error"] else "",
                    "traceback": cell["error"].splitlines(),
                }
            )
        nb_cells.append(
            {
                "cell_type": "code",
                "execution_count": count,
                "metadata": {},
                "source": lines(cell["code"]),
                "outputs": outputs,
            }
        )
    return {
        "cells": nb_cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def write_notebook(path: Path, cells: list[dict], title: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    nb = notebook_json(cells, title)
    for i, cell in enumerate(nb["cells"]):
        cell["id"] = f"cell-{i}"
    path.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
    return path

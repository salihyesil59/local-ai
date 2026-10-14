"""The Offline view of the web UI: the user's library (files and notebooks) and the ZIM archives.

The library and the ZIM folders are the ones the library server of the mcp.json uses (its LOCAL_AI_LIBRARY and
LOCAL_AI_ZIM_DIR), so a file added here is found by search_library. A notebook is a folder notebooks/<name> of the
library: a chat that chooses it searches only its files.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from local_ai.core.mcp_bridge import read_servers
from local_ai.research import data
from local_ai.servers.common import NOTEBOOKS, library_dir, slugify, workspace_root, zim_paths

LIBRARY_SUFFIXES = {".pdf", ".md", ".txt"}
MAX_UPLOAD = 512 * 1024 * 1024  # bytes of one file
MAX_LISTED = 2000  # library files listed
UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class Places:
    library: Path
    zim_dirs: list[Path]
    workspace: Path


def places(config_path: Path | None) -> Places:
    """Where the library server of the mcp.json keeps its files (its environment), else the defaults."""
    env = {}
    if config_path:
        env = dict(read_servers(config_path).get("library", {}).get("env") or {})
    library = Path(env["LOCAL_AI_LIBRARY"]).expanduser() if env.get("LOCAL_AI_LIBRARY") else library_dir()
    if env.get("LOCAL_AI_ZIM_DIR"):
        zims = [Path(p).expanduser() for p in env["LOCAL_AI_ZIM_DIR"].split(os.pathsep) if p.strip()]
    else:
        zims = zim_paths()
    workspace = Path(env["LOCAL_AI_WORKSPACE"]).expanduser() if env.get("LOCAL_AI_WORKSPACE") else workspace_root()
    return Places(library, zims, workspace)


# -- the library -----------------------------------------------------------------------------------


def indexed_paths(workspace: Path) -> set[str]:
    """The library files the search index has (read only; empty before the first index)."""
    db = workspace / "library.sqlite"
    if not db.is_file():
        return set()
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            return {row[0] for row in conn.execute("SELECT path FROM docs WHERE kind IN ('pdf', 'text')")}
    except sqlite3.Error:
        return set()


def file_entry(root: Path, path: Path, indexed: set[str]) -> dict:
    stat = path.stat()
    return {
        "path": path.relative_to(root).as_posix(),
        "size": stat.st_size,
        "modified": stat.st_mtime,
        "indexed": str(path.resolve()) in indexed,
    }


def library_listing(where: Places) -> dict:
    """The files of the library and its notebooks."""
    root = where.library
    listing = {"dir": str(root), "exists": root.is_dir(), "files": [], "notebooks": [], "more": 0}
    if not root.is_dir():
        return listing
    indexed = indexed_paths(where.workspace)
    notebooks: dict[str, list[dict]] = {}
    count = 0
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in LIBRARY_SUFFIXES or not path.is_file():
            continue
        count += 1
        if count > MAX_LISTED:
            continue
        entry = file_entry(root, path, indexed)
        parts = Path(entry["path"]).parts
        if len(parts) >= 3 and parts[0] == NOTEBOOKS:
            notebooks.setdefault(parts[1], []).append(entry)
        else:
            listing["files"].append(entry)
    folder = root / NOTEBOOKS
    for book in sorted(p for p in folder.iterdir() if p.is_dir()) if folder.is_dir() else []:
        listing["notebooks"].append({"name": book.name, "files": notebooks.get(book.name, [])})
    listing["more"] = max(0, count - MAX_LISTED)
    return listing


def inside(root: Path, relative: str) -> Path:
    """A path of the library; ValueError when it would leave it."""
    target = (root / relative.strip().strip("/\\")).resolve()
    if target != root.resolve() and root.resolve() not in target.parents:
        raise ValueError("the path is outside the library")
    return target


def file_name(name: str) -> str:
    """A safe file name of an allowed kind (PDF, Markdown, text)."""
    name = UNSAFE.sub("_", Path(name.replace("\\", "/")).name).strip(" .")
    if Path(name).suffix.lower() not in LIBRARY_SUFFIXES:
        raise ValueError("only PDF, Markdown (.md) and text (.txt) files go into the library")
    return name


def save_file(where: Places, folder: str, name: str, content: bytes) -> dict:
    """Put a file into a folder of the library (a name that is taken gets a number)."""
    if not content or len(content) > MAX_UPLOAD:
        raise ValueError(f"the file is empty or larger than {MAX_UPLOAD // (1024 * 1024)} MB")
    name = file_name(name)
    target_dir = inside(where.library, folder)
    target_dir.mkdir(parents=True, exist_ok=True)
    target, n = target_dir / name, 2
    while target.exists():
        target = target_dir / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1
    target.write_bytes(content)
    return file_entry(where.library, target, set())


def delete_file(where: Places, relative: str) -> None:
    target = inside(where.library, relative)
    if not target.is_file() or target.suffix.lower() not in LIBRARY_SUFFIXES:
        raise FileNotFoundError(relative)
    target.unlink()


def notebook_folder(where: Places, name: str) -> Path:
    return where.library / NOTEBOOKS / slugify(name)


def create_notebook(where: Places, name: str) -> str:
    if not name.strip():
        raise ValueError("give the notebook a name")
    folder = notebook_folder(where, name)
    if folder.exists():
        raise ValueError(f"a notebook {folder.name} exists already")
    folder.mkdir(parents=True)
    return folder.name


def delete_notebook(where: Places, name: str) -> None:
    folder = where.library / NOTEBOOKS / name
    if not re.fullmatch(r"[a-z0-9-]+", name) or not folder.is_dir():
        raise FileNotFoundError(name)
    shutil.rmtree(folder)


def notebook_files(where: Places, name: str) -> list[str]:
    """The files of a notebook (their names), or FileNotFoundError when there is no such notebook."""
    folder = where.library / NOTEBOOKS / name
    if not re.fullmatch(r"[a-z0-9-]+", name) or not folder.is_dir():
        raise FileNotFoundError(name)
    return sorted(p.name for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in LIBRARY_SUFFIXES)


# -- ZIM archives ------------------------------------------------------------------------------------


def zim_listing(where: Places) -> list[dict]:
    archives = []
    for folder in where.zim_dirs:
        found = [folder] if folder.suffix == ".zim" and folder.is_file() else sorted(folder.glob("*.zim"))
        for path in found if folder.exists() else []:
            m = data.ZIM_NAME_RE.match(path.name)
            archives.append({
                "file": path.name,
                "folder": str(path.parent),
                "name": m["prefix"] if m else path.stem,
                "date": m["date"] if m else "",
                "size": path.stat().st_size,
            })
    return archives


def zim_updates(where: Places) -> list[dict]:
    """The archives that have a newer release in the Kiwix catalog (needs the internet)."""
    found = []
    for folder in {Path(a["folder"]) for a in zim_listing(where)}:
        for z in data.local_zims(folder):
            newest = data.newer_release(z) if z["date"] else None
            if newest:
                found.append({
                    "file": z["path"].name,
                    "folder": str(folder),
                    "new_file": newest["file"],
                    "url": newest["url"],
                    "size": newest["size"],
                })
    return found


@dataclass
class ZimJob:
    """Downloads of newer ZIM releases, in a thread of its own; the view polls `state`."""

    state: dict = field(default_factory=lambda: {"running": False, "file": "", "done": 0, "total": 0, "log": []})
    cancelled: threading.Event = field(default_factory=threading.Event)

    def start(self, updates: list[dict], keep_old: bool) -> None:
        if self.state["running"]:
            raise RuntimeError("an update is running already")
        self.cancelled.clear()
        self.state.update(running=True, file="", done=0, total=0, log=[])
        threading.Thread(target=self.run, args=(updates, keep_old), daemon=True).start()

    def progress(self, done: int, total: int) -> None:
        if self.cancelled.is_set():
            raise RuntimeError("cancelled")
        self.state.update(done=done, total=total)

    def run(self, updates: list[dict], keep_old: bool) -> None:
        try:
            for update in updates:
                folder = Path(update["folder"])
                self.state.update(file=update["new_file"], done=0, total=update["size"])
                try:
                    data.download(update["url"], folder / update["new_file"], quiet=True, progress=self.progress)
                except Exception as e:  # a failed or cancelled download: the part is kept for the next try
                    self.state["log"].append(f"{update['new_file']}: {e}")
                    if self.cancelled.is_set():
                        break
                    continue
                if not keep_old:
                    (folder / update["file"]).unlink(missing_ok=True)
                self.state["log"].append(f"{update['file']} -> {update['new_file']}")
        finally:
            self.state.update(running=False, file="", finished=time.time())

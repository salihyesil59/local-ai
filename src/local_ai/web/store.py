"""What the web UI remembers between sessions: its chats (runs) and the folders they are sorted into.

<workspace>/ui/index.json     {"folders": [{"id", "name"}], "runs": {id: {question, project, mode, status, title,
                              folder, created}}}
<workspace>/ui/runs/<id>.jsonl  the events of a run, one JSON object per line (live token output is not kept)
<workspace>/ui/runs/<id>.chat.json  the conversation of a code chat with the model, to go on with it later
<workspace>/ui/runs/<id>.trace.jsonl  its runs with the model's thinking, as training data (local-ai traces export)
<workspace>/ui/images/<id>/   the images sent with the messages of a chat
<workspace>/ui/files/<id>/    the other files sent with them (PDF, Word, notes, code, ...)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

IMAGE_NAME = re.compile(r"[0-9a-f]{12}\.(?:png|jpg|webp|gif)")


class Store:
    def __init__(self, workspace: Path):
        self.dir = workspace / "ui"
        self.index_path = self.dir / "index.json"
        self.folders: list[dict] = []
        self.runs: dict[str, dict] = {}
        if self.index_path.is_file():
            try:
                data = json.loads(self.index_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:  # a damaged index must not keep the UI from starting
                data = {}
            self.folders = list(data.get("folders", []))
            self.runs = dict(data.get("runs", {}))

    # -- index -------------------------------------------------------------------

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"folders": self.folders, "runs": self.runs}, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, self.index_path)

    def add_run(self, run_id: str, **meta) -> dict:
        self.runs[run_id] = {"title": "", "folder": None, "created": time.time(), **meta}
        self.save()
        return self.runs[run_id]

    def update_run(self, run_id: str, **fields) -> None:
        if run_id in self.runs:
            self.runs[run_id].update(fields)
            self.save()

    def delete_run(self, run_id: str) -> None:
        self.runs.pop(run_id, None)
        self.save()
        (self.dir / "runs" / f"{run_id}.jsonl").unlink(missing_ok=True)
        (self.dir / "runs" / f"{run_id}.chat.json").unlink(missing_ok=True)
        self.trace_path(run_id).unlink(missing_ok=True)
        shutil.rmtree(self.dir / "images" / run_id, ignore_errors=True)
        shutil.rmtree(self.dir / "files" / run_id, ignore_errors=True)

    # -- folders -----------------------------------------------------------------

    def folder_exists(self, folder_id: str | None) -> bool:
        return folder_id is None or any(f["id"] == folder_id for f in self.folders)

    def add_folder(self, name: str) -> dict:
        folder = {"id": uuid.uuid4().hex[:8], "name": name}
        self.folders.append(folder)
        self.save()
        return folder

    def rename_folder(self, folder_id: str, name: str) -> bool:
        for folder in self.folders:
            if folder["id"] == folder_id:
                folder["name"] = name
                self.save()
                return True
        return False

    def delete_folder(self, folder_id: str) -> bool:
        """Delete a folder; its chats move out of it, they are not deleted."""
        if not self.folder_exists(folder_id) or folder_id is None:
            return False
        self.folders = [f for f in self.folders if f["id"] != folder_id]
        for meta in self.runs.values():
            if meta.get("folder") == folder_id:
                meta["folder"] = None
        self.save()
        return True

    # -- events ------------------------------------------------------------------

    def append_event(self, run_id: str, event: dict) -> None:
        path = self.dir / "runs" / f"{run_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def read_events(self, run_id: str) -> list[dict]:
        path = self.dir / "runs" / f"{run_id}.jsonl"
        if not path.is_file():
            return []
        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:  # a line cut off by a crash
                continue
        return events

    # -- conversations -----------------------------------------------------------

    def save_messages(self, run_id: str, conversation: dict) -> None:
        path = self.dir / "runs" / f"{run_id}.chat.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(conversation, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def trace_path(self, run_id: str) -> Path:
        """The trace of a code chat: its runs with the model's thinking, for training (local-ai traces export)."""
        return self.dir / "runs" / f"{run_id}.trace.jsonl"

    def load_messages(self, run_id: str) -> dict:
        path = self.dir / "runs" / f"{run_id}.chat.json"
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except json.JSONDecodeError:  # cut off by a crash: the chat starts a new conversation
            return {}

    # -- images ------------------------------------------------------------------

    def save_image(self, run_id: str, data: bytes, ext: str) -> str:
        """Keep an image sent with a message; returns its name within the chat."""
        name = f"{uuid.uuid4().hex[:12]}.{ext}"
        folder = self.dir / "images" / run_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
        return name

    def image_path(self, run_id: str, name: str) -> Path | None:
        """The file of an image of a chat; None for a name that is not one (no path can leave the folder)."""
        if not IMAGE_NAME.fullmatch(name) or not re.fullmatch(r"\w+", run_id):
            return None
        path = self.dir / "images" / run_id / name
        return path if path.is_file() else None

    # -- files (documents, notes, code) --------------------------------------------

    def save_file(self, run_id: str, name: str, data: bytes) -> str:
        """Keep a file sent with a message (name: a safe file name); returns its name within the chat."""
        stored = f"{uuid.uuid4().hex[:8]}-{name}"
        folder = self.dir / "files" / run_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / stored).write_bytes(data)
        return stored

    def file_path(self, run_id: str, name: str) -> Path | None:
        """The file of a chat; None for a name that is not one (no path can leave the folder)."""
        if not re.fullmatch(r"[0-9a-f]{8}-[^/\\]+", name) or not re.fullmatch(r"\w+", run_id) or ".." in name:
            return None
        path = self.dir / "files" / run_id / name
        return path if path.is_file() else None

    def clear(self) -> None:
        """For tests and resets: forget everything."""
        shutil.rmtree(self.dir, ignore_errors=True)
        self.folders, self.runs = [], {}

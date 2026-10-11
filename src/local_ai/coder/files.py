"""File tools for clients that have none of their own (LM Studio chat). Goose has its own `developer` extension,
so they are off unless the server starts with `--files`.

Every path is relative to the project folder and may not leave it. The plan state (.local-coder) is read-only.
"""

from __future__ import annotations

from pathlib import Path

from local_ai.coder.state import STATE_DIR
from local_ai.coder.verify import SKIP_DIRS

MAX_READ = 20000  # characters returned by one read_file
MAX_LIST = 300  # entries returned by one list_files


def resolve(root: Path, path: str, write: bool = False) -> Path:
    """The file or folder `path` inside root; ValueError when it is outside or protected."""
    target = (root / path).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"{path} is outside the project folder {root}")
    if write and STATE_DIR in target.relative_to(root).parts:
        raise ValueError(f"{STATE_DIR} belongs to the coder tools; do not write there")
    return target


def read_file(root: Path, path: str, start: int = 1, end: int = 0) -> str:
    try:
        target = resolve(root, path)
        text = target.read_text(encoding="utf-8")
    except (ValueError, OSError, UnicodeDecodeError) as e:
        return f"Error: cannot read {path}: {e}"
    lines = text.splitlines()
    first = max(start, 1)
    last = min(end, len(lines)) if end > 0 else len(lines)
    chunk = "\n".join(lines[first - 1 : last])
    note = ""
    if len(chunk) > MAX_READ:
        chunk = chunk[:MAX_READ]
        note = f"\n[cut at {MAX_READ} characters of {len(lines)} lines; read the rest with start/end]"
    return f"{chunk}{note}" if chunk else f"(empty: {path} has {len(lines)} lines)"


def write_file(root: Path, path: str, content: str) -> str:
    try:
        target = resolve(root, path, write=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")
    except (ValueError, OSError) as e:
        return f"Error: cannot write {path}: {e}"
    return f"Wrote {path} ({len(content.splitlines())} lines)."


def edit_file(root: Path, path: str, old: str, new: str) -> str:
    """Replace one exact occurrence of `old`; works on files with Windows line endings too."""
    if not old:
        return "Error: old must not be empty; use write_file to create a file."
    try:
        target = resolve(root, path, write=True)
        raw = target.read_bytes().decode("utf-8")  # bytes: universal newlines would hide CRLF
    except (ValueError, OSError, UnicodeDecodeError) as e:
        return f"Error: cannot edit {path}: {e}"
    if "\r\n" in raw and "\r\n" not in old:
        old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
    count = raw.count(old)
    if count == 0:
        return f"Error: old text not found in {path}. Read the file again and copy the lines exactly."
    if count > 1:
        return f"Error: old text appears {count} times in {path}. Include more surrounding lines to make it unique."
    target.write_bytes(raw.replace(old, new).encode("utf-8"))
    return f"Edited {path}."


def list_files(root: Path, path: str = ".") -> str:
    try:
        base = resolve(root, path)
    except ValueError as e:
        return f"Error: {e}"
    if not base.is_dir():
        return f"Error: {path} is not a folder."
    names = []
    for item in sorted(base.rglob("*")):
        rel = item.relative_to(root)
        if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in rel.parts):
            continue
        names.append(f"{rel.as_posix()}/" if item.is_dir() else rel.as_posix())
    shown = names[:MAX_LIST]
    more = f"\n[{len(names) - MAX_LIST} more entries not shown; list a subfolder]" if len(names) > MAX_LIST else ""
    return "\n".join(shown) + more if shown else f"(no files in {path})"

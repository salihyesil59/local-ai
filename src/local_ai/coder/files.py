"""File tools for clients that have none of their own (LM Studio chat). Goose has its own `developer` extension,
so they are off unless the server starts with `--files`.

Every path is relative to the project folder and may not leave it. The plan state (.local-coder) is read-only.
"""

from __future__ import annotations

import os
import re
from fnmatch import fnmatch
from pathlib import Path

from local_ai.coder.state import STATE_DIR
from local_ai.coder.verify import SKIP_DIRS

MAX_READ = 20000  # characters returned by one read_file
MAX_LIST = 300  # entries returned by one list_files
TEST_FILE = re.compile(r"(^|/)(tests?/|test_[^/]*$|[^/]*_test\.\w+$)")
TEST_DEF = re.compile(r"(?:async\s+)?def\s+(test\w*)")
TOP_LEVEL = re.compile(r"(?:async\s+def|def|class|@)\b|\S+\s*=")  # what ends a test function at the margin


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


def replace_in_files(
    root: Path, old: str, new: str, path: str = ".", glob: str = "", word: bool = False, regex: bool = False
) -> str:
    """Replace every occurrence of `old` in the text files under path (a rename in one call). word: whole words
    only; regex: old is a regular expression and new may use \\1; glob: only files whose name matches."""
    if not old:
        return "Error: old must not be empty."
    try:
        base = resolve(root, path)
        found = old if regex else re.escape(old)
        pattern = re.compile(rf"\b(?:{found})\b" if word else found)
    except (ValueError, re.error) as e:
        return f"Error: {e}"
    files = [base] if base.is_file() else [root / name for name in walk(root, base) if not name.endswith("/")]
    changed = []
    for target in files:
        rel = target.relative_to(root).as_posix()
        if glob and not (fnmatch(target.name, glob) or fnmatch(rel, glob)):
            continue
        try:
            text = target.read_bytes().decode("utf-8")  # bytes: the line endings stay as they are
            result, count = pattern.subn(new if regex else (lambda _: new), text)
        except UnicodeDecodeError:
            continue  # not a text file
        except (OSError, re.error) as e:
            return f"Error in {rel}: {e}" + (f" ({len(changed)} files were changed before it)" if changed else "")
        if count:
            target.write_bytes(result.encode("utf-8"))
            changed.append(f"  {rel}: {count}")
    if not changed:
        return f"No match for {old!r} under {path}; nothing was changed."
    total = sum(int(line.rsplit(": ", 1)[1]) for line in changed)
    return f"Replaced {total} occurrences in {len(changed)} files:\n" + "\n".join(changed[:100])


def walk(root: Path, base: Path | None = None) -> list[str]:
    """Files and folders (with a trailing /) under base, relative to root; caches and virtual environments
    are skipped."""
    def skipped(name: str) -> bool:
        return name in SKIP_DIRS or name.endswith(".egg-info")

    found = []  # (path, is a folder); skipped folders are not entered: an environment has thousands of files
    for folder, dirs, files in os.walk(base or root):
        dirs[:] = [d for d in dirs if not skipped(d)]
        found += [(Path(folder) / d, True) for d in dirs] + [(Path(folder) / f, False) for f in files]
    names = []
    for item, is_dir in sorted(found):
        rel = item.relative_to(root)
        if any(skipped(part) for part in rel.parts):
            continue
        names.append(f"{rel.as_posix()}/" if is_dir else rel.as_posix())
    return names


def is_test_file(path: str) -> bool:
    return bool(TEST_FILE.search(path.replace("\\", "/")))


def test_lines(text: str) -> dict[str, str]:
    """The lines of the test functions of a file (stripped, not blank), each with the name of its test. A test
    ends at the next definition or assignment at the margin; a string at the margin (expected output) is part of it."""
    found: dict[str, str] = {}
    test = None
    for line in text.splitlines():
        code = line.lstrip()
        if code and line == code and TOP_LEVEL.match(code):  # at the margin
            m = TEST_DEF.match(code)
            test = m.group(1) if m else None
        if test and code:
            found.setdefault(code.rstrip(), test)
    return found


def list_files(root: Path, path: str = ".") -> str:
    try:
        base = resolve(root, path)
    except ValueError as e:
        return f"Error: {e}"
    if not base.is_dir():
        return f"Error: {path} is not a folder."
    names = walk(root, base)
    shown = names[:MAX_LIST]
    more = f"\n[{len(names) - MAX_LIST} more entries not shown; list a subfolder]" if len(names) > MAX_LIST else ""
    return "\n".join(shown) + more if shown else f"(no files in {path})"

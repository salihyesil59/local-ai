"""What a code run changed in a git project, file by file, and undoing the change of one file.

The web UI shows these in its Changes panel. Git is only read, except for an undo, which puts one file back as it
is in the last commit (or deletes a file that is not in it). Files the user had already changed before the run
are listed but never undone: that would throw away the user's own work.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from local_ai.coder import files as fl

MAX_FILES = 200  # changed files listed
MAX_DIRTY = 2000  # paths kept from the state before a run
DIFF_CHARS = 60_000  # of one file's diff
NEW_FILE_BYTES = 200_000  # a larger new file is listed without its text


def git(root: Path, *args: str) -> str:
    """The output of a git command in root; "" when it fails (not a repository, no git, ...)."""
    try:
        done = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except OSError:
        return ""
    return done.stdout if done.returncode == 0 else ""


def is_repo(root: Path) -> bool:
    """True when root is in a git repository with at least one commit."""
    return root.is_dir() and bool(git(root, "rev-parse", "--verify", "HEAD"))


def status(root: Path) -> list[tuple[str, str]]:
    """(code, path) of every changed file under root, paths relative to root, the coder's own folder left out.
    code is git's two-letter status ("??" for a new file git does not know)."""
    prefix = git(root, "rev-parse", "--show-prefix").strip()  # root's place in the repository
    raw = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", ".")
    entries, parts = [], raw.split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if "R" in code or "C" in code:  # a rename is followed by its old path
            i += 1
        if prefix and not path.startswith(prefix):
            continue
        path = path[len(prefix) :]
        if path.split("/")[0] == fl.STATE_DIR:
            continue
        entries.append((code, path))
    return entries


def dirty(root: Path) -> list[str]:
    """The files that differ from the last commit now: taken before a run, so they are never undone."""
    return [path for _, path in status(root)][:MAX_DIRTY]


def counts(diff: str) -> tuple[int, int]:
    """Added and removed lines of a unified diff."""
    lines = diff.splitlines()
    added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
    return added, removed


def new_file_diff(root: Path, path: str) -> str:
    """A file git does not know yet, as a diff that adds all its lines."""
    target = root / path
    try:
        if target.stat().st_size > NEW_FILE_BYTES:
            return ""
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    lines = text.splitlines()
    return f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{line}\n" for line in lines)


def changes(root: Path, before: set[str]) -> list[dict]:
    """The changed files with their diffs. before: the files that were already changed before the run."""
    found = []
    for code, path in status(root)[:MAX_FILES]:
        if code == "??":
            kind, diff = "added", new_file_diff(root, path)
        else:
            kind = "deleted" if "D" in code else "added" if "A" in code else "modified"
            diff = git(root, "diff", "HEAD", "--", path)
        added, removed = counts(diff)
        found.append({
            "path": path,
            "status": kind,
            "diff": diff[:DIFF_CHARS],
            "cut": len(diff) > DIFF_CHARS,
            "added": added,
            "removed": removed,
            "before_run": path in before,
        })  # fmt: skip
    return found


def undo(root: Path, path: str, before: set[str]) -> str:
    """Put one file back as it is in the last commit, or delete it when it is new. "" when done, else why not."""
    if path in before:
        return "This file was already changed before the run; it is not undone, so that your own work stays."
    try:
        target = fl.resolve(root, path)
    except ValueError as e:
        return str(e)
    rel = target.relative_to(root).as_posix()
    if rel not in {p for _, p in status(root)}:
        return f"{rel} has no change to undo."
    if _in_head(root, rel):
        git(root, "checkout", "HEAD", "--", rel)
    else:  # new in this run: remove it from the index if it was added there, and from the disk
        git(root, "rm", "--cached", "--quiet", "--force", "--", rel)
        target.unlink(missing_ok=True)
    return "" if rel not in {p for _, p in status(root)} else f"{rel} could not be undone."


def _in_head(root: Path, rel: str) -> bool:
    """True when the last commit has the file (cat-file -e prints nothing, so its exit code is checked)."""
    try:
        done = subprocess.run(["git", "cat-file", "-e", f"HEAD:./{rel}"], cwd=root, capture_output=True)
    except OSError:
        return False
    return done.returncode == 0

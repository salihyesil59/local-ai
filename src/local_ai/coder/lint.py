"""Problems in a changed Python file, found right after the edit: syntax errors, undefined names, redefinitions.

The coder's edit tools add these to their result, so the model sees a broken file in the same turn instead of
after a slow test run. Ruff checks only a few rules that are nearly always real mistakes; without ruff the file is
compiled, which finds syntax errors only. No model call, no change to the project (ruff runs without its cache).
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

from local_ai.coder import files as fl
from local_ai.coder import verify as vf

RULES = "E9,F63,F7,F82,F821,F811"  # syntax errors, wrong comparisons, misplaced statements, undefined names
TIMEOUT = 15  # seconds for one ruff run
MAX_OUTPUT = 2000  # characters of problems added to a tool result


def ruff_command() -> list[str] | None:
    """How to start ruff: from PATH, else from this program's environment; None when there is none."""
    found = shutil.which("ruff")
    if found:
        return [found]
    if importlib.util.find_spec("ruff") is not None:
        return [sys.executable, "-m", "ruff"]
    return None


def compile_errors(root: Path, paths: list[str]) -> str:
    """Syntax errors of the files, as ruff would list them: path:line:column: message."""
    found = []
    for path in paths:
        try:
            source = fl.resolve(root, path).read_text(encoding="utf-8")
            compile(source, path, "exec")
        except SyntaxError as e:
            found.append(f"{path}:{e.lineno or 0}:{e.offset or 0}: SyntaxError: {e.msg}")
        except (OSError, UnicodeDecodeError, ValueError):
            continue
    return "\n".join(found)


def check(root: Path, paths: list[str]) -> str:
    """The problems of the Python files among paths (relative to root), one per line; "" when there are none."""
    paths = [p for p in paths if p.endswith(".py")]
    if not paths:
        return ""
    command = ruff_command()
    if command is None:
        return compile_errors(root, paths)
    args = ["check", "--output-format=concise", "--no-fix", "--no-cache", "--quiet", f"--select={RULES}"]
    try:
        done = subprocess.run(
            [*command, *args, *paths],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return compile_errors(root, paths)
    if done.returncode not in (0, 1):  # ruff itself failed (bad config, ...): not the model's problem
        return compile_errors(root, paths)
    lines = [line for line in done.stdout.splitlines() if line.strip() and not line.startswith("Found ")]
    return vf.summarize("\n".join(lines), MAX_OUTPUT) if lines else ""


def with_problems(root: Path, output: str, paths: list[str]) -> str:
    """A successful edit's result with the problems of the changed files after it."""
    if output.startswith("Error") or output.startswith("No match"):
        return output
    problems = check(root, paths)
    if not problems:
        return output
    return f"{output}\n\nProblems found in the changed code (fix them first):\n{problems}"

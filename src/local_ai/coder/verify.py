"""Find the project's test command, run it and reduce its output to what the model needs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
ERROR_RE = re.compile(r"FAILED|FAIL\b|ERROR|Error|error\b|Exception|panicked|assert")
MAX_OUTPUT = 6000  # characters of test output returned to the model
# Not part of the project's source: caches, environments, build output and our own state.
SKIP_DIRS = {
    ".local-coder",
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "target",
    "dist",
    "build",
}


@dataclass
class Result:
    passed: bool
    output: str
    seconds: float
    timed_out: bool = False


def detect_command(root: Path) -> str | None:
    """The usual test command for the project in `root`, or None when there is nothing to run yet."""
    python_markers = ("pyproject.toml", "pytest.ini", "setup.py", "setup.cfg", "tox.ini", "tests", "test")
    if any((root / m).exists() for m in python_markers) or any(root.glob("test_*.py")):
        return f"{project_python(root)} -m pytest -x -q --tb=short"
    package = root / "package.json"
    if package.is_file():
        try:
            scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts", {})
        except json.JSONDecodeError:
            scripts = {}
        if "test" in scripts:
            return "npm test"
    if (root / "Cargo.toml").is_file():
        return "cargo test"
    if (root / "go.mod").is_file():
        return "go test ./..."
    return None


def project_python(root: Path) -> str:
    """The project's own virtual environment if it has one, else `python` from PATH."""
    for folder in (".venv", "venv"):
        exe = root / folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if exe.is_file():
            return f'"{exe}"'
    return "python"


def run(command: str, root: Path, timeout: float = 300) -> Result:
    """Run `command` in a shell in `root`. Passed means exit code 0. On a timeout the whole process tree is killed."""
    start = time.monotonic()
    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=os.name != "nt",
    )
    timed_out = False
    try:
        raw, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_tree(proc)
        raw, _ = proc.communicate()
    output = ANSI_RE.sub("", raw.decode("utf-8", errors="replace")).replace("\r\n", "\n").strip()
    if timed_out:
        output += f"\nTimed out after {timeout:g} s; the command was killed."
    return Result(proc.returncode == 0 and not timed_out, output, time.monotonic() - start, timed_out)


def kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        os.killpg(proc.pid, signal.SIGKILL)


FAILED_TEST = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)  # pytest's short summary
EXIT_FIRST = re.compile(r"\s(?:-x|--exitfirst|--maxfail[= ]\d+)(?=\s|$)")
TEST_ID = re.compile(r"[\w./\\\-\[\]:,=]+")


def failed_tests(output: str) -> list[str]:
    """The tests a pytest run reports as failed (tests/test_x.py::test_a), in order, each once."""
    return list(dict.fromkeys(FAILED_TEST.findall(output)))


def every_failure(command: str) -> str:
    """The command without pytest's stop-at-the-first-failure options: all failing tests get reported."""
    return EXIT_FIRST.sub("", command) if "pytest" in command else command


def test_mentioned(test_id: str, text: str) -> bool:
    """Whether a text (lower case) names the test or its file."""
    file = test_id.split("::")[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    name = test_id.split("::")[-1].split("[")[0].lower() if "::" in test_id else ""
    return file in text or bool(name and name in text)


def has_tests(root: Path) -> bool:
    """Whether the project has test files yet (a run before the first one would only say that there are none)."""
    for _, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for name in names:
            stem = name.rsplit(".", 1)[0]
            if Path(name).suffix in CODE_SUFFIXES and (stem.startswith("test_") or stem.endswith(("_test", ".test", ".spec"))):
                return True
    return False


def focused(command: str, tests: str, root: Path) -> str:
    """The command for some tests only (pytest: files or file::test). ValueError when that cannot be done."""
    if "pytest" not in command:
        raise ValueError("the verify command is not pytest")
    chosen = tests.replace(",", " ").split()
    for item in chosen:
        if not TEST_ID.fullmatch(item) or not (root / item.split("::")[0]).exists():
            raise ValueError(f"{item} is not a test file of the project")
    return f"{command} {' '.join(chosen)}"


def snapshot(root: Path) -> str:
    """Fingerprint of the project's files (path, size, modification time): tells whether anything was edited."""
    digest = hashlib.sha1()
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info"))
        for name in sorted(files):
            path = Path(folder) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            digest.update(f"{path}|{stat.st_size}|{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()[:12]


CODE_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go", ".java", ".c", ".cpp", ".h", ".rb", ".cs"}
TEST_NAME = re.compile(r"(?:def|function|fn|func)\s+(test\w*)|\b(?:it|test)\(\s*['\"]([^'\"]+)['\"]")


def project_names(root: Path) -> tuple[set[str], set[str]]:
    """Files (relative paths and base names) and test names of the project, for checking what a note refers to."""
    files: set[str] = set()
    tests: set[str] = set()
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for name in names:
            path = Path(folder) / name
            files.add(name)
            files.add(path.relative_to(root).as_posix())
            if path.suffix in CODE_SUFFIXES:
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                tests.update(m.group(1) or m.group(2) for m in TEST_NAME.finditer(text))
    return files, tests


def note_cites_project(note: str, root: Path) -> bool:
    """True if the note names a file or a test that exists in the project."""
    files, tests = project_names(root)
    for token in re.findall(r"[\w./\\:-]+", note):
        token = token.replace("\\", "/").strip(".:/")
        if token in files or token.rsplit("/", 1)[-1] in files or token.split("::")[-1] in tests:
            return True
    return False


def summarize(output: str, limit: int = MAX_OUTPUT) -> str:
    """Head and tail of long output: the first failure is near the top, the summary at the bottom."""
    if not output:
        return "(no output)"
    if len(output) <= limit:
        return output
    return output[: limit // 2] + "\n[... output truncated ...]\n" + output[-limit // 2 :]


def error_lines(output: str) -> list[str]:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return [line for line in lines if ERROR_RE.search(line)] or lines[-5:]


def first_error(output: str) -> str:
    lines = error_lines(output)
    return lines[0][:200] if lines else "(no output)"


def signature(output: str) -> str:
    """Identifies a failure across runs: its first error lines, without numbers (durations, line numbers, addresses)."""
    text = "\n".join(re.sub(r"\d+", "N", line) for line in error_lines(output)[:5])
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]

"""SWE-bench Lite tasks: download a task, prepare its repository with its own environment, reset it between runs
and score the agent's changes the way the benchmark does (its hidden tests on top of the changes)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROWS_URL = (
    "https://datasets-server.huggingface.co/rows?dataset=princeton-nlp%2FSWE-bench_Lite"
    "&config=default&split=test&offset={offset}&length=100"
)
EXCLUDE = ".venv/\n.local-coder/\n__pycache__/\n.pytest_cache/\n"
# PASS_TO_PASS tests that fail on this machine even with the reference fix (a path test written for Linux): the
# setup writes them here and the scoring leaves them out
IGNORED = "ignored.json"
RESULT_WORDS = ("PASSED", "FAILED", "ERROR", "XFAIL", "XPASS", "SKIPPED")


class SetupError(RuntimeError):
    pass


@dataclass
class Score:
    resolved: bool
    fail_to_pass: dict[str, str]  # test name -> result
    broken: list[str]  # PASS_TO_PASS tests that no longer pass
    changed: str  # git diff --stat of the agent's changes
    output: str = ""  # end of the test output when no result could be read


def git(cwd: Path, *args: str, stdin: bytes | None = None, check: bool = True) -> bytes:
    done = subprocess.run(["git", *args], cwd=cwd, input=stdin, capture_output=True)
    if check and done.returncode != 0:
        raise SetupError(f"git {' '.join(args)} failed: {done.stderr.decode(errors='replace').strip()}")
    return done.stdout


def python_of(project: Path) -> Path:
    windows = project / ".venv" / "Scripts" / "python.exe"
    return windows if windows.exists() or sys.platform == "win32" else project / ".venv" / "bin" / "python"


def all_rows(cache: Path) -> list[dict]:
    """The 300 tasks of SWE-bench Lite, downloaded once into cache."""
    if not cache.exists():
        rows = []
        for offset in (0, 100, 200):
            with urllib.request.urlopen(ROWS_URL.format(offset=offset), timeout=60) as r:
                rows += [x["row"] for x in json.load(r)["rows"]]
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(rows), encoding="utf-8")
    return json.loads(cache.read_text(encoding="utf-8"))


def write_task(row: dict, folder: Path) -> None:
    """task.json, test.patch, gold.patch and issue.md of one task; the agent never sees this folder."""
    folder.mkdir(parents=True, exist_ok=True)
    summary = {key: row[key] for key in ("instance_id", "repo", "base_commit", "version")} | {
        key: json.loads(row[key]) for key in ("FAIL_TO_PASS", "PASS_TO_PASS")
    }
    (folder / "task.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    (folder / "test.patch").write_bytes(row["test_patch"].encode("utf-8"))
    (folder / "gold.patch").write_bytes(row["patch"].encode("utf-8"))
    (folder / "issue.md").write_text(row["problem_statement"], encoding="utf-8", newline="\n")


def make_venv(project: Path, python: str, packages: list[str], env: dict[str, str] | None = None) -> None:
    if shutil.which("uv"):
        commands = [
            ["uv", "venv", "-q", "--python", python, ".venv"],
            ["uv", "pip", "install", "-q", "--python", str(python_of(project)), *packages],
        ]
    else:
        commands = [
            [sys.executable, "-m", "venv", ".venv"],
            [str(python_of(project)), "-m", "pip", "install", "-q", *packages],
        ]
    for command in commands:
        done = subprocess.run(
            command, cwd=project, capture_output=True, text=True, errors="replace", env={**os.environ, **(env or {})}
        )
        if done.returncode != 0:
            raise SetupError(f"{' '.join(command[:3])} failed: {done.stderr.strip()[-500:]}")


def prepare(spec: dict, task_dir: Path, project: Path, rows_cache: Path) -> str:
    """Download the task, check out its repository at the task's commit on a branch of its own with a virtual
    environment, and check that the benchmark's tests fail now and pass with the reference fix."""
    row = next((r for r in all_rows(rows_cache) if r["instance_id"] == spec["id"]), None)
    if row is None:
        raise SetupError(f"{spec['id']} is not a SWE-bench Lite task")
    write_task(row, task_dir)
    if project.exists():
        shutil.rmtree(project, onerror=lambda f, p, _: (Path(p).chmod(0o700), f(p)))
    project.mkdir(parents=True)
    git(project, "init", "-q")
    git(project, "remote", "add", "origin", spec["url"])
    git(project, "fetch", "-q", "--depth", "1", "origin", row["base_commit"])
    git(project, "checkout", "-q", "FETCH_HEAD")
    git(project, "switch", "-q", "-c", "agent-work")
    with open(project / ".git" / "info" / "exclude", "a", encoding="utf-8") as f:
        f.write(EXCLUDE)
    env = dict(spec.get("env", {}))
    if spec.get("scm_version"):  # setuptools-scm finds no tags in a shallow clone: give it the task's version
        env["SETUPTOOLS_SCM_PRETEND_VERSION"] = f"{row['version']}.0"
    make_venv(project, spec.get("python", "3.13"), spec["packages"], env)

    (task_dir / IGNORED).unlink(missing_ok=True)
    git(project, "apply", str(task_dir / "gold.patch"))
    after = evaluate(task_dir, project)
    reset(project)
    if not all(r == "PASSED" for r in after.fail_to_pass.values()):
        raise SetupError(f"{spec['id']}: the benchmark's test fails with the reference fix. {after.output[-300:]}")
    if len(after.broken) > len(json.loads((task_dir / "task.json").read_text(encoding="utf-8"))["PASS_TO_PASS"]) / 4:
        raise SetupError(f"{spec['id']}: {len(after.broken)} other tests fail with the reference fix")
    (task_dir / IGNORED).write_text(json.dumps(after.broken, indent=1), encoding="utf-8")
    if evaluate(task_dir, project).resolved:
        raise SetupError(f"{spec['id']}: the benchmark's test passes without a fix")
    files = git(project, "ls-files", "*.py").decode().splitlines()
    ignored = f", {len(after.broken)} tests that fail here left out" if after.broken else ""
    return f"{len(files)} python files{ignored}"


def reset(project: Path) -> None:
    """Back to the task's commit: the agent's changes and its state folder are dropped, the environment kept.
    Ignored files stay: the setup may have made some the project needs (pytest's generated _version.py)."""
    git(project, "checkout", "-q", "--", ".")
    git(project, "clean", "-fdq", "-e", ".venv")
    shutil.rmtree(project / ".local-coder", ignore_errors=True)


def evaluate(task_dir: Path, project: Path) -> Score:
    """The agent's changes without the test files the benchmark replaces, the benchmark's tests on top, in a
    temporary worktree; the project folder itself is left as it is."""
    task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    test_patch = (task_dir / "test.patch").read_bytes()
    test_files = [line.split(" b/")[-1] for line in test_patch.decode().splitlines() if line.startswith("diff --git")]
    tests = [f for f in test_files if f.endswith(".py")]  # the patch may bring data files too (a .toml)
    git(project, "add", "-A", "-N")  # new files count as changes
    changed = git(project, "diff", "--stat", task["base_commit"]).decode(errors="replace").strip()
    patch = git(project, "diff", "--binary", task["base_commit"], "--", ".", *[f":(exclude){f}" for f in test_files])
    git(project, "reset", "-q")  # undo the intent-to-add marks

    with tempfile.TemporaryDirectory(prefix="swe-eval-") as tmp:
        tree = Path(tmp) / "tree"
        git(project, "worktree", "add", "--detach", str(tree), task["base_commit"])
        try:
            if patch.strip():
                git(tree, "apply", "--whitespace=nowarn", stdin=patch)
            git(tree, "apply", "--whitespace=nowarn", stdin=test_patch)
            run = subprocess.run(
                [str(python_of(project)), "-m", "pytest", *tests, "-q", "-p", "no:cacheprovider", "-rA"],
                cwd=tree,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=1800,
            )
        finally:
            git(project, "worktree", "remove", "--force", str(tree), check=False)
    status = results(run.stdout)
    f2p = {t: status.get(t, "NOT RUN") for t in task["FAIL_TO_PASS"]}
    ignored = (
        set(json.loads((task_dir / IGNORED).read_text(encoding="utf-8"))) if (task_dir / IGNORED).exists() else set()
    )
    broken = [t for t in task["PASS_TO_PASS"] if status.get(t) != "PASSED" and t not in ignored]
    resolved = all(r == "PASSED" for r in f2p.values()) and not broken
    output = "" if status else (run.stdout[-2000:] + run.stderr[-1000:])
    return Score(resolved, f2p, broken, changed, output)


def results(output: str) -> dict[str, str]:
    """Test -> result from the summary of `pytest -rA`. A test is found by its whole id
    (path::Class::test[param], as most tasks name it), by its id up to the first space (the benchmark's lists cut
    an id with a space there) and by its bare name (as SymPy's tasks name it)."""
    status = {}
    for line in output.splitlines():
        word, _, rest = line.partition(" ")
        if word not in RESULT_WORDS or not rest:
            continue
        test = rest.split(" - ", 1)[0].strip()  # a failure ends with " - <message>"
        status[test] = word
        status.setdefault(test.split(" ")[0], word)
        status.setdefault(test.split("::")[-1], word)
    return status
